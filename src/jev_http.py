"""Single Jev HTTP request in a killable child process (including DNS)."""
from __future__ import annotations

import json
import sys

import httpx

ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
MAX_RESPONSE_BYTES = 64_000


def send(payload, api_key, timeout):
    try:
        with httpx.Client(timeout=timeout) as client:
            with client.stream(
                "POST", ENDPOINT,
                content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json", "X-Title": "GEO PULSE Jev shadow"},
            ) as response:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        return {"status": "invalid_response"}
                return {"status": "ok", "data": json.loads(body)}
    except httpx.HTTPStatusError as exc:
        return {"status": "provider_error", "http_status": exc.response.status_code}
    except httpx.TimeoutException:
        return {"status": "timeout"}
    except httpx.HTTPError:
        return {"status": "transport_error"}
    except Exception:
        # Never forward response content, credentials or exception messages.
        return {"status": "invalid_response"}


if __name__ == "__main__":
    try:
        request = json.load(sys.stdin)
        result = send(request["payload"], request["api_key"], request["timeout"])
    except Exception:
        result = {"status": "invalid_response"}
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
