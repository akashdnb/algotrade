def test_detection_job_module_imports():
    import jobs.detect_zones as module

    assert module.JOB_NAME == "zones_daily"


def test_detection_job_parser_supports_symbol_and_offline():
    from jobs.detect_zones import build_parser

    args = build_parser().parse_args(["--symbol", "RELIANCE", "--offline"])

    assert args.symbol == "RELIANCE"
    assert args.offline is True
    assert args.dry_run is False


def test_detection_job_dry_run_is_offline_alias():
    from jobs.detect_zones import build_parser

    args = build_parser().parse_args(["--symbol", "RELIANCE", "--dry-run"])

    assert args.symbol == "RELIANCE"
    assert args.dry_run is True


def _sessions():
    from datetime import date, timedelta

    return [date(2024, 1, 1) + timedelta(days=i) for i in range(10)]


def test_persistent_run_writes_latest_session_only():
    from jobs.detect_zones import select_targets

    days = _sessions()
    assert select_targets(days, as_of=None, through=None, backfill=5, offline=False) == [days[-1]]
    assert select_targets(days, as_of=None, through=None, backfill=5, offline=True) == days[-5:]


def test_persistent_run_refuses_historical_as_of():
    import pytest

    from jobs.detect_zones import select_targets

    days = _sessions()
    with pytest.raises(ValueError, match="read-only"):
        select_targets(days, as_of=days[3], through=None, backfill=5, offline=False)
    with pytest.raises(ValueError, match="read-only"):
        select_targets(days, as_of=None, through=days[3], backfill=5, offline=False)
    assert select_targets(days, as_of=None, through=days[3], backfill=2, offline=True) == days[2:4]
