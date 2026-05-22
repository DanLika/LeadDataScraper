"""Real-behavior tests for the un-covered `csv_helper` functions:
`merge_and_deduplicate`, `load_csv_with_unique_key` key-generation,
`save_csv`, `export_outreach_ready_csv`, `export_facebook_links`.

Every test builds a real DataFrame / writes a real temp file and
asserts the resulting data — no coverage-only assertions.
"""
import csv
import numpy as np
import pandas as pd

from src.utils.csv_helper import (
    merge_and_deduplicate,
    load_csv_with_unique_key,
    save_csv,
    export_outreach_ready_csv,
    export_facebook_links,
)


# ───────────────────────── merge_and_deduplicate ──────────────────────

def test_merge_empty_list_returns_empty_frame():
    out = merge_and_deduplicate([])
    assert isinstance(out, pd.DataFrame) and out.empty


def test_merge_dedupes_on_unique_key_keeping_first():
    a = pd.DataFrame({"unique_key": ["k1", "k2"], "name": ["A", "B"]})
    b = pd.DataFrame({"unique_key": ["k2", "k3"], "name": ["B-dup", "C"]})
    out = merge_and_deduplicate([a, b])
    assert sorted(out["unique_key"]) == ["k1", "k2", "k3"]
    # keep='first' — the original "B" survives, not "B-dup"
    assert out.loc[out["unique_key"] == "k2", "name"].iloc[0] == "B"


def test_merge_generates_fallback_unique_key_when_missing():
    a = pd.DataFrame({"Name": ["Acme"], "Website": ["acme.com"]})
    b = pd.DataFrame({"Name": ["Beta"], "Website": ["beta.com"]})
    out = merge_and_deduplicate([a, b])
    assert "unique_key" in out.columns
    assert out["unique_key"].notna().all()
    assert "acme.com" in out["unique_key"].iloc[0]


def test_merge_bad_input_returns_empty_frame_not_raise():
    # A non-DataFrame in the list makes pd.concat raise; the function
    # must swallow it and return an empty frame, never propagate.
    out = merge_and_deduplicate(["not a dataframe"])
    assert isinstance(out, pd.DataFrame) and out.empty


# ─────────────────── load_csv_with_unique_key (key gen) ───────────────

def test_load_csv_generates_unique_key_from_website_and_email(tmp_path):
    p = tmp_path / "leads.csv"
    p.write_text("Name,Website,email\nAcme,acme.com,a@acme.com\n")
    df = load_csv_with_unique_key(str(p))
    assert df["unique_key"].iloc[0] == "acme.com_a@acme.com"


def test_load_csv_unique_key_falls_back_to_index_when_no_identifiers(tmp_path):
    p = tmp_path / "blank.csv"
    p.write_text("Name,Website,email\n,,\n")
    df = load_csv_with_unique_key(str(p))
    assert df["unique_key"].iloc[0] == "idx_0"


# ────────────────────────────── save_csv ─────────────────────────────

def test_save_csv_writes_file_and_sanitises_formula_cells(tmp_path):
    p = tmp_path / "nested" / "out.csv"
    df = pd.DataFrame({"name": ["=HYPERLINK(1)", "Safe"], "n": [1, 2]})
    save_csv(df, str(p))
    assert p.exists()                                   # nested dir created
    rows = list(csv.reader(p.open()))
    assert rows[1][0] == "'=HYPERLINK(1)"               # formula neutralised
    assert rows[2][0] == "Safe"


# ───────────────────── export_outreach_ready_csv ─────────────────────

def test_export_outreach_maps_columns_and_drops_no_email_rows(tmp_path):
    p = tmp_path / "outreach.csv"
    df = pd.DataFrame({
        "email": ["a@x.com", "", "c@x.com"],
        "website": ["x.com", "y.com", "z.com"],
        "segment": ["SEO", "SEO", "Perf"],
        "first_name": ["Ann", "Bob", "Cy"],
        "pain_points": ["slow", "none", "weak"],
    })
    out = export_outreach_ready_csv(df, str(p))
    # row with empty email dropped → 2 survive
    assert len(out) == 2
    assert set(out["email"]) == {"a@x.com", "c@x.com"}
    # canonical column layout
    assert list(out.columns) == ["email", "website", "category", "first_name", "location", "pain_point"]
    assert p.exists()


def test_export_outreach_handles_list_valued_pain_points(tmp_path):
    p = tmp_path / "o2.csv"
    df = pd.DataFrame({"email": ["a@x.com"], "pain_points": [["slow", "no ssl"]]})
    out = export_outreach_ready_csv(df, str(p))
    assert out["pain_point"].iloc[0] == "slow, no ssl"


# ───────────────────────── export_facebook_links ─────────────────────

def test_export_facebook_links_extracts_unique_valid_links(tmp_path):
    p = tmp_path / "fb.csv"
    df = pd.DataFrame({"facebook": [
        "fb.com/acme", "fb.com/acme", "fb.com/beta", "", "no social found", "None",
    ]})
    out = export_facebook_links(df, str(p))
    links = set(out["Facebook Link"])
    assert links == {"fb.com/acme", "fb.com/beta"}       # deduped, junk dropped
    assert p.exists()


def test_export_facebook_links_missing_column_yields_empty_export(tmp_path):
    p = tmp_path / "fb_empty.csv"
    out = export_facebook_links(pd.DataFrame({"name": ["Acme"]}), str(p))
    assert list(out.columns) == ["Facebook Link"]
    assert len(out) == 0
    assert p.exists()
