import unittest
import pandas as pd
import os
from src.core.task_orchestrator import TaskOrchestrator
from src.utils.csv_helper import load_csv_with_unique_key

class TestDataGovernance(unittest.TestCase):
    def setUp(self):
        self.orchestrator = TaskOrchestrator()
        self.test_csv = "/tmp/test_leads.csv"
        # Create a dummy CSV
        data = {
            "name": ["Clinic A", "Clinic B", "Clinic A"],
            "website": ["clinica.com", "clinicb.com", "clinica.com"],
            "email": ["info@clinica.com", "contact@clinicb.com", ""],
            "phone": ["123", "456", "123"]
        }
        pd.DataFrame(data).to_csv(self.test_csv, index=False)

    def tearDown(self):
        if os.path.exists(self.test_csv):
            os.remove(self.test_csv)

    def test_unique_key_generation(self):
        df = load_csv_with_unique_key(self.test_csv, "Test")
        keys = df['unique_key'].tolist()
        
        # Row 1: clinica.com + info@clinica.com -> clinica.com_info@clinica.com
        self.assertEqual(keys[0], "clinica.com_info@clinica.com")
        
        # Row 2: clinicb.com + contact@clinicb.com -> clinicb.com_contact@clinicb.com
        self.assertEqual(keys[1], "clinicb.com_contact@clinicb.com")
        
        # Row 3: clinica.com + "" -> Should use Website if available
        self.assertEqual(keys[2], "clinica.com") 

    def test_ingestion_deduplication(self):
        # This test requires Supabase connection or mock
        # For now, we verify the logic flow
        pass

if __name__ == '__main__':
    unittest.main()
