# Диагностика «мёртвых» источников — 2026-06-13

**Вопрос оператора:** почему так много статей/источников в статусе DEAD?

> ✅ **Статус: пофикшено в этом же PR.** 55 битых/гео-блокируемых фидов
> переведены на Google-News `site:`-обёртки (`scripts/fix_dead_feeds.py`), а в
> коллектор добавлены инструментация исхода fetch (`last_status`/`last_error`/
> `consecutive_failures`, миграция 015) и ретраи. Разбор причин ниже — для
> истории и для прогона на прод-БД.

**Метод:** `scripts/diagnose_sources.py --yaml` — живая проба всех **188**
сконфигурированных источников (`sources.yaml` + `sources_world.yaml`, 92 страны;
179 rss + 9 web) прямо из текущего окружения, с классификацией причины каждого
сбоя. Telegram-источники здесь не пробуются (их собирает отдельный
`tg-collector` по сессии Telethon).

> ⚠️ **Важная оговорка по IP.** Пробы выполнены с **датацентрового IP**. Причины
> `geoblock_or_forbidden` (403/451) и часть `http_5xx` зависят от IP: с прод-IP
> (если он тоже датацентровый/облачный) поведение будет таким же, с резидентного —
> часть откроется. А вот `http_404_gone`, `empty_or_malformed_feed`, `empty_feed`
> — **URL-уровневые, от IP не зависят** и мертвы везде.

## Итог по причинам (188 источников)

| Причина | Кол-во | IP-зависит? | Природа |
|---|---:|:--:|---|
| `feed_ok_but_quiet` | 118 | — | **Фид жив и отдаёт записи.** Не мёртв на уровне URL. |
| `geoblock_or_forbidden` (403/451) | 30 | да | Западные СМИ режут датацентровые IP. |
| `http_404_gone` (404/410) | 13 | нет | URL удалён/изменён — фид мёртв везде. |
| `empty_or_malformed_feed` | 10 | нет | HTTP 200, но это не валидный RSS/Atom. |
| `web_reachable` | 8 | — | web-тип: 200, извлечение не проверялось (нужен `--deep`). |
| `http_5xx` | 5 | частично | Сервер источника отвечает ошибкой (часть — иранские СМИ). |
| `empty_feed` | 3 | нет | Валидный фид, 0 записей. |
| `http_4xx` | 1 | нет | ICG отдаёт 415 на наш запрос. |

## Корневые причины (по убыванию влияния)

### 1. Гео-блок датацентрового IP — ~30 источников (главная причина для мира)
Крупные западные издания (NYT, Politico, Guardian, Telegraph, Le Monde, Le Figaro,
RFI, DW, Spiegel, Tagesschau, FAZ, El País, ABC/SMH, Yonhap, Arab News, Times of
Israel, The Hindu, ToI и др.) возвращают **403/451** на запросы с датацентровых
диапазонов. Если прод-коллектор работает с облачного/датацентрового IP — все эти
фиды мертвы у нас, хотя в браузере живы. Это объясняет массовую «смерть» именно
мировых mainstream-источников.

**Что характерно:** ни одна Google-News `site:`-обёртка в гео-блок не попала —
все 118 `feed_ok_but_quiet` отдают записи. Google News как прокси-адаптер
**обходит гео-блок бесплатно**.

**Лечение:** (а) заменить гео-блокируемые прямые RSS на Google-News
`site:домен+russia` обёртки; (б) для оставшихся — резидентный/ротируемый прокси
(опц. `proxy` в `sources.config` JSONB); (в) кэш-фолбэк.

### 2. Битые/протухшие URL — 26 источников (чинится сразу, не зависит от IP)
- **13 × 404/410** (`http_404_gone`): CivilNet.am, Gulf News, The National, Infobae,
  Página/12, Dhaka Tribune, SwissInfo (410), El Espectador, LRT, Montsame, El
  Universal, The Star, B92 — фид переехал/убран. Нужен новый URL.
- **10 × не-RSS** (`empty_or_malformed_feed`): Press TV, Daily Sabah, Korea Herald,
  Rudaw, Shafaq, Khaama, Brussels Times, Maliweb, Thai PBS, Últimas Noticias —
  по URL отдаётся HTML/битый XML, а не фид.
- **3 × пустой фид** (`empty_feed`): Jakarta Globe, Aristegui, Hurriyet.

**Лечение:** прогнать кандидатов через `scripts/validate_feed.py <url>` и заменить
URL; где RSS нет — перейти на Google-News `site:`-обёртку.

### 3. Хрупкий `web`-тип — 8 источников
Tengrinews, Informburo, Zakon.kz, Asia-Plus, Turkmen.news, Kun.uz, Haqqin,
Zerkalo — отдают HTTP 200, но извлечение идёт через trafilatura→Firecrawl
(`src/collectors/scraper.py`) и легко даёт 0 статей на JS-вёрстке/пейволле, если
Firecrawl недоступен. Проверяется `diagnose_sources.py --deep`.

**Лечение:** найти у этих сайтов реальный RSS (часто есть `/feed`, `/rss`) и
перевести с `web` на `rss`; иначе — мониторить доступность Firecrawl.

### 4. Telegram — отдельный риск (здесь не пробовался)
`collect.py` логирует telegram как «Unknown source type» и пропускает — их
собирает `tg-collector` (`src/collectors/telegram.py`) по сессии Telethon. При
**протухшей сессии все TG-источники замолкают разом** и через 7 дней становятся
DEAD без видимой причины. Проверять: жив ли контейнер `tg-collector` и файл
сессии в `/app/sessions`.

### 5. «Тихие, но живые» — 118 источников ≠ мёртвые
118 фидов отдают записи. Если в **прод-health** они показаны DEAD/STALE — причина
**не в источнике**, а downstream: (а) коллектор не запущен/падает; (б) наш
дедуп/парсинг дат (`collect.py` ставит `now()` при непарсимой дате → косой
cadence); (в) фильтр релевантности отбрасывает все статьи. Это надо смотреть по
`articles`/`analysis`, а не по URL.

## Почему причина не видна сейчас (и как чинить «по-взрослому»)

`scripts/collect.py` → `collect_rss`/`scrape_web` **молча возвращают `[]`** на
любой ошибке; на `sources` нет полей `last_fetch_at`/`last_error`/
`consecutive_failures`, а `health.py` выводит DEAD лишь по отсутствию статей 7+
дней. Поэтому оператор видит «DEAD», но не «почему».

**Рекомендованный фикс (следующий PR, по выбору оператора отложён):**
1. Миграция: добавить на `sources` колонки `last_fetch_at`, `last_status`
   (ok/http_4xx/geoblock/…), `last_error`, `consecutive_failures`.
2. `collect.py`/`rss.py`/`scraper.py`: на каждой попытке писать исход (по
   классификации из `diagnose_sources.py`); добавить retry/backoff на 5xx/timeout.
3. Health/API: показывать причину и `consecutive_failures` в `/api/v2/health/sources`.
4. (Опц.) авто-карантин: снимать `active` после N подряд провалов с пометкой причины.
5. Прокси-поддержка через `sources.config` JSONB для гео-блокируемых.

## Как воспроизвести / гонять на проде

```bash
# из YAML (без БД, как этот отчёт):
python scripts/diagnose_sources.py --yaml --out docs/research/dead-sources-<date>.md
# по живой БД с health-статусами (на проде через geopulse-prod):
python scripts/diagnose_sources.py            # только STALE+DEAD
python scripts/diagnose_sources.py --all --deep   # + web-извлечение
```

> Запуск на прод-БД даст реальные health-статусы (OK/STALE/DEAD) и разрез по
> странам/тирам; данный YAML-прогон показывает URL-уровневую исправность
> **сконфигурированных** фидов.
