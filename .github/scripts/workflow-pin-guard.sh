#!/usr/bin/env bash
#
# Reject `uses: org/action@vN` (mutable tag) refs in workflow files.
# Every action MUST be SHA-pinned with a `# vX.Y.Z` comment.
#
# Invoked by:
#   * .pre-commit-config.yaml (workflow-pin-guard hook, commit-time)
#   * .github/workflows/ci.yml::pre-commit job (PR-time, local-CI parity)
#
# Args: one or more paths to workflow YAML files (pre-commit passes the
# staged subset; CI passes all of .github/workflows/*.yml).
#
# Exit codes:
#   0 — all paths SHA-pinned
#   1 — at least one unpinned tag found (paths + line numbers printed)
#
# History: previously inlined as a multi-line bash heredoc in
# .pre-commit-config.yaml ``entry:`` scalar. PyYAML's block-mapping
# parser bailed on the multi-line single-quoted scalar
# (InvalidConfigError); collapsing to one line tripped shlex on
# embedded ``"$@"`` quoting (No closing quotation). Extracting here
# sidesteps both because pre-commit treats a single-path ``entry:``
# as one argv token. Issue #339 has the full diagnosis.

set -euo pipefail

if [ "$#" -eq 0 ]; then
    echo "usage: $0 <workflow.yml> [<workflow.yml> ...]" >&2
    exit 2
fi

UNPINNED_PATTERN='uses:[[:space:]]+[^@#]+@v[0-9]+[[:space:]]*$'

if grep -qE "$UNPINNED_PATTERN" "$@"; then
    echo "Unpinned action tags found (use SHA + # vX.Y.Z comment):"
    grep -nE "$UNPINNED_PATTERN" "$@"
    exit 1
fi

exit 0
