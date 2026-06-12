# WorldMonitor (koala73/worldmonitor) — каталог информационных слоёв и проекция на GEO PULSE

Дата исследования: 2026-06-12. Метод: GitHub API (дерево 4011 файлов), сырые конфиги
(`src/config/feeds.ts`, `map-layer-definitions.ts`, `pipelines.ts`, `ai-datacenters.ts`),
`docs/data-sources.mdx`, `docs/finance-data.mdx`, `data/telegram-channels.json`.

## 0. Общая картина

**Что это:** real-time дашборд глобальной обстановки. TypeScript/Vite (vanilla, без React),
карта globe.gl + deck.gl/MapLibre, бэкенд — Vercel Edge Functions (60+) + Railway relay +
Redis (Upstash), API через Protocol Buffers (34 сервиса). 500+ RSS-фидов в 15+ категориях,
**56 слоёв карты**, ~65 внешних провайдеров, 6 вариантов сайта из одной кодовой базы
(world/tech/finance/commodity/happy/energy). AI-синтез брифов через Ollama/Groq/OpenRouter.

**Ключевая идея, переносимая в GEO PULSE:** WM — это набор независимых типизированных слоёв
(feed-слои, API-слои, статические curated-датасеты), которые скармливаются в общий signal
aggregator → корреляционный движок → composite-индексы (CII) → AI-брифы. У GEO PULSE та же
архитектура уже есть (tiers → GDELT → LLM sentiment → RRI → signals → briefs); нужно лишь
добавлять слои и преломлять каждый через ось «страна ↔ Россия».

## 1. Разведка / OSINT-фиды

Три подсистемы: (а) RSS `INTEL_SOURCES` + категория thinktanks, (б) Telegram OSINT,
(в) GDELT-топики.

### а) RSS intel-источники (`src/config/feeds.ts`)
- **Defense/military (tier 1):** Defense One, The War Zone, Defense News, Janes, Military
  Times, Task & Purpose, USNI News, gCaptain, **Oryx OSINT** (подсчёт потерь техники),
  UK MOD, Breaking Defense.
- **Think tanks IR (tier 2):** Foreign Policy, Foreign Affairs, Atlantic Council, Chatham
  House, ECFR, Middle East Institute, EU ISS.
- **Research (tier 3):** RAND, Brookings, Carnegie Endowment, CSIS, FAS, NTI, RUSI, Wilson
  Center, GMF, Stimson, CNAS, Lowy Institute, **Jamestown Foundation** (Eurasia/Russia!),
  FPRI, AEI, War on the Rocks, Responsible Statecraft.
- **Nuclear/arms control:** Arms Control Association, Bulletin of the Atomic Scientists, IAEA.
- **OSINT/расследования:** **Bellingcat**, DFRLab, **OCCRP**, Lighthouse Reports, The Sentry,
  GITOC, VSquare, Correctiv, InSight Crime, CrisisWatch (ICG).
- Приём: половина фидов — **Google News RSS с `site:` фильтром**
  (`news.google.com/rss/search?q=site:csis.org+when:7d`) — обход отсутствующих RSS.
  Все источники размечены `SourceType` и **propaganda-risk** (`low/medium/high`,
  поле `stateAffiliated` — TASS/RT помечены явно).

### б) Telegram OSINT (`data/telegram-channels.json`, 56 каналов)
Tier 1 (Vahid Online, IDF Official, Rocket Alert), Tier 2 (Aurora Intel, Clash Report,
DeepState, LiveUAMap, OSINTdefender, BNO News, IRGC Official, Tasnim...), Tier 3 (Bellingcat,
NEXTA, Spectator Index, DD Geopolitics...). Метаданные: topic/region/tier. State-affiliated
и «воюющие стороны» включены осознанно — «raw OSINT leads, not endorsed truth».
GramJS MTProto на Railway, 60-сек цикл, дедуп, буфер 200.

### в) GDELT intel-топики (`src/services/gdelt-intel.ts`)
Преднастроенные запросы: military, cyber, nuclear, sanctions, intelligence, maritime
security + social-velocity (Reddit).

**Проекция на GEO PULSE:** tier «analytics/intel» в RSS-иерархии с российским фильтром:
Jamestown EDM, Carnegie Politika, ISW, RUSI, ECFR, CSIS Russia, Bellingcat, OCCRP +
state-стороны с пометкой propaganda-risk. Google News `site:`-RSS закрывает «бесфидовые»
think tanks бесплатно. Лента «Что пишут аналитические центры о России» с фильтром по стране.

## 2. Экономика

Источники (бесплатные): **FRED** (CPI, GDP, ставки, фрахт), **BLS**, **BIS** (policy rates,
REER, credit-to-GDP), **World Bank**, **IMF**, **EIA**, US Treasury MTS, FAO Food Price
Index, Big Mac Index, USAspending.gov. Уникальное: собственный скрейпер продуктовых корзин
из 26 ритейлеров в 10 странах → индекс продуктовой инфляции.
Визуализация: tile-панели (macro-tiles, yield-curve, national-debt), слой карты, панель
Economic Calendar, корреляционная панель «Economic Warfare» (санкции × рынки).

**Проекция:** «экономическая экспозиция страны к России»: доля РФ во внешней торговле
(Comtrade/WB), динамика валют (BIS REER), блок «экономика отношений» в досье. FAO Food
Price + цены удобрений/зерна (РФ — ключевой экспортёр) — дешёвый русскоцентричный индикатор.

## 3. Финансы / рынки («finance radar»)

Гео-слои (curated): **29 бирж** с капитализацией и часами, 19 финцентров (GFCI),
14 ЦБ/наднациональных, 10 commodity-хабов, Gulf FDI (64 инвестиции).

**Market Radar — 7-сигнальный композит** (все источники бесплатны: Yahoo Finance unofficial,
mempool.space, alternative.me):

| Сигнал | Расчёт | Bullish если |
|---|---|---|
| Liquidity | JPY/USD 30d ROC | ROC > −2% |
| Flow Structure | BTC 5d vs QQQ 5d | разрыв < 5% |
| Macro Regime | QQQ 20d vs XLP 20d | QQQ опережает |
| Technical Trend | BTC vs SMA50 + VWAP | выше обоих |
| Hash Rate | 30d изменение | рост > 3% |
| Mining Cost | цена vs implied cost | прибыльно |
| Fear & Greed | alternative.me | > 50 |

Вердикт BUY/CASH: ≥57% известных сигналов bullish; unknown исключаются из знаменателя.

**Проекция:** **«Russia Financial Isolation Radar»** — 5–7 сигналов по той же схеме:
курс RUB (официальный vs кросс), MOEX/RTS, дисконт Urals–Brent, CDS/евробонды, объёмы
юань/рубль, SWIFT-отключения, потоки EM-фондов. Каждый сигнал — ряд в TimescaleDB с порогом;
композит рядом с RRI. Слой «финансовые центры» → «юрисдикции российских капиталов»
(Дубай, Гонконг, Стамбул, Алматы).

## 4. Торговля

- **WTO API** (бесплатный ключ): ограничения, тарифы, потоки YoY, SPS/TBT-барьеры.
- **UN Comtrade** (бесплатно): двусторонние потоки.
- **US Treasury MTS**: помесячные таможенные сборы — реал-тайм прокси тарифов.
- **19 торговых маршрутов** (curated) + **13 чокпоинтов** (вкл. **Керченский пролив**,
  Босфор): IMF PortWatch (бесплатно) + AISStream + навигационные предупреждения,
  disruption score 0–100.
- Supply Chain: FRED-фрахт + HHI-концентрация критических минералов.
- **62 стратегических порта** (вкл. Владивосток).

**Проекция:** вкладка «Торговля с Россией» в досье (Comtrade RU↔X напрямую); рост реэкспорта
через KZ/AM/KG/UAE виден прямо в Comtrade — готовый сигнал «параллельный импорт» для signals
engine. Чокпоинты РФ: Босфор, Керчь, Датские проливы + Севморпуть как curated-маршруты.

## 5. Энергетика

- **EIA API**: WTI/Brent, добыча, запасы, газохранилища, генерация.
- **Пайплайны** (curated из Global Energy Monitor + EIA): Druzhba, Nord Stream, TurkStream,
  Power of Siberia.
- **IEA Energy Crisis Policy Tracker** (~200 политик, ~60 стран).
- Hormuz Tracker + Live Tankers (AIS), **shadow fleet** в фид-запросах.
- RSS: OilPrice, Rigzone, EIA, Google-News «OPEC & Crude», «LNG», «Pipelines & Chokepoints»,
  «Energy Sanctions (price cap/embargo)», IEA, Platts.
- Слои: pipelines, storageFacilities, fuelShortages, liveTankers, nuclear, NASA FIRMS
  (горящие НПЗ видны как термоточки).

**Проекция (самый «русский» слой):** (1) цены — Brent vs **Urals-дисконт**, TTF, price cap;
(2) по странам — импорт российской энергии: **CREA** (Centre for Research on Energy and
Clean Air) публикует готовый счётчик импорта российских ископаемых по странам — идеально
для нашей оси; (3) curated-карта экспортных труб РФ со статусами; (4) shadow fleet события
в signals engine. Энергозависимость — главный материальный субстрат отношений с РФ.

## 6. AI-сектор

- **AI-датацентры**: датасет Epoch AI GPU Clusters (CC-BY) — 313 кластеров >1000 GPU
  с owner/chipType/powerMW.
- **AI-лаборатории** (curated): Anthropic, OpenAI, DeepMind... с focusAreas и координатами.
- **AI-регуляции** (curated): EU AI Act с дедлайнами, указы США, китайские правила.
- RSS: ArXiv cs.AI/LG, VentureBeat, MIT Tech Review, SemiAnalysis, Tom's Hardware,
  Stanford HAI, DigiChina; tile ai-tokens, tech-hub-index.

**Проекция:** «технологическая развязка с Россией»: (а) лента chip export controls /
параллельный импорт полупроводников; (б) curated: российские AI/tech-активы (Yandex/Nebius,
Sber AI, Christofari) и санкционный статус; (в) по странам — хабы реэкспорта (Comtrade
HS 8542 в РФ через третьи страны — измеримо); (г) «цифровой суверенитет» регуляции.
Второстепенный, но дешёвый слой: 2 curated-JSON + 3 RSS-запроса.

## 7. Прочие слои (кратко)

| Слой | Источники | Для GEO PULSE |
|---|---|---|
| Конфликты | ACLED (OAuth, 30d) + UCDP GED (бесплатно, годовой лаг) | ACLED по ЧВК/РФ (Сахель) — слой «военное присутствие РФ» |
| Протесты | ACLED + GDELT, дедуп 0.1°, режим-зависимый скоринг | приём: вес событий зависит от типа режима |
| Санкции | OFAC SDN + Consolidated (diff новых) + OpenSanctions | «санкционное давление на РФ» по программам и странам |
| Prediction markets | Polymarket (+Kalshi), гео-теггинг regex | «вероятность событий вокруг РФ» (перемирие, санкции) |
| Море/шиппинг | AISStream, IMF PortWatch, dark ships (AIS-гэпы >60 мин) | dark ships = shadow fleet detection |
| Кибер | Feodo/URLhaus, C2IntelFeeds, OTX, Ransomware.live + apt-groups | APT-атрибуция → «кибер-активность РФ против страны X» |
| Авиация/EW | ADS-B, **gpsjam.org** (H3-гексы: Baltic, Black Sea!) | GPS-джамминг у границ РФ — бесплатный сигнал |
| Военные | 226 баз (curated), CelesTrak TLE, USNI Fleet Tracker | |
| CII (аналог RRI) | composite по 31 стране, choropleth | перенять флаг COVERAGE_PARTIAL при выпадении источника |
| Signals | correlation engine (9 модулей), geographic convergence | паттерн «N независимых источников в одной точке» |

## 8. Архитектурные приёмы (брать независимо от слоёв)

1. **Google News RSS как универсальный адаптер** (`site:domain+when:Nd`).
2. **Circuit breakers с persist-кэшем на каждый внешний API** — независимая деградация.
3. **Data freshness tracker** — staleness каждого фида показан явно.
4. **propaganda-risk + stateAffiliated теги** — RT/TASS включены, но размечены.
5. **Curated-статика как полноценный слой** — версионируемые JSON/TS-файлы, без API.
6. **Layer explanations** — у каждого слоя задекларированы source/freshness/confidence/
   limitations — паттерн методологической честности для /about.

**Ключевые файлы WM:** `src/config/feeds.ts`, `data/telegram-channels.json`,
`src/config/map-layer-definitions.ts`, `docs/data-sources.mdx`, `docs/finance-data.mdx`,
`src/config/pipelines.ts`, `src/config/ai-datacenters.ts`, `docs/panels/*.mdx`.
Лицензия AGPL-3.0: код не копируем без открытия исходников (наш репо и так AGPL),
конфиги источников — факты, архитектурные идеи лицензией не защищены.
