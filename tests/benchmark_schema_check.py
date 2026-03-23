import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Add current dir to path to import src
sys.path.append(os.path.abspath(os.curdir))

# Mock the entire supabase and dotenv module before importing SupabaseHelper
mock_supabase = MagicMock()
sys.modules["supabase"] = mock_supabase
sys.modules["supabase.client"] = mock_supabase
sys.modules["dotenv"] = MagicMock()

from src.utils.supabase_helper import SupabaseHelper

class MockResponse:
    def __init__(self, data=None):
        self.data = data or []

class TestCheckSchemaBenchmark(unittest.TestCase):
    def setUp(self):
        # Mock environment variables to allow SupabaseHelper initialization
        with patch.dict(os.environ, {"SUPABASE_URL": "http://mock-url.com", "SUPABASE_ANON_KEY": "mock-key"}):
            self.helper = SupabaseHelper()

        # Mock the client
        self.helper.client = MagicMock()

        # Mock for execute()
        self.execute_mock = MagicMock()
        self.execute_mock.execute.return_value = MockResponse()

        # Mock for limit(1)
        self.limit_mock = MagicMock()
        self.limit_mock.limit.return_value = self.execute_mock

        # Mock for select(col)
        self.select_mock = MagicMock()
        self.select_mock.select.return_value = self.limit_mock

        # Mock for table("leads")
        self.helper.client.table.return_value = self.select_mock

        # Mock for RPC
        self.rpc_mock = MagicMock()
        self.rpc_mock.rpc.return_value = self.execute_mock
        self.helper.client.rpc = self.rpc_mock.rpc

    def test_check_schema_success_call_count(self):
        # Success case: 1 call to execute()
        self.execute_mock.execute.reset_mock()
        self.execute_mock.execute.return_value = MockResponse(data=[{"some": "data"}])

        print("\nRunning optimized check_schema (Success case)...")
        missing = self.helper.check_schema()

        call_count = self.execute_mock.execute.call_count
        print(f"Total execute() calls: {call_count}")
        print(f"Missing columns found: {len(missing)}")

        self.assertEqual(call_count, 1, f"Should have exactly 1 call, got {call_count}")
        self.assertEqual(len(missing), 0)

    def test_check_schema_failure_rpc_success(self):
        # Failure case: 1st call fails, 2nd call (RPC) succeeds
        self.execute_mock.execute.reset_mock()

        def execute_side_effect():
            # First call is the select(...).limit(1).execute()
            if self.execute_mock.execute.call_count == 1:
                raise Exception("column \"missing_col\" does not exist")
            # Second call is the RPC
            return MockResponse(data=[{"column_name": "enrichment_status"}, {"column_name": "high_risk_flag"}])

        self.execute_mock.execute.side_effect = execute_side_effect

        print("\nRunning optimized check_schema (Select fail, RPC success)...")
        missing = self.helper.check_schema()

        call_count = self.execute_mock.execute.call_count
        print(f"Total execute() calls: {call_count}")
        print(f"Missing columns found: {len(missing)}")

        # Should be 2 calls: 1 select and 1 RPC
        self.assertEqual(call_count, 2, f"Should have exactly 2 calls, got {call_count}")
        # There are 24 required columns. We mocked 2 existing columns.
        self.assertEqual(len(missing), 22)

    def test_check_schema_all_fail_fallback(self):
        # All optimized paths fail, fallback to individual checks
        self.execute_mock.execute.reset_mock()

        def execute_side_effect():
            # First call: select bulk
            if self.execute_mock.execute.call_count == 1:
                raise Exception("column \"multiple\" does not exist")
            # Second call: RPC
            if self.execute_mock.execute.call_count == 2:
                raise Exception("RPC failed")
            # Remaining calls: individual selects
            # Mocking that 1st individual select fails
            if self.execute_mock.execute.call_count == 3:
                 raise Exception("column \"enrichment_status\" does not exist")
            return MockResponse()

        self.execute_mock.execute.side_effect = execute_side_effect

        print("\nRunning optimized check_schema (Fallback case)...")
        missing = self.helper.check_schema()

        call_count = self.execute_mock.execute.call_count
        print(f"Total execute() calls: {call_count}")
        print(f"Missing columns found: {len(missing)}")

        # 1 (bulk) + 1 (RPC) + 24 (individual) = 26 calls
        self.assertEqual(call_count, 26, f"Should have 26 calls, got {call_count}")
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0], "enrichment_status")

if __name__ == "__main__":
    unittest.main()
