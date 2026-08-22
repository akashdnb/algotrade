"""The dependency rule, enforced rather than merely intended (design §1.1).

`domain/` is where every rule about money lives. It stays testable with no
database only as long as nothing in it reaches for one. Without a mechanical
check, Mongo queries leak into business logic within a year -- so this test
exists to fail loudly the first time someone (including a future me) adds an
`import pymongo` to a domain module because it was locally convenient.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DOMAIN = REPO / "algotrade" / "domain"

#: Packages the domain must never depend on, and why.
FORBIDDEN = {
    "pymongo": "domain must not know about the database",
    "bson": "domain must not know about the database",
    "motor": "domain must not know about the database",
    "requests": "domain must not perform I/O",
    "polars": "domain works on entities, not dataframes",
    "algotrade.repositories": "domain must not depend on persistence",
    "algotrade.services": "domain must not depend on orchestration",
    "algotrade.io": "domain must not perform I/O",
    "algotrade.platform": "domain must not depend on infrastructure",
}


def _domain_modules() -> list[Path]:
    return sorted(DOMAIN.glob("*.py"))


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_there_are_domain_modules_to_check():
    """Guards against the check silently passing because the glob broke."""
    assert len(_domain_modules()) >= 6


@pytest.mark.parametrize("path", _domain_modules(), ids=lambda p: p.name)
def test_domain_module_imports_nothing_forbidden(path: Path):
    for imported in _imported_names(path):
        for banned, why in FORBIDDEN.items():
            assert not (imported == banned or imported.startswith(banned + ".")), (
                f"{path.name} imports {imported!r} -- {why}"
            )


def test_domain_imports_only_stdlib_and_itself():
    """The positive form of the same rule: anything new and third-party in the
    domain should be a deliberate decision, not an accident."""
    allowed_prefixes = ("algotrade.domain",)
    stdlib = {
        "__future__", "abc", "ast", "collections", "dataclasses", "datetime",
        "decimal", "enum", "functools", "itertools", "math", "re", "typing",
        "uuid",
    }
    for path in _domain_modules():
        for imported in _imported_names(path):
            root = imported.split(".")[0]
            assert root in stdlib or imported.startswith(allowed_prefixes), (
                f"{path.name} imports {imported!r}, which is neither stdlib nor "
                f"algotrade.domain -- add it to this test's allow-list only if "
                f"the domain genuinely needs it"
            )


def test_pymongo_appears_only_under_repositories():
    """The other half of the boundary: persistence stays in one place."""
    offenders = []
    for path in (REPO / "algotrade").rglob("*.py"):
        if "repositories" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "import pymongo" in text or "from pymongo" in text or "from bson" in text:
            offenders.append(str(path.relative_to(REPO)))
    assert offenders == [], (
        f"pymongo/bson imported outside repositories/: {offenders}"
    )
