import os
import time
import sqlite3
import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

import aiohttp


# 必須跟 main.py 的 DB_PATH 預設一致
DB_PATH = os.getenv("DB_PATH", "/app/data/radar.db")

OI_TRACKER_ENABLED = os.getenv("OI_TRACKER_ENABLED", "true").lower() == "true"
OI_TRACK_CHECK_INTERVAL_SECONDS = int(os.getenv("OI_TRACK_CHECK_INTERVAL_SECONDS", "300"))
OI_SIGNAL_DEDUP_SECONDS = int(os.getenv("OI_SIGNAL_DEDUP_SECONDS", "900"))
OI_SIGNAL_EXPIRE_SECONDS = int(os.getenv("OI_SIGNAL_EXPIRE_SECONDS", "3600"))
OI_STATS_LOOKBACK = int(os.getenv("OI_STATS_LOOKBACK", "100"))

BINANCE_FAPI_BASE_URL = os.getenv("BINANCE_FAPI_BASE_URL", "https://fapi.binance.com")


def utc_now_ts() -> int:
    return int(time.time())


def utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def ensure_data_dir():
    d = os.path.dirname(DB_PATH)
    if d:
        os.makedirs(d, exist_ok=True)


def get_conn():
    ensure_data_dir()
    return sqlite3.connect(DB_PATH, timeout=30)


def init_oi_tracker_db():
    ensure_data_dir()

    with get_conn() as conn:
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
        )
        """)

        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_oi_signals_symbol_direction_ts
        ON oi_signals(symbol, direction, detected_ts)
        """)

        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_oi_signals_status
        ON oi_signals(status)
        """)

        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_oi_signals_detected_ts
        ON oi_signals(detected_ts)
        """)

        conn.commit()


def normalize_direction(direction_text: Optional[str]) -> Optional[str]:
    if not direction_text:
        return None

    text = str(direction_text).upper()

    if "SHORT" in text or "空" in text:
        return "SHORT"

    if "LONG" in text or "多" in text:
        return "LONG"

    return None


def calc_directional_return(direction: str, entry_price: float, current_price: float) -> Optional[float]:
    try:
        entry_price = float(entry_price)
        current_price = float(current_price)

        if entry_price <= 0 or current_price <= 0:
            return None

        if direction == "SHORT":
            return (entry_price - current_price) / entry_price

        if direction == "LONG":
            return (current_price - entry_price) / entry_price

        return None

    except Exception:
        return None


async def get_binance_futures_price(session: aiohttp.ClientSession, symbol: str) -> float:
    url = f"{BINANCE_FAPI_BASE_URL}/fapi/v1/ticker/price"

    async with session.get(url, params={"symbol": symbol}) as resp:
        text = await resp.text()

        if resp.status != 200:
            raise RuntimeError(f"ticker price failed status={resp.status} body={text[:200]}")

        data = await resp.json()
        return float(data["price"])


def should_skip_duplicate(symbol: str, direction: str) -> bool:
    now_ts = utc_now_ts()
    since_ts = now_ts - OI_SIGNAL_DEDUP_SECONDS

    with get_conn() as conn:
        cur = conn.cursor()

        row = cur.execute("""
            SELECT id
            FROM oi_signals
            WHERE symbol = ?
              AND direction = ?
              AND detected_ts >= ?
            ORDER BY detected_ts DESC
            LIMIT 1
        """, (symbol, direction, since_ts)).fetchone()

    return row is not None


def record_oi_signal(signal: Dict[str, Any]):
    """
    signal 格式：
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

    symbol = str(signal.get("symbol", "")).upper().strip()
    direction = normalize_direction(signal.get("direction"))

    if not symbol or not direction:
        return False, "missing_symbol_or_direction"

    entry_price = signal.get("entry_price")

    try:
        entry_price = float(entry_price)
        if entry_price <= 0:
            return False, "invalid_entry_price"
    except Exception:
        return False, "invalid_entry_price"

    if should_skip_duplicate(symbol, direction):
        return False, "duplicate_skipped"

    now_ts = utc_now_ts()
    now_text = utc_now_text()

    with get_conn() as conn:
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
            entry_price,
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
            now_text,
        ))

        conn.commit()

    return True, "recorded"


def check_tp_sl(
    direction: str,
    current_price: float,
    tp1: Optional[float],
    tp2: Optional[float],
    stop_price: Optional[float],
):
    hit_tp1 = False
    hit_tp2 = False
    hit_sl = False

    try:
        current_price = float(current_price)
    except Exception:
        return hit_tp1, hit_tp2, hit_sl

    if direction == "SHORT":
        if tp1 is not None and current_price <= float(tp1):
            hit_tp1 = True
        if tp2 is not None and current_price <= float(tp2):
            hit_tp2 = True
        if stop_price is not None and current_price >= float(stop_price):
            hit_sl = True

    elif direction == "LONG":
        if tp1 is not None and current_price >= float(tp1):
            hit_tp1 = True
        if tp2 is not None and current_price >= float(tp2):
            hit_tp2 = True
        if stop_price is not None and current_price <= float(stop_price):
            hit_sl = True

    return hit_tp1, hit_tp2, hit_sl


async def update_open_oi_signals(session: aiohttp.ClientSession):
    if not OI_TRACKER_ENABLED:
        return

    now_ts = utc_now_ts()
    now_text = utc_now_text()

    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        rows = cur.execute("""
            SELECT *
            FROM oi_signals
            WHERE status = 'TRACKING'
            ORDER BY detected_ts ASC
        """).fetchall()

        for row in rows:
            signal_id = row["id"]
            symbol = row["symbol"]
            direction = row["direction"]
            entry_price = row["entry_price"]
            detected_ts = row["detected_ts"]
            age = now_ts - detected_ts

            try:
                current_price = await get_binance_futures_price(session, symbol)
            except Exception as e:
                print(f"[OI Tracker] get price failed {symbol}: {e}")
                continue

            current_return = calc_directional_return(direction, entry_price, current_price)

            if current_return is None:
                continue

            old_mfe = row["max_favorable_return"] if row["max_favorable_return"] is not None else 0.0
            old_mae = row["max_adverse_return"] if row["max_adverse_return"] is not None else 0.0

            new_mfe = max(old_mfe, current_return)
            new_mae = min(old_mae, current_return)

            hit_tp1_now, hit_tp2_now, hit_sl_now = check_tp_sl(
                direction=direction,
                current_price=current_price,
                tp1=row["tp1"],
                tp2=row["tp2"],
                stop_price=row["stop_price"],
            )

            hit_tp1 = 1 if hit_tp1_now or row["hit_tp1"] else 0
            hit_tp2 = 1 if hit_tp2_now or row["hit_tp2"] else 0
            hit_sl = 1 if hit_sl_now or row["hit_sl"] else 0

            updates = {
                "max_favorable_return": new_mfe,
                "max_adverse_return": new_mae,
                "hit_tp1": hit_tp1,
                "hit_tp2": hit_tp2,
                "hit_sl": hit_sl,
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

            # 狀態優先順序：TP2 > TP1 > SL > EXPIRED
            if hit_tp2_now:
                updates["status"] = "HIT_TP2"
            elif hit_tp1_now:
                updates["status"] = "HIT_TP1"
            elif hit_sl_now:
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


async def oi_tracker_loop():
    if not OI_TRACKER_ENABLED:
        print("[OI Tracker] disabled")
        return

    print("[OI Tracker] started")

    timeout = aiohttp.ClientTimeout(total=20)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            try:
                await update_open_oi_signals(session)
            except Exception as e:
                print(f"[OI Tracker] loop error: {e}")

            await asyncio.sleep(OI_TRACK_CHECK_INTERVAL_SECONDS)


def format_pct(x: Optional[float]) -> str:
    if x is None:
        return "-"

    try:
        return f"{float(x) * 100:+.2f}%"
    except Exception:
        return "-"


def format_price(x: Optional[float]) -> str:
    if x is None:
        return "-"

    try:
        return f"{float(x):.8f}"
    except Exception:
        return "-"


def status_emoji(status: str) -> str:
    status = str(status).upper()

    if status == "TRACKING":
        return "⏳"
    if status == "HIT_TP1":
        return "✅"
    if status == "HIT_TP2":
        return "🏆"
    if status == "HIT_SL":
        return "🛑"
    if status == "EXPIRED":
        return "⌛"

    return "•"


def format_oi_log(limit: int = 10) -> str:
    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        rows = cur.execute("""
            SELECT *
            FROM oi_signals
            ORDER BY detected_ts DESC
            LIMIT ?
        """, (limit,)).fetchall()

    if not rows:
        return "📒 OI 訊號紀錄\n\n目前沒有 OI 訊號紀錄。"

    lines = []
    lines.append("📒 <b>OI 訊號紀錄｜最近訊號</b>")
    lines.append("")

    for i, row in enumerate(rows, 1):
        status = row["status"]
        icon = status_emoji(status)

        lines.append(
            f"#{i} <b>{row['symbol']}</b>｜<b>{row['direction']}</b>｜{icon} <code>{status}</code>\n"
            f"時間：<code>{row['detected_at']}</code>\n"
            f"分數：<b>{row['score']}</b>\n"
            f"訊號價：<code>{format_price(row['entry_price'])}</code>\n"
            f"15m：<b>{format_pct(row['return_15m'])}</b>｜"
            f"30m：<b>{format_pct(row['return_30m'])}</b>｜"
            f"60m：<b>{format_pct(row['return_60m'])}</b>\n"
            f"MFE：<b>{format_pct(row['max_favorable_return'])}</b>｜"
            f"MAE：<b>{format_pct(row['max_adverse_return'])}</b>\n"
            f"TP1：{'✅' if row['hit_tp1'] else '❌'}｜"
            f"TP2：{'✅' if row['hit_tp2'] else '❌'}｜"
            f"SL：{'✅' if row['hit_sl'] else '❌'}"
        )
        lines.append("")

    return "\n".join(lines)


def rate(a: int, b: int) -> str:
    if b <= 0:
        return "-"
    return f"{a / b * 100:.1f}%"


def avg_return(rows: List[sqlite3.Row], key: str) -> str:
    vals = []

    for r in rows:
        v = r[key]
        if v is not None:
            vals.append(float(v))

    if not vals:
        return "-"

    return f"{sum(vals) / len(vals) * 100:+.2f}%"


def format_oi_stats(symbol: Optional[str] = None, lookback: Optional[int] = None) -> str:
    if lookback is None:
        lookback = OI_STATS_LOOKBACK

    params = []
    where = ""

    if symbol:
        symbol = symbol.upper().strip()
        where = "WHERE symbol = ?"
        params.append(symbol)

    params.append(lookback)

    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        rows = cur.execute(f"""
            SELECT *
            FROM oi_signals
            {where}
            ORDER BY detected_ts DESC
            LIMIT ?
        """, params).fetchall()

    if not rows:
        if symbol:
            return f"🎯 OI 訊號統計｜{symbol}\n\n目前沒有這個交易對的 OI 統計資料。"
        return "🎯 OI 訊號統計\n\n目前沒有 OI 統計資料。"

    total = len(rows)
    tracking = sum(1 for r in rows if r["status"] == "TRACKING")
    completed = total - tracking

    hit_tp1 = sum(1 for r in rows if r["hit_tp1"])
    hit_tp2 = sum(1 for r in rows if r["hit_tp2"])
    hit_sl = sum(1 for r in rows if r["hit_sl"])

    rows_15m = [r for r in rows if r["return_15m"] is not None]
    rows_30m = [r for r in rows if r["return_30m"] is not None]
    rows_60m = [r for r in rows if r["return_60m"] is not None]

    pos_15m = sum(1 for r in rows_15m if r["return_15m"] > 0)
    pos_30m = sum(1 for r in rows_30m if r["return_30m"] > 0)
    pos_60m = sum(1 for r in rows_60m if r["return_60m"] > 0)

    long_rows = [r for r in rows if r["direction"] == "LONG"]
    short_rows = [r for r in rows if r["direction"] == "SHORT"]

    title = "🎯 <b>OI 訊號統計</b>"
    if symbol:
        title += f"｜<b>{symbol}</b>"

    lines = []
    lines.append(title)
    lines.append("")
    lines.append(f"統計筆數：<b>{total}</b>")
    lines.append(f"追蹤中：<b>{tracking}</b>")
    lines.append(f"已結案：<b>{completed}</b>")
    lines.append("")
    lines.append(f"TP1 命中：<b>{hit_tp1}</b>｜{rate(hit_tp1, total)}")
    lines.append(f"TP2 命中：<b>{hit_tp2}</b>｜{rate(hit_tp2, total)}")
    lines.append(f"防守觸發：<b>{hit_sl}</b>｜{rate(hit_sl, total)}")
    lines.append("")
    lines.append(f"15m 正向率：<b>{pos_15m}/{len(rows_15m)}</b>｜{rate(pos_15m, len(rows_15m))}｜平均：<b>{avg_return(rows_15m, 'return_15m')}</b>")
    lines.append(f"30m 正向率：<b>{pos_30m}/{len(rows_30m)}</b>｜{rate(pos_30m, len(rows_30m))}｜平均：<b>{avg_return(rows_30m, 'return_30m')}</b>")
    lines.append(f"60m 正向率：<b>{pos_60m}/{len(rows_60m)}</b>｜{rate(pos_60m, len(rows_60m))}｜平均：<b>{avg_return(rows_60m, 'return_60m')}</b>")
    lines.append("")
    lines.append(f"LONG 訊號：<b>{len(long_rows)}</b>")
    lines.append(f"SHORT 訊號：<b>{len(short_rows)}</b>")

    return "\n".join(lines)
