import unittest
import sys
import os
from unittest.mock import MagicMock

# Mock external dependencies before imports
sys.modules['playwright'] = MagicMock()
sys.modules['playwright.async_api'] = MagicMock()
sys.modules['google.generativeai'] = MagicMock()
sys.modules['supabase'] = MagicMock()

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.agentic_router import AgenticRouter

class TestAgenticRouter(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.router = AgenticRouter()

    async def test_execute_task_unknown_task(self):
        """Verify that execute_task returns an error for unknown tasks."""
        task_name = "UNKNOWN_TASK"
        plan = {"task": task_name, "params": {}}

        result = await self.router.execute_task(plan)

        self.assertEqual(result, {"error": f"Unknown task: {task_name}"})

if __name__ == '__main__':
    unittest.main()
