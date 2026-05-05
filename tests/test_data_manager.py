import unittest
from unittest.mock import patch, MagicMock

class TestDataManager(unittest.TestCase):
    @patch('src.core.data_manager.pd.concat')
    def test_merge_and_deduplicate_error(self, mock_concat):
        from src.core.data_manager import merge_and_deduplicate

        mock_concat.side_effect = Exception("Test Error")

        mock_input = [MagicMock()]
        result = merge_and_deduplicate(mock_input)

        # Verify it returns an empty DataFrame
        self.assertTrue(result.empty)
        mock_concat.assert_called_once()

if __name__ == '__main__':
    unittest.main()
