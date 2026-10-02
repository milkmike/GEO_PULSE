"""Small, sequential recovery/benchmark runs with conservative prepaid reservations.

Each attempt consumes $0.10 of the local budget, even on timeout or cheap success.
32KB input + <=1000 output tokens at provider-enforced $1/$2 per million and zero
per-request fee stays below that reservation: even two tokens per UTF-8 byte
plus 2000 envelope tokens cost <=$0.068, below the $0.10 reservation.
No retries, provider fallback, tools, media, or normal worker use.
"""
from decimal import Decimal, InvalidOperation
import time

import httpx

from src.llm import LLMError, OPENROUTER_URL


class BudgetedChat:
    RESERVATION = Decimal("0.10")
    MODELS = {"deepseek/deepseek-v4-flash", "qwen/qwen3.6-flash"}

    def __init__(self, api_key: str, max_usd: Decimal,
                 model: str = "deepseek/deepseek-v4-flash"):
        if not max_usd.is_finite() or not Decimal("0") < max_usd <= Decimal("5"):
            raise ValueError("Budget must be positive and at most $5")
        if model not in self.MODELS:
            raise ValueError("Model is not approved for bounded recovery")
        self.api_key, self.max_usd, self.model = api_key, max_usd, model
        self.reserved = Decimal("0")
        self.actual_cost = Decimal("0")
        self.requests = []

    def chat(self, prompt: str, max_tokens: int = 350,
             temperature=None, script="recovery") -> tuple[str, str]:
        if len(prompt.encode("utf-8")) > 32000 or not 1 <= max_tokens <= 1000:
            raise ValueError("Request exceeds recovery size limits")
        if self.reserved + self.RESERVATION > self.max_usd:
            raise LLMError("Recovery budget exhausted")
        self.reserved += self.RESERVATION
        record = {"model": self.model, "status": "unknown", "reserved_usd": "0.10"}
        self.requests.append(record)
        started = time.monotonic()
        payload = {
            "model": self.model, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens, "temperature": 0 if temperature is None else temperature,
            "reasoning": {"enabled": False},
            "provider": {"max_price": {"prompt": 1, "completion": 2, "request": 0},
                         "allow_fallbacks": False, "require_parameters": True},
        }
        phase = "transport"
        try:
            response = httpx.post(OPENROUTER_URL,
                headers={"Authorization": f"Bearer {self.api_key}"}, json=payload, timeout=45)
            record["http_status"] = response.status_code
            phase = "http_status"
            response.raise_for_status()
            phase = "provider_json"
            data = response.json()
            phase = "provider_envelope"
            choice = data["choices"][0]
            record["finish_reason"] = choice.get("finish_reason")
            record["usage"] = data.get("usage", {})
            phase = "provider_usage"
            cost = Decimal(str(data.get("usage", {}).get("cost")))
            if not cost.is_finite() or cost < 0:
                raise ValueError("Invalid provider cost")
            self.actual_cost += cost
            record.update(status="ok", cost_usd=str(cost))
            if cost > self.RESERVATION:
                self.reserved = self.max_usd
                record["error_reason"] = "cost_over_reservation"
                raise ValueError("Provider exceeded reserved price; stop")
            phase = "provider_content"
            content = choice["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("Empty response")
            return content.strip(), self.model
        except (httpx.HTTPError, KeyError, IndexError, AttributeError, TypeError, ValueError, InvalidOperation) as exc:
            record["status"] = "failed"
            record.setdefault("error_reason", {
                "transport": "transport_error", "http_status": "provider_http_error",
                "provider_json": "invalid_provider_json",
                "provider_envelope": "invalid_provider_envelope",
                "provider_usage": "invalid_provider_usage",
                "provider_content": "invalid_provider_content",
            }[phase])
            raise LLMError(f"Bounded request failed: {type(exc).__name__}") from None
        finally:
            record["duration_ms"] = round((time.monotonic() - started) * 1000)
