"""Serverless entrypoint.

The repository root is put on `sys.path` so `algotrade.domain` and
`algotrade.repositories` import here exactly as they do in the trading jobs.
Sharing that code rather than reimplementing it is the whole reason the
dashboard lives inside this repository.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]      # <repo>/
DASHBOARD = ROOT / "dashboard"

for path in (str(ROOT), str(DASHBOARD)):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.main import create_app  # noqa: E402

app = create_app()
