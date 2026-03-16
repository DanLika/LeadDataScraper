import asyncio
import json
import os
import hashlib
import re
from typing import List, Optional
from playwright.async_api import async_playwright
from src.utils.supabase_helper import SupabaseHelper
from src.core.agentic_router import AgenticRouter
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

class DiscoveryEngine:
    def __init__(self):
        self.db = SupabaseHelper()
        self.router = AgenticRouter()

    async def find_leads(self, query: str, location: Optional[str] = None, max_results: int = 50) -> List[dict]:
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
            page = await context.new_page()

            try:
                # 1. Navigate to Google Maps
                url = f"https://www.google.com/maps/search/{search_query.replace(' ', '+')}"
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(5) # Give it a moment to load markers

                # 2. Scroll to load more results
                # Google Maps uses a scrollable div for results. Usually it's the one with specific role.
                prev_count = 0
                for _ in range(10):
                    await page.mouse.wheel(0, 8000)
                    await asyncio.sleep(2)
                    current_containers = await page.query_selector_all("div[role='article'], a[href*='/maps/place/']")
                    current_count = len(current_containers) if current_containers else 0
                    if current_count >= max_results or current_count == prev_count:
                        break
                    prev_count = current_count

                # 3. Extract result containers
                containers = await page.query_selector_all("div[role='article'], a[href*='/maps/place/']")
                if not containers:
                    logger.warning("No result containers found for: %s", search_query)
                    return []

                logger.info("Found %d potential result containers.", len(containers))

                for container in containers[:max_results]:
                    try:
                        # a. Extract Name
                        name_elem = await container.query_selector(".fontHeadlineSmall, .qBF1Pd, h3, .fontBodyMedium > span:first-child, [data-jspb]")
                        name = await name_elem.inner_text() if name_elem else await container.get_attribute("aria-label")

                        if not name:
                            continue

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
                            unique_key = hashlib.md5(name.encode()).hexdigest()[:16]

                        # c. Extract Rating
                        rating_elem = await container.query_selector("span[aria-label*='stars'], .MW4T7d")
                        rating = await rating_elem.get_attribute("aria-label") if rating_elem else None

                        # d. Extract Website
                        website = None
                        website_selectors = [
                            "a[data-value='Website']",
                            "a[aria-label*='Website']",
                            "a[href*='http']:not([href*='google.com'])",
                            "a.l761vp", # Common class for website links in Maps
                            "a.cs939e"
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
                                await asyncio.sleep(2) # Wait for panel
                                # Look in the whole page for the website link now that the panel is open
                                panel_website_elem = await page.query_selector("a[data-item-id='authority'], a[aria-label*='Website']")
                                if panel_website_elem:
                                    website_href = await panel_website_elem.get_attribute("href")
                                    if website_href and "google.com" not in website_href:
                                        website = website_href
                            except Exception:
                                pass

                        # e. Extract Phone
                        phone = None
                        all_text = await container.inner_text()
                        phone_match = re.search(r'(\+?\d{1,4}[\s.-]?)?(\(?\d{3}\)?[\s.-]?)?\d{3}[\s.-]?\d{4}', all_text)
                        if phone_match:
                            phone = phone_match.group()

                        # Fallback phone from panel
                        if not phone:
                            try:
                                panel_phone_elem = await page.query_selector("button[data-tooltip*='phone'], button[aria-label*='Phone']")
                                if panel_phone_elem:
                                    phone = await panel_phone_elem.inner_text()
                            except Exception:
                                pass

                        leads.append({
                            "name": name.strip(),
                            "unique_key": unique_key,
                            "website": website,
                            "phone": phone,
                            "rating": self._parse_rating(rating),
                            "audit_status": "Pending"
                        })
                    except Exception as inner_e:
                        logger.warning("Error parsing single discovery result: %s", inner_e)
                        continue

            except Exception as e:
                logger.error("Error during lead discovery process: %s", e, exc_info=True)
            finally:
                await browser.close()

        # Deduplicate leads by unique_key
        unique_leads = {}
        for lead in leads:
            unique_leads[lead["unique_key"]] = lead
        leads = list(unique_leads.values())

        logger.info("Discovery complete. Found %d unique leads.", len(leads))
        return leads

    @staticmethod
    def _parse_rating(rating_text: Optional[str]) -> Optional[float]:
        """Safely parse a rating string like '4.5 stars' to a float."""
        if not rating_text:
            return None
        try:
            # Handle formats like "4.5 stars", "4,5 stars", "Rated 4.5"
            numbers = re.findall(r'[\d]+[.,]?\d*', rating_text)
            if numbers:
                return float(numbers[0].replace(',', '.'))
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
