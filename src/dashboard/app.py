"""CIS Thermometer — Streamlit Dashboard (Premium UI)."""
import os
import time
from datetime import datetime, timezone, timedelta

import httpx
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000")

st.set_page_config(
    page_title="🌡️ CIS Термометр",
    page_icon="🌡️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─── Premium CSS injection ───
st.markdown('''
<style>
/* === RESET & TYPOGRAPHY === */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

[data-testid="stAppViewContainer"],
[data-testid="stAppViewContainer"] * {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, system-ui, sans-serif !important;
}
[data-testid="stAppViewContainer"] {
    background: #0a0a0f !important;
    color: #e4e4e7 !important;
}

/* Hide Streamlit branding */
#MainMenu, footer, header {visibility: hidden !important;}
.stDeployButton {display: none !important;}
[data-testid="stToolbar"] {display: none !important;}

/* === MAIN CONTENT === */
.block-container {
    padding-top: 2rem !important;
    max-width: 1200px !important;
}

/* === SIDEBAR === */
[data-testid="stSidebar"] {
    background: #111118 !important;
    border-right: 1px solid rgba(255,255,255,0.06) !important;
}
[data-testid="stSidebar"] [data-testid="stMarkdown"] p {
    color: #a1a1aa !important;
}

/* === HEADINGS === */
h1 {
    font-weight: 700 !important;
    letter-spacing: -0.03em !important;
    font-size: 2.2rem !important;
    color: #f4f4f5 !important;
}
h2 {
    font-weight: 600 !important;
    letter-spacing: -0.02em !important;
    font-size: 1.5rem !important;
    color: #a1a1aa !important;
}
h3 {
    font-weight: 600 !important;
    letter-spacing: -0.01em !important;
    font-size: 1.2rem !important;
    color: #d4d4d8 !important;
}

/* === CARDS === */
.country-card {
    background: linear-gradient(135deg, rgba(255,255,255,0.04) 0%, rgba(255,255,255,0.01) 100%);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 12px;
    padding: 16px;
    margin: 4px 0;
    transition: all 0.3s cubic-bezier(.4,0,.2,1);
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
}
.country-card:hover {
    border-color: rgba(255,255,255,0.14);
    transform: translateY(-2px);
    box-shadow: 0 8px 32px rgba(0,0,0,0.4);
}

/* Temperature display */
.temp-value {
    font-size: 2.2rem;
    font-weight: 700;
    letter-spacing: -0.04em;
    line-height: 1;
    margin: 4px 0;
}
.temp-label {
    font-size: 0.85rem;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    opacity: 0.6;
}
.trend-badge {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 0.8rem;
    font-weight: 600;
}

/* === DIGEST CARDS === */
.digest-card {
    background: rgba(255,255,255,0.02);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 12px;
    padding: 20px;
    margin: 10px 0;
    line-height: 1.7;
    font-size: 0.95rem;
    transition: all 0.25s ease;
}
.digest-card:hover {
    background: rgba(255,255,255,0.035);
}
.digest-card .country-name {
    font-weight: 600;
    font-size: 1.1rem;
    margin-bottom: 8px;
    color: #e4e4e7;
}

/* === METRICS ROW === */
.metrics-row {
    display: flex;
    gap: 10px;
    margin: 10px 0;
}
.metric-box {
    flex: 1;
    background: linear-gradient(135deg, rgba(255,255,255,0.04) 0%, rgba(255,255,255,0.015) 100%);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 10px;
    padding: 14px 12px;
    text-align: center;
    transition: all 0.25s ease;
}
.metric-box:hover {
    border-color: rgba(255,255,255,0.12);
    transform: translateY(-1px);
}
.metric-value {
    font-size: 1.5rem;
    font-weight: 700;
    letter-spacing: -0.03em;
    color: #f4f4f5;
}
.metric-label {
    font-size: 0.78rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    opacity: 0.45;
    margin-top: 6px;
    color: #a1a1aa;
}

/* === EVENT FEED === */
.event-item {
    padding: 16px 20px;
    border-bottom: 1px solid rgba(255,255,255,0.04);
    display: flex;
    align-items: center;
    gap: 16px;
    transition: background 0.2s;
    border-radius: 10px;
    margin: 2px 0;
}
.event-item:hover {
    background: rgba(255,255,255,0.025);
}
.event-badge {
    min-width: 48px;
    height: 48px;
    border-radius: 12px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1.2rem;
}

/* KEY EVENT */
.key-event {
    background: rgba(255,255,255,0.02);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 12px;
    padding: 16px 20px;
    margin: 8px 0;
    transition: all 0.25s ease;
}
.key-event:hover {
    background: rgba(255,255,255,0.035);
    border-color: rgba(255,255,255,0.1);
}

/* === PLOTLY CHARTS === */
.js-plotly-plot .plotly .modebar { display: none !important; }

/* === SCROLLBAR === */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,0.18); }

/* === RADIO BUTTONS === */
.stRadio > div { gap: 2px !important; }
.stRadio > div > label {
    margin: 0 !important;
    transition: all 0.2s !important;
    font-size: 0.82rem !important;
}

/* === NAV TABS (hide radio circles, show as clean tabs) === */
/* Hide the radio circle (first div child of label) */
.stRadio > div > label[data-baseweb="radio"] > div:first-child {
    display: none !important;
}
/* Hide the actual radio input */
.stRadio > div > label[data-baseweb="radio"] > input {
    display: none !important;
}
/* Active tab underline */
.stRadio > div > label[data-baseweb="radio"][class*="st-ag"]:has(input:checked) {
    border-bottom: 2px solid #3b82f6 !important;
    border-radius: 0 !important;
    background: transparent !important;
    border-top: none !important;
    border-left: none !important;
    border-right: none !important;
    color: #f4f4f5 !important;
    padding: 6px 14px !important;
}
.stRadio > div > label[data-baseweb="radio"]:not(:has(input:checked)) {
    border: none !important;
    background: transparent !important;
    border-bottom: 2px solid transparent !important;
    border-radius: 0 !important;
    padding: 6px 14px !important;
    color: #71717a !important;
}
.stRadio > div > label[data-baseweb="radio"]:not(:has(input:checked)):hover {
    color: #a1a1aa !important;
    border-bottom: 2px solid rgba(255,255,255,0.1) !important;
}

/* === PERIOD PILLS === */
.period-pills {
    display: flex;
    gap: 6px;
    align-items: center;
}
.period-pill {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 0.78rem;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s;
    border: 1px solid rgba(255,255,255,0.08);
    background: rgba(255,255,255,0.03);
    color: #71717a;
    text-decoration: none;
}
.period-pill:hover {
    background: rgba(255,255,255,0.06);
    color: #a1a1aa;
}
.period-pill.active {
    background: rgba(59,130,246,0.15);
    border-color: rgba(59,130,246,0.4);
    color: #93c5fd;
    font-weight: 600;
}

/* === RESPONSIVE LAYOUT === */
.block-container {
    max-width: 1600px !important;
}

/* Two-column layout helper */
.row-layout {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin: 16px 0;
}
@media (max-width: 768px) {
    .row-layout {
        grid-template-columns: 1fr;
    }
}

/* === BUTTONS (pill-style) === */
.stButton > button {
    background: rgba(255,255,255,0.03) !important;
    border: 1px solid rgba(255,255,255,0.08) !important;
    color: #71717a !important;
    border-radius: 20px !important;
    font-size: 0.75rem !important;
    font-weight: 500 !important;
    padding: 4px 14px !important;
    transition: all 0.2s !important;
    margin-top: 0 !important;
    min-height: unset !important;
    line-height: 1.4 !important;
}
.stButton > button:hover {
    background: rgba(59,130,246,0.08) !important;
    border-color: rgba(59,130,246,0.25) !important;
    color: #93c5fd !important;
}
.stButton > button[kind="primary"] {
    background: rgba(59,130,246,0.15) !important;
    border-color: rgba(59,130,246,0.4) !important;
    color: #93c5fd !important;
    font-weight: 600 !important;
}
/* "Подробнее" buttons — ghost style */
.stButton > button[kind="secondary"] {
    background: transparent !important;
    border: 1px solid rgba(255,255,255,0.06) !important;
    color: #52525b !important;
    border-radius: 6px !important;
    font-size: 0.7rem !important;
    font-weight: 400 !important;
    padding: 2px 8px !important;
}

/* === TABS === */
.stTabs [data-baseweb="tab-list"] {
    gap: 8px !important;
    border-bottom: 1px solid rgba(255,255,255,0.06) !important;
}
.stTabs [data-baseweb="tab"] {
    border-radius: 8px 8px 0 0 !important;
    padding: 10px 20px !important;
    font-weight: 500 !important;
    color: #a1a1aa !important;
}

/* === SELECTBOX & MULTISELECT === */
[data-baseweb="select"] {
    border-radius: 10px !important;
}

/* === DIVIDER === */
hr {
    border-color: rgba(255,255,255,0.06) !important;
    margin: 24px 0 !important;
}

/* === DATAFRAME === */
[data-testid="stDataFrame"] {
    border-radius: 12px !important;
    overflow: hidden !important;
}

/* === SLIDER === */
.stSlider > div > div > div {
    border-radius: 10px !important;
}

/* Section header */
.section-header {
    font-size: 1.05rem;
    font-weight: 600;
    letter-spacing: -0.02em;
    color: #a1a1aa;
    margin: 20px 0 10px 0;
    padding-bottom: 8px;
    border-bottom: 1px solid rgba(255,255,255,0.06);
}

/* Country grid */
.country-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 12px;
    margin: 16px 0;
}

/* Hide sidebar completely */
[data-testid=stSidebar] { display: none !important; }
section[data-testid=stSidebar] { display: none !important; }
[data-testid=collapsedControl] { display: none !important; }
button[kind=header] { display: none !important; }
button[kind=headerNoPadding] { display: none !important; }

/* Compact layout */
.block-container {
    padding-top: 0.5rem !important;
    padding-bottom: 0 !important;
    max-width: 1600px !important;
}
[data-testid=stVerticalBlock] > div {
    gap: 0.15rem !important;
}

/* Countries grid */
.countries-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 10px;
    margin: 16px 0;
}

/* === INFO TOOLTIPS === */
.info-tt {
    position: relative;
    display: inline-block;
    cursor: pointer;
    vertical-align: middle;
}
.info-tt .info-i {
    font-size: 0.72rem;
    opacity: 0.35;
    margin-left: 6px;
    transition: opacity 0.2s;
    user-select: none;
    -webkit-user-select: none;
}
.info-tt:hover .info-i {
    opacity: 0.75;
}
.info-tt .info-chk {
    display: none;
}
.info-tt .info-pop {
    display: none;
    position: absolute;
    left: 50%;
    transform: translateX(-50%);
    top: calc(100% + 8px);
    z-index: 9999;
    background: rgba(20,20,30,0.95);
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 10px;
    padding: 16px;
    max-width: 380px;
    min-width: 260px;
    width: max-content;
    font-size: 0.82rem;
    color: rgba(255,255,255,0.7);
    line-height: 1.6;
    font-weight: 400;
    letter-spacing: 0;
    text-transform: none;
    box-shadow: 0 8px 32px rgba(0,0,0,0.5);
    animation: infoFadeIn 0.18s ease;
    pointer-events: auto;
}
.info-tt .info-chk:checked ~ .info-pop {
    display: block;
}
@keyframes infoFadeIn {
    from { opacity: 0; transform: translateX(-50%) translateY(4px); }
    to { opacity: 1; transform: translateX(-50%) translateY(0); }
}
/* Arrow */
.info-tt .info-pop::before {
    content: '';
    position: absolute;
    top: -6px;
    left: 50%;
    transform: translateX(-50%);
    border-left: 6px solid transparent;
    border-right: 6px solid transparent;
    border-bottom: 6px solid rgba(255,255,255,0.1);
}

/* ========== MOBILE RESPONSIVE ========== */
@media (max-width: 640px) {
    /* Main padding */
    .block-container {
        padding-top: 0.5rem !important;
        padding-left: 0.5rem !important;
        padding-right: 0.5rem !important;
    }

    /* NAV TABS — force single-line horizontal scroll */
    .stRadio > div {
        flex-wrap: nowrap !important;
        overflow-x: scroll !important;
        -webkit-overflow-scrolling: touch !important;
        gap: 0 !important;
        padding-bottom: 4px !important;
        scrollbar-width: none !important;
        max-width: 100vw !important;
    }
    .stRadio > div::-webkit-scrollbar { display: none !important; }
    .stRadio > div > label[data-baseweb="radio"] {
        white-space: nowrap !important;
        padding: 4px 8px !important;
        font-size: 0.7rem !important;
        flex-shrink: 0 !important;
    }

    /* ALL columns — wrap and shrink */
    [data-testid="stHorizontalBlock"] {
        flex-wrap: wrap !important;
        gap: 4px 6px !important;
        justify-content: flex-start !important;
    }
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
        flex: 0 0 auto !important;
        width: auto !important;
        min-width: 0 !important;
    }

    /* Period buttons — small pills, no text wrap */
    .stButton > button {
        font-size: 0.65rem !important;
        padding: 4px 10px !important;
        min-height: 24px !important;
        line-height: 1.2 !important;
        width: auto !important;
        white-space: nowrap !important;
    }
    .stButton > button[kind="primary"] {
        font-size: 0.65rem !important;
        padding: 4px 12px !important;
        width: auto !important;
    }
    .stButton > button[kind="secondary"] {
        font-size: 0.6rem !important;
        padding: 1px 6px !important;
        width: auto !important;
    }

    /* Metrics — 2x2 grid */
    .metrics-row {
        flex-wrap: wrap !important;
        gap: 6px !important;
    }
    .metric-box {
        flex: 1 1 calc(50% - 4px) !important;
        min-width: calc(50% - 4px) !important;
        padding: 10px 8px !important;
    }
    .metric-value { font-size: 1.15rem !important; }
    .metric-label { font-size: 0.62rem !important; }

    /* Country cards */
    .country-card { padding: 12px !important; }
    .temp-value { font-size: 1.5rem !important; }

    /* Digest columns — full width on mobile */
    [data-testid="stHorizontalBlock"]:has(.digest-card) > [data-testid="stColumn"],
    [data-testid="stHorizontalBlock"]:has(.country-card) > [data-testid="stColumn"] {
        flex: 1 1 100% !important;
        width: 100% !important;
    }

    /* DIGEST CARDS — force word-wrap, prevent horizontal overflow */
    .digest-card {
        padding: 12px !important;
        font-size: 0.82rem !important;
        line-height: 1.55 !important;
        word-wrap: break-word !important;
        overflow-wrap: break-word !important;
        max-width: 100% !important;
        overflow-x: hidden !important;
    }
    .digest-card p {
        word-wrap: break-word !important;
        overflow-wrap: break-word !important;
    }
    .digest-card a {
        word-break: break-all !important;
    }

    /* ALL markdown containers — prevent overflow */
    [data-testid="stMarkdown"] {
        max-width: 100% !important;
        overflow-x: hidden !important;
        word-wrap: break-word !important;
    }
    [data-testid="stMarkdown"] div,
    [data-testid="stMarkdown"] p {
        max-width: 100% !important;
        overflow-wrap: break-word !important;
        word-wrap: break-word !important;
    }

    /* Plotly map — crop empty space from top on mobile */
    [data-testid="stPlotlyChart"] {
        max-height: 260px !important;
        overflow: hidden !important;
        position: relative !important;
    }
    [data-testid="stPlotlyChart"] > div {
        margin-top: -180px !important;
        position: relative !important;
    }

    /* DataFrames — horizontal scroll on mobile */
    [data-testid="stDataFrame"] {
        overflow-x: auto !important;
        overflow: visible !important;
        -webkit-overflow-scrolling: touch !important;
    }

    /* Section headers */
    .section-header { font-size: 0.95rem !important; margin: 12px 0 6px !important; }

    /* Info tooltips */
    .info-tt .info-popup { max-width: 260px !important; font-size: 0.75rem !important; }

    /* Headings */
    h1 { font-size: 1.4rem !important; }
    h2 { font-size: 1.05rem !important; }
    h3 { font-size: 0.9rem !important; }

    /* Period badge */
    [data-testid="stMarkdown"] span[style*="border-radius:20px"] {
        font-size: 0.7rem !important;
        padding: 4px 10px !important;
    }

    /* Force body no horizontal scroll */
    body, html, [data-testid="stAppViewContainer"] {
        overflow-x: hidden !important;
        max-width: 100vw !important;
    }
}

/* Tablet tweaks */
@media (max-width: 1024px) and (min-width: 641px) {
    .metrics-row { flex-wrap: wrap !important; }
    .metric-box { flex: 1 1 calc(50% - 6px) !important; }
    [data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; }
}
    .metric-box {
        flex: 1 1 calc(50% - 6px) !important;
    }
    [data-testid="stHorizontalBlock"] {
        flex-wrap: wrap !important;
    }
}

</style>
''', unsafe_allow_html=True)

# Auto-refresh every 5 minutes
if "selected_country" not in st.session_state:
    st.session_state.selected_country = None
if "navigate_to" not in st.session_state:
    st.session_state.navigate_to = None
if "last_refresh" not in st.session_state:
    st.session_state.last_refresh = time.time()
if time.time() - st.session_state.last_refresh > 86400:
    st.session_state.last_refresh = time.time()
    st.rerun()


ACTION_LEVEL_DISPLAY = {
    1: ("⚡", "Заявление", "×1"),
    2: ("⚡⚡", "Переговоры/визит", "×3"),
    3: ("⚡⚡⚡", "Соглашение", "×5"),
    4: ("💥", "Санкции/запрет", "×8"),
    5: ("💥💥", "Разрыв/выход", "×12"),
    6: ("💥💥💥", "Военные действия", "×15"),
}

# ─── Plotly chart theme ───
CHART_LAYOUT = dict(
    paper_bgcolor='rgba(0,0,0,0)',
    plot_bgcolor='rgba(0,0,0,0)',
    font=dict(family='Inter, system-ui, sans-serif', color='#a1a1aa', size=13),
    xaxis=dict(
        gridcolor='rgba(255,255,255,0.04)',
        zerolinecolor='rgba(255,255,255,0.08)',
        tickfont=dict(size=11),
    ),
    yaxis=dict(
        gridcolor='rgba(255,255,255,0.04)',
        zerolinecolor='rgba(255,255,255,0.08)',
        tickfont=dict(size=11),
    ),
    margin=dict(l=40, r=20, t=40, b=40),
    hoverlabel=dict(
        bgcolor='#1a1a2e',
        bordercolor='rgba(255,255,255,0.1)',
        font=dict(color='white', family='Inter, system-ui', size=13),
    ),
    legend=dict(
        bgcolor='rgba(0,0,0,0)',
        font=dict(color='#a1a1aa'),
    ),
)


def api_get(endpoint: str, params: dict = None) -> dict | None:
    """Fetch data from API."""
    try:
        r = httpx.get(f"{API_URL}{endpoint}", params=params, timeout=15.0)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Ошибка API: {e}")
        return None


import re as _re
from src.dashboard.threads_page import render_threads_page

def md_to_html(text):
    """Convert markdown links to HTML and add paragraph breaks."""
    if not text:
        return text
    # Convert [text](url) to <a href>
    text = _re.sub(
        r'\[([^\]]+)\]\(([^)]+)\)',
        lambda m: f'<a href="{m.group(2)}" target="_blank" style="color:#60a5fa;text-decoration:none;border-bottom:1px solid rgba(96,165,250,0.3);">{m.group(1)}</a>',
        text
    )
    # Split into paragraphs every 2 sentences
    sentences = _re.split(r'(?<=[.!?])\s+', text)
    paragraphs = []
    current = []
    for s in sentences:
        current.append(s)
        if len(current) >= 2:
            paragraphs.append(' '.join(current))
            current = []
    if current:
        paragraphs.append(' '.join(current))
    return '</p><p style="margin:12px 0;">'.join(paragraphs)

def color_for_temp(temp: float | None) -> str:
    """Return color based on temperature."""
    if temp is None:
        return "#52525b"
    if temp > 30:
        return "#22c55e"
    elif temp > 10:
        return "#86efac"
    elif temp > -10:
        return "#fbbf24"
    elif temp > -30:
        return "#f97316"
    else:
        return "#ef4444"


def action_badge(level: int) -> str:
    """Return emoji badge for action level."""
    info = ACTION_LEVEL_DISPLAY.get(level, ACTION_LEVEL_DISPLAY[1])
    return f"{info[0]} {info[2]}"


import uuid as _uuid

def info_badge(text: str) -> str:
    """Return HTML for an info tooltip icon with click-to-toggle popover."""
    _id = f"ib{_uuid.uuid4().hex[:8]}"
    return (
        f'<label class="info-tt" onclick="event.stopPropagation();">'
        f'<input type="checkbox" class="info-chk" id="{_id}">'
        f'<span class="info-i" onclick="var c=document.getElementById(\'{_id}\');c.checked=!c.checked;event.preventDefault();event.stopPropagation();">ⓘ</span>'
        f'<span class="info-pop" onclick="event.stopPropagation();">{text}</span>'
        f'</label>'
    )


# ─── Horizontal navigation ───
_default_page_idx = 0
if st.session_state.navigate_to:
    _default_page_idx = 1  # Country page

# Header + nav in one line
_hdr_left, _hdr_right = st.columns([1, 5])
with _hdr_left:
    st.markdown('<div style="font-size:1.1rem;font-weight:700;letter-spacing:-0.02em;padding:4px 0;color:#f4f4f5;">GEO PULSE</div>', unsafe_allow_html=True)
with _hdr_right:
    page = st.radio("nav", ["🌡️ Обзор", "🏳️ Страна", "🧵 Сюжеты", "📊 Аналитика", "📡 Источники", "ℹ️ О проекте"], horizontal=True, label_visibility="collapsed", index=_default_page_idx)


# ═══════════════════════════════════════════════════════════
# 🌡️ MAP PAGE
# ═══════════════════════════════════════════════════════════
if page == "🌡️ Обзор":

    # ─── Period selector ───
    PERIOD_OPTIONS = {
        "Неделя": 7,
        "Месяц": 30,
        "Квартал": 90,
        "Год": 365,
        "4 года": 1460,
    }
    _period_names = list(PERIOD_OPTIONS.keys())
    if "overview_period_idx" not in st.session_state:
        st.session_state.overview_period_idx = 1  # default: Месяц

    # Period pills row
    _pill_cols = st.columns(len(_period_names))
    for _pi, _pname in enumerate(_period_names):
        with _pill_cols[_pi]:
            if st.button(_pname, key=f"period_{_pi}",
                         type="primary" if _pi == st.session_state.overview_period_idx else "secondary"):
                st.session_state.overview_period_idx = _pi
                st.rerun()
    # View toggle
    _view = st.radio("Вид", ["Термометр", "Связи"], horizontal=True, label_visibility="collapsed")

    _selected_period = _period_names[st.session_state.overview_period_idx]
    _period_days = PERIOD_OPTIONS[_selected_period]

    data = api_get("/api/v1/countries", {"days": _period_days} if _period_days > 0 else None)
    stats = api_get("/api/v1/stats", {"days": _period_days} if _period_days > 0 else None)

    if not data:
        st.warning("Не удалось загрузить данные. API недоступен.")
        st.stop()

    countries = data["countries"]

    if _view == "Связи":
        import streamlit.components.v1 as _components
        import sys as _sys
        if "/app/src/dashboard" not in _sys.path:
            _sys.path.insert(0, "/app/src/dashboard")
        from threads_viz import get_threads_html as _get_threads

        _viz = []
        for c in countries:
            _ev_resp = api_get(f"/api/v1/countries/{c['code']}/events", {"limit": 5})
            _evl = []
            if _ev_resp and _ev_resp.get("events"):
                for e in _ev_resp["events"][:3]:
                    if (e.get("action_level") or 1) >= 2:
                        _evl.append({"title": (e.get("title") or "")[:60], "action_level": e.get("action_level", 1)})
            _viz.append({
                "code": c["code"], "name": c.get("name", c["code"]),
                "temperature": c.get("temperature") or 0,
                "trend": c.get("trend", "stable"),
                "articles": c.get("article_count", 0), "events": _evl,
            })
        _components.html(_get_threads(_viz), height=550, scrolling=False)
        st.stop()

    # Fetch UN votes summary for cards
    _un_summary_resp = api_get("/api/v1/un-votes/summary")
    _un_summary = _un_summary_resp.get("summary", {}) if _un_summary_resp else {}

    # Stats row — premium metrics
    if stats:
        total_articles = stats.get("total_articles", 0)
        total_analyzed = stats.get("total_analyzed", 0)
        total_relevant = stats.get("total_relevant", 0)
        active_sources = stats.get("active_sources", 0)
        dupes = stats.get("total_duplicates", 0)

        # Date range info
        _oldest = stats.get("oldest_article")
        _newest = stats.get("newest_article")
        _last_temp = stats.get("last_temperature_update")
        _date_range_str = ""
        if _oldest and _newest:
            _msk = timezone(timedelta(hours=3))
            try:
                _o = datetime.fromisoformat(_oldest).astimezone(_msk)
                _n = datetime.fromisoformat(_newest).astimezone(_msk)
                _date_range_str = f"{_o.strftime('%d.%m.%Y')} — {_n.strftime('%d.%m.%Y')}"
            except Exception:
                pass
        _updated_str = ""
        if _last_temp:
            try:
                _msk = timezone(timedelta(hours=3))
                _lt = datetime.fromisoformat(_last_temp).astimezone(_msk)
                _now_msk = datetime.now(_msk)
                _diff = _now_msk - _lt
                if _diff.total_seconds() < 3600:
                    _updated_str = f"{int(_diff.total_seconds() // 60)} мин назад"
                elif _diff.total_seconds() < 86400:
                    _updated_str = f"{int(_diff.total_seconds() // 3600)} ч назад"
                else:
                    _updated_str = _lt.strftime("%d.%m %H:%M")
            except Exception:
                pass

        _period_label = _selected_period if _period_days > 0 else "всё время"
        _period_badge = f"""<div style="text-align:center;margin-bottom:12px;">
            <span style="display:inline-block;background:rgba(96,165,250,0.12);border:1px solid rgba(96,165,250,0.25);border-radius:20px;padding:6px 18px;font-size:0.82rem;color:#93c5fd;">
                {_period_label}{(' · ' + _date_range_str) if _date_range_str else ''}{(' · обновлено ' + _updated_str) if _updated_str else ''}
            </span>{info_badge('Период определяет окно анализа. Все метрики и температуры пересчитываются для выбранного временного окна.')}
        </div>"""
        st.markdown(_period_badge, unsafe_allow_html=True)

        _metrics_info = info_badge('Статьи собираются каждые 30 минут из 149 медиаисточников через RSS и веб-скрапинг. Анализ проводится Claude Sonnet 4 — модель определяет релевантность статьи к отношениям страны с Россией.')
        st.markdown(f'''
        <div style="display:flex;align-items:center;gap:0;">
        <div class="metrics-row" style="flex:1;">
            <div class="metric-box">
                <div class="metric-value">{total_articles:,}</div>
                <div class="metric-label">Статей собрано</div>
            </div>
            <div class="metric-box">
                <div class="metric-value">{total_analyzed:,}</div>
                <div class="metric-label">Проанализировано</div>
            </div>
            <div class="metric-box">
                <div class="metric-value">{total_relevant:,}</div>
                <div class="metric-label">Релевантных</div>
            </div>
            <div class="metric-box">
                <div class="metric-value">{active_sources}</div>
                <div class="metric-label">Источников</div>
            </div>
        </div>{_metrics_info}
        </div>
        ''', unsafe_allow_html=True)


    # ─── Map ───
    st.markdown(f'<div class="section-header">Карта отношений{info_badge("Цвет отражает «температуру» — взвешенный sentiment за выбранный период. Шкала: от −100° (враждебность, красный) до +100° (союзничество, зелёный). Нейтральные страны — жёлтые. Нажмите на страну для перехода к деталям.")}</div>', unsafe_allow_html=True)

    # Capital coordinates for annotations
    _CAPITALS = {
        "KAZ": {"lat": 49.0, "lon": 66.0, "flag": "🇰🇿", "short": "KZ", "name": "Казахстан"},
        "ARM": {"lat": 39.5, "lon": 44.5, "flag": "🇦🇲", "short": "AM", "name": "Армения"},
        "UZB": {"lat": 42.0, "lon": 63.5, "flag": "🇺🇿", "short": "UZ", "name": "Узбекистан"},
        "KGZ": {"lat": 41.5, "lon": 74.5, "flag": "🇰🇬", "short": "KG", "name": "Кыргызстан"},
        "TJK": {"lat": 38.5, "lon": 70.5, "flag": "🇹🇯", "short": "TJ", "name": "Таджикистан"},
        "TKM": {"lat": 40.0, "lon": 58.5, "flag": "🇹🇲", "short": "TM", "name": "Туркменистан"},
        "AZE": {"lat": 40.8, "lon": 49.5, "flag": "🇦🇿", "short": "AZ", "name": "Азербайджан"},
        "GEO": {"lat": 42.3, "lon": 43.5, "flag": "🇬🇪", "short": "GE", "name": "Грузия"},
        "MDA": {"lat": 47.0, "lon": 28.5, "flag": "🇲🇩", "short": "MD", "name": "Молдова"},
        "BLR": {"lat": 53.2, "lon": 28.0, "flag": "🇧🇾", "short": "BY", "name": "Беларусь"},
    }

    # Trend helpers
    _TREND_ARROWS = {"rising": "▲", "falling": "▼", "stable": "→"}
    _TREND_LABELS_RU = {"rising": "потепление", "falling": "охлаждение", "stable": "стабильно"}

    # Zone classification
    def _temp_zone(t):
        if t is None: return ("—", "#52525b")
        if t <= -30: return ("🔴 Враждебность", "#ef4444")
        if t <= -15: return ("🟠 Напряжённость", "#f97316")
        if t <= -5:  return ("🟡 Охлаждение", "#fbbf24")
        if t <= 5:   return ("⚪ Нейтралитет", "#a1a1aa")
        if t <= 15:  return ("🟢 Потепление", "#86efac")
        if t <= 30:  return ("🟢 Сотрудничество", "#22c55e")
        return ("🟢 Союзничество", "#16a34a")

    # Build map data with enriched info
    map_data = []
    for c in countries:
        if c.get("iso3"):
            temp = c.get("temperature")
            trend = c.get("trend") or "stable"
            zone_label, zone_color = _temp_zone(temp)
            arrow = _TREND_ARROWS.get(trend, "→")
            trend_ru = _TREND_LABELS_RU.get(trend, "стабильно")
            arts = c.get("article_count", 0)
            un_pct = _un_summary.get(c["code"], {}).get("agreement_pct")
            un_str = f"{un_pct:.0f}%" if un_pct is not None else "—"

            # Temperature visual bar (20 chars wide)
            if temp is not None:
                bar_pos = max(0, min(20, int((temp + 60) / 120 * 20)))
                bar = "▬" * bar_pos + "●" + "▬" * (20 - bar_pos)
                bar_str = f"-60° {bar} +60°"
            else:
                bar_str = ""

            map_data.append({
                "iso3": c["iso3"],
                "country": c["name"],
                "code": c["code"],
                "temperature": temp if temp is not None else float("nan"),
                "has_data": temp is not None,
                "trend": trend,
                "trend_ru": trend_ru,
                "arrow": arrow,
                "articles": arts,
                "zone_label": zone_label,
                "zone_color": zone_color,
                "un_pct": un_str,
                "bar_str": bar_str,
            })

    if map_data:
        df = pd.DataFrame(map_data)
        df_data = df[df["has_data"]].copy()
        df_nodata = df[~df["has_data"]].copy()

        fig = go.Figure()

        # Russia — subtle light-gray fill
        fig.add_trace(go.Choropleth(
            locations=["RUS"],
            z=[0],
            colorscale=[[0, '#252530'], [1, '#252530']],
            showscale=False,
            marker_line_color='rgba(255,255,255,0.08)',
            marker_line_width=0.8,
            hovertemplate="<b>🇷🇺 Россия</b><br>Опорная точка<extra></extra>",
        ))

        # Countries with data — rich hover
        if not df_data.empty:
            hover_texts = df_data.apply(
                lambda r: (
                    f"<b>{r['country']}</b><br>"
                    f"<span style='font-size:18px;font-weight:bold;color:{r['zone_color']}'>{r['temperature']:.1f}°</span> "
                    f"<span style='color:{r['zone_color']}'>{r['zone_label']}</span><br>"
                    f"<br>"
                    f"📊 Тренд: <b>{r['arrow']} {r['trend_ru']}</b><br>"
                    f"📰 Статей за период: <b>{r['articles']}</b><br>"
                    f"🏛️ Голоса ООН с РФ: <b>{r['un_pct']}</b><br>"
                    f"<br>"
                    f"<span style='font-family:monospace;font-size:10px;color:#71717a'>{r['bar_str']}</span><br>"
                    f"<br>"
                    f"<span style='color:#60a5fa;font-size:11px'>⟵ Нажмите для детальной аналитики</span>"
                ), axis=1,
            )
            fig.add_trace(go.Choropleth(
                locations=df_data["iso3"],
                z=df_data["temperature"],
                text=hover_texts,
                hoverinfo="text",
                hoverlabel=dict(
                    bgcolor='#1e1e2e',
                    bordercolor='rgba(96,165,250,0.3)',
                    font=dict(family='Inter, system-ui, sans-serif', size=13, color='#e4e4e7'),
                    namelength=0,
                ),
                colorscale=[
                    [0, '#991b1b'],
                    [0.15, '#ef4444'],
                    [0.3, '#f97316'],
                    [0.45, '#fbbf24'],
                    [0.5, '#fde68a'],
                    [0.55, '#bbf7d0'],
                    [0.7, '#86efac'],
                    [0.85, '#22c55e'],
                    [1, '#16a34a'],
                ],
                zmid=0,
                zmin=-60,
                zmax=60,
                colorbar=dict(
                    title=dict(text="°C", side="right", font=dict(size=12, color='#a1a1aa')),
                    tickvals=[-60, -40, -20, 0, 20, 40, 60],
                    ticktext=["-60°", "-40°", "-20°", "0°", "+20°", "+40°", "+60°"],
                    outlinewidth=0,
                    bgcolor='rgba(0,0,0,0)',
                    tickfont=dict(color='#71717a', size=11),
                    len=0.55,
                    thickness=14,
                    x=1.02,
                ),
                marker_line_color='rgba(255,255,255,0.15)',
                marker_line_width=1,
            ))

        # Countries without data
        if not df_nodata.empty:
            fig.add_trace(go.Choropleth(
                locations=df_nodata["iso3"],
                z=[0] * len(df_nodata),
                text=df_nodata.apply(
                    lambda r: f"<b>{r['country']}</b><br><span style='color:#71717a'>Нет данных за период</span><br>📰 Статей: {r['articles']}",
                    axis=1,
                ),
                hoverinfo="text",
                hoverlabel=dict(
                    bgcolor='#1e1e2e',
                    bordercolor='rgba(255,255,255,0.1)',
                    font=dict(family='Inter, system-ui, sans-serif', size=13, color='#a1a1aa'),
                ),
                colorscale=[[0, '#1a1a24'], [1, '#1a1a24']],
                showscale=False,
                marker_line_color='rgba(255,255,255,0.08)',
                marker_line_width=0.5,
            ))

        # Moscow anchor marker
        fig.add_trace(go.Scattergeo(
            lat=[55.75], lon=[37.62],
            mode="markers+text",
            marker=dict(size=10, color='#60a5fa', symbol='diamond', line=dict(width=1.5, color='rgba(96,165,250,0.6)')),
            text=["🇷🇺 Москва"],
            textposition="bottom center",
            textfont=dict(size=11, color='#60a5fa', family='Inter, system-ui, sans-serif'),
            showlegend=False,
            hovertemplate="<b>🇷🇺 Москва</b><br>Опорная точка<extra></extra>",
        ))

        # Connection lines from Moscow to each capital + temperature labels
        _label_lats = []
        _label_lons = []
        _label_texts = []
        for iso3, cap in _CAPITALS.items():
            row = df[df["iso3"] == iso3]
            if row.empty:
                continue
            r = row.iloc[0]

            if r["has_data"]:
                temp = r["temperature"]
                arrow = r["arrow"]

                # Line color based on temperature
                if temp <= -15:
                    lcolor = 'rgba(239,68,68,0.3)'
                elif temp <= 0:
                    lcolor = 'rgba(251,191,36,0.25)'
                elif temp <= 15:
                    lcolor = 'rgba(134,239,172,0.25)'
                else:
                    lcolor = 'rgba(34,197,94,0.3)'

                # Connection line
                fig.add_trace(go.Scattergeo(
                    lat=[55.75, cap["lat"]], lon=[37.62, cap["lon"]],
                    mode="lines",
                    line=dict(width=1, color=lcolor, dash='dot'),
                    showlegend=False,
                    hoverinfo="skip",
                ))

                # Label text
                _label_lats.append(cap["lat"])
                _label_lons.append(cap["lon"])
                _label_texts.append(f"{cap['flag']} {temp:.0f}° {arrow}")
            else:
                _label_lats.append(cap["lat"])
                _label_lons.append(cap["lon"])
                _label_texts.append(f"{cap['flag']} —")

        # Temperature labels as Scattergeo text
        if _label_lats:
            fig.add_trace(go.Scattergeo(
                lat=_label_lats,
                lon=_label_lons,
                mode="text",
                text=_label_texts,
                textfont=dict(
                    size=12,
                    color='#e4e4e7',
                    family='Inter, system-ui, sans-serif',
                ),
                textposition="top center",
                showlegend=False,
                hoverinfo="skip",
            ))

        fig.update_geos(
            projection_type="equirectangular",
            center=dict(lat=45, lon=52),
            lataxis_range=[36, 56],
            lonaxis_range=[24, 80],
            bgcolor='rgba(0,0,0,0)',
            showcoastlines=True,
            coastlinecolor='rgba(255,255,255,0.06)',
            showland=True,
            landcolor='#141420',
            showocean=True,
            oceancolor='#080810',
            showcountries=True,
            countrycolor='rgba(255,255,255,0.05)',
            showlakes=False,
            showframe=False,
            resolution=50,
        )

        map_layout = {k: v for k, v in CHART_LAYOUT.items() if k != 'margin'}
        fig.update_layout(
            **map_layout,
            height=550,
            margin=dict(l=0, r=40, t=0, b=0),
            geo=dict(bgcolor='rgba(0,0,0,0)'),
        )

        # Render map with click handling
        _map_event = st.plotly_chart(fig, use_container_width=True, on_select="rerun", key="geo_map")

        # Handle click → navigate to country
        if _map_event and hasattr(_map_event, 'selection') and _map_event.selection:
            _sel = _map_event.selection
            if hasattr(_sel, 'points') and _sel.points:
                _clicked = _sel.points[0]
                _loc = getattr(_clicked, 'location', None) or (_clicked.get('location') if isinstance(_clicked, dict) else None)
                if _loc and _loc != "RUS":
                    _click_code = None
                    for _mc in map_data:
                        if _mc["iso3"] == _loc:
                            _click_code = _mc["code"]
                            break
                    if _click_code:
                        st.session_state.selected_country = _click_code
                        st.session_state.navigate_to = _click_code
                        st.rerun()

    # Country cards — premium design
    _period_hint = f"за {_selected_period.lower()}" if _period_days > 0 else "за всё время"
    st.markdown(f'<div class="section-header">Температура по странам <span style="font-size:0.75rem;color:#52525b;font-weight:400;">· {_period_hint}</span>{info_badge("Каждая карточка показывает агрегированную температуру за выбранный период. Формула учитывает: тип события (×1–×15), свежесть (экспоненциальное затухание τ=14 дней), вес источника и кластерное подавление дублей.")}</div>', unsafe_allow_html=True)

    cols = st.columns(5)
    for i, c in enumerate(countries):
        with cols[i % 5]:
            temp = c.get("temperature")
            color = color_for_temp(temp)
            temp_str = f"{temp:+.1f}°" if temp is not None else "—"
            trend = c.get("trend", "stable")

            # Human-friendly temperature description
            if temp is None:
                temp_desc = "Нет данных"
            elif temp >= 30:
                temp_desc = "Союзничество"
            elif temp >= 10:
                temp_desc = "Сотрудничество"
            elif temp >= -10:
                temp_desc = "Нейтралитет"
            elif temp >= -25:
                temp_desc = "Охлаждение"
            elif temp >= -45:
                temp_desc = "Напряжённость"
            else:
                temp_desc = "Враждебность"

            # Human-friendly trend
            trend_map = {
                "rising": ("↗ Потепление", "#22c55e"),
                "falling": ("↘ Охлаждение", "#ef4444"),
                "stable": ("→ Без изменений", "#71717a"),
            }
            trend_label, trend_color = trend_map.get(trend, ("→ Без изменений", "#71717a"))

            _art_count = c.get("article_count", 0)
            st.markdown(f'''
            <div class="country-card">
                <div class="temp-label">{c['name']}</div>
                <div class="temp-value" style="color:{color}">{temp_str}</div>
                <div style="font-size:0.82rem; color:{color}; opacity:0.7; margin:4px 0;">{temp_desc}</div>
                <div style="font-size:0.78rem; color:{trend_color}; margin-top:6px;">{trend_label}</div>
                <div style="font-size:0.72rem; color:#52525b; margin-top:4px;">{_art_count} статей · {_period_hint}</div>
                {"" if c["code"] not in _un_summary else f'<div style="font-size:0.78rem;opacity:0.7;margin-top:4px;">ООН: <span style="color:{"#22c55e" if _un_summary[c["code"]].get("agreement_pct",0) >= 75 else "#fbbf24" if _un_summary[c["code"]].get("agreement_pct",0) >= 60 else "#ef4444"};font-weight:600;">{_un_summary[c["code"]].get("agreement_pct",0):.0f}%</span> ООН</div>'}
            </div>
            ''', unsafe_allow_html=True)
            if st.button(f"Подробнее →", key=f"btn_{c['code']}", use_container_width=True):
                st.session_state.selected_country = c["code"]
                st.session_state.navigate_to = c["code"]
                st.rerun()

    # Weekly digests — premium cards
    st.markdown(f'<div class="section-header">Еженедельные сводки{info_badge("Автоматически генерируются Claude Sonnet 4 на основе топ-событий за неделю. Содержат ссылки на источники.")}</div>', unsafe_allow_html=True)
    digest_cols = st.columns(2)
    for i, c in enumerate(countries):
        with digest_cols[i % 2]:
            d = api_get(f"/api/v1/countries/{c['code']}/digest")
            if d and d.get("digest"):
                border_color = color_for_temp(c.get("temperature"))
                st.markdown(f'''
                <div class="digest-card" style="border-left: 3px solid {border_color};">
                    <div class="country-name">{c['name']}</div>
                    <div style="color:rgba(255,255,255,0.65);line-height:1.7;"><p style="margin:12px 0;">{md_to_html(d['digest'])}</p></div>
                </div>
                ''', unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════
# 🏳️ COUNTRY PAGE
# ═══════════════════════════════════════════════════════════

elif page == "🏳️ Страна":
    # Handle navigation from overview cards
    if st.session_state.navigate_to:
        _nav_country = st.session_state.navigate_to
        st.session_state.navigate_to = None
    else:
        _nav_country = st.session_state.selected_country

    data = api_get("/api/v1/countries")
    if not data:
        st.stop()

    countries = {c["code"]: c["name"] for c in data["countries"]}
    _country_list = list(countries.keys())
    _default_idx = _country_list.index(_nav_country) if _nav_country in _country_list else 0

    selected = st.selectbox("Выберите страну", _country_list,
                            index=_default_idx,
                            format_func=lambda x: f"{countries[x]} ({x})")
    st.session_state.selected_country = selected

    # Country header with digest
    _c_data = next((c for c in data["countries"] if c["code"] == selected), None)
    if _c_data:
        _temp = _c_data.get("temperature", 0)
        _color = color_for_temp(_temp)
        _sign = "+" if _temp and _temp > 0 else ""
        _trend = {"rising": "↗ Рост", "falling": "↘ Спад", "stable": "→ Стабильно"}.get(_c_data.get("trend","stable"), "→")
        st.markdown(f'''
        <div style="background:linear-gradient(135deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01));
                    border:1px solid rgba(255,255,255,0.08); border-radius:16px; padding:28px 32px;
                    margin-bottom:24px; display:flex; align-items:center; gap:32px;">
            <div>
                <div style="font-size:3.2rem; font-weight:700; color:{_color}; letter-spacing:-0.04em;">{_sign}{_temp:.1f}°</div>
                <div style="font-size:0.9rem; color:#71717a; margin-top:4px;">{_trend} · {_c_data.get("article_count",0)} статей</div>
            </div>
            <div style="flex:1;">
                <h1 style="margin:0; font-size:1.8rem;">{countries[selected]}</h1>
            </div>
        </div>
        ''', unsafe_allow_html=True)

    # Digest card
    _digest = api_get(f"/api/v1/countries/{selected}/digest")
    if _digest and _digest.get("digest"):
        _d_color = color_for_temp(_c_data.get("temperature") if _c_data else 0)
        st.markdown(f'''
        <div class="digest-card" style="border-left:3px solid {_d_color}; margin-bottom:24px;">
            <div class="country-name" style="margin-bottom:8px;">📝 Еженедельная сводка</div>
            <div style="color:rgba(255,255,255,0.7); line-height:1.7;"><p style="margin:12px 0;">{md_to_html(_digest["digest"])}</p></div>
        </div>
        ''', unsafe_allow_html=True)

    days = st.slider("Период (дней)", 7, 90, 30)

    col_left, col_right = st.columns([3, 2])
    with col_left:
        # Temperature chart
        temp_data = api_get(f"/api/v1/countries/{selected}/temperature", {"days": days})
        if temp_data and temp_data.get("data"):
            st.markdown(f'<div class="section-header">🌡️ Температура: {countries[selected]}{info_badge("Ежедневная температура рассчитывается как взвешенный средний sentiment статей за скользящее окно 14 дней с экспоненциальным затуханием.")}</div>', unsafe_allow_html=True)
            df = pd.DataFrame(temp_data["data"])
            df["time"] = pd.to_datetime(df["time"], format="mixed")

            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=df["time"], y=df["temperature"],
                mode="lines+markers",
                name="Температура",
                line=dict(color="#3b82f6", width=2.5, shape='spline'),
                marker=dict(size=5, color="#3b82f6"),
                fill='tozeroy',
                fillcolor='rgba(59,130,246,0.08)',
            ))
            fig.add_hline(y=0, line_dash="dash", line_color="rgba(255,255,255,0.15)", line_width=1)
            fig.update_layout(
                **CHART_LAYOUT,
                yaxis_title="Температура (−100..+100)",
                xaxis_title="",
                height=400,
                showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True)

            # Components breakdown
            if any(d.get("components") for d in temp_data["data"]):
                st.markdown(f'<div class="section-header">📊 Разбивка по типам событий{info_badge("Sentiment разбит на 5 компонент: дипломатия, военное, экономика, культура, безопасность. Показывает какая сфера отношений доминирует.")}</div>', unsafe_allow_html=True)
                latest = temp_data["data"][-1].get("components", {})

                comp_items = [
                    ("diplomatic", "🤝 Дипломатия"),
                    ("military", "⚔️ Военное"),
                    ("economic", "💰 Экономика"),
                    ("cultural", "🎭 Культура"),
                    ("security", "🛡️ Безопасность"),
                ]

                comp_vals = {}
                for comp, label in comp_items:
                    val = latest.get(comp) if latest else None
                    if val is not None:
                        comp_vals[label] = val

                if comp_vals:
                    comp_html = '<div class="metrics-row">'
                    for label, val in comp_vals.items():
                        val_color = "#22c55e" if val > 0 else "#ef4444" if val < 0 else "#71717a"
                        comp_html += f'''
                        <div class="metric-box">
                            <div class="metric-value" style="color:{val_color};font-size:1.6rem;">{val:+.1f}</div>
                            <div class="metric-label">{label}</div>
                        </div>'''
                    comp_html += '</div>'
                    st.markdown(comp_html, unsafe_allow_html=True)

                    bar_colors = ["#3b82f6", "#ef4444", "#22c55e", "#f59e0b", "#8b5cf6"]
                    fig_comp = go.Figure(go.Bar(
                        x=list(comp_vals.keys()),
                        y=list(comp_vals.values()),
                        marker_color=bar_colors[:len(comp_vals)],
                        marker=dict(cornerradius=6),
                    ))
                    fig_comp.update_layout(
                        **CHART_LAYOUT,
                        height=300,
                        yaxis_title="Средний sentiment",
                        showlegend=False,
                    )
                    fig_comp.add_hline(y=0, line_dash="dash", line_color="rgba(255,255,255,0.15)", line_width=1)
                    st.plotly_chart(fig_comp, use_container_width=True)
        else:
            st.info("Данных о температуре пока нет. Подождите, пока система соберёт и проанализирует статьи.")

    with col_right:
        # Tier breakdown — human-friendly
        st.markdown(f'<div class="section-header">📊 Что говорят разные типы СМИ{info_badge("Сравнение тональности между тирами источников. Divergence показывает расхождение: если государственные СМИ позитивны, а независимые негативны — расхождение высокое.")}</div>', unsafe_allow_html=True)
        tiers_data = api_get(f"/api/v1/countries/{selected}/tiers", {"days": days})
        if tiers_data and tiers_data.get("tiers"):
            div_val = tiers_data.get("divergence", 0)
            overall = tiers_data.get('overall_sentiment', 0)

            if overall > 1:
                overall_desc = "Позитивный"
                overall_color = "#22c55e"
            elif overall > 0:
                overall_desc = "Скорее позитивный"
                overall_color = "#86efac"
            elif overall > -0.5:
                overall_desc = "Нейтральный"
                overall_color = "#71717a"
            elif overall > -1.5:
                overall_desc = "Скорее негативный"
                overall_color = "#f97316"
            else:
                overall_desc = "Негативный"
                overall_color = "#ef4444"

            if div_val < 1.0:
                div_desc = "СМИ единодушны — разные типы источников дают похожую картину"
                div_icon = "🟢"
            elif div_val <= 2.0:
                div_desc = "Есть расхождения — официальные и независимые СМИ видят ситуацию по-разному"
                div_icon = "🟡"
            else:
                div_desc = "Сильный разрыв — картина в государственных и оппозиционных СМИ кардинально отличается"
                div_icon = "🔴"

            st.markdown(f'''
            <div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06); border-radius:12px; padding:20px; margin:12px 0;">
                <div style="display:flex; align-items:center; gap:16px; margin-bottom:16px;">
                    <div style="font-size:2rem; font-weight:700; color:{overall_color};">{overall_desc}</div>
                    <div style="font-size:0.85rem; color:#71717a;">общий тон медиа ({overall:+.2f})</div>
                </div>
                <div style="display:flex; align-items:center; gap:8px; font-size:0.9rem; color:rgba(255,255,255,0.6);">
                    <span>{div_icon}</span>
                    <span>{div_desc}</span>
                </div>
            </div>
            ''', unsafe_allow_html=True)

            # Group tiers into categories
            _TIER_GROUPS = [
                ("🏛️ Государственные СМИ", ["official"], "Официальные источники: правительства, МИД, госагентства"),
                ("📺 Крупные медиа", ["mainstream", "analytics"], "Ведущие СМИ и аналитические издания"),
                ("📰 Независимые", ["independent"], "Негосударственные медиа со своей редакционной политикой"),
                ("📢 Оппозиция", ["domestic_opposition", "western_proxy"], "Оппозиционные и зарубежные русскоязычные СМИ"),
                ("💬 Соцсети и Telegram", ["social"], "Telegram-каналы и социальные медиа"),
            ]

            _tier_map = {t["tier"]: t for t in tiers_data["tiers"] if t["article_count"] > 0}

            for _grp_label, _grp_tiers, _grp_desc in _TIER_GROUPS:
                _grp_items = [_tier_map[_tk] for _tk in _grp_tiers if _tk in _tier_map]
                if not _grp_items:
                    continue

                _grp_sent = sum(t["sentiment"] * t["article_count"] for t in _grp_items) / max(1, sum(t["article_count"] for t in _grp_items))
                _grp_cnt = sum(t["article_count"] for t in _grp_items)

                if _grp_sent > 0.5:
                    _gt = "позитивно"; _gc = "#22c55e"
                elif _grp_sent > 0:
                    _gt = "скорее позитивно"; _gc = "#86efac"
                elif _grp_sent > -0.5:
                    _gt = "нейтрально"; _gc = "#71717a"
                elif _grp_sent > -1.5:
                    _gt = "скорее негативно"; _gc = "#f97316"
                else:
                    _gt = "негативно"; _gc = "#ef4444"

                _bar_pct = max(5, min(95, (_grp_sent + 3) / 6 * 100))

                # Sub-items for this group
                _sub_html = ""
                for _ti in _grp_items:
                    _ts = _ti["sentiment"]
                    _tc2 = _ti["article_count"]
                    if _ts > 0.3:
                        _stc = "#22c55e"
                    elif _ts > -0.3:
                        _stc = "#71717a"
                    else:
                        _stc = "#f97316"
                    _sub_html += f'<div style="display:flex; justify-content:space-between; padding:3px 0; font-size:0.8rem;"><span style="color:#a1a1aa;">{_ti["label"]}</span><span style="color:{_stc};">{_ts:+.2f} ({_tc2})</span></div>'

                st.markdown(f"""
                <div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06); border-radius:10px; padding:14px 18px; margin:8px 0;">
                    <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:6px;">
                        <div style="font-size:0.95rem; font-weight:600;">{_grp_label}</div>
                        <div style="display:flex; align-items:center; gap:12px;">
                            <span style="font-size:0.85rem; color:{_gc}; font-weight:500;">{_gt}</span>
                            <span style="font-size:0.78rem; color:#52525b;">{_grp_cnt} ст.</span>
                        </div>
                    </div>
                    <div style="background:rgba(255,255,255,0.04); border-radius:4px; height:6px; overflow:hidden; margin-bottom:8px;">
                        <div style="width:{_bar_pct}%; height:100%; background:{_gc}; border-radius:4px;"></div>
                    </div>
                    {_sub_html}
                    <div style="font-size:0.72rem; color:#3f3f46; margin-top:6px;">{_grp_desc}</div>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info("Данных по тирам пока нет.")


    # ── 🔥 Top Resonance Events ──
    resonance_resp = api_get(f"/api/v1/countries/{selected}/resonance?days=14&limit=5")
    if resonance_resp and resonance_resp.get("events"):
        st.markdown(f'<div class="section-header">🔥 Резонансные события{info_badge("Resonance score = log(sources) × tier_diversity × action_weight × freshness. Показывает какие события получили наибольший охват в разных типах СМИ.")}</div>', unsafe_allow_html=True)
        for _ev in resonance_resp["events"]:
            _rs = _ev["resonance_score"]
            _sent = _ev["avg_sentiment"]
            _sc = _ev["source_count"]
            _ac = _ev["article_count"]
            _tc = _ev["tier_count"]
            _al = _ev["max_action_level"]
            _sh = _ev["spread_hours"]
            
            # Resonance bar color
            if _rs >= 5:
                _rc = "#ef4444"; _fire = "🔥🔥🔥"
            elif _rs >= 3:
                _rc = "#f59e0b"; _fire = "🔥🔥"
            elif _rs >= 1.5:
                _rc = "#3b82f6"; _fire = "🔥"
            else:
                _rc = "#71717a"; _fire = "📰"
            
            # Sentiment color
            if _sent > 0.3:
                _sentc = "#22c55e"; _sent_icon = "💚"
            elif _sent < -0.3:
                _sentc = "#ef4444"; _sent_icon = "💔"
            else:
                _sentc = "#71717a"; _sent_icon = "🤍"
            
            # Speed badge
            if _sh < 6:
                _speed = "⚡ молниеносно"
            elif _sh < 24:
                _speed = "🕐 быстро"
            else:
                _speed = f"📅 {_sh:.0f}ч"
            
            _bar_w = min(100, _rs * 10)
            
            _sources_str = ", ".join(_ev.get("source_names", [])[:4])
            if len(_ev.get("source_names", [])) > 4:
                _sources_str += f" +{len(_ev['source_names']) - 4}"
            
            st.markdown(f"""
            <div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06); border-radius:10px; padding:14px 18px; margin:8px 0;">
                <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:6px;">
                    <div style="font-size:0.92rem; font-weight:600; flex:1;">{_fire} {_ev['event_key']}</div>
                    <div style="display:flex; align-items:center; gap:10px;">
                        <span style="font-size:0.82rem; color:{_sentc};">{_sent_icon} {_sent:+.1f}</span>
                        <span style="font-size:0.95rem; font-weight:700; color:{_rc};">{_rs}</span>
                    </div>
                </div>
                <div style="background:rgba(255,255,255,0.04); border-radius:3px; height:4px; overflow:hidden; margin-bottom:8px;">
                    <div style="width:{_bar_w}%; height:100%; background:{_rc}; border-radius:3px;"></div>
                </div>
                <div style="display:flex; gap:16px; font-size:0.78rem; color:#71717a; flex-wrap:wrap;">
                    <span>📰 {_ac} статей</span>
                    <span>📡 {_sc} источников</span>
                    <span>🏷️ {_tc} типов СМИ</span>
                    <span>{_speed}</span>
                    <span>⚡ action {_al}</span>
                </div>
                <div style="font-size:0.72rem; color:#3f3f46; margin-top:4px;">{_sources_str}</div>
            </div>
            """, unsafe_allow_html=True)
        
        st.markdown("")

    col_un, col_trade = st.columns(2)
    with col_un:
        # ── UN Votes section ──
        st.markdown(f'<div class="section-header">🗳️ Голосования в ООН — совпадение с Россией{info_badge("Данные из UN Digital Library. Процент совпадения голосов страны с Россией по резолюциям Генассамблеи (2014–2023).")}</div>', unsafe_allow_html=True)
        un_data = api_get(f"/api/v1/countries/{selected}/un-votes")
        if un_data and un_data.get("data"):
            un_df = pd.DataFrame(un_data["data"])
            latest_un = un_df.iloc[-1] if len(un_df) > 0 else None
            if latest_un is not None:
                _un_pct = latest_un["agreement_pct"]
                _un_color = "#22c55e" if _un_pct >= 75 else "#fbbf24" if _un_pct >= 60 else "#ef4444"
                _un_trend = ""
                if len(un_df) >= 2:
                    _un_diff = un_df.iloc[-1]["agreement_pct"] - un_df.iloc[-2]["agreement_pct"]
                    _un_trend_icon = "↗" if _un_diff > 0 else "↘" if _un_diff < 0 else "→"
                    _un_trend = f'<span style="color:{"#22c55e" if _un_diff > 0 else "#ef4444" if _un_diff < 0 else "#71717a"};margin-left:12px;">{_un_trend_icon} {_un_diff:+.1f}%</span>'

                st.markdown(f"""
                <div class="metrics-row">
                    <div class="metric-box">
                        <div class="metric-value" style="color:{_un_color}">{_un_pct:.1f}%</div>
                        <div class="metric-label">Совпадение ({int(latest_un["year"])})</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-value">{int(latest_un["total_votes"])}</div>
                        <div class="metric-label">Голосований</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-value" style="color:#22c55e">{int(latest_un["agree_with_russia"])}</div>
                        <div class="metric-label">Совпали</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-value" style="color:#ef4444">{int(latest_un["disagree_with_russia"])}</div>
                        <div class="metric-label">Разошлись</div>
                    </div>
                </div>
                """, unsafe_allow_html=True)

            fig_un = go.Figure()
            fig_un.add_trace(go.Scatter(
                x=un_df["year"], y=un_df["agreement_pct"],
                mode="lines+markers",
                name="% совпадения",
                line=dict(color="#8b5cf6", width=2.5, shape="spline"),
                marker=dict(size=7, color="#8b5cf6"),
                fill="tozeroy",
                fillcolor="rgba(139,92,246,0.08)",
                hovertemplate="<b>%{x}</b><br>Совпадение: %{y:.1f}%<extra></extra>",
            ))
            _un_layout = {k: v for k, v in CHART_LAYOUT.items() if k not in ("yaxis", "margin")}
            fig_un.update_layout(
                **_un_layout,
                height=350,
                yaxis=dict(
                    title="% совпадения голосов",
                    range=[30, 100],
                    gridcolor="rgba(255,255,255,0.04)",
                    zerolinecolor="rgba(255,255,255,0.08)",
                    tickfont=dict(size=11),
                ),
                margin=dict(l=50, r=20, t=20, b=40),
                xaxis_title="",
                showlegend=False,
            )
            st.plotly_chart(fig_un, use_container_width=True)
        else:
            st.info("Данных по голосованиям ООН пока нет.")

    with col_trade:
        # ── Trade section ──
        st.markdown(f'<div class="section-header">💰 Торговля с Россией{info_badge("Данные UN Comtrade API. Для AZ, BY, TM, TJ дополнены из ФТС России — Comtrade для этих стран неполный.")}</div>', unsafe_allow_html=True)
        trade_resp = api_get(f"/api/v1/countries/{selected}/trade")
        if trade_resp and trade_resp.get("data"):
            trade_df = pd.DataFrame(trade_resp["data"])
            latest_tr = trade_df.iloc[-1] if len(trade_df) > 0 else None
            if latest_tr is not None:
                _total_b = latest_tr["total_trade_usd"] / 1e9
                _exp_b = latest_tr["ru_export_usd"] / 1e9
                _imp_b = latest_tr["ru_import_usd"] / 1e9
                _yoy = latest_tr.get("yoy_change_pct")
                _yoy_str = f"{_yoy:+.1f}%" if _yoy is not None else "—"
                _yoy_color = "#22c55e" if _yoy and _yoy > 0 else "#ef4444" if _yoy and _yoy < 0 else "#71717a"
                st.markdown(f"""
                <div class="metrics-row">
                    <div class="metric-box">
                        <div class="metric-value">${_total_b:.1f}B</div>
                        <div class="metric-label">Товарооборот ({int(latest_tr["year"])})</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-value" style="color:#3b82f6">${_exp_b:.1f}B</div>
                        <div class="metric-label">🇷🇺 Экспорт РФ</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-value" style="color:#f59e0b">${_imp_b:.1f}B</div>
                        <div class="metric-label">📥 Импорт в РФ</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-value" style="color:{_yoy_color}">{_yoy_str}</div>
                        <div class="metric-label">Год к году</div>
                    </div>
                </div>
                """, unsafe_allow_html=True)

            # Stacked bar chart: export + import
            fig_trade = go.Figure()
            fig_trade.add_trace(go.Bar(
                x=trade_df["year"],
                y=trade_df["ru_export_usd"].apply(lambda x: x / 1e6),
                name="Экспорт РФ",
                marker_color="#3b82f6",
                marker=dict(cornerradius=4),
                hovertemplate="<b>%{x}</b><br>Экспорт: $%{y:,.0f}M<extra></extra>",
            ))
            fig_trade.add_trace(go.Bar(
                x=trade_df["year"],
                y=trade_df["ru_import_usd"].apply(lambda x: x / 1e6),
                name="Импорт в РФ",
                marker_color="#f59e0b",
                marker=dict(cornerradius=4),
                hovertemplate="<b>%{x}</b><br>Импорт: $%{y:,.0f}M<extra></extra>",
            ))
            fig_trade.add_trace(go.Scatter(
                x=trade_df["year"],
                y=trade_df["total_trade_usd"].apply(lambda x: x / 1e6),
                mode="lines+markers",
                name="Товарооборот",
                line=dict(color="#e4e4e7", width=2, dash="dot"),
                marker=dict(size=5, color="#e4e4e7"),
                yaxis="y",
                hovertemplate="<b>%{x}</b><br>Всего: $%{y:,.0f}M<extra></extra>",
            ))
            _tr_layout = {k: v for k, v in CHART_LAYOUT.items() if k not in ("yaxis", "margin", "legend")}
            fig_trade.update_layout(
                **_tr_layout,
                height=400,
                barmode="stack",
                yaxis=dict(
                    title="$ млн",
                    gridcolor="rgba(255,255,255,0.04)",
                    zerolinecolor="rgba(255,255,255,0.08)",
                    tickfont=dict(size=11),
                ),
                margin=dict(l=60, r=20, t=20, b=40),
                xaxis_title="",
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=1.02,
                    bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#a1a1aa"),
                ),
            )
            st.plotly_chart(fig_trade, use_container_width=True)
            # Data source footnote
            _patched_countries = {'AZ': 'ФТС/WITS (2015-2024)', 'BY': 'Belstat/ФТС (2022-2024)', 'TM': 'ФТС (2022-2024)', 'TJ': 'ФТС (2024)'}
            _src_note = _patched_countries.get(selected, None)
            if _src_note:
                st.markdown(f'<p style="color:#71717a;font-size:0.75rem;margin-top:-10px;">📊 Источник: {_src_note} — данные UN Comtrade для этой страны неполные</p>', unsafe_allow_html=True)
            else:
                st.markdown('<p style="color:#71717a;font-size:0.75rem;margin-top:-10px;">📊 Источник: UN Comtrade API</p>', unsafe_allow_html=True)
        else:
            st.info("Торговых данных пока нет.")

    st.markdown("---")

    # ── Key events (action_level >= 3) ──
    events = api_get(f"/api/v1/countries/{selected}/events", {"limit": 100})
    if events and events.get("events"):
        all_events = events["events"]
        # Filter events by selected period
        from datetime import datetime, timedelta, timezone as _tz
        _cutoff = datetime.now(_tz.utc) - timedelta(days=days)
        _filtered_events = []
        for _ev in all_events:
            _pub = _ev.get("published_at", "")
            if _pub:
                try:
                    _pub_dt = pd.to_datetime(_pub, format="mixed", utc=True)
                    if _pub_dt >= _cutoff:
                        _filtered_events.append(_ev)
                except Exception:
                    _filtered_events.append(_ev)
            else:
                _filtered_events.append(_ev)
        all_events = _filtered_events
        key_events = [e for e in all_events if (e.get("action_level") or 1) >= 3]

        if key_events:
            st.markdown(f'<div class="section-header">🔑 Ключевые события{info_badge("Фильтр: action_level ≥ 3 (соглашения, санкции, разрывы, военные действия). Action level определяется LLM при анализе.")}</div>', unsafe_allow_html=True)

            for ev in sorted(key_events, key=lambda e: (e.get("action_level", 1) * abs(e.get("sentiment") or 0)), reverse=True)[:10]:
                al = ev.get("action_level", 1)
                al_info = ACTION_LEVEL_DISPLAY.get(al, ACTION_LEVEL_DISPLAY[1])
                sentiment = ev.get("sentiment")
                sent_color_val = "#22c55e" if sentiment and sentiment > 0.5 else "#ef4444" if sentiment and sentiment < -0.5 else "#fbbf24"
                sent_str = f"{sentiment:+.1f}" if sentiment is not None else "—"
                ev_date = ev.get('published_at', '')[:10]
                ev_url = ev.get('url', '')
                link_html = f'<a href="{ev_url}" target="_blank" style="color:#3b82f6;text-decoration:none;opacity:0.7;">→ источник</a>' if ev_url else ""

                st.markdown(f'''
                <div class="key-event">
                    <div style="display:flex;align-items:center;gap:12px;">
                        <span style="font-size:1.3rem;">{al_info[0]}</span>
                        <div style="flex:1;">
                            <div style="font-weight:600;color:#e4e4e7;">{ev['title'][:120]}</div>
                            <div style="font-size:0.82rem;color:#71717a;margin-top:4px;">
                                {ev_date} · {ev.get('source', '—')} · {al_info[1]} {al_info[2]} {link_html}
                            </div>
                        </div>
                        <span style="font-weight:700;font-size:1.1rem;color:{sent_color_val};">{sent_str}</span>
                    </div>
                </div>
                ''', unsafe_allow_html=True)

            st.markdown("---")

        # Events feed
        st.markdown(f'<div class="section-header">📰 Лента событий{info_badge("Все проанализированные статьи, признанные релевантными. Sentiment −3..+3 отражает тональность отношений страны к России (не тон статьи).")}</div>', unsafe_allow_html=True)
        sort_by = st.radio("Сортировка", ["По дате", "По влиянию", "По резонансу"], horizontal=True, key="ev_sort")

        ev_list = all_events
        if sort_by == "По влиянию":
            ev_list = sorted(ev_list, key=lambda e: (e.get("action_level", 1) * abs(e.get("sentiment") or 0)), reverse=True)
        elif sort_by == "По резонансу":
            ev_list = sorted(ev_list, key=lambda e: e.get("reprint_count", 0), reverse=True)

        ev_type_emoji_map = {
            "diplomatic": "🤝",
            "military": "⚔️",
            "economic": "💰",
            "cultural": "🎭",
            "security": "🛡️",
        }

        for ev in ev_list[:50]:
            sentiment = ev.get("sentiment")
            if sentiment is not None:
                sent_color_val = "#22c55e" if sentiment > 0.5 else "#ef4444" if sentiment < -0.5 else "#fbbf24"
                sent_str = f"{sentiment:+.1f}"
            else:
                sent_color_val = "#52525b"
                sent_str = "—"

            rc = ev.get("reprint_count", 0)
            reprint_html = f'<span style="background:rgba(59,130,246,0.12);color:#60a5fa;padding:2px 8px;border-radius:12px;font-size:0.75rem;margin-left:8px;">🔄 ×{rc}</span>' if rc > 0 else ""

            al = ev.get("action_level", 1)
            al_info = ACTION_LEVEL_DISPLAY.get(al, ACTION_LEVEL_DISPLAY[1])
            al_html = f'<span style="opacity:0.6;font-size:0.8rem;">{al_info[0]}{al_info[2]}</span>' if al > 1 else ""

            ev_type = ev.get("event_type", "")
            ev_type_emoji = ev_type_emoji_map.get(ev_type, "📄")
            ev_date = ev.get('published_at', '')[:10]
            ev_source = ev.get('source', '—')
            ev_url = ev.get('url', '')
            link_html = f'<a href="{ev_url}" target="_blank" style="color:#3b82f6;text-decoration:none;font-size:0.8rem;">→</a>' if ev_url else ""

            badge_color = sent_color_val

            st.markdown(f'''
            <div class="event-item">
                <div class="event-badge" style="background:{badge_color}15;">
                    {ev_type_emoji}
                </div>
                <div style="flex:1;min-width:0;">
                    <div style="font-weight:500;color:#e4e4e7;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{ev['title'][:100]}{reprint_html}</div>
                    <div style="font-size:0.8rem;color:#52525b;margin-top:3px;">{ev_source} · {ev_date} · {ev_type_emoji} {ev_type} {al_html} {link_html}</div>
                </div>
                <div style="margin-left:auto;font-weight:700;color:{sent_color_val};font-size:1rem;white-space:nowrap;">{sent_str}</div>
            </div>
            ''', unsafe_allow_html=True)
    else:
        st.info("Событий пока нет.")


# ═══════════════════════════════════════════════════════════
# ⚖️ COMPARE PAGE
# ═══════════════════════════════════════════════════════════
elif page == "⚖️ Сравнение":
    st.title("⚖️ Сравнение стран")

    data = api_get("/api/v1/countries")
    if not data:
        st.stop()

    all_countries = {c["code"]: c["name"] for c in data["countries"]}
    selected = st.multiselect(
        "Выберите страны для сравнения (2-5)",
        list(all_countries.keys()),
        default=["KZ", "AM", "UZ"],
        format_func=lambda x: all_countries[x],
        max_selections=5,
    )

    if not selected or len(selected) < 2:
        st.info("Выберите минимум 2 страны для сравнения.")
        st.stop()

    days = st.slider("Период (дней)", 7, 90, 30, key="compare_days")

    compare_data = api_get("/api/v1/compare", {"countries": ",".join(selected), "days": days})
    if compare_data and compare_data.get("comparison"):
        fig = go.Figure()
        colors = ["#3b82f6", "#ef4444", "#22c55e", "#f59e0b", "#8b5cf6"]

        for i, (code, cdata) in enumerate(compare_data["comparison"].items()):
            if cdata.get("data"):
                df = pd.DataFrame(cdata["data"])
                df["time"] = pd.to_datetime(df["time"], format="mixed")
                fig.add_trace(go.Scatter(
                    x=df["time"], y=df["temperature"],
                    mode="lines",
                    name=f"{cdata['name']} ({code})",
                    line=dict(color=colors[i % len(colors)], width=2.5, shape='spline'),
                ))

        fig.add_hline(y=0, line_dash="dash", line_color="rgba(255,255,255,0.15)", line_width=1)
        fig.update_layout(
            **CHART_LAYOUT,
            yaxis_title="Температура",
            height=500,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, bgcolor='rgba(0,0,0,0)'),
        )
        st.plotly_chart(fig, use_container_width=True)

    # Comparison table — premium cards
    st.markdown('<div class="section-header">📊 Текущие значения</div>', unsafe_allow_html=True)

    comp_cols = st.columns(len(selected))
    for idx, c in enumerate(data["countries"]):
        if c["code"] in selected:
            col_idx = selected.index(c["code"])
            with comp_cols[col_idx]:
                temp = c.get("temperature")
                color = color_for_temp(temp)
                temp_str = f"{temp:+.1f}°" if temp is not None else "—"
                trend = c.get("trend", "stable")
                trend_emoji = {"rising": "↗", "falling": "↘", "stable": "→"}.get(trend, "→")
                trend_color = {"rising": "#22c55e", "falling": "#ef4444", "stable": "#71717a"}.get(trend, "#71717a")
                articles = c.get("article_count", 0)
                div_val = c.get("divergence", 0) or 0

                st.markdown(f'''
                <div class="country-card" style="text-align:center;">
                    <div class="temp-label">{c['name']}</div>
                    <div class="temp-value" style="color:{color};font-size:2.2rem;">{temp_str}</div>
                    <span class="trend-badge" style="background:{trend_color}18;color:{trend_color};">
                        {trend_emoji} {trend}
                    </span>
                    <div style="margin-top:12px;font-size:0.82rem;color:#52525b;">
                        📰 {articles} статей · давл. {div_val:.1f}
                    </div>
                </div>
                ''', unsafe_allow_html=True)



# ═══════════════════════════════════════════════════════════
# 📊 ANALYTICS PAGE
# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════
# 🧵 THREADS PAGE
# ═══════════════════════════════════════════════════════════
elif page == "🧵 Сюжеты":
    render_threads_page()


elif page == "📊 Аналитика":
    import numpy as np

    st.title("📊 Аналитика")
    st.markdown('<p style="color:#71717a;font-size:1.05rem;margin-top:-10px;">Объективные данные: голосования в ООН, торговля с Россией и корреляция с медийной температурой</p>', unsafe_allow_html=True)

    _AN_COUNTRIES = {
        "KZ": "🇰🇿 Казахстан", "AM": "🇦🇲 Армения", "UZ": "🇺🇿 Узбекистан",
        "KG": "🇰🇬 Кыргызстан", "TJ": "🇹🇯 Таджикистан", "TM": "🇹🇲 Туркменистан",
        "AZ": "🇦🇿 Азербайджан", "GE": "🇬🇪 Грузия", "MD": "🇲🇩 Молдова", "BY": "🇧🇾 Беларусь",
    }
    _CC_LIST = list(_AN_COUNTRIES.keys())

    # ── Fetch all data ──
    _all_un = {}
    _all_trade = {}
    for _cc in _CC_LIST:
        _un_r = api_get(f"/api/v1/countries/{_cc}/un-votes")
        if _un_r and _un_r.get("data"):
            _all_un[_cc] = _un_r["data"]
        _tr_r = api_get(f"/api/v1/countries/{_cc}/trade")
        if _tr_r and _tr_r.get("data"):
            _all_trade[_cc] = _tr_r["data"]

    _un_summary = api_get("/api/v1/un-votes/summary")
    _countries_data = api_get("/api/v1/countries")

    # ─────────────────────────────────────────────────
    # 🗳️ Section 1: UN Votes
    # ─────────────────────────────────────────────────
    st.markdown(f'<div class="section-header">🗳️ Голосования в ООН — совпадение с Россией{info_badge("Данные из UN Digital Library. Процент совпадения голосов страны с Россией по резолюциям Генассамблеи (2014–2023).")}</div>', unsafe_allow_html=True)

    if _un_summary and _un_summary.get("summary"):
        _summ = _un_summary["summary"]
        _un_rows = []
        for _cc in _CC_LIST:
            if _cc not in _summ:
                continue
            _latest_pct = _summ[_cc]["agreement_pct"]
            _trend_str = "—"
            if _cc in _all_un and len(_all_un[_cc]) >= 2:
                _sorted_un = sorted(_all_un[_cc], key=lambda x: x["year"])
                _prev_pct = _sorted_un[-2]["agreement_pct"]
                _diff = _latest_pct - _prev_pct
                if _diff > 0:
                    _trend_str = '<span style="color:#22c55e;">\u25b2 +' + f"{_diff:.1f}" + '%</span>'
                elif _diff < 0:
                    _trend_str = '<span style="color:#ef4444;">\u25bc ' + f"{_diff:.1f}" + '%</span>'
                else:
                    _trend_str = '<span style="color:#a1a1aa;">\u25cf 0%</span>'
            if _latest_pct >= 70:
                _clr = "#22c55e"
            elif _latest_pct >= 50:
                _clr = "#fbbf24"
            else:
                _clr = "#ef4444"
            _un_rows.append((_cc, _AN_COUNTRIES[_cc], _latest_pct, _clr, _trend_str))

        _un_rows.sort(key=lambda x: -x[2])

        _tbl = '<div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;font-size:0.95rem;">'
        _tbl += '<tr style="border-bottom:1px solid rgba(255,255,255,0.1);">'
        _tbl += '<th style="text-align:left;padding:12px 16px;color:#71717a;font-weight:500;">Страна</th>'
        _tbl += '<th style="text-align:center;padding:12px 16px;color:#71717a;font-weight:500;">% совпадения (2023)</th>'
        _tbl += '<th style="text-align:center;padding:12px 16px;color:#71717a;font-weight:500;">Тренд</th>'
        _tbl += '</tr>'
        for _cc, _nm, _pct, _clr, _trnd in _un_rows:
            _bw = str(int(_pct * 1.5))
            _tbl += '<tr style="border-bottom:1px solid rgba(255,255,255,0.04);">'
            _tbl += '<td style="padding:10px 16px;font-weight:500;">' + _nm + '</td>'
            _tbl += '<td style="padding:10px 16px;text-align:center;">'
            _tbl += '<div style="display:flex;align-items:center;gap:10px;justify-content:center;">'
            _tbl += '<div style="width:' + _bw + 'px;height:8px;background:' + _clr + ';border-radius:4px;opacity:0.7;"></div>'
            _tbl += '<span style="color:' + _clr + ';font-weight:600;">' + f"{_pct:.1f}" + '%</span>'
            _tbl += '</div></td>'
            _tbl += '<td style="padding:10px 16px;text-align:center;">' + _trnd + '</td>'
            _tbl += '</tr>'
        _tbl += '</table></div>'
        st.markdown(_tbl, unsafe_allow_html=True)

    st.markdown("")

    # ── UN Heatmap ──
    if _all_un:
        st.markdown(f'<div class="section-header">Динамика по годам{info_badge("Динамика совпадения голосов с Россией по годам. Чем зеленее — тем больше совпадений. Данные: UN Digital Library, резолюции Генассамблеи.")}</div>', unsafe_allow_html=True)
        _hm_cc = []
        _hm_years = set()
        _hm_data = {}
        for _cc in _CC_LIST:
            if _cc not in _all_un:
                continue
            _hm_cc.append(_cc)
            for _row in _all_un[_cc]:
                _hm_years.add(_row["year"])
                _hm_data[(_cc, _row["year"])] = _row["agreement_pct"]

        _yrs = sorted(_hm_years)
        _z = []
        _ylbl = []
        for _cc in _hm_cc:
            _ylbl.append(_AN_COUNTRIES[_cc])
            _zrow = []
            for _yr in _yrs:
                _zrow.append(_hm_data.get((_cc, _yr)))
            _z.append(_zrow)

        _text_z = [[("{:.0f}%".format(v) if v else "") for v in row] for row in _z]

        _hm_fig = go.Figure(data=go.Heatmap(
            z=_z,
            x=[str(y) for y in _yrs],
            y=_ylbl,
            colorscale=[[0, "#dc2626"], [0.3, "#f97316"], [0.5, "#fbbf24"], [0.7, "#86efac"], [1, "#22c55e"]],
            zmin=30, zmax=90,
            text=_text_z,
            texttemplate="%{text}",
            textfont=dict(size=11, color="white"),
            hovertemplate="<b>%{y}</b><br>Год: %{x}<br>Совпадение: %{z:.1f}%<extra></extra>",
            colorbar=dict(title=dict(text="%", font=dict(color="#a1a1aa")), tickfont=dict(color="#a1a1aa"), bgcolor="rgba(0,0,0,0)"),
        ))
        _hml = {k: v for k, v in CHART_LAYOUT.items() if k not in ("xaxis", "yaxis", "margin")}
        _hm_fig.update_layout(
            **_hml,
            height=420,
            margin=dict(l=140, r=40, t=30, b=40),
            yaxis=dict(tickfont=dict(size=12, color="#a1a1aa"), autorange="reversed"),
            xaxis=dict(tickfont=dict(size=12, color="#a1a1aa"), dtick=1),
        )
        st.plotly_chart(_hm_fig, use_container_width=True, key="an_un_heatmap")

    st.markdown("---")

    # ─────────────────────────────────────────────────
    # 💰 Section 2: Trade
    # ─────────────────────────────────────────────────
    st.markdown('<div class="section-header">💰 Торговля с Россией</div>', unsafe_allow_html=True)

    if _all_trade:
        _trade_latest = {}
        for _cc in _CC_LIST:
            if _cc not in _all_trade:
                continue
            _st = sorted(_all_trade[_cc], key=lambda x: x["year"])
            _last = _st[-1]
            _trade_latest[_cc] = {
                "year": _last["year"],
                "total": _last["total_trade_usd"],
                "export": _last["ru_export_usd"],
                "import": _last["ru_import_usd"],
                "yoy": _last.get("yoy_change_pct"),
            }
        _trade_ranked = sorted(_trade_latest.items(), key=lambda x: -x[1]["total"])

        def _fmt_bln(v):
            if v is None:
                return "—"
            b = v / 1e9
            if b >= 1:
                return "$" + "{:.1f}".format(b) + "B"
            return "$" + "{:.0f}".format(v / 1e6) + "M"

        _trt = '<div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;font-size:0.95rem;">'
        _trt += '<tr style="border-bottom:1px solid rgba(255,255,255,0.1);">'
        _trt += '<th style="text-align:left;padding:12px 16px;color:#71717a;font-weight:500;">#</th>'
        _trt += '<th style="text-align:left;padding:12px 16px;color:#71717a;font-weight:500;">Страна</th>'
        _trt += '<th style="text-align:right;padding:12px 16px;color:#71717a;font-weight:500;">Товарооборот</th>'
        _trt += '<th style="text-align:right;padding:12px 16px;color:#71717a;font-weight:500;">Экспорт РФ</th>'
        _trt += '<th style="text-align:right;padding:12px 16px;color:#71717a;font-weight:500;">Импорт РФ</th>'
        _trt += '<th style="text-align:center;padding:12px 16px;color:#71717a;font-weight:500;">Г/г</th>'
        _trt += '</tr>'

        for _i, (_cc, _td) in enumerate(_trade_ranked, 1):
            _yoy_s = "—"
            if _td["yoy"] is not None:
                if _td["yoy"] > 0:
                    _yoy_s = '<span style="color:#22c55e;">+' + "{:.1f}".format(_td["yoy"]) + '%</span>'
                elif _td["yoy"] < 0:
                    _yoy_s = '<span style="color:#ef4444;">' + "{:.1f}".format(_td["yoy"]) + '%</span>'
                else:
                    _yoy_s = '<span style="color:#a1a1aa;">0%</span>'
            _trt += '<tr style="border-bottom:1px solid rgba(255,255,255,0.04);">'
            _trt += '<td style="padding:10px 16px;color:#71717a;">' + str(_i) + '</td>'
            _trt += '<td style="padding:10px 16px;font-weight:500;">' + _AN_COUNTRIES[_cc] + '</td>'
            _trt += '<td style="padding:10px 16px;text-align:right;font-weight:600;color:#60a5fa;">' + _fmt_bln(_td["total"]) + '</td>'
            _trt += '<td style="padding:10px 16px;text-align:right;color:#a1a1aa;">' + _fmt_bln(_td["export"]) + '</td>'
            _trt += '<td style="padding:10px 16px;text-align:right;color:#a1a1aa;">' + _fmt_bln(_td["import"]) + '</td>'
            _trt += '<td style="padding:10px 16px;text-align:center;">' + _yoy_s + '</td>'
            _trt += '</tr>'
        _trt += '</table></div>'
        st.markdown(_trt, unsafe_allow_html=True)

        st.markdown("")

        # ── Stacked bar: all countries ──
        st.markdown("#### Экспорт / Импорт по странам")
        _bd = []
        for _cc, _td in _trade_ranked:
            _bd.append({"country": _AN_COUNTRIES[_cc], "export": _td["export"] / 1e9, "import": _td["import"] / 1e9})
        _bdf = pd.DataFrame(_bd)
        _bfig = go.Figure()
        _bfig.add_trace(go.Bar(
            x=_bdf["country"], y=_bdf["export"], name="Экспорт РФ", marker_color="#3b82f6",
            hovertemplate="<b>%{x}</b><br>Экспорт: $%{y:.1f}B<extra></extra>",
        ))
        _bfig.add_trace(go.Bar(
            x=_bdf["country"], y=_bdf["import"], name="Импорт РФ", marker_color="#8b5cf6",
            hovertemplate="<b>%{x}</b><br>Импорт: $%{y:.1f}B<extra></extra>",
        ))
        _bl = {k: v for k, v in CHART_LAYOUT.items() if k not in ("yaxis", "margin")}
        _bfig.update_layout(
            **_bl, barmode="stack", height=400,
            margin=dict(l=50, r=20, t=30, b=80),
            yaxis=dict(title="$ млрд", gridcolor="rgba(255,255,255,0.04)", tickfont=dict(size=11, color="#a1a1aa")),
            xaxis=dict(tickangle=-30, tickfont=dict(size=10, color="#a1a1aa")),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(color="#a1a1aa")),
        )
        st.plotly_chart(_bfig, use_container_width=True, key="an_trade_bar")

        # ── Trade trend over time ──
        st.markdown("#### Динамика товарооборота")
        _tfig = go.Figure()
        _tc = ["#3b82f6", "#8b5cf6", "#22c55e", "#f59e0b", "#ef4444", "#06b6d4", "#ec4899", "#a78bfa", "#f97316", "#14b8a6"]
        for _idx, _cc in enumerate(_CC_LIST):
            if _cc not in _all_trade:
                continue
            _st = sorted(_all_trade[_cc], key=lambda x: x["year"])
            _tfig.add_trace(go.Scatter(
                x=[r["year"] for r in _st], y=[r["total_trade_usd"] / 1e9 for r in _st],
                name=_AN_COUNTRIES[_cc], mode="lines+markers",
                line=dict(width=2, color=_tc[_idx % len(_tc)]), marker=dict(size=5),
                hovertemplate="<b>" + _AN_COUNTRIES[_cc] + "</b><br>%{x}: $%{y:.1f}B<extra></extra>",
            ))
        _tl2 = {k: v for k, v in CHART_LAYOUT.items() if k not in ("yaxis", "margin")}
        _tfig.update_layout(
            **_tl2, height=420,
            margin=dict(l=50, r=20, t=30, b=40),
            yaxis=dict(title="$ млрд", gridcolor="rgba(255,255,255,0.04)", tickfont=dict(size=11, color="#a1a1aa")),
            xaxis=dict(dtick=1, tickfont=dict(size=11, color="#a1a1aa")),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(color="#a1a1aa", size=11)),
        )
        st.plotly_chart(_tfig, use_container_width=True, key="an_trade_trend")

        st.markdown('<p style="color:#71717a;font-size:0.75rem;margin-top:-8px;">📊 Данные: UN Comtrade API. AZ (2015-2024), BY (2022-2024), TM (2022-2024), TJ (2024) дополнены из ФТС России — Comtrade для этих стран/периодов неполный.</p>', unsafe_allow_html=True)

    st.markdown("---")

    # ─────────────────────────────────────────────────
    # 🔗 Section 3: Correlation
    # ─────────────────────────────────────────────────
    st.markdown(f'<div class="section-header">🔗 Корреляция: ООН vs Медийная температура{info_badge("Scatter plot: X = % совпадения голосов с РФ в ООН, Y = медийная температура. R² показывает насколько медийное восприятие соответствует реальному голосованию.")}</div>', unsafe_allow_html=True)
    st.markdown('<p style="color:#71717a;font-size:0.9rem;">Если точки близки к диагонали — наш медийный термометр коррелирует с реальным голосованием в ООН. Отклонения показывают расхождение восприятия и реальности.</p>', unsafe_allow_html=True)

    if _un_summary and _un_summary.get("summary") and _countries_data and _countries_data.get("countries"):
        _summ = _un_summary["summary"]
        _temps = {}
        for c in _countries_data["countries"]:
            if c.get("temperature") is not None:
                _temps[c["code"]] = c["temperature"]

        _sc_data = []
        for _cc in _CC_LIST:
            if _cc in _summ and _cc in _temps:
                _sc_data.append({"code": _cc, "name": _AN_COUNTRIES[_cc], "un_pct": _summ[_cc]["agreement_pct"], "temperature": _temps[_cc]})

        if _sc_data:
            _scdf = pd.DataFrame(_sc_data)
            _sc_colors = [color_for_temp(r["temperature"]) for r in _sc_data]

            _sfig = go.Figure()
            _sfig.add_trace(go.Scatter(
                x=_scdf["un_pct"], y=_scdf["temperature"],
                mode="markers+text", text=_scdf["code"], textposition="top center",
                textfont=dict(size=11, color="#a1a1aa"),
                marker=dict(size=16, color=_sc_colors, line=dict(width=1, color="rgba(255,255,255,0.2)")),
                hovertemplate="<b>%{customdata[0]}</b><br>ООН: %{x:.1f}%<br>Температура: %{y:.1f}°<extra></extra>",
                customdata=[[r["name"]] for r in _sc_data],
            ))

            # Trend line + R²
            if len(_scdf) >= 3:
                _zp = np.polyfit(_scdf["un_pct"], _scdf["temperature"], 1)
                _pp = np.poly1d(_zp)
                _xr = [_scdf["un_pct"].min() - 2, _scdf["un_pct"].max() + 2]
                _sfig.add_trace(go.Scatter(
                    x=_xr, y=[_pp(_xr[0]), _pp(_xr[1])],
                    mode="lines", line=dict(color="rgba(255,255,255,0.15)", width=1, dash="dash"),
                    showlegend=False, hoverinfo="skip",
                ))
                _yp = _pp(_scdf["un_pct"])
                _ssr = ((_scdf["temperature"] - _yp) ** 2).sum()
                _sst = ((_scdf["temperature"] - _scdf["temperature"].mean()) ** 2).sum()
                _r2 = 1 - _ssr / _sst if _sst > 0 else 0

                if _r2 > 0.5:
                    _r2c = "#22c55e"
                    _r2t = "Сильная связь — термометр отражает реальность"
                elif _r2 > 0.2:
                    _r2c = "#fbbf24"
                    _r2t = "Умеренная связь — есть расхождения"
                else:
                    _r2c = "#ef4444"
                    _r2t = "Слабая связь — медийное восприятие отличается от голосований"

                _r2_html = '<div style="display:flex;gap:24px;margin:8px 0 16px 0;">'
                _r2_html += '<div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);border-radius:10px;padding:14px 20px;">'
                _r2_html += '<span style="color:#71717a;font-size:0.8rem;text-transform:uppercase;letter-spacing:0.05em;">R\u00b2 корреляция</span><br>'
                _r2_html += '<span style="font-size:1.4rem;font-weight:700;color:' + _r2c + ';">' + "{:.3f}".format(_r2) + '</span>'
                _r2_html += '</div>'
                _r2_html += '<div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);border-radius:10px;padding:14px 20px;">'
                _r2_html += '<span style="color:#71717a;font-size:0.8rem;text-transform:uppercase;letter-spacing:0.05em;">Интерпретация</span><br>'
                _r2_html += '<span style="font-size:0.95rem;color:#e4e4e7;">' + _r2t + '</span>'
                _r2_html += '</div></div>'
                st.markdown(_r2_html, unsafe_allow_html=True)

            _sl = {k: v for k, v in CHART_LAYOUT.items() if k not in ("yaxis", "margin")}
            _sfig.update_layout(
                **_sl, height=480,
                margin=dict(l=60, r=40, t=30, b=60),
                xaxis=dict(title="% совпадения с РФ в ООН", gridcolor="rgba(255,255,255,0.04)", tickfont=dict(size=11, color="#a1a1aa"), ticksuffix="%"),
                yaxis=dict(title="Медийная температура (°)", gridcolor="rgba(255,255,255,0.04)", tickfont=dict(size=11, color="#a1a1aa"), zeroline=True, zerolinecolor="rgba(255,255,255,0.1)"),
                showlegend=False,
            )
            st.plotly_chart(_sfig, use_container_width=True, key="an_correlation")

            # Outlier analysis
            st.markdown("#### \U0001f50d Заметные расхождения")
            _outliers = []
            for _r in _sc_data:
                if _r["un_pct"] > 65 and _r["temperature"] < -10:
                    _outliers.append(
                        "\u26a0\ufe0f **" + _r["name"] + "** — высокое совпадение в ООН (" + "{:.0f}".format(_r["un_pct"]) + "%), "
                        "но негативная температура (" + "{:.1f}".format(_r["temperature"]) + "°). Медиа негативнее реальных отношений."
                    )
                elif _r["un_pct"] < 55 and _r["temperature"] > 0:
                    _outliers.append(
                        "\u26a0\ufe0f **" + _r["name"] + "** — низкое совпадение в ООН (" + "{:.0f}".format(_r["un_pct"]) + "%), "
                        "но позитивная температура (" + "{:.1f}".format(_r["temperature"]) + "°). Медиа позитивнее реальных отношений."
                    )

            if _outliers:
                for _o in _outliers:
                    st.markdown('<div style="background:rgba(251,191,36,0.06);border:1px solid rgba(251,191,36,0.15);border-radius:10px;padding:14px 18px;margin:6px 0;font-size:0.92rem;color:#e4e4e7;">' + _o + '</div>', unsafe_allow_html=True)
            else:
                st.markdown('<div style="background:rgba(34,197,94,0.06);border:1px solid rgba(34,197,94,0.15);border-radius:10px;padding:14px 18px;margin:6px 0;font-size:0.92rem;color:#e4e4e7;">\u2705 Значительных расхождений не обнаружено — медийная температура в целом соответствует голосованиям в ООН.</div>', unsafe_allow_html=True)
    else:
        st.warning("Недостаточно данных для построения корреляции.")



# ═══════════════════════════════════════════════════════════
# 📡 SOURCES PAGE
# ═══════════════════════════════════════════════════════════
elif page == "📡 Источники":
    st.title("📡 Источники")

    COUNTRY_NAMES_EMOJI = {
        "KZ": "🇰🇿 Казахстан", "AM": "🇦🇲 Армения", "UZ": "🇺🇿 Узбекистан",
        "KG": "🇰🇬 Кыргызстан", "TJ": "🇹🇯 Таджикистан", "TM": "🇹🇲 Туркменистан",
        "AZ": "🇦🇿 Азербайджан", "GE": "🇬🇪 Грузия", "MD": "🇲🇩 Молдова", "BY": "🇧🇾 Беларусь",
    }
    SOURCE_TYPES = {"rss": "RSS", "web": "Web", "telegram": "Telegram"}
    TIERS = {
        "official": "🏛️ Официальный", "mainstream": "📰 Mainstream",
        "analytics": "🔍 Аналитика", "social": "💬 Соцсети",
        "opposition": "📢 Оппозиция",
    }

    data = api_get("/api/v1/sources")
    if not data:
        st.warning("Не удалось загрузить источники.")
        st.stop()

    sources = data["sources"]

    total = len(sources)
    active = sum(1 for s in sources if s["active"])
    total_articles = sum(s.get("article_count", 0) for s in sources)

    st.markdown(f'''
    <div class="metrics-row">
        <div class="metric-box">
            <div class="metric-value">{total}</div>
            <div class="metric-label">Всего источников</div>
        </div>
        <div class="metric-box">
            <div class="metric-value" style="color:#22c55e;">{active}</div>
            <div class="metric-label">Активных</div>
        </div>
        <div class="metric-box">
            <div class="metric-value" style="color:#ef4444;">{total - active}</div>
            <div class="metric-label">Отключённых</div>
        </div>
        <div class="metric-box">
            <div class="metric-value">{total_articles:,}</div>
            <div class="metric-label">Статей собрано</div>
        </div>
    </div>
    ''', unsafe_allow_html=True)

    st.markdown("---")

    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        filter_country = st.multiselect("Страна", list(COUNTRY_NAMES_EMOJI.keys()),
                                         format_func=lambda x: COUNTRY_NAMES_EMOJI[x])
    with col_f2:
        filter_type = st.multiselect("Тип", list(SOURCE_TYPES.keys()),
                                      format_func=lambda x: SOURCE_TYPES[x])
    with col_f3:
        filter_tier = st.multiselect("Тир", list(TIERS.keys()),
                                      format_func=lambda x: TIERS[x])

    filtered = sources
    if filter_country:
        filtered = [s for s in filtered if s["country_code"] in filter_country]
    if filter_type:
        filtered = [s for s in filtered if s["source_type"] in filter_type]
    if filter_tier:
        filtered = [s for s in filtered if s.get("tier", "mainstream") in filter_tier]

    if filtered:
        rows = []
        for s in filtered:
            last = s.get("last_collected")
            if last:
                last = last[:16].replace("T", " ")
            else:
                last = "—"

            if s["active"] and s.get("article_count", 0) > 0:
                status_icon = "🟢"
            elif not s["active"]:
                status_icon = "⚫"
            else:
                status_icon = "🔴"

            avg_sent = s.get("avg_sentiment")
            avg_sent_str = f"{avg_sent:+.2f}" if avg_sent is not None else "—"

            rows.append({
                "": status_icon,
                "Страна": COUNTRY_NAMES_EMOJI.get(s["country_code"], s["country_code"]),
                "Название": s["name"],
                "Тип": SOURCE_TYPES.get(s["source_type"], s["source_type"]),
                "Тир": TIERS.get(s.get("tier", "mainstream"), "—"),
                "Статей": s.get("article_count", 0),
                "Ср.тон": avg_sent_str,
                "Последний сбор": last,
            })

        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True,
            hide_index=True,
            height=min(len(rows) * 40 + 40, 600),
        )
    else:
        st.info("Нет источников, соответствующих фильтрам.")


# ═══════════════════════════════════════════════════════════
# ℹ️ ABOUT PAGE
# ═══════════════════════════════════════════════════════════
elif page == "ℹ️ О проекте":

    st.markdown("""
    <div style="text-align:center; margin:20px 0 32px;">
        <div style="font-size:2.5rem; font-weight:800; letter-spacing:-0.04em; margin-bottom:8px;">🌡️ GEO PULSE</div>
        <div style="font-size:1.1rem; color:#a1a1aa; font-weight:300;">Количественный мониторинг отношений стран постсоветского пространства с Россией</div>
        <div style="font-size:0.85rem; color:#52525b; margin-top:8px;">v2.0 · Февраль 2026</div>
    </div>
    """, unsafe_allow_html=True)

    # ── Live stats from API ──
    _about_stats = api_get("/api/v1/stats")
    _about_sources = api_get("/api/v1/sources")
    _src_count = len(_about_sources.get("sources", [])) if _about_sources else 149
    _articles = _about_stats.get("total_articles", 6000) if _about_stats else 6000
    _analyzed = _about_stats.get("total_analyzed", 5700) if _about_stats else 5700
    _relevant = _about_stats.get("total_relevant", 2100) if _about_stats else 2100

    st.markdown(f'''
    <div class="metrics-row">
        <div class="metric-box">
            <div class="metric-value" style="color:#3b82f6;">{_src_count}</div>
            <div class="metric-label">📡 Источников</div>
        </div>
        <div class="metric-box">
            <div class="metric-value">{_articles:,}</div>
            <div class="metric-label">📰 Статей собрано</div>
        </div>
        <div class="metric-box">
            <div class="metric-value" style="color:#22c55e;">10</div>
            <div class="metric-label">🌍 Стран</div>
        </div>
        <div class="metric-box">
            <div class="metric-value" style="color:#f59e0b;">7</div>
            <div class="metric-label">📊 Тиров источников</div>
        </div>
    </div>
    ''', unsafe_allow_html=True)

    st.markdown("""
    <h2 style="font-size:1.2rem; font-weight:600; margin:28px 0 12px;">🔍 Как это появилось</h2>
    <p style="color:rgba(255,255,255,0.75); line-height:1.9; font-size:0.95rem;">
    Мы заметили, что медиапространство каждой постсоветской страны работает как чуткий барометр: задолго до официальных заявлений, санкций или дипломатических демаршей — тональность публикаций начинает смещаться. Казахстанские СМИ начинают чаще писать о «суверенитете» за месяцы до выхода из очередного российского интеграционного проекта. Армянская пресса раскачивается между «стратегическим партнёрством» и «европейским выбором» с точностью маятника. Белорусские государственные медиа держат ровный позитивный тон — и любое отклонение от этой нормы сигнализирует о реальном напряжении.</p>

    <p style="color:rgba(255,255,255,0.75); line-height:1.9; font-size:0.95rem; margin-top:12px;">
    Из этого наблюдения родился <strong>GEO PULSE</strong> — инструмент, который переводит медийный шум в числовую «температуру» отношений. Система каждые 30 минут собирает публикации из 149 источников на 10 стран, пропускает их через AI-анализ (Claude Sonnet 4), взвешивает по типу события, значимости и свежести — и выдаёт единый показатель от −100° (открытая враждебность) до +100° (союзничество).</p>

    <h3 style="font-size:1.05rem; font-weight:600; margin:24px 0 10px; color:rgba(255,255,255,0.85);">💡 Что мы узнали</h3>
    <div style="color:rgba(255,255,255,0.65); line-height:1.85; font-size:0.9rem;">
    <p>• <strong>Медиа опережают дипломатию.</strong> Сдвиг тональности в национальных СМИ на 2–4 недели предшествует политическим решениям. Охлаждение в казахстанских медиа началось задолго до дела Бишимбаева и скандала с Сабуровым.</p>
    <p style="margin-top:6px;">• <strong>Молдова и Грузия — зеркальные антиподы.</strong> Обе страны показывают устойчиво отрицательную температуру (−34° и −16°), но по разным причинам: Молдова — через институциональный разрыв (курс на ЕС), Грузия — через внутренний конфликт элит.</p>
    <p style="margin-top:6px;">• <strong>Беларусь — единственная страна в устойчивом «зелёном» спектре</strong> (+24°), но даже здесь появляются трещины: любое снижение градуса в белорусских госСМИ — сигнал, невидимый невооружённым глазом.</p>
    <p style="margin-top:6px;">• <strong>Центральная Азия играет в «прагматичный нейтралитет».</strong> Узбекистан (−0.3°), Кыргызстан (+0.1°), Таджикистан (+9.6°) — все кучкуются у нуля. Это не безразличие, а осознанная стратегия: максимум экономических выгод при минимуме политических обязательств.</p>
    <p style="margin-top:6px;">• <strong>Голосование в ООН коррелирует с температурой, но не линейно.</strong> Беларусь совпадает с Россией в 81% голосований, Молдова — в 44%. Но самое интересное — страны со средним совпадением (~70%): именно они в зоне наибольшей непредсказуемости.</p>
    </div>

    <p style="color:rgba(255,255,255,0.5); line-height:1.7; font-size:0.88rem; margin-top:16px;">
    Три слоя верификации: <strong>медийный sentiment</strong> (что пишут СМИ) → <strong>объективные данные</strong> (как голосуют в ООН, сколько торгуют) → <strong>корреляция</strong> (насколько медиа отражают реальность). Цель — не просто измерять температуру, а предсказывать траекторию.</p>
    <div style="border-bottom:1px solid rgba(255,255,255,0.06); margin:24px 0;"></div>
    """, unsafe_allow_html=True)

    # ── Три паттерна ──
    st.markdown("""
    <h2 style="font-size:1.2rem; font-weight:600; margin:24px 0 12px;">🔬 Обнаруженные паттерны</h2>
    <p style="color:rgba(255,255,255,0.6); line-height:1.7; font-size:0.9rem; margin-bottom:16px;">
    Анализ медиапространства выявил три устойчивых паттерна поведения стран по отношению к России:</p>
    """, unsafe_allow_html=True)

    p1, p2, p3 = st.columns(3)
    with p1:
        st.markdown("""
        <div style="background:rgba(239,68,68,0.04); border:1px solid rgba(239,68,68,0.12); border-radius:12px; padding:18px; height:100%;">
            <div style="font-size:1.5rem; margin-bottom:6px;">🇦🇲</div>
            <div style="font-weight:600; color:#ef4444; margin-bottom:6px;">Спираль разочарования</div>
            <div style="color:rgba(255,255,255,0.5); font-size:0.82rem; line-height:1.6;">
            Необратимый разрыв. Каждое негативное событие усиливает следующее. Выход из ОДКБ, дистанцирование от российских интеграционных структур.</div>
        </div>
        """, unsafe_allow_html=True)
    with p2:
        st.markdown("""
        <div style="background:rgba(251,191,36,0.04); border:1px solid rgba(251,191,36,0.12); border-radius:12px; padding:18px; height:100%;">
            <div style="font-size:1.5rem; margin-bottom:6px;">🇰🇿</div>
            <div style="font-weight:600; color:#fbbf24; margin-bottom:6px;">Маятник многовекторности</div>
            <div style="color:rgba(255,255,255,0.5); font-size:0.82rem; line-height:1.6;">
            Управляемые качели. Балансирование между Россией, Китаем и Западом. Прагматичная дистанция без разрыва.</div>
        </div>
        """, unsafe_allow_html=True)
    with p3:
        st.markdown("""
        <div style="background:rgba(34,197,94,0.04); border:1px solid rgba(34,197,94,0.12); border-radius:12px; padding:18px; height:100%;">
            <div style="font-size:1.5rem; margin-bottom:6px;">🇺🇿</div>
            <div style="font-weight:600; color:#22c55e; margin-bottom:6px;">Прагматичный дрейф</div>
            <div style="color:rgba(255,255,255,0.5); font-size:0.82rem; line-height:1.6;">
            Сближение через экономику. Минимум политической риторики, максимум торговых соглашений и миграционного сотрудничества.</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("""<div style="border-bottom:1px solid rgba(255,255,255,0.06); margin:24px 0;"></div>""", unsafe_allow_html=True)

    # ── Архитектура ──
    st.markdown("""
    <h2 style="font-size:1.2rem; font-weight:600; margin:24px 0 12px;">⚙️ Архитектура системы</h2>
    """, unsafe_allow_html=True)

    a1, a2 = st.columns(2)
    with a1:
        st.markdown("""
        <div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06); border-radius:12px; padding:18px; height:100%;">
            <div style="font-weight:600; margin-bottom:10px;">📡 Сбор данных</div>
            <ul style="color:rgba(255,255,255,0.6); line-height:1.8; font-size:0.88rem; padding-left:18px; margin:0;">
                <li>149 источников из 10 стран</li>
                <li>RSS, веб-скрапинг, Telegram</li>
                <li>Многоязычный: русский + национальные языки (казахский, узбекский, грузинский, румынский и др.)</li>
                <li>Сбор каждые 30 минут</li>
                <li>Дедупликация через pg_trgm (порог 0.85)</li>
                <li>Подсчёт репринтов для оценки резонанса</li>
            </ul>
        </div>
        """, unsafe_allow_html=True)
    with a2:
        st.markdown("""
        <div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06); border-radius:12px; padding:18px; height:100%;">
            <div style="font-weight:600; margin-bottom:10px;">🧠 AI-анализ</div>
            <ul style="color:rgba(255,255,255,0.6); line-height:1.8; font-size:0.88rem; padding-left:18px; margin:0;">
                <li>LLM: Claude Sonnet 4 через OpenRouter</li>
                <li>Prompt v1.6 со строгим фильтром релевантности</li>
                <li>Sentiment -3..+3 (отношение страны к РФ, не тон)</li>
                <li>Action level 1-6 (от заявления до военных действий)</li>
                <li>Event clustering — автоматическая группировка событий</li>
                <li>Narrative Threads — LLM-генерация сюжетных линий</li>
            </ul>
        </div>
        """, unsafe_allow_html=True)

    a3, a4 = st.columns(2)
    with a3:
        st.markdown("""
        <div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06); border-radius:12px; padding:18px; margin-top:12px; height:100%;">
            <div style="font-weight:600; margin-bottom:10px;">📊 Объективные данные</div>
            <ul style="color:rgba(255,255,255,0.6); line-height:1.8; font-size:0.88rem; padding-left:18px; margin:0;">
                <li>🗳️ Голосования ООН — % совпадения с Россией (2014-2023)</li>
                <li>💰 Торговля — UN Comtrade + ФТС России</li>
                <li>🔗 Корреляция R² — медиа vs реальность</li>
                <li>Данные по всем 10 странам</li>
            </ul>
        </div>
        """, unsafe_allow_html=True)
    with a4:
        st.markdown("""
        <div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06); border-radius:12px; padding:18px; margin-top:12px; height:100%;">
            <div style="font-weight:600; margin-bottom:10px;">🧵 Narrative Threads</div>
            <ul style="color:rgba(255,255,255,0.6); line-height:1.8; font-size:0.88rem; padding-left:18px; margin:0;">
                <li>Кластеризация событий через pg_trgm similarity</li>
                <li>Arc phases: emerging → escalating → peak → cooling → resolved</li>
                <li>Resonance scoring — оценка медийного резонанса</li>
                <li>Автоматическая LLM-генерация нарративов</li>
            </ul>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("""<div style="border-bottom:1px solid rgba(255,255,255,0.06); margin:24px 0;"></div>""", unsafe_allow_html=True)

    # ── Температура ──
    st.markdown("""
    <h2 style="font-size:1.2rem; font-weight:600; margin:24px 0 12px;">🌡️ Формула температуры</h2>
    <p style="color:rgba(255,255,255,0.7); line-height:1.7; font-size:0.95rem;">
    Шкала от <span style="color:#ef4444; font-weight:600;">-100°</span> (враждебность) до <span style="color:#22c55e; font-weight:600;">+100°</span> (союзничество). Взвешенное среднее с экспоненциальным затуханием (τ=14 дней).</p>
    <p style="color:rgba(255,255,255,0.55); line-height:1.7; font-size:0.9rem; margin-top:8px;">
    <strong>Действия &gt; слова</strong> — подписание договора (×5) перевешивает 50 заявлений (×1).<br>
    <strong>Свежесть</strong> — экспоненциальный decay, свежие события важнее старых.<br>
    <strong>Тиры источников</strong> — аналитика (1.2) &gt; официальные (1.0) &gt; оппозиция (0.5).<br>
    <strong>Давление (divergence)</strong> — расхождение между тирами показывает «зазор» между официальной и народной картиной.<br>
    <strong>5 компонент</strong> — дипломатия, военное, экономика, культура, безопасность — отслеживаются отдельно.</p>
    """, unsafe_allow_html=True)

    st.markdown("""
    <table style="width:100%; border-collapse:collapse; margin:16px 0; font-size:0.88rem;">
        <tr style="background:rgba(255,255,255,0.04); color:#a1a1aa;">
            <th style="padding:10px 14px; text-align:left;">Уровень действия</th>
            <th style="padding:10px 14px; text-align:left;">Множитель</th>
            <th style="padding:10px 14px; text-align:left;">Пример</th>
        </tr>
        <tr style="color:rgba(255,255,255,0.6);"><td style="padding:8px 14px; border-top:1px solid rgba(255,255,255,0.04);">1 ⚡ Заявление</td><td style="padding:8px 14px;">×1</td><td style="padding:8px 14px;">Комментарий МИД</td></tr>
        <tr style="color:rgba(255,255,255,0.6);"><td style="padding:8px 14px; border-top:1px solid rgba(255,255,255,0.04);">2 ⚡⚡ Переговоры</td><td style="padding:8px 14px;">×3</td><td style="padding:8px 14px;">Визит делегации, встреча президентов</td></tr>
        <tr style="color:rgba(255,255,255,0.6);"><td style="padding:8px 14px; border-top:1px solid rgba(255,255,255,0.04);">3 ⚡⚡⚡ Соглашение</td><td style="padding:8px 14px;">×5</td><td style="padding:8px 14px;">Подписание договора, торговое соглашение</td></tr>
        <tr style="color:rgba(255,255,255,0.6);"><td style="padding:8px 14px; border-top:1px solid rgba(255,255,255,0.04);">4 💥 Санкции / Запрет</td><td style="padding:8px 14px;">×8</td><td style="padding:8px 14px;">Запрет на въезд, высылка дипломатов, нота протеста</td></tr>
        <tr style="color:rgba(255,255,255,0.6);"><td style="padding:8px 14px; border-top:1px solid rgba(255,255,255,0.04);">5 💥💥 Разрыв</td><td style="padding:8px 14px;">×12</td><td style="padding:8px 14px;">Выход из ОДКБ, разрыв дипотношений</td></tr>
        <tr style="color:rgba(255,255,255,0.6);"><td style="padding:8px 14px; border-top:1px solid rgba(255,255,255,0.04);">6 💥💥💥 Военные действия</td><td style="padding:8px 14px;">×15</td><td style="padding:8px 14px;">Размещение войск, военные столкновения</td></tr>
    </table>
    <div style="border-bottom:1px solid rgba(255,255,255,0.06); margin:24px 0;"></div>
    """, unsafe_allow_html=True)

    # ── Тиры источников ──
    st.markdown("""
    <h2 style="font-size:1.2rem; font-weight:600; margin:24px 0 12px;">📊 7 тиров источников</h2>
    <p style="color:rgba(255,255,255,0.6); line-height:1.7; font-size:0.9rem; margin-bottom:12px;">
    Каждый источник классифицирован в один из 7 тиров с разным весом влияния на итоговую температуру:</p>
    <table style="width:100%; border-collapse:collapse; margin:12px 0; font-size:0.88rem;">
        <tr style="background:rgba(255,255,255,0.04); color:#a1a1aa;">
            <th style="padding:10px 14px; text-align:left;">Тир</th>
            <th style="padding:10px 14px; text-align:center;">Вес</th>
            <th style="padding:10px 14px; text-align:left;">Описание</th>
            <th style="padding:10px 14px; text-align:left;">Примеры</th>
        </tr>
        <tr style="color:rgba(255,255,255,0.6);"><td style="padding:8px 14px; border-top:1px solid rgba(255,255,255,0.04);">🏛️ Официальные</td><td style="padding:8px 14px; text-align:center;">1.0</td><td style="padding:8px 14px;">Госагентства, МИД, правительства</td><td style="padding:8px 14px; color:#52525b;">ТАСС, Trend.az, Turkmenistan.gov</td></tr>
        <tr style="color:rgba(255,255,255,0.6);"><td style="padding:8px 14px; border-top:1px solid rgba(255,255,255,0.04);">📺 Мейнстрим</td><td style="padding:8px 14px; text-align:center;">1.0</td><td style="padding:8px 14px;">Крупные частные издания</td><td style="padding:8px 14px; color:#52525b;">Tengrinews, Gazeta.uz</td></tr>
        <tr style="color:rgba(255,255,255,0.6);"><td style="padding:8px 14px; border-top:1px solid rgba(255,255,255,0.04);">🔬 Аналитика</td><td style="padding:8px 14px; text-align:center; color:#f59e0b; font-weight:600;">1.2</td><td style="padding:8px 14px;">Think tanks, исследовательские центры</td><td style="padding:8px 14px; color:#52525b;">Carnegie, CABAR.asia</td></tr>
        <tr style="color:rgba(255,255,255,0.6);"><td style="padding:8px 14px; border-top:1px solid rgba(255,255,255,0.04);">📰 Независимые</td><td style="padding:8px 14px; text-align:center;">0.7</td><td style="padding:8px 14px;">Негосударственные медиа</td><td style="padding:8px 14px; color:#52525b;">Mediazona, CivilNet</td></tr>
        <tr style="color:rgba(255,255,255,0.6);"><td style="padding:8px 14px; border-top:1px solid rgba(255,255,255,0.04);">📢 Оппозиция</td><td style="padding:8px 14px; text-align:center;">0.5</td><td style="padding:8px 14px;">Оппозиционные медиа внутри стран</td><td style="padding:8px 14px; color:#52525b;">Batumi Today, ОппМедиа</td></tr>
        <tr style="color:rgba(255,255,255,0.6);"><td style="padding:8px 14px; border-top:1px solid rgba(255,255,255,0.04);">🌐 Внешние прокси</td><td style="padding:8px 14px; text-align:center;">0.3</td><td style="padding:8px 14px;">Западные медиа на языках региона</td><td style="padding:8px 14px; color:#52525b;">Radio Azattyq (RFE/RL)</td></tr>
        <tr style="color:rgba(255,255,255,0.6);"><td style="padding:8px 14px; border-top:1px solid rgba(255,255,255,0.04);">💬 Социальные</td><td style="padding:8px 14px; text-align:center;">0.5</td><td style="padding:8px 14px;">Telegram-каналы, блоги</td><td style="padding:8px 14px; color:#52525b;">Telegram-каналы стран</td></tr>
    </table>
    <div style="border-bottom:1px solid rgba(255,255,255,0.06); margin:24px 0;"></div>
    """, unsafe_allow_html=True)

    # ── Страны ──
    st.markdown("""
    <h2 style="font-size:1.2rem; font-weight:600; margin:24px 0 12px;">🌍 10 стран мониторинга</h2>
    <div style="display:grid; grid-template-columns:repeat(5, 1fr); gap:10px; margin:16px 0;">
        <div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06); border-radius:10px; padding:14px; text-align:center;">
            <div style="font-size:1.5rem;">🇰🇿</div><div style="font-size:0.82rem; color:#a1a1aa; margin-top:4px;">Казахстан</div></div>
        <div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06); border-radius:10px; padding:14px; text-align:center;">
            <div style="font-size:1.5rem;">🇦🇲</div><div style="font-size:0.82rem; color:#a1a1aa; margin-top:4px;">Армения</div></div>
        <div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06); border-radius:10px; padding:14px; text-align:center;">
            <div style="font-size:1.5rem;">🇺🇿</div><div style="font-size:0.82rem; color:#a1a1aa; margin-top:4px;">Узбекистан</div></div>
        <div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06); border-radius:10px; padding:14px; text-align:center;">
            <div style="font-size:1.5rem;">🇰🇬</div><div style="font-size:0.82rem; color:#a1a1aa; margin-top:4px;">Кыргызстан</div></div>
        <div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06); border-radius:10px; padding:14px; text-align:center;">
            <div style="font-size:1.5rem;">🇹🇯</div><div style="font-size:0.82rem; color:#a1a1aa; margin-top:4px;">Таджикистан</div></div>
        <div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06); border-radius:10px; padding:14px; text-align:center;">
            <div style="font-size:1.5rem;">🇹🇲</div><div style="font-size:0.82rem; color:#a1a1aa; margin-top:4px;">Туркменистан</div></div>
        <div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06); border-radius:10px; padding:14px; text-align:center;">
            <div style="font-size:1.5rem;">🇦🇿</div><div style="font-size:0.82rem; color:#a1a1aa; margin-top:4px;">Азербайджан</div></div>
        <div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06); border-radius:10px; padding:14px; text-align:center;">
            <div style="font-size:1.5rem;">🇬🇪</div><div style="font-size:0.82rem; color:#a1a1aa; margin-top:4px;">Грузия</div></div>
        <div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06); border-radius:10px; padding:14px; text-align:center;">
            <div style="font-size:1.5rem;">🇲🇩</div><div style="font-size:0.82rem; color:#a1a1aa; margin-top:4px;">Молдова</div></div>
        <div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06); border-radius:10px; padding:14px; text-align:center;">
            <div style="font-size:1.5rem;">🇧🇾</div><div style="font-size:0.82rem; color:#a1a1aa; margin-top:4px;">Беларусь</div></div>
    </div>
    <div style="border-bottom:1px solid rgba(255,255,255,0.06); margin:24px 0;"></div>
    """, unsafe_allow_html=True)

    # ── Pipeline визуализация ──
    st.markdown("""
    <h2 style="font-size:1.2rem; font-weight:600; margin:24px 0 12px;">🔄 Data Pipeline</h2>
    <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin:16px 0; font-size:0.85rem;">
        <span style="background:rgba(59,130,246,0.1); color:#60a5fa; padding:8px 14px; border-radius:8px; font-weight:500;">📡 RSS / Web / Telegram</span>
        <span style="color:#3f3f46;">→</span>
        <span style="background:rgba(139,92,246,0.1); color:#a78bfa; padding:8px 14px; border-radius:8px; font-weight:500;">🧹 Дедупликация (pg_trgm)</span>
        <span style="color:#3f3f46;">→</span>
        <span style="background:rgba(245,158,11,0.1); color:#fbbf24; padding:8px 14px; border-radius:8px; font-weight:500;">🧠 Claude Sonnet 4 (sentiment + action)</span>
        <span style="color:#3f3f46;">→</span>
        <span style="background:rgba(34,197,94,0.1); color:#86efac; padding:8px 14px; border-radius:8px; font-weight:500;">🌡️ Temperature calc (weighted, decay)</span>
        <span style="color:#3f3f46;">→</span>
        <span style="background:rgba(239,68,68,0.1); color:#fca5a5; padding:8px 14px; border-radius:8px; font-weight:500;">🧵 Narrative Threads</span>
        <span style="color:#3f3f46;">→</span>
        <span style="background:rgba(255,255,255,0.06); color:#e4e4e7; padding:8px 14px; border-radius:8px; font-weight:500;">📊 Dashboard + API</span>
    </div>
    <div style="border-bottom:1px solid rgba(255,255,255,0.06); margin:24px 0;"></div>
    """, unsafe_allow_html=True)

    # ── Возможности ──
    st.markdown("""
    <h2 style="font-size:1.2rem; font-weight:600; margin:24px 0 12px;">🎯 Возможности платформы</h2>
    """, unsafe_allow_html=True)

    f1, f2, f3 = st.columns(3)
    with f1:
        st.markdown("""
        <div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06); border-radius:12px; padding:18px; height:100%;">
            <div style="font-size:1.2rem; margin-bottom:8px;">🌡️</div>
            <div style="font-weight:600; font-size:0.95rem; margin-bottom:8px;">Температура в реальном времени</div>
            <div style="color:rgba(255,255,255,0.5); font-size:0.82rem; line-height:1.6;">
            Карта + карточки по странам. 5 компонент (дипломатия, военное, экономика, культура, безопасность). Тренды и аномалии.</div>
        </div>
        """, unsafe_allow_html=True)
    with f2:
        st.markdown("""
        <div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06); border-radius:12px; padding:18px; height:100%;">
            <div style="font-size:1.2rem; margin-bottom:8px;">📊</div>
            <div style="font-weight:600; font-size:0.95rem; margin-bottom:8px;">Аналитика с hard data</div>
            <div style="color:rgba(255,255,255,0.5); font-size:0.82rem; line-height:1.6;">
            Голосования ООН (2014-2023). Торговля с Россией (UN Comtrade + ФТС). R² корреляция: медиа vs реальность.</div>
        </div>
        """, unsafe_allow_html=True)
    with f3:
        st.markdown("""
        <div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06); border-radius:12px; padding:18px; height:100%;">
            <div style="font-size:1.2rem; margin-bottom:8px;">🧵</div>
            <div style="font-weight:600; font-size:0.95rem; margin-bottom:8px;">Сюжетные нити</div>
            <div style="color:rgba(255,255,255,0.5); font-size:0.82rem; line-height:1.6;">
            Автокластеризация событий. Отслеживание развития сюжетов (arc phases). LLM-нарративы. Resonance scoring.</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("""<div style="border-bottom:1px solid rgba(255,255,255,0.06); margin:24px 0;"></div>""", unsafe_allow_html=True)

    # ── Ограничения ──
    st.markdown("""
    <h2 style="font-size:1.2rem; font-weight:600; margin:24px 0 12px;">⚠️ Ограничения и честность</h2>
    <div style="background:rgba(239,68,68,0.06); border:1px solid rgba(239,68,68,0.12); border-radius:10px; padding:14px 18px; margin:12px 0;">
        <p style="color:rgba(255,255,255,0.8); font-size:0.9rem; margin:0;"><strong>Это исследовательский проект, а не истина в последней инстанции.</strong></p>
    </div>
    <ul style="color:rgba(255,255,255,0.6); line-height:1.8; font-size:0.88rem; padding-left:18px;">
        <li>Мультиязычный сбор с приоритетом на русский — национальные языки добавляются</li>
        <li>LLM может ошибаться в оценке — prompt v1.6 со строгим фильтром, но не идеален</li>
        <li>Объективные метрики (ООН, торговля) доступны с задержкой в 1-2 года</li>
        <li>Нет ground truth — планируется 1000 экспертных разметок для калибровки</li>
        <li>Корреляция не означает каузацию — медийная температура ≠ реальные отношения</li>
    </ul>
    <div style="border-bottom:1px solid rgba(255,255,255,0.06); margin:24px 0;"></div>
    """, unsafe_allow_html=True)

    # ── Roadmap ──
    st.markdown("""<h2 style="font-size:1.2rem; font-weight:600; margin:24px 0 12px;">🗺️ Дорожная карта</h2>""", unsafe_allow_html=True)

    r1, r2 = st.columns(2)
    with r1:
        st.markdown("""
        <div style="background:rgba(34,197,94,0.06); border:1px solid rgba(34,197,94,0.15); border-radius:10px; padding:14px; margin-bottom:10px;">
            <div style="font-weight:600; color:#22c55e; font-size:0.9rem;">✅ Фаза 1 — выполнена</div>
            <div style="color:rgba(255,255,255,0.5); font-size:0.82rem; margin-top:4px;">149 источников, 7 тиров, prompt v1.6, дедупликация, action_level 1-6, 5 компонент температуры</div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("""
        <div style="background:rgba(34,197,94,0.06); border:1px solid rgba(34,197,94,0.15); border-radius:10px; padding:14px; margin-bottom:10px;">
            <div style="font-weight:600; color:#22c55e; font-size:0.9rem;">✅ Фаза 2 — выполнена</div>
            <div style="color:rgba(255,255,255,0.5); font-size:0.82rem; margin-top:4px;">Национальные языки, UN Voting, торговые данные (Comtrade + ФТС), корреляционный анализ R²</div>
        </div>
        """, unsafe_allow_html=True)
    with r2:
        st.markdown("""
        <div style="background:rgba(59,130,246,0.06); border:1px solid rgba(59,130,246,0.12); border-radius:10px; padding:14px; margin-bottom:10px;">
            <div style="font-weight:600; color:#60a5fa; font-size:0.9rem;">🔄 Фаза 3 — в работе</div>
            <div style="color:rgba(255,255,255,0.5); font-size:0.82rem; margin-top:4px;">Narrative Threads, алерт-система, Telegram-бот для уведомлений, Credibility Score</div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("""
        <div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06); border-radius:10px; padding:14px; margin-bottom:10px;">
            <div style="font-weight:600; color:#a1a1aa; font-size:0.9rem;">📋 Фаза 4 — планируется</div>
            <div style="color:rgba(255,255,255,0.4); font-size:0.82rem; margin-top:4px;">Multi-model ensemble, Temporal Graph Networks, предиктивная аналитика, confidence intervals</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("""<div style="border-bottom:1px solid rgba(255,255,255,0.06); margin:24px 0;"></div>""", unsafe_allow_html=True)

    # ── Технологии ──
    st.markdown("""
    <h2 style="font-size:1.2rem; font-weight:600; margin:24px 0 12px;">🛠️ Технологии</h2>
    <div style="display:flex; flex-wrap:wrap; gap:8px; margin:12px 0;">
        <span style="background:rgba(59,130,246,0.1); border:1px solid rgba(59,130,246,0.2); border-radius:20px; padding:5px 14px; font-size:0.82rem; color:#60a5fa;">Python 3.12</span>
        <span style="background:rgba(59,130,246,0.1); border:1px solid rgba(59,130,246,0.2); border-radius:20px; padding:5px 14px; font-size:0.82rem; color:#60a5fa;">PostgreSQL + TimescaleDB</span>
        <span style="background:rgba(59,130,246,0.1); border:1px solid rgba(59,130,246,0.2); border-radius:20px; padding:5px 14px; font-size:0.82rem; color:#60a5fa;">Docker Compose</span>
        <span style="background:rgba(245,158,11,0.1); border:1px solid rgba(245,158,11,0.2); border-radius:20px; padding:5px 14px; font-size:0.82rem; color:#fbbf24;">Claude Sonnet 4</span>
        <span style="background:rgba(245,158,11,0.1); border:1px solid rgba(245,158,11,0.2); border-radius:20px; padding:5px 14px; font-size:0.82rem; color:#fbbf24;">OpenRouter API</span>
        <span style="background:rgba(34,197,94,0.1); border:1px solid rgba(34,197,94,0.2); border-radius:20px; padding:5px 14px; font-size:0.82rem; color:#86efac;">FastAPI</span>
        <span style="background:rgba(34,197,94,0.1); border:1px solid rgba(34,197,94,0.2); border-radius:20px; padding:5px 14px; font-size:0.82rem; color:#86efac;">Streamlit 1.41</span>
        <span style="background:rgba(34,197,94,0.1); border:1px solid rgba(34,197,94,0.2); border-radius:20px; padding:5px 14px; font-size:0.82rem; color:#86efac;">Plotly</span>
        <span style="background:rgba(139,92,246,0.1); border:1px solid rgba(139,92,246,0.2); border-radius:20px; padding:5px 14px; font-size:0.82rem; color:#a78bfa;">pg_trgm</span>
        <span style="background:rgba(139,92,246,0.1); border:1px solid rgba(139,92,246,0.2); border-radius:20px; padding:5px 14px; font-size:0.82rem; color:#a78bfa;">trafilatura</span>
        <span style="background:rgba(139,92,246,0.1); border:1px solid rgba(139,92,246,0.2); border-radius:20px; padding:5px 14px; font-size:0.82rem; color:#a78bfa;">UN Comtrade API</span>
        <span style="background:rgba(139,92,246,0.1); border:1px solid rgba(139,92,246,0.2); border-radius:20px; padding:5px 14px; font-size:0.82rem; color:#a78bfa;">httpx + tenacity</span>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div style="text-align:center; margin:40px 0 20px; padding:24px; background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06); border-radius:16px;">
        <div style="font-size:0.95rem; color:#a1a1aa; margin-bottom:8px;">Исследовательский проект</div>
        <div style="font-size:1.2rem; font-weight:600; color:#e4e4e7;">Михаил Ткаченко</div>
        <div style="margin-top:12px;">
            <a href="https://github.com/milkmike/GEO_PULSE" target="_blank"
               style="color:#60a5fa; text-decoration:none; font-size:0.9rem; border-bottom:1px solid rgba(96,165,250,0.3);">
               github.com/milkmike/GEO_PULSE</a>
        </div>
    </div>
    """, unsafe_allow_html=True)

