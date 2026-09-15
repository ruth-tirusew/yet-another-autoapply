"""Tests for Greenhouse apply helpers."""

import unittest

from src.apply.greenhouse import normalize_otp_code


class GreenhouseOtpTests(unittest.TestCase):
    def test_normalize_otp_strips_non_digits(self):
        self.assertEqual(normalize_otp_code("12-34 56"), "123456")

    def test_normalize_otp_empty(self):
        self.assertEqual(normalize_otp_code(""), "")
        self.assertEqual(normalize_otp_code("   "), "")

    def test_normalize_otp_preserves_digits(self):
        self.assertEqual(normalize_otp_code("847291"), "847291")


if __name__ == "__main__":
    unittest.main()
