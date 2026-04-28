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
# 資料庫路徑
# =========================

def get_db_path():
    """
    依序嘗試常見資料庫路徑。
    如果你有在 Zeabur 設定 DB_PATH，會優先使用。
    """
    candidates = []

    env_db_path = os.getenv("DB_PATH")
    if env_db_path:
        candidates.append(env_db_path)

    candidates.extend([
        "/data/funding_radar.db",
        "/app/funding_radar.db",
        "funding_radar.db",
        "data/funding_radar.db",
        "./funding_radar.db",
    ])

    for path in candidates:
        if path and Path(path).exists():
            return path

    # 如果都不存在，回傳第一個預設值
    return env_db_path or "/data/funding_radar.db"


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
        # 如果 timestamp 很大，可能是毫秒；否則當秒
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


def format_rate_columns_for_display(df):
    """
    只做顯示輔助，不改原始圖表數值。
    """
    return df


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
# 資料庫狀態
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
# 讀取最新資料
# =========================

order_col = "ts" if "ts" in columns else None

if order_col:
    latest_df = read_sql("""
        SELECT *
        FROM scan_results
        ORDER BY ts DESC
        LIMIT 1000
    """)
else:
    latest_df = read_sql("""
        SELECT *
        FROM scan_results
        LIMIT 1000
    """)

latest_df = add_time_column(latest_df)


numeric_cols = [
    "current_funding_rate",
    "avg_funding_rate_7d",
    "basis_rate",
    "total_slippage",
    "total_slippage_rate",
    "payback_days",
    "score",
    "open_interest_notional",
    "quote_volume_24h",
    "volume_24h",
    "mark_price",
    "index_price",
]

latest_df = safe_to_numeric(latest_df, numeric_cols)


# =========================
# 頂部指標
# =========================

total_count = len(latest_df)

watch_count = 0
pass_count = 0

if not latest_df.empty:
    if "signal" in latest_df.columns:
        watch_count = int((latest_df["signal"].astype(str).str.upper() == "WATCH").sum())
        pass_count = int((latest_df["signal"].astype(str).str.upper() == "PASS").sum())
    elif "status" in latest_df.columns:
        watch_count = int((latest_df["status"].astype(str).str.upper() == "WATCH").sum())
        pass_count = int((latest_df["status"].astype(str).str.upper() == "PASS").sum())


m1, m2, m3, m4 = st.columns(4)

with m1:
    st.metric("最新資料筆數", total_count)

with m2:
    st.metric("WATCH", watch_count)

with m3:
    st.metric("PASS", pass_count)

with m4:
    if not latest_df.empty and "time" in latest_df.columns:
        latest_time = latest_df["time"].dropna()
        if not latest_time.empty:
            st.metric("最後更新", str(latest_time.max())[:19])
        else:
            st.metric("最後更新", "-")
    else:
        st.metric("最後更新", "-")


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

        if "signal" in latest_df.columns:
            watch_df = latest_df[
                latest_df["signal"].astype(str).str.upper() == "WATCH"
            ].copy()
        elif "status" in latest_df.columns:
            watch_df = latest_df[
                latest_df["status"].astype(str).str.upper() == "WATCH"
            ].copy()

        if watch_df.empty:
            st.info("目前沒有 WATCH 訊號。")
        else:
            display_cols = existing_cols(watch_df, [
                "time",
                "symbol",
                "signal",
                "status",
                "score",
                "current_funding_rate",
                "avg_funding_rate_7d",
                "basis_rate",
                "total_slippage",
                "total_slippage_rate",
                "payback_days",
                "open_interest_notional",
                "quote_volume_24h",
                "volume_24h",
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
        filter_symbol = None

        if "symbol" in latest_df.columns:
            symbols = sorted(latest_df["symbol"].dropna().astype(str).unique().tolist())
            selected = st.multiselect(
                "篩選 Symbol",
                options=symbols,
                default=[],
            )

            if selected:
                latest_view = latest_df[latest_df["symbol"].astype(str).isin(selected)].copy()
            else:
                latest_view = latest_df.copy()
        else:
            latest_view = latest_df.copy()

        display_cols = existing_cols(latest_view, [
            "time",
            "symbol",
            "signal",
            "status",
            "score",
            "current_funding_rate",
            "avg_funding_rate_7d",
            "basis_rate",
            "total_slippage",
            "total_slippage_rate",
            "payback_days",
            "open_interest_notional",
            "quote_volume_24h",
            "volume_24h",
            "mark_price",
            "index_price",
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
        if "ts" in columns:
            symbols_df = read_sql("""
                SELECT DISTINCT symbol
                FROM scan_results
                WHERE symbol IS NOT NULL
                ORDER BY symbol ASC
            """)
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
            symbol = st.selectbox("選擇 Symbol", symbols_df["symbol"].astype(str).tolist())

            if "ts" in columns:
                hist = read_sql("""
                    SELECT *
                    FROM scan_results
                    WHERE symbol=?
                    ORDER BY ts ASC
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
                # SQLite 讀出來的數字欄位有時會變成文字，必須轉成 numeric。
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
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.info("目前沒有 Total Slippage 資料可繪圖。")

                st.subheader("最近 100 筆歷史資料")

                display_cols = existing_cols(hist, [
                    "time",
                    "symbol",
                    "signal",
                    "status",
                    "score",
                    "current_funding_rate",
                    "avg_funding_rate_7d",
                    "basis_rate",
                    "total_slippage",
                    "total_slippage_rate",
                    "payback_days",
                    "open_interest_notional",
                    "quote_volume_24h",
                    "volume_24h",
                    "mark_price",
                    "index_price",
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

    try:
        count_df = read_sql("SELECT COUNT(*) AS count FROM scan_results")
        if not count_df.empty:
            st.metric("scan_results 總筆數", int(count_df["count"].iloc[0]))
    except Exception as e:
        st.error(f"統計資料筆數失敗：{e}")

    st.write("資料表清單：")
    tables_df = read_sql("""
        SELECT name
        FROM sqlite_master
        WHERE type='table'
        ORDER BY name
    """)
    st.dataframe(tables_df, use_container_width=True)
