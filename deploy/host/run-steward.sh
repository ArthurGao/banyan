#!/usr/bin/env bash
# llm-steward as Banyan uses it on the host (deploy/README.md §2, §8): lite-rag and lite-mem in
# steward's process, embeddings and extraction on the host's Ollama. Run by launchd from
# banyan-host.sh; arguments: steward's backend directory, the uv binary.
# lite-rag reads its database from LITERAG_DATABASE_URL (psycopg scheme); it is derived from
# steward's own .env here, never printed.
set -euo pipefail
cd "$1"
LITERAG_DATABASE_URL="$(python3 - <<'PY'
import re
for line in open(".env"):
    m = re.match(r"^STEWARD_DATABASE_URL=(.*)$", line.strip())
    if m:
        print(re.sub(r"^postgresql\+asyncpg", "postgresql", m.group(1).strip('"')))
PY
)"
export LITERAG_DATABASE_URL
export STEWARD_KB_PROVIDER=literag STEWARD_LITERAG_MODE=inprocess
export STEWARD_MEMORY_PROVIDER=litemem STEWARD_LITEMEM_MODE=inprocess STEWARD_MEMORY_LLM=false
# qwen3 thinks by default: one extraction then exceeds steward's 30 s job limit (deploy/README.md §2).
export LITEMEM_LLM__REASONING=none
exec "$2" run steward serve
