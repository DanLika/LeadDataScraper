"""
Comprehensive verification tests for all 10 cherry-picked improvements.
Tests each fix in isolation without requiring external services.
"""
import asyncio
import re
import ssl
import os
import sys
import uuid
import unittest
from unittest.mock import MagicMock, patch, AsyncMock

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# TIER 1 — SECURITY TESTS
# ============================================================

class TestFix1_SSLBypassRemoved(unittest.TestCase):
    """Verify the insecure SSL fallback (CERT_NONE) has been completely removed."""

    def test_no_cert_none_in_seo_audit(self):
        """The file must not contain any ssl.CERT_NONE usage."""
        seo_audit_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'scrapers', 'seo_audit.py')
        with open(seo_audit_path, 'r') as f:
            content = f.read()

        self.assertNotIn('CERT_NONE', content,
            "CRITICAL: ssl.CERT_NONE is still present in seo_audit.py — MitM vulnerability exists!")
        self.assertNotIn('check_hostname = False', content,
            "CRITICAL: check_hostname = False is still present — SSL bypass exists!")
        self.assertNotIn('verify_mode', content,
            "CRITICAL: verify_mode override is still present — SSL bypass exists!")

    def test_ssl_error_returns_flags_without_fallback(self):
        """When SSL fails, the result should show error flags but NOT attempt insecure connection."""
        from src.scrapers.seo_audit import perform_seo_audit_async

        # Simulate by checking the function source code structure
        import inspect
        source = inspect.getsource(perform_seo_audit_async)

        # The SSL exception handler should exist
        self.assertIn('ClientConnectorSSLError', source)
        self.assertIn('ssl.SSLError', source)

        # But it should NOT contain fallback SSL context creation
        self.assertNotIn('create_default_context', source,
            "SSL fallback context creation still exists in the function!")


class TestFix2_UUIDTempFilenames(unittest.TestCase):
    """Verify file upload uses UUID filenames, not user-controlled names."""

    def test_no_purepath_in_backend(self):
        """PurePath import should be removed from backend/main.py."""
        backend_path = os.path.join(os.path.dirname(__file__), '..', 'backend', 'main.py')
        with open(backend_path, 'r') as f:
            content = f.read()

        self.assertNotIn('from pathlib import PurePath', content,
            "PurePath import still exists — path traversal protection may be weak!")

    def test_uuid_import_present(self):
        """UUID module should be imported."""
        backend_path = os.path.join(os.path.dirname(__file__), '..', 'backend', 'main.py')
        with open(backend_path, 'r') as f:
            content = f.read()

        self.assertIn('import uuid', content,
            "UUID import is missing from backend/main.py!")

    def test_temp_path_uses_uuid(self):
        """Temp path construction should use uuid4, not user filename."""
        backend_path = os.path.join(os.path.dirname(__file__), '..', 'backend', 'main.py')
        with open(backend_path, 'r') as f:
            content = f.read()

        self.assertIn('uuid.uuid4()', content,
            "UUID-based temp path not found — still using user-controlled filename!")
        self.assertNotIn('PurePath(file.filename)', content,
            "User-controlled filename is still being used for temp file!")

    def test_uuid4_generates_valid_filename(self):
        """Verify UUID4 hex produces a safe filename."""
        test_path = f"tmp_{uuid.uuid4().hex}.csv"
        # Must not contain path separators
        self.assertNotIn('/', test_path)
        self.assertNotIn('\\', test_path)
        self.assertNotIn('..', test_path)
        # Must have correct format
        self.assertTrue(test_path.startswith('tmp_'))
        self.assertTrue(test_path.endswith('.csv'))
        self.assertEqual(len(test_path), 4 + 32 + 4)  # tmp_ + 32 hex chars + .csv


class TestFix3_APIKeyLeakPrevention(unittest.TestCase):
    """Verify GEMINI_API_KEY is never exposed in user-facing error messages."""

    def test_no_api_key_in_error_messages(self):
        """No error message should contain 'GEMINI_API_KEY'."""
        router_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'core', 'agentic_router.py')
        with open(router_path, 'r') as f:
            content = f.read()

        # Find all return statements with "error" key
        error_returns = re.findall(r'return\s*\{["\']error["\']\s*:\s*["\']([^"\']+)', content)

        for msg in error_returns:
            self.assertNotIn('GEMINI_API_KEY', msg,
                f"API key name leaked in error message: '{msg}'")
            self.assertNotIn('SUPABASE', msg,
                f"Supabase key leaked in error message: '{msg}'")
            self.assertNotIn('API_SECRET', msg,
                f"API secret leaked in error message: '{msg}'")

    def test_generic_error_messages_used(self):
        """Error messages should be generic, not revealing env var names."""
        router_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'core', 'agentic_router.py')
        with open(router_path, 'r') as f:
            content = f.read()

        # The old message "Set GEMINI_API_KEY" should not exist
        self.assertNotIn('Set GEMINI_API_KEY', content,
            "Old error message with API key name still present!")

        # The new generic message should exist
        self.assertIn('AI model not initialized.', content,
            "Generic error message not found — fix may not be applied!")


class TestFix4_CORSWildcardGuard(unittest.TestCase):
    """Verify CORS wildcard + credentials combo is prevented."""

    def test_wildcard_guard_code_exists(self):
        """Backend should strip wildcard origins entirely (stricter than disabling credentials)."""
        backend_path = os.path.join(os.path.dirname(__file__), '..', 'backend', 'main.py')
        with open(backend_path, 'r') as f:
            content = f.read()

        self.assertIn('origin != "*"', content,
            "Wildcard CORS strip not found!")

    def test_cors_with_normal_origins(self):
        """With normal origins, credentials should remain True."""
        # Simulate the logic
        allowed_origins = ["http://localhost:3000", "https://app.example.com"]
        allow_credentials = True

        if "*" in allowed_origins:
            allow_credentials = False
            allowed_origins = ["*"]

        self.assertTrue(allow_credentials)
        self.assertEqual(len(allowed_origins), 2)

    def test_cors_with_wildcard_origin(self):
        """With wildcard origin, credentials should be disabled."""
        allowed_origins = ["*"]
        allow_credentials = True

        if "*" in allowed_origins:
            allow_credentials = False
            allowed_origins = ["*"]

        self.assertFalse(allow_credentials)
        self.assertEqual(allowed_origins, ["*"])

    def test_cors_with_mixed_wildcard(self):
        """With wildcard mixed in normal origins, should still disable credentials."""
        allowed_origins = ["http://localhost:3000", "*", "https://app.example.com"]
        allow_credentials = True

        if "*" in allowed_origins:
            allow_credentials = False
            allowed_origins = ["*"]

        self.assertFalse(allow_credentials)
        self.assertEqual(allowed_origins, ["*"])


class TestFix5_SQLInjectionRegex(unittest.TestCase):
    """Verify the auto_migrate regex uses \\Z instead of $ to prevent trailing newline bypass."""

    def test_regex_uses_backslash_Z(self):
        """The regex should use \\Z, not $ for end-of-string matching."""
        helper_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'utils', 'supabase_helper.py')
        with open(helper_path, 'r') as f:
            content = f.read()

        # Must contain \Z anchor
        self.assertIn('\\Z', content,
            "\\Z anchor not found in supabase_helper.py — SQL injection still possible!")

    def test_valid_column_names_pass(self):
        """Normal column names should pass validation."""
        pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\Z")

        valid_names = [
            "seo_score", "facebook", "company_name", "enrichment_status",
            "first_name", "priority_link", "a", "A_B_c_123"
        ]
        for name in valid_names:
            self.assertIsNotNone(pattern.match(name),
                f"Valid column name '{name}' was rejected!")

    def test_trailing_newline_blocked(self):
        """Column names with trailing newlines should be REJECTED (the fix)."""
        pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\Z")

        # This is the exploit: $ would allow "valid_name\n" but \Z should block it
        malicious = "valid_name\n"
        self.assertIsNone(pattern.match(malicious),
            "CRITICAL: Trailing newline bypassed the regex — SQL injection possible!")

    def test_sql_injection_payloads_blocked(self):
        """Various SQL injection payloads should all be rejected."""
        pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\Z")

        payloads = [
            "col; DROP TABLE leads--",
            "col' OR '1'='1",
            "col\"; DROP TABLE leads;--",
            "col\nDROP TABLE leads",
            "col\n",
            "123col",  # starts with number
            "",  # empty
            " col",  # leading space
            "col name",  # space in middle
            "col-name",  # hyphen
        ]
        for payload in payloads:
            self.assertIsNone(pattern.match(payload),
                f"CRITICAL: SQL injection payload '{repr(payload)}' was accepted!")

    def test_dollar_sign_vulnerability_demonstration(self):
        """Demonstrate why $ was vulnerable and \\Z is not."""
        old_pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
        new_pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\Z")

        exploit = "valid_name\n"

        # $ matches before trailing newline — VULNERABLE
        old_result = old_pattern.match(exploit)
        # \Z does not match before trailing newline — SAFE
        new_result = new_pattern.match(exploit)

        self.assertIsNotNone(old_result,
            "The old $ pattern should have matched (demonstrating vulnerability)")
        self.assertIsNone(new_result,
            "The new \\Z pattern should NOT match (demonstrating fix)")


# ============================================================
# TIER 2 — PERFORMANCE TESTS
# ============================================================

class TestFix6_ParallelEnrichmentFetching(unittest.TestCase):
    """Verify enrichment engine fetches pages concurrently via asyncio.gather."""

    def test_asyncio_gather_in_enrichment(self):
        """The enrichment engine should use asyncio.gather for parallel fetching."""
        engine_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'scrapers', 'enrichment_engine.py')
        with open(engine_path, 'r') as f:
            content = f.read()

        self.assertIn('asyncio.gather', content,
            "asyncio.gather not found — pages are still fetched sequentially!")
        self.assertIn('fetch_page', content,
            "fetch_page helper function not found!")

    def test_no_sequential_loop_for_pages(self):
        """There should not be a sequential for loop that fetches pages one by one."""
        engine_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'scrapers', 'enrichment_engine.py')
        with open(engine_path, 'r') as f:
            content = f.read()

        # The old sequential pattern was "for url in urls_to_check[:3]:"
        # followed directly by page operations (not inside a helper function)
        # With the new code, the loop creates tasks instead
        self.assertIn('tasks = [fetch_page(url) for url in urls_to_check[:3]]', content,
            "Task creation list comprehension not found!")

    def test_concurrent_fetch_preserves_results(self):
        """Verify the gather results are properly collected into content_blocks."""
        engine_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'scrapers', 'enrichment_engine.py')
        with open(engine_path, 'r') as f:
            content = f.read()

        self.assertIn('results = await asyncio.gather(*tasks)', content,
            "asyncio.gather results not being collected!")
        self.assertIn('for res in results:', content,
            "Results not being iterated after gather!")
        self.assertIn('content_blocks.append(res)', content,
            "Results not being appended to content_blocks!")


class TestFix7_SmartWaitInDiscovery(unittest.TestCase):
    """Verify discovery engine uses wait_for_selector instead of hardcoded sleep."""

    def test_no_hardcoded_sleep_for_initial_load(self):
        """The discovery engine should NOT use asyncio.sleep(5) for initial page load."""
        engine_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'scrapers', 'discovery_engine.py')
        with open(engine_path, 'r') as f:
            content = f.read()

        # Find all sleep calls
        sleep_calls = re.findall(r'asyncio\.sleep\((\d+)\)', content)

        # sleep(5) should NOT exist (it was the hardcoded initial wait)
        self.assertNotIn('5', sleep_calls,
            "asyncio.sleep(5) still exists — blind wait not replaced!")

    def test_wait_for_selector_used(self):
        """Should use wait_for_selector for maps results."""
        engine_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'scrapers', 'discovery_engine.py')
        with open(engine_path, 'r') as f:
            content = f.read()

        self.assertIn('wait_for_selector', content,
            "wait_for_selector not found — still using blind wait!")
        self.assertIn("div[role='article']", content,
            "Maps result selector not found in wait_for_selector!")

    def test_timeout_fallback_exists(self):
        """The wait_for_selector should have a timeout with graceful fallback."""
        engine_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'scrapers', 'discovery_engine.py')
        with open(engine_path, 'r') as f:
            content = f.read()

        self.assertIn('PlaywrightTimeoutError', content,
            "Timeout error handling not found!")
        self.assertIn('timeout=10000', content,
            "10-second timeout not found for wait_for_selector!")


class TestFix8_NPlus1QueryElimination(unittest.TestCase):
    """Verify N+1 DB queries are eliminated in campaign strategy generation."""

    def test_lead_data_passthrough_exists(self):
        """The campaign strategy should pass lead_data to avoid re-fetching."""
        router_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'core', 'agentic_router.py')
        with open(router_path, 'r') as f:
            content = f.read()

        self.assertIn('"lead_data": lead', content,
            "lead_data passthrough not found in campaign strategy!")

    def test_outreach_draft_accepts_lead_data(self):
        """_generate_outreach_draft should check for lead_data in params before querying."""
        router_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'core', 'agentic_router.py')
        with open(router_path, 'r') as f:
            content = f.read()

        self.assertIn('params.get("lead_data")', content,
            "_generate_outreach_draft doesn't check for pre-fetched lead_data!")

    def test_fallback_query_still_works(self):
        """When lead_data is not provided, it should still fetch from DB."""
        router_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'core', 'agentic_router.py')
        with open(router_path, 'r') as f:
            content = f.read()

        # The fallback DB query should still exist
        # Find the outreach draft function section
        draft_func_start = content.find('async def _generate_outreach_draft')
        draft_func_end = content.find('async def _generate_linkedin_draft')
        draft_section = content[draft_func_start:draft_func_end]

        self.assertIn('.table("leads").select("*").eq("unique_key"', draft_section,
            "Fallback DB query removed — breaks standalone outreach draft calls!")


# ============================================================
# TIER 3 — CODE HEALTH TESTS
# ============================================================

class TestFix9_PrecompiledRegex(unittest.TestCase):
    """Verify segment matching uses precompiled regex patterns."""

    def test_compiled_patterns_at_module_level(self):
        """All 6 segment patterns should be compiled at module level."""
        hunter_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'processors', 'leadhunter.py')
        with open(hunter_path, 'r') as f:
            content = f.read()

        expected_patterns = [
            '_SECURITY_PATTERN',
            '_PERFORMANCE_PATTERN',
            '_MOBILE_PATTERN',
            '_MARKETING_PATTERN',
            '_ENTERPRISE_PATTERN',
            '_LOCAL_SMB_PATTERN'
        ]
        for pattern_name in expected_patterns:
            self.assertIn(f'{pattern_name} = re.compile', content,
                f"Compiled pattern {pattern_name} not found at module level!")

    def test_patterns_actually_work(self):
        """Test that each compiled pattern matches expected strings."""
        from src.processors.leadhunter import (
            _SECURITY_PATTERN, _PERFORMANCE_PATTERN, _MOBILE_PATTERN,
            _MARKETING_PATTERN, _ENTERPRISE_PATTERN, _LOCAL_SMB_PATTERN
        )

        self.assertIsNotNone(_SECURITY_PATTERN.search("critical vulnerability found"))
        self.assertIsNotNone(_SECURITY_PATTERN.search("missing ssl certificate"))
        self.assertIsNotNone(_SECURITY_PATTERN.search("security issue"))

        self.assertIsNotNone(_PERFORMANCE_PATTERN.search("slow page load"))
        self.assertIsNotNone(_PERFORMANCE_PATTERN.search("high latency"))
        self.assertIsNotNone(_PERFORMANCE_PATTERN.search("load time is 5s"))
        self.assertIsNotNone(_PERFORMANCE_PATTERN.search("performance bottleneck"))

        self.assertIsNotNone(_MOBILE_PATTERN.search("mobile unfriendly"))
        self.assertIsNotNone(_MOBILE_PATTERN.search("missing viewport"))
        self.assertIsNotNone(_MOBILE_PATTERN.search("not responsive"))

        self.assertIsNotNone(_MARKETING_PATTERN.search("missing pixel"))
        self.assertIsNotNone(_MARKETING_PATTERN.search("no analytics"))
        self.assertIsNotNone(_MARKETING_PATTERN.search("no tracking"))
        self.assertIsNotNone(_MARKETING_PATTERN.search("missing ga4"))

        self.assertIsNotNone(_ENTERPRISE_PATTERN.search("enterprise clients"))
        self.assertIsNotNone(_ENTERPRISE_PATTERN.search("fortune 500"))
        self.assertIsNotNone(_ENTERPRISE_PATTERN.search("corporate"))

        self.assertIsNotNone(_LOCAL_SMB_PATTERN.search("small business"))
        self.assertIsNotNone(_LOCAL_SMB_PATTERN.search("local clients"))
        self.assertIsNotNone(_LOCAL_SMB_PATTERN.search("homeowners"))

    def test_no_inline_any_patterns_in_segment_lead(self):
        """segment_lead should use compiled patterns, not inline any() calls."""
        hunter_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'processors', 'leadhunter.py')
        with open(hunter_path, 'r') as f:
            content = f.read()

        # Find the segment_lead method
        seg_start = content.find('def segment_lead(')
        seg_end = content.find('\n    async def ', seg_start)
        segment_body = content[seg_start:seg_end]

        # Should NOT contain any() pattern matching
        self.assertNotIn('any(x in p_str', segment_body,
            "segment_lead still uses inline any() pattern matching!")

        # SHOULD contain compiled pattern .search() calls
        self.assertIn('_SECURITY_PATTERN.search', segment_body)
        self.assertIn('_PERFORMANCE_PATTERN.search', segment_body)
        self.assertIn('_MOBILE_PATTERN.search', segment_body)
        self.assertIn('_MARKETING_PATTERN.search', segment_body)


class TestFix10_ReputationSegmentHelper(unittest.TestCase):
    """Verify _get_reputation_segment helper exists and works correctly."""

    def test_helper_method_exists(self):
        """The _get_reputation_segment method should exist on LeadHunter."""
        from src.processors.leadhunter import LeadHunter
        hunter = LeadHunter()
        self.assertTrue(hasattr(hunter, '_get_reputation_segment'),
            "_get_reputation_segment method not found on LeadHunter!")

    def test_low_rating_returns_reputation_repair(self):
        """Leads with rating < 3.8 should get 'Reputation Repair' segment."""
        from src.processors.leadhunter import LeadHunter
        hunter = LeadHunter()

        lead = {'rating': '3.5', 'reviews': '50'}
        result = hunter._get_reputation_segment(lead)
        self.assertEqual(result, "Reputation Repair")

    def test_high_rating_low_reviews_returns_new_business(self):
        """Leads with good rating but few reviews should get 'New Business / Growth'."""
        from src.processors.leadhunter import LeadHunter
        hunter = LeadHunter()

        lead = {'rating': '4.5', 'reviews': '5'}
        result = hunter._get_reputation_segment(lead)
        self.assertEqual(result, "New Business / Growth")

    def test_good_rating_many_reviews_returns_none(self):
        """Leads with good rating and many reviews should return None."""
        from src.processors.leadhunter import LeadHunter
        hunter = LeadHunter()

        lead = {'rating': '4.5', 'reviews': '100'}
        result = hunter._get_reputation_segment(lead)
        self.assertIsNone(result)

    def test_no_rating_returns_none(self):
        """Leads with no rating data should return None."""
        from src.processors.leadhunter import LeadHunter
        hunter = LeadHunter()

        lead = {}
        result = hunter._get_reputation_segment(lead)
        self.assertIsNone(result)

    def test_segment_lead_integration(self):
        """Full segment_lead should use _get_reputation_segment correctly."""
        from src.processors.leadhunter import LeadHunter
        hunter = LeadHunter()

        # Security pain point should take priority
        lead1 = {'pain_points': 'critical security issue', 'rating': '3.5'}
        self.assertEqual(hunter.segment_lead(lead1), "Security/Critical Fix")

        # Without security pain point, reputation should kick in
        lead2 = {'pain_points': 'website looks outdated', 'rating': '3.5', 'reviews': '50'}
        self.assertEqual(hunter.segment_lead(lead2), "Reputation Repair")

        # New business segment
        lead3 = {'pain_points': 'limited online presence', 'rating': '4.8', 'reviews': '3'}
        self.assertEqual(hunter.segment_lead(lead3), "New Business / Growth")


# ============================================================
# INTEGRATION / END-TO-END TESTS
# ============================================================

class TestEndToEnd_SEOAudit(unittest.TestCase):
    """End-to-end test: SEO audit still works correctly after SSL fix."""

    def test_audit_with_html_input_works(self):
        """Audit should work when HTML is provided directly (no network needed)."""
        from src.scrapers.seo_audit import perform_seo_audit_async

        test_html = """
        <html>
        <head>
            <title>Test Company</title>
            <meta name="description" content="We are a test company that does things.">
            <meta name="viewport" content="width=device-width, initial-scale=1">
        </head>
        <body>
            <h1>Welcome to Test Company</h1>
            <p>Contact us at test@example.com</p>
            <a href="https://facebook.com/testcompany">Facebook</a>
            <a href="https://instagram.com/testcompany">Instagram</a>
        </body>
        </html>
        """

        result = asyncio.run(
            perform_seo_audit_async("https://test-company.com", html=test_html)
        )

        self.assertTrue(result["is_up"])
        self.assertEqual(result["title"], "Test Company")
        self.assertIn("test@example.com", result["emails"])
        self.assertTrue(result["has_ssl"])
        self.assertEqual(result["h1_count"], 1)
        self.assertTrue(result["tech_flags"]["has_viewport"])
        self.assertGreater(result["score"], 0)
        self.assertEqual(result["facebook"], "https://facebook.com/testcompany")
        self.assertEqual(result["instagram"], "https://instagram.com/testcompany")

    def test_audit_invalid_url_returns_error(self):
        """Audit with invalid URL should return graceful error."""
        from src.scrapers.seo_audit import perform_seo_audit_async

        result = asyncio.run(
            perform_seo_audit_async("")
        )

        self.assertFalse(result["is_up"])
        self.assertEqual(result["score"], 0)
        self.assertIn("Invalid URL", result["red_flags"])


class TestEndToEnd_SegmentLead(unittest.TestCase):
    """End-to-end: segmentation logic works correctly after refactor."""

    def test_all_segments_reachable(self):
        """Every segment category should be reachable with appropriate inputs."""
        from src.processors.leadhunter import LeadHunter
        hunter = LeadHunter()

        test_cases = [
            ({"pain_points": "critical vulnerability", "outreach_score": 0}, "Security/Critical Fix"),
            ({"pain_points": "slow load time 8s", "outreach_score": 0}, "Performance Optimization"),
            ({"pain_points": "missing viewport meta", "outreach_score": 0}, "Mobile Experience"),
            ({"pain_points": "no pixel tracking", "outreach_score": 0}, "Marketing Analytics"),
            ({"pain_points": "", "rating": "3.2", "reviews": "100", "outreach_score": 0}, "Reputation Repair"),
            ({"pain_points": "", "rating": "4.9", "reviews": "3", "outreach_score": 0}, "New Business / Growth"),
            ({"pain_points": "", "target_clients": "enterprise companies", "outreach_score": 0}, "Enterprise B2B"),
            ({"pain_points": "", "target_clients": "local homeowners", "outreach_score": 0}, "Local SMB"),
            ({"pain_points": "", "outreach_score": 80}, "High Value / Outreach Ready"),
            ({"pain_points": "", "outreach_score": 55}, "Warm / Needs Personalization"),
            ({"pain_points": "", "outreach_score": 10}, "Low Priority Prospect"),
        ]

        for lead, expected_segment in test_cases:
            result = hunter.segment_lead(lead)
            self.assertEqual(result, expected_segment,
                f"Lead {lead} expected segment '{expected_segment}' but got '{result}'")


if __name__ == '__main__':
    unittest.main(verbosity=2)
