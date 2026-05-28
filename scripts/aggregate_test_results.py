#!/usr/bin/env python3
"""Aggregate per-terminal markdown result tables into TEST_RESULTS.md + summary JSON.

Each terminal writes `test-results/NN-<slug>.md` with exactly one markdown table
whose header is `| ID | Category | Target | Test | Status | Detail |`. This
walker globs `test-results/[0-9]*-*.md`, parses every data row, and emits two
artifacts at repo root:

    TEST_RESULTS.md            -- per-terminal totals + every non-PASS row
    test-results/_summary.json -- list of {terminal, pass, fail, skip, blocked}

Status values are case-sensitive: PASS, FAIL, SKIP, BLOCKED. Any other value
is reported as MALFORMED and counted as FAIL so it is impossible to silently
ship broken rows. Missing / empty `Detail` on a non-PASS row is also flagged.

Exit code 0 if the aggregator itself ran cleanly (regardless of whether the
underlying tests passed). CI consumers should read `_summary.json` for
pass/fail signal.
"""
from __future__ import annotations

import json
import re
import sys
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

VALID_STATUS = {"PASS", "FAIL", "SKIP", "BLOCKED"}
HEADER_RX = re.compile(
    r"^\|\s*ID\s*\|\s*Category\s*\|\s*Target\s*\|\s*Test\s*\|\s*Status\s*\|\s*Detail\s*\|\s*$",
    re.IGNORECASE,
)
SEPARATOR_RX = re.compile(r"^\|[\s:\-|]+\|\s*$")
DATA_ROW_RX = re.compile(r"^\|(.+)\|\s*$")
FILE_GLOB = "[0-9]*-*.md"


@dataclass
class Row:
    terminal: str
    file: Path
    line_no: int
    id: str
    category: str
    target: str
    test: str
    status: str
    detail: str


@dataclass
class TerminalSummary:
    terminal: str
    pass_: int = 0
    fail: int = 0
    skip: int = 0
    blocked: int = 0
    rows: list[Row] = field(default_factory=list)

    def add(self, row: Row) -> None:
        self.rows.append(row)
        if row.status == "PASS":
            self.pass_ += 1
        elif row.status == "FAIL":
            self.fail += 1
        elif row.status == "SKIP":
            self.skip += 1
        elif row.status == "BLOCKED":
            self.blocked += 1

    @property
    def total(self) -> int:
        return self.pass_ + self.fail + self.skip + self.blocked

    def to_dict(self) -> dict:
        return {
            "terminal": self.terminal,
            "pass": self.pass_,
            "fail": self.fail,
            "skip": self.skip,
            "blocked": self.blocked,
            "total": self.total,
        }


def parse_row(cells: list[str], path: Path, line_no: int, terminal: str) -> Row | None:
    if len(cells) < 6:
        return None
    id_, cat, target, test_, status, *rest = (c.strip() for c in cells)
    detail = "|".join(rest).strip() if rest else ""
    return Row(
        terminal=terminal,
        file=path,
        line_no=line_no,
        id=id_,
        category=cat,
        target=target,
        test=test_,
        status=status,
        detail=detail,
    )


def parse_file(path: Path) -> tuple[str, list[Row]]:
    terminal = path.stem  # e.g. "01-security"
    rows: list[Row] = []
    in_table = False
    text = path.read_text(encoding="utf-8")
    for idx, line in enumerate(text.splitlines(), start=1):
        if HEADER_RX.match(line):
            in_table = True
            continue
        if in_table and SEPARATOR_RX.match(line):
            continue
        if in_table:
            m = DATA_ROW_RX.match(line)
            if not m:
                if line.strip() == "":
                    in_table = False
                    continue
                in_table = False
                continue
            cells = [c.strip() for c in m.group(1).split("|")]
            row = parse_row(cells, path, idx, terminal)
            if row is None:
                continue
            if not row.id or row.id.lower() == "id":
                continue
            rows.append(row)
    return terminal, rows


def normalize_status(rows: list[Row]) -> list[str]:
    """Mutates rows in place: any status outside VALID_STATUS becomes FAIL with
    a MALFORMED detail prefix so the table is never silently corrupt.
    Returns the list of warnings raised."""
    warnings: list[str] = []
    for row in rows:
        if row.status not in VALID_STATUS:
            warnings.append(
                f"{row.file.name}:{row.line_no} id={row.id!r} status={row.status!r} "
                "is not one of PASS/FAIL/SKIP/BLOCKED — coercing to FAIL"
            )
            prefix = f"[MALFORMED status={row.status!r}] "
            row.detail = (prefix + row.detail).strip()
            row.status = "FAIL"
        if row.status != "PASS" and not row.detail:
            warnings.append(
                f"{row.file.name}:{row.line_no} id={row.id!r} status={row.status} "
                "has empty Detail (required for non-PASS rows)"
            )
            row.detail = "(no detail provided)"
    return warnings


def render_summary_md(
    summaries: list[TerminalSummary],
    failures: list[Row],
    warnings: list[str],
) -> str:
    grand = TerminalSummary(terminal="GRAND TOTAL")
    for s in summaries:
        grand.pass_ += s.pass_
        grand.fail += s.fail
        grand.skip += s.skip
        grand.blocked += s.blocked
    lines: list[str] = []
    lines.append("# Test results — aggregated")
    lines.append("")
    lines.append("Generated by `scripts/aggregate_test_results.py`. Do not hand-edit.")
    lines.append("")
    lines.append("## Per-terminal totals")
    lines.append("")
    lines.append("| Terminal | PASS | FAIL | SKIP | BLOCKED | Total |")
    lines.append("|----------|-----:|-----:|-----:|--------:|------:|")
    for s in summaries:
        lines.append(
            f"| {s.terminal} | {s.pass_} | {s.fail} | {s.skip} | {s.blocked} | {s.total} |"
        )
    lines.append(
        f"| **{grand.terminal}** | **{grand.pass_}** | **{grand.fail}** | "
        f"**{grand.skip}** | **{grand.blocked}** | **{grand.total}** |"
    )
    lines.append("")
    lines.append("## Failures + blocked + skips")
    lines.append("")
    if not failures:
        lines.append("_None. All recorded rows PASS._")
    else:
        lines.append("| Terminal | ID | Status | Category | Target | Test | Detail |")
        lines.append("|----------|----|--------|----------|--------|------|--------|")
        for r in failures:
            esc = lambda s: s.replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {r.terminal} | {r.id} | {r.status} | {esc(r.category)} | "
                f"{esc(r.target)} | {esc(r.test)} | {esc(r.detail)} |"
            )
    lines.append("")
    if warnings:
        lines.append("## Parser warnings")
        lines.append("")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    results_dir = repo_root / "test-results"
    if not results_dir.is_dir():
        print(f"error: {results_dir} does not exist", file=sys.stderr)
        return 2
    files = sorted(results_dir.glob(FILE_GLOB))
    summaries: list[TerminalSummary] = []
    failures: list[Row] = []
    warnings: list[str] = []
    by_terminal: OrderedDict[str, TerminalSummary] = OrderedDict()
    for path in files:
        terminal, rows = parse_file(path)
        warnings.extend(normalize_status(rows))
        s = by_terminal.setdefault(terminal, TerminalSummary(terminal=terminal))
        for row in rows:
            s.add(row)
            if row.status != "PASS":
                failures.append(row)
    summaries = list(by_terminal.values())
    out_md = repo_root / "TEST_RESULTS.md"
    out_json = results_dir / "_summary.json"
    out_md.write_text(render_summary_md(summaries, failures, warnings), encoding="utf-8")
    out_json.write_text(
        json.dumps([s.to_dict() for s in summaries], indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out_md.relative_to(repo_root)} ({len(summaries)} terminals, "
          f"{sum(s.total for s in summaries)} rows)")
    print(f"wrote {out_json.relative_to(repo_root)}")
    if warnings:
        print(f"{len(warnings)} parser warning(s) — see {out_md.name} 'Parser warnings'",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
