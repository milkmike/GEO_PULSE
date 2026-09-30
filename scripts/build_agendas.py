"""Article-first agendas. Default is unpaid discovery; --apply uses a capped campaign."""
import argparse
import json
import logging
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
from src.agenda_worker import locked_cycle, CAMPAIGN
from src.agenda_store import load_articles,load_groups
from src.agenda_discovery import candidate_groups


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--loop',action='store_true')
    parser.add_argument('--interval',type=int,default=600)
    parser.add_argument('--max-calls',type=int,default=20)
    parser.add_argument('--budget-usd',type=Decimal,default=Decimal(os.getenv('JEV_AGENDA_BUDGET_USD','0')))
    args=parser.parse_args()
    if args.interval<60:
        parser.error('interval must be at least 60 seconds')
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    while True:
        if args.apply:
            report=locked_cycle(budget_usd=args.budget_usd,campaign=CAMPAIGN,max_calls=args.max_calls)
        else:
            articles=load_articles()
            groups=candidate_groups(articles,load_groups())
            report={'mode':'dry-run','articles_scanned':len(articles),'candidate_groups':len(groups),
                    'groups':[{'anchor_id':g['anchor']['id'],'candidate_ids':[a['id'] for a in g['candidates']]} for g in groups]}
        print(json.dumps(report,ensure_ascii=False,default=str),flush=True)
        if not args.loop:
            return
        time.sleep(args.interval)

if __name__=='__main__':
    main()
