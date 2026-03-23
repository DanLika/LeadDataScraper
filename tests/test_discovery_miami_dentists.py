import sys
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

class TestDiscoveryMiamiDentists(unittest.IsolatedAsyncioTestCase):

    @classmethod
    def setUpClass(cls):
        # Mock external dependencies before importing anything that might need them
        cls.module_patcher = patch.dict('sys.modules', {
            'playwright': MagicMock(),
            'playwright.async_api': MagicMock(),
            'google.generativeai': MagicMock(),
            'supabase': MagicMock()
        })
        cls.module_patcher.start()

        # Import after mocking
        global run_miami_dentists_discovery
        from src.scripts.discovery_miami_dentists import run_miami_dentists_discovery

    @classmethod
    def tearDownClass(cls):
        cls.module_patcher.stop()

    @patch('src.scripts.discovery_miami_dentists.DiscoveryEngine')
    @patch('builtins.print')
    async def test_run_miami_dentists_discovery_success(self, mock_print, mock_discovery_engine_class):
        # Arrange
        mock_engine_instance = mock_discovery_engine_class.return_value
        # Mock find_leads to return dummy leads
        dummy_leads = [{"name": "Dentist 1"}, {"name": "Dentist 2"}]
        mock_engine_instance.find_leads = AsyncMock(return_value=dummy_leads)
        # Mock enrich_and_save to be an AsyncMock
        mock_engine_instance.enrich_and_save = AsyncMock()

        # Act
        await run_miami_dentists_discovery()

        # Assert
        # Check that find_leads was called with correct arguments
        mock_engine_instance.find_leads.assert_called_once_with("Dentist", "Miami, FL")

        # Check that enrich_and_save was called with the dummy leads
        mock_engine_instance.enrich_and_save.assert_called_once_with(dummy_leads)

        # Check print output for success flow
        mock_print.assert_any_call("🚀 Launching discovery for Dentist in Miami, FL...")
        mock_print.assert_any_call("✨ Found 2 leads. Saving to Supabase...")
        mock_print.assert_any_call("✅ Discovery and persistence complete.")

    @patch('src.scripts.discovery_miami_dentists.DiscoveryEngine')
    @patch('builtins.print')
    async def test_run_miami_dentists_discovery_no_leads(self, mock_print, mock_discovery_engine_class):
        # Arrange
        mock_engine_instance = mock_discovery_engine_class.return_value
        # Mock find_leads to return no leads
        mock_engine_instance.find_leads = AsyncMock(return_value=[])
        mock_engine_instance.enrich_and_save = AsyncMock()

        # Act
        await run_miami_dentists_discovery()

        # Assert
        # Check that find_leads was called with correct arguments
        mock_engine_instance.find_leads.assert_called_once_with("Dentist", "Miami, FL")

        # Check that enrich_and_save was NOT called
        mock_engine_instance.enrich_and_save.assert_not_called()

        # Check print output for failure/empty flow
        mock_print.assert_any_call("🚀 Launching discovery for Dentist in Miami, FL...")
        mock_print.assert_any_call("❌ No leads found.")

if __name__ == '__main__':
    unittest.main()
