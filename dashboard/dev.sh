#!/usr/bin/env bash
# Run the dashboard locally. Works from anywhere.
#
# Two things this gets right that a bare uvicorn command does not:
#
#   * The entrypoint is `api/index.py` at the REPOSITORY ROOT, not inside
#     `dashboard/` -- that is Vercel's zero-config location, and it needs the
#     root on sys.path to import the `algotrade` package.
#   * `--reload-dir` is scoped to the three directories that hold code.
#     Without it uvicorn watches the whole repo, and StatReload polls the
#     1.9 GB of market data in `data/` on every cycle.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="${PORT:-8000}"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

if [ ! -f "$ROOT/dashboard/.env" ]; then
  echo "dashboard/.env is missing. Copy dashboard/.env.example and fill it in." >&2
  exit 1
fi

echo "dashboard  http://127.0.0.1:$PORT"
exec "$PY" -m uvicorn api.index:app --reload \
  --reload-dir dashboard --reload-dir algotrade --reload-dir api \
  --host 127.0.0.1 --port "$PORT"
