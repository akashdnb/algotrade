"""Vercel entrypoint.

At the repository root because that is Vercel's zero-config convention: a
Python file under `/api/` becomes a serverless function with no runtime
pinning to get wrong. The catch-all rewrite in `vercel.json` sends every path
here, and FastAPI does its own routing -- including `/static`, so styling
cannot break independently of the app.

The repository root is on `sys.path` already (this file lives in it), which is
what lets `algotrade.domain` and `algotrade.services` import here exactly as
they do in the trading jobs. Sharing that code rather than reimplementing it
is why the dashboard lives in this repository at all.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard"

for path in (str(ROOT), str(DASHBOARD)):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.main import create_app  # noqa: E402

app = create_app()
