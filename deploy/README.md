# Развёртывание GEO PULSE

> ⚠️ Развернуть из облачной сессии Claude нельзя — окружение блокирует
> исходящий SSH. Эти шаги выполняются **вами** с машины, у которой есть
> доступ к серверу по SSH.

## Быстрый старт (сервер 147.45.99.24)

С вашего локального компьютера:

```bash
# 1. Зайти на сервер
ssh root@147.45.99.24

# 2. Получить deploy-скрипт (любой способ):
#    а) если репо публичный/есть токен — скачать raw,
#    б) либо скопировать deploy/deploy.sh через scp заранее.

# 3. Развернуть. REPO_URL нужен с токеном, т.к. репозиторий приватный:
REPO_URL="https://<GITHUB_TOKEN>@github.com/milkmike/GEO_PULSE.git" \
  bash deploy.sh
```

Первый запуск создаст `/opt/geopulse/.env` из шаблона и остановится —
заполните ключи и запустите скрипт снова:

```bash
nano /opt/geopulse/.env     # DB_PASSWORD, OPENROUTER_API_KEY, JINA_API_KEY
cd /opt/geopulse && bash deploy/deploy.sh
```

## Что нужно в `.env` (минимум для мирового контура)

| Переменная | Зачем | Обязательна |
|---|---|---|
| `DB_PASSWORD` | пароль Postgres | да |
| `DATABASE_URL` | тот же пароль внутри строки подключения | да |
| `OPENROUTER_API_KEY` | LLM-анализ статей и брифинги | да |
| `JINA_API_KEY` | эмбеддинги для кластеризации сюжетов | желательно |
| `TELEGRAM_*` | только для vox/tg-коллекторов | нет |

Получить ключи: [OpenRouter](https://openrouter.ai/keys) · [Jina](https://jina.ai/embeddings).

## Что поднимется

14 сервисов мирового контура (db, redis, collector, analyzer, temperature,
threads, integrity, api, **gdelt-collector, ru-index, signals, briefs**).
Telegram-коллекторы по умолчанию выключены — добавьте `WITH_TELEGRAM=1`,
если заполнили `TELEGRAM_*`.

После старта:

- **Дашборд:** `http://147.45.99.24:8100/world`
- **API:** `http://147.45.99.24:8100/api/v2/countries`

Порт по умолчанию — `8100`. Чтобы отдать дашборд на 80-м порту, поставьте
перед ним nginx-reverse-proxy или поменяйте маппинг порта api в
`docker-compose.yml` (`"80:8000"`).

## После развёртывания

1. **Сменить пароль сервера** — он засветился в чате: `passwd`.
2. Проверить GDELT (из дата-центра иногда блокируется): логи
   `docker compose logs gdelt-collector` — должны идти upsert'ы.
   Бэкфилл за 90 дней наполняет карту по 99 странам.
3. Дать системе ~2 недели накопить историю GDELT — тогда заработают
   сигналы сдвига тона/объёма (нужны z-скоры от 90-дневной нормы).

## Полезные команды

```bash
docker compose ps                                   # статус
docker compose logs -f ru-index                     # расчёт индекса
docker compose run --rm ru-index python scripts/calc_ru_index.py   # пересчёт сейчас
docker compose run --rm gdelt-collector python scripts/collect_gdelt.py --days 90
docker compose restart api                           # перезапуск API
```
