# 🌡️ GeoPulse — Геополитическая температура

Аналитический дашборд геополитической температуры стран СНГ. Собирает статьи из 60+ телеграм-каналов и СМИ, анализирует тональность, строит нарративы и показывает температуру медиа-поля.

## Stack

- **Frontend**: Next.js 16 + TypeScript + Tailwind v4 + shadcn/ui + Recharts + Plotly
- **Backend**: FastAPI (Python) — `/api/v1/...`
- **DB**: PostgreSQL
- **Deploy**: Docker + nginx + Let's Encrypt

## Quick Start

```bash
# Install
npm install

# Dev (needs backend at localhost:8100)
cp .env.example .env.local
npm run dev

# Build
npm run build

# Test
npm test -- --run
```

## Architecture

```
src/
├── app/              # Next.js pages (App Router)
│   ├── page.tsx      # 🌡️ Overview — карта + топ-сюжеты
│   ├── threads/      # 🧵 Threads — сюжеты/нарративы
│   ├── vox/          # 📢 VOX Populi — комментарии TG
│   ├── analytics/    # 📊 Analytics — графики/тренды
│   ├── sources/      # 📡 Sources — медиа-источники
│   ├── country/      # 🏳️ Country — детали по стране
│   └── admin/        # ⚙️ Admin — управление
├── components/       # React-компоненты
├── lib/
│   ├── api-client.ts # Единый HTTP-клиент (SSR + client)
│   ├── api.ts        # API функции + типы
│   ├── api-v2.ts     # Pipeline/Admin API
│   ├── vox-api.ts    # VOX API
│   ├── constants.ts  # Константы (страны, тиры, фазы)
│   └── dashboard-context.tsx  # DashboardProvider (глобальный стейт + URL sync)
└── __tests__/        # Vitest smoke-тесты
```

## Deploy

```bash
# One-command deploy (from local machine)
./deploy.sh

# Or manually:
rsync -avz --exclude='node_modules' --exclude='.next' --exclude='.git' \
  -e "ssh -i ~/.ssh/graf_vlasti" src/ root@YOUR_SERVER_IP:/root/geo-pulse-next/src/
ssh -i ~/.ssh/graf_vlasti root@YOUR_SERVER_IP 'bash /root/geo-pulse-next/deploy.sh'
```

## Key Design Decisions

- `NEXT_PUBLIC_API_URL=""` (empty) → client uses relative paths → nginx proxies `/api/` to backend
- `api-client.ts` → SSR uses `http://127.0.0.1:8100`, client uses relative paths
- `DashboardProvider` → filters stored in URL (`?country=KZ&days=30`)
- `ErrorBoundary` → crashes show retry UI instead of white screen
- `ApiErrorToast` → API errors surface as floating notifications

## Links

- **Live**: https://massaraksh.tech
- **Lab** (prototype): https://lab.massaraksh.tech
- **Backend API**: https://massaraksh.tech/api/v1/countries
