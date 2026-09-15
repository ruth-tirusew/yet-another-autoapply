"""Tests for Greenhouse OTP email parsing."""

import unittest
from email.message import EmailMessage

from src.apply.otp_mail import (
    extract_otp_from_message,
    parse_greenhouse_otp,
    _pick_otp_code,
)


class ParseGreenhouseOtpTests(unittest.TestCase):
    def test_parses_standard_greenhouse_email(self):
        code = parse_greenhouse_otp(
            "Your Greenhouse verification code",
            "Your verification code is 847291. It expires in 10 minutes.",
            "notifications@greenhouse-mail.io",
        )
        self.assertEqual(code, "847291")

    def test_rejects_non_greenhouse_sender(self):
        code = parse_greenhouse_otp(
            "Your verification code",
            "Your verification code is 123456",
            "noreply@example.com",
        )
        self.assertIsNone(code)

    def test_rejects_greenhouse_email_without_verification_context(self):
        code = parse_greenhouse_otp(
            "Application update",
            "We received your resume.",
            "notifications@greenhouse.io",
        )
        self.assertIsNone(code)

    def test_prefers_code_near_verification_keywords(self):
        code = _pick_otp_code(
            "Reference number 100200. Your verification code is 554433. Footer 998877."
        )
        self.assertEqual(code, "554433")

    def test_extracts_from_html_multipart_message(self):
        msg = EmailMessage()
        msg["From"] = "Greenhouse <noreply@greenhouse.io>"
        msg["Subject"] = "Confirm your email"
        msg.set_content("Fallback")
        msg.add_alternative(
            "<html><body><p>Enter the code <strong>602918</strong> to verify your email.</p></body></html>",
            subtype="html",
        )
        self.assertEqual(extract_otp_from_message(msg), "602918")

    def test_single_six_digit_code(self):
        self.assertEqual(_pick_otp_code("Code: 112233"), "112233")


if __name__ == "__main__":
    unittest.main()
