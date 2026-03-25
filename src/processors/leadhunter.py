import aiohttp
import asyncio
import re
from bs4 import BeautifulSoup
from typing import Optional, Tuple, List
import os
import random
import json
from urllib.parse import unquote, quote_plus, urlparse, parse_qs
from google import genai
from src.utils.json_helper import extract_json_from_response
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# --- Crawlbase API Tokens (Configurable via ENV) ---
CRAWLBASE_NORMAL_TOKEN = os.environ.get('CRAWLBASE_NORMAL_TOKEN')
CRAWLBASE_JS_TOKEN = os.environ.get('CRAWLBASE_JS_TOKEN')
if not CRAWLBASE_NORMAL_TOKEN or not CRAWLBASE_JS_TOKEN:
    import warnings
    warnings.warn("CRAWLBASE_NORMAL_TOKEN and CRAWLBASE_JS_TOKEN not set - scraping features will be unavailable.")
    CRAWLBASE_NORMAL_TOKEN = CRAWLBASE_NORMAL_TOKEN or "PLACEHOLDER"
    CRAWLBASE_JS_TOKEN = CRAWLBASE_JS_TOKEN or "PLACEHOLDER"
CRAWLBASE_API_URL_NORMAL = "https://api.crawlbase.com/"
CRAWLBASE_API_URL_JS = "https://api.crawlbase.com/js"

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
]

class LeadHunter:
    def __init__(self):
        self.api_key = os.environ.get("GEMINI_API_KEY")
        self.model_id = 'gemini-flash-latest'
        self._session: Optional[aiohttp.ClientSession] = None
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key)
        else:
            self.client = None
            logger.warning("GEMINI_API_KEY not found in environment.")

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create a shared aiohttp session for connection pooling."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60),
                connector=aiohttp.TCPConnector(limit=20, ttl_dns_cache=300)
            )
        return self._session

    async def close(self):
        """Close the shared session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def trazi_social_linkove_async(self, pojam: str, scraped_phone: Optional[str] = None) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
        """
        Searches for official Facebook, Instagram, LinkedIn, TikTok, and Pinterest links using DuckDuckGo and Crawlbase.
        Handles DuckDuckGo redirects to get the actual social media URL.
        """
        if not pojam and not scraped_phone:
            return None, None, None, None, None

        query_parts = []
        if pojam and len(pojam) > 2:
            query_parts.append(pojam)
        if scraped_phone and len(scraped_phone) > 5:
            query_parts.append(scraped_phone)

        # Broad attempt
        query = ' '.join(query_parts) + ' official facebook instagram linkedin tiktok pinterest page'
        results = await self._ddg_search_async(query)
        fb, insta, li, tt, pin = self._extract_socials(results)

        # Individual narrow fallbacks if needed
        tasks = []
        platforms_needed = []

        if not fb:
            tasks.append(self._ddg_search_async(f"{pojam} facebook official page"))
            platforms_needed.append('fb')
        if not insta:
            tasks.append(self._ddg_search_async(f"{pojam} instagram official"))
            platforms_needed.append('insta')
        if not li:
            tasks.append(self._ddg_search_async(f"{pojam} linkedin company official"))
            platforms_needed.append('li')
        if not tt:
            tasks.append(self._ddg_search_async(f"{pojam} tiktok official"))
            platforms_needed.append('tt')
        if not pin:
            tasks.append(self._ddg_search_async(f"{pojam} pinterest official"))
            platforms_needed.append('pin')

        if tasks:
            results = await asyncio.gather(*tasks)
            for platform, html in zip(platforms_needed, results):
                extracted = self._extract_socials(html)
                if platform == 'fb' and extracted[0]:
                    fb = extracted[0]
                elif platform == 'insta' and extracted[1]:
                    insta = extracted[1]
                elif platform == 'li' and extracted[2]:
                    li = extracted[2]
                elif platform == 'tt' and extracted[3]:
                    tt = extracted[3]
                elif platform == 'pin' and extracted[4]:
                    pin = extracted[4]

        return fb, insta, li, tt, pin

    async def search_for_email_async(self, business_name: str, website: Optional[str] = None) -> Optional[str]:
        """
        Actively hunts for a business email address using DuckDuckGo and Crawlbase.
        """
        if not business_name: return None

        queries = [
            f"{business_name} contact email address",
            f"{business_name} official email",
        ]
        if website:
            domain = website.replace('https://', '').replace('http://', '').replace('www.', '').split('/')[0]
            queries.append(f'site:{domain} "email" OR "contact"')

        for query in queries:
            html = await self._ddg_search_async(query)
            if not html: continue

            email_regex = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
            emails = re.findall(email_regex, html, re.IGNORECASE)

            # Filter out obvious junk
            for email in emails:
                email = email.lower()
                # Expanded junk list based on production data
                junk_list = [
                    'example.com', 'email.com', 'yourname', 'sentry.io', 'wixpress.com',
                    'domain.com', 'test.com', 'info@wix.com', 'noreply', 'support@wix.com',
                    'placeholder', 'my-email', 'abuse@', 'postmaster@', 'security@',
                    'generic@', 'office@domain.com', 'spam@', 'mailer-daemon'
                ]
                if any(x in email for x in junk_list) or len(email) < 5:
                    continue
                return email
        return None

    async def _ddg_search_async(self, query: str) -> Optional[str]:
        """Helper to perform a DuckDuckGo search via Crawlbase."""
        target_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        params = {
            'token': CRAWLBASE_NORMAL_TOKEN,
            'url': target_url,
            'user_agent': random.choice(USER_AGENTS)
        }
        try:
            session = await self._get_session()
            async with session.get(CRAWLBASE_API_URL_NORMAL, params=params) as response:
                if response.status == 429:
                    await asyncio.sleep(random.uniform(5, 10))
                    async with session.get(CRAWLBASE_API_URL_NORMAL, params=params) as retry:
                        return await retry.text() if retry.status == 200 else None
                return await response.text() if response.status == 200 else None
        except Exception:
            logger.debug("DDG search failed for query: %s", query)
            return None

    def clean_duckduckgo_link(self, url: str) -> str:
        """Extracts the actual destination URL from a DuckDuckGo redirect link."""
        if not url: return ""
        if 'duckduckgo.com/l/?' in url or 'duckduckgo.com/y.js?' in url:
            try:
                parsed = urlparse(url)
                qs = parse_qs(parsed.query)
                # 'uddg' is the common parameter for the direct external link
                if 'uddg' in qs:
                    return unquote(qs['uddg'][0])
                # Alternate parameter names found in DDG redirects
                for param in ['r', 'u', 'ad_domain']:
                    if param in qs:
                        return unquote(qs[param][0])
            except Exception:
                pass
        return url

    def _extract_socials(self, html: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
        """Helper to extract social links from DDG HTML."""
        if not html: return None, None, None, None, None
        soup = BeautifulSoup(html, 'html.parser')
        fb, insta, li, tt, pin = None, None, None, None, None
        for link in soup.find_all('a', class_='result__a'):
            href = link.get('href')
            if href:
                href = self.clean_duckduckgo_link(href)
                if not href.startswith('http'): continue

                # Cleaning logic to ensure profile links
                if 'facebook.com' in href and not fb:
                    if not any(x in href for x in ['search', 'directory', 'public', 'groups', '/l.php', 'sharer.php']):
                        fb = href
                if 'instagram.com' in href and not insta:
                    if not any(x in href for x in ['explore', 'accounts/login', 'p/', 'direct/']):
                        insta = href
                if 'linkedin.com' in href and not li:
                    if 'company' in href or '/in/' in href:
                        li = href
                if 'tiktok.com' in href and not tt:
                    if '@' in href and not any(x in href for x in ['/share', '/video/']):
                        tt = href
                if 'pinterest.com' in href and not pin:
                    if not any(x in href for x in ['/pin/', '/search/', '/explore/', '/create/']):
                        pin = href
        return fb, insta, li, tt, pin

    def get_priority_link(self, fb: Optional[str] = None, insta: Optional[str] = None, website: Optional[str] = None) -> str:
        """
        Determines the best single link for manual research.
        Priority: FB > Insta > Website
        """
        if fb: return fb
        if insta: return insta
        return website or ""

    def extract_personal_name(self, text: str) -> Optional[str]:
        """
        Extracts a human first name for personalization.
        Skips common job titles or generic prefixes discovered in testing.
        """
        if not text or text.lower() in ['unknown', 'n/a', 'none', '']:
            return None

        # Clean string from common separators
        parts = re.split(r'[,\s&|/()]+', text.strip())

        titles_to_skip = {
            "dr", "dr.", "prof", "prof.", "ceo", "founder", "owner",
            "md", "director", "manager", "representative", "support",
            "the", "company", "services", "global", "inc", "ltd", "agency",
            "group", "team", "mr", "mrs", "ms", "sir"
        }

        for part in parts:
            if not part: continue
            p_lower = part.lower()
            if p_lower in titles_to_skip:
                continue
            if len(p_lower) < 2:
                continue

            return part.strip().capitalize()

        return None

    async def scrape_business_details_async(self, url: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        """
        Scrapes business name, phone number, email, and page text from a website.
        If phone/email is missing, attempts to find and crawl a sub-page (Contact/About).
        """
        if not url or not url.startswith('http'):
            return None, None, None, None

        params = {
            'token': CRAWLBASE_JS_TOKEN,
            'url': url,
            'user_agent': random.choice(USER_AGENTS),
            'js_render': 'true'
        }

        try:
            session = await self._get_session()
            async with session.get(CRAWLBASE_API_URL_JS, params=params) as response:
                if response.status != 200:
                    return None, None, None, None

                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')

                # 1. Business Name
                business_name = self._extract_business_name(soup)

                # 2. Extract Phone & Email from main page
                text_content = soup.get_text(separator=' ', strip=True)
                phone = self._extract_phone(text_content)
                email = self._extract_email_from_text(text_content)

                # 3. If missing core info, find a sub-page
                if not phone or not email:
                    sub_link = self._find_subpage_link(soup, url)
                    if sub_link:
                        sub_data = await self._scrape_subpage(session, sub_link)
                        if sub_data:
                            if not phone: phone = self._extract_phone(sub_data)
                            if not email:
                                sub_email = self._extract_email_from_text(sub_data)
                                if sub_email: email = sub_email
                            text_content += "\n\n--- SUBPAGE CONTENT ---\n\n" + sub_data

                # 4. Final cleaning for phone
                if phone:
                    phone = re.sub(r'[^\d+]', '', phone)
                    if len(phone) < 7: phone = None

                return business_name, phone, email, text_content
        except Exception as e:
            logger.error("Error in scrape_business_details_async: %s", e, exc_info=True)
            return None, None, None, None

    def _extract_business_name(self, soup: BeautifulSoup) -> Optional[str]:
        business_name = None
        # Try Meta tags
        for prop in ['og:site_name', 'og:title']:
            tag = soup.find('meta', property=prop)
            if tag and tag.get('content'):
                business_name = tag.get('content').strip()
                break
        # Fallback to Title
        if not business_name and soup.title:
            business_name = soup.title.string.strip()
        # Fallback to H1
        if not business_name:
            h1s = [h.get_text().strip() for h in soup.find_all('h1') if h.get_text().strip()]
            if h1s: business_name = max(h1s, key=len)

        if business_name:
            marketplace_pattern = r'\s*[|/\-]+?\s*(Booking\.com|Airbnb|Accommodation|Hotels|Apartments|Guest House|Villa|TripAdvisor|Yelp)\b.*'
            business_name = re.sub(marketplace_pattern, '', business_name, flags=re.IGNORECASE)
            business_name = re.sub(r'\s*[|/\-]+?\s*$', '', business_name).strip()
            if len(business_name) < 3: business_name = None

        return business_name

    def _extract_phone(self, text: str) -> Optional[str]:
        patterns = [
            r'\+?\d{1,4}[\s.\-]?\(?\d{1,4}\)?[\s.\-]?\d{1,4}[\s.\-]?\d{1,4}[\s.\-]?\d{1,9}',
            r'\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}',
            r'\b0\d{10}\b', # UK Format
            r'\b0\d{4}\s\d{6}\b', # UK Format with space
            r'\b\d{9,15}\b' # Generic long number
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match: return match.group()
        return None

    def _extract_email_from_text(self, text: str) -> Optional[str]:
        email_regex = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        match = re.search(email_regex, text)
        if match:
            email = match.group().lower()
            if not any(x in email for x in ['example.com', 'email.com', 'yourname', 'sentry.io', 'wixpress.com']):
                return email
        return None

    def _find_subpage_link(self, soup: BeautifulSoup, base_url: str) -> Optional[str]:
        """Finds a 'Contact' or 'About' link."""
        for a in soup.find_all('a', href=True):
            text = a.get_text().lower()
            href = a['href'].lower()
            if any(x in text for x in ['contact', 'about', 'get in touch', 'reach us']):
                link = a['href']
                if not (link.startswith('http') or link.startswith('//')):
                    base_url = base_url.rstrip('/')
                    if not link.startswith('/'): link = '/' + link
                    link = base_url + link
                return link
        return None

    async def _scrape_subpage(self, session, url: str) -> Optional[str]:
        """Scrapes a sub-page using normal token (faster)."""
        params = {
            'token': CRAWLBASE_NORMAL_TOKEN,
            'url': url,
            'user_agent': random.choice(USER_AGENTS)
        }
        try:
            async with session.get(CRAWLBASE_API_URL_NORMAL, params=params, timeout=30) as response:
                if response.status == 200:
                    html = await response.text()
                    sub_soup = BeautifulSoup(html, 'html.parser')
                    return sub_soup.get_text(separator=' ', strip=True)
        except Exception:
            logger.debug("Subpage scrape failed for %s", url)
            return None

    def calculate_outreach_score(self, lead: dict, socials: Optional[dict] = None) -> int:
        """
        Calculates a lead's outreach value score (0-100).
        """
        score = 0
        s_data = socials or {}

        # 1. Data Completeness (+60 max)
        if lead.get('email') or lead.get('EXTRACTED_EMAIL'): score += 20
        if lead.get('phone'): score += 10

        # Check social presence
        has_social = (
            s_data.get('facebook') or s_data.get('instagram') or s_data.get('linkedin') or
            lead.get('facebook') or lead.get('instagram') or lead.get('linkedin')
        )
        if has_social: score += 15

        # Reputation (New for Phase 17)
        rating = lead.get('rating') or lead.get('Rating')
        reviews = lead.get('reviews') or lead.get('Reviews')
        try:
            if rating:
                rating_val = float(str(rating).replace(',', '.'))
                # Low rating (< 4.0) is a pain point
                if rating_val < 4.0: score += 15
            if reviews:
                num_str = re.sub(r'\D', '', str(reviews))
                reviews_val = int(num_str) if num_str else 0
                # Low review count is a growth opportunity
                if reviews_val < 20: score += 10
        except (ValueError, TypeError):
            pass

        # 2. Enrichment & Intent (+20 max)
        e_data = lead.get('enrichment_data', {})
        if not e_data and any(k in lead for k in ['company_size', 'leadership_team']):
            e_data = lead

        if isinstance(e_data, str):
            try: e_data = json.loads(e_data)
            except Exception: e_data = {}

        if e_data.get('leadership_team') and e_data['leadership_team'] not in ['Unknown', '', None]:
            score += 10
        if e_data.get('company_size') and e_data['company_size'] not in ['Unknown', '', None]:
            score += 10

        # 3. Urgency / Pain Points (+20 max)
        audit = lead.get('audit_results', {})
        if isinstance(audit, str):
            try: audit = json.loads(audit)
            except Exception: audit = {}

        pain_points = lead.get('pain_points', []) or (audit and audit.get('pain_points', []))
        is_high_risk = lead.get('high_risk_flag') or (audit and audit.get('high_risk_flag'))

        if is_high_risk or len(pain_points) > 0:
            score += 20

        return min(score, 100)

    def segment_lead(self, lead: dict, pain_points: Optional[str] = None) -> str:
        """
        Categorizes a lead into an actionable outreach segment.
        """
        score = lead.get('outreach_score')
        if score is None:
            score = 0
        p_str = (pain_points or str(lead.get('pain_points', ''))).lower()

        # 1. High Priority Gaps
        if any(x in p_str for x in ["critical", "missing ssl", "security"]):
            return "Security/Critical Fix"

        if any(x in p_str for x in ["slow", "latency", "load time", "performance"]):
            return "Performance Optimization"

        if any(x in p_str for x in ["mobile", "viewport", "responsive"]):
            return "Mobile Experience"

        # 2. Reputation Segments (New for Phase 17)
        rating = lead.get('rating') or lead.get('Rating')
        reviews = lead.get('reviews') or lead.get('Reviews')
        try:
            if rating:
                rating_val = float(str(rating).replace(',', '.'))
                if rating_val < 3.8: return "Reputation Repair"
            if reviews:
                num_str = re.sub(r'\D', '', str(reviews))
                reviews_val = int(num_str) if num_str else 0
                if reviews_val < 10 and (rating_val if rating else 5.0) >= 4.0:
                    return "New Business / Growth"
        except (ValueError, TypeError):
            pass

        # 3. Marketing Gaps
        if any(x in p_str for x in ["pixel", "analytics", "tracking", "ga4"]):
            return "Marketing Analytics"

        # 4. Niche Enrichment
        e_data = lead.get('enrichment_data', {})
        if not e_data: e_data = lead # Fallback to direct lead dict

        target = str(e_data.get('target_clients', '')).lower()
        if "enterprise" in target or "fortune" in target or "corporate" in target:
            return "Enterprise B2B"
        elif any(x in target for x in ["small", "local", "home", "residential", "shop"]):
            return "Local SMB"

        if score > 75:
            return "High Value / Outreach Ready"
        elif score > 50:
            return "Warm / Needs Personalization"

        return "Low Priority Prospect"


    async def analyze_pain_points_async(self, page_text: str, business_name: Optional[str] = None, audit_results: Optional[dict] = None) -> str:
        """
        Uses Gemini to analyze website text and technical audit results for pain points.
        """
        if not self.client or not page_text:
            return "No page content available for analysis."

        name_str = f" for {business_name}" if business_name else ""

        # Incorporate technical audit data into the prompt
        tech_context = ""
        if audit_results:
            flags = audit_results.get("tech_flags", {})
            red_flags = audit_results.get("red_flags", [])
            cms = audit_results.get("cms")
            tech_context = "\nTechnical Audit Data:\n"
            if cms: tech_context += f"- CMS/Platform: {cms}\n"
            if not flags.get("has_viewport"): tech_context += "- Site is NOT mobile friendly (missing viewport).\n"
            if not flags.get("has_google_analytics") and not flags.get("has_gtm"):
                tech_context += "- No Google Analytics/GTM detected (missing tracking).\n"
            if not flags.get("has_facebook_pixel"):
                tech_context += "- No Facebook Pixel detected (missing social marketing tracking).\n"
            if flags.get("has_portal"): tech_context += "- Site has a client portal/dashboard.\n"
            if not flags.get("has_robots_txt"): tech_context += "- Missing robots.txt (indexing issues likely).\n"
            if not flags.get("has_sitemap"): tech_context += "- Missing sitemap.xml.\n"
            if audit_results.get("response_time", 0) > 3.0:
                tech_context += f"- Slow site performance (latency: {audit_results['response_time']}s).\n"
            if red_flags: tech_context += f"- Technical Red Flags: {', '.join(red_flags)}\n"

        prompt = f"""
        You are writing for a cold outreach campaign. Analyze the following website data{name_str} and identify the most impactful business or marketing pain points.
        {tech_context}
        Look for:
        - Marketing gaps (missing tracking pixels, no analytics, no retargeting).
        - Technical issues (no SSL, slow load times, not mobile-friendly).
        - Platform-specific missed opportunities (e.g. basic Shopify/WordPress setup without optimization).
        - Poor site structure, weak value proposition, or outdated design.
        - Missing social media presence or incomplete digital footprint.

        IMPORTANT OUTPUT RULES:
        - Write exactly 2 sentences in clean, professional English.
        - Write as factual observations, NOT as analysis or recommendations.
        - Use the business name naturally if available.
        - Do NOT use bullet points, labels, or prefixes like "Pain Points Identified:" — just write the sentences directly.
        - Do NOT use markdown formatting, asterisks, or special characters.
        - The text must read naturally as part of a cold email.

        Good example: "Your website currently lacks Google Analytics and Facebook Pixel tracking, which means you have no visibility into where your traffic is coming from or how visitors behave. Additionally, the missing SSL certificate may be causing browsers to flag your site as insecure, potentially driving away customers."

        Bad example: "• Missing GA4 tracking\n• No SSL certificate\n• Pain Points: technical issues detected"

        Website Text Snippet:
        {page_text[:3000]}
        """

        try:
            response = await self.client.aio.models.generate_content(
                model=self.model_id,
                contents=prompt
            )
            text = response.text.strip()
            return text
        except Exception as e:
            logger.error("Pain point analysis error: %s", e, exc_info=True)
            return "Could not analyze pain points."

    async def generate_outreach_hooks_async(self, pain_points: str, business_name: str, audit_results: Optional[dict] = None) -> dict:
        """
        Generates specific hooks for LinkedIn and Email based on pain points and technical data.
        """
        if not self.client or not pain_points:
            return {"linkedin_hook": "", "email_hook": ""}

        cms_info = f"Site is built on {audit_results['cms']}. " if audit_results and audit_results.get("cms") else ""

        prompt = f"""
        You are writing personalized outreach copy for a cold campaign targeting {business_name}.
        {cms_info}

        Their identified pain points:
        "{pain_points}"

        Generate two pieces of outreach copy:

        1. linkedin_hook: A friendly, professional opening line for a LinkedIn connection request.
           - MUST be under 200 characters.
           - Mention the business name naturally.
           - Focus on genuine curiosity or a shared interest, not selling.
           - Example: "Hi! I came across {business_name} and was impressed by your work — I'd love to connect and share some ideas."

        2. email_hook: A compelling opening line for a cold email that references a specific gap or opportunity you found.
           - Write one clear, natural sentence.
           - Be observant and helpful, not salesy or aggressive.
           - Mention a concrete detail from the pain points above.
           - Example: "I noticed {business_name}'s website doesn't have analytics tracking set up, which could mean you're missing key insights about your visitors."

        IMPORTANT OUTPUT RULES:
        - Write in clean, grammatically correct English.
        - Do NOT use markdown, asterisks, bullet points, or any formatting.
        - Do NOT include labels like "linkedin_hook:" or "email_hook:" in the actual text.
        - Each hook must be a complete, natural sentence ready to paste directly into an outreach tool.

        Return ONLY valid JSON:
        {{
            "linkedin_hook": "your linkedin text here",
            "email_hook": "your email text here"
        }}
        """
        try:
            response = await self.client.aio.models.generate_content(
                model=self.model_id,
                contents=prompt
            )
            text = response.text.strip()
            result = extract_json_from_response(text)
            if result:
                return result
            return {"linkedin_hook": "", "email_hook": ""}
        except Exception as e:
            logger.error("Hook generation error: %s", e, exc_info=True)
            return {"linkedin_hook": "", "email_hook": ""}

    async def enrich_business_data_async(self, page_text: str, business_name: Optional[str] = None) -> dict:
        """
        Uses Gemini to extract company size, leadership team, and business details from website text.
        Returns a dictionary with specific keys for DB update.
        """
        if not self.client or not page_text:
            return {}

        name_str = f" for {business_name}" if business_name else ""
        prompt = f"""
        Analyze the following text from a website{name_str} and extract specific business details.

        Fields to find:
        1. Company Size (Estimated number of employees or scale like 'Small', 'Mid-size', 'Large Enterprise').
        2. Leadership Team (Names and roles of key figures like Founders, CEOs, Directors).
        3. Business Details (A one-sentence description of what they do).
        4. Target Clients (Who are their ideal customers? e.g. 'Private homeowners', 'Fortune 500 tech companies').

        Return the results ONLY in the following JSON format:
        {{
            "company_size": "...",
            "leadership_team": "...",
            "business_details": "...",
            "target_clients": "..."
        }}

        If a piece of information is not found, use "Unknown". Keep descriptions professional and concise.

        Website Text Snippet:
        {page_text[:4000]}
        """

        try:
            response = await self.client.aio.models.generate_content(
                model=self.model_id,
                contents=prompt
            )
            text = response.text.strip()
            # Basic JSON extraction in case Gemini adds markdown boilerplate
            result = extract_json_from_response(text)
            if result:
                return result
            return {}
        except Exception as e:
            logger.error("Enrichment error: %s", e, exc_info=True)
            return {}
