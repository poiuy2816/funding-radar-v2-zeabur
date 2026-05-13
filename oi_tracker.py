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
    lines.append("<code>15m / 30m / 60m 為依照 LONG/SHORT 方向換算後的報酬</code>")
    lines.append("")

    for i, row in enumerate(rows, 1):
        status = row["status"]
        icon = status_emoji(status)

        lines.append(
            f"#{i} <b>{row['symbol']}</b>｜<b>{row['direction']}</b>｜{icon} <code>{status}</code>\n"
            f"時間：<code>{row['detected_at']}</code>\n"
            f"分數：<b>{row['score']}</b>\n"
            f"訊號價：<code>{format_price(row['entry_price'])}</code>\n"
            f"方向報酬 15m：<b>{format_pct(row['return_15m'])}</b>｜"
            f"30m：<b>{format_pct(row['return_30m'])}</b>｜"
            f"60m：<b>{format_pct(row['return_60m'])}</b>\n"
            f"MFE 最大有利：<b>{format_pct(row['max_favorable_return'])}</b>｜"
            f"MAE 最大不利：<b>{format_pct(row['max_adverse_return'])}</b>\n"
            f"TP1：{'✅' if row['hit_tp1'] else '❌'}｜"
            f"TP2：{'✅' if row['hit_tp2'] else '❌'}｜"
            f"SL：{'✅' if row['hit_sl'] else '❌'}"
        )
        lines.append("")

    return "\n".join(lines)


# =========================
# Stats Helpers
# =========================

def rate(a: int, b: int) -> str:
    if b <= 0:
        return "-"
    return f"{a / b * 100:.1f}%"


def safe_row_float(row: sqlite3.Row, key: str, default=None):
    try:
        v = row[key]
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def avg_pct_from_values(vals: List[float]) -> str:
    vals = [float(v) for v in vals if v is not None]

    if not vals:
        return "-"

    return f"{sum(vals) / len(vals) * 100:+.2f}%"


def avg_return(rows: List[sqlite3.Row], key: str) -> str:
    vals = []

    for r in rows:
        v = safe_row_float(r, key, None)
        if v is not None:
            vals.append(v)

    return avg_pct_from_values(vals)


def avg_number(rows: List[sqlite3.Row], key: str) -> str:
    vals = []

    for r in rows:
        v = safe_row_float(r, key, None)
        if v is not None:
            vals.append(v)

    if not vals:
        return "-"

    return f"{sum(vals) / len(vals):.2f}"


def status_upper(row: sqlite3.Row) -> str:
    return str(row["status"] or "").upper()


def is_tracking(row: sqlite3.Row) -> bool:
    return status_upper(row) == "TRACKING"


def is_tp1(row: sqlite3.Row) -> bool:
    return status_upper(row) == "HIT_TP1"


def is_tp2(row: sqlite3.Row) -> bool:
    return status_upper(row) == "HIT_TP2"


def is_win(row: sqlite3.Row) -> bool:
    return status_upper(row) in ("HIT_TP1", "HIT_TP2")


def is_loss(row: sqlite3.Row) -> bool:
    return status_upper(row) == "HIT_SL"


def is_expired(row: sqlite3.Row) -> bool:
    return status_upper(row) == "EXPIRED"


def is_closed(row: sqlite3.Row) -> bool:
    return status_upper(row) in ("HIT_TP1", "HIT_TP2", "HIT_SL", "EXPIRED")


def count_status(rows: List[sqlite3.Row], status: str) -> int:
    status = status.upper()
    return sum(1 for r in rows if status_upper(r) == status)


def positive_count(rows: List[sqlite3.Row], key: str) -> int:
    n = 0

    for r in rows:
        v = safe_row_float(r, key, None)
        if v is not None and v > 0:
            n += 1

    return n


def rows_with_value(rows: List[sqlite3.Row], key: str) -> List[sqlite3.Row]:
    return [r for r in rows if safe_row_float(r, key, None) is not None]


def calc_subset_summary(rows: List[sqlite3.Row]) -> Dict[str, Any]:
    total = len(rows)
    tracking = sum(1 for r in rows if is_tracking(r))
    closed = sum(1 for r in rows if is_closed(r))
    wins = sum(1 for r in rows if is_win(r))
    losses = sum(1 for r in rows if is_loss(r))
    expired = sum(1 for r in rows if is_expired(r))
    tp1 = sum(1 for r in rows if is_tp1(r))
    tp2 = sum(1 for r in rows if is_tp2(r))

    strict_base = wins + losses + expired
    trade_base = wins + losses

    return {
        "total": total,
        "tracking": tracking,
        "closed": closed,
        "wins": wins,
        "losses": losses,
        "expired": expired,
        "tp1": tp1,
        "tp2": tp2,
        "strict_base": strict_base,
        "trade_base": trade_base,
        "strict_win_rate": rate(wins, strict_base),
        "trade_win_rate": rate(wins, trade_base),
        "loss_rate": rate(losses, strict_base),
        "expired_rate": rate(expired, strict_base),
    }


def format_subset_line(name: str, rows: List[sqlite3.Row]) -> str:
    s = calc_subset_summary(rows)

    return (
        f"{name}：<b>{s['total']}</b> 筆｜"
        f"勝 <b>{s['wins']}</b>｜敗 <b>{s['losses']}</b>｜過期 <b>{s['expired']}</b>｜"
        f"交易勝率 <b>{s['trade_win_rate']}</b>"
    )


def score_bucket_rows(rows: List[sqlite3.Row], low: Optional[float] = None, high: Optional[float] = None) -> List[sqlite3.Row]:
    result = []

    for r in rows:
        score = safe_row_float(r, "score", None)

        if score is None:
            continue

        if low is not None and score < low:
            continue

        if high is not None and score >= high:
            continue

        result.append(r)

    return result


def get_latest_rows(
    symbol: Optional[str] = None,
    lookback: Optional[int] = None,
    direction: Optional[str] = None,
    since_ts: Optional[int] = None,
) -> List[sqlite3.Row]:
    if lookback is None:
        lookback = OI_STATS_LOOKBACK

    params = []
    where_parts = []

    if symbol:
        symbol = symbol.upper().strip()
        where_parts.append("symbol = ?")
        params.append(symbol)

    if direction:
        direction = direction.upper().strip()
        where_parts.append("UPPER(direction) = ?")
        params.append(direction)

    if since_ts is not None:
        where_parts.append("detected_ts >= ?")
        params.append(int(since_ts))

    where = ""
    if where_parts:
        where = "WHERE " + " AND ".join(where_parts)

    limit_clause = ""
    if lookback is not None and lookback > 0:
        limit_clause = "LIMIT ?"
        params.append(lookback)

    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        rows = cur.execute(f"""
            SELECT *
            FROM oi_signals
            {where}
            ORDER BY detected_ts DESC
            {limit_clause}
        """, params).fetchall()

    return rows


# =========================
# Enhanced OI Stats
# =========================

def format_oi_stats(symbol: Optional[str] = None, lookback: Optional[int] = None) -> str:
    if lookback is None:
        lookback = OI_STATS_LOOKBACK

    rows = get_latest_rows(symbol=symbol, lookback=lookback)

    if symbol:
        symbol = symbol.upper().strip()

    if not rows:
        if symbol:
            return f"🎯 OI 訊號統計｜{symbol}\n\n目前沒有這個交易對的 OI 統計資料。"
        return "🎯 OI 訊號統計\n\n目前沒有 OI 統計資料。"

    total = len(rows)

    tracking = count_status(rows, "TRACKING")
    hit_tp1_status = count_status(rows, "HIT_TP1")
    hit_tp2_status = count_status(rows, "HIT_TP2")
    hit_sl_status = count_status(rows, "HIT_SL")
    expired_status = count_status(rows, "EXPIRED")

    closed_rows = [r for r in rows if is_closed(r)]
    win_rows = [r for r in rows if is_win(r)]
    loss_rows = [r for r in rows if is_loss(r)]
    expired_rows = [r for r in rows if is_expired(r)]

    wins = len(win_rows)
    losses = len(loss_rows)
    expired = len(expired_rows)

    strict_base = wins + losses + expired
    trade_base = wins + losses

    long_rows = [r for r in rows if str(r["direction"]).upper() == "LONG"]
    short_rows = [r for r in rows if str(r["direction"]).upper() == "SHORT"]

    rows_15m = rows_with_value(rows, "return_15m")
    rows_30m = rows_with_value(rows, "return_30m")
    rows_60m = rows_with_value(rows, "return_60m")

    pos_15m = positive_count(rows_15m, "return_15m")
    pos_30m = positive_count(rows_30m, "return_30m")
    pos_60m = positive_count(rows_60m, "return_60m")

    high_90_rows = score_bucket_rows(rows, low=90)
    score_80_90_rows = score_bucket_rows(rows, low=80, high=90)
    low_80_rows = score_bucket_rows(rows, high=80)

    title = "🎯 <b>OI 訊號統計｜強化版</b>"
    if symbol:
        title += f"｜<b>{symbol}</b>"

    lines = []
    lines.append(title)
    if "since_text" in locals() and since_text:
        lines.append(f"統計範圍：<b>{since_text}</b> 之後")
        lines.append("時間基準：<code>detected_ts / detected_at UTC</code>")
    if "direction" in locals() and direction:
        lines.append(f"方向篩選：<b>{direction}</b>")
    lines.append("")
    lines.append(f"統計範圍：最近 <b>{total}</b> 筆 / 上限 <b>{lookback}</b> 筆")
    lines.append(f"平均分數：<b>{avg_number(rows, 'score')}</b>")
    lines.append("")
    lines.append("📌 <b>狀態分布</b>")
    lines.append(f"追蹤中 TRACKING：<b>{tracking}</b>")
    lines.append(f"✅ HIT_TP1：<b>{hit_tp1_status}</b>")
    lines.append(f"🏆 HIT_TP2：<b>{hit_tp2_status}</b>")
    lines.append(f"🛑 HIT_SL：<b>{hit_sl_status}</b>")
    lines.append(f"⌛ EXPIRED：<b>{expired_status}</b>")
    lines.append("")

    lines.append("🏁 <b>勝負統計</b>")
    lines.append(f"勝：<b>{wins}</b>｜敗：<b>{losses}</b>｜過期：<b>{expired}</b>")
    lines.append(f"嚴格勝率：<b>{rate(wins, strict_base)}</b> <code>勝 / 勝+敗+過期</code>")
    lines.append(f"交易勝率：<b>{rate(wins, trade_base)}</b> <code>勝 / 勝+敗</code>")
    lines.append(f"TP2 率：<b>{rate(hit_tp2_status, strict_base)}</b>")
    lines.append(f"SL 率：<b>{rate(losses, strict_base)}</b>")
    lines.append("")

    lines.append("⏱ <b>時間報酬統計</b>")
    lines.append(
        f"15m 正向率：<b>{pos_15m}/{len(rows_15m)}</b>｜"
        f"{rate(pos_15m, len(rows_15m))}｜平均：<b>{avg_return(rows_15m, 'return_15m')}</b>"
    )
    lines.append(
        f"30m 正向率：<b>{pos_30m}/{len(rows_30m)}</b>｜"
        f"{rate(pos_30m, len(rows_30m))}｜平均：<b>{avg_return(rows_30m, 'return_30m')}</b>"
    )
    lines.append(
        f"60m 正向率：<b>{pos_60m}/{len(rows_60m)}</b>｜"
        f"{rate(pos_60m, len(rows_60m))}｜平均：<b>{avg_return(rows_60m, 'return_60m')}</b>"
    )
    lines.append("")

    lines.append("📈 <b>MFE / MAE</b>")
    lines.append(f"平均最大有利 MFE：<b>{avg_return(rows, 'max_favorable_return')}</b>")
    lines.append(f"平均最大不利 MAE：<b>{avg_return(rows, 'max_adverse_return')}</b>")
    lines.append("")

    lines.append("🟢🔴 <b>多空拆分</b>")
    lines.append(format_subset_line("LONG", long_rows))
    lines.append(format_subset_line("SHORT", short_rows))
    lines.append("")

    lines.append("⭐ <b>分數區間</b>")
    lines.append(format_subset_line("90 分以上", high_90_rows))
    lines.append(format_subset_line("80～89 分", score_80_90_rows))
    lines.append(format_subset_line("80 分以下", low_80_rows))
    lines.append("")

    lines.append("📖 <b>口徑說明</b>")
    lines.append("勝：<code>HIT_TP1 / HIT_TP2</code>")
    lines.append("敗：<code>HIT_SL</code>")
    lines.append("過期：<code>EXPIRED</code>")
    lines.append("嚴格勝率包含 EXPIRED，交易勝率不包含 EXPIRED。")

    return "\n".join(lines)


# =========================
# OI Simulation
# =========================

OI_SIM_LEVERAGE = float(os.getenv("OI_SIM_LEVERAGE", "1"))
OI_SIM_ROUNDTRIP_FEE_RATE = float(os.getenv("OI_SIM_ROUNDTRIP_FEE_RATE", "0.0008"))
OI_SIM_SLIPPAGE_RATE = float(os.getenv("OI_SIM_SLIPPAGE_RATE", "0.0003"))
OI_SIM_INCLUDE_EXPIRED = os.getenv("OI_SIM_INCLUDE_EXPIRED", "true").lower() == "true"


def parse_oi_sim_since_text(text: str) -> Optional[Dict[str, Any]]:
    text = str(text or "").strip()

    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return {
                "ts": int(dt.timestamp()),
                "text": dt.strftime("%Y-%m-%d %H:%M:%S UTC"),
            }
        except ValueError:
            continue

    return None


def first_available_return(row: sqlite3.Row) -> Optional[float]:
    for key in ("return_60m", "return_30m", "return_15m"):
        v = safe_row_float(row, key, None)
        if v is not None:
            return v

    return None


def calc_target_return_from_row(row: sqlite3.Row, target_key: str) -> Optional[float]:
    direction = str(row["direction"]).upper()
    entry_price = safe_row_float(row, "entry_price", None)
    target_price = safe_row_float(row, target_key, None)

    if entry_price is None or target_price is None:
        return None

    return calc_directional_return(direction, entry_price, target_price)


def estimate_exit_return(row: sqlite3.Row) -> Optional[float]:
    """
    用目前資料估算出場報酬。

    HIT_TP2：用 tp2 價估算
    HIT_TP1：用 tp1 價估算
    HIT_SL：用 stop_price 價估算
    EXPIRED：用 60m 報酬，沒有就用 30m，再沒有就用 15m
    TRACKING：不納入
    """
    status = status_upper(row)

    if status == "HIT_TP2":
        r = calc_target_return_from_row(row, "tp2")
        if r is not None:
            return r
        return safe_row_float(row, "max_favorable_return", None)

    if status == "HIT_TP1":
        r = calc_target_return_from_row(row, "tp1")
        if r is not None:
            return r
        return safe_row_float(row, "max_favorable_return", None)

    if status == "HIT_SL":
        r = calc_target_return_from_row(row, "stop_price")
        if r is not None:
            return r
        return safe_row_float(row, "max_adverse_return", None)

    if status == "EXPIRED":
        return first_available_return(row)

    return None


def calc_max_drawdown(returns: List[float]) -> float:
    equity = 1.0
    peak = 1.0
    max_dd = 0.0

    for r in returns:
        equity *= (1.0 + r)

        if equity > peak:
            peak = equity

        dd = (equity - peak) / peak

        if dd < max_dd:
            max_dd = dd

    return max_dd


def calc_profit_factor(returns: List[float]) -> str:
    gross_profit = sum(r for r in returns if r > 0)
    gross_loss = abs(sum(r for r in returns if r < 0))

    if gross_loss <= 0:
        if gross_profit > 0:
            return "∞"
        return "-"

    return f"{gross_profit / gross_loss:.2f}"


def max_consecutive_losses(returns: List[float]) -> int:
    max_streak = 0
    cur = 0

    for r in returns:
        if r < 0:
            cur += 1
            max_streak = max(max_streak, cur)
        else:
            cur = 0

    return max_streak


def format_oi_sim(
    symbol: Optional[str] = None,
    lookback: Optional[int] = None,
    direction: Optional[str] = None,
    since_ts: Optional[int] = None,
    since_text: Optional[str] = None,
) -> str:
    if lookback is None:
        lookback = OI_STATS_LOOKBACK

    rows = get_latest_rows(
        symbol=symbol,
        lookback=lookback,
        direction=direction,
        since_ts=since_ts,
    )

    if symbol:
        symbol = symbol.upper().strip()

    if direction:
        direction = direction.upper().strip()

    if not rows:
        if symbol:
            return f"📈 OI 模擬績效｜{symbol}\n\n目前沒有這個交易對的 OI 紀錄。"
        return "📈 OI 模擬績效\n\n目前沒有 OI 紀錄。"

    sim_rows = []

    for r in rows:
        if is_tracking(r):
            continue

        if is_expired(r) and not OI_SIM_INCLUDE_EXPIRED:
            continue

        gross_return = estimate_exit_return(r)

        if gross_return is None:
            continue

        total_cost = OI_SIM_ROUNDTRIP_FEE_RATE + OI_SIM_SLIPPAGE_RATE
        net_return = gross_return * OI_SIM_LEVERAGE - total_cost * OI_SIM_LEVERAGE

        sim_rows.append({
            "row": r,
            "gross_return": gross_return,
            "net_return": net_return,
        })

    if not sim_rows:
        return (
            "📈 <b>OI 模擬績效</b>\n\n"
            "目前沒有足夠的已結案訊號可以模擬。"
        )

    net_returns = [x["net_return"] for x in sim_rows]
    gross_returns = [x["gross_return"] for x in sim_rows]

    total_trades = len(sim_rows)
    wins = sum(1 for r in net_returns if r > 0)
    losses = sum(1 for r in net_returns if r < 0)
    flats = total_trades - wins - losses

    avg_net = sum(net_returns) / len(net_returns)
    total_simple = sum(net_returns)

    equity = 1.0
    for r in net_returns:
        equity *= (1.0 + r)

    compounded_return = equity - 1.0
    max_dd = calc_max_drawdown(net_returns)
    pf = calc_profit_factor(net_returns)
    max_loss_streak = max_consecutive_losses(net_returns)

    long_sim = [x for x in sim_rows if str(x["row"]["direction"]).upper() == "LONG"]
    short_sim = [x for x in sim_rows if str(x["row"]["direction"]).upper() == "SHORT"]

    def sim_subset_line(name: str, items: List[Dict[str, Any]]) -> str:
        if not items:
            return f"{name}：<b>0</b> 筆"

        rs = [x["net_return"] for x in items]
        w = sum(1 for r in rs if r > 0)

        return (
            f"{name}：<b>{len(items)}</b> 筆｜"
            f"勝率 <b>{rate(w, len(items))}</b>｜"
            f"平均 <b>{avg_pct_from_values(rs)}</b>｜"
            f"總和 <b>{format_pct(sum(rs))}</b>"
        )

    best = sorted(sim_rows, key=lambda x: x["net_return"], reverse=True)[:3]
    worst = sorted(sim_rows, key=lambda x: x["net_return"])[:3]

    title = "📈 <b>OI 模擬績效</b>"
    if symbol:
        title += f"｜<b>{symbol}</b>"

    lines = []
    lines.append(title)
    if "since_text" in locals() and since_text:
        lines.append(f"統計範圍：<b>{since_text}</b> 之後")
        lines.append("時間基準：<code>detected_ts / detected_at UTC</code>")
    if "direction" in locals() and direction:
        lines.append(f"方向篩選：<b>{direction}</b>")
    lines.append("")
    if not since_text:
        lines.append(f"統計範圍：最近 <b>{len(rows)}</b> 筆 / 上限 <b>{lookback}</b> 筆")
    lines.append(f"納入模擬：<b>{total_trades}</b> 筆已結案訊號")
    lines.append("")
    lines.append("⚙️ <b>模擬參數</b>")
    lines.append(f"槓桿：<b>{OI_SIM_LEVERAGE:.1f}x</b>")
    lines.append(f"來回手續費：<b>{format_pct(OI_SIM_ROUNDTRIP_FEE_RATE)}</b>")
    lines.append(f"滑價成本：<b>{format_pct(OI_SIM_SLIPPAGE_RATE)}</b>")
    lines.append(f"是否納入 EXPIRED：<b>{'是' if OI_SIM_INCLUDE_EXPIRED else '否'}</b>")
    lines.append("")

    lines.append("🏁 <b>模擬結果</b>")
    lines.append(f"勝：<b>{wins}</b>｜敗：<b>{losses}</b>｜打平：<b>{flats}</b>")
    lines.append(f"勝率：<b>{rate(wins, total_trades)}</b>")
    lines.append(f"平均單筆淨報酬：<b>{format_pct(avg_net)}</b>")
    lines.append(f"單利總報酬：<b>{format_pct(total_simple)}</b>")
    lines.append(f"複利總報酬：<b>{format_pct(compounded_return)}</b>")
    lines.append(f"最大回撤：<b>{format_pct(max_dd)}</b>")
    lines.append(f"Profit Factor：<b>{pf}</b>")
    lines.append(f"最大連虧：<b>{max_loss_streak}</b> 筆")
    lines.append("")

    lines.append("🟢🔴 <b>多空模擬</b>")
    lines.append(sim_subset_line("LONG", long_sim))
    lines.append(sim_subset_line("SHORT", short_sim))
    lines.append("")

    lines.append("🏆 <b>最佳 3 筆</b>")
    for i, item in enumerate(best, 1):
        r = item["row"]
        lines.append(
            f"#{i} <b>{r['symbol']}</b>｜{r['direction']}｜{status_emoji(r['status'])} {r['status']}｜"
            f"<b>{format_pct(item['net_return'])}</b>"
        )

    lines.append("")
    lines.append("🧊 <b>最差 3 筆</b>")
    for i, item in enumerate(worst, 1):
        r = item["row"]
        lines.append(
            f"#{i} <b>{r['symbol']}</b>｜{r['direction']}｜{status_emoji(r['status'])} {r['status']}｜"
            f"<b>{format_pct(item['net_return'])}</b>"
        )

    lines.append("")
    lines.append("📖 <b>模擬口徑</b>")
    lines.append("HIT_TP1：以 TP1 價出場")
    lines.append("HIT_TP2：以 TP2 價出場")
    lines.append("HIT_SL：以 stop_price 出場")
    lines.append("EXPIRED：以 60m 報酬估算，沒有 60m 則用 30m / 15m")
    lines.append("此為粗略模擬，未處理同一根 K 線內 TP/SL 先後順序。")

    return "\n".join(lines)
