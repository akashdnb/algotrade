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
