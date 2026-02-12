# 🚀 Deployment Guide — GeoPulse на HTTPS

## Инфраструктура

```
[Браузер] → HTTPS → [Nginx :443] → [Next.js :3333] → SSR fetch → [FastAPI :8100]
                          ↓
                    /api/* proxy → [FastAPI :8100]
```

- **VPS**: `YOUR_SERVER_IP` (Ubuntu, Docker)
- **Домен**: `massaraksh.tech` (reg.ru, A-record → VPS)
- **SSL**: Let's Encrypt (certbot, auto-renewal)
- **Nginx**: reverse proxy, `/api/` → backend
- **Docker**: `geo-pulse-next:massaraksh` на порту 3333→3000

---

## 🐛 Ошибки HTTPS-деплоя (февраль 2026)

### Ошибка 1: Mixed Content Blocking
**Симптом**: Все данные пропали после перехода на HTTPS. Дашборд пустой.
**Причина**: Frontend (HTTPS) вызывал API по `http://YOUR_SERVER_IP:8100` — браузеры блокируют HTTP-запросы с HTTPS-страниц (Mixed Content).
**Решение**: Добавить nginx location `/api/` → `proxy_pass http://127.0.0.1:8100/api/` — API доступен через тот же HTTPS-домен.

### Ошибка 2: NEXT_PUBLIC_* — build-time, не runtime!
**Симптом**: `--build-arg API_URL=""` не помогло — старый URL остался в бандле.
**Причина**: `NEXT_PUBLIC_API_URL` в Dockerfile был захардкожен как `ENV` (не `ARG`). Next.js инлайнит `NEXT_PUBLIC_*` переменные **при сборке** в JS-бандлы. Изменить их после билда невозможно.
**Решение**: 
```dockerfile
ARG API_URL=""
ENV NEXT_PUBLIC_API_URL=$API_URL
RUN npm run build
```

### Ошибка 3: `||` vs `??` для fallback URL
**Симптом**: Даже с `ARG API_URL=""` в бандле появлялся `http://YOUR_SERVER_IP:8100`.
**Причина**: `process.env.NEXT_PUBLIC_API_URL || "http://..."` — пустая строка `""` falsy в JS, поэтому `||` берёт fallback.
**Решение**: Использовать `??` (nullish coalescing) вместо `||`:
```typescript
// ❌ Плохо — пустая строка считается falsy
export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://YOUR_SERVER_IP:8100";

// ✅ Хорошо — пустая строка проходит, только null/undefined берут fallback
export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";
```

### Ошибка 4: Дублирование API_URL в компонентах
**Симптом**: После фикса api.ts и api-v2.ts — Сюжеты (threads) всё ещё 0 данных.
**Причина**: `threads/page.tsx` имел **собственное** определение `const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://..."` вместо импорта из `api.ts`.
**Решение**: Убрать все локальные определения `API_URL`, импортировать только из `@/lib/api`.

**Правило**: `API_URL` должен определяться **в одном месте** (`api.ts`). Все остальные файлы — только `import { API_URL } from "@/lib/api"`.

### Ошибка 5: `new URL()` крашит с относительным путём
**Симптом**: VOX страница — 0 данных. SSR ошибка.
**Причина**: `new URL("/api/v1/vox")` без base URL бросает `TypeError` в Node.js. В браузере тоже не работает без origin.
**Решение**: Двойная стратегия для клиента и сервера:
```typescript
async function apiFetch<T>(path: string, params?: Record<string, string | number>): Promise<T> {
  // Для new URL() всегда нужен absolute base
  const base = API_URL.startsWith("http") ? API_URL : "http://127.0.0.1:8100";
  const url = new URL(`${base}${path}`);
  // ...build search params on url...
  
  // Client: relative path (через nginx proxy)
  // Server (SSR): absolute URL (напрямую к backend)
  const fetchUrl = typeof window !== "undefined" 
    ? `${API_URL}${path}${url.search}` 
    : url.toString();
  
  return fetch(fetchUrl, { next: { revalidate: 300 } });
}
```

### Ошибка 6: VPS исходники не синхронизированы с Git
**Симптом**: Фикс api.ts применился, но другие файлы на VPS остались старые.
**Причина**: На VPS нет git clone — файлы копировались вручную, `scp` обновлял только отдельные файлы.
**Решение**: Использовать `rsync` для полной синхронизации:
```bash
rsync -avz --exclude='node_modules' --exclude='.next' --exclude='.git' \
  -e "ssh -i ~/.ssh/graf_vlasti" \
  ./src/ root@YOUR_SERVER_IP:/root/geo-pulse-next/src/
```
**TODO**: Настроить git clone на VPS или CI/CD pipeline.

---

## 📋 Чеклист деплоя

```
□ API_URL определён ТОЛЬКО в src/lib/api.ts
□ Все файлы используют import { API_URL } from "@/lib/api"
□ Fallback через ?? (не ||)
□ Dockerfile: ARG API_URL="" → ENV NEXT_PUBLIC_API_URL=$API_URL
□ Nginx: location /api/ → proxy_pass backend
□ rsync ВЕСЬ src/ на VPS перед билдом
□ docker build --no-cache --build-arg API_URL=""
□ Проверить: grep -r "212.67" в бандле = 0 совпадений
□ Проверить: curl https://domain/api/v1/stats = 200
□ Проверить: открыть в браузере ВСЕ страницы
```

---

## 🔧 Быстрый деплой (copy-paste)

```bash
# 1. Sync sources
rsync -avz --exclude='node_modules' --exclude='.next' --exclude='.git' \
  -e "ssh -i ~/.ssh/graf_vlasti" \
  ./src/ root@YOUR_SERVER_IP:/root/geo-pulse-next/src/

# 2. Build
ssh -i ~/.ssh/graf_vlasti root@YOUR_SERVER_IP \
  'cd /root/geo-pulse-next && docker build --no-cache --build-arg API_URL="" -t geo-pulse-next:massaraksh .'

# 3. Deploy
ssh -i ~/.ssh/graf_vlasti root@YOUR_SERVER_IP \
  'docker stop geo-pulse-next; docker rm geo-pulse-next; docker run -d --name geo-pulse-next --restart unless-stopped -p 3333:3000 geo-pulse-next:massaraksh'

# 4. Verify
curl -s https://massaraksh.tech/api/v1/stats | python3 -m json.tool
```

---

## Nginx конфиг (`/etc/nginx/sites-available/massaraksh`)

```nginx
server {
    server_name massaraksh.tech;

    location /api/ {
        proxy_pass http://127.0.0.1:8100/api/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
        proxy_pass http://127.0.0.1:3333;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # SSL managed by certbot
    listen 443 ssl;
    ssl_certificate /etc/letsencrypt/live/massaraksh.tech/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/massaraksh.tech/privkey.pem;
}
```
