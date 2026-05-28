# Developer-facing targets. CI runs the same commands directly — this
# file exists so the local-dev → CI parity is one `make` away.

.PHONY: install-hooks uninstall-hooks pre-commit-all fmt fmt-frontend workflow-hashes lock-python verify-prod-constraints verify-prod-constraints-canary help

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

install-hooks:  ## Install pre-commit hooks (one-time per clone)
	@command -v pre-commit >/dev/null 2>&1 || { \
		echo "pre-commit not found. Installing via pip..."; \
		python3 -m pip install --user pre-commit; \
	}
	pre-commit install
	@echo "Pre-commit hooks installed. They will run on every \`git commit\`."
	@echo "To run on all files now: make pre-commit-all"

uninstall-hooks:  ## Remove pre-commit hooks (escape hatch)
	pre-commit uninstall

pre-commit-all:  ## Run pre-commit on every file (matches CI)
	pre-commit run --all-files

fmt:  ## Auto-format Python (ruff) and frontend (prettier --write)
	ruff format src/ tests/ backend/
	ruff check --fix src/ tests/ backend/
	$(MAKE) fmt-frontend

fmt-frontend:  ## Auto-format frontend with prettier (writes changes)
	cd frontend && npx prettier --write "**/*.{ts,tsx,js,jsx,mjs,cjs,json,css,md}"

lock-python:  ## Regenerate requirements.txt from requirements.in with sha256 hashes
	@command -v pip-compile >/dev/null 2>&1 || python3 -m pip install --user pip-tools
	pip-compile --generate-hashes --strip-extras --output-file requirements.txt requirements.in
	@echo "Regenerated requirements.txt with hashes. Commit alongside requirements.in changes."

verify-prod-constraints:  ## Verify prod CHECK constraints (run AFTER applying any migration with regex/IN-list literals)
	@if [ -z "$$SUPABASE_ACCESS_TOKEN" ]; then \
		echo "ERROR: SUPABASE_ACCESS_TOKEN not set."; \
		echo "Generate a PAT at https://supabase.com/dashboard/account/tokens, then:"; \
		echo "  SUPABASE_ACCESS_TOKEN=sbp_... make verify-prod-constraints"; \
		exit 1; \
	fi
	python3 scripts/migrations/_verify_constraints.py

verify-prod-constraints-canary:  ## One-shot probe — confirm Management API echoes RAISE EXCEPTION verbatim
	@if [ -z "$$SUPABASE_ACCESS_TOKEN" ]; then \
		echo "ERROR: SUPABASE_ACCESS_TOKEN not set."; exit 1; \
	fi
	python3 scripts/migrations/_verify_constraints.py --canary

workflow-hashes:  ## Regenerate .github/workflow-hashes.json after intentional workflow edits
	@python3 -c "import json, hashlib, pathlib; \
files = sorted(pathlib.Path('.github/workflows').glob('*.yml')); \
data = {'_doc': 'Pinned sha256 of every .github/workflows/*.yml file. Regenerate via make workflow-hashes.', \
        'files': {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in files}}; \
pathlib.Path('.github/workflow-hashes.json').write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')"
	@echo "Regenerated .github/workflow-hashes.json — commit alongside the workflow change."
