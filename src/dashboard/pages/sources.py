"""Страница управления источниками."""
import os
import httpx
import pandas as pd
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000")

st.set_page_config(page_title="📡 Источники", page_icon="📡", layout="wide")

COUNTRY_NAMES = {
    "KZ": "🇰🇿 Казахстан", "AM": "🇦🇲 Армения", "UZ": "🇺🇿 Узбекистан",
    "KG": "🇰🇬 Кыргызстан", "TJ": "🇹🇯 Таджикистан", "TM": "🇹🇲 Туркменистан",
    "AZ": "🇦🇿 Азербайджан", "GE": "🇬🇪 Грузия", "MD": "🇲🇩 Молдова", "BY": "🇧🇾 Беларусь",
}

SOURCE_TYPES = {"rss": "RSS", "web": "Web", "telegram": "Telegram"}
TIERS = {
    "official": "🏛️ Официальный",
    "mainstream": "📰 Mainstream",
    "analytics": "🔍 Аналитика",
    "social": "💬 Соцсети/Telegram",
    "opposition": "📢 Оппозиция",
}

LANGUAGES = {"ru": "Русский", "en": "English", "kk": "Қазақша", "uz": "O'zbek",
             "hy": "Հայերեն", "ka": "ქართული", "ro": "Română", "az": "Azərbaycan",
             "tg": "Тоҷикӣ", "tk": "Türkmen"}


def api_call(method: str, endpoint: str, json=None, params=None):
    try:
        r = httpx.request(method, f"{API_URL}{endpoint}", json=json, params=params, timeout=15.0)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            detail = e.response.json().get("detail", "")
        except Exception:
            pass
        st.error(f"Ошибка API: {e.response.status_code} — {detail or e}")
        return None
    except Exception as e:
        st.error(f"Ошибка соединения: {e}")
        return None


# ───── Header ─────
st.title("📡 Управление источниками")

# ───── Load sources ─────
data = api_call("GET", "/api/v1/sources")
if not data:
    st.warning("Не удалось загрузить источники. Проверьте API.")
    st.stop()

sources = data["sources"]

# ───── Stats summary ─────
total = len(sources)
active = sum(1 for s in sources if s["active"])
total_articles = sum(s.get("article_count", 0) for s in sources)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Всего источников", total)
c2.metric("Активных", active)
c3.metric("Отключённых", total - active)
c4.metric("Статей собрано", total_articles)

st.markdown("---")

# ───── Filters ─────
col_f1, col_f2, col_f3 = st.columns(3)
with col_f1:
    filter_country = st.multiselect("Страна", list(COUNTRY_NAMES.keys()),
                                     format_func=lambda x: COUNTRY_NAMES[x])
with col_f2:
    filter_type = st.multiselect("Тип", list(SOURCE_TYPES.keys()),
                                  format_func=lambda x: SOURCE_TYPES[x])
with col_f3:
    filter_tier = st.multiselect("Тир", list(TIERS.keys()),
                                  format_func=lambda x: TIERS[x])
col_f4, _ = st.columns(2)
with col_f4:
    filter_status = st.selectbox("Статус", ["Все", "Активные", "Отключённые"])

filtered = sources
if filter_country:
    filtered = [s for s in filtered if s["country_code"] in filter_country]
if filter_type:
    filtered = [s for s in filtered if s["source_type"] in filter_type]
if filter_tier:
    filtered = [s for s in filtered if s.get("tier", "mainstream") in filter_tier]
if filter_status == "Активные":
    filtered = [s for s in filtered if s["active"]]
elif filter_status == "Отключённые":
    filtered = [s for s in filtered if not s["active"]]

# ───── Sources table ─────
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
        rel_pct = 0
        if s.get("article_count", 0) > 0 and s.get("relevant_count", 0) > 0:
            rel_pct = round(s["relevant_count"] / s["article_count"] * 100, 1)

        rows.append({
            "": status_icon,
            "Страна": COUNTRY_NAMES.get(s["country_code"], s["country_code"]),
            "Название": s["name"],
            "URL": s["url"],
            "Тип": SOURCE_TYPES.get(s["source_type"], s["source_type"]),
            "Тир": TIERS.get(s.get("tier", "mainstream"), s.get("tier", "mainstream")),
            "Вес": s["weight"],
            "Язык": s["language"],
            "Статей": s.get("article_count", 0),
            "Релев.%": rel_pct,
            "Ср.тон": avg_sent_str,
            "Последний сбор": last,
            "id": s["id"],
        })

    df = pd.DataFrame(rows)
    st.dataframe(
        df.drop(columns=["id"]),
        use_container_width=True,
        hide_index=True,
        height=min(len(rows) * 40 + 40, 600),
    )
else:
    st.info("Нет источников, соответствующих фильтрам.")

st.markdown("---")

# ───── Actions ─────
tab_add, tab_edit, tab_manage = st.tabs(["➕ Добавить источник", "✏️ Редактировать", "⚙️ Управление"])

# ─── Add source ───
with tab_add:
    st.subheader("Новый источник")
    with st.form("add_source", clear_on_submit=True):
        ac1, ac2 = st.columns(2)
        with ac1:
            new_country = st.selectbox("Страна", list(COUNTRY_NAMES.keys()),
                                        format_func=lambda x: COUNTRY_NAMES[x], key="add_country")
            new_name = st.text_input("Название", key="add_name")
            new_url = st.text_input("URL", key="add_url", placeholder="https://...")
            new_type = st.selectbox("Тип", list(SOURCE_TYPES.keys()),
                                     format_func=lambda x: SOURCE_TYPES[x], key="add_type")
        with ac2:
            new_tier = st.selectbox("Тир", list(TIERS.keys()),
                                     format_func=lambda x: TIERS[x], key="add_tier")
            new_weight = st.slider("Вес", 0.5, 2.0, 1.0, 0.1, key="add_weight")
            new_lang = st.selectbox("Язык", list(LANGUAGES.keys()),
                                     format_func=lambda x: LANGUAGES[x], key="add_lang")
            new_active = st.checkbox("Активен", value=True, key="add_active")

        col_btn1, col_btn2 = st.columns(2)
        submitted = col_btn1.form_submit_button("💾 Сохранить", use_container_width=True)
        test_btn = col_btn2.form_submit_button("🧪 Тестировать", use_container_width=True)

    if submitted and new_name and new_url:
        result = api_call("POST", "/api/v1/sources", json={
            "name": new_name, "url": new_url, "country_code": new_country,
            "source_type": new_type, "weight": new_weight, "tier": new_tier,
            "language": new_lang, "active": new_active, "config": {},
        })
        if result:
            st.success(f"✅ Источник «{new_name}» добавлен!")
            st.rerun()

    if test_btn and new_url:
        with st.spinner("Тестируем источник..."):
            result = api_call("POST", "/api/v1/sources/test-url", json={
                "name": new_name or "test", "url": new_url,
                "country_code": new_country or "KZ",
                "source_type": new_type or "rss",
                "weight": new_weight, "language": new_lang or "ru",
            })
        if result:
            if result.get("success"):
                st.success(f"✅ {result['message']}")
                if result.get("sample"):
                    st.json(result["sample"])
            else:
                st.error(f"❌ {result.get('message', 'Ошибка тестирования')}")

# ─── Edit source ───
with tab_edit:
    if not sources:
        st.info("Нет источников для редактирования.")
    else:
        source_options = {s["id"]: f"{COUNTRY_NAMES.get(s['country_code'], s['country_code'])} — {s['name']}" for s in sources}
        selected_id = st.selectbox("Выберите источник", list(source_options.keys()),
                                    format_func=lambda x: source_options[x], key="edit_select")

        sel = next((s for s in sources if s["id"] == selected_id), None)
        if sel:
            with st.form("edit_source"):
                ec1, ec2 = st.columns(2)
                with ec1:
                    edit_country = st.selectbox("Страна", list(COUNTRY_NAMES.keys()),
                                                 index=list(COUNTRY_NAMES.keys()).index(sel["country_code"]),
                                                 format_func=lambda x: COUNTRY_NAMES[x], key="edit_country")
                    edit_name = st.text_input("Название", value=sel["name"], key="edit_name")
                    edit_url = st.text_input("URL", value=sel["url"], key="edit_url")
                    edit_type = st.selectbox("Тип", list(SOURCE_TYPES.keys()),
                                              index=list(SOURCE_TYPES.keys()).index(sel["source_type"]),
                                              format_func=lambda x: SOURCE_TYPES[x], key="edit_type")
                    sel_tier = sel.get("tier", "mainstream")
                    tier_keys = list(TIERS.keys())
                    tier_idx = tier_keys.index(sel_tier) if sel_tier in tier_keys else 1
                    edit_tier = st.selectbox("Тир", tier_keys, index=tier_idx,
                                              format_func=lambda x: TIERS[x], key="edit_tier")
                with ec2:
                    edit_weight = st.slider("Вес", 0.5, 2.0, float(sel["weight"]), 0.1, key="edit_weight")
                    lang_keys = list(LANGUAGES.keys())
                    lang_idx = lang_keys.index(sel["language"]) if sel["language"] in lang_keys else 0
                    edit_lang = st.selectbox("Язык", lang_keys, index=lang_idx,
                                              format_func=lambda x: LANGUAGES[x], key="edit_lang")
                    edit_active = st.checkbox("Активен", value=sel["active"], key="edit_active")

                if st.form_submit_button("💾 Сохранить изменения", use_container_width=True):
                    result = api_call("PUT", f"/api/v1/sources/{selected_id}", json={
                        "name": edit_name, "url": edit_url, "country_code": edit_country,
                        "source_type": edit_type, "weight": edit_weight, "tier": edit_tier,
                        "language": edit_lang, "active": edit_active,
                    })
                    if result:
                        st.success(f"✅ Источник «{edit_name}» обновлён!")
                        st.rerun()

# ─── Manage (toggle/delete/test) ───
with tab_manage:
    if not sources:
        st.info("Нет источников.")
    else:
        for s in filtered:
            with st.container():
                mc1, mc2, mc3, mc4, mc5 = st.columns([3, 1, 1, 1, 1])
                mc1.write(f"**{s['name']}** ({s['country_code']}) — {s.get('article_count', 0)} статей")

                status_label = "🟢 Вкл" if s["active"] else "⚫ Выкл"
                if mc2.button(status_label, key=f"toggle_{s['id']}", use_container_width=True):
                    result = api_call("PATCH", f"/api/v1/sources/{s['id']}/toggle")
                    if result:
                        st.toast(result["message"])
                        st.rerun()

                if mc3.button("🧪", key=f"test_{s['id']}", help="Тестировать", use_container_width=True):
                    with st.spinner("Тестируем..."):
                        result = api_call("POST", f"/api/v1/sources/{s['id']}/test")
                    if result:
                        if result.get("success"):
                            st.success(f"✅ {result['message']}")
                            if result.get("sample"):
                                st.json(result["sample"])
                        else:
                            st.error(f"❌ {result.get('message')}")

                if mc4.button("📊", key=f"stats_{s['id']}", help="Статистика", use_container_width=True):
                    detail = api_call("GET", f"/api/v1/sources/{s['id']}")
                    if detail:
                        st.markdown(f"""
                        **{detail['name']}** — подробная статистика:
                        - 📰 Всего статей: **{detail.get('article_count', 0)}**
                        - 📅 Среднее в день: **{detail.get('avg_articles_per_day', 0)}**
                        - 🎯 Релевантных: **{detail.get('relevant_count', 0)}** ({detail.get('relevance_pct', 0)}%)
                        - 💬 Средний тон: **{detail.get('avg_sentiment', '—')}**
                        - 🕐 Последний сбор: {detail.get('last_collected', '—')}
                        """)

                if mc5.button("🗑️", key=f"del_{s['id']}", help="Удалить", use_container_width=True):
                    st.session_state[f"confirm_del_{s['id']}"] = True

                if st.session_state.get(f"confirm_del_{s['id']}"):
                    st.warning(f"Удалить «{s['name']}» и все связанные статьи?")
                    dc1, dc2 = st.columns(2)
                    if dc1.button("✅ Да, удалить", key=f"confirm_yes_{s['id']}"):
                        result = api_call("DELETE", f"/api/v1/sources/{s['id']}")
                        if result:
                            st.toast(result["message"])
                            del st.session_state[f"confirm_del_{s['id']}"]
                            st.rerun()
                    if dc2.button("❌ Отмена", key=f"confirm_no_{s['id']}"):
                        del st.session_state[f"confirm_del_{s['id']}"]
                        st.rerun()

                st.divider()
