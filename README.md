# 🌡️ GEO PULSE — Quantitative Thermometer of CIS–Russia Relations

> Real-time monitoring of media temperature between 10 post-Soviet states and Russia.  
> 149 sources · 11,000+ articles · 4-year historical depth · AI-powered sentiment analysis

![Dashboard](https://img.shields.io/badge/Dashboard-Live-brightgreen) ![Countries](https://img.shields.io/badge/Countries-10-blue) ![Sources](https://img.shields.io/badge/Sources-149-orange) ![Articles](https://img.shields.io/badge/Articles-11%2C243-red)

---

## What is this?

GEO PULSE is a quantitative research platform that continuously monitors the media landscape of 10 post-Soviet countries, analyzes every article's sentiment toward Russia using LLM, and computes a "temperature" index ranging from **-60°** (hostility) to **+60°** (alliance).

The system collects articles every 30 minutes from 149 media sources across 6 tiers — from official state agencies to independent outlets and Telegram channels — and processes them through a multi-stage AI pipeline.

### Current Temperatures (Feb 2026)

| Country | Temp | Zone | Trend |
|---------|------|------|-------|
| 🇧🇾 Belarus | +24° | Cooperation | ↘ |
| 🇹🇯 Tajikistan | +10° | Neutrality | ↘ |
| 🇹🇲 Turkmenistan | +2° | Neutrality | ↗ |
| 🇰🇬 Kyrgyzstan | 0° | Neutrality | ↘ |
| 🇺🇿 Uzbekistan | -0° | Neutrality | ↘ |
| 🇦🇿 Azerbaijan | -5° | Neutrality | ↗ |
| 🇰🇿 Kazakhstan | -13° | Cooling | ↘ |
| 🇬🇪 Georgia | -17° | Cooling | ↘ |
| 🇦🇲 Armenia | -21° | Cooling | ↗ |
| 🇲🇩 Moldova | -34° | Tension | ↘ |

---

## Key Research Findings

### 📡 Media leads diplomacy by 2–4 weeks
Shifts in media tone consistently precede official diplomatic actions. Kazakh media began writing about "sovereignty" months before each exit from Russian integration frameworks.

### 🪞 Moldova & Georgia — geopolitical mirror antipodes
Their temperature curves are nearly identical (-34° and -17°), creating a "Western-oriented corridor" across the map. Both follow similar patterns driven by EU integration trajectories.

### 🟢 Belarus — the sole green zone
The only country consistently above +20°, Belarus maintains stable cooperation temperature. Even here, micro-fluctuations track real diplomatic friction.

### ⚖️ Central Asia — pragmatic neutrality band
Kazakhstan, Uzbekistan, Kyrgyzstan, Tajikistan, and Turkmenistan cluster tightly around 0° (±15°), reflecting their strategy of balanced multivector foreign policy.

### 🏛️ UN voting correlates — but nonlinearly
Countries voting 70%+ with Russia at the UN (Belarus 81%, Azerbaijan 78%) can still show very different temperatures (+24° vs -5°), proving that formal alignment ≠ media sentiment.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        DATA COLLECTION                          │
│  RSS feeds · Web scraping · Telegram channels · Gov portals     │
│  149 sources × 30-min polling cycle                             │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                      ANALYSIS PIPELINE                          │
│  1. Keyword relevance filter (Russia-related)                   │
│  2. pg_trgm deduplication (fuzzy matching, reprint counting)    │
│  3. LLM sentiment analysis (Claude Sonnet via OpenRouter)       │
│     → sentiment: -3 to +3                                       │
│     → action_level: 1–6 (rhetoric → military)                  │
│     → category: politics/economics/military/culture/energy      │
│  4. Event clustering (related articles → story threads)         │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                    TEMPERATURE ENGINE                            │
│  • 14-day exponential decay window (τ=14d)                      │
│  • Tier-weighted scoring (6 tiers: official → social)           │
│  • Action level multipliers (AL1=×1 ... AL6=×15)               │
│  • Cluster decay 0.2^n (diminishing returns for same story)     │
│  • Scale: -100 to +100, display as -60° to +60°                │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                      PRESENTATION                               │
│  Streamlit Dashboard · FastAPI REST · Weekly AI Digests          │
│  Interactive map · Country deep-dives · Story threads            │
│  UN voting correlation · Trade data overlay                     │
└─────────────────────────────────────────────────────────────────┘
```

---

## Source Tiers

| Tier | Type | Count | Examples |
|------|------|-------|---------|
| 1 | Official / State | 23 | Government portals, state news agencies |
| 2 | Mainstream Media | 41 | Major national news outlets, TV portals |
| 3 | Independent / Alternative | 19 | Independent journalism, investigative |
| 4 | Domestic Opposition | 16 | Opposition-leaning media |
| 5 | Analytics / Expert | 14 | Think tanks, analytical platforms |
| 6 | Social / Telegram | 36 | Telegram channels, social media |

---

## Dashboard Pages

| Page | Description |
|------|-------------|
| 🌡️ **Overview** | Interactive map with temperature labels, country cards, weekly digests |
| 🏳️ **Country** | Deep-dive per country: temperature chart, key events, article feed |
| 🧵 **Story Threads** | Auto-clustered narratives showing how stories develop over time |
| 📊 **Analytics** | UN voting correlation, trade data, objective indicators |
| 📡 **Sources** | Full source catalog with tier, type, country, activity status |
| ℹ️ **About** | Project methodology, origin story, key insights |

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend API | Python 3.11, FastAPI |
| Dashboard | Streamlit (dark theme, mobile responsive) |
| Database | PostgreSQL + TimescaleDB |
| AI/LLM | Claude Sonnet 3.5 via OpenRouter |
| Containerization | Docker Compose (7 services) |
| Map | Plotly (equirectangular projection, custom tooltips) |
| Scheduling | APScheduler (30-min collection, daily temperature recalc) |

---

## Quick Start

```bash
git clone https://github.com/milkmike/GEO_PULSE.git
cd GEO_PULSE
cp .env.example .env
# Set OPENROUTER_API_KEY in .env
docker compose up -d
```

**Services:**
- 📊 Dashboard: `http://localhost:8101`
- 🔗 API: `http://localhost:8100/api/v1/countries`
- 🗄️ Database: PostgreSQL on port `5432`

---

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/countries` | All countries with current temperature, trend, article count |
| `GET /api/v1/countries/{code}` | Detailed country data |
| `GET /api/v1/countries/{code}/events` | Key events for a country |
| `GET /api/v1/countries/{code}/digest` | AI-generated weekly digest |
| `GET /api/v1/un-votes/summary` | UN voting agreement percentages |
| `GET /api/v1/sources` | Source catalog |

---

## Countries Covered

🇰🇿 Kazakhstan · 🇦🇲 Armenia · 🇺🇿 Uzbekistan · 🇰🇬 Kyrgyzstan · 🇹🇯 Tajikistan · 🇹🇲 Turkmenistan · 🇦🇿 Azerbaijan · 🇬🇪 Georgia · 🇲🇩 Moldova · 🇧🇾 Belarus

---

## Temperature Formula

```
T(country, date) = Σ [ sentiment × action_multiplier × tier_weight × exp(-Δt/τ) × cluster_decay ]
                   ─────────────────────────────────────────────────────────────────────────────
                                              Σ weights

Where:
  τ = 14 days (exponential decay half-life)
  action_multiplier: AL1=×1, AL2=×3, AL3=×5, AL4=×8, AL5=×12, AL6=×15
  cluster_decay: 0.2^n (n = article position within same story cluster)
  tier_weight: based on source credibility tier (1–6)
```

---

## License

Private research project. All rights reserved.
