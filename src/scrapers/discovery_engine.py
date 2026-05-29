import asyncio
import hashlib
import os
import re
from urllib.parse import quote_plus
from typing import List, Optional
from playwright.async_api import (
    async_playwright,
    BrowserContext,
    Error as PlaywrightError,
    Route,
    TimeoutError as PlaywrightTimeoutError,
)
from src.scrapers.enrichment_engine import _install_ssrf_route_guard
from src.utils.supabase_helper import SupabaseHelper
from src.core.agentic_router import AgenticRouter
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


_BLOCKED_RESOURCE_TYPES = frozenset({"image", "font", "media"})
# Google Maps vector + raster tiles and Street View imagery — the bulk of the
# memory footprint during scroll. We keep document/script/xhr/fetch so the
# results panel still populates. The route handler chains BEFORE the SSRF guard
# so blocked-type requests short-circuit; legitimate requests fall through to
# the security check (registration order = reverse run order in Playwright).
_BLOCKED_URL_PATTERN = re.compile(
    r"(maps/vt(/|\?)|streetviewpixels|googleusercontent\.com|"
    r"gstatic\.com/.*\.(png|jpg|jpeg|webp|gif|svg|ico))",
    re.IGNORECASE,
)


async def _install_resource_block(context: BrowserContext) -> None:
    """Drop heavy media subresources during Google Maps scroll.

    Cuts ~80-150 MB off single-call peak on a starter-plan container by skipping
    tiles, images, fonts, and Street View pixels — none of which contribute to
    the result-container DOM we actually scrape. SSRF route guard still runs on
    everything that fell through (registered earlier = runs later)."""

    async def _handler(route: Route) -> None:
        req = route.request
        if req.resource_type in _BLOCKED_RESOURCE_TYPES or _BLOCKED_URL_PATTERN.search(
            req.url
        ):
            await route.abort()
            return
        await route.fallback()

    await context.route("**/*", _handler)


_MAX_SCROLL_ITERS = max(1, int(os.getenv("DISCOVERY_MAX_SCROLL_ITERS", "5")))
_MAX_CONTAINERS = max(1, int(os.getenv("DISCOVERY_MAX_CONTAINERS", "30")))


class DiscoveryEngine:
    def __init__(self):
        self.db = SupabaseHelper()
        self.router = AgenticRouter()

    async def find_leads(
        self, query: str, location: Optional[str] = None, max_results: int = 50
    ) -> List[dict]:
        """
        Discover leads using Google Maps scraping.
        """
        search_query = f"{query} in {location}" if location else query
        logger.info("Starting discovery for: %s", search_query)

        leads = []
        async with async_playwright() as p:
            # Enhanced browser launch for local development
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
            )
            # Defence-in-depth: the search URL host is hardcoded to google.com,
            # so structural SSRF via `query` is already blocked by quote_plus
            # + fixed host. The route guard catches the remaining theoretical
            # exposure — a 30x redirect chain that hops to an internal IP, or
            # a subresource fetched from a private host — and keeps the SSRF
            # invariant consistent with enrichment_engine.
            await _install_ssrf_route_guard(context)
            # Register the resource-block handler AFTER the SSRF guard so it
            # runs FIRST (Playwright: last-registered handler dispatches first).
            # `route.fallback()` for non-blocked requests defers to the SSRF
            # guard, preserving the route-guard invariant on redirect chains.
            await _install_resource_block(context)
            page = await context.new_page()

            try:
                # 1. Navigate to Google Maps
                url = f"https://www.google.com/maps/search/{quote_plus(search_query)}"
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                try:
                    await page.wait_for_selector(
                        "div[role='article'], a[href*='/maps/place/']", timeout=10000
                    )
                except PlaywrightTimeoutError:
                    pass

                # 2. Scroll to load more results
                # Google Maps uses a scrollable div for results. Usually it's the one with specific role.
                prev_count = 0
                for _ in range(_MAX_SCROLL_ITERS):
                    await page.mouse.wheel(0, 8000)
                    await asyncio.sleep(2)
                    current_containers = await page.query_selector_all(
                        "div[role='article'], a[href*='/maps/place/']"
                    )
                    current_count = len(current_containers) if current_containers else 0
                    if current_count >= max_results or current_count == prev_count:
                        break
                    prev_count = current_count

                # 3. Extract result containers
                containers = await page.query_selector_all(
                    "div[role='article'], a[href*='/maps/place/']"
                )
                if not containers:
                    logger.warning("No result containers found for: %s", search_query)
                    return []

                logger.info("Found %d potential result containers.", len(containers))

                hard_cap = min(max_results, _MAX_CONTAINERS)
                for container in containers[:hard_cap]:
                    try:
                        lead_data = await self._extract_lead_data(page, container)
                        if lead_data:
                            leads.append(lead_data)
                    except (PlaywrightError, PlaywrightTimeoutError) as inner_e:
                        logger.warning(
                            "Error parsing single discovery result: %s", inner_e
                        )
                        continue

            except (PlaywrightError, PlaywrightTimeoutError) as e:
                logger.error(
                    "Error during lead discovery process: %s", e, exc_info=True
                )
            finally:
                await browser.close()

        # Deduplicate leads by unique_key
        unique_leads = {}
        for lead in leads:
            unique_leads[lead["unique_key"]] = lead
        leads = list(unique_leads.values())

        logger.info("Discovery complete. Found %d unique leads.", len(leads))
        return leads

    async def _extract_lead_data(self, page, container) -> Optional[dict]:
        """Extracts lead data from a single result container."""
        # a. Extract Name
        name_elem = await container.query_selector(
            ".fontHeadlineSmall, .qBF1Pd, h3, .fontBodyMedium > span:first-child, [data-jspb]"
        )
        name = (
            await name_elem.inner_text()
            if name_elem
            else await container.get_attribute("aria-label")
        )

        if not name:
            return None

        # b. Extract Maps URL & unique_key
        maps_url = ""
        link_elem = await container.query_selector("a[href*='/maps/place/']")
        if link_elem:
            maps_url = await link_elem.get_attribute("href") or ""

        tag_name = await container.evaluate("node => node.tagName.toLowerCase()")
        if not maps_url and tag_name == "a":
            maps_url = await container.get_attribute("href") or ""

        # Generate stable unique_key
        if maps_url and "!1s" in maps_url:
            unique_key = maps_url.split("!1s")[1].split("!")[0]
        elif maps_url:
            unique_key = maps_url.split("/place/")[1].split("/")[0]
        else:
            # MD5 here is a deduplication identifier, not a cryptographic
            # signature: we hash the business name to produce a stable 64-bit
            # tag when Google Maps gives us no place-ID URL. usedforsecurity=
            # False documents that intent and silences Bandit / Semgrep MD5
            # lints. Truncating to 16 hex chars is enough since collisions
            # would just route two distinct businesses to the same row, which
            # the human review queue catches.
            unique_key = hashlib.md5(name.encode(), usedforsecurity=False).hexdigest()[
                :16
            ]

        # c. Extract Rating
        rating_elem = await container.query_selector(
            "span[aria-label*='stars'], .MW4T7d"
        )
        rating = await rating_elem.get_attribute("aria-label") if rating_elem else None

        # d. Extract Website
        website = None
        website_selectors = [
            "a[data-value='Website']",
            "a[aria-label*='Website']",
            "a[href*='http']:not([href*='google.com'])",
            "a.l761vp",  # Common class for website links in Maps
            "a.cs939e",
        ]
        for selector in website_selectors:
            website_elem = await container.query_selector(selector)
            if website_elem:
                website_href = await website_elem.get_attribute("href")
                if website_href and "google.com" not in website_href:
                    website = website_href
                    break

        # Fallback: Click container to see more details in sidebar
        if not website:
            try:
                await container.click()
                await asyncio.sleep(2)  # Wait for panel
                # Look in the whole page for the website link now that the panel is open
                panel_website_elem = await page.query_selector(
                    "a[data-item-id='authority'], a[aria-label*='Website']"
                )
                if panel_website_elem:
                    website_href = await panel_website_elem.get_attribute("href")
                    if website_href and "google.com" not in website_href:
                        website = website_href
            except (PlaywrightError, PlaywrightTimeoutError):
                pass

        # e. Extract Phone
        phone = None
        # Cap input to bound regex CPU even on pathological injected text —
        # real Maps card text is far under 5 KB; this is belt-and-braces.
        all_text = (await container.inner_text())[:5000]
        phone_match = re.search(
            r"(\+?\d{1,4}[\s.-]?)?(\(?\d{3}\)?[\s.-]?)?\d{3}[\s.-]?\d{4}", all_text
        )
        if phone_match:
            phone = phone_match.group()

        # Fallback phone from panel
        if not phone:
            try:
                panel_phone_elem = await page.query_selector(
                    "button[data-tooltip*='phone'], button[aria-label*='Phone']"
                )
                if panel_phone_elem:
                    phone = await panel_phone_elem.inner_text()
            except (PlaywrightError, PlaywrightTimeoutError):
                pass

        # f. Extract Address — panel-only (not on result cards). The website +
        # phone fallbacks above may have already opened the side panel; query
        # first, click as a last resort. Prefer `aria-label` (formatted as
        # "Address: 123 Main St, City") over button inner text, which can
        # include leading icons / chevrons.
        address = await self._extract_address(page, container)

        return {
            "name": name.strip(),
            "unique_key": unique_key,
            "website": website,
            "phone": phone,
            "rating": self._parse_rating(rating),
            "audit_status": "Pending",
            # Provenance — lets `lead_source = 'google_maps'` queries find these
            # rows for cleanup, segmentation, or per-source analytics.
            # See BUGS.md Round 3 A.
            "lead_source": "google_maps",
            "address": address,
        }

    @staticmethod
    async def _extract_address(page, container) -> Optional[str]:
        """Pull the street address out of the Google Maps side panel.

        Returns None when the address can't be located — never raises.
        Selector targets (matched in order):
          1. `button[data-item-id='address']` — the canonical "Copy address" button
          2. `button[aria-label^='Address:']` — aria-label fallback
          3. `[data-tooltip='Copy address']` — older Maps DOM
        """
        selectors = [
            "button[data-item-id='address']",
            "button[aria-label^='Address:']",
            "[data-tooltip='Copy address']",
        ]
        try:
            elem = None
            for sel in selectors:
                elem = await page.query_selector(sel)
                if elem:
                    break
            if not elem:
                # Panel may not be open; try clicking the result card.
                try:
                    await container.click()
                    await asyncio.sleep(1.2)
                    for sel in selectors:
                        elem = await page.query_selector(sel)
                        if elem:
                            break
                except (PlaywrightError, PlaywrightTimeoutError):
                    return None
            if not elem:
                return None
            aria = await elem.get_attribute("aria-label") or ""
            raw = (
                aria[len("Address:") :]
                if aria.lower().startswith("address:")
                else (await elem.inner_text() or "")
            )
            # The Maps "Copy address" button includes a leading icon glyph
            # that comes through `inner_text()` as a whitespace prefix
            # (often U+E0F0-range Material Icons + `\n`). Collapse all
            # whitespace into single spaces and drop anything before the
            # first alphanumeric/diacritic character.
            text = re.sub(r"\s+", " ", raw).strip()
            m = re.search(r"[\w].*", text, flags=re.UNICODE)
            return (m.group(0) if m else text) or None
        except (PlaywrightError, PlaywrightTimeoutError):
            return None

    @staticmethod
    def _parse_rating(rating_text: Optional[str]) -> Optional[float]:
        """Safely parse a rating string like '4.5 stars' to a float."""
        if not rating_text:
            return None
        try:
            # Handle formats like "4.5 stars", "4,5 stars", "Rated 4.5"
            numbers = re.findall(r"[\d]+[.,]?\d*", rating_text)
            if numbers:
                return float(numbers[0].replace(",", "."))
        except (ValueError, IndexError):
            pass
        return None

    async def enrich_and_save(self, leads: List[dict]):
        """
        Optional AI enrichment and saving to Supabase.
        """
        if not leads:
            return

        logger.info("Saving %d discovered leads to database...", len(leads))
        self.db.upsert_leads(leads)
