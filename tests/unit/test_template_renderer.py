"""Unit tests for src/services/template_renderer.py.

Coverage:
- ALLOWED_VARS allowlist enforcement (validate_template_vars)
- Cold-AUP unsubscribe_url check (assert_cold_email_unsubscribe)
- render() happy path + missing-var + sandbox escape attempt
- HTML autoescape vs text mode
- Trim modifiers + filters don't fool the allowlist check
"""
from __future__ import annotations

import unittest

from src.services.template_renderer import (
    ALLOWED_VARS,
    DisallowedVariableError,
    MissingUnsubscribeUrlError,
    MissingVariableError,
    SecurityError,
    TemplateError,
    assert_cold_email_unsubscribe,
    render,
    validate_template_vars,
)


class TestValidateTemplateVars(unittest.TestCase):
    def test_only_allowed_vars_passes(self) -> None:
        body = "Hi {{ first_name }}, your company {{ company }} pinged us. {{ unsubscribe_url }}"
        self.assertEqual(validate_template_vars(body), [])

    def test_disallowed_var_returned_in_list(self) -> None:
        body = "Hi {{ first_name }}, secret is {{ api_key }}"
        self.assertEqual(validate_template_vars(body), ["api_key"])

    def test_multiple_disallowed_vars_sorted(self) -> None:
        body = "{{ admin_pass }} {{ first_name }} {{ debug_token }}"
        self.assertEqual(
            validate_template_vars(body),
            ["admin_pass", "debug_token"],
        )

    def test_filter_and_trim_modifiers_not_fooled(self) -> None:
        body = "Hi {{- first_name -}} ! {{ unsubscribe_url | upper }}"
        self.assertEqual(validate_template_vars(body), [])

    def test_syntax_error_raises_template_error(self) -> None:
        with self.assertRaises(TemplateError):
            validate_template_vars("Hi {{ unclosed")

    def test_empty_template_returns_empty_list(self) -> None:
        self.assertEqual(validate_template_vars(""), [])
        self.assertEqual(validate_template_vars(None), [])  # type: ignore[arg-type]


class TestAssertColdEmailUnsubscribe(unittest.TestCase):
    def test_body_with_unsubscribe_url_passes(self) -> None:
        body = "Hi {{ first_name }}. Click {{ unsubscribe_url }} to opt out."
        assert_cold_email_unsubscribe(body)  # no raise

    def test_body_without_unsubscribe_url_raises(self) -> None:
        body = "Hi {{ first_name }}, no opt-out link here."
        with self.assertRaises(MissingUnsubscribeUrlError):
            assert_cold_email_unsubscribe(body)

    def test_substring_only_doesnt_count(self) -> None:
        """Literal text 'unsubscribe_url' (no merge braces) does NOT
        count — must be an actual template variable reference."""
        body = "Hi, the unsubscribe_url is in the footer."
        with self.assertRaises(MissingUnsubscribeUrlError):
            assert_cold_email_unsubscribe(body)

    def test_filtered_unsubscribe_url_still_counts(self) -> None:
        # AST sees the var regardless of filter/trim modifiers.
        body = "Hi {{ first_name }}. {{- unsubscribe_url | escape -}}"
        assert_cold_email_unsubscribe(body)

    def test_empty_body_raises(self) -> None:
        with self.assertRaises(MissingUnsubscribeUrlError):
            assert_cold_email_unsubscribe("")


class TestRender(unittest.TestCase):
    def test_happy_path(self) -> None:
        body = "Hi {{ first_name }}, from {{ operator_name }}. {{ unsubscribe_url }}"
        out = render(body, {
            "first_name": "Ana",
            "operator_name": "Duško",
            "unsubscribe_url": "https://lds/u/abc",
        })
        self.assertEqual(out, "Hi Ana, from Duško. https://lds/u/abc")

    def test_missing_var_raises_strict(self) -> None:
        body = "Hi {{ first_name }} {{ last_name }}"
        with self.assertRaises(MissingVariableError):
            render(body, {"first_name": "Ana"})

    def test_extra_context_silently_dropped(self) -> None:
        """Context keys outside ALLOWED_VARS get filtered before render.
        Defensive — caller might pass a wider lead row by mistake."""
        body = "Hi {{ first_name }}"
        out = render(body, {
            "first_name": "Ana",
            "api_key": "sk-xxx",  # not in ALLOWED_VARS — must be dropped
        })
        self.assertEqual(out, "Hi Ana")

    def test_html_autoescape_on(self) -> None:
        body = "<p>Hi {{ first_name }}</p>"
        out = render(body, {"first_name": "<script>alert(1)</script>"},
                     content_type="html")
        # autoescaped → < becomes &lt; etc.
        self.assertIn("&lt;script&gt;", out)
        self.assertNotIn("<script>", out)

    def test_text_no_escape(self) -> None:
        body = "Hi {{ first_name }}"
        out = render(body, {"first_name": "<script>alert(1)</script>"})
        # text mode does NOT autoescape; the literal flows through.
        # This is OK for cold email bodies (mail clients render plain text).
        self.assertIn("<script>", out)


class TestSandboxEscape(unittest.TestCase):
    def test_attribute_walk_blocked(self) -> None:
        """SandboxedEnvironment blocks ``__class__.__subclasses__`` traversal."""
        # Construct a template that tries to escape via .mro / .subclasses.
        body = "{{ ''.__class__.__mro__[1].__subclasses__() }}"
        # validate_template_vars sees no Undefined vars (the empty string
        # literal isn't a variable); validation passes. The render path
        # is where the sandbox blocks.
        with self.assertRaises((SecurityError, MissingVariableError, TemplateError)):
            render(body, {})


class TestAllowedVarsContract(unittest.TestCase):
    def test_canonical_11_vars(self) -> None:
        """Pinned set — adding a var requires updating template_renderer
        AND any AI generator / UI dropdown that surfaces the list."""
        self.assertEqual(ALLOWED_VARS, frozenset({
            "first_name", "last_name", "company", "website",
            "city", "industry", "audit_score", "pain_point",
            "operator_name", "operator_signature", "unsubscribe_url",
        }))


if __name__ == "__main__":
    unittest.main()
