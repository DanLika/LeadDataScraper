from google import genai
import json
import os
import pandas as pd
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

class GeminiMapper:
    def __init__(self):
        self.api_key = os.environ.get("GEMINI_API_KEY")
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key)
        else:
            self.client = None
            logger.warning("GEMINI_API_KEY not found in environment.")

    def get_column_mapping(self, messy_columns: list):
        """
        Sends messy header names to Gemini Flash to get a mapping to standard Supabase columns.
        """
        if not self.client:
            logger.debug("AI client is None, skipping column mapping.")
            return {}

        standard_columns = [
            "name", "company_name", "website", "email", "phone", "address",
            "facebook", "instagram", "linkedin", "tiktok", "pinterest",
            "company_size", "leadership_team", "key_offerings", "business_details",
            "target_clients", "pain_points", "segment",
            "rating", "reviews", "seo_score", "outreach_score",
            "email_hook", "linkedin_hook"
        ]

        prompt = f"""
        You are a data processing expert. Map these CSV column headers to our standard database columns.

        Standard columns: {standard_columns}
        Input columns: {messy_columns}

        Rules:
        1. Only map columns that have a clear semantic match to a standard column.
        2. Ignore irrelevant columns like 'Unnamed: 0', 'row_id', 'id', 'created_at'.
        3. "company", "business", "firm", "organization" should map to "company_name".
        4. "first_name", "contact", "person" should map to "name".
        5. Return ONLY a valid JSON object where keys are input columns and values are standard columns.
        6. Do not include columns that have no match. Do not wrap in markdown.

        Example:
        {{
            "Company": "company_name",
            "web": "website",
            "E-mail": "email",
            "Contact Person": "name"
        }}
        """

        try:
            response = self.client.models.generate_content(
                model='gemini-flash-latest',
                contents=prompt
            )
            raw_text = response.text.strip('`').strip()
            if raw_text.startswith('json'):
                raw_text = raw_text[4:].strip()

            mapping = json.loads(raw_text)
            return mapping
        except Exception as e:
            logger.error("AI Mapping failed: %s", e, exc_info=True)
            return {}
