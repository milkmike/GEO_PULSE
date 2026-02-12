-- VOX POPULI: Народная температура
-- Миграция 003: таблицы для сбора и анализа комментариев

-- ═══════════════════════════════════════════
-- 1. Комментарии (сырые данные)
-- ═══════════════════════════════════════════

CREATE TABLE IF NOT EXISTS comments (
    id              BIGSERIAL PRIMARY KEY,
    article_id      BIGINT REFERENCES articles(id) ON DELETE SET NULL,
    source_id       BIGINT REFERENCES sources(id) ON DELETE SET NULL,
    country_code    CHAR(2) NOT NULL,

    -- Платформа
    platform        VARCHAR(20) NOT NULL,        -- telegram, youtube, vk, web
    channel_id      VARCHAR(100),                -- ID канала/группы
    post_id         VARCHAR(100),                -- ID поста/сообщения к которому коммент
    comment_id_ext  VARCHAR(100),                -- внешний ID комментария (для дедупликации)

    -- Контент
    text            TEXT NOT NULL,
    language        VARCHAR(5),                  -- ru, en, kk, uz...
    author_hash     VARCHAR(64),                 -- SHA256(platform + user_id) — анонимизация

    -- Метрики платформы
    likes           INT DEFAULT 0,
    replies_count   INT DEFAULT 0,
    views           INT DEFAULT 0,

    -- Временные метки
    published_at    TIMESTAMPTZ NOT NULL,
    collected_at    TIMESTAMPTZ DEFAULT NOW(),

    -- Дедупликация
    UNIQUE(platform, channel_id, comment_id_ext)
);

CREATE INDEX IF NOT EXISTS idx_comments_country ON comments(country_code, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_comments_article ON comments(article_id) WHERE article_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_comments_platform ON comments(platform, collected_at DESC);
CREATE INDEX IF NOT EXISTS idx_comments_published ON comments(published_at DESC);

-- ═══════════════════════════════════════════
-- 2. Анализ комментариев (LLM-результаты)
-- ═══════════════════════════════════════════

CREATE TABLE IF NOT EXISTS comment_analysis (
    comment_id      BIGINT PRIMARY KEY REFERENCES comments(id) ON DELETE CASCADE,

    -- Тональность (как у статей, -3..+3)
    sentiment       NUMERIC(4,2) NOT NULL,

    -- Эмоция (более тонко чем sentiment)
    emotion         VARCHAR(20),                 -- anger, fear, joy, sadness, disgust, surprise, neutral

    -- Позиция к России
    stance_russia   VARCHAR(10),                 -- pro, neutral, anti

    -- Темы (массив)
    topics          TEXT[],

    -- Бот-детекция
    bot_score       NUMERIC(3,2) DEFAULT 0,      -- 0..1 вероятность бота

    -- Мета
    model_used      VARCHAR(50),
    analyzed_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_comment_analysis_sentiment ON comment_analysis(sentiment);

-- ═══════════════════════════════════════════
-- 3. Народная температура (агрегат)
-- ═══════════════════════════════════════════

CREATE TABLE IF NOT EXISTS vox_temperature (
    time            TIMESTAMPTZ NOT NULL,
    country_code    CHAR(2) NOT NULL,

    -- Температура народа
    temperature     NUMERIC(5,2),                -- -100..+100
    comment_count   INT DEFAULT 0,
    unique_authors  INT DEFAULT 0,

    -- Бот-фильтрация
    bot_ratio       NUMERIC(3,2) DEFAULT 0,      -- доля ботов
    clean_temperature NUMERIC(5,2),              -- температура без ботов

    -- Разрыв с элитами
    elite_gap       NUMERIC(5,2),                -- media_temp - vox_temp
    media_temperature NUMERIC(5,2),              -- медийная температура на тот момент

    -- Эмоциональный профиль
    dominant_emotion VARCHAR(20),
    pro_ratio       NUMERIC(3,2),                -- доля про-российских
    anti_ratio      NUMERIC(3,2),                -- доля анти-российских

    PRIMARY KEY (time, country_code)
);

CREATE INDEX IF NOT EXISTS idx_vox_temp_country ON vox_temperature(country_code, time DESC);

-- ═══════════════════════════════════════════
-- 4. Telegram-каналы для мониторинга
-- ═══════════════════════════════════════════

CREATE TABLE IF NOT EXISTS vox_channels (
    id              SERIAL PRIMARY KEY,
    platform        VARCHAR(20) NOT NULL DEFAULT 'telegram',
    channel_username VARCHAR(100) NOT NULL,       -- @username без @
    channel_id_ext  BIGINT,                      -- числовой ID канала
    discussion_id   BIGINT,                      -- ID группы обсуждений (linked chat)
    country_code    CHAR(2) NOT NULL,
    source_id       BIGINT REFERENCES sources(id) ON DELETE SET NULL,
    name            VARCHAR(200),
    active          BOOLEAN DEFAULT TRUE,
    last_collected  TIMESTAMPTZ,
    last_post_id    BIGINT,                      -- ID последнего обработанного поста

    UNIQUE(platform, channel_username)
);
