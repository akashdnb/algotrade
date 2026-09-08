"""Every workflow that validates configuration must be GIVEN the configuration.

The first-ever `weekly-backup.yml` run failed on 2026-08-30 -- not in the
backup, but in its own first step. It ran `check_config`, which treats a
missing `HEALTHCHECKS_PING_URL` as a hard error in `paper`/`prod`, while the
workflow's `env:` block never passed that secret through. The same omission
sat latent in `record-execution.yml`, which is manual-only and had therefore
never run: it would have failed the first time a real fill was recorded.

The requirement is ENVIRONMENT-level -- "this deployment has a dead-man
switch" -- not per-job, so it applies to every workflow targeting `paper`
regardless of whether that workflow pings anything.

This is a static check on the YAML, deliberately: the failure mode is a
workflow that has never been run, so nothing dynamic would catch it. It reads
the required names from `validation.py` rather than hard-coding them, so
adding a new required setting fails here instead of in production at 03:00 on
a Sunday.
"""

from __future__ import annotations


from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((REPO / ".github" / "workflows").glob("*.yml"))

#: Settings `validation._check_*` raises a hard ERROR for when environment is
#: paper or prod. Kept in sync by `test_the_required_list_matches_validation`.
REQUIRED_IN_PAPER = {
    "MONGODB_URI",
    "MONGODB_DATABASE",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "HEALTHCHECKS_PING_URL",
    # A `vars.` rather than a `secrets.`, but required all the same.
    "DEFAULT_PORTFOLIO_ID",
}


def test_there_are_workflows_to_check():
    """A glob that silently matches nothing would make every test below pass."""
    assert WORKFLOWS, "no workflow files found -- this suite would be vacuous"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_paper_workflows_running_check_config_supply_every_required_secret(path):
    text = path.read_text(encoding="utf-8")

    if "environment: paper" not in text:
        pytest.skip("does not target the paper environment")
    if "check_config" not in text:
        pytest.skip("does not validate configuration")

    missing = sorted(name for name in REQUIRED_IN_PAPER if f"{name}:" not in text)
    assert missing == [], (
        f"{path.name} runs check_config with environment=paper but never passes "
        f"{missing} into its env: block. check_config treats these as hard "
        f"errors in paper/prod, so this workflow fails on its FIRST step, "
        f"before doing any of its actual work."
    )


#: `validate` reports one error keyed "TELEGRAM" for the pair, but a workflow
#: has to pass both names.
_KEY_TO_ENV = {"TELEGRAM": ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}


def test_the_required_list_matches_validation():
    """Guard on the guard.

    Derived by RUNNING the validator against an empty paper configuration
    rather than by reading the source: a regex over `_error(` also catches the
    conditional ones (a broker key set without its secret, an invalid
    LOG_LEVEL) that no workflow is expected to supply. What matters here is
    precisely the set that a paper deployment fails on when nothing is set.
    """
    from algotrade.config import validation
    from algotrade.config.settings import Settings

    empty_paper = Settings(
        environment="paper", log_level="INFO", mongodb_uri="", mongodb_database=""
    )
    keys = {f.key for f in validation.validate(empty_paper).errors}

    required = set()
    for key in keys:
        required.update(_KEY_TO_ENV.get(key, (key,)))

    missing = required - REQUIRED_IN_PAPER
    assert missing == set(), (
        f"an empty paper configuration now fails validation on {sorted(missing)}, "
        f"which REQUIRED_IN_PAPER does not list. Add them here, and make sure "
        f"every paper workflow passes them into its env: block."
    )


# ------------------------------------------- alerts that outlive the failure ---
#
# `daily.yml` resolved the business date AFTER validating configuration. When
# validation failed on 2026-09-07 the date step was skipped, and "Alert on
# failure" -- which interpolated that step's output into Python source --
# raised `ValueError: Invalid isoformat string: ''`. `continue-on-error: true`
# swallowed it, so the run that broke the ingest sent no notification at all.
#
# Monday's session went uningested and unreported. Static, because the failure
# mode only appears in a run that has already failed.

def _python_blocks(text: str) -> list[tuple[str, str]]:
    """Inline `python - <<'PY'` heredocs, paired with the step's `if:`."""
    import re

    out = []
    for step in re.split(r"\n      - name: ", text)[1:]:
        for match in re.finditer(r"python - <<'PY'\n(.*?)\n\s*PY\b", step, re.S):
            condition = ""
            found = re.search(r"^\s+if: (.+)$", step, re.M)
            if found:
                condition = found.group(1)
            out.append((condition, match.group(1)))
    return out


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_an_alert_never_interpolates_a_step_that_may_not_have_run(path):
    """A step guarded by `failure()` or `always()` runs precisely when earlier
    steps did NOT. Interpolating `steps.*.outputs.*` into its source is then a
    crash inside the alert -- the one thing that must still work. Pass the
    value through `env:` and handle it being empty."""
    import re

    for condition, block in _python_blocks(path.read_text()):
        if "failure()" not in condition and "always()" not in condition:
            continue
        leaked = re.findall(r"\$\{\{\s*steps\.[^}]*\}\}", block)
        assert not leaked, (
            f"{path.name}: an alert running on `{condition.strip()}` "
            f"interpolates {leaked} -- those are empty when the step it names "
            f"was skipped, which is exactly when this alert fires"
        )


def test_the_block_parser_actually_finds_the_alerts():
    """Guards the two tests above: a regex that matched nothing would make
    them pass on any workflow at all."""
    blocks = [b for path in WORKFLOWS for b in _python_blocks(path.read_text())]
    assert len(blocks) >= 6, f"only found {len(blocks)} inline python blocks"
    assert any("failure()" in condition for condition, _ in blocks)
