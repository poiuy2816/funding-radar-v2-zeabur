# oi_tracker.py

import os
import time
import sqlite3
import asyncio
import requests
from datetime import datetime, timezone

DB_PATH = os.getenv("DB_PATH", "/data/funding_radar.db")

OI_TRACKER_ENABLED = os.getenv("OI_TRACKER_ENABLED", "true").lower() == "true"
OI_TRACK_CHECK_INTERVAL_SECONDS = int(os.getenv("OI_TRACK_CHECK_INTERVAL_SECONDS", "300"))
OI_SIGNAL_DEDUP_SECONDS = int(os.getenv("OI_SIGNAL_DEDUP_SECONDS", "900"))
OI_SIGNAL_EXPIRE_SECONDS = int(os.getenv("OI_SIGNAL_EXPIRE_SECONDS", "3600"))
OI_STATS_LOOKBACK = int(os.getenv("OI_STATS_LOOKBACK", "100"))


def utc_now_ts():
    return int(time.time())


def utc_now_text():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def get_conn():
    return sqlite3.connect(DB_PATH)


def init_oi_tracker_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS oi_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        detected_ts INTEGER NOT NULL,
        detected_at TEXT NOT NULL,

        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        score REAL,
        label TEXT,

        entry_price REAL NOT NULL,
        price_change_15m REAL,
        oi_change_15m REAL,
        volume_ratio REAL,
        rsi REAL,
        atr REAL,
        funding REAL,
        quote_volume REAL,

        zone_low REAL,
        zone_high REAL,
        stop_price REAL,
        tp1 REAL,
        tp2 REAL,

        price_15m REAL,
        price_30m REAL,
        price_60m REAL,

        return_15m REAL,
        return_30m REAL,
        return_60m REAL,

        hit_tp1 INTEGER DEFAULT 0,
        hit_tp2 INTEGER DEFAULT 0,
        hit_sl INTEGER DEFAULT 0,

        max_favorable_return REAL DEFAULT 0,
        max_adverse_return REAL DEFAULT 0,

        status TEXT DEFAULT 'TRACKING',

        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_oi_signals_symbol_direction_ts
    ON oi_signals(symbol, direction, detected_ts);
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_oi_signals_status
    ON oi_signals(status);
    """)

    conn.commit()
    conn.close()


def normalize_direction(direction_text: str):
    if not direction_text:
        return None

    text = direction_text.upper()

    if "SHORT" in text or "空" in text:
        return "SHORT"

    if "LONG" in text or "多" in text:
        return "LONG"

    return None


def calc_directional_return(direction, entry_price, current_price):
    if not entry_price or not current_price:
        return None

    if direction == "SHORT":
        return (entry_price - current_price) / entry_price

    if direction == "LONG":
        return (current_price - entry_price) / entry_price

    return None


def get_binance_futures_price(symbol):
    url = "https://fapi.binance.com/fapi/v1/ticker/price"
    r = requests.get(url, params={"symbol": symbol}, timeout=10)
    r.raise_for_status()
    data = r.json()
    return float(data["price"])


def should_skip_duplicate(symbol, direction):
    now_ts = utc_now_ts()
    since_ts = now_ts - OI_SIGNAL_DEDUP_SECONDS

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT id
        FROM oi_signals
        WHERE symbol = ?
          AND direction = ?
          AND detected_ts >= ?
        ORDER BY detected_ts DESC
        LIMIT 1
    """, (symbol, direction, since_ts))

    row = cur.fetchone()
    conn.close()

    return row is not None


def record_oi_signal(signal: dict):
    """
    signal dict 建議格式：
    {
        "symbol": "FILUSDT",
        "direction": "SHORT",
        "score": 100,
        "label": "強訊號",
        "entry_price": 1.12,
        "price_change_15m": -0.0115,
        "oi_change_15m": 0.056,
        "volume_ratio": 2.22,
        "rsi": 54.8,
        "atr": 0.0175,
        "funding": 0.0001,
        "quote_volume": 219089562,
        "zone_low": 1.1182,
        "zone_high": 1.1235,
        "stop_price": 1.1463,
        "tp1": 1.0883,
        "tp2": 1.0672
    }
    """

    if not OI_TRACKER_ENABLED:
        return False, "tracker_disabled"

    symbol = signal.get("symbol")
    direction = normalize_direction(signal.get("direction"))

    if not symbol or not direction:
        return False, "missing_symbol_or_direction"

    if should_skip_duplicate(symbol, direction):
        return False, "duplicate_skipped"

    now_ts = utc_now_ts()
    now_text = utc_now_text()

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO oi_signals (
            detected_ts,
            detected_at,
            symbol,
            direction,
            score,
            label,
            entry_price,
            price_change_15m,
            oi_change_15m,
            volume_ratio,
            rsi,
            atr,
            funding,
            quote_volume,
            zone_low,
            zone_high,
            stop_price,
            tp1,
            tp2,
            status,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'TRACKING', ?, ?)
    """, (
        now_ts,
        now_text,
        symbol,
        direction,
        signal.get("score"),
        signal.get("label"),
        signal.get("entry_price"),
        signal.get("price_change_15m"),
        signal.get("oi_change_15m"),
        signal.get("volume_ratio"),
        signal.get("rsi"),
        signal.get("atr"),
        signal.get("funding"),
        signal.get("quote_volume"),
        signal.get("zone_low"),
        signal.get("zone_high"),
        signal.get("stop_price"),
        signal.get("tp1"),
        signal.get("tp2"),
        now_text,
        now_text
    ))

    conn.commit()
    conn.close()

    return True, "recorded"


def check_tp_sl(direction, current_price, tp1, tp2, stop_price):
    hit_tp1 = False
    hit_tp2 = False
    hit_sl = False

    if direction == "SHORT":
        if tp1 is not None and current_price <= tp1:
            hit_tp1 = True
        if tp2 is not None and current_price <= tp2:
            hit_tp2 = True
        if stop_price is not None and current_price >= stop_price:
            hit_sl = True

    elif direction == "LONG":
        if tp1 is not None and current_price >= tp1:
            hit_tp1 = True
        if tp2 is not None and current_price >= tp2:
            hit_tp2 = True
        if stop_price is not None and current_price <= stop_price:
            hit_sl = True

    return hit_tp1, hit_tp2, hit_sl


def update_open_oi_signals():
    if not OI_TRACKER_ENABLED:
        return

    now_ts = utc_now_ts()
    now_text = utc_now_text()

    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM oi_signals
        WHERE status = 'TRACKING'
        ORDER BY detected_ts ASC
    """)

    rows = cur.fetchall()

    for row in rows:
        signal_id = row["id"]
        symbol = row["symbol"]
        direction = row["direction"]
        entry_price = row["entry_price"]
        detected_ts = row["detected_ts"]
        age = now_ts - detected_ts

        try:
            current_price = get_binance_futures_price(symbol)
        except Exception as e:
            print(f"[OI Tracker] get price failed {symbol}: {e}")
            continue

        current_return = calc_directional_return(direction, entry_price, current_price)
        if current_return is None:
            continue

        old_mfe = row["max_favorable_return"] or 0
        old_mae = row["max_adverse_return"] or 0

        new_mfe = max(old_mfe, current_return)
        new_mae = min(old_mae, current_return)

        hit_tp1, hit_tp2, hit_sl = check_tp_sl(
            direction=direction,
            current_price=current_price,
            tp1=row["tp1"],
            tp2=row["tp2"],
            stop_price=row["stop_price"]
        )

        updates = {
            "max_favorable_return": new_mfe,
            "max_adverse_return": new_mae,
            "hit_tp1": 1 if hit_tp1 or row["hit_tp1"] else 0,
            "hit_tp2": 1 if hit_tp2 or row["hit_tp2"] else 0,
            "hit_sl": 1 if hit_sl or row["hit_sl"] else 0,
            "status": "TRACKING",
        }

        if age >= 15 * 60 and row["price_15m"] is None:
            updates["price_15m"] = current_price
            updates["return_15m"] = current_return

        if age >= 30 * 60 and row["price_30m"] is None:
            updates["price_30m"] = current_price
            updates["return_30m"] = current_return

        if age >= 60 * 60 and row["price_60m"] is None:
            updates["price_60m"] = current_price
            updates["return_60m"] = current_return

        if hit_tp2:
            updates["status"] = "HIT_TP2"
        elif hit_tp1:
            updates["status"] = "HIT_TP1"
        elif hit_sl:
            updates["status"] = "HIT_SL"
        elif age >= OI_SIGNAL_EXPIRE_SECONDS:
            updates["status"] = "EXPIRED"

        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        values = list(updates.values())
        values.append(now_text)
        values.append(signal_id)

        cur.execute(f"""
            UPDATE oi_signals
            SET {set_clause},
                updated_at = ?
            WHERE id = ?
        """, values)

    conn.commit()
    conn.close()


async def oi_tracker_loop():
    if not OI_TRACKER_ENABLED:
        print("[OI Tracker] disabled")
        return

    print("[OI Tracker] started")

    while True:
        try:
            update_open_oi_signals()
        except Exception as e:
            print(f"[OI Tracker] loop error: {e}")

        await asyncio.sleep(OI_TRACK_CHECK_INTERVAL_SECONDS)


def format_pct(x):
    if x is None:
        return "-"

    return f"{x * 100:+.2f}%"


def format_oi_log(limit=10):
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM oi_signals
        ORDER BY detected_ts DESC
        LIMIT ?
    """, (limit,))

    rows = cur.fetchall()
    conn.close()

    if not rows:
        return "目前沒有 OI 訊號紀錄。"

    lines = []
    lines.append("📒 OI 訊號紀錄｜最近訊號")
    lines.append("")

    for i, row in enumerate(rows, 1):
        lines.append(
            f"#{i} {row['symbol']}｜{row['direction']}｜{row['status']}\n"
            f"時間：{row['detected_at']}\n"
            f"分數：{row['score']}\n"
            f"訊號價：{row['entry_price']}\n"
            f"15m：{format_pct(row['return_15m'])}｜30m：{format_pct(row['return_30m'])}｜60m：{format_pct(row['return_60m'])}\n"
            f"MFE：{format_pct(row['max_favorable_return'])}｜MAE：{format_pct(row['max_adverse_return'])}\n"
            f"TP1：{'✅' if row['hit_tp1'] else '❌'}｜TP2：{'✅' if row['hit_tp2'] else '❌'}｜SL：{'✅' if row['hit_sl'] else '❌'}"
        )
        lines.append("")

    return "\n".join(lines)


def format_oi_stats(symbol=None, lookback=None):
    if lookback is None:
        lookback = OI_STATS_LOOKBACK

    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    params = []
    where = ""

    if symbol:
        where = "WHERE symbol = ?"
        params.append(symbol.upper())

    query = f"""
        SELECT *
        FROM oi_signals
        {where}
        ORDER BY detected_ts DESC
        LIMIT ?
    """

    params.append(lookback)
    cur.execute(query, params)

    rows = cur.fetchall()
    conn.close()

    if not rows:
        if symbol:
            return f"目前沒有 {symbol.upper()} 的 OI 統計資料。"
        return "目前沒有 OI 統計資料。"

    total = len(rows)
    tracking = sum(1 for r in rows if r["status"] == "TRACKING")
    hit_tp1 = sum(1 for r in rows if r["hit_tp1"])
    hit_tp2 = sum(1 for r in rows if r["hit_tp2"])
    hit_sl = sum(1 for r in rows if r["hit_sl"])

    completed_rows = [r for r in rows if r["status"] != "TRACKING"]

    positive_15m_rows = [r for r in rows if r["return_15m"] is not None]
    positive_30m_rows = [r for r in rows if r["return_30m"] is not None]
    positive_60m_rows = [r for r in rows if r["return_60m"] is not None]

    positive_15m = sum(1 for r in positive_15m_rows if r["return_15m"] > 0)
    positive_30m = sum(1 for r in positive_30m_rows if r["return_30m"] > 0)
    positive_60m = sum(1 for r in positive_60m_rows if r["return_60m"] > 0)

    def rate(a, b):
        if b == 0:
            return "-"
        return f"{a / b * 100:.1f}%"

    title = "🎯 OI 訊號統計"
    if symbol:
        title += f"｜{symbol.upper()}"

    lines = []
    lines.append(title)
    lines.append("")
    lines.append(f"統計筆數：{total}")
    lines.append(f"追蹤中：{tracking}")
    lines.append(f"已結案：{len(completed_rows)}")
    lines.append("")
    lines.append(f"TP1 命中：{hit_tp1}｜{rate(hit_tp1, total)}")
    lines.append(f"TP2 命中：{hit_tp2}｜{rate(hit_tp2, total)}")
    lines.append(f"防守觸發：{hit_sl}｜{rate(hit_sl, total)}")
    lines.append("")
    lines.append(f"15m 正向率：{positive_15m}/{len(positive_15m_rows)}｜{rate(positive_15m, len(positive_15m_rows))}")
    lines.append(f"30m 正向率：{positive_30m}/{len(positive_30m_rows)}｜{rate(positive_30m, len(positive_30m_rows))}")
    lines.append(f"60m 正向率：{positive_60m}/{len(positive_60m_rows)}｜{rate(positive_60m, len(positive_60m_rows))}")

    return "\n".join(lines)
