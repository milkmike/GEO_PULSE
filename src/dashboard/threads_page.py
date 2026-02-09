"""🧵 Narrative Threads tab for GEO PULSE dashboard."""
import os
from datetime import datetime, timedelta

import httpx
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000")

# Country emoji flags
COUNTRY_FLAGS = {
    "KZ": "🇰🇿", "AM": "🇦🇲", "UZ": "🇺🇿", "KG": "🇰🇬", "TJ": "🇹🇯",
    "TM": "🇹🇲", "AZ": "🇦🇿", "GE": "🇬🇪", "MD": "🇲🇩", "BY": "🇧🇾",
}

COUNTRY_NAMES_EMOJI = {
    "KZ": "🇰🇿 Казахстан", "AM": "🇦🇲 Армения", "UZ": "🇺🇿 Узбекистан",
    "KG": "🇰🇬 Кыргызстан", "TJ": "🇹🇯 Таджикистан", "TM": "🇹🇲 Туркменистан",
    "AZ": "🇦🇿 Азербайджан", "GE": "🇬🇪 Грузия", "MD": "🇲🇩 Молдова", "BY": "🇧🇾 Беларусь",
}

ARC_PHASE_CONFIG = {
    "emerging": {"label": "🌱 Emerging", "color": "#3b82f6", "bg": "rgba(59,130,246,0.1)"},
    "escalating": {"label": "📈 Escalating", "color": "#f59e0b", "bg": "rgba(245,158,11,0.1)"},
    "peak": {"label": "🔥 Peak", "color": "#ef4444", "bg": "rgba(239,68,68,0.1)"},
    "cooling": {"label": "❄️ Cooling", "color": "#06b6d4", "bg": "rgba(6,182,212,0.1)"},
    "resolved": {"label": "✅ Resolved", "color": "#22c55e", "bg": "rgba(34,197,94,0.1)"},
}

STATUS_CONFIG = {
    "developing": {"label": "🔄 Развивается", "color": "#3b82f6"},
    "resolved": {"label": "✅ Завершён", "color": "#22c55e"},
    "dormant": {"label": "💤 Неактивен", "color": "#71717a"},
}

ACTION_DISPLAY = {
    1: "⚡", 2: "⚡⚡", 3: "⚡⚡⚡", 4: "💥", 5: "💥💥", 6: "💥💥💥",
}


def _api_get(endpoint: str, params: dict = None) -> dict | None:
    try:
        r = httpx.get(f"{API_URL}{endpoint}", params=params, timeout=15.0)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Ошибка API: {e}")
        return None


def _sentiment_color(val: float | None) -> str:
    if val is None:
        return "#52525b"
    if val > 0.5:
        return "#22c55e"
    elif val > -0.5:
        return "#fbbf24"
    else:
        return "#ef4444"


def _render_arc_bar(phase: str) -> str:
    """Render a colored arc phase progress bar."""
    phases = ["emerging", "escalating", "peak", "cooling", "resolved"]
    current_idx = phases.index(phase) if phase in phases else 0

    segments = []
    for i, p in enumerate(phases):
        cfg = ARC_PHASE_CONFIG[p]
        opacity = "1.0" if i <= current_idx else "0.15"
        segments.append(
            f'<div style="flex:1;height:6px;background:{cfg["color"]};opacity:{opacity};'
            f'border-radius:{"3px 0 0 3px" if i == 0 else "0 3px 3px 0" if i == len(phases)-1 else "0"};"></div>'
        )

    return f'<div style="display:flex;gap:2px;margin:8px 0;">{"".join(segments)}</div>'


def _render_timeline_dots(articles_data: list[dict]) -> str:
    """Render timeline dots visualization."""
    if not articles_data:
        return ""

    dates = []
    for a in articles_data:
        if a.get("published_at"):
            try:
                dt = datetime.fromisoformat(a["published_at"].replace("Z", "+00:00"))
                dates.append(dt)
            except (ValueError, TypeError):
                pass

    if not dates:
        return ""

    min_date = min(dates)
    max_date = max(dates)
    span = (max_date - min_date).total_seconds()
    if span <= 0:
        span = 1

    # Create dot positions
    dots = []
    for dt in dates:
        pct = ((dt - min_date).total_seconds() / span) * 100
        dots.append(f'<div style="position:absolute;left:{pct}%;top:50%;transform:translate(-50%,-50%);'
                    f'width:8px;height:8px;background:#3b82f6;border-radius:50%;"></div>')

    date_labels = (
        f'<span style="color:#52525b;font-size:0.7rem;">{min_date:%b %d}</span>'
        f'<span style="color:#52525b;font-size:0.7rem;">{max_date:%b %d}</span>'
    )

    return f'''
    <div style="position:relative;height:16px;background:rgba(255,255,255,0.04);border-radius:8px;margin:8px 0;">
        {"".join(dots)}
    </div>
    <div style="display:flex;justify-content:space-between;">{date_labels}</div>
    '''


def _render_thread_card(thread: dict, expanded_key: str | None = None) -> None:
    """Render a single thread card."""
    cc = thread.get("country_code", "").strip()
    flag = COUNTRY_FLAGS.get(cc, "🏳️")
    title = thread.get("title", thread.get("thread_key", "—"))
    phase = thread.get("arc_phase", "emerging")
    phase_cfg = ARC_PHASE_CONFIG.get(phase, ARC_PHASE_CONFIG["emerging"])
    status = thread.get("status", "developing")
    status_cfg = STATUS_CONFIG.get(status, STATUS_CONFIG["developing"])
    sentiment = thread.get("avg_sentiment")
    sent_color = _sentiment_color(sentiment)
    sent_str = f"{sentiment:+.2f}" if sentiment is not None else "—"
    action_level = thread.get("max_action_level", 1)
    action_str = ACTION_DISPLAY.get(action_level, "⚡")
    article_count = thread.get("article_count", 0)
    importance = thread.get("importance_score", 0)
    narrative = thread.get("narrative", "")
    first_seen = thread.get("first_seen", "")
    last_seen = thread.get("last_seen", "")
    thread_id = thread.get("id")

    # Truncate title if needed
    if len(title) > 120:
        title = title[:117] + "..."

    # Format dates
    first_str = first_seen[:10] if first_seen else "—"
    last_str = last_seen[:10] if last_seen else "—"

    # Importance badge color
    if importance >= 20:
        imp_color = "#ef4444"
    elif importance >= 10:
        imp_color = "#f59e0b"
    else:
        imp_color = "#3b82f6"

    # Country name
    country_name = thread.get("country_name", cc)

    # Narrative block
    narrative_html = ""
    if narrative:
        narrative_html = (
            f'<div style="color:rgba(255,255,255,0.6);font-size:0.88rem;line-height:1.6;'
            f'margin:12px 0;padding:12px 16px;background:rgba(255,255,255,0.02);'
            f'border-radius:8px;border-left:2px solid {phase_cfg["color"]};">'
            f'📝 {narrative}</div>'
        )

    # Use st.html for reliable HTML rendering
    card_html = f'''
    <div style="background:linear-gradient(135deg,rgba(255,255,255,0.04) 0%,rgba(255,255,255,0.01) 100%);
                border:1px solid rgba(255,255,255,0.07);border-radius:16px;padding:20px;margin:10px 0;
                font-family:Inter,-apple-system,BlinkMacSystemFont,system-ui,sans-serif;">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">
            <div style="display:flex;align-items:center;gap:8px;">
                <span style="font-size:1.3rem;">{flag}</span>
                <span style="font-size:0.78rem;color:#71717a;">{country_name}</span>
            </div>
            <div style="display:flex;align-items:center;gap:8px;">
                <span style="background:{phase_cfg['bg']};color:{phase_cfg['color']};
                       padding:3px 10px;border-radius:12px;font-size:0.75rem;font-weight:600;">
                    {phase_cfg['label']}
                </span>
                <span style="background:rgba(255,255,255,0.04);color:{imp_color};
                       padding:3px 8px;border-radius:8px;font-size:0.72rem;font-weight:600;">
                    ★ {importance:.0f}
                </span>
            </div>
        </div>

        <div style="font-size:1.05rem;font-weight:600;color:#e4e4e7;margin-bottom:10px;line-height:1.4;">
            {title}
        </div>

        {_render_arc_bar(phase)}

        <div style="display:flex;gap:14px;font-size:0.8rem;color:#71717a;margin:10px 0;flex-wrap:wrap;">
            <span>{action_str} Action {action_level}</span>
            <span>📰 {article_count} статей</span>
            <span style="color:{sent_color};">💬 {sent_str}</span>
            <span>📅 {first_str} — {last_str}</span>
        </div>

        {narrative_html}
    </div>
    '''
    st.html(card_html)

    # Expandable timeline
    if thread_id:
        with st.expander(f"📋 Хронология ({article_count} статей)", expanded=False):
            detail = _api_get(f"/api/v1/threads/{thread_id}")
            if detail and detail.get("timeline"):
                for a in detail["timeline"]:
                    a_date = a.get("published_at", "")[:16].replace("T", " ") if a.get("published_at") else "—"
                    a_sent = a.get("sentiment")
                    a_sent_color = _sentiment_color(a_sent)
                    a_sent_str = f"{a_sent:+.1f}" if a_sent is not None else "—"
                    a_al = a.get("action_level", 1)
                    a_al_str = ACTION_DISPLAY.get(a_al, "⚡")
                    a_url = a.get("url", "")
                    link_html = f' <a href="{a_url}" target="_blank" style="color:#3b82f6;text-decoration:none;font-size:0.78rem;">→</a>' if a_url else ""

                    st.markdown(f'''
                    <div style="padding:8px 12px;border-bottom:1px solid rgba(255,255,255,0.04);
                                display:flex;align-items:center;gap:12px;">
                        <div style="min-width:110px;font-size:0.78rem;color:#52525b;">{a_date}</div>
                        <div style="flex:1;font-size:0.88rem;color:#d4d4d8;">
                            {a.get("title", "—")[:100]}{link_html}
                        </div>
                        <div style="display:flex;align-items:center;gap:8px;min-width:100px;justify-content:flex-end;">
                            <span style="font-size:0.75rem;color:#52525b;">{a_al_str}</span>
                            <span style="font-weight:600;color:{a_sent_color};font-size:0.85rem;">{a_sent_str}</span>
                        </div>
                    </div>
                    ''', unsafe_allow_html=True)
            else:
                st.info("Нет данных о статьях.")


def render_threads_page():
    """Main render function for the Threads tab."""
    st.title("🧵 Сюжетные нити")
    st.markdown(
        '<p style="color:#71717a;font-size:1.05rem;margin-top:-10px;">'
        'Кластеры связанных событий: как развиваются ключевые сюжеты в медиапространстве СНГ</p>',
        unsafe_allow_html=True,
    )

    # ── Filters ──
    col_f1, col_f2, col_f3 = st.columns([2, 1, 1])
    with col_f1:
        filter_countries = st.multiselect(
            "Страна",
            list(COUNTRY_NAMES_EMOJI.keys()),
            format_func=lambda x: COUNTRY_NAMES_EMOJI[x],
            key="threads_country_filter",
        )
    with col_f2:
        filter_status = st.selectbox(
            "Статус",
            [None, "developing", "resolved", "dormant"],
            format_func=lambda x: {"developing": "🔄 Развивается", "resolved": "✅ Завершён",
                                    "dormant": "💤 Неактивен", None: "Все"}[x],
            key="threads_status_filter",
        )
    with col_f3:
        filter_limit = st.selectbox("Показать", [10, 20, 50], index=1, key="threads_limit_filter")

    # ── Fetch threads ──
    params = {"limit": filter_limit}
    if filter_status:
        params["status"] = filter_status

    if filter_countries and len(filter_countries) == 1:
        # Use country-specific endpoint
        data = _api_get(f"/api/v1/countries/{filter_countries[0]}/threads", params)
        threads = data.get("threads", []) if data else []
    else:
        data = _api_get("/api/v1/threads", params)
        threads = data.get("threads", []) if data else []
        # Client-side country filter for multi-select
        if filter_countries:
            threads = [t for t in threads if t.get("country_code", "").strip() in filter_countries]

    if not threads:
        st.markdown('''
        <div style="text-align:center;padding:60px 20px;color:#52525b;">
            <div style="font-size:3rem;margin-bottom:16px;">🧵</div>
            <div style="font-size:1.1rem;">Сюжетных нитей пока нет</div>
            <div style="font-size:0.85rem;margin-top:8px;">Система автоматически кластеризует события каждый час</div>
        </div>
        ''', unsafe_allow_html=True)
        return

    # ── Summary metrics ──
    developing_count = sum(1 for t in threads if t.get("status") == "developing")
    total_articles = sum(t.get("article_count", 0) for t in threads)
    avg_importance = sum(t.get("importance_score", 0) for t in threads) / len(threads) if threads else 0

    st.markdown(f'''
    <div class="metrics-row">
        <div class="metric-box">
            <div class="metric-value">{len(threads)}</div>
            <div class="metric-label">🧵 Сюжетов</div>
        </div>
        <div class="metric-box">
            <div class="metric-value" style="color:#3b82f6;">{developing_count}</div>
            <div class="metric-label">🔄 Активных</div>
        </div>
        <div class="metric-box">
            <div class="metric-value">{total_articles}</div>
            <div class="metric-label">📰 Статей</div>
        </div>
        <div class="metric-box">
            <div class="metric-value" style="color:#f59e0b;">{avg_importance:.0f}</div>
            <div class="metric-label">★ Ср. важность</div>
        </div>
    </div>
    ''', unsafe_allow_html=True)

    st.markdown('<div style="border-bottom:1px solid rgba(255,255,255,0.06);margin:16px 0;"></div>',
                unsafe_allow_html=True)

    # ── Render thread cards ──
    for thread in threads:
        _render_thread_card(thread)
