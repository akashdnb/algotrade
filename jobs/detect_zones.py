from __future__ import annotations
import argparse, hashlib, logging, os, sys
from datetime import date
from pathlib import Path
import polars as pl

REPO=Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path: sys.path.insert(0,str(REPO))
from algotrade.config import settings as settings_mod
from algotrade.config import validation
from algotrade.platform import clock
from algotrade.repositories import client
from algotrade.repositories.jobs import JobAlreadySucceeded, JobRunRepository
from algotrade.repositories.zones import ZoneRepository
from algotrade.services import panel as panel_service
from algotrade.services.notification import NotificationService
from algotrade.platform.telegram import TelegramClient
from algotrade.zones.config import load_configs
from algotrade.zones.pipeline import ZONES_MIN_SESSIONS, run_for_universe
from algotrade.zones.report import render_zones_summary

JOB_NAME="zones_daily"

def main():
    p=argparse.ArgumentParser(); p.add_argument("--date",type=date.fromisoformat); p.add_argument("--through",type=date.fromisoformat); p.add_argument("--backfill",type=int,default=5); p.add_argument("--force",action="store_true"); p.add_argument("--dry-run",action="store_true"); a=p.parse_args()
    settings=settings_mod.load(); logging.basicConfig(level=getattr(logging,settings.log_level,logging.INFO))
    report=validation.validate(settings)
    if report.errors and not a.dry_run: return 2
    configs=load_configs(REPO/"config/zones/zones_v1.yaml")
    through=a.date or a.through
    loaded=panel_service.load(curated_dir=settings.curated_dir, actions_dir=settings.data_dir/"raw"/"actions", through=through, sessions=ZONES_MIN_SESSIONS+a.backfill, min_sessions=ZONES_MIN_SESSIONS)
    sessions=sorted(d for d in loaded.panel["date"].unique().to_list() if not a.through or d<=a.through)
    targets=[a.date] if a.date else sessions[-a.backfill:]
    if not targets: return 0
    db=None; jobs=None; repo=None
    if not a.dry_run:
        db=client.connect(settings.mongodb_uri,database=settings.mongodb_database); jobs=JobRunRepository(db); repo=ZoneRepository(db)
        targets=[d for d in targets if a.force or d in set(jobs.missing_dates(JOB_NAME,start=targets[0],end=targets[-1]))]
    total=0; results=[]
    for day in targets:
        run_id=None
        if jobs:
            try: run_id=jobs.claim(JOB_NAME,day,now=clock.SystemClock().now(),github_run_id=settings.github_run_id or None,code_commit=settings.code_commit,trigger=settings.github_event_name,trigger_source=settings.github_trigger_source)
            except JobAlreadySucceeded: continue
        try:
            results=run_for_universe(loaded.panel,configs=configs,as_of_date=day)
            count=0
            for result in results:
                for tf,zones in result.zones.items():
                    count+=len(zones)
                    if repo: repo.replace_for_symbol_timeframe(result.symbol,tf,zones,config_hash=hashlib.sha256((REPO/"config/zones/zones_v1.yaml").read_bytes()).hexdigest(),computed_at=clock.SystemClock().now())
            total+=count
            if jobs and run_id: jobs.complete(run_id,now=clock.SystemClock().now(),result={"symbols_scanned":len(results),"zones_written":count})
        except Exception as exc:
            if jobs and run_id: jobs.fail(run_id,now=clock.SystemClock().now(),error={"type":type(exc).__name__,"message":str(exc)[:1000]})
            raise
    if not a.dry_run and results:
        client_telegram=TelegramClient(settings.telegram_bot_token,settings.telegram_chat_id) if settings.telegram_bot_token and settings.telegram_chat_id else None
        NotificationService(repository=None,client=client_telegram,chat_id=settings.telegram_chat_id).send(kind="zones_daily",text=render_zones_summary(results),now=clock.SystemClock().now(),portfolio_id=None,business_date=targets[-1])
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"],"a") as f: f.write(f"zones_written={total}\nsymbols_scanned={len(results)}\n")
    print(f"{total} zones across {len(results)} symbols")
    return 0

if __name__=="__main__": raise SystemExit(main())
