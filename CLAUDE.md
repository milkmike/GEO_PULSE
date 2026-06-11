# GEO PULSE — заметки для Claude

## Что это
Платформа мониторинга отношений стран мира с Россией (вдохновлена
github.com/koala73/worldmonitor, паритет-план: docs/ROADMAP_WORLDMONITOR.md).
Два контура: tier 1 — глубокий (свои источники → LLM-анализ → температура),
tier 2 — глобальный (GDELT тон/объём по 99 странам). Поверх — Russia Relations
Index (RRI), сигнальный движок, AI-брифинги, реестр сущностей, дашборд /world.

## Конвенции
- Общение с пользователем — на русском; код, коммиты, комментарии — на английском.
- Лицензия AGPL-3.0: код/методологии worldmonitor можно адаптировать с атрибуцией.
- **Термометр v1 неприкосновенен**: src/engine/index.py и весь API v1 не ломать —
  температура служит медиа-слоем RRI для стран глубокого покрытия.
- Тиры источников (точные значения): official, mainstream, independent, social,
  domestic_opposition, western_proxy, analytics.
- Новые таблицы: добавлять и в scripts/migrations/NNN_*.sql (идемпотентно,
  IF NOT EXISTS), и в data/init.sql — init.sql обязан совпадать с живой схемой.

## Грабли (выученные на интеграционных тестах)
- Запрос к опциональной таблице (un_votes и т.п.) — только внутри
  session.begin_nested(), иначе ошибка отравляет всю транзакцию сессии.
- Каждый сигнальный детектор работает в собственной сессии (см. detect_all).
- GDELT DOC API: оператор AND не поддерживается (термины AND-ятся неявно);
  коды стран — FIPS, не ISO (поле fips в src/countries.py); с датацентровых
  IP может отдавать 503 — проверять с прод-сервера.
- Окружение Claude-сессий блокирует исходящий SSH — деплой только руками
  оператора через deploy/deploy.sh.

## Карта ключевых файлов
- src/countries.py — реестр 99 стран (ISO/FIPS, блоки, санкции, baseline_adj)
- src/engine/ru_index.py — RRI v1 (веса, полы/потолки, уровни ally..hostile)
- src/engine/signals.py — 6 детекторов (dedup_key + TTL против спама)
- src/engine/health.py — свежесть источников, вердикты HEALTHY..UNHEALTHY
- src/llm.py — цепочка моделей OpenRouter → Ollama, Redis-кеш ответов
- src/pipeline/{prompts,topics,sentiment,briefs}.py — промпт v2.0, 16 тем
- src/entities.py — 43 сущности РФ-орбиты с алиасами
- src/api/routes/world.py — API v2; src/api/static/world.html — дашборд /world
- scripts/{collect_gdelt,calc_ru_index,detect_signals,generate_briefs}.py — воркеры
- deploy/deploy.sh — идемпотентный деплой на сервер

## Тестирование (без прод-БД)
Локальный Postgres: initdb в /tmp + data/init.sql + data/002_threads.sql,
фикстуры статей/анализа, затем FastAPI TestClient (перед импортом app заглушить
feedparser: sys.modules['feedparser'] = types.ModuleType('feedparser')).
Образцы фикстур и проверок — в истории PR #1.
