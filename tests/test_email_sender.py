import asyncio
import os
import smtplib
import sys
import unittest
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from unittest.mock import patch, MagicMock

# Add the project root to the python path so imports work correctly
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.integrations.email_sender import SMTPEmailSender


class TestSMTPEmailSender(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # We patch os.environ to ensure consistent initialization
        self.env_patcher = patch.dict(os.environ, {
            "SMTP_HOST": "smtp.test.com",
            "SMTP_PORT": "587",
            "SMTP_USER": "testuser",
            "SMTP_PASS": "testpass",
            "SMTP_FROM": "test@test.com",
            "SMTP_FROM_NAME": "Test Sender",
            "EMAIL_RATE_LIMIT": "5"
        }, clear=True)
        self.env_patcher.start()

        self.sender = SMTPEmailSender()

    def tearDown(self):
        self.env_patcher.stop()

    async def test_missing_credentials(self):
        self.sender.smtp_user = ""
        self.sender.smtp_pass = ""
        result = await self.sender.send("test@example.com", "Subject", "Body")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "SMTP credentials not configured.")

    async def test_invalid_email_format(self):
        result = await self.sender.send("invalid-email", "Subject", "Body")
        self.assertEqual(result["status"], "error")
        self.assertTrue("Invalid email format" in result["error"])

    async def test_bounced_email_list(self):
        self.sender.bounced_emails.add("bounced@example.com")
        result = await self.sender.send("bounced@example.com", "Subject", "Body")
        self.assertEqual(result["status"], "bounced")
        self.assertTrue("is in bounce list" in result["error"])

    @patch('src.integrations.email_sender.SMTPEmailSender._send_smtp')
    async def test_successful_send(self, mock_send_smtp):
        # mock_send_smtp.return_value is not needed as loop.run_in_executor returns what it returns, and it returns None
        result = await self.sender.send("test@example.com", "Subject", "Body")

        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["to"], "test@example.com")
        self.assertIn("sent_at", result)
        mock_send_smtp.assert_called_once()

        # Verify call arguments
        args, kwargs = mock_send_smtp.call_args
        msg = args[0]
        to = args[1]
        self.assertIsInstance(msg, MIMEMultipart)
        self.assertEqual(msg["Subject"], "Subject")
        self.assertEqual(msg["To"], "test@example.com")
        self.assertEqual(msg["From"], "Test Sender <test@test.com>")
        self.assertEqual(to, "test@example.com")

    @patch('src.integrations.email_sender.SMTPEmailSender._send_smtp')
    async def test_custom_from_name(self, mock_send_smtp):
        result = await self.sender.send("test@example.com", "Subject", "Body", from_name="Custom Sender")
        self.assertEqual(result["status"], "sent")

        args, kwargs = mock_send_smtp.call_args
        msg = args[0]
        self.assertEqual(msg["From"], "Custom Sender <test@test.com>")

    @patch('src.integrations.email_sender.SMTPEmailSender._send_smtp')
    async def test_smtp_recipients_refused(self, mock_send_smtp):
        mock_send_smtp.side_effect = smtplib.SMTPRecipientsRefused({"test@example.com": (550, "User unknown")})

        result = await self.sender.send("test@example.com", "Subject", "Body")

        self.assertEqual(result["status"], "bounced")
        self.assertTrue("Recipient refused" in result["error"])
        self.assertIn("test@example.com", self.sender.bounced_emails)

    @patch('src.integrations.email_sender.SMTPEmailSender._send_smtp')
    async def test_generic_exception_during_send(self, mock_send_smtp):
        mock_send_smtp.side_effect = Exception("Unexpected failure")

        result = await self.sender.send("test@example.com", "Subject", "Body")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "Unexpected failure")

    @patch('src.integrations.email_sender.smtplib.SMTP')
    def test_sync_send_smtp(self, mock_smtp_class):
        mock_server = MagicMock()
        mock_smtp_class.return_value.__enter__.return_value = mock_server

        msg = MIMEMultipart()
        msg["Subject"] = "Test"

        self.sender._send_smtp(msg, "test@example.com")

        mock_smtp_class.assert_called_once_with("smtp.test.com", 587)
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("testuser", "testpass")
        mock_server.sendmail.assert_called_once_with("test@test.com", "test@example.com", msg.as_string())

    async def test_wait_for_rate_limit(self):
        # The rate limit is set to 5 in setUp
        now = datetime.now(timezone.utc).timestamp()

        # Add 5 recent sends to hit the rate limit
        self.sender._send_times = [now] * 5

        start_time = asyncio.get_event_loop().time()

        # Patch asyncio.sleep so we don't actually wait
        with patch('asyncio.sleep', new_callable=unittest.mock.AsyncMock) as mock_sleep:
            await self.sender._wait_for_rate_limit()
            mock_sleep.assert_called_once()
            args, _ = mock_sleep.call_args
            self.assertAlmostEqual(args[0], 60.0, places=1)

        self.assertEqual(len(self.sender._send_times), 6)

    async def test_wait_for_rate_limit_cleans_old(self):
        now = datetime.now(timezone.utc).timestamp()

        # Add 5 old sends (older than 60 seconds)
        self.sender._send_times = [now - 65] * 5

        with patch('asyncio.sleep', new_callable=unittest.mock.AsyncMock) as mock_sleep:
            await self.sender._wait_for_rate_limit()
            # Since the old sends should be cleared, rate limit is not hit
            mock_sleep.assert_not_called()

        # Only the new send should be in the list
        self.assertEqual(len(self.sender._send_times), 1)

if __name__ == '__main__':
    unittest.main()
