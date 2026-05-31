"""Phase 16 — reply-classifier benchmark runner.

Loads the synthetic dataset at
``tests/fixtures/reply_classifications/synthetic_dataset.jsonl``,
calls Claude Haiku 4.5 via the Anthropic Messages API on each row,
scores against the ground-truth ``expected`` field, and writes a
report JSON + a markdown summary to ``tests/benchmarks/``.

Requires ``ANTHROPIC_API_KEY`` in the environment. The companion test
``tests/unit/test_reply_classifier_prompt.py`` runs the prompt-shape
checks without any SDK call so CI stays green without the key.

Run::

    ANTHROPIC_API_KEY=sk-... python scripts/run_reply_classifier_bench.py

Tuning:
- ``--model claude-sonnet-4-6-...`` to compare Sonnet vs Haiku.
- ``--limit N`` to bench a subset (smoke during prompt-iteration).
- ``--no-cache`` to disable prompt caching for the system message
  (useful only when measuring cold-prompt latency).
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.services.reply_classifier_prompt import (  # noqa: E402
    CATEGORIES,
    DEFAULT_MODEL,
    build_classification_messages,
)

DATASET_PATH = REPO_ROOT / "tests" / "fixtures" / "reply_classifications" / "synthetic_dataset.jsonl"
REPORT_DIR = REPO_ROOT / "tests" / "benchmarks"

# Bench accuracy / latency targets — pinned in the PR description.
TARGET_ACCURACY_CLEAR = 0.85
TARGET_LATENCY_P95_S = 2.0


def _load_dataset(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _parse_model_response(raw: str) -> dict[str, Any]:
    """Tolerate light formatting drift — models occasionally wrap JSON
    in ```json fences or prepend whitespace. Strip both before parse.
    Hard failure on truly malformed output is the model's bug, surfaced
    in the per-row error column rather than silently treated as 'other'.
    """
    text = raw.strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1 :]
        if text.endswith("```"):
            text = text[:-3].rstrip()
    return json.loads(text)


def _classify_one(
    client: Any,
    *,
    model: str,
    reply: str,
    cache: bool,
) -> tuple[str, float, str, float, dict[str, Any] | None, str | None]:
    """Returns (predicted, confidence, reasoning, latency_s, raw_usage, error)."""
    system_str, user_messages = build_classification_messages(reply)
    # Prompt caching for the (large, stable) system instruction. Even on a
    # 50-row run the cache hits give a measurable cost cut; in prod the
    # webhook handler calls this thousands of times/day with the same
    # system message.
    system_block: Any
    if cache:
        system_block = [
            {
                "type": "text",
                "text": system_str,
                "cache_control": {"type": "ephemeral"},
            }
        ]
    else:
        system_block = system_str

    started = time.perf_counter()
    try:
        msg = client.messages.create(
            model=model,
            system=system_block,
            messages=user_messages,
            max_tokens=300,
            temperature=0.0,
        )
    except Exception as exc:
        return ("", 0.0, "", time.perf_counter() - started, None, str(exc))
    latency = time.perf_counter() - started

    # Anthropic SDK returns a list of content blocks; for our small JSON
    # response the first block is always the text we want.
    raw_text = msg.content[0].text if msg.content else ""
    try:
        parsed = _parse_model_response(raw_text)
    except json.JSONDecodeError as exc:
        usage = getattr(msg, "usage", None)
        return (
            "",
            0.0,
            "",
            latency,
            usage.model_dump() if usage else None,
            f"json_parse_error: {exc}; raw={raw_text[:200]!r}",
        )

    usage = getattr(msg, "usage", None)
    return (
        parsed.get("category", ""),
        float(parsed.get("confidence", 0.0)),
        parsed.get("reasoning", ""),
        latency,
        usage.model_dump() if usage else None,
        None,
    )


def _score(
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute overall accuracy + per-category precision/recall/F1 +
    latency stats + clear-vs-edge breakdown.
    """
    correct = sum(1 for r in results if r["predicted"] == r["expected"])
    total = len(results)
    overall_accuracy = correct / total if total else 0.0

    by_ambiguity: dict[str, dict[str, int]] = {
        "clear": {"correct": 0, "total": 0},
        "edge": {"correct": 0, "total": 0},
    }
    for r in results:
        ambig = r["ambiguity"]
        by_ambiguity[ambig]["total"] += 1
        if r["predicted"] == r["expected"]:
            by_ambiguity[ambig]["correct"] += 1
    ambiguity_breakdown = {
        k: {
            **v,
            "accuracy": v["correct"] / v["total"] if v["total"] else 0.0,
        }
        for k, v in by_ambiguity.items()
    }

    tp: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)
    for r in results:
        if r["predicted"] == r["expected"]:
            tp[r["expected"]] += 1
        else:
            fp[r["predicted"]] += 1
            fn[r["expected"]] += 1

    per_category: dict[str, dict[str, float]] = {}
    for cat in CATEGORIES:
        p_denom = tp[cat] + fp[cat]
        r_denom = tp[cat] + fn[cat]
        precision = tp[cat] / p_denom if p_denom else 0.0
        recall = tp[cat] / r_denom if r_denom else 0.0
        f1_denom = precision + recall
        f1 = (2 * precision * recall / f1_denom) if f1_denom else 0.0
        per_category[cat] = {
            "tp": tp[cat],
            "fp": fp[cat],
            "fn": fn[cat],
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": sum(1 for r in results if r["expected"] == cat),
        }

    latencies = [r["latency_s"] for r in results if r["error"] is None]
    latency_stats: dict[str, float] = {}
    if latencies:
        latencies_sorted = sorted(latencies)
        idx95 = max(0, int(len(latencies_sorted) * 0.95) - 1)
        latency_stats = {
            "min": min(latencies_sorted),
            "median": statistics.median(latencies_sorted),
            "mean": statistics.fmean(latencies_sorted),
            "p95": latencies_sorted[idx95],
            "max": max(latencies_sorted),
        }

    errors = sum(1 for r in results if r["error"] is not None)
    return {
        "overall_accuracy": overall_accuracy,
        "correct": correct,
        "total": total,
        "errors": errors,
        "ambiguity_breakdown": ambiguity_breakdown,
        "per_category": per_category,
        "latency_seconds": latency_stats,
    }


def _format_markdown_summary(
    *,
    model: str,
    dataset_path: Path,
    scores: dict[str, Any],
    timestamp: str,
) -> str:
    out: list[str] = []
    out.append(f"# Reply classifier benchmark — {timestamp}\n")
    out.append(f"- Model: `{model}`")
    out.append(f"- Dataset: `{dataset_path.relative_to(REPO_ROOT)}`")
    out.append(f"- Total: {scores['total']} | Correct: {scores['correct']} | Errors: {scores['errors']}")
    out.append(f"- **Overall accuracy: {scores['overall_accuracy']:.1%}**")
    if scores["latency_seconds"]:
        latst = scores["latency_seconds"]
        out.append(
            f"- Latency (s): min {latst['min']:.2f} | median {latst['median']:.2f} | "
            f"mean {latst['mean']:.2f} | p95 {latst['p95']:.2f} | max {latst['max']:.2f}"
        )
    out.append("\n## Per-ambiguity accuracy\n")
    out.append("| Bucket | Correct | Total | Accuracy |")
    out.append("|--------|---------|-------|----------|")
    for k, v in scores["ambiguity_breakdown"].items():
        out.append(f"| {k} | {v['correct']} | {v['total']} | {v['accuracy']:.1%} |")
    out.append("\n## Per-category F1\n")
    out.append("| Category | Support | TP | FP | FN | Precision | Recall | F1 |")
    out.append("|----------|---------|----|----|----|-----------|--------|-----|")
    for cat, c in scores["per_category"].items():
        out.append(
            f"| {cat} | {c['support']} | {c['tp']} | {c['fp']} | {c['fn']} | "
            f"{c['precision']:.2f} | {c['recall']:.2f} | {c['f1']:.2f} |"
        )
    out.append("\n## Targets\n")
    clear_acc = scores["ambiguity_breakdown"]["clear"]["accuracy"]
    p95 = scores["latency_seconds"].get("p95", 0.0)
    target_acc_met = clear_acc >= TARGET_ACCURACY_CLEAR
    target_lat_met = p95 <= TARGET_LATENCY_P95_S
    out.append(
        f"- Clear-case accuracy ≥ {TARGET_ACCURACY_CLEAR:.0%}: "
        f"{'PASS' if target_acc_met else 'FAIL'} ({clear_acc:.1%})"
    )
    out.append(
        f"- p95 latency ≤ {TARGET_LATENCY_P95_S}s: "
        f"{'PASS' if target_lat_met else 'FAIL'} ({p95:.2f}s)"
    )
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ANTHROPIC_API_KEY not set — bench requires a live API key. "
            "Memory note phase16_classifier_bench_2026-05-30 tracks the "
            "deferred-by-key-rotation state. Exiting 2.",
            file=sys.stderr,
        )
        return 2

    try:
        import anthropic
    except ImportError:
        print(
            "anthropic SDK not installed — pip install anthropic. "
            "Not added to requirements.txt yet because Phase 16 "
            "classifier wiring is gated behind PHASE16_REPLY_CLASSIFIER=0.",
            file=sys.stderr,
        )
        return 2

    rows = _load_dataset(DATASET_PATH)
    if args.limit:
        rows = rows[: args.limit]
    print(f"Loaded {len(rows)} rows from {DATASET_PATH.name}")

    client = anthropic.Anthropic()
    results: list[dict[str, Any]] = []
    for i, row in enumerate(rows, 1):
        predicted, confidence, reasoning, latency, usage, error = _classify_one(
            client,
            model=args.model,
            reply=row["reply"],
            cache=not args.no_cache,
        )
        results.append({
            "id": row["id"],
            "expected": row["expected"],
            "predicted": predicted,
            "confidence": confidence,
            "reasoning": reasoning,
            "language": row["language"],
            "ambiguity": row["ambiguity"],
            "latency_s": latency,
            "usage": usage,
            "error": error,
        })
        marker = "OK" if predicted == row["expected"] else "MISS"
        print(
            f"[{i:2d}/{len(rows)}] {marker} {row['id']} "
            f"expected={row['expected']:20s} got={predicted or '<none>':20s} "
            f"conf={confidence:.2f} {latency*1000:.0f}ms"
        )

    scores = _score(results)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    safe_model = args.model.replace("/", "_").replace(":", "_")
    json_path = REPORT_DIR / f"reply_classifier_bench_{safe_model}_{timestamp}.json"
    md_path = REPORT_DIR / f"reply_classifier_bench_{safe_model}_{timestamp}.md"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(
            {
                "model": args.model,
                "timestamp": timestamp,
                "dataset": str(DATASET_PATH.relative_to(REPO_ROOT)),
                "scores": scores,
                "rows": results,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    md_path.write_text(
        _format_markdown_summary(
            model=args.model,
            dataset_path=DATASET_PATH,
            scores=scores,
            timestamp=timestamp,
        )
    )
    print(f"\nWrote {json_path.relative_to(REPO_ROOT)}")
    print(f"Wrote {md_path.relative_to(REPO_ROOT)}")
    print(f"\nOverall accuracy: {scores['overall_accuracy']:.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
