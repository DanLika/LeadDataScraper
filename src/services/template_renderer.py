"""Jinja2 sandboxed renderer + variable allowlist + cold-AUP enforcement.

Sequence variants (Phase 15.1) carry ``subject_template`` and
``body_template`` strings. The dispatch tick (Phase 15.2) renders them
per-lead via this module before handing the payload to the dispatcher.

**Security model**

1. ``SandboxedEnvironment`` blocks attribute lookups on disallowed
   types — protects against template injection that walks
   ``{{ ''.__class__.__mro__[1].__subclasses__()[...] }}`` to escape
   sandbox. Jinja2's sandbox has had bypass CVEs across minors
   (CVE-2019-10906, CVE-2024-22195 / 56326) — pin patches via
   ``~=3.1.4`` in requirements.in.
2. ``StrictUndefined`` raises on every reference to an unprovided
   variable. Catches typos in variant copy at render time rather than
   shipping `` {{ first_nam }} `` literal into the recipient's inbox.
3. ``select_autoescape(['html'])`` auto-escapes HTML mode renders;
   text mode (default for cold email bodies) stays as-is. Caller
   passes ``content_type='html'`` to opt into the HTML path.
4. ``ALLOWED_VARS`` allowlist enforced at variant CREATE time via
   :func:`validate_template_vars`. The renderer itself trusts the
   variant has been validated; bypass requires going around the
   service layer.
5. Cold-channel email variants MUST reference ``{{ unsubscribe_url }}``
   — RFC 8058 + AUP. Enforced at create-time via
   :func:`assert_cold_email_unsubscribe`.

**Render contract**

``render(template, context, content_type='text')`` returns the rendered
string. Missing vars raise ``MissingVariableError``; sandbox violations
raise ``SecurityError`` (translated from Jinja2's internal exceptions).
Both subclass ``TemplateError`` so callers can collapse to one error
path.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from jinja2 import StrictUndefined, meta, select_autoescape
from jinja2.exceptions import (
    SecurityError as JinjaSecurityError,
    TemplateSyntaxError,
    UndefinedError as JinjaUndefinedError,
)
from jinja2.sandbox import SandboxedEnvironment

logger = logging.getLogger(__name__)

ContentType = Literal["text", "html"]

ALLOWED_VARS: frozenset[str] = frozenset({
    # Lead-derived (joined at dispatch_tick time from the leads table)
    "first_name",
    "last_name",
    "company",
    "website",
    "city",
    "industry",
    "audit_score",
    "pain_point",
    # Operator-derived (env-set; injected by the worker)
    "operator_name",
    "operator_signature",
    # System-injected (auto-populated by the dispatcher payload
    # builder — operators reference but don't supply)
    "unsubscribe_url",
})


# ----- Errors ---------------------------------------------------------------


class TemplateError(Exception):
    """Base for every render / validate failure."""


class MissingVariableError(TemplateError):
    """A template references a variable not present in the context."""


class DisallowedVariableError(TemplateError):
    """Template uses a variable outside ALLOWED_VARS. Raised at variant
    CREATE time so bad copy never reaches the render path."""


class MissingUnsubscribeUrlError(TemplateError):
    """A cold-channel email variant body doesn't reference
    ``{{ unsubscribe_url }}``. RFC 8058 + AUP requirement; rejected
    at variant CREATE time."""


class SecurityError(TemplateError):
    """Sandbox violation — template tried to escape the environment."""


# ----- Environments ---------------------------------------------------------


def _build_environment(content_type: ContentType) -> SandboxedEnvironment:
    """One environment per render call — cheap (Jinja2 internal caches
    don't depend on the env instance) and avoids global mutable state
    across content types.
    """
    return SandboxedEnvironment(
        undefined=StrictUndefined,
        autoescape=select_autoescape(["html"]) if content_type == "html" else False,
        trim_blocks=True,
        lstrip_blocks=True,
    )


# Validation env stays static — only used for AST parsing, never renders.
_VALIDATION_ENV: SandboxedEnvironment = SandboxedEnvironment(
    undefined=StrictUndefined,
    autoescape=False,
)


# ----- Validation -----------------------------------------------------------


def validate_template_vars(template_str: str) -> list[str]:
    """Return sorted list of variables in ``template_str`` that are NOT
    in :data:`ALLOWED_VARS`.

    Empty list = valid. Raises ``TemplateError`` on syntax errors so
    bad-syntax templates never reach the renderer. Walks the Jinja2
    AST (``meta.find_undeclared_variables``) — substring matching
    would miss e.g. ``{{ unsubscribe_url | escape }}`` or
    ``{{- unsubscribe_url -}}``.
    """
    if not template_str:
        return []
    try:
        ast = _VALIDATION_ENV.parse(template_str)
    except TemplateSyntaxError as exc:
        raise TemplateError(f"template syntax error: {exc.message}") from exc
    used = meta.find_undeclared_variables(ast)
    disallowed = sorted(used - ALLOWED_VARS)
    return disallowed


def assert_cold_email_unsubscribe(body_template: str) -> None:
    """Raise :class:`MissingUnsubscribeUrlError` if the body doesn't
    reference ``unsubscribe_url``.

    Walks the AST so trim modifiers / filters don't fool the check.
    Caller wires this for ``step.channel='email'`` variants only —
    LinkedIn variants don't carry an unsubscribe URL (different
    AUP model).
    """
    if not body_template:
        raise MissingUnsubscribeUrlError(
            "cold-channel body cannot be empty",
        )
    try:
        ast = _VALIDATION_ENV.parse(body_template)
    except TemplateSyntaxError as exc:
        raise TemplateError(f"template syntax error: {exc.message}") from exc
    used = meta.find_undeclared_variables(ast)
    if "unsubscribe_url" not in used:
        raise MissingUnsubscribeUrlError(
            "cold-channel email body MUST reference {{ unsubscribe_url }} "
            "(RFC 8058 + Instantly AUP)",
        )


# ----- Render ---------------------------------------------------------------


def render(
    template_str: str,
    context: dict[str, Any],
    *,
    content_type: ContentType = "text",
) -> str:
    """Render ``template_str`` against ``context``.

    ``context`` is filtered to ALLOWED_VARS before binding so the
    sandbox can't be tricked into rendering something a future caller
    accidentally smuggled in via an unbounded dict.
    """
    if template_str is None:
        return ""
    env = _build_environment(content_type)
    try:
        template = env.from_string(template_str)
    except TemplateSyntaxError as exc:
        raise TemplateError(f"template syntax error: {exc.message}") from exc

    # Whitelist the binding context. Extra keys silently dropped — safer
    # than raising because the caller may legitimately have wider
    # data (lead row with audit_score, seo_score, etc.) of which the
    # template uses a subset.
    bound = {k: v for k, v in (context or {}).items() if k in ALLOWED_VARS}

    try:
        return template.render(**bound)
    except JinjaUndefinedError as exc:
        raise MissingVariableError(str(exc)) from exc
    except JinjaSecurityError as exc:
        # Sandbox catch — log loud since this should never fire in
        # practice (operator-authored templates + AST-validated allowlist).
        logger.exception("template sandbox violation: %s", exc)
        raise SecurityError(str(exc)) from exc


__all__ = [
    "ALLOWED_VARS",
    "ContentType",
    "TemplateError",
    "MissingVariableError",
    "DisallowedVariableError",
    "MissingUnsubscribeUrlError",
    "SecurityError",
    "validate_template_vars",
    "assert_cold_email_unsubscribe",
    "render",
]
