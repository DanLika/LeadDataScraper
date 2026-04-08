import unittest
import sys
import os

# Add current dir to path to import src
sys.path.append(os.path.abspath(os.curdir))

from src.scrapers.discovery_engine import DiscoveryEngine

class TestDiscoveryEngine(unittest.TestCase):
    def test_parse_rating_valid(self):
        cases = {
            "4.5 stars": 4.5,
            "4,5 stars": 4.5,
            "Rated 4.5": 4.5,
            "4.8 out of 5": 4.8,
            "5": 5.0,
            "4,9": 4.9,
            "4.0": 4.0,
        }
        for rating_text, expected in cases.items():
            with self.subTest(rating_text=rating_text):
                self.assertEqual(DiscoveryEngine._parse_rating(rating_text), expected)

    def test_parse_rating_invalid(self):
        cases = [
            None,
            "",
            "No rating",
            "stars",
            "foo bar",
            " . ",
            "   ",
        ]
        for rating_text in cases:
            with self.subTest(rating_text=rating_text):
                self.assertIsNone(DiscoveryEngine._parse_rating(rating_text))

if __name__ == "__main__":
    unittest.main()
