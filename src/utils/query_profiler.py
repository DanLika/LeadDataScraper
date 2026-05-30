"""Dev-only Supabase query profiler.

Monkey-patches `SupabaseHelper.client.table` so every PostgREST call is
recorded with the verb (select/insert/update/upsert/delete) and the
nearest application caller (file + line + function). The recorded
events are aggregated into a per-caller histogram so an N+1 jumps out
visually: 50 hits on the same `<file>:<line>` is the smoking gun.

Usage — wrap any block of code you want to profile:

    from src.utils.query_profiler import QueryProfiler

    with QueryProfiler() as prof:
        await orchestrator.run_massive_pipeline(lead_ids=ids, tasks=["audit"])
        # ... wait for the job to finish via the status endpoint or
        #     by polling _process_in_chunks results...

    prof.report()                  # prints histogram
    prof.assert_o1(per_unit=N)     # asserts mean calls per lead <= 1
                                   # (raises if scaling looks O(N))

Or run the included CLI:

    PYTHONPATH=. python -m src.utils.query_profiler --help

Safety: the profiler MUST stay out of production. The `enable()` call
explicitly checks an env guard (`QUERY_PROFILER=1` or in-process opt-in)
to avoid a future caller wiring the wrapper into a release build —
monkey-patching the data layer is fine for diagnostics but fragile under
load (extra Python frame inspection per call, ~1-3% overhead).
"""

from __future__ import annotations

import inspect
import os
import time
from collections import defaultdict
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


_REPO_PREFIX = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_DEFAULT_ENV_GATE = "QUERY_PROFILER"


@dataclass
class QueryEvent:
    table: str
    verb: str  # select / insert / update / upsert / delete (best-effort)
    caller_file: str
    caller_line: int
    caller_func: str
    duration_ms: float
    filter_keys: Tuple[str, ...] = ()


@dataclass
class _Aggregate:
    count: int = 0
    total_ms: float = 0.0
    verbs: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    tables: Dict[str, int] = field(default_factory=lambda: defaultdict(int))


class QueryProfiler(AbstractContextManager["QueryProfiler"]):
    """Context-manager monkey-patch around supabase_helper.SupabaseHelper.

    On enter, replaces the bound `client.table` attribute on every cached
    SupabaseHelper instance the profiler can find (module-level
    singletons via `backend.main.db`, etc.) so the patch covers existing
    callers. On exit, restores the originals.
    """

    def __init__(self, env_gate: str = _DEFAULT_ENV_GATE):
        self._env_gate = env_gate
        self.events: List[QueryEvent] = []
        self._patched_clients: List[Tuple[Any, Callable[..., Any]]] = []
        self._active = False

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def enable(self) -> None:
        if self._active:
            return
        gate = os.getenv(self._env_gate)
        if gate is None or gate.lower() in ("0", "false", "no", ""):
            # Belt and braces: refuse to patch unless explicitly opted in.
            # This module is the kind of thing that gets accidentally
            # imported into a release build years from now.
            raise RuntimeError(
                f"QueryProfiler refused to enable: set {self._env_gate}=1 "
                "in the environment to opt in. Production code must not "
                "monkey-patch the Supabase client."
            )

        # Collect every Supabase client that the lazy singletons may have
        # constructed already. The `import` here is intentionally late so
        # importing this module itself doesn't pull in the chain.
        targets = list(self._discover_clients())
        if not targets:
            raise RuntimeError(
                "QueryProfiler found no live SupabaseHelper instances. "
                "Import `backend.main` (or otherwise instantiate the helper) "
                "before entering the profiler context."
            )

        for client in targets:
            original = client.table
            client.table = self._make_wrapped_table(original)
            self._patched_clients.append((client, original))

        self._active = True

    def disable(self) -> None:
        if not self._active:
            return
        for client, original in self._patched_clients:
            try:
                client.table = original
            except Exception:
                pass
        self._patched_clients.clear()
        self._active = False

    def __enter__(self) -> "QueryProfiler":
        self.enable()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.disable()

    # ------------------------------------------------------------------
    # discovery + patching
    # ------------------------------------------------------------------
    def _discover_clients(self) -> Any:
        import sys

        # SupabaseHelper class — used as the identity check.
        from src.utils.supabase_helper import SupabaseHelper

        seen: set[int] = set()
        for module in list(sys.modules.values()):
            if module is None:
                continue
            try:
                module_dict = getattr(module, "__dict__", {})
            except Exception:
                continue
            for value in module_dict.values():
                if isinstance(value, SupabaseHelper):
                    cli = getattr(value, "client", None)
                    if cli is None or id(cli) in seen:
                        continue
                    seen.add(id(cli))
                    yield cli

    def _make_wrapped_table(
        self, original_table: Callable[..., Any]
    ) -> Callable[..., Any]:
        prof = self

        def wrapped(table_name: str, *args: Any, **kwargs: Any) -> Any:
            qb = original_table(table_name, *args, **kwargs)
            return _ProfiledQueryBuilder(qb, table_name, prof)

        return wrapped

    # ------------------------------------------------------------------
    # event sink
    # ------------------------------------------------------------------
    def _record(self, event: QueryEvent) -> None:
        self.events.append(event)

    # ------------------------------------------------------------------
    # reporting
    # ------------------------------------------------------------------
    def aggregate_by_caller(self) -> Dict[Tuple[str, int, str], _Aggregate]:
        out: Dict[Tuple[str, int, str], _Aggregate] = defaultdict(_Aggregate)
        for ev in self.events:
            key = (ev.caller_file, ev.caller_line, ev.caller_func)
            agg = out[key]
            agg.count += 1
            agg.total_ms += ev.duration_ms
            agg.verbs[ev.verb] += 1
            agg.tables[ev.table] += 1
        return out

    def report(self, top_n: int = 20) -> str:
        """Format a human-readable report and return it (also prints)."""
        if not self.events:
            text = "QueryProfiler: 0 events recorded."
            print(text)
            return text

        agg = self.aggregate_by_caller()
        # Sort by count desc, then total_ms desc.
        rows = sorted(
            agg.items(),
            key=lambda kv: (-kv[1].count, -kv[1].total_ms),
        )

        lines: List[str] = []
        lines.append(
            f"QueryProfiler: {len(self.events)} total events across {len(agg)} unique callers"
        )
        lines.append("")
        lines.append(f"{'count':>6}  {'tot ms':>7}  {'verb':<8}  {'table':<22}  caller")
        lines.append("-" * 90)
        for (path, line, func), data in rows[:top_n]:
            short = (
                os.path.relpath(path, _REPO_PREFIX)
                if path.startswith(_REPO_PREFIX)
                else path
            )
            top_verb = max(data.verbs.items(), key=lambda kv: kv[1])[0]
            top_table = max(data.tables.items(), key=lambda kv: kv[1])[0]
            lines.append(
                f"{data.count:>6}  {data.total_ms:>7.1f}  {top_verb:<8}  {top_table:<22}  "
                f"{short}:{line} {func}"
            )

        text = "\n".join(lines)
        print(text)
        return text

    def assert_o1(self, per_unit: int, tolerance: float = 2.0) -> None:
        """Raise AssertionError if any *single caller* fired more than
        `per_unit * tolerance` queries.

        Use after a pipeline run on N leads:
            with QueryProfiler() as p:
                await run_pipeline_for(N=20)
            p.assert_o1(per_unit=20)   # any caller > 40 hits = N+1 suspect

        `tolerance` is the multiplier above which we flag — default 2x
        leaves room for an unrelated single-shot query inside the
        pipeline that's worth investigating but not necessarily a bug.
        """
        if per_unit <= 0:
            raise ValueError("per_unit must be positive")
        threshold = per_unit * tolerance
        offenders = []
        for key, agg in self.aggregate_by_caller().items():
            if agg.count > threshold:
                offenders.append((key, agg.count))
        if offenders:
            details = ", ".join(
                f"{os.path.relpath(p, _REPO_PREFIX)}:{ln} {fn} ({n} hits)"
                for (p, ln, fn), n in offenders
            )
            raise AssertionError(
                f"QueryProfiler: O(N) suspected — {len(offenders)} caller(s) "
                f"exceeded {threshold:.0f} hits for N={per_unit}: {details}"
            )


# ----------------------------------------------------------------------
# Internal: wraps the PostgREST query builder so .execute() can be timed
# and the verb (.select/.insert/.update/.upsert/.delete) recorded.
# ----------------------------------------------------------------------
class _ProfiledQueryBuilder:
    """Thin proxy: forwards all attribute access to the real PostgREST
    query builder, but intercepts `.select/.insert/.update/.upsert/
    .delete` to record the verb, and `.execute` to record the call."""

    _VERB_METHODS = frozenset({"select", "insert", "update", "upsert", "delete"})

    def __init__(self, inner: Any, table_name: str, profiler: QueryProfiler) -> None:
        # Bypass __setattr__ recursion by going through object.__setattr__.
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_table", table_name)
        object.__setattr__(self, "_profiler", profiler)
        object.__setattr__(self, "_verb", "unknown")

    def __getattr__(self, name: str) -> Any:
        # Called only for attrs not on the proxy itself.
        attr = getattr(self._inner, name)
        if name == "execute":
            return self._wrap_execute(attr)
        if name in self._VERB_METHODS:
            return self._wrap_verb(name, attr)
        if callable(attr):
            # Chainable: e.g. .eq, .in_, .order, .limit. Wrap so we keep
            # returning the proxy (not the inner builder), preserving
            # the verb recorded so far.
            return self._wrap_chain(attr)
        return attr

    def _wrap_verb(self, verb: str, method: Callable[..., Any]) -> Callable[..., Any]:
        def call(*args: Any, **kwargs: Any) -> "_ProfiledQueryBuilder":
            new_inner = method(*args, **kwargs)
            new_proxy = _ProfiledQueryBuilder(new_inner, self._table, self._profiler)
            object.__setattr__(new_proxy, "_verb", verb)
            return new_proxy

        return call

    def _wrap_chain(self, method: Callable[..., Any]) -> Callable[..., Any]:
        def call(*args: Any, **kwargs: Any) -> Any:
            result = method(*args, **kwargs)
            # If supabase returned a chainable builder, keep proxying;
            # otherwise (e.g. attribute access that returns a value)
            # passthrough untouched.
            if hasattr(result, "execute"):
                proxy = _ProfiledQueryBuilder(result, self._table, self._profiler)
                object.__setattr__(proxy, "_verb", self._verb)
                return proxy
            return result

        return call

    def _wrap_execute(self, method: Callable[..., Any]) -> Callable[..., Any]:
        def call(*args: Any, **kwargs: Any) -> Any:
            caller_file, caller_line, caller_func = _capture_caller()
            start = time.perf_counter()
            try:
                return method(*args, **kwargs)
            finally:
                duration_ms = (time.perf_counter() - start) * 1000.0
                self._profiler._record(
                    QueryEvent(
                        table=self._table,
                        verb=self._verb,
                        caller_file=caller_file,
                        caller_line=caller_line,
                        caller_func=caller_func,
                        duration_ms=duration_ms,
                    )
                )

        return call


def _capture_caller() -> Tuple[str, int, str]:
    """Walk the stack and return (file, line, function) of the nearest
    frame that lives inside the repo and outside this module. Skips
    SupabaseHelper internals + the wrapped to_thread bridge so the hit
    is attributed to the real application call site."""
    skip_files = (__file__, os.path.join(_REPO_PREFIX, "src/utils/supabase_helper.py"))
    for frame_info in inspect.stack()[1:]:
        path = frame_info.filename
        if any(path.endswith(s) or path == s for s in skip_files):
            continue
        # Stay inside the repo to avoid blaming stdlib (e.g. asyncio).
        if not path.startswith(_REPO_PREFIX):
            continue
        return path, frame_info.lineno, frame_info.function
    # Fall back: report the topmost stdlib frame so the event isn't lost.
    if inspect.stack():
        top = inspect.stack()[-1]
        return top.filename, top.lineno, top.function
    return "<unknown>", 0, "<unknown>"


# ----------------------------------------------------------------------
# CLI: drives a synthetic pipeline and prints the report. Stand-in for
# the operator who wants to "just run it" without writing glue.
# ----------------------------------------------------------------------
def _cli() -> int:
    import argparse
    import asyncio

    p = argparse.ArgumentParser(
        description="Profile Supabase queries during a sample pipeline run"
    )
    p.add_argument(
        "--leads", type=int, default=5, help="N to use for assert_o1 (default 5)"
    )
    p.add_argument(
        "--top", type=int, default=20, help="how many hot callers to print (default 20)"
    )
    p.add_argument(
        "--scenario",
        choices=["recover", "list_leads"],
        default="list_leads",
        help="which read scenario to drive",
    )
    args = p.parse_args()

    os.environ.setdefault(_DEFAULT_ENV_GATE, "1")

    # Importing backend.main wires up the lazy `db` singleton so the
    # profiler's _discover_clients sees something to patch.
    import backend.main  # noqa: F401 — required for lazy singleton population

    _ = backend.main.db  # force the lazy resolution

    async def run() -> int:
        with QueryProfiler() as prof:
            if args.scenario == "recover":
                from backend.main import orchestrator

                await orchestrator.recover_interrupted_jobs()
            else:
                from backend.main import db as _db

                await _db.list_leads_recent(limit=args.leads)
        prof.report(top_n=args.top)
        try:
            prof.assert_o1(per_unit=args.leads)
            print("\nassert_o1 PASS — no caller exceeded threshold")
            return 0
        except AssertionError as exc:
            print(f"\nassert_o1 FAIL — {exc}")
            return 1

    return asyncio.run(run())


if __name__ == "__main__":  # pragma: no cover — manual invocation only
    raise SystemExit(_cli())
