"""Regression test for Phase 9.10 (PR #274) Finding B.

`_check_meta_tags` crashed with ``AttributeError: 'NoneType' object has no
attribute 'strip'`` on Sotheby's homepage. Root cause: ``soup.title`` was
truthy (empty ``<title>`` element) but ``soup.title.string`` was ``None``,
and the original ``soup.title.string.strip()`` had no guard.

These tests pin the guard in `src/scrapers/seo_audit.py::_check_meta_tags`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bs4 import BeautifulSoup

from src.scrapers.seo_audit import _check_meta_tags


def _make_results() -> dict:
    return {"red_flags": []}


class TestNullTitleString:
    def test_empty_title_tag_does_not_crash(self):
        # The original bug: <title></title> → soup.title is truthy but
        # soup.title.string is None.
        soup = BeautifulSoup("<html><head><title></title></head><body></body></html>", "html.parser")
        results = _make_results()
        _check_meta_tags(soup, results)  # must not raise
        assert results["title"] is None
        assert "Missing Title Tag" in results["red_flags"]
        assert results["title_length"] == 0

    def test_title_with_multiple_children_does_not_crash(self):
        # When <title> has multiple children, BeautifulSoup's `.string`
        # returns None — another path into the original AttributeError.
        soup = BeautifulSoup(
            "<html><head><title><span>A</span><span>B</span></title></head><body></body></html>",
            "html.parser",
        )
        results = _make_results()
        _check_meta_tags(soup, results)
        # The exact value depends on parser; we only need non-crashing
        # behavior. Either None or a normal string is acceptable as long
        # as no exception was raised. The original bug was the crash.
        assert "title" in results

    def test_no_title_tag_at_all_does_not_crash(self):
        # Belt-and-braces: pre-existing happy path stays intact.
        soup = BeautifulSoup("<html><head></head><body></body></html>", "html.parser")
        results = _make_results()
        _check_meta_tags(soup, results)
        assert results["title"] is None
        assert "Missing Title Tag" in results["red_flags"]

    def test_normal_title_still_extracted(self):
        soup = BeautifulSoup(
            "<html><head><title>  Some Real Title For The Page  </title></head><body></body></html>",
            "html.parser",
        )
        results = _make_results()
        _check_meta_tags(soup, results)
        assert results["title"] == "Some Real Title For The Page"
        assert results["title_length"] == len("Some Real Title For The Page")


class TestNullMetaDescription:
    def test_meta_description_with_empty_content_attr_does_not_crash(self):
        # Same defect pattern on the description path: attribute key present
        # but value is None/empty.
        soup = BeautifulSoup(
            '<html><head><title>x</title><meta name="description" content=""></head></html>',
            "html.parser",
        )
        results = _make_results()
        _check_meta_tags(soup, results)
        assert results["meta_description"] is None
        assert "Missing Meta Description" in results["red_flags"]
        assert results["meta_length"] == 0

    def test_meta_description_missing_entirely(self):
        soup = BeautifulSoup(
            '<html><head><title>x</title></head></html>',
            "html.parser",
        )
        results = _make_results()
        _check_meta_tags(soup, results)
        assert results["meta_description"] is None
        assert "Missing Meta Description" in results["red_flags"]

    def test_meta_description_normal_content_extracted(self):
        soup = BeautifulSoup(
            '<html><head><title>x</title><meta name="description" content="  A long enough description for the site, exceeding seventy characters to stay clean.  "></head></html>',
            "html.parser",
        )
        results = _make_results()
        _check_meta_tags(soup, results)
        assert results["meta_description"].startswith("A long enough description")
        assert results["meta_length"] > 70
