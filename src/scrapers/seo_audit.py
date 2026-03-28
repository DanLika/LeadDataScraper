import asyncio
import aiohttp
import ssl
import re
import time
from bs4 import BeautifulSoup
from typing import Optional

def calculate_seo_score(results: dict) -> int:
    """Calculates a numerical SEO Health Score from 0-100."""
    score = 0
    # Core SEO (max 40)
    if results.get("has_ssl"): score += 10
    if results.get("title"): score += 10
    if results.get("meta_description"): score += 10
    if results.get("h1_count", 0) == 1: score += 10
    
    # Technical & Tracking (max 30)
    flags = results.get("tech_flags", {})
    if flags.get("has_viewport"): score += 10
    if flags.get("has_google_analytics") or flags.get("has_gtm"): score += 10
    if flags.get("has_facebook_pixel"): score += 10
    
    # Advanced & Health (max 30)
    if results.get("response_time", 0) < 2.0: score += 10
    if flags.get("has_robots_txt") and flags.get("has_sitemap"): score += 10
    if len(results.get("red_flags", [])) == 0: score += 10
    
    return min(score, 100)

def _parse_basic_seo(soup: BeautifulSoup, results: dict) -> None:
    """Extracts basic SEO tags and updates results."""
    results["title"] = soup.title.string.strip() if soup.title else None
    if results["title"]:
        results["title_length"] = len(results["title"])
        if results["title_length"] < 30 or results["title_length"] > 70:
            results["red_flags"].append(f"Title Length Warning ({results['title_length']} chars)")
    else:
        results["red_flags"].append("Missing Title Tag")
        results["title_length"] = 0

    desc = soup.find('meta', attrs={'name': 'description'})
    results["meta_description"] = desc['content'].strip() if desc and 'content' in desc.attrs else None
    if results["meta_description"]:
        results["meta_length"] = len(results["meta_description"])
        if results["meta_length"] < 70 or results["meta_length"] > 160:
            results["red_flags"].append(f"Meta Description Length Warning ({results['meta_length']} chars)")
    else:
        results["red_flags"].append("Missing Meta Description")
        results["meta_length"] = 0

    h1s = soup.find_all('h1')
    results["h1_count"] = len(h1s)
    if results["h1_count"] == 0: results["red_flags"].append("Missing H1 Header")
    elif results["h1_count"] > 1: results["red_flags"].append("Multiple H1 Headers Detected")

    h2s = soup.find_all('h2')
    results["h2_count"] = len(h2s)

def _detect_tech_flags(html_lower: str, soup: BeautifulSoup, results: dict) -> None:
    """Detects tracking tags and viewport meta."""
    flags = results["tech_flags"]
    # Google Analytics (GA4/UA)
    if any(x in html_lower for x in ["googletagmanager.com/gtag/js", "google-analytics.com/analytics.js", "ua-", "g-"]):
        flags["has_google_analytics"] = True

    # Google Tag Manager
    if "googletagmanager.com/gtm.js" in html_lower or "gtm-" in html_lower:
        flags["has_gtm"] = True

    # Facebook Pixel
    if "connect.facebook.net/en_us/fbevents.js" in html_lower or "fbq(" in html_lower:
        flags["has_facebook_pixel"] = True

    # LinkedIn
    if "snap.licdn.com/li.lms-analytics/insight.min.js" in html_lower:
        flags["has_linkedin_insight"] = True

    # TikTok Pixel
    if "analytics.tiktok.com/i18n/pixel/events.js" in html_lower or "ttq.load" in html_lower:
        flags["has_tiktok_pixel"] = True

    # Pinterest Tag
    if "tag.pinterest.com/qt.js" in html_lower or "pintrk(" in html_lower:
        flags["has_pinterest_tag"] = True

    # Hotjar
    if "static.hotjar.com" in html_lower or "_hjsettings" in html_lower:
        flags["has_hotjar"] = True

    # HubSpot
    if "js.hs-scripts.com" in html_lower or "js.hsadspixel.net" in html_lower:
        flags["has_hubspot"] = True

    # Viewport (Mobile)
    if soup.find('meta', attrs={'name': 'viewport'}):
        flags["has_viewport"] = True
    else:
        results["red_flags"].append("Missing Viewport (Not Mobile Friendly)")

    # Infrastructure (Robots/Sitemaps)
    if "robots.txt" in html_lower or 'rel="robots"' in html_lower:
        flags["has_robots_txt"] = True
    if "sitemap.xml" in html_lower or 'type="application/xml"' in html_lower and "sitemap" in html_lower:
        flags["has_sitemap"] = True

def _detect_cms(html_lower: str, results: dict) -> None:
    """Detects CMS platforms."""
    cms_map = {
        "WordPress": ["/wp-content/", "/wp-includes/", "wp-json", "wordpress"],
        "Shopify": ["cdn.shopify.com", "Shopify.shop", "shopify-payment-button", "shopify.com"],
        "Wix": ["wix.com", "_wix_", "wix-static", "wix-site-id"],
        "Squarespace": ["static1.squarespace.com", "squarespace-config", "squarespace.com"],
        "Webflow": ["data-wf-page", "webflow.js", "webflow.com"],
        "Joomla": ["/media/system/js/", "/components/com_", "joomla"],
        "Drupal": ["Drupal.settings", "/sites/default/files/", "drupal"]
    }

    for cms, patterns in cms_map.items():
        if any(p in html_lower for p in patterns):
            results["cms"] = cms
            results["tech_stack"].append(cms)
            break

def _detect_portal_and_social(soup: BeautifulSoup, results: dict) -> None:
    """Finds portal links and social media links."""
    portal_keywords = ["login", "portal", "dashboard", "log in", "sign in", "my account", "client-area"]
    for link in soup.find_all('a', href=True):
        link_text = link.get_text().lower()
        href = link['href']
        if any(kw in link_text for kw in portal_keywords):
            results["tech_flags"]["has_portal"] = True

        # Basic Social Link detection (regex fallback)
        if 'facebook.com' in href and not results.get("facebook"):
            if not any(x in href.lower() for x in ['sharer', 'messenger', 'plugins']):
                results["facebook"] = href
        if 'instagram.com' in href and not results.get("instagram"):
            if not any(x in href.lower() for x in ['explore', 'p/', 'reels']):
                results["instagram"] = href
        if 'linkedin.com' in href and not results.get("linkedin"):
            if 'company' in href or '/in/' in href:
                results["linkedin"] = href
        if 'tiktok.com' in href and not results.get("tiktok"):
            if '@' in href and not any(x in href for x in ['/share', '/video/']):
                results["tiktok"] = href
        if 'pinterest.com' in href and not results.get("pinterest"):
            if not any(x in href for x in ['/pin/', '/search/', '/explore/']):
                results["pinterest"] = href

def _extract_emails_and_text(html: str, soup: BeautifulSoup, results: dict) -> None:
    """Extracts emails and sets the page text."""
    email_regex = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    results["emails"] = list(set(re.findall(email_regex, html, re.IGNORECASE)))
    results["page_text"] = soup.get_text(separator=' ', strip=True)[:3000]

async def perform_seo_audit_async(url: str, html: Optional[str] = None):
    """
    Performs an asynchronous technical & SEO audit.
    Detects tracking pixels, CMS, mobile readiness, and portal links.
    """
    if not url or not isinstance(url, str):
        return {
            "url": url, "is_up": False, "score": 0, "tech_flags": {}, "red_flags": ["Invalid URL"]
        }

    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

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
            "has_sitemap": False
        },
        "cms": None,
        "red_flags": [],
        "tech_stack": [],
        "emails": []
    }

    if url.startswith('https://'):
        results["has_ssl"] = True

    try:
        timeout = aiohttp.ClientTimeout(total=20)
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        }

        if not html:
            start_time = time.time()
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                try:
                    async with session.get(url, timeout=12) as response:
                        html = await response.text()
                        results["is_up"] = True
                        results["response_time"] = round(time.time() - start_time, 2)
                except (aiohttp.ClientConnectorSSLError, ssl.SSLError):
                    results["red_flags"].append("SSL Certificate Error")
                    results["has_ssl"] = False
                    async with session.get(url, timeout=12, ssl=ssl_ctx) as response:
                        html = await response.text()
                        results["is_up"] = True
                        results["response_time"] = round(time.time() - start_time, 2)
        else:
            results["is_up"] = True
            results["response_time"] = 0.1

    except Exception as e:
        results["red_flags"].append(f"Connection Failed: {str(e)}")
        return results

    if results["is_up"]:
        soup = BeautifulSoup(html, 'html.parser')
        html_lower = html.lower()

        _parse_basic_seo(soup, results)
        _detect_tech_flags(html_lower, soup, results)
        _detect_cms(html_lower, results)
        _detect_portal_and_social(soup, results)
        
        # Score Calculation
        results["score"] = calculate_seo_score(results)

        _extract_emails_and_text(html, soup, results)

    return results

if __name__ == "__main__":
    # Test
    async def test():
        test_url = "https://google.com"
        print(f"Auditing {test_url}...")
        res = await perform_seo_audit_async(test_url)
        import json
        print(json.dumps(res, indent=2))
    
    asyncio.run(test())
