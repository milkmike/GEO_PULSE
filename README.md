# 🌡️ GEO PULSE — Frontend

> Next.js dashboard для платформы мониторинга медийной температуры СНГ–Россия.

**🔗 Live:** [http://YOUR_SERVER_IP:3333](http://YOUR_SERVER_IP:3333)  
**Backend repo:** [GEO_PULSE](https://github.com/milkmike/GEO_PULSE) (ветка `main`)  
**Эта ветка:** `nextjs`

---

## Стек

| Технология | Версия |
|-----------|--------|
| Next.js | 16.1.6 |
| React | 19.2.3 |
| TypeScript | 5.x |
| Tailwind CSS | 4.x |
| shadcn/ui | New York, Zinc |
| Recharts | 3.7 |
| Plotly.js | 3.3 |
| Radix UI | 1.4 |

---

## Страницы

| Маршрут | Описание | Ключевые виджеты |
|---------|----------|------------------|
| `/` | 🌡️ Обзор | Headline дня, карта Plotly, карточки стран со спарклайнами, StatsCards, график температуры |
| `/country/[code]` | 🏳️ Страна | Температура с компонентами, нарративный расклад (тиры), AI-дайджест, сюжеты, события, UN/Trade, аудиторный сплит |
| `/threads` | 🧵 Сюжеты | Список сюжетных нитей с arc_phase, importance score, связанные сюжеты |
| `/analytics` | 📊 Аналитика | Покрытие источников, расхождение тиров, UN-голосования, торговля, корреляция, GeoPulse |
| `/sources` | 📡 Источники | Каталог 191 источника с тирами и статусом |
| `/about` | ℹ️ О проекте | Методология, находки, формула |
| `/admin` | ⚙️ Админка | Пайплайн, управление источниками, API costs, резонанс |

---

## Компоненты

### Основные виджеты

| Компонент | Описание |
|-----------|----------|
| `Headline` | Заголовок дня — самая горячая/холодная/изменившаяся страна |
| `CountryCard` | Карточка страны со спарклайном температуры и полоской дивергенции |
| `TemperatureChart` | График температуры с динамическим цветом (синий→жёлтый→красный), toggle компонентов, маркеры аномалий |
| `NarrativeXray` | Простой нарративный расклад по тирам |
| `NarrativeXrayExpanded` | Расширенный анализ: спектр, динамика, попарное сравнение, темы, heatmap |
| `AudienceSplit` | Аудиторный сплит — как двуязычные СМИ пишут для разных аудиторий |
| `GeoPulse` | Геополитический пульс — горячие точки, лента событий, индекс активности |
| `GeoMap` / `PlotlyMap` | Интерактивная карта с температурными метками |
| `StatsCards` | 4 метрики: статьи, источники, анализ, температура |
| `TradeChart` | Торговые данные (экспорт/импорт) |
| `UNVotesChart` | Голосования в ООН (совпадение с Россией) |

### UI / Утилиты

| Компонент | Описание |
|-----------|----------|
| `SectionHeader` | Заголовок секции с описанием и info-попапом |
| `InfoPopover` | Всплывающая подсказка с glossary-контентом |
| `PeriodSelector` | Переключатель периода (неделя / месяц / квартал / год) |
| `ErrorBoundary` | Обёртка для устойчивости при ошибках |

### Glossary

21 запись в `src/lib/glossary.tsx`: temperature, sentiment, divergence, tiers, coverage, unVotes, trade, correlation, threads, sources, actionLevel, arcPhase, importanceScore, headline, audienceSplit, temperatureComponents, anomalyScore, sparkline, geoPulse, statsCards, geoMap.

---

## Структура проекта

```
src/
├── app/
│   ├── layout.tsx              # Навигация (7 пунктов), тёмная тема
│   ├── page.tsx                # Главная — обзор
│   ├── country/
│   │   ├── page.tsx            # Выбор страны
│   │   └── [code]/page.tsx     # Детальная страница страны
│   ├── threads/
│   │   ├── page.tsx            # Список сюжетов
│   │   └── [id]/page.tsx       # Детали сюжета
│   ├── analytics/page.tsx      # Аналитика
│   ├── sources/page.tsx        # Источники
│   ├── about/page.tsx          # О проекте
│   └── admin/                  # Админка (4 подстраницы)
├── components/
│   ├── AudienceSplit.tsx        # 362 строки
│   ├── CountryCard.tsx          # 113 строк
│   ├── ErrorBoundary.tsx
│   ├── GeoMap.tsx               # 600 строк
│   ├── GeoPulse.tsx             # 507 строк
│   ├── Headline.tsx             # 93 строки
│   ├── InfoPopover.tsx
│   ├── NarrativeXray.tsx        # 288 строк
│   ├── NarrativeXrayExpanded.tsx # 649 строк
│   ├── PeriodSelector.tsx
│   ├── PlotlyMap.tsx
│   ├── SectionHeader.tsx
│   ├── StatsCards.tsx
│   ├── TemperatureChart.tsx     # 195 строк
│   ├── TradeChart.tsx
│   ├── UNVotesChart.tsx
│   └── ui/                     # shadcn/ui примитивы
├── lib/
│   ├── api.ts                  # API_URL + все fetch-функции + типы
│   ├── api-v2.ts               # Функции для admin
│   ├── glossary.tsx            # 21 glossary-запись
│   └── utils.ts                # cn() helper
└── types/
    └── plotly.d.ts             # Типы для Plotly.js
```

**Всего**: ~10,400 строк TypeScript в 50 файлах.

---

## API подключение

Все API-вызовы через единый `API_URL` из `src/lib/api.ts`:

```typescript
export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://YOUR_SERVER_IP:8100";
```

Backend: **40 REST endpoints** — см. [основной репозиторий](https://github.com/milkmike/GEO_PULSE).

---

## Деплой

### Docker (production)

```bash
docker build -t geo-pulse-next .
docker run -d --name geo-pulse-next -p 3333:3000 --restart unless-stopped geo-pulse-next
```

Multi-stage Dockerfile: сборка внутри контейнера → standalone output → минимальный Alpine образ.

### Локальная разработка

```bash
npm install
npm run dev
# http://localhost:3000
```

Для работы с API нужен запущенный backend на `http://YOUR_SERVER_IP:8100` или локально.

---

## Лицензия

Частный исследовательский проект. Все права защищены.

© 2026 GEO PULSE
