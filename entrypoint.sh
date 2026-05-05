#!/usr/bin/env bash
set -uo pipefail

mkdir -p /logs || true

if [[ $# -gt 0 ]]; then
  exec uv run dabench "$@"
fi

uv run python -m data_agent_baseline.eval_entrypoint 2>&1 | tee -a /logs/runtime.log
exit ${PIPESTATUS[0]}

