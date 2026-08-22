"""Integration tests against a real MongoDB.

These verify the guarantees that only the database can provide: schema
validation actually rejecting bad documents, unique partial indexes actually
preventing a second success, and Decimal128 actually round-tripping. Skipped
without ALGOTRADE_TEST_MONGODB_URI (see conftest).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from bson.decimal128 import Decimal128
from pymongo.errors import WriteError

from algotrade.domain import money
from algotrade.domain.models import Holding, Lot, Position, Snapshot
from algotrade.repositories import client, codec, schema
from algotrade.repositories.base import AlreadyExists
from algotrade.repositories.holdings import HoldingRepository
from algotrade.repositories.jobs import JobAlreadySucceeded, JobRunRepository
from algotrade.repositories.notifications import NotificationRepository
from algotrade.repositories.portfolios import PortfolioRepository
from algotrade.repositories.rebalance_runs import RebalanceRunRepository
from algotrade.repositories.snapshots import SnapshotRepository

D = date
R = money.rupees
NOW = datetime(2026, 9, 30, 14, 30, tzinfo=timezone.utc)
PID = "paper_momentum_v1_001"


def _portfolio(**over):
    """Domain-shaped: money as Decimal, for writes THROUGH a repository."""
    return {
        "_id": PID, "mode": "paper", "strategy": "momentum",
        "strategy_version": "momentum_v1", "strategy_params_hash": "sha256:abc",
        "status": "active", "currency": "INR",
        "initial_capital": R("200000.00"), "monthly_contribution": R("20000.00"),
        "cash": R("14320.55"), "total_contributed": R("200000.00"),
        "units": R("2000.000"), "contribution_day": 1,
        **over,
    }


def _raw_portfolio(**over):
    """BSON-shaped, for writes that bypass the repository to test the VALIDATOR.

    Money must already be Decimal128 here: pymongo cannot encode a bare
    `Decimal` at all, which is itself a useful guarantee -- a stray script
    cannot accidentally write one.
    """
    doc = codec.encode_doc(
        _portfolio(), money_fields=schema.MONEY_FIELDS["portfolios"]
    )
    doc.update(over)
    return doc


# ------------------------------------------------------------- the schema ---


def test_apply_schema_is_idempotent(db):
    first = client.apply_schema(db)
    second = client.apply_schema(db)
    assert set(first) == set(second) == {c.name for c in schema.ALL}
    assert client.verify_schema(db) == []


def test_verify_schema_reports_a_missing_index(db):
    db["job_runs"].drop_index("one_success")
    problems = client.verify_schema(db)
    assert any("one_success" in p for p in problems)


def test_the_validator_rejects_a_float_in_a_money_field(db):
    """The guard that stops a stray script writing a double where rupees belong."""
    with pytest.raises(WriteError):
        db["portfolios"].insert_one(_raw_portfolio(cash=14320.55))


def test_pymongo_cannot_even_encode_a_bare_decimal(db):
    """Belt and braces: the driver refuses before the validator is consulted,
    so a Decimal that skipped the codec fails loudly rather than as a double."""
    from bson.errors import InvalidDocument

    with pytest.raises(InvalidDocument):
        db["portfolios"].insert_one(_portfolio())


def test_the_validator_rejects_a_malformed_portfolio_id(db):
    with pytest.raises(WriteError):
        db["portfolios"].insert_one(_raw_portfolio(_id="prod_momentum_v1_001"))


def test_the_validator_rejects_an_unknown_status(db):
    with pytest.raises(WriteError):
        db["portfolios"].insert_one(_raw_portfolio(status="running"))


def test_the_validator_rejects_a_business_date_that_is_not_iso(db):
    repo = SnapshotRepository(db)
    with pytest.raises(WriteError):
        db["portfolio_snapshots"].insert_one({
            "_id": f"{PID}:30-09-2026", "portfolio_id": PID,
            "date": "30-09-2026",
            "nav": Decimal128("1.00"), "cash": Decimal128("1.00"),
            "units": Decimal128("1.00"), "unit_nav": Decimal128("1.00"),
        })


# ------------------------------------------------------------------ money ---


def test_money_round_trips_through_a_real_write_and_read(db):
    repo = PortfolioRepository(db)
    repo.insert(_portfolio())
    back = repo.get(PID)
    assert back["cash"] == R("14320.55")
    assert back["initial_capital"] == R("200000.00")


def test_a_large_amount_keeps_its_paise_through_mongo(db):
    repo = PortfolioRepository(db)
    repo.insert(_portfolio(cash=R("99999999.99")))
    assert repo.get(PID)["cash"] == R("99999999.99")


# --------------------------------------------------------------- job runs ---


def test_a_second_success_for_the_same_business_date_cannot_be_recorded(db):
    """The exactly-once guarantee, enforced by the database rather than by an
    application check that could race (§11.5)."""
    repo = JobRunRepository(db)
    run = repo.claim("daily", D(2026, 9, 30), now=NOW)
    repo.complete(run, now=NOW)

    with pytest.raises(JobAlreadySucceeded):
        repo.claim("daily", D(2026, 9, 30), now=NOW)


def test_the_unique_index_blocks_a_direct_second_success_write(db):
    """Even bypassing `claim` -- which is the point of enforcing it in an index."""
    repo = JobRunRepository(db)
    repo.complete(repo.claim("daily", D(2026, 9, 30), now=NOW), now=NOW)

    from pymongo.errors import DuplicateKeyError

    with pytest.raises(DuplicateKeyError):
        db["job_runs"].insert_one({
            "_id": "daily:2026-09-30:99", "job_name": "daily",
            "business_date": "2026-09-30", "attempt": 99, "status": "success",
        })


def test_a_failure_does_not_block_a_retry(db):
    repo = JobRunRepository(db)
    first = repo.claim("daily", D(2026, 9, 30), now=NOW)
    repo.fail(first, now=NOW, error={"type": "NseFetchError"})

    second = repo.claim("daily", D(2026, 9, 30), now=NOW)
    assert second.endswith(":2")
    repo.complete(second, now=NOW)
    assert repo.already_succeeded("daily", D(2026, 9, 30))


def test_missing_dates_is_the_gap_between_expected_and_succeeded(db):
    repo = JobRunRepository(db)
    repo.complete(repo.claim("daily", D(2026, 9, 30), now=NOW), now=NOW)
    # 1 Oct 2026 is a Thursday, 2 Oct a Friday, 3-4 Oct the weekend.
    missing = repo.missing_dates("daily", start=D(2026, 9, 30), end=D(2026, 10, 5))
    assert missing == [D(2026, 10, 1), D(2026, 10, 2), D(2026, 10, 5)]


def test_a_stuck_run_is_found_by_age(db):
    repo = JobRunRepository(db)
    repo.claim("daily", D(2026, 9, 30), now=NOW - timedelta(hours=9))
    assert len(repo.stuck(now=NOW, older_than_hours=6)) == 1
    assert repo.stuck(now=NOW, older_than_hours=12) == []


def test_duration_is_recorded(db):
    repo = JobRunRepository(db)
    run = repo.claim("daily", D(2026, 9, 30), now=NOW)
    repo.complete(run, now=NOW + timedelta(seconds=157))
    assert repo.get(run)["duration_ms"] == 157_000


# --------------------------------------------------------- rebalance runs ---


def test_a_period_can_only_be_claimed_once(db):
    repo = RebalanceRunRepository(db)
    fp = {"panel_manifest_hash": "h", "strategy_params_hash": "p", "code_commit": "c"}
    repo.start(PID, "2026-09", now=NOW, as_of_session=D(2026, 9, 30), fingerprint=fp)

    with pytest.raises(AlreadyExists):
        repo.start(PID, "2026-09", now=NOW, as_of_session=D(2026, 9, 30),
                   fingerprint=fp)


def test_a_retry_reuses_the_same_document_and_increments_attempt(db):
    repo = RebalanceRunRepository(db)
    fp = {"panel_manifest_hash": "h", "strategy_params_hash": "p", "code_commit": "c"}
    run = repo.start(PID, "2026-09", now=NOW, as_of_session=D(2026, 9, 30),
                     fingerprint=fp)
    repo.fail(run, now=NOW, error={"type": "X"})
    repo.retry(run, now=NOW)

    assert repo.count({"portfolio_id": PID, "period": "2026-09"}) == 1
    assert repo.get(run)["attempt"] == 2
    assert repo.get(run)["status"] == "generating"


# ------------------------------------------------------------- snapshots ----


def test_a_snapshot_is_idempotent_by_portfolio_and_date(db):
    repo = SnapshotRepository(db)
    snap = Snapshot(
        portfolio_id=PID, date=D(2026, 9, 30), nav=R("284310.22"),
        cash=R("14320.55"), invested_value=R("269989.67"), units=R("2000.000"),
        unit_nav=R("142.16"), total_contributed=R("200000.00"),
        positions=[Position(symbol="TCS", quantity=47, close=R("1012.30"),
                            value=R("47578.10"), weight=0.1673)],
        peak_unit_nav=R("145.00"), drawdown=-0.0196,
    )
    assert repo.save(snap) is True          # inserted
    assert repo.save(snap) is False         # replaced, not duplicated
    assert repo.count({"portfolio_id": PID}) == 1

    back = repo.as_of(PID, D(2026, 9, 30))
    assert back.nav == R("284310.22")
    assert back.positions[0].symbol == "TCS"
    assert back.positions[0].close == R("1012.30")


def test_series_projects_away_the_positions_array(db):
    repo = SnapshotRepository(db)
    for i, day in enumerate([D(2026, 9, 28), D(2026, 9, 29), D(2026, 9, 30)]):
        repo.save(Snapshot(
            portfolio_id=PID, date=day, nav=R(f"{100 + i}.00"), cash=R("1.00"),
            invested_value=R("99.00"), units=R("1.000"), unit_nav=R("100.00"),
            total_contributed=R("100.00"),
            positions=[Position("TCS", 1, R("1.00"), R("1.00"), 1.0)],
        ))
    rows = repo.series(PID, fields=["nav"])
    assert len(rows) == 3
    assert "positions" not in rows[0]
    assert rows[0]["nav"] == R("100.00")


# -------------------------------------------------------------- holdings ----


def test_a_holding_round_trips_with_its_lots(db):
    repo = HoldingRepository(db)
    holding = Holding(portfolio_id=PID, symbol="TCS", isin="INE467B01029", lots=[
        Lot("L1", D(2026, 1, 2), 30, R("975.10"), R("15.34"), "set:1"),
        Lot("L2", D(2026, 6, 1), 17, R("992.55"), R("15.34"), "set:2"),
    ])
    repo.save(holding)

    back = repo.get_holding(PID, "TCS")
    assert back.quantity == 47
    assert back.cost_basis == holding.cost_basis
    assert [l.lot_id for l in back.lots] == ["L1", "L2"]
    assert back.lots[0].acquired_on == D(2026, 1, 2)


def test_saving_an_emptied_holding_deletes_the_row(db):
    """A zero-quantity row would have to be filtered out on every read, and the
    one place that forgets reports a closed position as held."""
    repo = HoldingRepository(db)
    repo.save(Holding(portfolio_id=PID, symbol="TCS",
                      lots=[Lot("L1", D(2026, 1, 2), 10, R("100.00"))]))
    assert repo.get_holding(PID, "TCS") is not None

    repo.save(Holding(portfolio_id=PID, symbol="TCS", lots=[]))
    assert repo.get_holding(PID, "TCS") is None
    assert repo.count({"portfolio_id": PID}) == 0


# ---------------------------------------------------------- notifications ---


def test_the_same_daily_summary_cannot_be_sent_twice(db):
    repo = NotificationRepository(db)
    first = repo.claim(kind="daily_summary", text="NAV up 1%", now=NOW,
                       portfolio_id=PID, business_date=D(2026, 9, 30))
    assert first is not None

    again = repo.claim(kind="daily_summary", text="NAV up 1%", now=NOW,
                       portfolio_id=PID, business_date=D(2026, 9, 30))
    assert again is None, "a re-run must not send a second summary"


def test_two_different_failures_on_the_same_day_both_send(db):
    """Deduping alerts by date alone would suppress the second, different
    problem -- exactly the one you have not seen yet (§2.8)."""
    repo = NotificationRepository(db)
    a = repo.claim(kind="job_failure", text="daily failed: NseFetchError",
                   now=NOW, business_date=D(2026, 9, 30))
    b = repo.claim(kind="job_failure", text="rebalance failed: ValidationError",
                   now=NOW, business_date=D(2026, 9, 30))
    assert a is not None and b is not None and a != b


def test_an_identical_failure_still_dedupes(db):
    repo = NotificationRepository(db)
    a = repo.claim(kind="job_failure", text="same", now=NOW,
                   business_date=D(2026, 9, 30))
    b = repo.claim(kind="job_failure", text="same", now=NOW,
                   business_date=D(2026, 9, 30))
    assert a is not None and b is None


# ------------------------------------------------------------ portfolios ---


def test_active_ids_is_what_the_actions_matrix_consumes(db):
    repo = PortfolioRepository(db)
    repo.insert(_portfolio())
    repo.insert(_portfolio(_id="shadow_momentum_v1_001", mode="shadow"))
    repo.insert(_portfolio(_id="paper_momentum_v1_002", status="closed"))

    assert repo.active_ids() == ["paper_momentum_v1_001", "shadow_momentum_v1_001"]
    assert repo.active_ids(mode="shadow") == ["shadow_momentum_v1_001"]


def test_inserting_the_same_portfolio_twice_raises_rather_than_overwriting(db):
    repo = PortfolioRepository(db)
    repo.insert(_portfolio())
    with pytest.raises(AlreadyExists):
        repo.insert(_portfolio(cash=R("999.00")))
    assert repo.get(PID)["cash"] == R("14320.55")
