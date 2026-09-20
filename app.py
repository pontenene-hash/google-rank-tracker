from __future__ import annotations

import os
import random
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import altair as alt
import pandas as pd
import requests
import streamlit as st


APP_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("RANK_TRACKER_DB", APP_DIR / "rank_history.db"))
SERPER_ENDPOINT = "https://google.serper.dev/search"


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS rankings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                checked_at TEXT NOT NULL,
                target_url TEXT NOT NULL,
                keyword TEXT NOT NULL,
                rank INTEGER,
                matched_url TEXT,
                provider TEXT NOT NULL
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rankings_lookup "
            "ON rankings(target_url, keyword, checked_at)"
        )


def normalize_url(value: str) -> str:
    value = value.strip()
    if value and not value.startswith(("http://", "https://")):
        value = "https://" + value
    parsed = urlparse(value)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/") or "/"
    return f"{host}{path}"


def is_match(result_url: str, target_url: str, match_mode: str) -> bool:
    result = normalize_url(result_url)
    target = normalize_url(target_url)
    if match_mode == "ドメイン全体":
        return result.split("/", 1)[0] == target.split("/", 1)[0]
    return result == target


def serper_rank(keyword: str, target_url: str, api_key: str, match_mode: str) -> tuple[int | None, str | None]:
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    # Serper accepts up to 100 organic results in one request.
    payload = {"q": keyword, "gl": "jp", "hl": "ja", "num": 100}
    response = requests.post(SERPER_ENDPOINT, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    if data.get("message"):
        raise RuntimeError(data["message"])
    for fallback_position, item in enumerate(data.get("organic", []), start=1):
        link = item.get("link", "")
        if link and is_match(link, target_url, match_mode):
            return int(item.get("position", fallback_position)), link
    return None, None


def demo_rank(keyword: str) -> tuple[int | None, str | None]:
    # Stable-looking sample values for UI evaluation; never presented as live data.
    seed = sum(ord(char) for char in keyword) + date.today().toordinal()
    rng = random.Random(seed)
    rank = rng.randint(1, 65)
    return rank, "デモデータ"


def save_result(target_url: str, keyword: str, rank: int | None, matched_url: str | None, provider: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO rankings(checked_at, target_url, keyword, rank, matched_url, provider) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), target_url, keyword, rank, matched_url, provider),
        )


def load_history(target_url: str, days: int = 365) -> pd.DataFrame:
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(
            "SELECT checked_at, keyword, rank, matched_url, provider FROM rankings "
            "WHERE target_url = ? AND checked_at >= ? ORDER BY checked_at",
            conn,
            params=(target_url, since),
            parse_dates=["checked_at"],
        )


def latest_rows(history: pd.DataFrame) -> pd.DataFrame:
    if history.empty:
        return history
    latest = history.sort_values("checked_at").groupby("keyword", as_index=False).tail(1).copy()
    latest["順位"] = latest["rank"].apply(lambda value: f"{int(value)}位" if pd.notna(value) else "100位圏外")
    latest["計測日時"] = latest["checked_at"].dt.strftime("%Y-%m-%d %H:%M")
    latest["検索ワード"] = latest["keyword"]
    latest["一致したURL"] = latest["matched_url"].fillna("—")
    return latest[["検索ワード", "順位", "計測日時", "一致したURL"]].sort_values("検索ワード")


st.set_page_config(page_title="Google順位チェッカー", page_icon="📈", layout="wide")
init_db()

st.markdown(
    """<style>
    .block-container {max-width: 1180px; padding-top: 2rem;}
    [data-testid="stMetric"] {background:#fff; border:1px solid #e6e9ef; padding:16px; border-radius:14px;}
    .hero {padding:1.25rem 1.4rem; border-radius:18px; color:white;
           background:linear-gradient(120deg,#3155d9,#11a9a0); margin-bottom:1.2rem;}
    .hero h1 {margin:0 0 .2rem 0; font-size:2rem;}
    .hero p {margin:0; opacity:.92;}
    </style>""",
    unsafe_allow_html=True,
)
st.markdown('<div class="hero"><h1>Google順位チェッカー</h1><p>検索ワードごとの掲載順位を記録し、1年間の変化を見える化します。</p></div>', unsafe_allow_html=True)

with st.sidebar:
    st.header("⚙️ 設定")
    api_key = st.text_input("Serper APIキー", value=os.getenv("SERPER_API_KEY", ""), type="password")
    demo_mode = st.toggle("デモモード", value=not bool(api_key), help="API通信を行わずサンプル順位で画面を確認します。")
    match_mode = st.radio(
        "URLの照合方法",
        ["入力ページのみ", "ドメイン全体"],
        index=1,
        help="ページ単位か、同じサイト内の全ページを対象にするかを選びます。",
    )
    st.caption("検索地域：日本 / 言語：日本語 / 取得範囲：上位100件")

def render_tracker(tab_key: str, default_url: str) -> None:
    left, right = st.columns([1.15, 1])
    with left:
        target_url = st.text_input(
            "特定のWebページ",
            value=default_url,
            key=f"target_url_{tab_key}",
        )
    with right:
        keyword_text = st.text_area(
            "検索ワードの一覧表（1行に1語）",
            height=130,
            placeholder="せんげん台 整体\n越谷市 整骨院\n春日部市 鍼灸",
            key=f"keywords_{tab_key}",
        )

    keywords = list(dict.fromkeys(line.strip() for line in keyword_text.splitlines() if line.strip()))
    run = st.button(
        "🔍 分析開始",
        type="primary",
        use_container_width=True,
        key=f"run_{tab_key}",
    )

    if run:
        if not target_url.strip():
            st.error("WebページのURLを入力してください。")
        elif not keywords:
            st.error("検索ワードを1つ以上入力してください。")
        elif not demo_mode and not api_key:
            st.error("本番計測にはSerper APIキーが必要です。左側の設定欄に入力してください。")
        else:
            progress = st.progress(0, text="計測を開始しています…")
            errors: list[str] = []
            provider = "demo" if demo_mode else "serper"
            for index, keyword in enumerate(keywords, start=1):
                try:
                    if demo_mode:
                        rank, matched_url = demo_rank(keyword)
                        time.sleep(0.08)
                    else:
                        rank, matched_url = serper_rank(keyword, target_url, api_key, match_mode)
                    save_result(target_url.strip(), keyword, rank, matched_url, provider)
                except Exception as exc:
                    errors.append(f"{keyword}: {exc}")
                progress.progress(index / len(keywords), text=f"{index}/{len(keywords)}件を計測中：{keyword}")
            progress.empty()
            if errors:
                st.error("一部の計測に失敗しました。\n\n" + "\n".join(errors))
            else:
                label = "デモデータを保存しました" if demo_mode else "最新順位を保存しました"
                st.success(f"{len(keywords)}件の{label}。")

    if target_url.strip():
        history = load_history(target_url.strip())
        if not history.empty:
            valid = history.dropna(subset=["rank"])
            latest = latest_rows(history)
            m1, m2, m3 = st.columns(3)
            m1.metric("登録キーワード", history["keyword"].nunique())
            m2.metric("10位以内", int((valid.sort_values("checked_at").groupby("keyword").tail(1)["rank"] <= 10).sum()))
            m3.metric("最新の平均順位", f'{valid.sort_values("checked_at").groupby("keyword").tail(1)["rank"].mean():.1f}位' if not valid.empty else "—")

            st.subheader("最新の検索順位")
            st.dataframe(latest, use_container_width=True, hide_index=True)

            st.subheader("過去1年間の順位変動")
            available_keywords = sorted(history["keyword"].unique())
            chosen = st.multiselect(
                "グラフに表示する検索ワード",
                available_keywords,
                default=available_keywords[:5],
                key=f"chart_keywords_{tab_key}",
            )
            chart_data = history[history["keyword"].isin(chosen)].dropna(subset=["rank"]).copy()
            if chart_data.empty:
                st.info("表示できる順位履歴がありません。")
            else:
                chart = (
                    alt.Chart(chart_data)
                    .mark_line(point=True)
                    .encode(
                        x=alt.X("checked_at:T", title="計測日"),
                        y=alt.Y("rank:Q", title="順位", scale=alt.Scale(reverse=True, domain=[1, max(100, int(chart_data['rank'].max()))])),
                        color=alt.Color("keyword:N", title="検索ワード"),
                        tooltip=[alt.Tooltip("checked_at:T", title="計測日時"), alt.Tooltip("keyword:N", title="検索ワード"), alt.Tooltip("rank:Q", title="順位")],
                    )
                    .properties(height=420)
                    .interactive()
                )
                st.altair_chart(chart, use_container_width=True)

            csv = history.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                "📥 履歴をCSVでダウンロード",
                csv,
                f"rank_history_{tab_key}.csv",
                "text/csv",
                key=f"download_{tab_key}",
            )
        else:
            st.info("「分析開始」を押すと、ここに最新順位と履歴グラフが表示されます。")
    else:
        st.info("URLと検索ワードを入力して分析を開始してください。")


site1_tab, site2_tab, site3_tab = st.tabs(
    ["ponte-nene.jp", "ponte-aroma.jp", "ponte-nene.net"]
)
with site1_tab:
    render_tracker("site1", "https://ponte-nene.jp/")
with site2_tab:
    render_tracker("site2", "https://ponte-aroma.jp/")
with site3_tab:
    render_tracker("site3", "https://ponte-nene.net/")

with st.expander("ご利用前の注意"):
    st.markdown("""
    - 検索順位は地域・端末・時刻などで変わるため、実際の個人検索と差が出ることがあります。
    - 「100位圏外」は上位100件に対象URLが見つからなかった状態です。
    - APIキーは画面入力中のみ利用し、データベースには保存しません。
    - 大量のキーワードを頻繁に計測するとAPI利用料が増えるため、契約プランをご確認ください。
    """)
