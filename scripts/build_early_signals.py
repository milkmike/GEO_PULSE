"""Bounded screening, draft writing and explicit editorial publication."""
import argparse
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.early_signal_store import save_dossier
from src.early_signal_worker import locked_screening_cycle, write_draft, WRITER_MODEL, WRITER_MODELS
from src.signal_workbench import instant


def read_json(path):
    data = Path(path).read_bytes()
    if len(data) > 32_000_000:
        raise ValueError('Input exceeds 32MB')
    return json.loads(data)


def write_json(path, value):
    # Avoid spending for a writer whose result cannot be saved.
    with Path(path).open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    screen = sub.add_parser('screen')
    screen.add_argument('--budget-usd', type=Decimal, default=Decimal(os.getenv('EARLY_SIGNAL_BUDGET_USD', '0')))
    screen.add_argument('--max-calls', type=int, default=4)
    screen.add_argument('--loop', action='store_true')
    screen.add_argument('--interval', type=int, default=3600)
    writer = sub.add_parser('write')
    writer.add_argument('--context', required=True)
    writer.add_argument('--out', required=True)
    writer.add_argument('--budget-usd', type=Decimal, default=Decimal('0'))
    writer.add_argument('--model', choices=sorted(WRITER_MODELS), default=WRITER_MODEL)
    publish = sub.add_parser('publish')
    publish.add_argument('--context', required=True)
    publish.add_argument('--draft', required=True)
    publish.add_argument('--review-note', required=True)
    args = parser.parse_args()
    if args.command == 'screen':
        if args.interval < 600:
            parser.error('interval must be at least 600 seconds')
        while True:
            try:
                report = locked_screening_cycle(budget_usd=args.budget_usd, max_calls=args.max_calls)
            except Exception as exc:
                report = {'status': 'error', 'error': type(exc).__name__}
            print(json.dumps(report, ensure_ascii=False), flush=True)
            if not args.loop:
                return
            time.sleep(args.interval)
    elif args.command == 'write':
        if Path(args.out).exists() or not Path(args.out).parent.is_dir():
            parser.error('output must be a new file in an existing directory')
        context = read_json(args.context)
        result = write_draft(context, budget_usd=args.budget_usd, model=args.model)
        write_json(args.out, result)
        print(json.dumps({'status': 'needs_review', 'out': args.out, 'cost_usd': result['cost_usd']}))
    else:
        context, draft = read_json(args.context), read_json(args.draft)
        item = save_dossier(draft.get('draft', draft), context['articles'], instant(context['as_of']),
                            release='published', review_note=args.review_note)
        print(json.dumps({'id': item, 'status': 'needs_review', 'release': 'published'}))


if __name__ == '__main__':
    main()
