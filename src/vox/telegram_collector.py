"""
VOX POPULI — Telegram Comment Collector

Собирает комментарии из обсуждений Telegram-каналов.
Использует Telethon (MTProto User API) — bot API не даёт читать обсуждения.

Алгоритм:
1. Для каждого канала из vox_channels:
   a. Получить последние N постов (или новые после last_post_id)
   b. Для каждого поста получить комментарии из discussion group
   c. Попытаться связать пост с article_id (по URL/тексту)
   d. Сохранить комментарии в БД
2. Обновить last_collected и last_post_id
"""

import asyncio
import hashlib
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
log = logging.getLogger("vox-telegram")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
TELEGRAM_API_ID = int(os.environ.get("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
TELEGRAM_SESSION = os.environ.get("TELEGRAM_SESSION", "vox_session")

# Сколько постов обрабатывать за один цикл (на канал)
POSTS_PER_CHANNEL = int(os.environ.get("VOX_POSTS_PER_CHANNEL", "20"))
# Максимум комментов на пост
MAX_COMMENTS_PER_POST = int(os.environ.get("VOX_MAX_COMMENTS", "100"))
# Цикл сбора (секунды)
COLLECT_INTERVAL = int(os.environ.get("VOX_COLLECT_INTERVAL", "1800"))  # 30 мин
# Минимальная длина комментария для анализа
MIN_COMMENT_LENGTH = int(os.environ.get("VOX_MIN_COMMENT_LENGTH", "10"))


def get_db():
    return psycopg2.connect(DATABASE_URL)


def author_hash(platform: str, user_id: int | str) -> str:
    """Анонимизация автора — SHA256(platform:user_id)"""
    raw = f"{platform}:{user_id}"
    return hashlib.sha256(raw.encode()).hexdigest()


def init_channels(conn):
    """Инициализировать vox_channels из sources с tier=social и url начинающимся на t.me"""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Получаем Telegram-источники, которых ещё нет в vox_channels
    cur.execute("""
        INSERT INTO vox_channels (platform, channel_username, country_code, source_id, name)
        SELECT 'telegram',
               REPLACE(REPLACE(url, 'https://t.me/', ''), 'http://t.me/', ''),
               country_code,
               id,
               name
        FROM sources
        WHERE (url LIKE '%%t.me/%%') AND active = true
        ON CONFLICT (platform, channel_username) DO NOTHING
        RETURNING channel_username
    """)
    new = cur.fetchall()
    if new:
        log.info(f"Added {len(new)} new Telegram channels to vox_channels")
    conn.commit()


def get_active_channels(conn) -> list[dict]:
    """Получить список активных каналов для мониторинга"""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT id, channel_username, channel_id_ext, discussion_id,
               country_code, source_id, name, last_post_id
        FROM vox_channels
        WHERE active = true AND platform = 'telegram'
        ORDER BY last_collected ASC NULLS FIRST
    """)
    return cur.fetchall()


def save_comments(conn, comments: list[dict]) -> int:
    """Сохранить комментарии в БД (upsert по platform+channel+ext_id)"""
    if not comments:
        return 0

    cur = conn.cursor()
    saved = 0
    for c in comments:
        try:
            cur.execute("""
                INSERT INTO comments
                    (article_id, source_id, country_code, platform,
                     channel_id, post_id, comment_id_ext,
                     text, language, author_hash,
                     likes, replies_count, published_at)
                VALUES
                    (%(article_id)s, %(source_id)s, %(country_code)s, %(platform)s,
                     %(channel_id)s, %(post_id)s, %(comment_id_ext)s,
                     %(text)s, %(language)s, %(author_hash)s,
                     %(likes)s, %(replies_count)s, %(published_at)s)
                ON CONFLICT (platform, channel_id, comment_id_ext) DO NOTHING
            """, c)
            if cur.rowcount > 0:
                saved += 1
        except Exception as e:
            log.warning(f"Failed to save comment: {e}")
            conn.rollback()
            continue

    conn.commit()
    return saved


def update_channel_state(conn, channel_id: int, last_post_id: int | None):
    """Обновить состояние канала после сбора"""
    cur = conn.cursor()
    if last_post_id:
        cur.execute("""
            UPDATE vox_channels
            SET last_collected = NOW(), last_post_id = %s
            WHERE id = %s
        """, (last_post_id, channel_id))
    else:
        cur.execute("""
            UPDATE vox_channels
            SET last_collected = NOW()
            WHERE id = %s
        """, (channel_id,))
    conn.commit()


def find_article_id(conn, channel_username: str, post_text: str, published_at) -> int | None:
    """Попытка связать пост канала с article_id по совпадению текста/URL."""
    if not post_text:
        return None

    cur = conn.cursor()
    # Ищем статью с похожим заголовком в тот же день
    cur.execute("""
        SELECT id FROM articles
        WHERE published_at BETWEEN %s - interval '2 days' AND %s + interval '1 day'
        AND (title %% %s OR url LIKE %s)
        ORDER BY published_at DESC
        LIMIT 1
    """, (published_at, published_at, post_text[:100], f"%t.me/{channel_username}%"))
    row = cur.fetchone()
    return row[0] if row else None


async def collect_channel(client, conn, channel: dict) -> int:
    """Собрать комментарии из одного канала."""
    from telethon import functions
    from telethon.tl.types import MessageService

    username = channel["channel_username"]
    log.info(f"Collecting comments from @{username} ({channel['country_code']})...")

    try:
        entity = await client.get_entity(username)
    except Exception as e:
        log.warning(f"Cannot resolve @{username}: {e}")
        return 0

    # Проверяем/сохраняем discussion group
    discussion_id = channel.get("discussion_id")
    if not discussion_id:
        try:
            full = await client(functions.channels.GetFullChannelRequest(entity))
            if full.full_chat.linked_chat_id:
                discussion_id = full.full_chat.linked_chat_id
                cur = conn.cursor()
                cur.execute(
                    "UPDATE vox_channels SET discussion_id = %s, channel_id_ext = %s WHERE id = %s",
                    (discussion_id, entity.id, channel["id"])
                )
                conn.commit()
                log.info(f"  Found discussion group: {discussion_id}")
            else:
                log.info(f"  @{username} has no discussion group, skipping")
                update_channel_state(conn, channel["id"], None)
                return 0
        except Exception as e:
            log.warning(f"  Cannot get full channel info for @{username}: {e}")
            update_channel_state(conn, channel["id"], None)
            return 0

    # Получаем последние посты
    last_post_id = channel.get("last_post_id") or 0
    total_comments = 0
    newest_post_id = last_post_id

    async for post in client.iter_messages(entity, limit=POSTS_PER_CHANNEL):
        if isinstance(post, MessageService):
            continue
        if post.id <= last_post_id:
            break

        newest_post_id = max(newest_post_id, post.id)
        post_text = post.text or ""

        if not post.replies or not post.replies.replies:
            continue

        # Попытка привязать к article
        article_id = find_article_id(conn, username, post_text, post.date)

        # Собираем комментарии к посту
        comments_batch = []
        try:
            async for reply in client.iter_messages(
                discussion_id,
                reply_to=post.id,
                limit=MAX_COMMENTS_PER_POST
            ):
                if isinstance(reply, MessageService):
                    continue
                text = reply.text or ""
                if len(text) < MIN_COMMENT_LENGTH:
                    continue

                comments_batch.append({
                    "article_id": article_id,
                    "source_id": channel.get("source_id"),
                    "country_code": channel["country_code"],
                    "platform": "telegram",
                    "channel_id": str(entity.id),
                    "post_id": str(post.id),
                    "comment_id_ext": str(reply.id),
                    "text": text,
                    "language": None,  # будет определен анализатором
                    "author_hash": author_hash("telegram", reply.sender_id or 0),
                    "likes": reply.forwards or 0,
                    "replies_count": (reply.replies.replies if reply.replies else 0),
                    "published_at": reply.date,
                })
        except Exception as e:
            log.warning(f"  Error reading replies for post {post.id}: {e}")
            continue

        if comments_batch:
            saved = save_comments(conn, comments_batch)
            total_comments += saved
            if saved:
                log.info(f"  Post {post.id}: {saved}/{len(comments_batch)} new comments")

    update_channel_state(conn, channel["id"], newest_post_id if newest_post_id > last_post_id else None)
    return total_comments


async def run_collection():
    """Основной цикл сбора."""
    from telethon import TelegramClient

    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        log.error("TELEGRAM_API_ID and TELEGRAM_API_HASH are required!")
        return

    conn = get_db()
    init_channels(conn)

    client = TelegramClient(TELEGRAM_SESSION, TELEGRAM_API_ID, TELEGRAM_API_HASH)
    await client.start()

    channels = get_active_channels(conn)
    log.info(f"═══ VOX POPULI: collecting from {len(channels)} Telegram channels ═══")

    total = 0
    for ch in channels:
        try:
            n = await collect_channel(client, conn, ch)
            total += n
        except Exception as e:
            log.error(f"Error collecting @{ch['channel_username']}: {e}")
        # Rate limiting — пауза между каналами
        await asyncio.sleep(2)

    log.info(f"═══ Collection complete: {total} new comments ═══")
    await client.disconnect()
    conn.close()


def main():
    while True:
        try:
            asyncio.run(run_collection())
        except Exception as e:
            log.error(f"Collection cycle failed: {e}")

        log.info(f"Sleeping {COLLECT_INTERVAL}s...")
        time.sleep(COLLECT_INTERVAL)


if __name__ == "__main__":
    main()
