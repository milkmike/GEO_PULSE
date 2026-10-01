# Jev-first news screening

The country workspace previously depended on four detailed text-generation attempts per worker cycle and a strict evidence-review gate. Collected article counts were not counts of reviewed articles. This release adds an independent, cheap discovery layer without presenting its labels as verified facts.

## Behavior and limits

- `article_news_triage` stores exact title/excerpt snapshots, model, version and typed labels, including negative decisions. Input excerpts are at most 2,000 characters. Changed source snapshots invalidate labels.
- `news-triage-v2` screens Russia relevance separately from country involvement, allowing reports about international organizations such as NATO. Exact English/Russian country-name matches only nominate a shorter option set for Jev; they do not establish involvement. Without matches the complete active-country catalog remains available. Publisher country never enters the prompt.
- Jev chooses relevance, topic, event stage, actor type, and up to two countries. Uncertain records remain leads. Country-free leads appear in an explicitly global section. Country identification beyond two countries, abbreviations and morphology remain limitations.
- At most eight articles and 24,000 encoded bytes per request; 12 requests per ordinary cycle, hard maximum 20. The first provider/parse failure stops screening for that cycle. A five-second killable child bounds each request. Existing source fingerprint reservations prevent rebilling the same attempt.
- Current Jev positives receive priority for detailed evidence extraction; current negative labels do not trigger it. Unclassified articles retain a fallback path. Strict evidence verification is unchanged.
- Russian headline translations use the shared exact-title cache and include ungrouped discovery leads. Public GETs never call models. Missing translations are labeled; original text is expandable.
- Coverage separately counts collected local-publisher articles, classified local-publisher articles, pending local articles, and candidates about the selected country from all sources. It does not infer completeness or independent corroboration.

## Budget

The existing campaign cap is unchanged at $3 within the previously authorized $5 recovery envelope. Previous holds are not refunded or reset. Jev uses a $0.01 reservation only after the shared tariff guard: at the maximum accepted input tariff, even two input tokens per encoded byte remain below $0.005 for 24 KB, with zero output/request fees. Translation prompts are capped at 6 KB and 1,000 output tokens with provider-enforced $1/$2 per million and zero request fee; a $0.02 hold covers two tokens per byte plus 2,000 envelope tokens. Other calls retain their $0.10 default. Unknown outcomes retain their original reservation, and an overage halts the campaign.

## Live acceptance probe, 1 October 2026

Six existing public articles were screened inside the same persistent campaign. The first version cost $0.000915768 across two calls, but lost explicit Serbia involvement and treated a Russia–NATO report as unrelated because the prompt required another named country. The revised version processed all six in one request for $0.00036876:

| Article ID | Outcome |
| --- | --- |
| 1725486 | Serbia gas agreement: direct Russia relation, Serbia, business, decision |
| 1723813 | Student List sanctions stance: uncertain Russia relation, Serbia, sanctions, statement; retained as a lead |
| 1722160 | Statement on Serbian elections: direct Russia relation, Serbia, diplomacy, statement |
| 1727142 | Ukraine / South Africa report: direct Russia relation, Ukraine, diplomacy; South Africa not identified from the abbreviated source |
| 1729243 | Russia–NATO / Kaliningrad: direct Russia relation, security, statement; no invented member-country assignment |
| 1730459 | Argentina–Bolivia football: unrelated; excluded from Russia leads |

This is a deliberately small regression sample, not an accuracy benchmark. It demonstrates the reported failure modes and still exposes incomplete entity recall. Probe records are source-checked before persistence; subsequent imports do not repeat paid requests.

References: [TypeSafe atomic questions](https://docs.typesafe.ai/introduction), [OpenRouter classification recipe](https://openrouter.ai/labs/jev/compile). The repository retains reviewed analytical outputs separately from candidate discovery.
