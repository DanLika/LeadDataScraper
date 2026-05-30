import asyncio
import logging
import aiohttp
import ssl
import re
import time
from bs4 import BeautifulSoup
from typing import Optional

from src.errors import AuditFetchError
from src.utils.ssrf_guard import SSRFError, SSRFGuardResolver, assert_safe_scheme

logger = logging.getLogger(__name__)

# Maximum HTTP response body the auditor will buffer into memory before
# raising. Real HTML pages are <500 KB; 2 MB is a comfortable ceiling
# that protects worker memory against a slow-trickle attack streaming
# 100 MB at just-under-timeout throughput. `aiohttp.ClientTimeout` only
# bounds wall-clock, not bytes — see vibe-security audit finding M6.
# The downstream `html[:50_000]` regex slice in `_extract_emails_and_text`
# happens AFTER the full body is in RAM, so it does NOT protect the worker.
MAX_HTML_BYTES = 2 * 1024 * 1024  # 2 MB

# Bot-block detection thresholds — see PR #274 Phase 9.10 Finding E.
# Sites that respond with these statuses are returning a "blocked" body
# (typical: "403 Forbidden" or a Cloudflare interstitial). Bodies shorter
# than the byte threshold can't carry enough signal for a real audit.
_BOT_BLOCKED_STATUSES = frozenset({401, 403, 429})
_MIN_AUDITABLE_CONTENT_BYTES = 500

# Pre-compiled regex patterns for social URL filtering (performance optimization)
FB_EXCLUDE = re.compile(r"sharer|messenger|plugins")
IG_EXCLUDE = re.compile(r"explore|p/|reels")
TT_EXCLUDE = re.compile(r"/share|/video/")
PIN_EXCLUDE = re.compile(r"/pin/|/search/|/explore/")


def calculate_seo_score(results: dict) -> int:
    """Calculates a numerical SEO Health Score from 0-100."""
    score = 0
    # Core SEO (max 40)
    if results.get("has_ssl"):
        score += 10
    if results.get("title"):
        score += 10
    if results.get("meta_description"):
        score += 10
    if results.get("h1_count", 0) == 1:
        score += 10

    # Technical & Tracking (max 30)
    flags = results.get("tech_flags", {})
    if flags.get("has_viewport"):
        score += 10
    if flags.get("has_google_analytics") or flags.get("has_gtm"):
        score += 10
    if flags.get("has_facebook_pixel"):
        score += 10

    # Advanced & Health (max 30)
    if results.get("response_time", 0) < 2.0:
        score += 10
    if flags.get("has_robots_txt") and flags.get("has_sitemap"):
        score += 10
    if len(results.get("red_flags", [])) == 0:
        score += 10

    return min(score, 100)


def _check_meta_tags(soup: BeautifulSoup, results: dict):
    """Check title, meta description, and canonical URL."""
    # ``soup.title.string`` can be None even when ``soup.title`` exists —
    # an empty ``<title></title>`` or ``<title><br></title>`` both yield
    # None, then ``.strip()`` raises ``AttributeError: 'NoneType' object
    # has no attribute 'strip'`` and the whole audit crashes. Phase 9.10
    # (PR #274 Finding B) caught this on Sotheby's homepage.
    title_text = soup.title.string if soup.title else None
    results["title"] = title_text.strip() if title_text else None
    if results["title"]:
        results["title_length"] = len(results["title"])
        if results["title_length"] < 30 or results["title_length"] > 70:
            results["red_flags"].append(
                f"Title Length Warning ({results['title_length']} chars)"
            )
    else:
        results["red_flags"].append("Missing Title Tag")
        results["title_length"] = 0

    desc = soup.find("meta", attrs={"name": "description"})
    # Same NoneType guard for ``<meta name="description" content="">`` —
    # ``'content' in desc.attrs`` is True but the value is empty/None.
    desc_content = desc["content"] if (desc and "content" in desc.attrs) else None
    results["meta_description"] = desc_content.strip() if desc_content else None
    if results["meta_description"]:
        results["meta_length"] = len(results["meta_description"])
        if results["meta_length"] < 70 or results["meta_length"] > 160:
            results["red_flags"].append(
                f"Meta Description Length Warning ({results['meta_length']} chars)"
            )
    else:
        results["red_flags"].append("Missing Meta Description")
        results["meta_length"] = 0


def _analyze_headings(soup: BeautifulSoup, results: dict):
    """Check H1/H2 presence and count."""
    h1s = soup.find_all("h1")
    results["h1_count"] = len(h1s)
    if results["h1_count"] == 0:
        results["red_flags"].append("Missing H1 Header")
    elif results["h1_count"] > 1:
        results["red_flags"].append("Multiple H1 Headers Detected")

    h2s = soup.find_all("h2")
    results["h2_count"] = len(h2s)


def _detect_tracking_and_tech(soup: BeautifulSoup, html_lower: str, results: dict):
    """Detect analytics, tracking pixels, and mobile viewport."""
    # Google Analytics (GA4/UA)
    if any(
        x in html_lower
        for x in [
            "googletagmanager.com/gtag/js",
            "google-analytics.com/analytics.js",
            "ua-",
            "g-",
        ]
    ):
        results["tech_flags"]["has_google_analytics"] = True

    # Google Tag Manager
    if "googletagmanager.com/gtm.js" in html_lower or "gtm-" in html_lower:
        results["tech_flags"]["has_gtm"] = True

    # Facebook Pixel
    if "connect.facebook.net/en_us/fbevents.js" in html_lower or "fbq(" in html_lower:
        results["tech_flags"]["has_facebook_pixel"] = True

    # LinkedIn
    if "snap.licdn.com/li.lms-analytics/insight.min.js" in html_lower:
        results["tech_flags"]["has_linkedin_insight"] = True

    # TikTok Pixel
    if (
        "analytics.tiktok.com/i18n/pixel/events.js" in html_lower
        or "ttq.load" in html_lower
    ):
        results["tech_flags"]["has_tiktok_pixel"] = True

    # Pinterest Tag
    if "tag.pinterest.com/qt.js" in html_lower or "pintrk(" in html_lower:
        results["tech_flags"]["has_pinterest_tag"] = True

    # Hotjar
    if "static.hotjar.com" in html_lower or "_hjsettings" in html_lower:
        results["tech_flags"]["has_hotjar"] = True

    # HubSpot
    if "js.hs-scripts.com" in html_lower or "js.hsadspixel.net" in html_lower:
        results["tech_flags"]["has_hubspot"] = True

    # Viewport (Mobile)
    if soup.find("meta", attrs={"name": "viewport"}):
        results["tech_flags"]["has_viewport"] = True
    else:
        results["red_flags"].append("Missing Viewport (Not Mobile Friendly)")


def _detect_cms(html_lower: str, results: dict):
    """Detect CMS/platform from HTML patterns."""
    cms_map = {
        "WordPress": ["/wp-content/", "/wp-includes/", "wp-json", "wordpress"],
        "Shopify": [
            "cdn.shopify.com",
            "Shopify.shop",
            "shopify-payment-button",
            "shopify.com",
        ],
        "Wix": ["wix.com", "_wix_", "wix-static", "wix-site-id"],
        "Squarespace": [
            "static1.squarespace.com",
            "squarespace-config",
            "squarespace.com",
        ],
        "Webflow": ["data-wf-page", "webflow.js", "webflow.com"],
        "Joomla": ["/media/system/js/", "/components/com_", "joomla"],
        "Drupal": ["Drupal.settings", "/sites/default/files/", "drupal"],
    }

    for cms, patterns in cms_map.items():
        if any(p in html_lower for p in patterns):
            results["cms"] = cms
            results["tech_stack"].append(cms)
            break


def _detect_infrastructure(html_lower: str, results: dict):
    """Check for robots.txt and sitemap references."""
    if "robots.txt" in html_lower or 'rel="robots"' in html_lower:
        results["tech_flags"]["has_robots_txt"] = True
    if (
        "sitemap.xml" in html_lower
        or 'type="application/xml"' in html_lower
        and "sitemap" in html_lower
    ):
        results["tech_flags"]["has_sitemap"] = True


def _detect_portals_and_socials(soup: BeautifulSoup, results: dict):
    """Detect client portals and extract social media links."""
    portal_keywords = [
        "login",
        "portal",
        "dashboard",
        "log in",
        "sign in",
        "my account",
        "client-area",
    ]
    for link in soup.find_all("a", href=True):
        link_text = link.get_text().lower()
        href = link["href"]
        if any(kw in link_text for kw in portal_keywords):
            results["tech_flags"]["has_portal"] = True

        # Social Link detection using compiled regex
        href_lower = href.lower()
        if "facebook.com" in href and not results.get("facebook"):
            if not FB_EXCLUDE.search(href_lower):
                results["facebook"] = href
        if "instagram.com" in href and not results.get("instagram"):
            if not IG_EXCLUDE.search(href_lower):
                results["instagram"] = href
        if "linkedin.com" in href and not results.get("linkedin"):
            if "company" in href or "/in/" in href:
                results["linkedin"] = href
        if "tiktok.com" in href and not results.get("tiktok"):
            if "@" in href and not TT_EXCLUDE.search(href):
                results["tiktok"] = href
        if "pinterest.com" in href and not results.get("pinterest"):
            if not PIN_EXCLUDE.search(href):
                results["pinterest"] = href


def _extract_emails_and_text(soup: BeautifulSoup, html: str, results: dict):
    """Extract email addresses and page text from HTML.

    `html` is attacker-controllable (scraped page body). The legacy
    email regex `\\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,24}\\b`
    is O(n²) under `re.findall` on pathological inputs (e.g.,
    `"a@" + "a." * 5000 + "x"` — 296 ms for 10 KB of payload, scales
    quadratically). Two-layer defense:
      (a) cap the input passed to `findall` at 200 KB — emails in real
          pages are in the head/footer, not buried past 200 KB.
      (b) pin the bound in `tests/test_redos.py` so a future cap
          removal trips CI.
    """
    email_regex = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}\b"
    bounded = html[:50_000]  # ReDoS cap — see commit + tests/test_redos.py
    results["emails"] = list(set(re.findall(email_regex, bounded, re.IGNORECASE)))
    results["page_text"] = soup.get_text(separator=" ", strip=True)[:3000]


async def perform_seo_audit_async(url: str, html: Optional[str] = None):
    """
    Performs an asynchronous technical & SEO audit.
    Detects tracking pixels, CMS, mobile readiness, and portal links.
    """
    if not url or not isinstance(url, str):
        return {
            "url": url,
            "is_up": False,
            "score": 0,
            "tech_flags": {},
            "red_flags": ["Invalid URL"],
        }

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        assert_safe_scheme(url)
    except SSRFError as e:
        return {
            "url": url,
            "is_up": False,
            "score": 0,
            "tech_flags": {},
            "red_flags": [f"Blocked URL: {e}"],
        }

    results = {
        "url": url,
        "is_up": False,
        "has_ssl": False,
        "score": 0,
        "response_time": 0,
        "title": None,
        "meta_description": None,
        "h1_count": 0,
        "tech_flags": {
            "has_viewport": False,
            "has_google_analytics": False,
            "has_gtm": False,
            "has_facebook_pixel": False,
            "has_linkedin_insight": False,
            "has_tiktok_pixel": False,
            "has_pinterest_tag": False,
            "has_hotjar": False,
            "has_hubspot": False,
            "has_portal": False,
            "has_robots_txt": False,
            "has_sitemap": False,
        },
        "cms": None,
        "red_flags": [],
        "tech_stack": [],
        "emails": [],
    }

    if url.startswith("https://"):
        results["has_ssl"] = True

    try:
        timeout = aiohttp.ClientTimeout(total=20)

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        }

        if not html:
            start_time = time.time()
            connector = aiohttp.TCPConnector(resolver=SSRFGuardResolver())
            async with aiohttp.ClientSession(
                timeout=timeout, headers=headers, connector=connector
            ) as session:
                try:
                    async with session.get(url, timeout=12) as response:
                        # Body-size cap: read MAX_HTML_BYTES + 1 so we can
                        # detect overshoot without buffering the entire
                        # attacker-controlled stream. See MAX_HTML_BYTES
                        # docstring above for the threat model.
                        raw = await response.content.read(MAX_HTML_BYTES + 1)
                        if len(raw) > MAX_HTML_BYTES:
                            logger.warning(
                                "seo_audit body exceeds cap for %s: %d bytes",
                                url,
                                len(raw),
                            )
                            raise AuditFetchError(
                                f"Response body exceeds {MAX_HTML_BYTES} bytes"
                            )
                        # `response.charset` may be None when the server
                        # omits a charset hint; default to UTF-8 and use
                        # errors="replace" so malformed bytes don't raise
                        # UnicodeDecodeError up through the auditor.
                        html = raw.decode(response.charset or "utf-8", errors="replace")
                        results["is_up"] = True
                        results["response_time"] = round(time.time() - start_time, 2)
                        results["http_status"] = response.status
                        # Bot-blocked detection — without this, a 403 page
                        # passes its small "403 Forbidden" body downstream
                        # and Gemini hallucinates pain_points that look
                        # grounded but reference signals (no Google Analytics,
                        # no Facebook Pixel) inferred from an empty
                        # tech_flags dict, not from the real homepage.
                        # See PR #274 Phase 9.10 Finding E.
                        if (
                            response.status in _BOT_BLOCKED_STATUSES
                            or len(html) < _MIN_AUDITABLE_CONTENT_BYTES
                        ):
                            results["is_bot_blocked"] = True
                            results["last_error"] = f"site_blocked_{response.status}"
                            results["red_flags"].append(
                                f"Bot-blocked (HTTP {response.status}, "
                                f"{len(html)} bytes) — Gemini analysis skipped"
                            )
                except (aiohttp.ClientConnectorSSLError, ssl.SSLError):
                    results["red_flags"].append("SSL Certificate Error")
                    results["has_ssl"] = False
                except SSRFError as e:
                    results["red_flags"].append(f"Blocked URL: {e}")
        else:
            results["is_up"] = True
            results["response_time"] = 0.1

    except AuditFetchError:
        # Propagate to the orchestrator's per-lead handler in
        # ParallelAuditor.audit_single_lead, which records the lead with
        # audit_status='Failed' and surfaces the message in last_error.
        # Must NOT be swallowed by the generic `except Exception` below,
        # which would convert it to a "Connection Failed: ..." red_flag
        # and obscure the body-cap signal.
        raise
    except Exception as e:
        results["red_flags"].append(f"Connection Failed: {str(e)}")
        return results

    if results["is_up"]:
        soup = BeautifulSoup(html, "html.parser")
        html_lower = html.lower()

        # Run all analysis steps
        _check_meta_tags(soup, results)
        _analyze_headings(soup, results)
        _detect_tracking_and_tech(soup, html_lower, results)
        _detect_cms(html_lower, results)
        _detect_infrastructure(html_lower, results)
        _detect_portals_and_socials(soup, results)

        # Score calculation
        results["score"] = calculate_seo_score(results)

        # Email extraction and page text
        _extract_emails_and_text(soup, html, results)

        # Clear page_text on bot-blocked sites so downstream Gemini callers
        # (analyze_pain_points_async / enrich_business_data_async / outreach
        # hook generation) all short-circuit on their existing
        # ``if not page_text: return`` guards. Without this, a 403 page's
        # ~25-byte body would be analysed by Gemini and yield plausible-
        # but-ungrounded pain points — see PR #274 Phase 9.10 Finding E.
        if results.get("is_bot_blocked"):
            results["page_text"] = ""

    return results
