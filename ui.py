import os
import sqlite3
from datetime import datetime

import pandas as pd
import streamlit as st
import plotly.express as px
from dotenv import load_dotenv


load_dotenv()

DB_PATH = os.getenv("DB_PATH", "/app/data/radar.db")
UI_PASSWORD = os.getenv("UI_PASSWORD", "")


def get_conn():
    return sqlite3.connect(DB_PATH, timeout=30)


def read_sql(sql: str, params=None) -> pd.DataFrame:
    try:
        with get_conn() as con:
            return pd.read_sql_query(sql, con, params=params or [])
    except Exception:
        return pd.DataFrame()


def execute(sql: str, params=None):
    with get_conn() as con:
        con.execute(sql, params or [])
        con.commit()


def get_setting(key: str, default: str = "") -> str:
    df = read_sql("SELECT value FROM settings WHERE key=?", [key])
    if df.empty:
        return default
    return str(df.iloc[0]["value"])


def set_setting(key: str, value: str):
    execute("""
    INSERT INTO settings(key,value,updated_at)
    VALUES(?,?,strftime('%s','now'))
    ON CONFLICT(key) DO UPDATE SET
        value=excluded.value,
        updated_at=excluded.updated_at
    """, [key, value])


def fmt_pct(x):
    if x is None or pd.isna(x):
        return ""
    return f"{x * 100:.4f}%"


def fmt_pct2(x):
    if x is None or pd.isna(x):
        return ""
    return f"{x * 100:.2f}%"


st.set_page_config(
    page_title="Funding Radar V2",
    page_icon="📡",
    layout="wide",
)


# =========================
# Simple Password
# =========================
if UI_PASSWORD:
    if "authed" not in st.session_state:
        st.session_state.authed = False

    if not st.session_state.authed:
        st.title("🔐 Funding Radar Login")
        password = st.text_input("請輸入 UI 密碼", type="password")
        if st.button("登入"):
            if password == UI_PASSWORD:
                st.session_state.authed = True
                st.rerun()
            else:
                st.error("密碼錯誤")
        st.stop()


st.title("📡 Funding Radar V2")
st.caption("Binance U 本位資金費率期現套利雷達｜Zeabur｜SQLite｜Telegram｜Streamlit")


# =========================
# Sidebar Control
# =========================
st.sidebar.header("控制面板")

paused = get_setting("scanner_paused", "false").lower() == "true"

if paused:
    st.sidebar.warning("掃描已暫停")
    if st.sidebar.button("▶️ 恢復掃描"):
        set_setting("scanner_paused", "false")
        st.rerun()
else:
    st.sidebar.success("掃描中")
    if st.sidebar.button("⏸ 暫停掃描"):
        set_setting("scanner_paused", "true")
        st.rerun()

st.sidebar.divider()
st.sidebar.subheader("黑名單")

new_symbol = st.sidebar.text_input("Symbol", placeholder="DOGEUSDT")
reason = st.sidebar.text_input("原因", placeholder="不想交易")

if st.sidebar.button("加入黑名單"):
    if new_symbol:
        execute("""
        INSERT OR REPLACE INTO blacklist(symbol,reason,created_at)
        VALUES(?,?,strftime('%s','now'))
        """, [new_symbol.upper(), reason])
        st.sidebar.success(f"已加入 {new_symbol.upper()}")
        st.rerun()

black_df = read_sql("SELECT * FROM blacklist ORDER BY created_at DESC")

if not black_df.empty:
    remove = st.sidebar.selectbox("移除黑名單", black_df["symbol"].tolist())
    if st.sidebar.button("移除選擇項目"):
        execute("DELETE FROM blacklist WHERE symbol=?", [remove])
        st.sidebar.success(f"已移除 {remove}")
        st.rerun()


tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "✅ 最新訊號",
    "📈 歷史圖表",
    "📝 下單意圖",
    "💼 交易紀錄",
    "⚙️ 系統資料",
])


# =========================
# Tab 1
# =========================
with tab1:
    st.subheader("最新 PASS 訊號")

    pass_df = read_sql("""
    SELECT *
    FROM scan_results
    WHERE status='PASS'
    ORDER BY ts DESC, payback_days ASC
    LIMIT 100
    """)

    if pass_df.empty:
        st.info("目前沒有 PASS 訊號。請等待掃描完成，或檢查條件是否太嚴格。")
    else:
        show = pass_df.copy()
        show["time"] = pd.to_datetime(show["ts"], unit="s")
        show["current_pct"] = show["current_funding_rate"].apply(fmt_pct)
        show["avg7_pct"] = show["avg_funding_rate_7d"].apply(fmt_pct)
        show["apy_pct"] = show["apy"].apply(fmt_pct2)
        show["basis_pct"] = show["basis_rate"].apply(fmt_pct)
        show["slip_pct"] = show["total_slippage"].apply(fmt_pct)

        st.dataframe(
            show[[
                "time",
                "symbol",
                "signal_level",
                "current_pct",
                "avg7_pct",
                "apy_pct",
                "payback_days",
                "basis_pct",
                "slip_pct",
                "quote_volume",
                "open_interest_notional",
            ]],
            use_container_width=True,
        )

    st.divider()
    st.subheader("高風險觀察 WATCH")

    watch_df = read_sql("""
    SELECT *
    FROM scan_results
    WHERE status='WATCH'
    ORDER BY ts DESC, current_funding_rate DESC
    LIMIT 100
    """)

    if watch_df.empty:
        st.info("目前沒有 WATCH 訊號。")
    else:
        show = watch_df.copy()
        show["time"] = pd.to_datetime(show["ts"], unit="s")
        show["current_pct"] = show["current_funding_rate"].apply(fmt_pct)
        st.dataframe(
            show[[
                "time",
                "symbol",
                "signal_level",
                "current_pct",
                "basis_rate",
                "total_slippage",
                "fail_reason",
            ]],
            use_container_width=True,
        )


# =========================
# Tab 2
# =========================
with tab2:
    st.subheader("歷史圖表")

    symbols_df = read_sql("""
    SELECT DISTINCT symbol
    FROM scan_results
    ORDER BY symbol
    """)

    if symbols_df.empty:
        st.info("尚無歷史資料。")
    else:
        symbol = st.selectbox("選擇 Symbol", symbols_df["symbol"].tolist())

        hist = read_sql("""
        SELECT *
        FROM scan_results
        WHERE symbol=?
        ORDER BY ts ASC
        """, [symbol])

        if hist.empty:
            st.info("沒有資料")
        else:
            hist["time"] = pd.to_datetime(hist["ts"], unit="s")

            c1, c2 = st.columns(2)

            with c1:
                fig = px.line(
                    hist,
                    x="time",
                    y=["current_funding_rate", "avg_funding_rate_7d"],
                    title=f"{symbol} Funding Rate"
                )
                st.plotly_chart(fig, use_container_width=True)

            with c2:
                fig = px.line(
                    hist,
                    x="time",
                    y="payback_days",
                    title=f"{symbol} Payback Days"
                )
                st.plotly_chart(fig, use_container_width=True)

            c3, c4 = st.columns(2)

            with c3:
                fig = px.line(
                    hist,
                    x="time",
                    y="basis_rate",
                    title=f"{symbol} Basis"
                )
                st.plotly_chart(fig, use_container_width=True)

            with c4:
                fig = px.line(
                    hist,
                    x="time",
                    y="total_slippage",
                    title=f"{symbol} Total Slippage"
                )
                st.plotly_chart(fig, use_container_width=True)

            st.dataframe(hist.tail(100), use_container_width=True)


# =========================
# Tab 3
# =========================
with tab3:
    st.subheader("半自動下單意圖")

    intents = read_sql("""
    SELECT *
    FROM order_intents
    ORDER BY created_at DESC
    LIMIT 200
    """)

    if intents.empty:
        st.info("目前沒有下單意圖。你可以用 Telegram：/order SOLUSDT 50")
    else:
        show = intents.copy()
        show["created_time"] = pd.to_datetime(show["created_at"], unit="s")
        show["updated_time"] = pd.to_datetime(show["updated_at"], unit="s")
        st.dataframe(
            show[[
                "created_time",
                "symbol",
                "side",
                "notional_usdt",
                "status",
                "confirm_code",
                "dry_run",
                "reason",
                "result_json",
            ]],
            use_container_width=True,
        )


# =========================
# Tab 4
# =========================
with tab4:
    st.subheader("交易紀錄")

    trades = read_sql("""
    SELECT *
    FROM trades
    ORDER BY ts DESC
    LIMIT 200
    """)

    if trades.empty:
        st.info("目前沒有交易紀錄。")
    else:
        trades["time"] = pd.to_datetime(trades["ts"], unit="s")
        st.dataframe(trades, use_container_width=True)


# =========================
# Tab 5
# =========================
with tab5:
    st.subheader("系統資料")

    c1, c2, c3 = st.columns(3)

    total_rows = read_sql("SELECT COUNT(*) AS n FROM scan_results")
    pass_rows = read_sql("SELECT COUNT(*) AS n FROM scan_results WHERE status='PASS'")
    watch_rows = read_sql("SELECT COUNT(*) AS n FROM scan_results WHERE status='WATCH'")

    c1.metric("Scan Rows", int(total_rows.iloc[0]["n"]) if not total_rows.empty else 0)
    c2.metric("PASS Rows", int(pass_rows.iloc[0]["n"]) if not pass_rows.empty else 0)
    c3.metric("WATCH Rows", int(watch_rows.iloc[0]["n"]) if not watch_rows.empty else 0)

    st.divider()

    st.subheader("Settings")
    settings = read_sql("SELECT * FROM settings ORDER BY key")
    st.dataframe(settings, use_container_width=True)

    st.subheader("Blacklist")
    st.dataframe(black_df, use_container_width=True)

    st.caption(f"DB_PATH: {DB_PATH}")
