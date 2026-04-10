import pytest
from unittest.mock import patch
import os
import sys

# Add root dir to path to import src
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.processors.leadhunter import LeadHunter

@pytest.fixture
def lead_hunter():
    with patch.dict(os.environ, {}, clear=True):
        return LeadHunter()

def test_calculate_outreach_score_empty(lead_hunter):
    assert lead_hunter.calculate_outreach_score({}) == 0

def test_calculate_outreach_score_data_completeness(lead_hunter):
    # Just email
    assert lead_hunter.calculate_outreach_score({'email': 'test@example.com'}) == 20
    # EXTRACTED_EMAIL
    assert lead_hunter.calculate_outreach_score({'EXTRACTED_EMAIL': 'test@example.com'}) == 20
    # phone
    assert lead_hunter.calculate_outreach_score({'phone': '1234567'}) == 10

def test_calculate_outreach_score_socials(lead_hunter):
    # Social in lead
    assert lead_hunter.calculate_outreach_score({'facebook': 'fb'}) == 15
    # Social in socials dict
    assert lead_hunter.calculate_outreach_score({}, {'linkedin': 'in'}) == 15

def test_calculate_outreach_score_reputation(lead_hunter):
    # Rating < 4.0 (+15)
    assert lead_hunter.calculate_outreach_score({'rating': 3.5}) == 15
    assert lead_hunter.calculate_outreach_score({'Rating': '3,5'}) == 15 # tests comma replace
    assert lead_hunter.calculate_outreach_score({'rating': 4.5}) == 0

    # Reviews < 20 (+10)
    assert lead_hunter.calculate_outreach_score({'reviews': 15}) == 10
    assert lead_hunter.calculate_outreach_score({'Reviews': '15 reviews'}) == 10
    assert lead_hunter.calculate_outreach_score({'reviews': 25}) == 0

    # Invalid rating/reviews shouldn't crash
    assert lead_hunter.calculate_outreach_score({'rating': 'bad', 'reviews': 'none'}) == 0

def test_calculate_outreach_score_enrichment(lead_hunter):
    # leadership_team (+10)
    assert lead_hunter.calculate_outreach_score({'enrichment_data': {'leadership_team': 'Yes'}}) == 10
    assert lead_hunter.calculate_outreach_score({'enrichment_data': {'leadership_team': 'Unknown'}}) == 0

    # company_size (+10)
    assert lead_hunter.calculate_outreach_score({'enrichment_data': {'company_size': '10-50'}}) == 10
    assert lead_hunter.calculate_outreach_score({'enrichment_data': {'company_size': ''}}) == 0

    # Enrichment string JSON
    assert lead_hunter.calculate_outreach_score({'enrichment_data': '{"leadership_team": "Yes"}'}) == 10
    assert lead_hunter.calculate_outreach_score({'enrichment_data': 'invalid json'}) == 0

    # Flat lead fallback
    assert lead_hunter.calculate_outreach_score({'company_size': '10-50'}) == 10

def test_calculate_outreach_score_urgency(lead_hunter):
    # pain_points list (+20)
    assert lead_hunter.calculate_outreach_score({'pain_points': ['Missing SEO']}) == 20
    assert lead_hunter.calculate_outreach_score({'pain_points': []}) == 0

    # high_risk_flag (+20)
    assert lead_hunter.calculate_outreach_score({'high_risk_flag': True}) == 20

    # audit_results dict
    assert lead_hunter.calculate_outreach_score({'audit_results': {'pain_points': ['Slow']}}) == 20
    assert lead_hunter.calculate_outreach_score({'audit_results': {'high_risk_flag': True}}) == 20

    # audit_results string JSON
    assert lead_hunter.calculate_outreach_score({'audit_results': '{"high_risk_flag": true}'}) == 20

def test_calculate_outreach_score_max_cap(lead_hunter):
    # Max score is 100
    lead = {
        'email': 'test@example.com', # +20
        'phone': '123', # +10
        'facebook': 'fb', # +15
        'rating': 3.0, # +15
        'reviews': 10, # +10
        'company_size': '10-50', # +10
        'leadership_team': 'Yes', # +10
        'high_risk_flag': True # +20
    }
    # Total would be 110
    assert lead_hunter.calculate_outreach_score(lead) == 100
