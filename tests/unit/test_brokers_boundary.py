"""`place()` is reachable from two files, and this test says which (design §4).

Placing an order is the only irreversible write in this system. Everything else
rebuilds -- holdings from executions, cash from the ledger, a bad snapshot from
a repair job. An order that reached the exchange cannot be undone by re-running
anything.

So the constraint is mechanical rather than a promise in a document, for the
same reason `test_domain_purity.py` exists: without a check, a call leaks into
somewhere convenient within a year, and by then the reviewer who would have
caught it has forgotten the rule.

This test does NOT check that the code is correct. It checks that the surface
which can place an order stays as small as it was designed to be.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "algotrade"
DASHBOARD = REPO / "dashboard" / "app"

#: Method names that submit an order: ours, and the vendor library's.
SUBMITTING = {"place", "place_order"}

#: Files permitted to CALL one of those, relative to the repo root.
#:
#: **One file.** `services/submission.py` owns the
#: write-intent-then-submit-then-confirm sequence that makes a crash
#: recoverable; a call from anywhere else bypasses it and can place an order
#: nothing has a record of. Defining the method is not calling it, which is why
#: `brokers/base.py` and `brokers/stub.py` are absent.
#:
#: `brokers/kite.py` joins when its `place()` stops raising and starts calling
#: the vendor's `place_order`. Nothing else should ever be added here without
#: the same argument being made again.
ALLOWED: set[str] = {"algotrade/services/submission.py"}


def _calls_place(path: Path) -> bool:
    """True if the module calls an order-submitting method on anything."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in SUBMITTING):
            return True
    return False


def _sources() -> list[Path]:
    roots = [PACKAGE, DASHBOARD] if DASHBOARD.exists() else [PACKAGE]
    return sorted(p for root in roots for p in root.rglob("*.py"))


def test_only_the_designated_files_can_place_an_order():
    offenders = sorted(
        str(path.relative_to(REPO))
        for path in _sources()
        if _calls_place(path) and str(path.relative_to(REPO)) not in ALLOWED
    )
    assert not offenders, (
        f"these files submit an order and are not on the allow-list: "
        f"{offenders}. Placing an order is the only irreversible write in "
        "this system; route it through services/submission.py so the "
        "intent-then-submit-then-confirm sequence still holds."
    )


def test_the_allow_list_stays_one_file():
    """Replaces `test_nothing_can_place_an_order_yet`, which held until §15
    step 4 arrived.

    The number is the point. One file can be reasoned about and reviewed; a
    list that grows by one convenient exception at a time is how the sequence
    in `submission.py` stops being the only path to the exchange.
    """
    assert len(ALLOWED) == 1, sorted(ALLOWED)


def test_the_allow_list_names_files_that_exist():
    """A stale entry would silently widen the boundary as files are renamed."""
    missing = sorted(name for name in ALLOWED if not (REPO / name).exists())
    assert not missing, (
        f"allow-list entries with no such file: {missing}. Remove them, or "
        "the boundary is wider than it reads."
    )


def test_the_check_can_actually_see_a_violation():
    """A guard that cannot fail is not a guard.

    Without this, a change to `_calls_place` that made it always return False
    would leave the boundary test passing while enforcing nothing.
    """
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
        handle.write("broker.place(request)\n")
        path = Path(handle.name)
    try:
        assert _calls_place(path)
    finally:
        path.unlink()
