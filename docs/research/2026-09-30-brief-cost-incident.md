# Brief generation: billed empty responses

Production inspection on 2026-09-30 found 64 HTTP-successful brief completions
but only 8 saved briefs over six hours. Qwen accounted for 59 of those calls
and $0.214636 in recorded provider charges. Repeated null content crashed
`.strip()` after usage was recorded as successful; fallback never ran.

The Qwen 3.7 Plus catalog reports optional reasoning enabled by default.
Brief calls did not disable it. We cannot retrospectively prove reasoning
exhaustion for individual old calls because finish reasons and reasoning token
counts were not stored. Empty content and the resulting exception are proven.

Fix: disable optional reasoning for the three explicitly supported brief/analyzer
models; record empty output as a paid error, fall back, and suppress the failing
model for two hours within the worker/script. Duplicate model IDs are tried once.
Provider cost is also forwarded for embedding requests; OpenRouter embedding
fallback estimates now use the verified $0.02/M input rate.

Validation: 12 regression tests failed before the fix; 119 targeted tests and all
1179 backend tests passed afterward. Independent review found no actionable issue.

A bounded synthetic Qwen probe through the brief service's network configuration
returned 1117 characters, finish_reason=stop, 246 completion tokens, zero reasoning
tokens, and $0.00034752 provider cost. An earlier probe from the API container
received HTTP 403 (its network environment differs); no paid response was reported.
Both attempts retain $0.10 conservative reservations. Explicit task reservations
now total $1.82 of the user's $5 limit; known actual task charges total $0.0062820455.
These figures exclude normal background production traffic.

Autogeneration was stopped during the incident. Deployment/runtime verification
must be recorded separately; a successful synthetic response is not a saved
production brief or proof of editorial quality.

Sources: https://openrouter.ai/api/v1/models,
https://openrouter.ai/api/v1/embeddings/models,
https://openrouter.ai/docs/guides/best-practices/reasoning-tokens.
