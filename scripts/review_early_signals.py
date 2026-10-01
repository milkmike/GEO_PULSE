"""Offline early-signal workbench. Prepares/imports JSON; never calls models."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.signal_workbench import prepare_review, import_screening, assemble_context, review_draft


def read(path):
    path = Path(path)
    if path.stat().st_size > 32_000_000:
        raise ValueError("input file exceeds 32 MB")
    return json.loads(path.read_text())


def write(path, value):
    # Never overwrite an analyst's earlier review or a source file.
    with Path(path).open('x', encoding='utf-8') as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prepare = sub.add_parser('prepare')
    prepare.add_argument('--articles', required=True)
    prepare.add_argument('--as-of', required=True)
    prepare.add_argument('--limit', type=int, default=80)
    prepare.add_argument('--out', required=True)
    screen = sub.add_parser('screen')
    screen.add_argument('--prepared', required=True)
    screen.add_argument('--responses', required=True)
    screen.add_argument('--out', required=True)
    context = sub.add_parser('context')
    context.add_argument('--screened', required=True)
    context.add_argument('--article-id', required=True, type=int)
    context.add_argument('--context', help='JSON array of explicitly selected context articles')
    context.add_argument('--out', required=True)
    review = sub.add_parser('review')
    review.add_argument('--context', required=True)
    review.add_argument('--draft', required=True)
    review.add_argument('--out', required=True, help='New HTML file for analyst review')
    review.add_argument('--example', action='store_true', help='Mark all events/sources as fictional')
    demo = sub.add_parser('example', help='Render the clearly fictional editorial acceptance fixture')
    demo.add_argument('--out', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            result = prepare_review(read(args.articles), as_of=args.as_of, limit=args.limit)
        elif args.command == 'screen':
            result = import_screening(read(args.prepared), read(args.responses))
        elif args.command == 'context':
            result = assemble_context(read(args.screened), args.article_id,
                                      read(args.context) if args.context else [])
        else:
            if args.command == 'example':
                fixture = read(Path(__file__).resolve().parents[1] / 'tests/fixtures/early_signal_example.json')
                _, html = review_draft(fixture['context'], fixture['draft'], example=True)
            else:
                _, html = review_draft(read(args.context), read(args.draft), example=args.example)
            with Path(args.out).open('x', encoding='utf-8') as handle:
                handle.write(html)
            print('Offline review saved. No model requests or production changes.')
            return
        write(args.out, result)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, f'Cannot prepare review: {exc}\n')
    print('Offline artifact saved. No model requests or production changes.')


if __name__ == '__main__':
    main()
