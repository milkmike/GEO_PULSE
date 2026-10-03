"""Private bounded HTTP child; never prints credentials or provider error bodies."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.decision_model import http_request


def main():
    try:
        envelope = json.loads(sys.stdin.buffer.read(80_000))
        result = http_request(envelope['payload'], envelope['api_key'], envelope['timeout'])
    except Exception:
        result = {'status': 'invalid_response'}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
