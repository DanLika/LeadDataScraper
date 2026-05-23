"""
Meta-test for every Pydantic BaseModel in backend.main.

Auto-discovers all subclasses and asserts:

  1. model_config has `extra='forbid'` — blocks mass-assignment via
     attacker-controlled extra fields.
  2. Every string-typed field carries a `max_length` constraint — bounds
     memory before the handler runs, blocking pre-handler DoS.
  3. Every enum-like field uses `Literal[...]` rather than raw `str` —
     keeps DB-bound and API-bound enums constrained at the boundary.

The meta-test means new models cannot be added to backend.main without
satisfying the same hardening invariants. Failure messages name the
offending Model.field so the fix is one line.
"""
import os
import re
import sys
import unittest
from typing import Literal, Optional, Union, get_args, get_origin

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set env early so backend.main imports cleanly.
os.environ.setdefault("API_SECRET_KEY", "meta-test-key")
os.environ.setdefault("ADMIN_TOKEN", "meta-test-admin")
os.environ.setdefault("ALLOWED_ORIGINS", "http://test")
os.environ.setdefault("GEMINI_API_KEY", "meta-test-fake")

from pydantic import BaseModel

# Field names that should be Literal-constrained when present. Restricted
# to TRULY closed-set fields. Excluded by design:
#  - "name"  → free-form user-typed campaign/lead name (constr-bounded only)
#  - "type"  → ExecutePlanParams.type is a free-form bucket label
#  - "rating" → could mean numeric rating (1-5) or WebVital category — only
#               WebVitalsMetric.rating is Literal, and it already is.
# Add to this set when the schema introduces a new enum-shaped field.
ENUMISH_FIELD_NAMES = {
    "channel",  # CampaignChannel
    "status",   # CampaignStatus
    "task",     # ExecutableTask
    "kind",
    "role",
}


def _all_basemodels(module) -> list[type[BaseModel]]:
    out: list[type[BaseModel]] = []
    for name in dir(module):
        obj = getattr(module, name)
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
            # Exclude pydantic-internal classes that might be re-exported.
            if obj.__module__ != module.__name__:
                continue
            out.append(obj)
    return out


def _annotation_is_str_or_optional_str(ann) -> bool:
    """True if `ann` is `str`, `Optional[str]`, or a constr-derived
    annotation that resolves to a str-with-metadata."""
    if ann is str:
        return True
    if get_origin(ann) is Union:
        return any(_annotation_is_str_or_optional_str(a) for a in get_args(ann) if a is not type(None))
    # Pydantic v2 constr returns an Annotated[str, ...] alias whose underlying
    # type is str. Check via __origin__/__metadata__ if present.
    underlying = getattr(ann, "__origin__", None)
    if underlying is str:
        return True
    return False


def _annotation_has_max_length(ann) -> bool:
    """
    Walks the annotation chain for a MaxLen / constr-max constraint.
    In Pydantic v2 constraints typically live on `FieldInfo.metadata`, not
    in the annotation — `_field_has_max_length` is the canonical check.
    This helper is kept for nested annotated types inside Optional/list.
    """
    if get_origin(ann) is Union:
        for a in get_args(ann):
            if a is type(None):
                continue
            if _annotation_has_max_length(a):
                return True
        return False

    metadata = getattr(ann, "__metadata__", ())
    for m in metadata:
        if getattr(m, "max_length", None) is not None:
            return True
    return False


def _field_has_max_length(finfo) -> bool:
    """
    Canonical Pydantic v2 check: constraints are stored on `FieldInfo.metadata`
    (a list of annotated_types objects like `MaxLen` / `StringConstraints`).
    `constr(max_length=N)` ends up in this list, not on the bare annotation.
    """
    for m in (finfo.metadata or ()):
        if getattr(m, "max_length", None) is not None:
            return True
    # Fallback: an Annotated[...] annotation that carries MaxLen inline.
    return _annotation_has_max_length(finfo.annotation)


def _annotation_is_literal(ann) -> bool:
    """True if `ann` is `Literal[...]` or `Optional[Literal[...]]`."""
    if get_origin(ann) is Literal:
        return True
    if get_origin(ann) is Union:
        return any(_annotation_is_literal(a) for a in get_args(ann) if a is not type(None))
    return False


def _field_is_constrained_or_literal(finfo) -> bool:
    """Acceptable shape: annotation is Literal OR FieldInfo carries a MaxLen."""
    return _annotation_is_literal(finfo.annotation) or _field_has_max_length(finfo)


class TestPydanticModelsMeta(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from backend import main as backend_main
        cls.models = _all_basemodels(backend_main)
        assert cls.models, "Found zero BaseModel subclasses in backend.main — discovery broken"

    def test_models_discovered(self):
        names = sorted(m.__name__ for m in self.models)
        # Surface the inventory for human review on each run
        print(f"\n[pydantic_meta] discovered models: {names}")
        # Sanity — the well-known set must be present. Anything new gets caught
        # by the rules below; this just guards against renames.
        expected_subset = {
            "CampaignCreate", "CampaignUpdate", "LeadProcessRequest",
            "AskInstruction", "AskRequest", "DiscoveryRequest",
            "PipelineRequest", "ExecutePlanParams", "ExecutePlanRequest",
        }
        missing = expected_subset - set(names)
        self.assertFalse(missing, f"Renamed/removed models: {missing}")

    def test_every_model_forbids_extra_fields(self):
        failures = []
        for model in self.models:
            cfg = model.model_config or {}
            extra = cfg.get("extra") if isinstance(cfg, dict) else getattr(cfg, "extra", None)
            if extra != "forbid":
                failures.append(f"{model.__name__}: model_config.extra={extra!r} (want 'forbid')")
        self.assertFalse(
            failures,
            "Mass-assignment defense (extra='forbid') missing:\n" + "\n".join(failures)
        )

    def test_every_string_field_has_max_length(self):
        """
        Catches any plain `str` field added to a request model — it would
        accept unbounded input and blow up memory before the handler runs.
        """
        failures = []
        for model in self.models:
            for fname, finfo in model.model_fields.items():
                ann = finfo.annotation
                if not _annotation_is_str_or_optional_str(ann):
                    continue
                if not _field_is_constrained_or_literal(finfo):
                    failures.append(
                        f"{model.__name__}.{fname}: str-typed but no max_length / Literal. "
                        f"annotation={ann!r}  metadata={finfo.metadata!r}"
                    )
        self.assertFalse(
            failures,
            "Unbounded string fields (memory-DoS surface):\n" + "\n".join(failures)
        )

    def test_enumish_fields_use_literal(self):
        """
        Fields whose names imply a closed value set MUST be Literal-typed.
        Adding a new enum-shaped field name → update ENUMISH_FIELD_NAMES.
        """
        failures = []
        for model in self.models:
            for fname, finfo in model.model_fields.items():
                if fname not in ENUMISH_FIELD_NAMES:
                    continue
                ann = finfo.annotation
                if not _annotation_is_literal(ann):
                    failures.append(
                        f"{model.__name__}.{fname}: enum-like name but annotation "
                        f"is {ann!r} (want Literal[...])"
                    )
        self.assertFalse(
            failures,
            "Enum-like fields not Literal-typed:\n" + "\n".join(failures)
        )

    def test_every_collection_field_has_max_length(self):
        """
        Defensive bonus — list/conlist must also be bounded. A 10M-element
        list of strings is the same DoS vector as one 10M-char string.
        """
        failures = []
        for model in self.models:
            for fname, finfo in model.model_fields.items():
                ann = finfo.annotation
                # Detect list / conlist via origin
                inner = ann
                if get_origin(ann) is Union:
                    inner = next(
                        (a for a in get_args(ann) if a is not type(None)), ann
                    )
                if get_origin(inner) is list:
                    # Plain `list[...]` without max_length — flag.
                    if not _field_has_max_length(finfo):
                        failures.append(
                            f"{model.__name__}.{fname}: list without max_length. "
                            f"annotation={ann!r}  metadata={finfo.metadata!r}"
                        )
                # conlist returns an Annotated[list[...], MaxLen(N), ...] —
                # already covered by _annotation_has_max_length.
        self.assertFalse(
            failures,
            "Unbounded list fields:\n" + "\n".join(failures)
        )


if __name__ == "__main__":
    unittest.main()
