# context-mode — full routing rules

Sourced from CLAUDE.md 2026-05-29 slim. CLAUDE.md retains a compressed pointer; full rules here.

These rules are NOT optional — they protect your context window from flooding. A single unrouted command can dump 56 KB into context and waste the entire session.

## BLOCKED commands — do NOT attempt these
- **curl / wget** — intercepted and replaced with error. Use `ctx_fetch_and_index(url, source)` or `ctx_execute(language: "javascript", code: "const r = await fetch(...)")`.
- **Inline HTTP** (`fetch('http`, `requests.get(`, `requests.post(`, `http.get(`, `http.request(`) — intercepted. Use `ctx_execute(language, code)`.
- **WebFetch** — denied entirely. URL extracted; use `ctx_fetch_and_index` then `ctx_search(queries)`.

## REDIRECTED tools — use sandbox equivalents
- **Bash >20 lines output** — Bash is ONLY for `git`, `mkdir`, `rm`, `mv`, `cd`, `ls`, `npm install`, `pip install`, and other short-output commands. For everything else use `ctx_batch_execute(commands, queries)` or `ctx_execute(language: "shell", code: "...")`.
- **Read (for analysis)** — if reading to Edit, Read is correct. If reading to analyze/explore/summarize, use `ctx_execute_file(path, language, code)` — only your printed summary enters context.
- **Grep (large results)** — use `ctx_execute(language: "shell", code: "grep ...")`.

## Tool selection hierarchy
1. **GATHER**: `ctx_batch_execute(commands, queries)` — primary tool. Runs all commands, auto-indexes output, returns search results. ONE call replaces 30+ individual calls.
2. **FOLLOW-UP**: `ctx_search(queries: ["q1", "q2", ...])` — query indexed content. Pass ALL questions as array in ONE call.
3. **PROCESSING**: `ctx_execute(language, code)` | `ctx_execute_file(path, language, code)` — sandbox; only stdout enters context.
4. **WEB**: `ctx_fetch_and_index(url, source)` then `ctx_search(queries)`.
5. **INDEX**: `ctx_index(content, source)` — store in FTS5 knowledge base.

## Subagent routing
Spawning subagents (Agent tool) — routing block auto-injected. Bash-type subagents upgraded to general-purpose. No manual instruction needed.

## Output constraints
- Keep responses under 500 words.
- Write artifacts (code, configs, PRDs) to FILES — never inline. Return file path + 1-line description.
- Indexing: use descriptive source labels so others can `ctx_search(source: "label")` later.

## ctx commands
| Command | Action |
|---------|--------|
| `ctx stats` | Call `ctx_stats`, display output verbatim |
| `ctx doctor` | Call `ctx_doctor`, run returned shell command, display as checklist |
| `ctx upgrade` | Call `ctx_upgrade`, run returned shell command, display as checklist |
