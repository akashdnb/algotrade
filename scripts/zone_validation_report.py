from __future__ import annotations
import argparse, json
from dataclasses import asdict
from datetime import date
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from algotrade.backtest import registry
from algotrade.io import store
from algotrade.zones.backtest import event_study_metrics
from algotrade.zones.config import load_configs
from algotrade.zones.pipeline import run_for_universe
DEV_END=date(2021,12,31)
def main():
    p=argparse.ArgumentParser(); p.add_argument('--end-date',type=date.fromisoformat,default=DEV_END); p.add_argument('--output-dir',type=Path,default=Path('reports')); a=p.parse_args()
    cfg=load_configs(Path('config/zones/zones_v1.yaml'))
    params={tf.value:asdict(c) for tf,c in cfg.items()}
    trial=registry.register(name='zone_scoring_v1',hypothesis='ATR-normalized displacement away from a compact base, combined with volume, structure and freshness, should rank zones with stronger subsequent favourable reaction.',params=params,dataset='dev',alpha_decay_note='Slow to moderate; event-study only.')
    panel=store.read(Path('data/curated'),end=a.end_date)
    zones=[]
    for day in sorted(panel['date'].unique().to_list()):
        zones.extend([z for r in run_for_universe(panel,configs=cfg,as_of_date=day) for z in r.all_zones])
    metrics=event_study_metrics(zones,panel); registry.record_result(trial.trial_id,metrics)
    a.output_dir.mkdir(parents=True,exist_ok=True); path=a.output_dir/f'zone_validation_{a.end_date.isoformat()}.md'; path.write_text('# Zone scoring validation\n\nTrial: '+trial.trial_id+'\n\n```json\n'+json.dumps(metrics,indent=2,default=str)+'\n```\n',encoding='utf-8'); print(path); return 0
if __name__=='__main__': raise SystemExit(main())
