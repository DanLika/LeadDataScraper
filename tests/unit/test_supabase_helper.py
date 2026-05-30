import unittest
from unittest.mock import MagicMock, patch
import os

from src.utils.supabase_helper import SupabaseHelper


class TestSupabaseHelper(unittest.TestCase):
    def setUp(self):
        # Prevent SupabaseHelper from complaining about missing env vars
        self.env_patcher = patch.dict(
            os.environ,
            {
                "SUPABASE_URL": "http://fake.url",
                "SUPABASE_SERVICE_ROLE_KEY": "fake_key",
            },
        )
        self.env_patcher.start()

        self.client_patcher = patch("src.utils.supabase_helper.create_client")
        self.mock_create_client = self.client_patcher.start()

        self.helper = SupabaseHelper()
        self.helper.client = MagicMock()

    def tearDown(self):
        self.env_patcher.stop()
        self.client_patcher.stop()

    def test_auto_migrate_sql_injection(self):
        # auto_migrate now calls the narrow add_lead_column(col text) RPC once
        # per validated column. Invalid column names must be rejected client-side
        # before reaching the RPC.
        missing_columns = [
            "valid_column",
            "invalid_column_123",
            "123invalid",
            "invalid column",
            "invalid;DROP TABLE leads;",
            "in'valid",
        ]

        mock_rpc = MagicMock()
        mock_rpc.execute.return_value = None
        self.helper.client.rpc.return_value = mock_rpc

        result = self.helper.auto_migrate(missing_columns)

        self.assertTrue(result)
        # Exactly the two well-formed columns reach the RPC.
        self.assertEqual(self.helper.client.rpc.call_count, 2)
        called_args = [call.args for call in self.helper.client.rpc.call_args_list]
        self.assertEqual(
            called_args,
            [
                ("add_lead_column", {"col": "valid_column"}),
                ("add_lead_column", {"col": "invalid_column_123"}),
            ],
        )

    def test_auto_migrate_no_valid_columns(self):
        missing_columns = ["123invalid", "invalid column", "invalid;DROP TABLE leads;"]

        mock_rpc = MagicMock()
        self.helper.client.rpc.return_value = mock_rpc

        result = self.helper.auto_migrate(missing_columns)

        self.assertFalse(result)
        self.helper.client.rpc.assert_not_called()

    def test_check_schema_no_client(self):
        """Test check_schema when client is None."""
        self.helper.client = None
        self.assertEqual(self.helper.check_schema(), [])

    def test_check_schema_initial_fetch_error(self):
        """Test check_schema when the initial '*' select throws an exception."""
        self.helper.client.table.return_value.select.return_value.limit.return_value.execute.side_effect = Exception(
            "General DB error"
        )
        self.assertEqual(self.helper.check_schema(), [])

    def test_check_schema_all_exist(self):
        """Test check_schema when all columns exist."""
        # By default, MagicMock won't raise any exception on method calls, so all execute() succeed
        self.assertEqual(self.helper.check_schema(), [])

    def test_check_schema_some_missing(self):
        """Test check_schema when some columns are missing and Supabase throws exceptions."""
        missing_cols_to_simulate = ["seo_score", "facebook"]

        call_count = [0]

        def mock_select(cols):
            chain_mock = MagicMock()
            call_count[0] += 1

            if "," in cols:
                # Bulk select check - find first missing column to simulate error
                missing_col = next((c for c in missing_cols_to_simulate if c in cols.split(",")), None)
                if missing_col:
                    chain_mock.limit.return_value.execute.side_effect = Exception(
                        f'column "{missing_col}" does not exist'
                    )
                else:
                    chain_mock.limit.return_value.execute.return_value = MagicMock(data=[])
                return chain_mock

            # Subsequent calls are individual column checks
            col = cols  # For individual checks, cols is a single column name
            if col in missing_cols_to_simulate:
                chain_mock.limit.return_value.execute.side_effect = Exception(
                    f'column "{col}" does not exist'
                )
            elif col == "tiktok":
                chain_mock.limit.return_value.execute.side_effect = Exception(
                    "Some other random exception"
                )
            else:
                chain_mock.limit.return_value.execute.return_value = MagicMock(data=[])
            return chain_mock

        self.helper.client.table.return_value.select.side_effect = mock_select
        # Also make the RPC fallback fail so we reach individual checks
        self.helper.client.rpc.side_effect = Exception("RPC not available")

        missing = self.helper.check_schema()

        # It should only catch the missing column ones
        self.assertIn("seo_score", missing)
        self.assertIn("facebook", missing)
        self.assertNotIn("tiktok", missing)
        self.assertEqual(len(missing), 2)

    def test_delete_all_jobs_success(self):
        # Helper now uses `.gte("created_at", "1970-01-01")` instead of
        # `.neq("id", "null")` because `.neq()` threw on UUID columns —
        # mirror the real chain so the assertion exercises the right path.
        mock_delete = MagicMock()
        self.helper.client.table.return_value.delete.return_value = mock_delete
        mock_gte = MagicMock()
        mock_delete.gte.return_value = mock_gte
        expected_result = MagicMock()
        mock_gte.execute.return_value = expected_result

        result = self.helper.delete_all_jobs()

        self.assertEqual(result, expected_result)
        self.helper.client.table.assert_called_with("orchestration_jobs")
        mock_delete.gte.assert_called_once_with("created_at", "1970-01-01")

    def test_delete_all_jobs_client_none(self):
        self.helper.client = None
        result = self.helper.delete_all_jobs()
        self.assertIsNone(result)

    def test_get_pending_leads_success(self):
        mock_execute = MagicMock(
            return_value=MagicMock(
                data=[{"unique_key": "123", "audit_status": "Pending"}]
            )
        )
        self.helper.client.table.return_value.select.return_value.eq.return_value.execute = mock_execute

        result = self.helper.get_pending_leads()

        self.assertEqual(
            result.data, [{"unique_key": "123", "audit_status": "Pending"}]
        )
        self.helper.client.table.assert_called_with("leads")
        self.helper.client.table.return_value.select.assert_called_with("*")
        self.helper.client.table.return_value.select.return_value.eq.assert_called_with(
            "audit_status", "Pending"
        )

    def test_get_pending_leads_client_none(self):
        self.helper.client = None
        result = self.helper.get_pending_leads()
        self.assertEqual(result, [])

    def test_update_audit_success(self):
        unique_key = "test_key"
        audit_data = {
            "score": 85,
            "emails": ["test@example.com"],
            "high_risk_flag": True,
        }

        mock_execute = MagicMock(return_value="success")
        self.helper.client.table.return_value.update.return_value.eq.return_value.execute = mock_execute

        result = self.helper.update_audit(unique_key, audit_data)

        self.assertEqual(result, "success")
        self.helper.client.table.assert_called_with("leads")

        update_call = self.helper.client.table.return_value.update.call_args[0][0]
        self.assertEqual(update_call["audit_status"], "Completed")
        self.assertEqual(update_call["email"], "test@example.com")
        self.assertEqual(update_call["seo_score"], 85.0)
        self.assertEqual(update_call["high_risk_flag"], True)

    def test_update_audit_error(self):
        self.helper.client.table.return_value.update.return_value.eq.return_value.execute.side_effect = Exception(
            "DB Error"
        )
        result = self.helper.update_audit("key", {})
        self.assertIsNone(result)

    def test_delete_all_leads_success(self):
        # Same migration as test_delete_all_jobs_success: `.neq(...)` →
        # `.gte("created_at", "1970-01-01")` (column-type-agnostic).
        mock_execute = MagicMock(return_value="deleted")
        self.helper.client.table.return_value.delete.return_value.gte.return_value.execute = mock_execute

        result = self.helper.delete_all_leads()

        self.assertEqual(result, "deleted")
        self.helper.client.table.assert_called_with("leads")
        self.helper.client.table.return_value.delete.return_value.gte.assert_called_with(
            "created_at", "1970-01-01"
        )


class TestSupabaseHelperUpsert(unittest.TestCase):
    def setUp(self):
        self.env_patcher = patch.dict(
            os.environ,
            {
                "SUPABASE_URL": "http://test-url",
                "SUPABASE_SERVICE_ROLE_KEY": "test-key",
            },
        )
        self.env_patcher.start()

        self.client_patcher = patch("src.utils.supabase_helper.create_client")
        self.mock_create = self.client_patcher.start()
        self.mock_create.return_value = MagicMock()

        self.helper = SupabaseHelper()

    def tearDown(self):
        self.env_patcher.stop()
        self.client_patcher.stop()

    def test_upsert_leads_no_client(self):
        """Test upsert_leads when self.client is None."""
        self.helper.client = None
        result = self.helper.upsert_leads([{"unique_key": "123"}])
        self.assertIsNone(result)

    def test_upsert_leads_success(self):
        """Test successful upsert of leads."""
        leads_data = [{"unique_key": "123", "name": "Test Lead"}]
        # Mock the APIResponse-like object so the helper can read result.data
        # — supabase_helper now reports actual landed-count from `result.data`
        # rather than input-count, so the mock has to expose it.
        mock_response = MagicMock(data=[{"unique_key": "123", "name": "Test Lead"}])
        mock_execute = MagicMock(return_value=mock_response)
        mock_upsert = MagicMock(return_value=MagicMock(execute=mock_execute))
        mock_table = MagicMock(return_value=MagicMock(upsert=mock_upsert))
        self.helper.client.table = mock_table

        with patch("src.utils.supabase_helper.logger") as mock_logger:
            result = self.helper.upsert_leads(leads_data)
            self.assertEqual(result, mock_response)
            mock_table.assert_called_once_with("leads")
            mock_upsert.assert_called_once_with(leads_data)
            mock_execute.assert_called_once()
            # Log now reports actual/input — pins the lying-success-count
            # contract change (1/1 on full success, 0/1 on silent rejection).
            mock_logger.info.assert_called_with(
                "Upserted %d/%d leads to Supabase.", 1, 1
            )

    def test_upsert_leads_exception_schema_mismatch(self):
        """Test upsert_leads handling schema mismatch (column does not exist)."""
        leads_data = [{"unique_key": "123", "name": "Test Lead"}]
        mock_execute = MagicMock(
            side_effect=Exception('column "missing_col" does not exist')
        )
        mock_upsert = MagicMock(return_value=MagicMock(execute=mock_execute))
        mock_table = MagicMock(return_value=MagicMock(upsert=mock_upsert))
        self.helper.client.table = mock_table

        with patch("src.utils.supabase_helper.logger") as mock_logger:
            result = self.helper.upsert_leads(leads_data)
            self.assertIsNone(result)
            mock_logger.error.assert_called_once()
            self.assertIn(
                "DATABASE SCHEMA MISMATCH:", mock_logger.error.call_args[0][0]
            )
            mock_logger.warning.assert_called_with(
                "Please run the SQL migration script provided in the implementation plan."
            )


if __name__ == "__main__":
    unittest.main()
