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

APP_TITLE = "📡 Funding Radar｜資金費率雷達"

# 你的目標年化，預設 16%
TARGET_APY = float(os.getenv("TARGET_APY", "0.16"))


# =========================
# 黑色主題
# =========================

px.defaults.template = "plotly_dark"

st.markdown("""
<style>
    .stApp {
        background: linear-gradient(180deg, #020617 0%, #05070d 45%, #020617 100%);
        color: #e5e7eb;
    }

    [data-testid="stHeader"] {
        background-color: rgba(2, 6, 23, 0.85);
        backdrop-filter: blur(8px);
    }

    [data-testid="stToolbar"] {
        right: 2rem;
    }

    h1, h2, h3, h4, h5, h6 {
        color: #f9fafb;
        font-weight: 800;
    }

    p, span, div, label {
        color: #e5e7eb;
    }

    hr {
        border-color: #1f2937;
    }

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

    [data-testid="stDataFrame"] {
        background-color: #0f172a;
        border-radius: 14px;
        border: 1px solid #1f2937;
        overflow: hidden;
    }

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

    .stAlert {
        background-color: #111827;
        border-radius: 14px;
        border: 1px solid #1f2937;
    }

    [data-testid="stCaptionContainer"] {
        color: #9ca3af;
    }

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

NUMERIC_COLS = [
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
    "target_apy",
    "apy_gap",
]


DISPLAY_NAME_MAP = {
    "time": "時間",
    "ts": "時間戳",
    "id": "ID",
    "symbol": "交易對",
    "status": "狀態",
    "signal_level": "訊號等級",

    "current_funding_rate": "當前資金費率",
    "avg_funding_rate_7d": "7日平均資金費率",
    "std_funding_rate_7d": "7日資金費率波動",
    "positive_ratio_7d": "7日正費率比例",
    "recent_avg_funding_rate": "近期平均資金費率",

    "apy": "年化收益率",
    "target_apy": "目標年化",
    "apy_gap": "距離目標",
    "target_status": "16% 目標狀態",

    "payback_days": "回本天數",
    "basis_rate": "現貨合約價差",
    "spot_slippage": "現貨滑點",
    "futures_slippage": "合約滑點",
    "total_slippage": "總滑點",

    "quote_volume": "24H 成交額",
    "open_interest_notional": "合約未平倉名目價值",
    "mark_price": "標記價格",
    "fail_reason": "未通過原因",
}


PERCENT_COLS = {
    "current_funding_rate",
    "avg_funding_rate_7d",
    "std_funding_rate_7d",
    "positive_ratio_7d",
    "recent_avg_funding_rate",
    "apy",
    "target_apy",
    "apy_gap",
    "basis_rate",
    "spot_slippage",
    "futures_slippage",
    "total_slippage",
    "total_slippage_rate",
}


MONEY_COLS = {
    "quote_volume",
    "open_interest_notional",
}


def safe_to_numeric(df, cols):
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def add_time_column(df):
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


def add_target_columns(df):
    if df.empty:
        return df

    df = df.copy()

    if "apy" in df.columns:
        df["target_apy"] = TARGET_APY
        df["apy_gap"] = pd.to_numeric(df["apy"], errors="coerce") - TARGET_APY

        def classify_target(x):
            if pd.isna(x):
                return "資料不足"
            if x >= TARGET_APY:
                return "🔥 已達 16% 目標"
            if x >= TARGET_APY * 0.75:
                return "🟡 接近目標"
            return "⚪ 未達目標"

        df["target_status"] = pd.to_numeric(df["apy"], errors="coerce").apply(classify_target)

    return df


def existing_cols(df, cols):
    return [col for col in cols if col in df.columns]


def translate_status(x):
    text = str(x).upper()

    if text == "PASS":
        return "通過"
    if text == "WATCH":
        return "觀察"
    if text == "FAIL":
        return "未通過"

    return x


def prepare_display_df(df, cols=None):
    if df is None or df.empty:
        return df

    if cols:
        view = df[cols].copy()
    else:
        view = df.copy()

    for col in view.columns:
        if col in PERCENT_COLS:
            view[col] = pd.to_numeric(view[col], errors="coerce")
            view[col] = view[col].apply(lambda x: f"{x * 100:.4f}%" if pd.notna(x) else "N/A")

        elif col in MONEY_COLS:
            view[col] = pd.to_numeric(view[col], errors="coerce")
            view[col] = view[col].apply(lambda x: f"{x:,.0f}" if pd.notna(x) else "N/A")

        elif col == "payback_days":
            view[col] = pd.to_numeric(view[col], errors="coerce")
            view[col] = view[col].apply(lambda x: f"{x:.2f} 天" if pd.notna(x) else "N/A")

        elif col == "mark_price":
            view[col] = pd.to_numeric(view[col], errors="coerce")
            view[col] = view[col].apply(lambda x: f"{x:,.8f}" if pd.notna(x) else "N/A")

        elif col == "status":
            view[col] = view[col].apply(translate_status)

        elif col == "time":
            view[col] = pd.to_datetime(view[col], errors="coerce")
            view[col] = view[col].apply(lambda x: str(x)[:19] if pd.notna(x) else "N/A")

    view = view.rename(columns=DISPLAY_NAME_MAP)
    return view


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


def fmt_pct_value(x, digits=2):
    try:
        if pd.isna(x):
            return "N/A"
        return f"{float(x) * 100:.{digits}f}%"
    except Exception:
        return "N/A"


def latest_per_symbol(df):
    if df.empty or "symbol" not in df.columns:
        return pd.DataFrame()

    out = df.copy()

    if "ts" in out.columns:
        out["ts"] = pd.to_numeric(out["ts"], errors="coerce")
        sort_cols = ["symbol", "ts"]
        ascending = [True, False]

        if "id" in out.columns:
            out["id"] = pd.to_numeric(out["id"], errors="coerce")
            sort_cols.append("id")
            ascending.append(False)

        out = out.sort_values(sort_cols, ascending=ascending)
    elif "time" in out.columns:
        out = out.sort_values(["symbol", "time"], ascending=[True, False])

    out = out.drop_duplicates(subset=["symbol"], keep="first")
    return out


def plot_layout(fig):
    fig.update_layout(
        paper_bgcolor="#020617",
        plot_bgcolor="#020617",
        font_color="#e5e7eb",
        legend_title_text="",
    )
    return fig


# =========================
# 登入檢查
# =========================

if not check_password():
    st.stop()


# =========================
# 主畫面
# =========================

st.title(APP_TITLE)
st.caption(f"目前資料庫：`{DB_PATH}`")
st.caption(f"目標年化：`{TARGET_APY * 100:.2f}%`")


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
# 讀取資料
# =========================

if "ts" in columns:
    all_df = read_sql("""
        SELECT *
        FROM scan_results
        ORDER BY ts DESC, id DESC
        LIMIT 5000
    """)
else:
    all_df = read_sql("""
        SELECT *
        FROM scan_results
        LIMIT 5000
    """)

all_df = add_time_column(all_df)
all_df = safe_to_numeric(all_df, NUMERIC_COLS)
all_df = add_target_columns(all_df)

latest_df = all_df.head(1000).copy()
symbol_latest_df = latest_per_symbol(all_df)
symbol_latest_df = add_target_columns(symbol_latest_df)


# =========================
# 頂部指標
# =========================

total_count = len(latest_df)
symbol_count = symbol_latest_df["symbol"].nunique() if not symbol_latest_df.empty and "symbol" in symbol_latest_df.columns else 0
pass_count = status_count(symbol_latest_df, "PASS")
watch_count = status_count(symbol_latest_df, "WATCH")
fail_count = status_count(symbol_latest_df, "FAIL")

target_count = 0
near_target_count = 0

if not symbol_latest_df.empty and "apy" in symbol_latest_df.columns:
    target_count = int((pd.to_numeric(symbol_latest_df["apy"], errors="coerce") >= TARGET_APY).sum())
    near_target_count = int((
        (pd.to_numeric(symbol_latest_df["apy"], errors="coerce") >= TARGET_APY * 0.75)
        & (pd.to_numeric(symbol_latest_df["apy"], errors="coerce") < TARGET_APY)
    ).sum())

m1, m2, m3, m4, m5 = st.columns(5)

with m1:
    st.metric("最新資料筆數", total_count)

with m2:
    st.metric("交易對數量", symbol_count)

with m3:
    st.metric("通過 PASS", pass_count)

with m4:
    st.metric("達 16% 目標", target_count)

with m5:
    st.metric("最後更新", fmt_latest_time(latest_df))


st.divider()


# =========================
# 分頁
# =========================

tab_target, tab_symbol_latest, tab_watch, tab_latest, tab_history, tab_fail, tab_db = st.tabs([
    "🔥 16% 目標",
    "每個交易對最新狀態",
    "高風險觀察 WATCH",
    "最新掃描結果",
    "歷史走勢",
    "未通過原因統計",
    "資料庫狀態",
])


# =========================
# 16% 目標分頁
# =========================

with tab_target:
    st.subheader("🔥 16% 年化目標")

    st.info(
        f"本頁會用 `年化收益率 APY` 與目標 `{TARGET_APY * 100:.2f}%` 比較。"
        "注意：APY 是根據資金費率推算的毛年化，不代表保證收益。"
    )

    if symbol_latest_df.empty:
        st.info("目前沒有資料。")
    else:
        target_view = symbol_latest_df.copy()

        if "apy" in target_view.columns:
            target_view["apy"] = pd.to_numeric(target_view["apy"], errors="coerce")
            target_view = target_view.sort_values(
                ["apy", "payback_days"],
                ascending=[False, True],
            )

        reached_df = target_view[target_view["apy"] >= TARGET_APY].copy() if "apy" in target_view.columns else pd.DataFrame()
        near_df = target_view[
            (target_view["apy"] >= TARGET_APY * 0.75) & (target_view["apy"] < TARGET_APY)
        ].copy() if "apy" in target_view.columns else pd.DataFrame()

        c1, c2, c3 = st.columns(3)

        with c1:
            st.metric("已達目標", len(reached_df))

        with c2:
            st.metric("接近目標", len(near_df))

        with c3:
            best_apy = target_view["apy"].max() if "apy" in target_view.columns and not target_view["apy"].dropna().empty else None
            st.metric("目前最高年化", fmt_pct_value(best_apy, 2))

        st.divider()

        display_cols = existing_cols(target_view, [
            "time",
            "symbol",
            "status",
            "signal_level",
            "target_status",
            "apy",
            "target_apy",
            "apy_gap",
            "current_funding_rate",
            "avg_funding_rate_7d",
            "payback_days",
            "basis_rate",
            "total_slippage",
            "quote_volume",
            "open_interest_notional",
            "fail_reason",
        ])

        if reached_df.empty:
            st.warning("目前沒有達到 16% 目標的交易對。")
        else:
            st.success("以下交易對已達 16% 目標：")
            st.dataframe(
                prepare_display_df(reached_df, display_cols),
                use_container_width=True,
            )

        st.subheader("接近 16% 目標的交易對")
        if near_df.empty:
            st.info("目前沒有接近 16% 目標的交易對。")
        else:
            st.dataframe(
                prepare_display_df(near_df, display_cols),
                use_container_width=True,
            )

        st.subheader("依年化收益率排序")
        st.dataframe(
            prepare_display_df(target_view.head(100), display_cols),
            use_container_width=True,
        )

        if "apy" in target_view.columns and "symbol" in target_view.columns:
            chart_df = target_view.dropna(subset=["apy"]).head(30).copy()

            if not chart_df.empty:
                fig = px.bar(
                    chart_df,
                    x="symbol",
                    y="apy",
                    color="target_status" if "target_status" in chart_df.columns else None,
                    title="各交易對年化收益率 APY 排名",
                )
                fig.add_hline(
                    y=TARGET_APY,
                    line_dash="dash",
                    line_color="#f97316",
                    annotation_text=f"目標 {TARGET_APY * 100:.2f}%",
                )
                fig = plot_layout(fig)
                st.plotly_chart(fig, use_container_width=True)


# =========================
# 每個交易對最新狀態
# =========================

with tab_symbol_latest:
    st.subheader("每個交易對最新狀態")

    if symbol_latest_df.empty:
        st.info("目前沒有資料。")
    else:
        view = symbol_latest_df.copy()

        c_filter1, c_filter2, c_filter3 = st.columns(3)

        with c_filter1:
            if "status" in view.columns:
                statuses = sorted(view["status"].dropna().astype(str).unique().tolist())
                selected_statuses = st.multiselect(
                    "篩選狀態",
                    options=statuses,
                    default=[],
                    key="symbol_latest_status_filter",
                )

                if selected_statuses:
                    view = view[view["status"].astype(str).isin(selected_statuses)].copy()

        with c_filter2:
            if "target_status" in view.columns:
                target_statuses = sorted(view["target_status"].dropna().astype(str).unique().tolist())
                selected_target_statuses = st.multiselect(
                    "篩選 16% 目標狀態",
                    options=target_statuses,
                    default=[],
                    key="target_status_filter",
                )

                if selected_target_statuses:
                    view = view[view["target_status"].astype(str).isin(selected_target_statuses)].copy()

        with c_filter3:
            min_apy = st.number_input(
                "最低年化收益率 %",
                min_value=-100.0,
                max_value=1000.0,
                value=0.0,
                step=1.0,
            )

            if "apy" in view.columns:
                view = view[pd.to_numeric(view["apy"], errors="coerce") >= min_apy / 100].copy()

        if "apy" in view.columns:
            view = view.sort_values(["apy", "payback_days"], ascending=[False, True])

        display_cols = existing_cols(view, [
            "time",
            "symbol",
            "status",
            "signal_level",
            "target_status",
            "current_funding_rate",
            "avg_funding_rate_7d",
            "apy",
            "apy_gap",
            "payback_days",
            "basis_rate",
            "total_slippage",
            "quote_volume",
            "open_interest_notional",
            "mark_price",
            "fail_reason",
        ])

        st.dataframe(
            prepare_display_df(view, display_cols),
            use_container_width=True,
        )


# =========================
# WATCH 分頁
# =========================

with tab_watch:
    st.subheader("高風險觀察 WATCH")

    if symbol_latest_df.empty:
        st.info("目前沒有資料。")
    else:
        watch_df = pd.DataFrame()

        if "status" in symbol_latest_df.columns:
            watch_df = symbol_latest_df[
                symbol_latest_df["status"].astype(str).str.upper() == "WATCH"
            ].copy()

        if watch_df.empty:
            st.info("目前沒有 WATCH 訊號。")
        else:
            display_cols = existing_cols(watch_df, [
                "time",
                "symbol",
                "status",
                "signal_level",
                "target_status",
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

            st.dataframe(
                prepare_display_df(watch_df, display_cols),
                use_container_width=True,
            )


# =========================
# 最新掃描結果
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
                    "篩選交易對",
                    options=symbols,
                    default=[],
                    key="latest_symbol_filter",
                )

                if selected_symbols:
                    latest_view = latest_view[
                        latest_view["symbol"].astype(str).isin(selected_symbols)
                    ].copy()

        with c_filter2:
            if "status" in latest_view.columns:
                statuses = sorted(latest_view["status"].dropna().astype(str).unique().tolist())
                selected_statuses = st.multiselect(
                    "篩選狀態",
                    options=statuses,
                    default=[],
                    key="latest_status_filter",
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
            "target_status",
            "current_funding_rate",
            "avg_funding_rate_7d",
            "std_funding_rate_7d",
            "positive_ratio_7d",
            "recent_avg_funding_rate",
            "apy",
            "target_apy",
            "apy_gap",
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

        st.dataframe(
            prepare_display_df(latest_view, display_cols),
            use_container_width=True,
        )


# =========================
# 歷史走勢
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
                "選擇交易對",
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
                hist = safe_to_numeric(hist, NUMERIC_COLS)
                hist = add_target_columns(hist)

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
                            title=f"{symbol}｜資金費率走勢",
                        )
                        fig.add_hline(
                            y=TARGET_APY / 365 / 3,
                            line_dash="dash",
                            line_color="#f97316",
                            annotation_text="16% 年化所需單期平均費率",
                        )
                        fig = plot_layout(fig)
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.info("目前沒有資金費率資料可繪圖。")

                with c2:
                    if "apy" in hist.columns and "time" in hist.columns:
                        fig = px.line(
                            hist,
                            x="time",
                            y="apy",
                            title=f"{symbol}｜年化收益率 APY",
                        )
                        fig.add_hline(
                            y=TARGET_APY,
                            line_dash="dash",
                            line_color="#f97316",
                            annotation_text=f"目標 {TARGET_APY * 100:.2f}%",
                        )
                        fig = plot_layout(fig)
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.info("目前沒有 APY 資料可繪圖。")

                c3, c4 = st.columns(2)

                with c3:
                    if "payback_days" in hist.columns and "time" in hist.columns:
                        fig = px.line(
                            hist,
                            x="time",
                            y="payback_days",
                            title=f"{symbol}｜回本天數",
                        )
                        fig = plot_layout(fig)
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.info("目前沒有回本天數資料可繪圖。")

                with c4:
                    if "basis_rate" in hist.columns and "time" in hist.columns:
                        fig = px.line(
                            hist,
                            x="time",
                            y="basis_rate",
                            title=f"{symbol}｜現貨合約價差 Basis",
                        )
                        fig = plot_layout(fig)
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.info("目前沒有 Basis 資料可繪圖。")

                c5, c6 = st.columns(2)

                with c5:
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
                            title=f"{symbol}｜總滑點",
                        )
                        fig = plot_layout(fig)
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.info("目前沒有總滑點資料可繪圖。")

                with c6:
                    if "apy_gap" in hist.columns and "time" in hist.columns:
                        fig = px.line(
                            hist,
                            x="time",
                            y="apy_gap",
                            title=f"{symbol}｜距離 16% 目標差距",
                        )
                        fig.add_hline(
                            y=0,
                            line_dash="dash",
                            line_color="#22c55e",
                            annotation_text="達標線",
                        )
                        fig = plot_layout(fig)
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.info("目前沒有目標差距資料可繪圖。")

                st.subheader("最近 100 筆歷史資料")

                display_cols = existing_cols(hist, [
                    "time",
                    "symbol",
                    "status",
                    "signal_level",
                    "target_status",
                    "current_funding_rate",
                    "avg_funding_rate_7d",
                    "std_funding_rate_7d",
                    "positive_ratio_7d",
                    "recent_avg_funding_rate",
                    "apy",
                    "target_apy",
                    "apy_gap",
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

                st.dataframe(
                    prepare_display_df(hist.tail(100), display_cols),
                    use_container_width=True,
                )


# =========================
# 未通過原因統計
# =========================

with tab_fail:
    st.subheader("未通過原因統計")

    if all_df.empty:
        st.info("目前沒有資料。")
    elif "fail_reason" not in all_df.columns:
        st.warning("資料表裡沒有 fail_reason 欄位。")
    else:
        fail_df = all_df.copy()

        if "status" in fail_df.columns:
            fail_df = fail_df[fail_df["status"].astype(str).str.upper() == "FAIL"].copy()

        fail_df = fail_df[fail_df["fail_reason"].notna()].copy()

        if fail_df.empty:
            st.info("目前沒有未通過原因資料。")
        else:
            reason_df = (
                fail_df["fail_reason"]
                .astype(str)
                .value_counts()
                .reset_index()
            )
            reason_df.columns = ["未通過原因", "次數"]

            c1, c2 = st.columns(2)

            with c1:
                st.dataframe(reason_df.head(50), use_container_width=True)

            with c2:
                fig = px.bar(
                    reason_df.head(20),
                    x="次數",
                    y="未通過原因",
                    orientation="h",
                    title="未通過原因排行",
                )
                fig = plot_layout(fig)
                st.plotly_chart(fig, use_container_width=True)

            st.subheader("最近未通過資料")

            display_cols = existing_cols(fail_df, [
                "time",
                "symbol",
                "status",
                "current_funding_rate",
                "avg_funding_rate_7d",
                "apy",
                "payback_days",
                "basis_rate",
                "total_slippage",
                "quote_volume",
                "open_interest_notional",
                "fail_reason",
            ])

            st.dataframe(
                prepare_display_df(fail_df.head(200), display_cols),
                use_container_width=True,
            )


# =========================
# 資料庫狀態
# =========================

with tab_db:
    st.subheader("資料庫狀態")

    st.write("目前使用資料庫：")
    st.code(DB_PATH)

    st.write("目標年化設定：")
    st.code(f"TARGET_APY={TARGET_APY}")

    st.write("scan_results 欄位：")
    columns_df = pd.DataFrame({
        "原始欄位": columns,
        "中文名稱": [DISPLAY_NAME_MAP.get(c, "") for c in columns],
    })
    st.dataframe(columns_df, use_container_width=True)

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

        if not status_df.empty:
            status_df["status"] = status_df["status"].apply(translate_status)
            status_df = status_df.rename(columns={"status": "狀態", "count": "筆數"})

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

        symbol_df = symbol_df.rename(columns={
            "symbol": "交易對",
            "count": "筆數",
        })

        st.write("資料最多的交易對：")
        st.dataframe(symbol_df, use_container_width=True)

    st.write("資料表清單：")
    tables_df = read_sql("""
        SELECT name
        FROM sqlite_master
        WHERE type='table'
        ORDER BY name
    """)

    tables_df = tables_df.rename(columns={"name": "資料表名稱"})
    st.dataframe(tables_df, use_container_width=True)
