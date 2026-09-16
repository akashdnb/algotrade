"""Every production `HoldingsService` must be given the action history.

`verify()` re-derives the book and compares it against what is stored. It and
`rebuild()` therefore have to read corporate actions from the *same* source --
if only one of them replays them, a correctly split-adjusted book reports
itself as disagreeing with its own trade record, every night, forever.

That is not hypothetical. `acceptance/status.py` was missed on the first pass
of this change and immediately scored the system RED with "the book disagrees
with the trade record, so every derived number is wrong" -- on a book that was
right. The constructor argument is optional (so the change could land without
touching every call site at once), which is exactly why it needs a mechanical
guard rather than a habit.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

#: Where production wiring lives. Tests build the service deliberately without
#: an action history -- that is the "behaves as before" case -- so they are not
#: scanned.
PRODUCTION_DIRS = ("algotrade", "jobs", "dashboard")


def _call_sites() -> list[tuple[Path, ast.Call]]:
    out: list[tuple[Path, ast.Call]] = []
    for directory in PRODUCTION_DIRS:
        for path in (REPO / directory).rglob("*.py"):
            if "test" in path.name:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError:  # pragma: no cover
                continue
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "HoldingsService"):
                    out.append((path, node))
    return out


def test_there_are_call_sites_to_check():
    """Guards against the scan silently passing because the walk broke."""
    assert len(_call_sites()) >= 5


@pytest.mark.parametrize(
    "path,node", _call_sites(),
    ids=lambda v: v.name if isinstance(v, Path) else "",
)
def test_every_production_holdings_service_gets_corporate_actions(path, node):
    passed = {kw.arg for kw in node.keywords}
    assert "corporate_actions" in passed, (
        f"{path.relative_to(REPO)}:{node.lineno} builds a HoldingsService "
        f"without `corporate_actions`. It will derive a pre-split book and "
        f"report a correct one as corrupt -- pass CorporateActionRepository(db)."
    )
