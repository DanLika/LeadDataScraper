import pytest
from src.scripts.export_leads import check_vulnerability

def test_check_vulnerability_no_audit_results():
    assert check_vulnerability({}) is False

def test_check_vulnerability_empty_audit_results():
    assert check_vulnerability({'audit_results': {}}) is False

def test_check_vulnerability_ssl_valid_false():
    assert check_vulnerability({'audit_results': {'ssl_valid': False}}) is True

def test_check_vulnerability_ssl_valid_true():
    assert check_vulnerability({'audit_results': {'ssl_valid': True}}) is False

def test_check_vulnerability_no_h1_true():
    assert check_vulnerability({'audit_results': {'no_h1': True}}) is True

def test_check_vulnerability_no_h1_false():
    assert check_vulnerability({'audit_results': {'no_h1': False}}) is False

def test_check_vulnerability_both_vulnerabilities():
    assert check_vulnerability({'audit_results': {'ssl_valid': False, 'no_h1': True}}) is True

def test_check_vulnerability_missing_keys():
    assert check_vulnerability({'audit_results': {'other_key': 'value'}}) is False

def test_check_vulnerability_none_audit_results():
    assert check_vulnerability({'audit_results': None}) is False

def test_check_vulnerability_ssl_valid_none():
    assert check_vulnerability({'audit_results': {'ssl_valid': None}}) is False

def test_check_vulnerability_no_h1_none():
    assert check_vulnerability({'audit_results': {'no_h1': None}}) is False
