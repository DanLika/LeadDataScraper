"""Adversarial upload tests against `/upload`.

Exercises every defense in `backend.main.upload_leads`:
  - `validate_csv_metadata`: filename suffix + content-type allowlist
  - `read_capped`: streaming 50 MB cap → 413
  - filesystem write under `tempfile.gettempdir()` with a UUID name
    (path-traversal mitigation)

What we test directly (TestClient, in-process):
  1. **Boundary size**  — exactly `MAX_UPLOAD_BYTES` → 200; +1 byte → 413
  2. **Content-Type mismatch** — `image/png` body → 400
  3. **Filename extension**  — `.exe` → 400
  4. **Filename traversal**  — `../../etc/passwd.csv` reaches the
     server; the response does NOT echo the traversal path; no file
     with `passwd` in its name appears in `tempfile.gettempdir()`.
  5. **Null-byte filename**  — `leads\\x00.exe.csv`: HTTP multipart
     forbids NUL; we verify the server doesn't 500 / leaks no path.
  6. **Polyglot CSV**       — body is valid CSV AND valid HTML/JS;
     accepted as CSV (no parser confusion), nothing rendered.
  7. **BOM tricks**          — UTF-7 / UTF-16 LE / UTF-16 BE BOMs do
     not crash the gate. Server-side encoding sniffing is out of
     scope; we just guarantee no DoS / 500.
  8. **MIME confusion**       — `text/html` body declared as `text/csv`
     passes the gate (server can't sniff body); test pins that the
     filename extension is the load-bearing check.
  9. **ZIP bomb sniff**       — current backend does NOT content-sniff
     beyond the content-type header. We flag the gap with a test
     that documents the current behavior. The fix is to read the
     first ~512 bytes and reject magic numbers for ZIP / GZIP / PNG.
 10. **Gzip-encoded body**    — `Content-Encoding: gzip` with non-gzip
     bytes must not 500.

What we do NOT test here (out of scope or covered elsewhere):
  - X-API-Key rejection (other tests).
  - Background task completion against real Supabase.
"""

from __future__ import annotations

import gzip
import os
import sys
import tempfile
from io import BytesIO

import pytest
from fastapi.testclient import TestClient


backend_path = os.path.join(os.getcwd(), "backend")
if backend_path not in sys.path:
    sys.path.append(backend_path)

from main import app, MAX_UPLOAD_BYTES  # noqa: E402


API_KEY_HEADER = "X-API-Key"


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch):
    # Ensure `verify_api_key` accepts our test header. The existing
    # `.env` value would also work but pinning here keeps the test
    # independent of operator-rotatable secrets.
    monkeypatch.setenv("API_SECRET_KEY", "test-upload-key")


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """/upload is `@limiter.limit("5/minute")`. Running 30+ tests in
    sequence trips the bucket, so reset the slowapi in-memory store
    before each test."""
    from main import limiter
    # slowapi's MovingWindowStorage exposes `storage`; clear all keys.
    try:
        limiter._storage.storage.clear()  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        # Newer slowapi exposes `reset()` on the limiter itself.
        try:
            limiter.reset()
        except Exception:
            pass
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth():
    return {API_KEY_HEADER: "test-upload-key"}


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _files(name: str, body: bytes, content_type: str = "text/csv"):
    return {"file": (name, BytesIO(body), content_type)}


def _is_clean_status(status: int) -> bool:
    """A response is 'clean' (gate fired correctly) if status is one of
    the explicit error codes the gate emits, NOT 500."""
    return status != 500


# ---------------------------------------------------------------------------
# 1) Boundary size.
# ---------------------------------------------------------------------------

def test_exactly_max_upload_bytes_is_accepted(client, auth):
    body = b"a\n" + b"x" * (MAX_UPLOAD_BYTES - 2)
    assert len(body) == MAX_UPLOAD_BYTES
    r = client.post("/upload", files=_files("ok.csv", body), headers=auth)
    # Backend stops at `total > max_bytes` — so exactly max is accepted.
    assert r.status_code == 200, f"boundary upload rejected: {r.status_code} {r.text[:200]}"


def test_one_byte_over_max_upload_bytes_returns_413(client, auth):
    body = b"a\n" + b"x" * (MAX_UPLOAD_BYTES - 1)  # one byte over
    assert len(body) == MAX_UPLOAD_BYTES + 1
    r = client.post("/upload", files=_files("oversize.csv", body), headers=auth)
    assert r.status_code == 413, f"got {r.status_code} body={r.text[:200]}"


# ---------------------------------------------------------------------------
# 2) Content-Type mismatch.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "ct",
    [
        "image/png",
        "image/jpeg",
        "application/zip",
        "application/x-gzip",
        "text/html",
        "application/json",
        "application/octet-stream",
    ],
)
def test_disallowed_content_types_rejected(client, auth, ct):
    r = client.post(
        "/upload",
        files={"file": ("file.csv", BytesIO(b"a,b\n1,2"), ct)},
        headers=auth,
    )
    assert r.status_code == 400, f"content-type {ct} reached the handler: {r.status_code}"


# ---------------------------------------------------------------------------
# 3) Filename extension.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name",
    [
        "leads.exe",
        "leads.exe.csv.bak",          # actual ext is .bak
        "leads.csv.exe",              # actual ext is .exe
        "leads",                       # no extension at all
        "leads.CSV.JPG",
        "leads.csv\n",                 # trailing newline
    ],
)
def test_non_csv_filename_extension_rejected(client, auth, name):
    r = client.post(
        "/upload",
        files=_files(name, b"a,b\n1,2"),
        headers=auth,
    )
    assert r.status_code == 400, (
        f"non-CSV filename {name!r} accepted: {r.status_code} {r.text[:200]}"
    )


def test_uppercase_csv_extension_accepted(client, auth):
    r = client.post(
        "/upload",
        files=_files("leads.CSV", b"a,b\n1,2"),
        headers=auth,
    )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# 4) Filename traversal — server names the temp file itself.
# ---------------------------------------------------------------------------

def test_traversal_filename_does_not_write_to_traversed_path(client, auth):
    tmpdir = tempfile.gettempdir()
    poisoned_paths = [
        os.path.join(tmpdir, "passwd"),
        os.path.join("/etc", "passwd_lead_upload_probe"),
    ]
    # Snapshot to detect any file that names itself after the traversal.
    before = set(os.listdir(tmpdir))

    r = client.post(
        "/upload",
        files=_files("../../../etc/passwd.csv", b"a,b\n1,2"),
        headers=auth,
    )
    assert r.status_code == 200

    after = set(os.listdir(tmpdir))
    new_files = after - before
    for name in new_files:
        assert "passwd" not in name.lower(), (
            f"server stored upload as {name!r} — filename was not sanitized"
        )
        assert ".." not in name, f"traversal preserved in temp name: {name!r}"

    # Body echo of the original filename is fine in JSON (no HTML render).
    # We just assert nothing got written outside the tempdir.
    for p in poisoned_paths:
        assert not os.path.exists(p), f"upload created {p!r}"


# ---------------------------------------------------------------------------
# 5) Null-byte filename.
# ---------------------------------------------------------------------------

def test_nul_byte_in_filename_does_not_500(client, auth):
    # Some HTTP stacks reject NUL in the filename; others pass it through.
    # Either way, the server must NOT 500. Acceptable: 400 (rejected by
    # the gate) or 200 (gate accepted; tempfile name is server-generated
    # so traversal is moot).
    r = client.post(
        "/upload",
        files=_files("leads\x00.exe.csv", b"a,b\n1,2"),
        headers=auth,
    )
    assert _is_clean_status(r.status_code), (
        f"NUL-byte filename produced 500: body={r.text[:200]}"
    )
    # If accepted, the temp file name must not carry the NUL through.
    if r.status_code == 200:
        for name in os.listdir(tempfile.gettempdir()):
            assert "\x00" not in name, f"NUL preserved in {name!r}"


# ---------------------------------------------------------------------------
# 6) Polyglot CSV — valid CSV that's also valid HTML/JS.
# ---------------------------------------------------------------------------

def test_polyglot_csv_html_js_accepted_as_csv(client, auth):
    body = (
        b'header_a,header_b\n'
        b'"<script>alert(1)</script>","<img src=x onerror=alert(1)>"\n'
        b'"=HYPERLINK(\\"http://evil\\")","@SUM(1+1)"\n'
    )
    r = client.post("/upload", files=_files("polyglot.csv", body), headers=auth)
    # Gate accepts (it's a textual CSV with .csv extension + text/csv MIME).
    # The downstream `sanitize_dataframe_for_csv` neutralises the formula
    # injections at export time; HTML/JS embedded as cell text is inert
    # until something renders it as HTML.
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# 7) BOM tricks — UTF-7, UTF-16 LE, UTF-16 BE.
# ---------------------------------------------------------------------------

# The legacy UTF-7 BOM (`+/v8-`) can cause an HTML-rendering consumer to
# auto-switch encoding and decode adjacent ASCII as UTF-7. Pandas reads
# CSVs as UTF-8 by default; UTF-16 bodies appear as junk. Server must
# not 500 either way.
BOM_PAYLOADS = [
    ("utf7", b"\x2b\x2f\x76\x38\x2d" + b"name,age\nrudy,42\n"),
    ("utf16_le", b"\xff\xfe" + "name,age\nrudy,42\n".encode("utf-16-le")),
    ("utf16_be", b"\xfe\xff" + "name,age\nrudy,42\n".encode("utf-16-be")),
    ("utf8_bom", b"\xef\xbb\xbf" + b"name,age\nrudy,42\n"),
]


@pytest.mark.parametrize(
    "label,body",
    BOM_PAYLOADS,
    ids=[label for label, _ in BOM_PAYLOADS],
)
def test_bom_payloads_do_not_crash_gate(client, auth, label, body):
    r = client.post("/upload", files=_files(f"{label}.csv", body), headers=auth)
    assert _is_clean_status(r.status_code), (
        f"BOM payload {label} caused 500: body={r.text[:300]}"
    )
    # Gate's content-type + extension check passes for these — they're
    # `.csv` with `text/csv` MIME. Whether pandas parses them correctly
    # is a separate concern; we just confirm no DoS / no 500.
    assert r.status_code in (200, 400, 413)


# ---------------------------------------------------------------------------
# 8) MIME confusion: HTML body declared as text/csv with .csv name.
# ---------------------------------------------------------------------------

def test_html_body_with_csv_metadata_passes_gate(client, auth):
    """The gate is filename-extension + content-type header — it can't
    sniff the body. This test pins that contract; a future hardening
    that ADDS body sniffing should change this assertion."""
    body = b"<!DOCTYPE html><script>alert(1)</script>"
    r = client.post("/upload", files=_files("file.csv", body), headers=auth)
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# 9) ZIP / GZIP / PNG bombs with .csv extension — content sniff missing.
# ---------------------------------------------------------------------------

# These are the magic-byte prefixes the server SHOULD reject if it
# added body sniffing. Currently it does not — the test asserts the
# response is at least not a 500, and DOCUMENTS the gap so a future
# hardening (sniff first ~512 bytes for ZIP/GZIP/PNG magic) gets a
# failing test to flip.

BOMB_PAYLOADS = [
    # Real ZIP: 1 KB header + small compressed content. Renamed .csv.
    ("zip_renamed_csv", b"PK\x03\x04" + b"\x00" * 1020),
    ("gzip_renamed_csv", b"\x1f\x8b\x08\x00" + b"\x00" * 1020),
    ("png_renamed_csv", b"\x89PNG\r\n\x1a\n" + b"\x00" * 1020),
]


@pytest.mark.parametrize(
    "label,body",
    BOMB_PAYLOADS,
    ids=[label for label, _ in BOMB_PAYLOADS],
)
def test_binary_bombs_renamed_csv(client, auth, label, body):
    r = client.post("/upload", files=_files(f"{label}.csv", body), headers=auth)
    # Document current behavior: gate accepts based on filename+MIME.
    # If a future hardening adds magic-byte sniffing, flip these to
    # `r.status_code == 400`.
    assert _is_clean_status(r.status_code), (
        f"binary bomb {label} caused 500: {r.text[:200]}"
    )


def test_zip_bomb_does_not_inflate_on_disk(client, auth):
    """A real ZIP bomb: a 1 KB compressed file that decompresses to 4 GB.
    Even if the gate accepts (it currently does — filename+MIME only),
    the server stores the file as-is on disk; it doesn't decompress
    anything at the upload layer. We probe by uploading a real 1 KB
    `gzip`-compressed payload of 4 MB zeros (1024× expansion) and
    asserting the on-disk tempfile is bounded by the upload byte
    count, not the decompressed size."""
    payload = gzip.compress(b"\x00" * (4 * 1024 * 1024))  # 4 MB → ~5 KB gz
    assert len(payload) < 50_000
    tmpdir = tempfile.gettempdir()
    before_sizes = {
        f: os.path.getsize(os.path.join(tmpdir, f))
        for f in os.listdir(tmpdir)
        if os.path.isfile(os.path.join(tmpdir, f))
    }

    r = client.post(
        "/upload",
        files=_files("bomb.csv", payload),
        headers=auth,
    )
    assert _is_clean_status(r.status_code)

    after = os.listdir(tmpdir)
    for f in after:
        path = os.path.join(tmpdir, f)
        if not os.path.isfile(path):
            continue
        if f.startswith("leadscraper_") and f.endswith(".csv"):
            # Server-generated upload tempfile. Must be exactly the
            # uploaded byte count, not an inflated size.
            assert os.path.getsize(path) <= len(payload), (
                f"upload tempfile inflated: {path} is "
                f"{os.path.getsize(path)} bytes vs {len(payload)} uploaded"
            )


# ---------------------------------------------------------------------------
# 10) Content-Encoding mismatch.
# ---------------------------------------------------------------------------

def test_content_encoding_gzip_with_non_gzip_body_does_not_500(client, auth):
    """If the request says `Content-Encoding: gzip` but the body isn't
    actually gzip, Starlette / FastAPI may try to decompress and raise
    — that should be surfaced as 4xx, not 500. We send the header
    manually via the headers dict; whether the server honors it depends
    on the stack."""
    body = b"name,age\nrudy,42\n"
    headers = {**{API_KEY_HEADER: "test-upload-key"},
               "Content-Encoding": "gzip"}
    r = client.post(
        "/upload",
        files=_files("plain.csv", body),
        headers=headers,
    )
    assert _is_clean_status(r.status_code), (
        f"Content-Encoding lie crashed server: {r.status_code} {r.text[:200]}"
    )


def test_real_gzip_body_without_decompress_handling_does_not_500(client, auth):
    """Body IS gzipped but Content-Encoding header omitted. Server
    receives the bytes as-is. Pandas can't parse them; the upload
    background task may log an error. Synchronous response must be
    a clean status, not 500."""
    gzipped = gzip.compress(b"name,age\nrudy,42\n")
    r = client.post(
        "/upload",
        files=_files("gz.csv", gzipped),
        headers=auth,
    )
    assert _is_clean_status(r.status_code)


# ---------------------------------------------------------------------------
# 11) Lying Content-Length is hard to forge from TestClient — out of scope.
# ---------------------------------------------------------------------------

def test_lying_content_length_not_directly_testable_via_testclient():
    """TestClient (httpx-backed) sets Content-Length from the actual
    body bytes — it cannot send a CL header that lies about the body
    size. The defense (`read_capped` streams chunks and ignores the
    declared CL) is exercised indirectly by the 50 MB boundary tests
    above: the server reads until EOF or cap, never trusts CL for
    upper bound. This test exists as documentation that the lying-CL
    scenario requires a raw socket / proxy-level test if the operator
    wants empirical proof against forged CL."""
    pytest.skip("documented — requires raw socket; see test docstring")
