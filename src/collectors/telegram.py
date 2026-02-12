"""
Telegram Article Collector

Собирает посты из Telegram-каналов как статьи для Geo Pulse.
Использует Telethon (MTProto User API) для чтения каналов.

Два режима:
1. Backfill — собрать последние N постов из каждого канала
2. Live — слушать новые посты в реальном времени

Преимущества над RSS:
- Мгновенная доставка (vs 30 мин RSS цикл)
- Полный текст поста
- Реакции и просмотры
- Каналы без RSS-фидов
"""

import asyncio
import logging
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient, events
from telethon.tl.types import Channel, MessageMediaWebPage

import psycopg2
import psycopg2.extras

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("tg-collector")

# ─── Config ───────────────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "")
TELEGRAM_API_ID = int(os.environ.get("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
TELEGRAM_SESSION = os.environ.get("TELEGRAM_SESSION", "geopulse_vox")
SESSION_PATH = os.environ.get("TG_SESSION_PATH", "/app/sessions")

# Сколько постов при backfill
BACKFILL_LIMIT = int(os.environ.get("TG_BACKFILL_LIMIT", "50"))
# Минимальная длина поста для сохранения
MIN_POST_LENGTH = int(os.environ.get("TG_MIN_POST_LENGTH", "80"))
# Цикл backfill (секунды)
COLLECT_INTERVAL = int(os.environ.get("TG_COLLECT_INTERVAL", "900"))  # 15 мин
# Режим: "live" или "poll"
MODE = os.environ.get("TG_MODE", "live")

# ─── Language detection ───────────────────────────────────────────
_CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")
_LATIN_RE = re.compile(r"[A-Za-zÀ-ÿ]")
_RO_CHARS = set("ăâîșțĂÂÎȘȚ")
_UZ_MARKERS = re.compile(r"(o'z|O'z|bilan|haqida|uchun|bo'yicha)", re.IGNORECASE)
_TK_MARKERS = re.compile(r"[ňžäýöüŇŽÄÝÖÜ]|(barada|döwlet|türkmen)", re.IGNORECASE)


def detect_language(text: str) -> str:
    if not text or len(text.strip()) < 3:
        return "ru"
    cyrillic = len(_CYRILLIC_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    total = cyrillic + latin
    if total == 0:
        return "ru"
    if latin / total >= 0.5 and cyrillic <= 2:
        if any(c in _RO_CHARS for c in text):
            return "ro"
        if _UZ_MARKERS.search(text):
            return "uz"
        if _TK_MARKERS.search(text):
            return "tk"
        return "en"
    return "ru"


# ─── Title extraction ────────────────────────────────────────────
def extract_title(text: str, max_len: int = 200) -> str:
    """Extract title from TG post: first line or first sentence."""
    if not text:
        return ""
    # First non-empty line
    for line in text.split("\n"):
        line = line.strip()
        if len(line) > 10:
            # Remove markdown-like formatting
            line = re.sub(r"[*_~`]", "", line)
            if len(line) > max_len:
                # Cut at sentence boundary
                for sep in (". ", "! ", "? ", "— ", ": "):
                    idx = line.find(sep, 40)
                    if 40 < idx < max_len:
                        return line[: idx + 1].strip()
                return line[:max_len].strip() + "…"
            return line
    return text[:max_len].strip()


def normalize_title(title: str) -> str:
    """Normalize title for dedup matching."""
    t = title.lower().strip()
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


# ─── URL extraction ──────────────────────────────────────────────
def extract_url(message) -> str | None:
    """Extract URL from TG message: web preview or first link in text."""
    # Web page preview
    if message.media and isinstance(message.media, MessageMediaWebPage):
        wp = message.media.webpage
        if hasattr(wp, "url") and wp.url:
            return wp.url

    # URLs in text entities
    if message.entities:
        for ent in message.entities:
            if hasattr(ent, "url") and ent.url:
                return ent.url

    # Regex fallback
    if message.text:
        urls = re.findall(r"https?://[^\s<>\"']+", message.text)
        if urls:
            return urls[0]

    return None


# ─── Database ────────────────────────────────────────────────────
def get_db():
    return psycopg2.connect(DATABASE_URL)


def get_tg_sources(conn) -> list[dict]:
    """Get active Telegram sources from DB."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT id, name, url, country_code, weight, language, config
        FROM sources
        WHERE source_type = 'telegram' AND active = true
    """)
    sources = cur.fetchall()
    # Extract username from URL
    for s in sources:
        username = s["url"].replace("https://t.me/", "").replace("http://t.me/", "").strip("/")
        s["username"] = username
    return sources


def get_last_external_id(conn, source_id: int) -> int | None:
    """Get the latest message ID we have for this source."""
    cur = conn.cursor()
    cur.execute("""
        SELECT external_id FROM articles
        WHERE source_id = %s AND external_id IS NOT NULL
        ORDER BY published_at DESC LIMIT 1
    """, (source_id,))
    row = cur.fetchone()
    if row and row[0]:
        try:
            return int(row[0])
        except (ValueError, TypeError):
            return None
    return None


def save_article(conn, source_id: int, message_id: int, title: str,
                 body: str, url: str | None, published_at: datetime,
                 language: str, views: int | None = None,
                 reactions: dict | None = None) -> int | None:
    """Save TG post as article. Returns article ID or None if duplicate."""
    external_id = str(message_id)
    title_norm = normalize_title(title)

    cur = conn.cursor()

    # Check exact duplicate (same source + message_id)
    cur.execute(
        "SELECT id FROM articles WHERE source_id = %s AND external_id = %s",
        (source_id, external_id),
    )
    if cur.fetchone():
        return None  # Already exists

    # Check fuzzy title duplicate (same day, similar title)
    if title_norm and len(title_norm) > 20:
        cur.execute("""
            SELECT id FROM articles
            WHERE published_at > %s AND published_at < %s
              AND title_normalized = %s AND source_id != %s
            LIMIT 1
        """, (
            published_at - timedelta(hours=24),
            published_at + timedelta(hours=24),
            title_norm,
            source_id,
        ))
        dup = cur.fetchone()
        is_dup = dup is not None
        dup_of = dup[0] if dup else None
    else:
        is_dup = False
        dup_of = None

    # Build metadata JSON
    meta = {}
    if views:
        meta["views"] = views
    if reactions:
        meta["reactions"] = reactions

    cur.execute("""
        INSERT INTO articles (source_id, external_id, title, body, url,
                              published_at, language, title_normalized,
                              is_duplicate, duplicate_of, is_backfill)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, false)
        ON CONFLICT (source_id, external_id) DO NOTHING
        RETURNING id
    """, (
        source_id, external_id, title, body, url,
        published_at, language, title_norm,
        is_dup, dup_of,
    ))

    row = cur.fetchone()
    conn.commit()

    if row:
        log.info(f"  💾 Saved article #{row[0]}: {title[:60]}...")
        return row[0]
    return None


# ─── Redis queue (optional) ──────────────────────────────────────
_redis = None

def enqueue_article(article_id: int, country_code: str):
    """Enqueue to analysis pipeline if Redis available."""
    global _redis
    try:
        if _redis is None:
            import redis
            redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
            _redis = redis.from_url(redis_url)
        import json
        _redis.lpush(
            "q:raw_articles",
            json.dumps({"article_id": article_id, "country_code": country_code}),
        )
    except Exception as e:
        log.debug(f"Redis enqueue failed (non-critical): {e}")


# ─── Telethon Client ─────────────────────────────────────────────
def make_client() -> TelegramClient:
    session_file = f"{SESSION_PATH}/{TELEGRAM_SESSION}"
    return TelegramClient(session_file, TELEGRAM_API_ID, TELEGRAM_API_HASH)


# ─── Collect (poll mode) ─────────────────────────────────────────
async def collect_channel(client: TelegramClient, source: dict, conn) -> int:
    """Collect recent posts from one channel. Returns count of new articles."""
    username = source["username"]
    source_id = source["id"]
    country_code = source["country_code"].strip()
    saved = 0

    try:
        entity = await client.get_entity(username)
    except Exception as e:
        log.warning(f"  ❌ Cannot resolve @{username}: {e}")
        return 0

    # Get last known message ID
    last_id = get_last_external_id(conn, source_id)
    min_id = last_id if last_id else 0

    try:
        messages = await client.get_messages(
            entity,
            limit=BACKFILL_LIMIT,
            min_id=min_id,
        )
    except Exception as e:
        log.warning(f"  ❌ Cannot get messages from @{username}: {e}")
        return 0

    for msg in reversed(messages):  # oldest first
        if not msg.text or len(msg.text) < MIN_POST_LENGTH:
            continue

        # Skip service messages, forwards from other channels (optional)
        if msg.fwd_from:
            continue

        title = extract_title(msg.text)
        body = msg.text
        url = extract_url(msg) or f"https://t.me/{username}/{msg.id}"
        published = msg.date.astimezone(timezone.utc) if msg.date else datetime.now(timezone.utc)
        lang = detect_language(msg.text)
        views = msg.views

        # Extract reactions
        reactions = {}
        if msg.reactions and msg.reactions.results:
            for r in msg.reactions.results:
                emoji = getattr(r.reaction, "emoticon", None) or str(r.reaction)
                reactions[emoji] = r.count

        article_id = save_article(
            conn, source_id, msg.id, title, body, url,
            published, lang, views, reactions,
        )
        if article_id:
            enqueue_article(article_id, country_code)
            saved += 1

    return saved


async def poll_loop(client: TelegramClient):
    """Poll all channels periodically."""
    while True:
        conn = get_db()
        sources = get_tg_sources(conn)
        log.info(f"📡 Polling {len(sources)} Telegram channels...")

        total_saved = 0
        for src in sources:
            log.info(f"  📌 @{src['username']} ({src['country_code'].strip()})...")
            try:
                n = await collect_channel(client, src, conn)
                total_saved += n
                await asyncio.sleep(2)  # Rate limit
            except Exception as e:
                log.error(f"  ❌ Error collecting @{src['username']}: {e}")
                await asyncio.sleep(5)

        conn.close()
        log.info(f"✅ Poll complete: {total_saved} new articles from {len(sources)} channels")
        log.info(f"💤 Sleeping {COLLECT_INTERVAL}s...")
        await asyncio.sleep(COLLECT_INTERVAL)


# ─── Live mode (event-based) ─────────────────────────────────────
async def live_listener(client: TelegramClient):
    """Listen for new posts in real-time via event handler."""
    conn = get_db()
    sources = get_tg_sources(conn)

    # Build username → source mapping
    source_map = {}
    channel_entities = []
    for src in sources:
        source_map[src["username"].lower()] = src
        try:
            entity = await client.get_entity(src["username"])
            channel_entities.append(entity)
            source_map[str(entity.id)] = src
            log.info(f"  ✅ Watching @{src['username']} (id={entity.id})")
            await asyncio.sleep(0.5)
        except Exception as e:
            log.warning(f"  ❌ Cannot resolve @{src['username']}: {e}")

    conn.close()
    log.info(f"👁️ Live mode: watching {len(channel_entities)} channels")

    @client.on(events.NewMessage(chats=channel_entities))
    async def handler(event):
        msg = event.message
        if not msg.text or len(msg.text) < MIN_POST_LENGTH:
            return
        if msg.fwd_from:
            return

        # Find source
        chat = await event.get_chat()
        username = getattr(chat, "username", "") or ""
        src = source_map.get(username.lower()) or source_map.get(str(chat.id))
        if not src:
            log.warning(f"Unknown channel: @{username} (id={chat.id})")
            return

        title = extract_title(msg.text)
        body = msg.text
        url = extract_url(msg) or f"https://t.me/{username}/{msg.id}"
        published = msg.date.astimezone(timezone.utc) if msg.date else datetime.now(timezone.utc)
        lang = detect_language(msg.text)
        views = msg.views
        reactions = {}
        if msg.reactions and msg.reactions.results:
            for r in msg.reactions.results:
                emoji = getattr(r.reaction, "emoticon", None) or str(r.reaction)
                reactions[emoji] = r.count

        db = get_db()
        try:
            article_id = save_article(
                db, src["id"], msg.id, title, body, url,
                published, lang, views, reactions,
            )
            if article_id:
                enqueue_article(article_id, src["country_code"].strip())
                log.info(f"⚡ LIVE @{username}: {title[:60]}")
        finally:
            db.close()

    # Also do initial backfill
    log.info("📦 Running initial backfill before going live...")
    conn = get_db()
    for src in sources:
        try:
            n = await collect_channel(client, src, conn)
            if n:
                log.info(f"  📦 Backfill @{src['username']}: {n} articles")
            await asyncio.sleep(2)
        except Exception as e:
            log.error(f"  ❌ Backfill @{src['username']}: {e}")
    conn.close()
    log.info("🚀 Backfill done. Listening for live posts...")

    # Keep running
    await client.run_until_disconnected()


# ─── Main ────────────────────────────────────────────────────────
async def main():
    log.info("🚀 Telegram Article Collector starting...")
    log.info(f"   Mode: {MODE}")
    log.info(f"   Session: {SESSION_PATH}/{TELEGRAM_SESSION}")

    client = make_client()
    await client.start()
    me = await client.get_me()
    log.info(f"   Logged in as: {me.first_name} (id={me.id})")

    if MODE == "live":
        await live_listener(client)
    else:
        await poll_loop(client)


if __name__ == "__main__":
    asyncio.run(main())
