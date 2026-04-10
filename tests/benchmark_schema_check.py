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
        # All optimized paths fail, fallback to iterative bulk checks
        self.execute_mock.execute.reset_mock()

        def execute_side_effect():
            # First call: select bulk
            if self.execute_mock.execute.call_count == 1:
                raise Exception("column \"multiple\" does not exist")
            # Second call: RPC
            if self.execute_mock.execute.call_count == 2:
                raise Exception("RPC failed")
            # Remaining calls: iterative bulk checks
            # Call 3: we report "enrichment_status" missing
            if self.execute_mock.execute.call_count == 3:
                raise Exception("column \"enrichment_status\" does not exist")
            # Call 4: successful select of remaining 23 columns
            return MockResponse()

        self.execute_mock.execute.side_effect = execute_side_effect

        print("\nRunning optimized check_schema (Fallback case)...")
        missing = self.helper.check_schema()

        call_count = self.execute_mock.execute.call_count
        print(f"Total execute() calls: {call_count}")
        print(f"Missing columns found: {len(missing)}")

        # 1 (bulk) + 1 (RPC) + 2 (iterative bulk checks: 1 fail, 1 success) = 4 calls
        self.assertEqual(call_count, 4, f"Should have 4 calls, got {call_count}")
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0], "enrichment_status")

    @patch('src.utils.supabase_helper.logger')
    def test_check_schema_outer_exception(self, mock_logger):
        # We want to test the outer except Exception as e block at the end of check_schema.
        # It's reachable if an unhandled exception bubbles up from within the outer try block.
        # We can achieve this by making Optimization 1 raise a normal "does not exist" error,
        # then in Optimization 2, we make the rpc call raise an error, which triggers
        # logger.debug("RPC schema check failed..."). If we make logger.debug raise an exception,
        # it will escape Optimization 2's inner try-except block and get caught by the outer one.

        self.execute_mock.execute.reset_mock()

        # Opt 1: raise standard "column does not exist" error to move to Opt 2
        def execute_side_effect():
            # First call is the select bulk
            if self.execute_mock.execute.call_count == 1:
                raise Exception("column \"some_col\" does not exist")
            return MockResponse()
        self.execute_mock.execute.side_effect = execute_side_effect

        # Opt 2: RPC raises an error
        # Since self.helper.client.rpc("exec_sql", ...).execute() is what runs,
        # and self.helper.client.rpc is mocked to return self.execute_mock in setUp(),
        # we need to make sure the SECOND call to execute_mock.execute raises an error
        # Actually in setUp:
        # self.rpc_mock.rpc.return_value = self.execute_mock
        # self.helper.client.rpc = self.rpc_mock.rpc
        # So the RPC call uses self.execute_mock.execute !

        def execute_side_effect():
            if self.execute_mock.execute.call_count == 1:
                raise Exception("column \"some_col\" does not exist")
            if self.execute_mock.execute.call_count == 2:
                raise Exception("RPC failed")
            return MockResponse()
        self.execute_mock.execute.side_effect = execute_side_effect

        # Opt 2 catch block: logger.debug raises an exception to bubble up to outer block
        mock_logger.debug.side_effect = Exception("Debug logging failed")

        print("\nRunning optimized check_schema (Outer exception case)...")
        missing = self.helper.check_schema()

        # It should return an empty list when the outer exception catches it
        self.assertEqual(missing, [])

        # Verify the outer exception handler logged the correct error
        # NOTE: mock_logger.error might be called by other things if we aren't careful,
        # but in this path it shouldn't be. However, wait! Opt 1 catch block logs
        # an error IF the exception does NOT contain "column does not exist".
        # We ensured Opt 1 raises an exception WITH "column does not exist", so it shouldn't log there.
        # But wait, self.rpc_mock is mocked in setUp().
        # Let's verify the exact call to error.
        mock_logger.error.assert_called_once()
        called_args = mock_logger.error.call_args[0]
        self.assertEqual(called_args[0], "Error checking schema: %s")
        self.assertEqual(str(called_args[1]), "Debug logging failed")

if __name__ == "__main__":
    unittest.main()
