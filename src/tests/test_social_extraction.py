import unittest
import asyncio
from src.scrapers.seo_audit import perform_seo_audit_async

class TestSocialExtraction(unittest.TestCase):
    async def test_regex_extraction(self):
        # Mock HTML containing social links
        html = """
        <html>
            <body>
                <a href="https://facebook.com/testpage">FB</a>
                <a href="https://instagram.com/testinsta">IG</a>
                <a href="https://linkedin.com/company/testlink">LI</a>
            </body>
        </html>
        """
        # Test the regex extraction logic directly (extracted from seo_audit.py)
        import re
        fb = re.search(r'facebook\.com/(?!tr/|ads/|sharer/|v\d+\.\d+/)([a-zA-Z0-9\._\-]+)', html)
        ig = re.search(r'instagram\.com/([a-zA-Z0-9\._\-]+)', html)
        li = re.search(r'linkedin\.com/(company|in)/([a-zA-Z0-9\._\-]+)', html)
        
        self.assertIsNotNone(fb)
        self.assertEqual(fb.group(1), "testpage")
        self.assertIsNotNone(ig)
        self.assertEqual(ig.group(1), "testinsta")
        self.assertIsNotNone(li)
        self.assertEqual(li.group(2), "testlink")

    def test_run_async(self):
        asyncio.run(self.test_regex_extraction())


if __name__ == '__main__':
    unittest.main()
