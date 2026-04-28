import os
import sqlite3
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st


# =========================
# 基本設定
# =========================

st.set_page_config(
    page_title="Funding Radar",
    page_icon="📡",
    layout="wide",
)


APP_TITLE = "📡 Funding Radar"


# =========================
# 黑色主題
# =========================

px.defaults.template = "plotly_dark"

st.markdown("""
<style>
    /* 整體背景 */
    .stApp {
        background: linear-gradient(180deg, #020617 0%, #05070d 45%, #020617 100%);
        color: #e5e7eb;
    }

    /* 頂部 header */
    [data-testid="stHeader"] {
        background-color: rgba(2, 6, 23, 0.85);
        backdrop-filter: blur(8px);
    }

    [data-testid="stToolbar"] {
        right: 2rem;
    }

    /* 文字 */
    h1, h2, h3, h4, h5, h6 {
        color: #f9fafb;
        font-weight: 700;
    }

    p, span, div, label {
        color: #e5e7eb;
    }

    /* 分隔線 */
    hr {
        border-color: #1f2937;
    }

    /* Tabs */
    .stTabs [data-baseweb="tab-list"] {
        gap: 10px;
        background-color: transparent;
    }

    .stTabs [data-baseweb="tab"] {
        background-color: #111827;
        border: 1px solid #1f2937;
        border-radius: 12px;
        padding: 10px 18px;
        color: #d1d5db;
    }

    .stTabs [aria-selected="true"] {
        background: linear-gradient(135deg, #2563eb, #0ea5e9);
        color: white;
        border: 1px solid #38bdf8;
    }

    /* Metric 卡片 */
    [data-testid="stMetric"] {
        background: linear-gradient(135deg, #0f172a, #111827);
        border: 1px solid #1f2937;
        padding: 18px;
        border-radius: 16px;
        box-shadow: 0 8px 20px rgba(0, 0, 0, 0.28);
    }

    [data-testid="stMetricLabel"] {
        color: #9ca3af;
    }

    [data-testid="stMetricValue"] {
        color: #38bdf8;
        font-weight: 800;
    }

    /* DataFrame */
    [data-testid="stDataFrame"] {
        background-color: #0f172a;
        border-radius: 14px;
        border: 1px solid #1f2937;
        overflow: hidden;
    }

    /* 按鈕 */
    .stButton > button {
        background: linear-gradient(135deg, #2563eb, #0ea5e9);
        color: white;
        border-radius: 12px;
        border: 0px;
        padding: 8px 18px;
        font-weight: 700;
    }

    .stButton > button:hover {
        background: linear-gradient(135deg, #1d4ed8, #0284c7);
        color: white;
        border: 0px;
    }

    /* 輸入框 */
    input {
        background-color: #111827 !important;
        color: #e5e7eb !important;
        border: 1px solid #374151 !important;
        border-radius: 10px !important;
    }

    textarea {
        background-color: #111827 !important;
        color: #e5e7eb !important;
        border: 1px solid #374151 !important;
    }

    /* Selectbox / Multiselect */
    .stSelectbox div {
        color: #e5e7eb;
    }

    .stMultiSelect div {
        color: #e5e7eb;
    }

    [data-baseweb="select"] {
        background-color: #111827;
        border-radius: 10px;
    }

    /* Code */
    code {
        color: #93c5fd;
        background-color: #111827;
        border-radius: 6px;
        padding: 2px 5px;
    }

    pre {
        background-color: #111827 !important;
        color: #e5e7eb !important;
        border: 1px solid #1f2937;
        border-radius: 12px;
    }

    /* Alert */
    .stAlert {
        background-color: #111827;
        border-radius: 14px;
        border: 1px solid #1f2937;
    }

    /* Caption */
    [data-testid="stCaptionContainer"] {
        color: #9ca3af;
    }

    /* Sidebar 若未來有用到 */
    [data-testid="stSidebar"] {
        background-color: #020617;
        border-right: 1px solid #1f2937;
    }
</style>
""", unsafe_allow_html=True)


# =========================
# 資料庫路徑
# =========================

def get_db_path():
    """
    依序嘗試常見資料庫路徑。
    如果 Zeabur Variables 有設定 DB_PATH，會優先使用。
    """

    candidates = []

    env_db_path = os.getenv("DB_PATH")
    if env_db_path:
        candidates.append(env_db_path)

    candidates.extend([
        "/app/data/radar.db",
        "/data/radar.db",
        "/data/funding_radar.db",
        "/app/funding_radar.db",
        "funding_radar.db",
        "radar.db",
        "data/radar.db",
        "data/funding_radar.db",
        "./radar.db",
        "./funding_radar.db",
    ])

    for path in candidates:
        if path and Path(path).exists():
            return path

    return env_db_path or "/app/data/radar.db"


DB_PATH = get_db_path()


# =========================
# 密碼登入
# =========================

def check_password():
    """
    使用 Zeabur Variables 裡面的 UI_PASSWORD。
    如果沒有設定 UI_PASSWORD，會顯示警告，但仍允許進入。
    """

    ui_password = os.getenv("UI_PASSWORD", "")

    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False

    if st.session_state["authenticated"]:
        return True

    st.title(APP_TITLE)
    st.subheader("登入")

    if not ui_password:
        st.warning("目前沒有設定 UI_PASSWORD。建議到 Zeabur Variables 設定 UI_PASSWORD。")
        st.session_state["authenticated"] = True
        return True

    password = st.text_input("請輸入 UI 密碼", type="password")

    if st.button("登入"):
        if password == ui_password:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("密碼錯誤")

    return False


# =========================
# SQLite 工具
# =========================

def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def table_exists(table_name):
    try:
        with get_conn() as conn:
            df = pd.read_sql_query(
                """
                SELECT name
                FROM sqlite_master
                WHERE type='table' AND name=?
                """,
                conn,
                params=[table_name],
            )
        return not df.empty
    except Exception:
        return False


def read_sql(query, params=None):
    if params is None:
        params = []

    try:
        with get_conn() as conn:
            return pd.read_sql_query(query, conn, params=params)
    except Exception as e:
        st.error(f"讀取資料庫失敗：{e}")
        return pd.DataFrame()


def get_table_columns(table_name):
    try:
        with get_conn() as conn:
            df = pd.read_sql_query(f"PRAGMA table_info({table_name})", conn)
        if df.empty or "name" not in df.columns:
            return []
        return df["name"].tolist()
    except Exception:
        return []


# =========================
# 資料處理工具
# =========================

def safe_to_numeric(df, cols):
    """
    將指定欄位轉成數字。
    避免 Plotly 因為 object/string 型別報錯。
    """
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def add_time_column(df):
    """
    依照常見時間欄位建立 time 欄位。
    """
    if df.empty:
        return df

    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], errors="coerce")
        return df

    if "ts" in df.columns:
        df["time"] = pd.to_datetime(df["ts"], unit="s", errors="coerce")
        return df

    if "timestamp" in df.columns:
        timestamp_numeric = pd.to_numeric(df["timestamp"], errors="coerce")
        if timestamp_numeric.dropna().empty:
            df["time"] = pd.to_datetime(df["timestamp"], errors="coerce")
        else:
            median_value = timestamp_numeric.dropna().median()
            if median_value > 10_000_000_000:
                df["time"] = pd.to_datetime(timestamp_numeric, unit="ms", errors="coerce")
            else:
                df["time"] = pd.to_datetime(timestamp_numeric, unit="s", errors="coerce")
        return df

    if "created_at" in df.columns:
        df["time"] = pd.to_datetime(df["created_at"], errors="coerce")
        return df

    if "updated_at" in df.columns:
        df["time"] = pd.to_datetime(df["updated_at"], errors="coerce")
        return df

    df["time"] = pd.NaT
    return df


def existing_cols(df, cols):
    return [col for col in cols if col in df.columns]


def status_count(df, target_status):
    if df.empty or "status" not in df.columns:
        return 0
    return int((df["status"].astype(str).str.upper() == target_status.upper()).sum())


def fmt_latest_time(df):
    if df.empty or "time" not in df.columns:
        return "-"

    latest_time = df["time"].dropna()
    if latest_time.empty:
        return "-"

    return str(latest_time.max())[:19]


# =========================
# 登入檢查
# =========================

if not check_password():
    st.stop()


# =========================
# 主畫面
# =========================

st.title(APP_TITLE)
st.caption(f"Database path: `{DB_PATH}`")


# =========================
# 資料庫狀態檢查
# =========================

if not Path(DB_PATH).exists():
    st.error("找不到資料庫檔案。")
    st.info("如果程式剛部署完成，請等掃描器先跑一輪，或確認 Zeabur Volume / DB_PATH 設定。")
    st.stop()

if not table_exists("scan_results"):
    st.error("資料庫裡找不到 `scan_results` 資料表。")
    st.info("請確認 scanner 是否已經成功寫入資料。")
    st.stop()


columns = get_table_columns("scan_results")

if not columns:
    st.error("無法讀取 `scan_results` 欄位。")
    st.stop()


# =========================
# 共用數字欄位
# =========================

numeric_cols = [
    "current_funding_rate",
    "avg_funding_rate_7d",
    "std_funding_rate_7d",
    "positive_ratio_7d",
    "recent_avg_funding_rate",
    "apy",
    "basis_rate",
    "spot_slippage",
    "futures_slippage",
    "total_slippage",
    "total_slippage_rate",
    "payback_days",
    "score",
    "open_interest_notional",
    "quote_volume",
    "quote_volume_24h",
    "volume_24h",
    "mark_price",
    "index_price",
]


# =========================
# 讀取最新資料
# =========================

if "ts" in columns:
    latest_df = read_sql("""
        SELECT *
        FROM scan_results
        ORDER BY ts DESC, id DESC
        LIMIT 1000
    """)
else:
    latest_df = read_sql("""
        SELECT *
        FROM scan_results
        LIMIT 1000
    """)

latest_df = add_time_column(latest_df)
latest_df = safe_to_numeric(latest_df, numeric_cols)


# =========================
# 頂部指標
# =========================

total_count = len(latest_df)
pass_count = status_count(latest_df, "PASS")
watch_count = status_count(latest_df, "WATCH")
fail_count = status_count(latest_df, "FAIL")

m1, m2, m3, m4 = st.columns(4)

with m1:
    st.metric("最新資料筆數", total_count)

with m2:
    st.metric("PASS", pass_count)

with m3:
    st.metric("WATCH", watch_count)

with m4:
    st.metric("最後更新", fmt_latest_time(latest_df))


st.divider()


# =========================
# 分頁
# =========================

tab_watch, tab_latest, tab_history, tab_db = st.tabs([
    "高風險觀察 WATCH",
    "最新掃描結果",
    "歷史走勢",
    "資料庫狀態",
])


# =========================
# WATCH 分頁
# =========================

with tab_watch:
    st.subheader("高風險觀察 WATCH")

    if latest_df.empty:
        st.info("目前沒有資料。")
    else:
        watch_df = pd.DataFrame()

        if "status" in latest_df.columns:
            watch_df = latest_df[
                latest_df["status"].astype(str).str.upper() == "WATCH"
            ].copy()

        if watch_df.empty:
            st.info("目前沒有 WATCH 訊號。")
        else:
            display_cols = existing_cols(watch_df, [
                "time",
                "symbol",
                "status",
                "signal_level",
                "current_funding_rate",
                "avg_funding_rate_7d",
                "apy",
                "payback_days",
                "basis_rate",
                "total_slippage",
                "quote_volume",
                "open_interest_notional",
                "mark_price",
                "fail_reason",
            ])

            if display_cols:
                st.dataframe(
                    watch_df[display_cols].head(200),
                    use_container_width=True,
                )
            else:
                st.dataframe(
                    watch_df.head(200),
                    use_container_width=True,
                )


# =========================
# 最新掃描結果分頁
# =========================

with tab_latest:
    st.subheader("最新掃描結果")

    if latest_df.empty:
        st.info("目前沒有掃描資料。")
    else:
        latest_view = latest_df.copy()

        c_filter1, c_filter2 = st.columns(2)

        with c_filter1:
            if "symbol" in latest_view.columns:
                symbols = sorted(latest_view["symbol"].dropna().astype(str).unique().tolist())
                selected_symbols = st.multiselect(
                    "篩選 Symbol",
                    options=symbols,
                    default=[],
                )

                if selected_symbols:
                    latest_view = latest_view[
                        latest_view["symbol"].astype(str).isin(selected_symbols)
                    ].copy()

        with c_filter2:
            if "status" in latest_view.columns:
                statuses = sorted(latest_view["status"].dropna().astype(str).unique().tolist())
                selected_statuses = st.multiselect(
                    "篩選 Status",
                    options=statuses,
                    default=[],
                )

                if selected_statuses:
                    latest_view = latest_view[
                        latest_view["status"].astype(str).isin(selected_statuses)
                    ].copy()

        display_cols = existing_cols(latest_view, [
            "time",
            "symbol",
            "status",
            "signal_level",
            "current_funding_rate",
            "avg_funding_rate_7d",
            "std_funding_rate_7d",
            "positive_ratio_7d",
            "recent_avg_funding_rate",
            "apy",
            "payback_days",
            "basis_rate",
            "spot_slippage",
            "futures_slippage",
            "total_slippage",
            "quote_volume",
            "open_interest_notional",
            "mark_price",
            "fail_reason",
        ])

        if display_cols:
            st.dataframe(
                latest_view[display_cols],
                use_container_width=True,
            )
        else:
            st.dataframe(
                latest_view,
                use_container_width=True,
            )


# =========================
# 歷史走勢分頁
# =========================

with tab_history:
    st.subheader("歷史走勢")

    if "symbol" not in columns:
        st.warning("scan_results 裡沒有 `symbol` 欄位，無法顯示歷史走勢。")
    else:
        symbols_df = read_sql("""
            SELECT DISTINCT symbol
            FROM scan_results
            WHERE symbol IS NOT NULL
            ORDER BY symbol ASC
        """)

        if symbols_df.empty:
            st.info("尚無歷史資料。")
        else:
            symbol = st.selectbox(
                "選擇 Symbol",
                symbols_df["symbol"].astype(str).tolist(),
            )

            if "ts" in columns:
                hist = read_sql("""
                    SELECT *
                    FROM scan_results
                    WHERE symbol=?
                    ORDER BY ts ASC, id ASC
                """, [symbol])
            else:
                hist = read_sql("""
                    SELECT *
                    FROM scan_results
                    WHERE symbol=?
                """, [symbol])

            if hist.empty:
                st.info("沒有資料")
            else:
                hist = add_time_column(hist)

                # 重要：修正 Plotly 型別錯誤
                hist = safe_to_numeric(hist, numeric_cols)

                c1, c2 = st.columns(2)

                with c1:
                    y_cols = existing_cols(hist, [
                        "current_funding_rate",
                        "avg_funding_rate_7d",
                    ])

                    if y_cols and "time" in hist.columns:
                        fig = px.line(
                            hist,
                            x="time",
                            y=y_cols,
                            title=f"{symbol} Funding Rate",
                        )
                        fig.update_layout(
                            paper_bgcolor="#020617",
                            plot_bgcolor="#020617",
                            font_color="#e5e7eb",
                        )
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.info("目前沒有 Funding Rate 資料可繪圖。")

                with c2:
                    if "payback_days" in hist.columns and "time" in hist.columns:
                        fig = px.line(
                            hist,
                            x="time",
                            y="payback_days",
                            title=f"{symbol} Payback Days",
                        )
                        fig.update_layout(
                            paper_bgcolor="#020617",
                            plot_bgcolor="#020617",
                            font_color="#e5e7eb",
                        )
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.info("目前沒有 Payback Days 資料可繪圖。")

                c3, c4 = st.columns(2)

                with c3:
                    if "basis_rate" in hist.columns and "time" in hist.columns:
                        fig = px.line(
                            hist,
                            x="time",
                            y="basis_rate",
                            title=f"{symbol} Basis",
                        )
                        fig.update_layout(
                            paper_bgcolor="#020617",
                            plot_bgcolor="#020617",
                            font_color="#e5e7eb",
                        )
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.info("目前沒有 Basis 資料可繪圖。")

                with c4:
                    slippage_col = None

                    if "total_slippage" in hist.columns:
                        slippage_col = "total_slippage"
                    elif "total_slippage_rate" in hist.columns:
                        slippage_col = "total_slippage_rate"

                    if slippage_col and "time" in hist.columns:
                        fig = px.line(
                            hist,
                            x="time",
                            y=slippage_col,
                            title=f"{symbol} Total Slippage",
                        )
                        fig.update_layout(
                            paper_bgcolor="#020617",
                            plot_bgcolor="#020617",
                            font_color="#e5e7eb",
                        )
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.info("目前沒有 Total Slippage 資料可繪圖。")

                st.subheader("最近 100 筆歷史資料")

                display_cols = existing_cols(hist, [
                    "time",
                    "symbol",
                    "status",
                    "signal_level",
                    "current_funding_rate",
                    "avg_funding_rate_7d",
                    "std_funding_rate_7d",
                    "positive_ratio_7d",
                    "recent_avg_funding_rate",
                    "apy",
                    "payback_days",
                    "basis_rate",
                    "spot_slippage",
                    "futures_slippage",
                    "total_slippage",
                    "quote_volume",
                    "open_interest_notional",
                    "mark_price",
                    "fail_reason",
                ])

                if display_cols:
                    st.dataframe(
                        hist[display_cols].tail(100),
                        use_container_width=True,
                    )
                else:
                    st.dataframe(
                        hist.tail(100),
                        use_container_width=True,
                    )


# =========================
# 資料庫狀態分頁
# =========================

with tab_db:
    st.subheader("資料庫狀態")

    st.write("目前使用資料庫：")
    st.code(DB_PATH)

    st.write("scan_results 欄位：")
    st.dataframe(
        pd.DataFrame({"columns": columns}),
        use_container_width=True,
    )

    count_df = read_sql("SELECT COUNT(*) AS count FROM scan_results")
    if not count_df.empty and "count" in count_df.columns:
        st.metric("scan_results 總筆數", int(count_df["count"].iloc[0]))

    if "status" in columns:
        status_df = read_sql("""
            SELECT status, COUNT(*) AS count
            FROM scan_results
            GROUP BY status
            ORDER BY count DESC
        """)

        st.write("狀態統計：")
        st.dataframe(status_df, use_container_width=True)

    if "symbol" in columns:
        symbol_df = read_sql("""
            SELECT symbol, COUNT(*) AS count
            FROM scan_results
            GROUP BY symbol
            ORDER BY count DESC
            LIMIT 30
        """)

        st.write("資料最多的 Symbol：")
        st.dataframe(symbol_df, use_container_width=True)

    st.write("資料表清單：")
    tables_df = read_sql("""
        SELECT name
        FROM sqlite_master
        WHERE type='table'
        ORDER BY name
    """)
    st.dataframe(tables_df, use_container_width=True)
