import unittest
import numpy as np
from src.processors.google_maps import clean_phone

class TestCleanPhone(unittest.TestCase):

    def test_valid_phone_no_plus(self):
        self.assertEqual(clean_phone('1234567'), '1234567')
        self.assertEqual(clean_phone('123 456 7890'), '1234567890')
        self.assertEqual(clean_phone('(555) 123-4567'), '5551234567')
        self.assertEqual(clean_phone('555.123.4567'), '5551234567')

    def test_valid_phone_with_plus(self):
        self.assertEqual(clean_phone('+1234567'), '+1234567')
        self.assertEqual(clean_phone('+1 123 456 7890'), '+11234567890')
        self.assertEqual(clean_phone('+44 (0) 20 1234 5678'), '+4402012345678')

    def test_too_short_phone(self):
        self.assertTrue(np.isnan(clean_phone('123456')))
        self.assertTrue(np.isnan(clean_phone('+12345')))
        self.assertTrue(np.isnan(clean_phone('(12) 34')))

    def test_invalid_input(self):
        self.assertTrue(np.isnan(clean_phone(None)))
        self.assertTrue(np.isnan(clean_phone('')))
        self.assertTrue(np.isnan(clean_phone('   ')))
        self.assertTrue(np.isnan(clean_phone(1234567))) # int instead of str
        self.assertTrue(np.isnan(clean_phone([])))

    def test_phone_with_mixed_characters(self):
        self.assertEqual(clean_phone('Call me: 555-123-4567!'), '5551234567')
        self.assertEqual(clean_phone('Phone: +1 (555) 123-4567 ext 123'), '+15551234567123')

if __name__ == '__main__':
    unittest.main()
