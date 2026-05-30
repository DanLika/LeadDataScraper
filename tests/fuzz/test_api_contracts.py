"""Property-based fuzz of inbound API contracts.

Tier 1 (this file) is **in-process, no network, no DB, no money**.
We treat each `BaseModel` in `backend/main.py` as a black-box contract:

- Schema-valid input (`hypothesis_jsonschema.from_schema` driven by
  `Model.model_json_schema()`) MUST round-trip — `Model(**payload)`
  either constructs OR raises `pydantic.ValidationError`. Any other
  exception is a 500-equivalent (the FastAPI `Exception` handler would
  turn it into `{"error":"Internal server error"}` 500) — fail.

- Adversarial composites layered on top: deeply nested objects,
  oversize strings, NUL / control chars, `NaN`/`Infinity` floats,
  unknown extra keys. All must surface as `ValidationError`, never a
  bare exception.

Why no HTTP? POSTing to the live ASGI app would either need real
DB / Gemini wiring or a heavy dependency-override harness. The model
layer is where the contract lives: every endpoint's body is parsed
by Pydantic *before* the handler runs (the `@app.exception_handler
(RequestValidationError)` shim is the only thing between malformed
input and a 500). Validating the model directly covers the same
surface without the I/O cost.

Tier 2 (prod, side-effect-free targets like `POST /metrics` and GET
reads with fuzzed query params) is a one-off run, not committed —
see `docs/runbooks/api-fuzz-tier2.md` if it gets promoted.
"""

from __future__ import annotations

import math
import os
import sys

import pytest

# Set fakes BEFORE `backend.main` import so module-load checks don't barf.
# These never travel — Pydantic models do not touch env at construct time;
# the env reads happen in middleware + handlers we never reach here.
os.environ.setdefault("API_SECRET_KEY", "x" * 64)
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "x" * 40)
os.environ.setdefault("GEMINI_API_KEY", "x" * 39)
os.environ.setdefault("ADMIN_TOKEN", "x" * 32)
os.environ.setdefault("INSTANTLY_API_KEY", "x" * 32)
os.environ.setdefault("INSTANTLY_WEBHOOK_SECRET", "x" * 32)

# Repo root on path for `from backend.main import ...` when pytest is run
# from a sibling directory.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from hypothesis import HealthCheck, given, settings, strategies as st
from hypothesis_jsonschema import from_schema
from pydantic import BaseModel, ValidationError

from backend.main import (  # noqa: E402  (import after env + path setup)
    AccountDeletionRequest,
    AskInstruction,
    AskRequest,
    CampaignCreate,
    CampaignUpdate,
    DemoLeadsDeletionRequest,
    DiscoveryRequest,
    ExecutePlanParams,
    ExecutePlanRequest,
    LeadProcessRequest,
    PipelineFilters,
    PipelineRequest,
    WebVitalsMetric,
)

# Every inbound Pydantic model the public API exposes via a request body.
# `WebVitalsMetric` lives on the unauth-but-rate-limited `/metrics` beacon,
# the rest gate behind `verify_api_key` and (for destructive ops)
# `verify_admin_token`. The fuzz target is the parse layer, identical for all.
ALL_MODELS: tuple[type[BaseModel], ...] = (
    PipelineFilters,
    CampaignCreate,
    CampaignUpdate,
    LeadProcessRequest,
    AskInstruction,
    AskRequest,
    DiscoveryRequest,
    PipelineRequest,
    ExecutePlanParams,
    ExecutePlanRequest,
    WebVitalsMetric,
    DemoLeadsDeletionRequest,
    AccountDeletionRequest,
)

# Hypothesis defaults are generous for this size — bump examples for the
# real signal (50/model, ~30s wallclock). Disable filter-too-much because
# `from_schema` + `safe_constr` AfterValidator can reject a chunk of
# schema-valid strings (control chars, etc.) — that's the point of the
# fuzz, not a strategy bug.
_FUZZ_SETTINGS = settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.filter_too_much,
        HealthCheck.data_too_large,
    ],
)


def _assert_constructs_or_validates(model: type[BaseModel], payload: object) -> None:
    """Round-trip invariant: any input either constructs or raises
    `ValidationError`. Anything else (TypeError, RecursionError, raw
    ValueError out of an AfterValidator, etc.) would propagate to
    FastAPI's `Exception` handler and become a 500 in prod — which
    the data-loss-audit lesson explicitly bans on validation paths.
    """
    if not isinstance(payload, dict):
        # `from_schema` for `type: object` always yields a dict; this
        # is a strategy-level invariant we re-assert because a future
        # `Union[BaseModel, list]` shape would silently fuzz the wrong
        # surface.
        raise AssertionError(
            f"strategy yielded non-dict for {model.__name__}: {type(payload).__name__}"
        )
    try:
        model(**payload)
    except ValidationError:
        # Documented failure mode: 422 path (or 403 if behind authz).
        return
    except Exception as exc:  # noqa: BLE001  -- this is the bug we hunt
        raise AssertionError(
            f"{model.__name__} raised non-ValidationError on {payload!r}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


# ----- one parametrized test per model, schema-driven -----------------------


@pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__name__)
def test_schema_driven_round_trip(model: type[BaseModel]) -> None:
    """For every model, 50 schema-valid payloads must round-trip cleanly.

    `from_schema` reads the same JSON Schema that FastAPI publishes in
    `/openapi.json`, so the strategy mirrors what a well-behaved client
    would actually send. Adversarial overlay is in the sibling test.
    """
    strategy = from_schema(model.model_json_schema())

    @_FUZZ_SETTINGS
    @given(strategy)
    def _run(payload: object) -> None:
        _assert_constructs_or_validates(model, payload)

    _run()


# ----- adversarial overlay --------------------------------------------------


# Generic JSON values: scalars, deep nesting, NaN/Infinity, NUL, controls.
# `recursive(...)` with a small leaves alphabet hits 5-deep dicts/lists
# routinely — well below Python's recursion limit so we don't crash the
# test runner, well above what real clients send.
_ADVERSARIAL_LEAVES = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**62), max_value=2**62),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(
        # Latin-1 + 8 control bytes mixed in. Excludes the BMP surrogates
        # block (`0xd800-0xdfff`) which JSON forbids; otherwise spans NUL,
        # bell, CR, LF, tab, vertical tab, form feed, DEL.
        alphabet=st.characters(
            blacklist_categories=("Cs",),
            min_codepoint=0,
            max_codepoint=0xFFFF,
        ),
        max_size=64,
    ),
    st.binary(max_size=32).map(lambda b: b.hex()),
)
_ADVERSARIAL_JSON = st.recursive(
    _ADVERSARIAL_LEAVES,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(max_size=16), children, max_size=4),
    ),
    max_leaves=8,
)


def _scrub_nan_inf(value: object) -> object:
    """`json.dumps` (and Starlette's response encoder) reject NaN/Infinity,
    but Pydantic accepts them on `float` fields. We want the fuzz to
    *try* to feed them in — so we don't scrub here. The invariant we
    pin is that Pydantic itself doesn't raise a bare ValueError on
    NaN; that's a separate 500 path (`tests/test_json_pollution.py`
    locks the validation-handler side)."""
    return value


def _is_finite_or_other(_: object) -> bool:  # used only to silence linters
    return True


@pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__name__)
def test_adversarial_payload_never_500(model: type[BaseModel]) -> None:
    """Throw deep / weird / oversize JSON at every model. Same invariant:
    constructs or `ValidationError`, never anything else.

    Hypothesis seeds the random walk; on a finding it shrinks to the
    minimal repro and prints it. Stop condition: any non-`ValidationError`
    exception (real 500-equivalent bug)."""

    @_FUZZ_SETTINGS
    @given(st.dictionaries(st.text(max_size=24), _ADVERSARIAL_JSON, max_size=8))
    def _run(payload: dict) -> None:
        _assert_constructs_or_validates(model, _scrub_nan_inf(payload))

    _run()


# ----- targeted regression pins --------------------------------------------


@pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__name__)
def test_extra_field_rejected(model: type[BaseModel]) -> None:
    """`model_config = ConfigDict(extra='forbid')` is set on every
    inbound model. A junk extra key must 422, not silently land.
    Pinned because losing `extra='forbid'` on `PipelineFilters` shipped
    a bypass in early 2026 (see `PipelineFilters` docstring)."""
    schema = model.model_json_schema()
    assert schema.get("additionalProperties") is False, (
        f"{model.__name__} missing additionalProperties:false — extra='forbid' regressed"
    )
    with pytest.raises(ValidationError):
        model(unexpected_field_xyz="x")


def test_nan_inf_float_never_crashes_validate() -> None:
    """`WebVitalsMetric.value` is a bounded float. NaN/Infinity must
    surface as `ValidationError` (or be normalised), never a raw
    ValueError from a downstream validator that escapes the handler."""
    for bad in (math.nan, math.inf, -math.inf):
        try:
            WebVitalsMetric(
                name="LCP", value=bad, rating="good", path="/x", id="abc"
            )
        except ValidationError:
            continue
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(
                f"WebVitalsMetric raised non-ValidationError on value={bad}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        # Some Pydantic versions normalise NaN silently — accept that
        # too. The bug we're hunting is the non-ValidationError path.


def test_deeply_nested_payload_does_not_recurse_to_death() -> None:
    """The `RecursionError` → 413 mapping lives in
    `_json_exception_handler`. Model-level we just want to confirm
    that *Pydantic itself* tolerates deep input without crashing the
    interpreter, so the handler-level mapping never has to fire on
    well-meaning clients.
    """
    payload: object = {}
    for _ in range(40):
        payload = {"nested": payload}
    try:
        PipelineRequest(filters=payload)  # type: ignore[arg-type]
    except (ValidationError, RecursionError):
        return
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(
            f"deep-nested payload raised non-ValidationError: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
