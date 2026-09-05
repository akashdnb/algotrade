"""The dashboard must not reach the trading system's heavy dependencies.

`pyproject.toml` declares the deploy's dependencies deliberately separately
from the trading system's `requirements.txt`, and says why: polars, pandas,
duckdb, pyarrow and nsefin together threaten Vercel's function bundle limit,
and the dashboard does not need any of them.

That was a comment, not a check — so on 2026-09-05 a new route imported
`StrategySpec` from `algotrade.services.signals`, which imports polars, and the
whole deployment failed at startup. **Every route returned 500, including
`/healthz`**, which exists precisely to answer when everything else cannot.
The merge had to be reverted.

This walks the import graph from every dashboard module and fails if a
forbidden package is reachable, naming the chain. Adding a dependency to the
deploy is then a deliberate act -- add it to `pyproject.toml`, which this
test reads -- rather than something a single convenient import does silently.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "dashboard" / "app"

#: Never acceptable in the deploy. Size, not principle: polars alone is larger
#: than every other dependency combined.
FORBIDDEN = {"polars", "pandas", "duckdb", "pyarrow", "nsefin", "openpyxl"}

#: Distribution name -> the module you actually import. Only the cases where
#: they differ.
_MODULE_OF = {
    "python-multipart": "multipart",
    "pyyaml": "yaml",
}

#: Packages a declared dependency brings with it, which the deploy therefore
#: has even though `pyproject.toml` does not name them.
_TRANSITIVE = {"starlette", "bson", "anyio", "pydantic"}


def _declared() -> set[str]:
    """Third-party packages the deploy carries, **read from pyproject.toml**.

    Derived rather than duplicated. A hand-kept copy of this list is a second
    source of truth that drifts, and the drift would show up as the deploy
    failing at startup -- which is the failure this file exists to prevent, so
    it must not be able to cause it.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = text.split("dependencies = [", 1)[1].split("]", 1)[0]
    names = set()
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith(('"', "'")):
            continue
        raw = line.strip("\"',").split("==")[0].split(">=")[0].strip().lower()
        names.add(_MODULE_OF.get(raw, raw.replace("-", "_")))
    return names | _TRANSITIVE


ALLOWED = _declared()


def _imports(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def _module_file(module: str) -> Path | None:
    direct = ROOT / (module.replace(".", "/") + ".py")
    if direct.exists():
        return direct
    package = ROOT / module.replace(".", "/") / "__init__.py"
    return package if package.exists() else None


def _reach(module: str, chain: list[str], seen: set[str],
           hits: dict[str, str]) -> None:
    if module in seen:
        return
    seen.add(module)
    path = _module_file(module)
    if path is None:
        return
    for imported in sorted(_imports(path)):
        top = imported.split(".")[0]
        if top in FORBIDDEN:
            hits.setdefault(top, " -> ".join(chain + [module, imported]))
        elif imported.startswith("algotrade"):
            _reach(imported, chain + [module], seen, hits)


def _dashboard_modules() -> list[Path]:
    return sorted(p for p in APP.rglob("*.py") if "__pycache__" not in p.parts)


def test_no_dashboard_module_reaches_a_heavy_dependency():
    hits: dict[str, str] = {}
    for path in _dashboard_modules():
        origin = str(path.relative_to(ROOT))
        for imported in sorted(_imports(path)):
            if imported.startswith("algotrade"):
                _reach(imported, [origin], set(), hits)

    assert not hits, (
        "the deploy would not start -- these packages are not in "
        "pyproject.toml and are reachable from the dashboard:\n  "
        + "\n  ".join(f"{dep}: {chain}" for dep, chain in sorted(hits.items()))
    )


def test_every_third_party_import_is_declared():
    """A package reachable but undeclared fails at startup, not at first use.

    Catches the subtler version of the same bug: not a huge dependency, just
    one nobody added to the deploy's list.
    """
    undeclared: dict[str, str] = {}
    for path in _dashboard_modules():
        origin = str(path.relative_to(ROOT))
        seen: set[str] = set()
        queue = [(imported, [origin]) for imported in sorted(_imports(path))]
        while queue:
            module, chain = queue.pop()
            top = module.split(".")[0]
            if module.startswith("algotrade"):
                if module in seen:
                    continue
                seen.add(module)
                found = _module_file(module)
                if found is not None:
                    queue += [(m, chain + [module])
                              for m in sorted(_imports(found))]
            elif top in ALLOWED or top in FORBIDDEN or _is_stdlib(top):
                continue
            elif top not in ("app",):
                undeclared.setdefault(top, " -> ".join(chain + [module]))

    assert not undeclared, (
        "reachable from the dashboard but not in pyproject.toml:\n  "
        + "\n  ".join(f"{dep}: {chain}" for dep, chain in sorted(undeclared.items()))
    )


def _is_stdlib(name: str) -> bool:
    import sys
    return name in sys.stdlib_module_names


def test_the_app_builds_with_the_heavy_packages_unavailable():
    """The check the AST walk cannot make: actually build it.

    Static analysis misses an import inside a function, or one reached through
    a string. This blocks the forbidden packages at the import system and then
    calls `create_app`, which is what Vercel does -- and what nobody did before
    the merge that took the deployment down.
    """
    import subprocess
    import sys

    probe = """
import sys, os
class Blocker:
    BLOCKED = %r
    def find_module(self, name, path=None):
        return self if name.split('.')[0] in self.BLOCKED else None
    def load_module(self, name):
        raise ImportError(name + ' is not in the Vercel bundle')
sys.meta_path.insert(0, Blocker())
sys.path.insert(0, %r); sys.path.insert(0, %r)
os.environ.update(MONGODB_URI='mongodb://localhost:27017/',
                  MONGODB_DATABASE='algotrade_paper',
                  PORTFOLIO_ID='paper_momentum_v1_001',
                  DASHBOARD_USER='u', DASHBOARD_PASSWORD='abcdefghijkl')
from app.main import create_app
app = create_app()
paths = {r.path for r in app.routes if hasattr(r, 'path')}
assert '/broker' in paths and '/review' in paths, sorted(paths)
print('OK')
""" % (set(FORBIDDEN), str(ROOT / "dashboard"), str(ROOT))

    result = subprocess.run([sys.executable, "-c", probe],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, (
        "the app does not build without the heavy packages, so the deploy "
        f"would fail at startup:\n{result.stderr[-2000:]}"
    )


def test_the_check_can_actually_see_a_violation():
    """A guard that cannot fail is not a guard."""
    hits: dict[str, str] = {}
    _reach("algotrade.services.signals", ["probe"], set(), hits)
    assert "polars" in hits, (
        "services.signals no longer imports polars -- if that is deliberate, "
        "point this probe at another module that does, or the boundary test "
        "above is enforcing nothing."
    )
