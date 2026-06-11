#!/usr/bin/env bash
# GEO PULSE — turnkey deploy. Run ON the target server (Ubuntu/Debian, as root).
#
#   curl -fsSL <raw-url>/deploy/deploy.sh -o deploy.sh   # or scp it over
#   REPO_URL="https://<github-token>@github.com/milkmike/GEO_PULSE.git" bash deploy.sh
#
# Idempotent: installs Docker if missing, clones/updates the repo on the
# world branch, ensures .env, builds & starts the world-contour stack,
# applies migration 008, and (optionally) backfills GDELT.
#
# Env knobs:
#   REPO_URL       git URL incl. auth for the private repo (required on first run
#                  unless the script is executed from inside an existing checkout)
#   BRANCH         default: claude/cool-allen-flyikw
#   APP_DIR        default: /opt/geopulse
#   WITH_TELEGRAM  "1" to also start vox/tg collectors (needs TELEGRAM_* in .env)
#   GDELT_BACKFILL_DAYS  default: 90 (set 0 to skip the initial backfill)
set -euo pipefail

BRANCH="${BRANCH:-main}"
APP_DIR="${APP_DIR:-/opt/geopulse}"
WITH_TELEGRAM="${WITH_TELEGRAM:-0}"
GDELT_BACKFILL_DAYS="${GDELT_BACKFILL_DAYS:-90}"

CORE_SERVICES="db redis collector analyzer temperature threads integrity api gdelt-collector ru-index signals briefs fx-collector web"
TG_SERVICES="vox-collector vox-analyzer vox-engine tg-collector"

log() { printf '\n\033[1;36m▸ %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" = "0" ] || die "Запустите от root (sudo bash deploy.sh)."

# ── 1. Docker ──────────────────────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
  log "Устанавливаю Docker…"
  curl -fsSL https://get.docker.com | sh
fi
if ! docker compose version >/dev/null 2>&1; then
  log "Ставлю docker compose plugin…"
  apt-get update -qq && apt-get install -y -qq docker-compose-plugin
fi
systemctl enable --now docker >/dev/null 2>&1 || true

# ── 2. Код ─────────────────────────────────────────────────────────────────
if [ -f "docker-compose.yml" ] && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  APP_DIR="$(pwd)"
  log "Использую текущий репозиторий: $APP_DIR"
  git fetch origin "$BRANCH" && git checkout "$BRANCH" && git pull origin "$BRANCH"
elif [ -d "$APP_DIR/.git" ]; then
  log "Обновляю $APP_DIR ($BRANCH)…"
  cd "$APP_DIR"
  git fetch origin "$BRANCH" && git checkout "$BRANCH" && git pull origin "$BRANCH"
else
  [ -n "${REPO_URL:-}" ] || die "REPO_URL не задан, а $APP_DIR пуст. Передайте REPO_URL с токеном для приватного репо."
  log "Клонирую в $APP_DIR ($BRANCH)…"
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
  cd "$APP_DIR"
fi

# ── 3. .env ────────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
  cp .env.example .env
  die "Создан .env из шаблона. Заполните DB_PASSWORD, OPENROUTER_API_KEY (и JINA_API_KEY), затем перезапустите скрипт."
fi
# DATABASE_URL обязан содержать тот же пароль, что и DB_PASSWORD — проверим грубо
grep -q '^OPENROUTER_API_KEY=.\+' .env || die "В .env пуст OPENROUTER_API_KEY — анализ и брифинги не заработают."
grep -q '^DB_PASSWORD=.\+' .env || die "В .env пуст DB_PASSWORD."

# ── 4. Запуск стека ────────────────────────────────────────────────────────
SERVICES="$CORE_SERVICES"
[ "$WITH_TELEGRAM" = "1" ] && SERVICES="$SERVICES $TG_SERVICES"

log "Сборка и запуск: $SERVICES"
# shellcheck disable=SC2086
docker compose up -d --build $SERVICES

log "Жду готовности БД…"
for i in $(seq 1 30); do
  if docker compose exec -T db pg_isready -U thermo -d cis_thermometer >/dev/null 2>&1; then
    echo "  БД готова."; break
  fi
  sleep 2
  [ "$i" = "30" ] && die "БД не поднялась за 60с — смотрите: docker compose logs db"
done

# ── 5. Миграции world-контура (идемпотентны) ───────────────────────────────
for mig in 008_world_expansion 009_entities_fx; do
  log "Применяю миграцию ${mig}…"
  docker compose exec -T db psql -U thermo -d cis_thermometer \
    < "scripts/migrations/${mig}.sql" >/dev/null 2>&1 \
    && echo "  ${mig}: применена." \
    || echo "  ${mig}: уже применена либо схема создана init.sql (это норм)"
done

# ── 6. Бэкфилл GDELT (первичное наполнение мирового контура) ────────────────
if [ "$GDELT_BACKFILL_DAYS" != "0" ]; then
  log "Бэкфилл GDELT за $GDELT_BACKFILL_DAYS дн. (фоном, ~10-20 мин)…"
  docker compose run -d --rm gdelt-collector \
    python scripts/collect_gdelt.py --days "$GDELT_BACKFILL_DAYS" >/dev/null 2>&1 \
    && echo "  Бэкфилл запущен в фоне." \
    || echo "  Не удалось запустить бэкфилл — выполните вручную позже."
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
log "Готово."
cat <<EOF

  Next.js-дашборд: http://${IP:-<server-ip>}:3334
  Мини-дашборд:    http://${IP:-<server-ip>}:8100/world
  API v2:          http://${IP:-<server-ip>}:8100/api/v2/countries
  Статус:          docker compose ps
  Логи индекса:    docker compose logs -f ru-index
  Логи сигналов:   docker compose logs -f signals

  Если бэкфилл GDELT не стартовал — запустите вручную:
    docker compose run --rm gdelt-collector python scripts/collect_gdelt.py --days 90

  Бэкфилл валют за год (опционально, ~5 мин):
    docker compose run --rm fx-collector python scripts/collect_fx.py --backfill-days 365

  Голосования ООН по 98 странам (опционально):
    docker compose run --rm ru-index python scripts/load_un_votes.py

  Первый расчёт RRI пройдёт автоматически в течение часа; чтобы сразу:
    docker compose run --rm ru-index python scripts/calc_ru_index.py

EOF
