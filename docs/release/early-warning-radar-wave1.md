# Early Warning Radar Wave 1: production release gate

Этот runbook — единственный разрешённый порядок первого включения Radar Wave 1.
Все проверки БД выполняются в `REPEATABLE READ READ ONLY`; файлы отчётов —
единственная запись audit-команды. Любой ненулевой код завершения останавливает
релиз. Команды не печатают `DATABASE_URL`, пароль БД или другие секреты.

Ожидаются локальный чистый `main`, SSH-алиас `geopulse-prod`, production checkout
`/opt/geopulse` и существующий cron auto-deploy. Не подставляйте IP-адрес вместо
алиаса. Все JSON-отчёты и дамп остаются в gitignored-каталоге
`/opt/geopulse/backups/radar-wave1`.

## 1. Резервная копия и снимок до релиза

Сначала зафиксируйте commit, убедитесь, что четыре release-файла уже находятся в
нём, но **ещё не отправляйте `main`**. Первая команда не перезаписывает
существующий дамп.

```bash
set -euo pipefail
git switch main
git status --short
test -z "$(git status --porcelain)"
RELEASE_COMMIT="$(git rev-parse HEAD)"

ssh geopulse-prod 'set -euo pipefail; cd /opt/geopulse; mkdir -p backups/radar-wave1; test ! -e backups/radar-wave1/pre-radar-wave1.dump; docker compose exec -T db pg_dump -U thermo -d cis_thermometer -Fc > backups/radar-wave1/pre-radar-wave1.dump; test -s backups/radar-wave1/pre-radar-wave1.dump'

# Передаём только audit-скрипт во временный файл: production checkout остаётся чистым.
ssh geopulse-prod 'cat > /tmp/audit_radar_wave1.py && chmod 0444 /tmp/audit_radar_wave1.py' < scripts/audit_radar_wave1.py
ssh geopulse-prod 'set -euo pipefail; cd /opt/geopulse; test ! -e backups/radar-wave1/before.json; docker compose run --rm --no-deps -v /tmp/audit_radar_wave1.py:/app/scripts/audit_radar_wave1.py:ro -v /opt/geopulse/backups:/app/backups temperature python scripts/audit_radar_wave1.py --phase before --json --out /app/backups/radar-wave1/before.json'
```

`before.json` обязан содержать все десять protected-таблиц: `articles`,
`analysis`, `temperature`, `ru_index`, `signals`, `briefs`, `threads`,
`thread_articles`, `stories`, `story_articles`, их количества, колонки первичных
ключей, границы и SHA-256 fingerprints. Статус `missing` или
`invalid_no_primary_key` — блокировка, а не допустимое отсутствие.

## 2. Feature flag выключен до deploy

Зафиксируйте false в gitignored `.env` до отправки commit и пересоздайте только
текущие API/web-контейнеры. Значение не выводится вместе с остальным `.env`.

```bash
ssh geopulse-prod 'set -euo pipefail; cd /opt/geopulse; if grep -q "^FEATURE_EARLY_WARNING_RADAR=" .env; then sed -i "s/^FEATURE_EARLY_WARNING_RADAR=.*/FEATURE_EARLY_WARNING_RADAR=false/" .env; else printf "\nFEATURE_EARLY_WARNING_RADAR=false\n" >> .env; fi; docker compose up -d --no-deps api web; test "$(docker compose exec -T web printenv FEATURE_EARLY_WARNING_RADAR)" = false'
```

Навигация и публичные Radar-компоненты должны оставаться скрытыми вплоть до
успешного shadow replay, bounded apply и hidden API smoke.

## 3. Push `main`, auto-deploy и миграции

Push в `main` запускает существующий pull-based auto-deploy (обычно не более
пяти минут). Он сам выполняет `docker compose run --rm migrate` до смены
контейнеров. Не делайте параллельный ручной deploy.

```bash
git push origin main
ssh geopulse-prod "set -euo pipefail; cd /opt/geopulse; expected='${RELEASE_COMMIT}'; for attempt in \$(seq 1 32); do deployed=\$(cat .deploy-state/last-successful-commit 2>/dev/null || true); if test \"\$deployed\" = \"\$expected\" && test \"\$(git rev-parse HEAD)\" = \"\$expected\"; then exit 0; fi; sleep 15; done; echo 'auto-deploy did not reach the release commit' >&2; exit 1"

ssh geopulse-prod 'set -euo pipefail; cd /opt/geopulse; docker compose exec -T db psql -U thermo -d cis_thermometer -X -v ON_ERROR_STOP=1 -c "SELECT filename FROM schema_migrations WHERE filename IN ('\''027_early_warning_radar.sql'\'', '\''028_radar_evidence_roots.sql'\'', '\''029_radar_evidence_relation_rows.sql'\'') ORDER BY filename;"; test "$(docker compose exec -T db psql -U thermo -d cis_thermometer -X -Atqc "SELECT count(*) FROM schema_migrations WHERE filename IN ('\''027_early_warning_radar.sql'\'', '\''028_radar_evidence_roots.sql'\'', '\''029_radar_evidence_relation_rows.sql'\'')")" = 3; test "$(docker compose exec -T db psql -U thermo -d cis_thermometer -X -Atqc "SELECT to_regclass('\''public.uq_radar_trend_evidence_observation'\'') IS NULL")" = t'
```

Миграции учитываются **по полному имени файла**. `029` — upgrade-repair для
production-баз, где ранняя `028` уже была отмечена как применённая: она удаляет
ошибочную уникальность пары trend/observation и дозаполняет evidence roots, не
удаляя и не объединяя audit-факты. Никогда не удаляйте строку `028` из
`schema_migrations`, не запускайте `028` повторно и не правьте историю вручную.
Если `029` отсутствует, остановитесь, изучите log auto-deploy и после устранения
причины безопасно повторите только штатный runner:

```bash
ssh geopulse-prod 'set -euo pipefail; cd /opt/geopulse; docker compose run --rm migrate'
```

## 4. 90-дневный shadow replay и compare gate

Shadow-команда ничего не сохраняет в БД. Поэтому audit получает её JSON как
явный input и проверяет полноту evidence по replay, а protected identities — по
`before.json`.

```bash
ssh geopulse-prod 'set -euo pipefail; cd /opt/geopulse; docker compose --profile radar run --rm --no-deps -v /opt/geopulse/backups:/app/backups radar-worker python scripts/build_radar.py --shadow --days 90 --json-report /app/backups/radar-wave1/shadow-replay.json; docker compose --profile radar run --rm --no-deps -v /opt/geopulse/backups:/app/backups radar-worker python scripts/audit_radar_wave1.py --phase shadow --compare /app/backups/radar-wave1/before.json --replay-report /app/backups/radar-wave1/shadow-replay.json --json --out /app/backups/radar-wave1/shadow-audit.json'
```

Продолжать можно только при `gates.passed=true`: protected counts не уменьшились,
старые primary-key identities сохранились, все миграции 027–029 отмечены,
evidence completeness не ниже `RADAR_EVIDENCE_COMPLETENESS_GATE` (по умолчанию
`0.95`), а replay содержит хотя бы одно наблюдение. Статусы `unavailable` и
нулевой denominator не считаются успехом.

## 5. Один bounded apply

Это единственная разрешённая запись Radar на первом релизе. Не включайте loop,
cron или постоянный radar-worker.

```bash
ssh geopulse-prod 'set -euo pipefail; cd /opt/geopulse; docker compose --profile radar run --rm --no-deps -v /opt/geopulse/backups:/app/backups radar-worker python scripts/build_radar.py --apply --days 90 --json-report /app/backups/radar-wave1/bounded-apply.json; docker compose stop radar-worker >/dev/null 2>&1 || true'
```

## 6. Hidden API smoke и доказательство отсутствия GET-записей

На время пары row-count snapshots кратко остановите штатных writers, иначе их
обычная фоновая работа неотличима от записи GET-запроса. `trap` гарантирует их
возврат даже при провале gate. API остаётся доступным, а Radar UI всё ещё скрыт.

```bash
ssh geopulse-prod 'bash -se' <<'REMOTE'
set -euo pipefail
cd /opt/geopulse
candidates='collector analyzer temperature threads integrity gdelt-collector ru-index signals briefs fx-collector un-votes-loader trade-loader sanctions-loader crea-imports-loader market-radar-loader vox-collector vox-analyzer vox-engine tg-collector'
running_writers="$(docker compose ps --status running --services | grep -E "^($(printf '%s' "$candidates" | tr ' ' '|'))$" || true)"
test -z "$running_writers" || docker compose stop $running_writers
trap 'test -z "$running_writers" || docker compose start $running_writers >/dev/null' EXIT
trend_id="$(docker compose exec -T db psql -U thermo -d cis_thermometer -X -Atqc "SELECT public_id FROM radar_trends ORDER BY id LIMIT 1")"
country="$(docker compose exec -T db psql -U thermo -d cis_thermometer -X -Atqc "SELECT country_code FROM radar_trends WHERE country_code IS NOT NULL ORDER BY id LIMIT 1")"
test -n "$trend_id"
test -n "$country"
docker compose --profile radar run --rm --no-deps -v /opt/geopulse/backups:/app/backups radar-worker python scripts/audit_radar_wave1.py --phase after --compare /app/backups/radar-wave1/before.json --json --out /app/backups/radar-wave1/hidden-get-before.json
for path in "/api/v2/radar?limit=1" "/api/v2/radar/trends/$trend_id" "/api/v2/radar/trends/$trend_id/timeline" "/api/v2/radar/trends/$trend_id/evidence?limit=10" "/api/v2/countries/$country/radar?limit=1" "/api/v2/radar/coverage" "/api/v2/methodology/radar"; do
  curl -fsS -o /dev/null "http://127.0.0.1:8100$path"
done
docker compose --profile radar run --rm --no-deps -v /opt/geopulse/backups:/app/backups radar-worker python scripts/audit_radar_wave1.py --phase after --compare /app/backups/radar-wave1/before.json --public-get-before /app/backups/radar-wave1/hidden-get-before.json --json --out /app/backups/radar-wave1/hidden-get-after.json
test -z "$running_writers" || docker compose start $running_writers >/dev/null
trap - EXIT
REMOTE
```

`hidden-get-after.json` обязан иметь
`public_get_write_verification.status="unchanged"`. Проверяются protected и все
десять Radar-таблиц, включая `analysis_runs` и `notification_events`.

## 7. Включение flag и полный публичный smoke

Только после двух успешных JSON gates выше установите flag и пересоздайте
API/web. Затем снимите ещё одну пару write snapshots и проверьте ровно семь
публичных страниц: главную, `/radar`, один trend, country, story, signal и About.

```bash
ssh geopulse-prod 'set -euo pipefail; cd /opt/geopulse; sed -i "s/^FEATURE_EARLY_WARNING_RADAR=.*/FEATURE_EARLY_WARNING_RADAR=true/" .env; docker compose up -d --no-deps api web; test "$(docker compose exec -T web printenv FEATURE_EARLY_WARNING_RADAR)" = true; test "$(docker compose exec -T api printenv FEATURE_EARLY_WARNING_RADAR)" = true'

ssh geopulse-prod 'bash -se' <<'REMOTE'
set -euo pipefail
cd /opt/geopulse
candidates='collector analyzer temperature threads integrity gdelt-collector ru-index signals briefs fx-collector un-votes-loader trade-loader sanctions-loader crea-imports-loader market-radar-loader vox-collector vox-analyzer vox-engine tg-collector'
running_writers="$(docker compose ps --status running --services | grep -E "^($(printf '%s' "$candidates" | tr ' ' '|'))$" || true)"
test -z "$running_writers" || docker compose stop $running_writers
trap 'test -z "$running_writers" || docker compose start $running_writers >/dev/null' EXIT
trend_id="$(docker compose exec -T db psql -U thermo -d cis_thermometer -X -Atqc "SELECT public_id FROM radar_trends ORDER BY id LIMIT 1")"
country="$(docker compose exec -T db psql -U thermo -d cis_thermometer -X -Atqc "SELECT country_code FROM radar_trends WHERE country_code IS NOT NULL ORDER BY id LIMIT 1")"
story_id="$(docker compose exec -T db psql -U thermo -d cis_thermometer -X -Atqc "SELECT id FROM stories ORDER BY id LIMIT 1")"
signal_id="$(docker compose exec -T db psql -U thermo -d cis_thermometer -X -Atqc "SELECT id FROM signals ORDER BY id LIMIT 1")"
test -n "$trend_id"
test -n "$country"
test -n "$story_id"
test -n "$signal_id"
docker compose --profile radar run --rm --no-deps -v /opt/geopulse/backups:/app/backups radar-worker python scripts/audit_radar_wave1.py --phase after --compare /app/backups/radar-wave1/before.json --json --out /app/backups/radar-wave1/public-get-before.json
for path in "/" "/radar" "/radar/$trend_id" "/country/$country" "/stories/$story_id" "/signals/$signal_id" "/about"; do
  curl -fsS -o /dev/null "http://127.0.0.1:3334$path"
done
docker compose --profile radar run --rm --no-deps -v /opt/geopulse/backups:/app/backups radar-worker python scripts/audit_radar_wave1.py --phase after --compare /app/backups/radar-wave1/before.json --public-get-before /app/backups/radar-wave1/public-get-before.json --json --out /app/backups/radar-wave1/after.json
test -z "$running_writers" || docker compose start $running_writers >/dev/null
trap - EXIT
REMOTE
```

Финальный `after.json` должен иметь `gates.passed=true`, неизменный public GET
snapshot, ноль duplicate public IDs, ноль T0 violations, ноль notification
delivery-key duplicates/invalid references, ноль confirmed trends ниже coverage
hard gate и evidence completeness не ниже порога. Сохраните dump и все отчёты
вместе с release commit ID.

## 8. Rollback без потери истории

Rollback — только скрытие интерфейса и остановка Radar writer. Он не откатывает
обычные collectors и не удаляет результаты уже выполненного bounded apply.

```bash
ssh geopulse-prod 'set -euo pipefail; cd /opt/geopulse; if grep -q "^FEATURE_EARLY_WARNING_RADAR=" .env; then sed -i "s/^FEATURE_EARLY_WARNING_RADAR=.*/FEATURE_EARLY_WARNING_RADAR=false/" .env; else printf "\nFEATURE_EARLY_WARNING_RADAR=false\n" >> .env; fi; docker compose stop radar-worker >/dev/null 2>&1 || true; docker compose up -d --no-deps api web; test "$(docker compose exec -T web printenv FEATURE_EARLY_WARNING_RADAR)" = false'
```

**Никогда не делайте `DROP`/`TRUNCATE` Radar-таблиц, не удаляйте строки
`schema_migrations`, не восстанавливайте pre-release dump поверх живой БД и не
перезаписывайте protected-данные.** Дамп предназначен для расследования и
аварийного восстановления по отдельному одобренному плану, а не для штатного
rollback.
