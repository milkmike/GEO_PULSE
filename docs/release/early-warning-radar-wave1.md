# Early Warning Radar Wave 1: production release gate

Это обязательная последовательность первого включения Radar. Любой ненулевой
код, отсутствующая метрика или неподтверждённый browser smoke выключает feature
flag и возвращает web к скрытой сборке. Audit работает в `REPEATABLE READ READ
ONLY`; он пишет только явно указанный `--out`.

Порог полноты traceable evidence — `0.95`. Порог связанных media/action
контуров — `0.80`; если возможных пар нет, отчёт обязан явно вернуть
`not_applicable_no_pairs`, `possible_pairs=0`, `linked_pairs=0`, `ratio=null`.

Все production-команды используют только SSH-алиас `geopulse-prod`. Не
подставляйте IP, URL БД или пароль.

## 1. Локальный preflight образа

Именно `radar-worker` должен содержать оба CLI и миграции 027–030. До backup и
push выполните:

```bash
set -euo pipefail
git switch main
test -z "$(git status --porcelain)"
RELEASE_COMMIT="$(git rev-parse HEAD)"
docker compose config -q
docker compose build radar-worker
docker compose --profile radar run --rm --no-deps radar-worker python scripts/build_radar.py --help
docker compose --profile radar run --rm --no-deps radar-worker python scripts/audit_radar_wave1.py --help
docker compose --profile radar run --rm --no-deps radar-worker python -c "from scripts.audit_radar_wave1 import required_migrations; assert required_migrations() == ['027_early_warning_radar.sql','028_radar_evidence_roots.sql','029_radar_evidence_relation_rows.sql','030_radar_contour_alignment_identity.sql']"
```

## 2. Protected backup до deploy

Создайте закрытый случайный каталог. Не используйте `/tmp` или предсказуемое
имя; существующий backup никогда не перезаписывается.

```bash
umask 077
RELEASE_DIR="$(ssh geopulse-prod 'set -euo pipefail; umask 077; cd /opt/geopulse; mkdir -p backups; mktemp -d backups/radar-wave1.XXXXXXXX')"
case "$RELEASE_DIR" in backups/radar-wave1.*) ;; *) exit 1 ;; esac
ssh geopulse-prod "set -euo pipefail; umask 077; cd /opt/geopulse; test ! -e '$RELEASE_DIR/protected-before.dump'; docker compose exec -T db pg_dump -U thermo -d cis_thermometer -Fc > '$RELEASE_DIR/protected-before.dump'; test -s '$RELEASE_DIR/protected-before.dump'"
```

Дамп — только аварийный артефакт. Его нельзя восстанавливать поверх живой БД в
этом runbook.

## 3. Скрытый deploy и миграции

До push зафиксируйте false и действительно пересоберите web: flag является
build arg, одного recreate старого образа недостаточно.

```bash
ssh geopulse-prod 'set -euo pipefail; umask 077; cd /opt/geopulse; if grep -q "^FEATURE_EARLY_WARNING_RADAR=" .env; then sed -i "s/^FEATURE_EARLY_WARNING_RADAR=.*/FEATURE_EARLY_WARNING_RADAR=false/" .env; else printf "\nFEATURE_EARLY_WARNING_RADAR=false\n" >> .env; fi; if ! docker compose build web; then docker compose stop web; exit 1; fi; docker compose up -d --no-deps --force-recreate web || { docker compose stop web; exit 1; }'
git push origin main
ssh geopulse-prod "set -euo pipefail; cd /opt/geopulse; expected='$RELEASE_COMMIT'; for attempt in \$(seq 1 32); do deployed=\$(cat .deploy-state/last-successful-commit 2>/dev/null || true); test \"\$deployed\" = \"\$expected\" && test \"\$(git rev-parse HEAD)\" = \"\$expected\" && exit 0; sleep 15; done; exit 1"
ssh geopulse-prod "set -euo pipefail; cd /opt/geopulse; applied=\$(docker compose exec -T db psql -U thermo -d cis_thermometer -X -Atqc \"SELECT count(*) FROM public.schema_migrations WHERE filename = ANY (ARRAY['027_early_warning_radar.sql','028_radar_evidence_roots.sql','029_radar_evidence_relation_rows.sql','030_radar_contour_alignment_identity.sql'])\"); test \"\$applied\" = 4; bad_index=\$(docker compose exec -T db psql -U thermo -d cis_thermometer -X -Atqc \"SELECT pg_catalog.to_regclass('public.uq_radar_trend_evidence_observation') IS NULL\"); test \"\$bad_index\" = t"
```

Миграции учитываются по полному filename. `029` чинит production-схему ранней
`028`: снимает неверную уникальность trend/observation и дозаполняет roots без
удаления или объединения audit-фактов. `030` аддитивно хранит отдельную
alignment identity для связи media/action contours при разных local identities.
Никогда не удаляйте записи 028–030 из `public.schema_migrations` и не переигрывайте 028. При сбое исправьте причину и
повторите только штатный `docker compose run --rm migrate`.

## 4. Один quiesced release-сеанс

Следующий защищённый script устанавливается в случайный release-каталог с
`umask 077`. Его failure trap объявлен **до** остановки writers. Trap всегда
пишет false, пересобирает/recreates web и запускает только те writers, которые
были запущены до gate.

```bash
ssh geopulse-prod "set -euo pipefail; umask 077; cd /opt/geopulse; cat > '$RELEASE_DIR/release-gate.sh'" <<'RELEASE_GATE'
#!/usr/bin/env bash
set -euo pipefail
umask 077
cd /opt/geopulse
: "${RELEASE_DIR:?RELEASE_DIR is required}"
case "$RELEASE_DIR" in backups/radar-wave1.*) ;; *) exit 1 ;; esac

candidates='collector analyzer temperature threads integrity gdelt-collector ru-index signals briefs fx-collector un-votes-loader trade-loader sanctions-loader crea-imports-loader market-radar-loader vox-collector vox-analyzer vox-engine tg-collector radar-worker'
running_services="$(docker compose ps --status running --services)"
running_writers="$(printf '%s\n' "$running_services" | grep -E "^($(printf '%s' "$candidates" | tr ' ' '|'))$" || true)"

set_flag() {
  value="$1"
  if grep -q '^FEATURE_EARLY_WARNING_RADAR=' .env; then
    sed -i "s/^FEATURE_EARLY_WARNING_RADAR=.*/FEATURE_EARLY_WARNING_RADAR=$value/" .env
  else
    printf '\nFEATURE_EARLY_WARNING_RADAR=%s\n' "$value" >> .env
  fi
}

restart_writers() {
  test -z "$running_writers" || docker compose start $running_writers >/dev/null
}

fail_closed() {
  status=$?
  trap - EXIT HUP INT TERM
  set +e
  docker compose stop radar-worker >/dev/null 2>&1
  if set_flag false && docker compose build web; then
    docker compose up -d --no-deps --force-recreate web || docker compose stop web
  else
    docker compose stop web
  fi
  restart_writers
  exit "$status"
}

trap 'fail_closed' EXIT HUP INT TERM
test -z "$running_writers" || docker compose stop $running_writers

radar_run() {
  docker compose --profile radar run --rm --no-deps \
    -v /opt/geopulse/backups:/app/backups radar-worker "$@"
}

# Authoritative protected identity: writers remain stopped through final compare.
radar_run python scripts/audit_radar_wave1.py \
  --phase before --json --out "/app/$RELEASE_DIR/authoritative-before.json"

# Exactly 90 days, no persistence; the audit validates the enriched replay contract.
radar_run sh -c "umask 077; exec python scripts/build_radar.py --shadow --days 90 --json-report '/app/$RELEASE_DIR/shadow-replay.json'"
radar_run python scripts/audit_radar_wave1.py \
  --phase shadow \
  --compare "/app/$RELEASE_DIR/authoritative-before.json" \
  --replay-report "/app/$RELEASE_DIR/shadow-replay.json" \
  --evidence-minimum 0.95 --contour-minimum 0.80 \
  --json --out "/app/$RELEASE_DIR/shadow-audit.json"

# One bounded write; never start a loop or permanent radar-worker here.
radar_run sh -c "umask 077; exec python scripts/build_radar.py --apply --days 90 --json-report '/app/$RELEASE_DIR/bounded-apply.json'"
docker compose stop radar-worker >/dev/null 2>&1 || true

# Fresh PostgreSQL write-counter and exact row snapshot after apply, before any GET.
radar_run python scripts/audit_radar_wave1.py \
  --phase before --json --out "/app/$RELEASE_DIR/public-get-before.json"

trend_id="$(docker compose exec -T db psql -U thermo -d cis_thermometer -X -Atqc 'SELECT public_id FROM public.radar_trends ORDER BY id LIMIT 1')"
country="$(docker compose exec -T db psql -U thermo -d cis_thermometer -X -Atqc 'SELECT country_code FROM public.radar_trends WHERE country_code IS NOT NULL ORDER BY id LIMIT 1')"
story_id="$(docker compose exec -T db psql -U thermo -d cis_thermometer -X -Atqc 'SELECT id FROM public.stories ORDER BY id LIMIT 1')"
signal_id="$(docker compose exec -T db psql -U thermo -d cis_thermometer -X -Atqc 'SELECT id FROM public.signals ORDER BY id LIMIT 1')"
test -n "$trend_id" && test -n "$country" && test -n "$story_id" && test -n "$signal_id"

# hidden API smoke
for path in "/api/v2/radar?limit=1" "/api/v2/radar/trends/$trend_id" "/api/v2/radar/trends/$trend_id/timeline" "/api/v2/radar/trends/$trend_id/evidence?limit=10" "/api/v2/countries/$country/radar?limit=1" "/api/v2/radar/coverage" "/api/v2/methodology/radar"; do
  curl -fsS -o /dev/null "http://127.0.0.1:8100$path"
done
sleep 2
radar_run python scripts/audit_radar_wave1.py \
  --phase after \
  --compare "/app/$RELEASE_DIR/authoritative-before.json" \
  --public-get-before "/app/$RELEASE_DIR/public-get-before.json" \
  --evidence-minimum 0.95 --contour-minimum 0.80 \
  --json --out "/app/$RELEASE_DIR/hidden-after.json"

# Enable only after hidden gates. The API is intentionally always registered;
# only web is feature-flagged, so there is no API flag assertion.
# Equivalent environment mutation: FEATURE_EARLY_WARNING_RADAR=true.
set_flag true
docker compose build web
docker compose up -d --no-deps --force-recreate web

# Feature-specific API/content checks before the mandatory real browser gate.
curl -fsS "http://127.0.0.1:8100/api/v2/radar?limit=1" | grep -F 'items' >/dev/null
curl -fsS "http://127.0.0.1:8100/api/v2/radar/trends/$trend_id" | grep -F "$trend_id" >/dev/null
curl -fsS "http://127.0.0.1:3334/radar" | grep -F 'Радар' >/dev/null

printf '%s\n' \
  'MANDATORY browser smoke (real browser, not curl):' \
  '  https://massaraksh.tech/                         — radar navigation and home panel' \
  '  https://massaraksh.tech/radar                    — list and filters' \
  "  https://massaraksh.tech/radar/$trend_id          — trend evidence/contours/T0" \
  "  https://massaraksh.tech/country/$country         — related Radar panel" \
  "  https://massaraksh.tech/stories/$story_id        — analytical trends panel" \
  "  https://massaraksh.tech/signals/$signal_id       — related trend card" \
  '  https://massaraksh.tech/about                    — Radar methodology'
read -r -p 'After all seven pages render feature-specific content, type BROWSER_SMOKE_PASSED: ' browser_gate
test "$browser_gate" = BROWSER_SMOKE_PASSED

sleep 2
radar_run python scripts/audit_radar_wave1.py \
  --phase after \
  --compare "/app/$RELEASE_DIR/authoritative-before.json" \
  --public-get-before "/app/$RELEASE_DIR/public-get-before.json" \
  --evidence-minimum 0.95 --contour-minimum 0.80 \
  --json --out "/app/$RELEASE_DIR/final-after.json"

test -z "$running_writers" || docker compose start $running_writers >/dev/null
trap - EXIT HUP INT TERM
printf 'RADAR_RELEASE_PASSED reports=%s\n' "$RELEASE_DIR"
RELEASE_GATE

ssh -tt geopulse-prod "set -euo pipefail; umask 077; cd /opt/geopulse; RELEASE_DIR='$RELEASE_DIR' bash '$RELEASE_DIR/release-gate.sh'"
```

`final-after.json` должен иметь `gates.passed=true`, точное равенство count и
полного PK fingerprint для всех protected-таблиц, неизменные
`n_tup_ins/n_tup_upd/n_tup_del` и stats reset identity во всех protected/Radar
таблицах, traceable evidence ≥0.95, contour completeness ≥0.80 (либо честный
no-pair статус), ноль T0/coverage/notification/public-ID violations.

## 5. Ручной rollback

Если UI нужно скрыть после успешного релиза, примените тот же rebuild-путь.
Radar history остаётся в БД.

```bash
ssh geopulse-prod 'set -euo pipefail; umask 077; cd /opt/geopulse; if grep -q "^FEATURE_EARLY_WARNING_RADAR=" .env; then sed -i "s/^FEATURE_EARLY_WARNING_RADAR=.*/FEATURE_EARLY_WARNING_RADAR=false/" .env; else printf "\nFEATURE_EARLY_WARNING_RADAR=false\n" >> .env; fi; docker compose stop radar-worker >/dev/null 2>&1 || true; if ! docker compose build web; then docker compose stop web; exit 1; fi; docker compose up -d --no-deps --force-recreate web || { docker compose stop web; exit 1; }'
```

Никогда не выполняйте `DROP`/`TRUNCATE` Radar-таблиц, не удаляйте migration
history, не восстанавливайте backup поверх живой БД и не удаляйте/перезаписывайте
protected data. Для настоящего восстановления нужен отдельный одобренный план.
