# 🏗️ Архитектурный обзор GeoPulse — Честная оценка

## Общая оценка: 6.5/10 — Крепкий средний уровень

Не junior-поделка, но и не production-ready enterprise. Это **хороший прототип аналитического продукта**, который работает и показывает данные, но имеет архитектурные долги, типичные для быстрой разработки.

---

## ✅ Что сделано хорошо (сильные стороны)

### Backend (FastAPI) — 7.5/10
- **30+ endpoints** с чёткой REST-структурой (`/countries`, `/threads`, `/vox`, `/analytics`)
- **Многослойный сбор данных**: RSS, Web scraping (Firecrawl), Telegram (Telethon), комментарии
- **AI-анализ каждой статьи и комментария**: sentiment, emotions, topics, stance, bot detection
- **Thread detection**: автоматическая кластеризация статей в сюжетные линии
- **Temperature model**: собственная метрика геополитической температуры
- **10 стран, 220+ источников, 25K+ статей** — серьёзный объём данных
- Docker Compose с разделением сервисов (api, scheduler, analyzer, collector)

### Frontend (Next.js) — 6/10
- **8 полноценных страниц** с rich UI: карта, графики, таблицы, фильтры
- **Next.js 16** + TypeScript + Tailwind v4 + shadcn/ui — современный стек
- **Recharts + Plotly** для визуализации
- **VOX insights**: эмоции, спектр настроений, облако тем, языковая карта
- Тёмная тема, responsive навигация

### Продуктовая ценность — 8/10
- **Реальная аналитическая задача** — мониторинг геополитики в 10 странах СНГ
- **Данные собираются и обновляются** автоматически каждые 30 минут
- **AI-анализ** sentiment + emotions — не просто агрегация, а понимание контента
- **VOX POPULI** — уникальная фича анализа народного мнения из комментариев

---

## ⚠️ Архитектурные проблемы (слабые стороны)

### 1. Нет глобального состояния — КРИТИЧНО
```
Текущее: Каждая страница — изолированный остров. 
         Свой useState, свой fetch, свой loading.
         Клик на карте НЕ фильтрует другие виджеты.
         
Должно быть: React Context / Zustand / URL State
              Клик → глобальный фильтр → все виджеты реагируют
```
**Уровень проблемы**: Это отличает "набор виджетов" от "аналитического дашборда". У Palantir Workshop, Tableau, Metabase — всё провязано. Тут — нет.

### 2. URL не отражает состояние — ВАЖНО
```
Текущее: https://massaraksh.tech/threads — всегда одинаковый URL
         Нельзя скопировать ссылку на "сюжеты Казахстана в эскалации"
         
Должно быть: /threads?country=KZ&status=escalating
```
**Почему важно**: Шареability, закладки, навигация назад, deep links для отчётов.

### 3. API_URL и конфигурация — размазаны
```
Текущее: API_URL определялся в 3+ файлах (api.ts, api-v2.ts, threads/page.tsx)
         Два разных apiFetch (один с new URL, другой с template string)
         Нет единого HTTP-клиента
         
Должно быть: Один API-клиент с interceptors, retry, error handling
```

### 4. Нет error boundaries и loading states
```
Текущее: try/catch → setData([]) — ошибка молча глотается
         Пользователь видит "0 сюжетов" и не понимает почему
         
Должно быть: Error boundary с retry, toast уведомления,
             skeleton loaders, offline indicator
```

### 5. Дублирование констант
```
COUNTRY_FLAGS, TIER_COLORS, COUNTRY_NAMES — определены в 5+ файлах.
Нет единого constants.ts.
```

### 6. Нет тестов
```
0 unit тестов, 0 integration тестов, 0 e2e тестов.
Любое изменение — ручная проверка всех страниц.
```

### 7. Нет CI/CD
```
Деплой: rsync + docker build вручную.
Нет GitHub Actions, нет автоматических проверок.
```

---

## 📊 Сравнение с уровнями

| Критерий | Junior (3/10) | Текущий GeoPulse (6.5/10) | Production (8/10) | Enterprise (9.5/10) |
|----------|--------------|---------------------------|-------------------|---------------------|
| **Стек** | CRA + JS | Next.js 16 + TS + Tailwind ✅ | То же + тесты | + monorepo + design system |
| **API** | fetch в компонентах | 3 API-слоя, typed responses ⚠️ | Один HTTP-клиент + interceptors | + GraphQL/tRPC + codegen |
| **Стейт** | useState везде ❌ | useState везде ❌ | Context/Zustand + URL sync | + real-time subscriptions |
| **Ошибки** | console.log | try/catch → [] ⚠️ | Error boundaries + retry + toast | + Sentry + observability |
| **Тесты** | 0 | 0 ❌ | Unit + integration | + e2e + visual regression |
| **CI/CD** | ручной FTP | rsync + docker ⚠️ | GitHub Actions + staging | + canary + feature flags |
| **Данные** | JSON файл | FastAPI + PostgreSQL + AI ✅ | + кеширование + pagination | + data lake + streaming |
| **UI** | Bootstrap | shadcn + Recharts + Plotly ✅ | + animations + a11y | + i18n + design tokens |

---

## 🎯 Что нужно для перехода на 8/10

### Phase 1: Фундамент (4-6 часов)
1. **DashboardProvider** — глобальный контекст для фильтров
2. **URL State sync** — `?country=KZ&days=30` в каждом маршруте
3. **Единый API-клиент** — один `apiClient.ts` с retry + error handling
4. **constants.ts** — все флаги, цвета, названия в одном месте

### Phase 2: Надёжность (4-6 часов)
5. **Error boundaries** на каждой странице
6. **Skeleton loaders** вместо "Загрузка..."
7. **Toast notifications** при ошибках API
8. **GitHub Actions** — lint + build на каждый PR

### Phase 3: Провязка виджетов (6-8 часов)
9. **Клик на карте → фильтрует сюжеты, источники, VOX**
10. **Breadcrumbs** — "Обзор → Казахстан → Сюжет: Экстрадиция"
11. **Cross-page deep links** — со страницы VOX в конкретный сюжет

### Phase 4: Продакшн (4-6 часов)
12. **Тесты** — хотя бы API calls + key components
13. **Sentry** для мониторинга ошибок
14. **Proper CI/CD** — push → build → deploy

---

## 💡 Вердикт

**GeoPulse — это сильный прототип уровня "advanced MVP".**

Он впечатляет **продуктовой глубиной**: AI-анализ, thread detection, VOX sentiment, 220+ источников — это не учебный проект. Бэкенд архитектурно зрелее фронтенда.

Фронтенд — **типичный "быстро собрали на Next.js"**: всё работает, выглядит хорошо, но каждая страница живёт сама по себе. Это уровень хорошего хакатон-проекта или ранней стадии стартапа.

Для **демонстрации клиенту** — отлично. Для **daily use аналитиками** — нужна провязка виджетов и надёжная обработка ошибок. Для **enterprise** — нужны тесты, CI/CD, мониторинг.

**Ближайший аналог по уровню**: сырой Metabase/Redash dashboard с кастомным бэкендом. Потенциал — стать мини-Palantir для геополитики, если доделать widget linking из плана GEO-LAB.
