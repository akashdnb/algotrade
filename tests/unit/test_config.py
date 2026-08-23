"""Tests for settings resolution, validation, and the environment safety guards.

The guards are the important part: they exist to make one specific accident
impossible -- operating real money from a non-production environment, or
pointing a job at another environment's database.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from algotrade.config import settings as S
from algotrade.config import validation as V

REPO = Path(__file__).resolve().parents[2]


def _env(**over) -> dict[str, str]:
    base = {
        "ENVIRONMENT": "dev",
        "MONGODB_URI": "mongodb://localhost:27017",
        "MONGODB_DATABASE": "algotrade_dev",
    }
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def _load(**over) -> S.Settings:
    return S.load(environ=_env(**over), env_file=Path("/nonexistent"), repo_root=REPO)


# ------------------------------------------------------------ .env parsing ---


def test_env_file_parsing_handles_comments_blanks_and_quotes():
    parsed = S.parse_env_file(
        "\n".join([
            "# a comment",
            "",
            "PLAIN=value",
            'QUOTED="quoted value"',
            "SINGLE='single'",
            "  SPACED  =  padded  ",
            "NO_EQUALS_SIGN",
            "EMPTY=",
        ])
    )
    assert parsed == {
        "PLAIN": "value", "QUOTED": "quoted value", "SINGLE": "single",
        "SPACED": "padded", "EMPTY": "",
    }


def test_a_url_with_a_hash_is_not_treated_as_a_comment():
    """Splitting on '#' anywhere would corrupt a connection string."""
    parsed = S.parse_env_file("MONGODB_URI=mongodb://u:p#word@host/db")
    assert parsed["MONGODB_URI"] == "mongodb://u:p#word@host/db"


def test_the_process_environment_beats_the_env_file(tmp_path: Path):
    """CI sets real Secrets; a stale local .env must never override them."""
    env_file = tmp_path / ".env"
    env_file.write_text("MONGODB_URI=mongodb://from-file\nENVIRONMENT=prod\n")
    s = S.load(environ={"MONGODB_URI": "mongodb://from-process"},
               env_file=env_file, repo_root=REPO)
    assert s.mongodb_uri == "mongodb://from-process"
    assert s.environment == "prod"      # only in the file, so the file supplies it


# ----------------------------------------------------------------- loading ---


def test_the_database_defaults_per_environment():
    assert S.load(environ={"ENVIRONMENT": "dev", "MONGODB_URI": "m"},
                  env_file=Path("/none")).mongodb_database == "algotrade_dev"
    assert S.load(environ={"ENVIRONMENT": "prod", "MONGODB_URI": "m"},
                  env_file=Path("/none")).mongodb_database == "algotrade_prod"


def test_credentials_never_appear_in_the_redacted_view():
    s = _load(MONGODB_URI="mongodb+srv://user:hunter2@cluster.mongodb.net/db",
              TELEGRAM_BOT_TOKEN="123:SECRET", BROKER_API_SECRET="broker-secret")
    blob = str(s.redacted())
    assert "hunter2" not in blob
    assert "SECRET" not in blob
    assert "broker-secret" not in blob
    assert "cluster.mongodb.net" in blob


def test_redaction_survives_a_uri_with_no_credentials():
    assert S.redact_uri("mongodb://localhost:27017") == "mongodb://localhost:27017"


def test_redaction_of_a_password_containing_an_at_sign():
    """rpartition, not partition: an '@' inside the password would otherwise
    leave the rest of it exposed."""
    out = S.redact_uri("mongodb+srv://user:p@ss@cluster.net/db")
    assert "p@ss" not in out
    assert out.endswith("cluster.net/db")


# -------------------------------------------------------------- validation ---


def test_a_clean_dev_configuration_passes():
    assert V.validate(_load()).ok


def test_a_missing_mongodb_uri_is_an_error():
    report = V.validate(_load(MONGODB_URI=None))
    assert not report.ok
    assert any(f.key == "MONGODB_URI" for f in report.errors)


def test_an_unknown_environment_is_an_error():
    assert not V.validate(_load(ENVIRONMENT="staging")).ok


def test_an_invalid_log_level_is_an_error():
    assert not V.validate(_load(LOG_LEVEL="CHATTY")).ok


def test_every_problem_is_reported_not_just_the_first():
    """Setting up a new environment is when this runs most; fixing one
    variable per attempt is a bad loop."""
    report = V.validate(_load(ENVIRONMENT="paper", MONGODB_URI=None,
                              MONGODB_DATABASE="algotrade_paper"))
    keys = {f.key for f in report.errors}
    assert {"MONGODB_URI", "TELEGRAM", "HEALTHCHECKS_PING_URL",
            "DEFAULT_PORTFOLIO_ID"} <= keys


def test_telegram_is_optional_in_dev_but_required_in_paper():
    assert V.validate(_load()).ok
    paper = V.validate(_load(ENVIRONMENT="paper", MONGODB_DATABASE="algotrade_paper"))
    assert any(f.key == "TELEGRAM" for f in paper.errors)


def test_the_heartbeat_is_required_once_running_unattended():
    """Without it, total failure is silent -- see platform.health."""
    paper = V.validate(_load(ENVIRONMENT="paper", MONGODB_DATABASE="algotrade_paper"))
    assert any(f.key == "HEALTHCHECKS_PING_URL" for f in paper.errors)


def test_broker_credentials_are_optional_because_nothing_places_orders():
    report = V.validate(_load())
    assert report.ok
    assert any(f.key == "BROKER" for f in report.findings)


def test_a_half_configured_broker_is_an_error():
    assert not V.validate(_load(BROKER_API_KEY="key")).ok
    assert not V.validate(_load(BROKER_API_SECRET="secret")).ok


def test_broker_credentials_outside_prod_are_flagged():
    report = V.validate(_load(BROKER_API_KEY="k", BROKER_API_SECRET="s",
                              BROKER_NAME="zerodha"))
    assert any(f.key == "BROKER" for f in report.warnings)


# --------------------------------------------------------- safety guards ----


def test_pointing_one_environment_at_anothers_database_is_refused():
    """The most expensive configuration mistake available here."""
    report = V.validate(_load(ENVIRONMENT="dev", MONGODB_DATABASE="algotrade_prod"))
    assert not report.ok
    assert any("belongs to" in f.message for f in report.errors)


def test_an_unrecognised_database_name_is_only_a_warning():
    """A scratch database is legitimate; another environment's is not."""
    report = V.validate(_load(MONGODB_DATABASE="algotrade_scratch"))
    assert report.ok
    assert any(f.key == "MONGODB_DATABASE" for f in report.warnings)


def test_a_live_portfolio_outside_prod_is_refused_at_validation():
    report = V.validate(_load(ENVIRONMENT="dev",
                              DEFAULT_PORTFOLIO_ID="live_momentum_v1_001"))
    assert not report.ok
    assert any("live portfolio" in f.message for f in report.errors)


def test_a_malformed_default_portfolio_id_is_refused():
    assert not V.validate(_load(DEFAULT_PORTFOLIO_ID="PAPER_MAIN")).ok


def test_guard_portfolio_blocks_live_from_a_paper_environment():
    """Checked at the point of action too, because a job can be handed an id on
    the command line that startup validation never saw."""
    s = _load(ENVIRONMENT="paper", MONGODB_DATABASE="algotrade_paper")
    with pytest.raises(S.ConfigError, match="real money requires"):
        V.guard_portfolio(s, "live_momentum_v1_001")


def test_guard_portfolio_allows_live_in_prod():
    s = _load(ENVIRONMENT="prod", MONGODB_DATABASE="algotrade_prod")
    V.guard_portfolio(s, "live_momentum_v1_001")       # must not raise


def test_guard_portfolio_allows_paper_anywhere():
    for env, db in (("dev", "algotrade_dev"), ("paper", "algotrade_paper"),
                    ("prod", "algotrade_prod")):
        V.guard_portfolio(_load(ENVIRONMENT=env, MONGODB_DATABASE=db),
                          "paper_momentum_v1_001")


def test_guard_portfolio_rejects_a_malformed_id():
    with pytest.raises(S.ConfigError):
        V.guard_portfolio(_load(), "not-a-portfolio")


def test_guard_write_blocks_mutation_during_a_dry_run():
    s = _load(ALGOTRADE_DRY_RUN="true")
    assert s.dry_run
    with pytest.raises(S.ConfigError, match="dry run"):
        V.guard_write(s, operation="save snapshot")


def test_require_valid_raises_with_every_error_listed():
    with pytest.raises(S.ConfigError) as exc:
        V.require_valid(_load(ENVIRONMENT="paper", MONGODB_URI=None,
                              MONGODB_DATABASE="algotrade_paper"))
    assert "MONGODB_URI" in str(exc.value)
    assert "TELEGRAM" in str(exc.value)
