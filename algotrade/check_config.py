"""Verify configuration and connectivity before anything depends on them.

    python -m algotrade.check_config
    python -m algotrade.check_config --no-network     # config only, no I/O
    python -m algotrade.check_config --json

Exit codes: 0 all good, 1 at least one error, 2 configuration could not even be
loaded. Warnings never fail the check -- they are things you should know, not
things that stop a run.

Run it as the first step of every workflow. A job that fails at 20:05 because
`MONGODB_URI` was never set should have failed at 20:00 with a message saying
so, rather than mid-way through a partial write.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from algotrade.config import settings as settings_mod  # noqa: E402
from algotrade.config import validation  # noqa: E402
from algotrade.config.validation import Finding, Severity  # noqa: E402


def check_mongodb(s: settings_mod.Settings) -> list[Finding]:
    """Connect, ping, and report whether the schema has been applied."""
    if not s.mongodb_uri:
        return [Finding(Severity.ERROR, "mongodb", "no URI to connect with")]
    try:
        from algotrade.repositories import client, schema

        db = client.connect(s.mongodb_uri, database=s.mongodb_database)
        latency = client.ping(db)

        out = [Finding(Severity.INFO, "mongodb",
                       f"connected to {s.mongodb_database} in {latency:.0f} ms")]
        if latency > 1000:
            out.append(Finding(Severity.WARNING, "mongodb",
                               f"{latency:.0f} ms round trip is slow for a shared tier"))

        problems = client.verify_schema(db)
        if problems:
            out.append(Finding(
                Severity.ERROR, "mongodb.schema",
                f"{len(problems)} discrepancies vs schema.py; first: {problems[0]}",
                "run: python -m jobs.init_db",
            ))
        else:
            out.append(Finding(Severity.INFO, "mongodb.schema",
                               f"{len(schema.ALL)} collections match schema.py"))

        usage = client.stats(db)
        mb = usage["storage_bytes"] / 1e6
        # Atlas M0 is 512 MB. §10.1 sets warn at 400 MB.
        level = Severity.WARNING if mb > 400 else Severity.INFO
        out.append(Finding(level, "mongodb.storage",
                           f"{mb:.1f} MB used, {usage['objects']:,} documents"))
        db.client.close()
        return out
    except Exception as exc:  # noqa: BLE001 -- any failure here is a config failure
        return [Finding(Severity.ERROR, "mongodb",
                        f"{type(exc).__name__}: {exc}",
                        "check MONGODB_URI, the Atlas IP allow-list, and the user's roles")]


def check_telegram(s: settings_mod.Settings) -> list[Finding]:
    """Verify the bot token and that the chat is reachable.

    `getChat` rather than `sendMessage`: a config check must not put noise in
    the channel every time it runs.
    """
    if not s.telegram_configured:
        return [Finding(Severity.INFO, "telegram", "not configured; skipped")]
    try:
        from algotrade.platform import telegram

        bot = telegram.TelegramClient(s.telegram_bot_token, s.telegram_chat_id)
        me = bot.get_me()
        out = [Finding(Severity.INFO, "telegram",
                       f"authenticated as @{me.get('username', '?')}")]
        chat = bot.get_chat()
        title = chat.get("title") or chat.get("username") or chat.get("id")
        out.append(Finding(Severity.INFO, "telegram.chat", f"reachable: {title}"))
        return out
    except Exception as exc:  # noqa: BLE001
        return [Finding(Severity.ERROR, "telegram", f"{type(exc).__name__}: {exc}",
                        "check TELEGRAM_BOT_TOKEN, and that the bot has been "
                        "added to the chat and can post")]


def check_healthchecks(s: settings_mod.Settings) -> list[Finding]:
    """Ping the dead-man switch's /start endpoint.

    /start rather than the bare URL: a config check must not register a
    *success*, which would reset the timer and mask a genuinely dead system.
    """
    if not s.healthchecks_configured:
        return [Finding(Severity.INFO, "healthchecks", "not configured; skipped")]
    try:
        import requests

        response = requests.get(
            s.healthchecks_ping_url.rstrip("/") + "/start", timeout=10
        )
        if response.ok:
            return [Finding(Severity.INFO, "healthchecks", "ping endpoint reachable")]
        return [Finding(Severity.WARNING, "healthchecks",
                        f"HTTP {response.status_code} from the ping URL")]
    except Exception as exc:  # noqa: BLE001
        return [Finding(Severity.WARNING, "healthchecks",
                        f"{type(exc).__name__}: {exc}",
                        "the heartbeat is unreachable; total failure would be silent")]


def check_market_data(s: settings_mod.Settings) -> list[Finding]:
    """Enough history on disk to compute the signal at all."""
    # archive_dirs, not archive_dir: the property also knows about the
    # pre-existing research mirror, so this must not report "no data" on a
    # machine that has 16 years of it.
    roots = ([s.sessions_dir] if s.sessions_dir.exists() else []) + s.archive_dirs
    if not roots:
        return [Finding(Severity.WARNING, "market_data",
                        "no session or archive directory yet",
                        "run: python -m jobs.ingest_session --bootstrap")]
    count = sum(len(list(r.rglob("sec_bhavdata_full_*.csv"))) for r in roots)

    if count == 0:
        return [Finding(Severity.WARNING, "market_data", "no session files found")]
    if count < s.min_history_sessions:
        return [Finding(
            Severity.WARNING, "market_data",
            f"{count} sessions on disk, need {s.min_history_sessions}",
            "12-1 momentum needs 252 + 21 = 273 sessions; the signal cannot be "
            "computed until then",
        )]
    return [Finding(Severity.INFO, "market_data", f"{count:,} session files on disk")]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-network", action="store_true",
                    help="validate configuration only; contact nothing")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--env-file", type=Path, default=None)
    args = ap.parse_args()

    try:
        s = settings_mod.load(env_file=args.env_file)
    except Exception as exc:  # noqa: BLE001
        print(f"could not load settings: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    findings = list(validation.validate(s).findings)
    if not args.no_network:
        findings += check_mongodb(s)
        findings += check_telegram(s)
        findings += check_healthchecks(s)
    findings += check_market_data(s)

    report = validation.Report(findings)

    if args.json:
        print(json.dumps({
            "ok": report.ok,
            "environment": s.environment,
            "settings": {k: str(v) for k, v in s.redacted().items()},
            "findings": [asdict(f) for f in findings],
        }, indent=2, default=str))
        return 0 if report.ok else 1

    print(f"algotrade config check -- environment={s.environment}\n")
    for key, value in s.redacted().items():
        print(f"  {key:<24} {value}")
    print()

    for finding in findings:
        print(finding)

    errors, warnings = len(report.errors), len(report.warnings)
    print()
    if report.ok:
        print(f"OK -- {warnings} warning(s), no errors")
        return 0
    print(f"FAIL -- {errors} error(s), {warnings} warning(s)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
