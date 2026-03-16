import asyncio
import os
import json
import re
from typing import List, Dict, Any
from playwright.async_api import async_playwright
from google import genai
from dotenv import load_dotenv
from src.utils.json_helper import extract_json_from_response
from src.utils.logging_config import get_logger

load_dotenv()

logger = get_logger(__name__)

class EnrichmentEngine:
    """
    Responsible for deep data enrichment of leads by scraping their websites.
    Uses Gemini AI to extract structured business details from raw page content.
    """
    def __init__(self):
        """
        Initializes the EnrichmentEngine with API keys and Gemini model configuration.
        """
        self.api_key = os.getenv("GEMINI_API_KEY")
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key)
        else:
            self.client = None
            logger.warning("GEMINI_API_KEY not found. AI features will be disabled.")

        # Resource pooling: Limit max concurrent browsers to 5 regardless of orchestrator concurrency
        self.browser_semaphore = asyncio.Semaphore(5)

    async def extract_page_content(self, url: str) -> str:
        """
        Navigates to a specific URL and extracts the core text content while stripping noise.
        """
        # Set a strict 60s timeout for the whole enrichment operation
        async with async_playwright() as p:
            browser = None
            try:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                    viewport={'width': 1280, 'height': 800}
                )
                page = await context.new_page()

                try:
                    # Navigation timeout and wait_until refinement
                    await asyncio.wait_for(
                        page.goto(url, wait_until="domcontentloaded", timeout=45000),
                        timeout=50.0
                    )
                    # Get the body text, stripping scripts and styles
                    text = await page.evaluate("() => document.body.innerText")
                    return text[:10000] # Cap text for AI context limits
                except asyncio.TimeoutError:
                    logger.warning("Enrichment Timeout: Operation took > 50s for %s", url)
                    return ""
                except Exception as e:
                    logger.error("Navigation/Content error for %s: %s", url, e)
                    return ""
                finally:
                    await context.close()
            except Exception as e:
                logger.error("Browser enrichment context error: %s", e)
                return ""
            finally:
                if browser:
                    await browser.close()

    async def deep_ai_parse(self, content_blocks: List[str], lead_name: str) -> Dict[str, Any]:
        """
        Uses the Gemini AI model to perform deep structured parsing of multiple content blocks.
        """
        if not self.client:
            return {}

        combined_content = "\n\n--- PAGE BREAK ---\n\n".join(content_blocks)

        prompt = f"""
        Analyze the following company website text and extract business details. All text values MUST be written in clean, professional English — no bullet points, no markdown, no special characters. Each value should read as a natural sentence or phrase.

        Fields to extract:
        1. company_name: The official business name exactly as written on their website.
        2. company_size: Describe the scale naturally (e.g. "Small local business with approximately 10-20 employees" or "Mid-size company with multiple locations").
        3. leadership_team: Full names and titles of founders, CEO, or key executives if mentioned. Write as a natural list (e.g. "John Smith, CEO; Jane Doe, Co-Founder").
        4. key_offerings: Their main products or services in one clear sentence (e.g. "They specialize in residential plumbing, emergency repairs, and bathroom renovations").
        5. contact_details: Email, phone, and address if found. Write naturally (e.g. "info@company.com, (305) 555-1234, 123 Main St, Miami FL").
        6. business_details: A one-sentence summary of what the business does and its mission.
        7. target_clients: Who their ideal customers are, written naturally (e.g. "Homeowners and small businesses in the Miami area looking for affordable plumbing services").
        8. pain_points: Based on their website, identify 2-3 specific business or marketing challenges this company likely faces. Write as complete sentences ready for use in outreach emails (e.g. "The website lacks any form of analytics tracking, making it impossible to measure marketing ROI. Their social media presence appears inactive, with no links to any platforms found on the site.").

        IMPORTANT: Every value must be grammatically correct, written in complete sentences or natural phrases, and ready to be used directly in a professional outreach email without any editing.

        TEXT:
        {combined_content[:8000]}

        Return ONLY a valid JSON object with these 8 keys. Use null for missing information.
        """

        try:
            response = await self.client.aio.models.generate_content(
                model='gemini-flash-latest',
                contents=prompt
            )
            result = extract_json_from_response(response.text)
            return result if result else {}
        except Exception as e:
            logger.error("AI Enrichment Error for %s: %s", lead_name, e, exc_info=True)
            return {}

    async def enrich_lead(self, lead: Dict[str, Any]) -> Dict[str, Any]:
        """
        The main orchestration method for enriching a lead with deep business data.
        Identifies relevant pages, scrapes them, and parses the information using AI.
        """
        urls_to_check = []
        if lead.get("website"):
            urls_to_check.append(lead["website"])

        for key in ["about_url", "team_url", "clients_url"]:
            if lead.get(key) and lead[key] not in urls_to_check:
                urls_to_check.append(lead[key])

        if not urls_to_check:
            return lead

        content_blocks = []

        async with self.browser_semaphore:
            async with async_playwright() as p:
                browser = None
                try:
                    browser = await p.chromium.launch(headless=True)
                    context = await browser.new_context(
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
                        viewport={'width': 1280, 'height': 800}
                    )

                    # Fetch up to 3 pages using the SAME browser context
                    for url in urls_to_check[:3]:
                        if not url or not str(url).startswith('http'): continue
                        page = await context.new_page()
                        try:
                            # Shorter navigation timeout per page to avoid whole job hang
                            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                            text = await page.evaluate("() => document.body.innerText")
                            if text and len(text.strip()) > 100:
                                content_blocks.append(text[:5000])
                        except Exception as e:
                            logger.warning("Error fetching %s: %s", url, e)
                        finally:
                            await page.close()
                except Exception as e:
                    logger.error("Browser failure: %s", e, exc_info=True)
                finally:
                    if browser:
                        await browser.close()

        if content_blocks:
            enrichment_data = await self.deep_ai_parse(content_blocks, lead.get("name", "Unknown"))
            # Clean up enrichment data to avoid "Unknown" strings
            clean_data = {k: v for k, v in enrichment_data.items() if v not in [None, "Unknown", "N/A", "null"]}
            lead.update(clean_data)
            lead["enrichment_status"] = "COMPLETED"
        else:
            lead["enrichment_status"] = "FAILED_NO_CONTENT"

        return lead

async def test_enrichment():
    engine = EnrichmentEngine()
    test_lead = {
        "name": "Example Dental",
        "website": "https://www.google.com" # Just a placeholder
    }
    result = await engine.enrich_lead(test_lead)
    logger.info("Test result: %s", result)

if __name__ == "__main__":
    asyncio.run(test_enrichment())
