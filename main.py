import os
import json
import time
import hmac
import html
import hashlib
import sqlite3
import asyncio
import logging
from uuid import uuid4
from datetime import datetime, timezone
from urllib.parse import urlencode
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import pandas as pd
from dotenv import load_dotenv


load_dotenv()


# =========================
# Config
# =========================
BINANCE_FAPI_BASE_URL = os.getenv("BINANCE_FAPI_BASE_URL", "https://fapi.binance.com")
BINANCE_SPOT_BASE_URL = os.getenv("BINANCE_SPOT_BASE_URL", "https://api.binance.com")

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = str(os.getenv("TELEGRAM_CHAT_ID", ""))

DB_PATH = os.getenv("DB_PATH", "/app/data/radar.db")

SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "600"))
ALERT_COOLDOWN_SECONDS = int(os.getenv("ALERT_COOLDOWN_SECONDS", "14400"))
REQUEST_CONCURRENCY = int(os.getenv("REQUEST_CONCURRENCY", "8"))
HTTP_TIMEOUT_SECONDS = int(os.getenv("HTTP_TIMEOUT_SECONDS", "20"))

# =========================
# 正向套利真實淨利版核心設定
# =========================
TARGET_APY = float(os.getenv("TARGET_APY", "0.16"))
TARGET_NET_APY = float(os.getenv("TARGET_NET_APY", "0.16"))

EXPECTED_HOLD_DAYS = float(os.getenv("EXPECTED_HOLD_DAYS", "30"))
INCLUDE_EXIT_COST = os.getenv("INCLUDE_EXIT_COST", "true").lower() == "true"

# 16% 年化對應單期 funding 底線
# Binance funding 通常一天 3 期
MIN_REQUIRED_AVG_FUNDING_RATE = TARGET_APY / 365 / 3

CURRENT_FUNDING_RATE_THRESHOLD = max(
    float(os.getenv("CURRENT_FUNDING_RATE_THRESHOLD", "0.0002")),
    0.0002,
)

AVG_FUNDING_RATE_THRESHOLD = max(
    float(os.getenv("AVG_FUNDING_RATE_THRESHOLD", "0.00015")),
    MIN_REQUIRED_AVG_FUNDING_RATE,
    0.00015,
)

POSITIVE_RATIO_THRESHOLD = max(
    float(os.getenv("POSITIVE_RATIO_THRESHOLD", "0.8")),
    0.8,
)

HISTORICAL_FUNDING_LIMIT = int(os.getenv("HISTORICAL_FUNDING_LIMIT", "21"))
ALWAYS_TRACK_SYMBOLS = {
    norm_symbol(x)
    for x in os.getenv("ALWAYS_TRACK_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT").split(",")
    if x.strip()
}


HIGH_RISK_CURRENT_RATE_THRESHOLD = float(os.getenv("HIGH_RISK_CURRENT_RATE_THRESHOLD", "0.0005"))
ENABLE_HIGH_RISK_WATCHLIST = os.getenv("ENABLE_HIGH_RISK_WATCHLIST", "true").lower() == "true"

MAX_ABS_BASIS_RATE = float(os.getenv("MAX_ABS_BASIS_RATE", "0.0015"))

MAX_FUNDING_STD_7D = float(os.getenv("MAX_FUNDING_STD_7D", "0.0002"))
MAX_STD_TO_AVG_RATIO = float(os.getenv("MAX_STD_TO_AVG_RATIO", "2.0"))
MAX_ABS_HISTORICAL_FUNDING_RATE = float(os.getenv("MAX_ABS_HISTORICAL_FUNDING_RATE", "0.003"))

RECENT_FUNDING_CHECK_PERIODS = int(os.getenv("RECENT_FUNDING_CHECK_PERIODS", "3"))
RECENT_AVG_MIN_RATIO_TO_7D = float(os.getenv("RECENT_AVG_MIN_RATIO_TO_7D", "0.7"))
CURRENT_MIN_RATIO_TO_7D = float(os.getenv("CURRENT_MIN_RATIO_TO_7D", "0.6"))

SPOT_TAKER_FEE_RATE = float(os.getenv("SPOT_TAKER_FEE_RATE", "0.001"))
FUTURES_TAKER_FEE_RATE = float(os.getenv("FUTURES_TAKER_FEE_RATE", "0.001"))

USE_DYNAMIC_SLIPPAGE_COST = os.getenv("USE_DYNAMIC_SLIPPAGE_COST", "true").lower() == "true"
SLIPPAGE_TEST_NOTIONAL_USDT = float(os.getenv("SLIPPAGE_TEST_NOTIONAL_USDT", "10000"))
MAX_TOTAL_SLIPPAGE_RATE = float(os.getenv("MAX_TOTAL_SLIPPAGE_RATE", "0.001"))
ORDER_BOOK_LIMIT = int(os.getenv("ORDER_BOOK_LIMIT", "100"))

MAX_PAYBACK_DAYS = min(
    float(os.getenv("MAX_PAYBACK_DAYS", "4")),
    4.0,
)

STRONG_SIGNAL_PAYBACK_DAYS = min(
    float(os.getenv("STRONG_SIGNAL_PAYBACK_DAYS", "2")),
    MAX_PAYBACK_DAYS,
)

MIN_24H_QUOTE_VOLUME_USDT = max(
    float(os.getenv("MIN_24H_QUOTE_VOLUME_USDT", "10000000")),
    5_000_000,
)

MIN_OPEN_INTEREST_NOTIONAL_USDT = max(
    float(os.getenv("MIN_OPEN_INTEREST_NOTIONAL_USDT", "5000000")),
    5_000_000,
)

ENABLE_TRADING = os.getenv("ENABLE_TRADING", "false").lower() == "true"
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
MAX_ORDER_NOTIONAL_USDT = float(os.getenv("MAX_ORDER_NOTIONAL_USDT", "100"))
DEFAULT_ORDER_NOTIONAL_USDT = float(os.getenv("DEFAULT_ORDER_NOTIONAL_USDT", "50"))
DEFAULT_FUTURES_LEVERAGE = int(os.getenv("DEFAULT_FUTURES_LEVERAGE", "1"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("funding-radar")


# =========================
# Helpers
# =========================
def now_ts() -> int:
    return int(time.time())


def utc_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def fmt_pct(x: Optional[float], digits: int = 4) -> str:
    if x is None:
        return "N/A"
    try:
        return f"{float(x) * 100:.{digits}f}%"
    except Exception:
        return "N/A"


def norm_symbol(s: str) -> str:
    return s.strip().upper()


def ensure_data_dir():
    d = os.path.dirname(DB_PATH)
    if d:
        os.makedirs(d, exist_ok=True)


def safe_float(x, default=0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


# =========================
# SQLite DB
# =========================
class RadarDB:
    def __init__(self, path: str):
        self.path = path
        ensure_data_dir()
        self.init_db()
        self.ensure_scan_result_columns()

    def conn(self):
        return sqlite3.connect(self.path, timeout=30)

    def init_db(self):
        with self.conn() as con:
            cur = con.cursor()

            cur.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS blacklist (
                symbol TEXT PRIMARY KEY,
                reason TEXT,
                created_at INTEGER NOT NULL
            )
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                alert_key TEXT PRIMARY KEY,
                last_sent_ts INTEGER NOT NULL
            )
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS scan_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                status TEXT NOT NULL,
                signal_level TEXT,
                arb_direction TEXT,
                current_funding_rate REAL,
                avg_funding_rate_7d REAL,
                std_funding_rate_7d REAL,
                positive_ratio_7d REAL,
                recent_avg_funding_rate REAL,
                apy REAL,
                gross_apy REAL,
                net_apy REAL,
                entry_cost_rate REAL,
                roundtrip_cost_rate REAL,
                daily_funding_yield REAL,
                daily_cost_drag REAL,
                expected_hold_days REAL,
                payback_days REAL,
                basis_rate REAL,
                spot_slippage REAL,
                futures_slippage REAL,
                total_slippage REAL,
                quote_volume REAL,
                open_interest_notional REAL,
                mark_price REAL,
                fail_reason TEXT
            )
            """)

            cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_scan_results_ts_symbol
            ON scan_results(ts, symbol)
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS order_intents (
                id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                notional_usdt REAL NOT NULL,
                status TEXT NOT NULL,
                confirm_code TEXT NOT NULL,
                reason TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                executed_at INTEGER,
                dry_run INTEGER NOT NULL,
                result_json TEXT
            )
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                intent_id TEXT,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                notional_usdt REAL,
                result_json TEXT
            )
            """)

            con.commit()

        self.set_default("scanner_paused", "false")
        self.set_default("telegram_paused", "false")

    def ensure_scan_result_columns(self):
        columns_to_add = {
            "arb_direction": "TEXT",
            "gross_apy": "REAL",
            "net_apy": "REAL",
            "entry_cost_rate": "REAL",
            "roundtrip_cost_rate": "REAL",
            "daily_funding_yield": "REAL",
            "daily_cost_drag": "REAL",
            "expected_hold_days": "REAL",
        }

        with self.conn() as con:
            rows = con.execute("PRAGMA table_info(scan_results)").fetchall()
            existing_cols = {r[1] for r in rows}

            for col, col_type in columns_to_add.items():
                if col not in existing_cols:
                    logger.info(f"DB migrate: add column scan_results.{col}")
                    con.execute(f"ALTER TABLE scan_results ADD COLUMN {col} {col_type}")

            con.commit()

    def set_default(self, key: str, value: str):
        with self.conn() as con:
            con.execute(
                "INSERT OR IGNORE INTO settings(key,value,updated_at) VALUES(?,?,?)",
                (key, value, now_ts()),
            )
            con.commit()

    def get_setting(self, key: str, default: str = "") -> str:
        with self.conn() as con:
            row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str):
        with self.conn() as con:
            con.execute("""
            INSERT INTO settings(key,value,updated_at)
            VALUES(?,?,?)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at=excluded.updated_at
            """, (key, value, now_ts()))
            con.commit()

    def is_paused(self) -> bool:
        return self.get_setting("scanner_paused", "false").lower() == "true"

    def blacklist(self) -> set:
        with self.conn() as con:
            rows = con.execute("SELECT symbol FROM blacklist").fetchall()
        return {r[0] for r in rows}

    def add_blacklist(self, symbol: str, reason: str = ""):
        with self.conn() as con:
            con.execute("""
            INSERT OR REPLACE INTO blacklist(symbol,reason,created_at)
            VALUES(?,?,?)
            """, (norm_symbol(symbol), reason, now_ts()))
            con.commit()

    def remove_blacklist(self, symbol: str):
        with self.conn() as con:
            con.execute("DELETE FROM blacklist WHERE symbol=?", (norm_symbol(symbol),))
            con.commit()

    def insert_scan(self, row: Dict[str, Any]):
        with self.conn() as con:
            con.execute("""
            INSERT INTO scan_results (
                ts,
                symbol,
                status,
                signal_level,
                arb_direction,

                current_funding_rate,
                avg_funding_rate_7d,
                std_funding_rate_7d,
                positive_ratio_7d,
                recent_avg_funding_rate,

                apy,
                gross_apy,
                net_apy,
                entry_cost_rate,
                roundtrip_cost_rate,
                daily_funding_yield,
                daily_cost_drag,
                expected_hold_days,

                payback_days,
                basis_rate,
                spot_slippage,
                futures_slippage,
                total_slippage,

                quote_volume,
                open_interest_notional,
                mark_price,
                fail_reason
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                row.get("ts", now_ts()),
                row.get("symbol"),
                row.get("status"),
                row.get("signal_level"),
                row.get("arb_direction", "FORWARD"),

                row.get("current_funding_rate"),
                row.get("avg_funding_rate_7d"),
                row.get("std_funding_rate_7d"),
                row.get("positive_ratio_7d"),
                row.get("recent_avg_funding_rate"),

                row.get("apy"),
                row.get("gross_apy"),
                row.get("net_apy"),
                row.get("entry_cost_rate"),
                row.get("roundtrip_cost_rate"),
                row.get("daily_funding_yield"),
                row.get("daily_cost_drag"),
                row.get("expected_hold_days"),

                row.get("payback_days"),
                row.get("basis_rate"),
                row.get("spot_slippage"),
                row.get("futures_slippage"),
                row.get("total_slippage"),

                row.get("quote_volume"),
                row.get("open_interest_notional"),
                row.get("mark_price"),
                row.get("fail_reason"),
            ))
            con.commit()

    def should_alert(self, key: str) -> bool:
        with self.conn() as con:
            row = con.execute("SELECT last_sent_ts FROM alerts WHERE alert_key=?", (key,)).fetchone()
        if not row:
            return True
        return now_ts() - int(row[0]) >= ALERT_COOLDOWN_SECONDS

    def mark_alert(self, key: str):
        with self.conn() as con:
            con.execute("""
            INSERT INTO alerts(alert_key,last_sent_ts)
            VALUES(?,?)
            ON CONFLICT(alert_key) DO UPDATE SET last_sent_ts=excluded.last_sent_ts
            """, (key, now_ts()))
            con.commit()

    def create_intent(self, symbol: str, notional: float, reason: str) -> Dict[str, Any]:
        intent_id = str(uuid4())
        code = intent_id.split("-")[0].upper()
        row = {
            "id": intent_id,
            "symbol": norm_symbol(symbol),
            "side": "HEDGE_ENTRY",
            "notional_usdt": float(notional),
            "status": "PENDING_CONFIRM",
            "confirm_code": code,
            "reason": reason,
            "created_at": now_ts(),
            "updated_at": now_ts(),
            "dry_run": 1 if DRY_RUN else 0,
        }
        with self.conn() as con:
            con.execute("""
            INSERT INTO order_intents(
                id,symbol,side,notional_usdt,status,confirm_code,
                reason,created_at,updated_at,dry_run
            )
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """, (
                row["id"], row["symbol"], row["side"], row["notional_usdt"],
                row["status"], row["confirm_code"], row["reason"],
                row["created_at"], row["updated_at"], row["dry_run"]
            ))
            con.commit()
        return row

    def get_pending_intent(self, code: str) -> Optional[Dict[str, Any]]:
        with self.conn() as con:
            con.row_factory = sqlite3.Row
            row = con.execute("""
            SELECT * FROM order_intents
            WHERE confirm_code=? AND status='PENDING_CONFIRM'
            """, (code.upper(),)).fetchone()
        return dict(row) if row else None

    def update_intent(self, intent_id: str, status: str, result: Dict[str, Any]):
        with self.conn() as con:
            con.execute("""
            UPDATE order_intents
            SET status=?, updated_at=?, executed_at=?, result_json=?
            WHERE id=?
            """, (
                status,
                now_ts(),
                now_ts() if status in ("EXECUTED", "FAILED", "CANCELLED") else None,
                json.dumps(result, ensure_ascii=False),
                intent_id,
            ))
            con.commit()

    def insert_trade(self, intent_id: str, symbol: str, action: str, notional: float, result: Dict[str, Any]):
        with self.conn() as con:
            con.execute("""
            INSERT INTO trades(ts,intent_id,symbol,action,notional_usdt,result_json)
            VALUES(?,?,?,?,?,?)
            """, (
                now_ts(),
                intent_id,
                symbol,
                action,
                notional,
                json.dumps(result, ensure_ascii=False),
            ))
            con.commit()


# =========================
# HTTP Client
# =========================
class Http:
    def __init__(self, session: aiohttp.ClientSession, sem: asyncio.Semaphore):
        self.session = session
        self.sem = sem

    async def get(self, base: str, path: str, params: Optional[Dict[str, Any]] = None, retries: int = 3):
        url = base + path
        for i in range(1, retries + 1):
            try:
                async with self.sem:
                    async with self.session.get(url, params=params) as resp:
                        text = await resp.text()
                        if resp.status == 200:
                            return json.loads(text)
                        logger.warning(f"GET {url} status={resp.status} body={text[:300]}")
                        await asyncio.sleep(i)
            except Exception as e:
                logger.warning(f"GET error {url}: {e}")
                await asyncio.sleep(i)
        raise RuntimeError(f"GET failed: {url}")

    async def post_signed(self, base: str, path: str, params: Dict[str, Any]):
        if not BINANCE_API_KEY or not BINANCE_API_SECRET:
            raise RuntimeError("BINANCE_API_KEY 或 BINANCE_API_SECRET 未設定")

        params = dict(params)
        params["timestamp"] = int(time.time() * 1000)
        query = urlencode(params)
        sig = hmac.new(
            BINANCE_API_SECRET.encode(),
            query.encode(),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = sig

        headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
        url = base + path

        async with self.sem:
            async with self.session.post(url, params=params, headers=headers) as resp:
                text = await resp.text()
                try:
                    data = json.loads(text)
                except Exception:
                    data = {"raw": text}
                if resp.status != 200:
                    raise RuntimeError(f"POST signed failed status={resp.status} data={data}")
                return data


# =========================
# Binance Public
# =========================
class BinancePublic:
    def __init__(self, http: Http):
        self.http = http

    async def futures_exchange_info(self):
        return await self.http.get(BINANCE_FAPI_BASE_URL, "/fapi/v1/exchangeInfo")

    async def spot_exchange_info(self):
        return await self.http.get(BINANCE_SPOT_BASE_URL, "/api/v3/exchangeInfo")

    async def premium_all(self):
        return await self.http.get(BINANCE_FAPI_BASE_URL, "/fapi/v1/premiumIndex")

    async def ticker_24h_all(self):
        return await self.http.get(BINANCE_FAPI_BASE_URL, "/fapi/v1/ticker/24hr")

    async def open_interest(self, symbol: str):
        return await self.http.get(BINANCE_FAPI_BASE_URL, "/fapi/v1/openInterest", {"symbol": symbol})

    async def funding_history(self, symbol: str):
        return await self.http.get(
            BINANCE_FAPI_BASE_URL,
            "/fapi/v1/fundingRate",
            {"symbol": symbol, "limit": HISTORICAL_FUNDING_LIMIT},
        )

    async def spot_depth(self, symbol: str):
        return await self.http.get(
            BINANCE_SPOT_BASE_URL,
            "/api/v3/depth",
            {"symbol": symbol, "limit": ORDER_BOOK_LIMIT},
        )

    async def futures_depth(self, symbol: str):
        return await self.http.get(
            BINANCE_FAPI_BASE_URL,
            "/fapi/v1/depth",
            {"symbol": symbol, "limit": ORDER_BOOK_LIMIT},
        )


# =========================
# Trading Skeleton
# =========================
class Trader:
    def __init__(self, http: Http, db: RadarDB):
        self.http = http
        self.db = db

    async def execute_hedge_entry(self, intent: Dict[str, Any]) -> Dict[str, Any]:
        symbol = intent["symbol"]
        notional = float(intent["notional_usdt"])

        if not ENABLE_TRADING:
            return {
                "ok": False,
                "mode": "BLOCKED",
                "message": "ENABLE_TRADING=false，已阻擋實盤交易。",
            }

        if notional <= 0 or notional > MAX_ORDER_NOTIONAL_USDT:
            return {
                "ok": False,
                "mode": "RISK_BLOCKED",
                "message": f"下單金額不合法：{notional}",
            }

        if DRY_RUN:
            result = {
                "ok": True,
                "mode": "DRY_RUN",
                "symbol": symbol,
                "notional_usdt": notional,
                "steps": [
                    "模擬現貨 MARKET BUY",
                    "模擬設定合約槓桿",
                    "模擬合約 MARKET SELL",
                ],
            }
            self.db.insert_trade(intent["id"], symbol, "DRY_RUN_HEDGE_ENTRY", notional, result)
            return result

        return {
            "ok": False,
            "mode": "LIVE_NOT_IMPLEMENTED_SAFE_GUARD",
            "message": "安全保護：此版本不直接執行實盤下單。請先使用 DRY_RUN。",
        }


# =========================
# Market Math
# =========================
def parse_futures_symbols(info: Dict[str, Any]) -> set:
    out = set()
    for s in info.get("symbols", []):
        if (
            s.get("quoteAsset") == "USDT"
            and s.get("contractType") == "PERPETUAL"
            and s.get("status") == "TRADING"
        ):
            out.add(s.get("symbol"))
    return out


def parse_spot_symbols(info: Dict[str, Any]) -> set:
    out = set()
    for s in info.get("symbols", []):
        if (
            s.get("quoteAsset") == "USDT"
            and s.get("status") == "TRADING"
            and s.get("isSpotTradingAllowed", True)
        ):
            out.add(s.get("symbol"))
    return out


def orderbook_mid(book: Dict[str, Any]) -> Optional[float]:
    try:
        bid = float(book["bids"][0][0])
        ask = float(book["asks"][0][0])
        if bid <= 0 or ask <= 0:
            return None
        return (bid + ask) / 2
    except Exception:
        return None


def estimate_buy_slippage(book: Dict[str, Any], notional: float) -> Optional[float]:
    mid = orderbook_mid(book)
    if mid is None:
        return None

    remain = notional
    qty_sum = 0.0
    cost_sum = 0.0

    for price_s, qty_s in book.get("asks", []):
        price = float(price_s)
        qty = float(qty_s)
        cost = price * qty

        if cost >= remain:
            q = remain / price
            qty_sum += q
            cost_sum += remain
            remain = 0
            break

        qty_sum += qty
        cost_sum += cost
        remain -= cost

    if remain > 0 or qty_sum <= 0:
        return None

    avg = cost_sum / qty_sum
    return max(0.0, (avg - mid) / mid)


def estimate_sell_slippage(book: Dict[str, Any], notional: float) -> Optional[float]:
    mid = orderbook_mid(book)
    if mid is None:
        return None

    remain = notional
    qty_sum = 0.0
    proceeds_sum = 0.0

    for price_s, qty_s in book.get("bids", []):
        price = float(price_s)
        qty = float(qty_s)
        val = price * qty

        if val >= remain:
            q = remain / price
            qty_sum += q
            proceeds_sum += remain
            remain = 0
            break

        qty_sum += qty
        proceeds_sum += val
        remain -= val

    if remain > 0 or qty_sum <= 0:
        return None

    avg = proceeds_sum / qty_sum
    return max(0.0, (mid - avg) / mid)


def analyze_history(history: List[Dict[str, Any]], current_rate: float) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    回傳：
    1. stats：只要歷史資料足夠，就會回傳 funding 統計資料
    2. fail_reason：若穩定性不通過，回傳原因；若通過，回傳空字串

    這樣即使 FAIL，/why 也可以顯示：
    - 7日平均資金費率
    - 正費率比例
    - Funding 標準差
    - 近期平均資金費率
    - 毛年化與真實淨年化
    """
    if len(history) < HISTORICAL_FUNDING_LIMIT:
        return None, "歷史資料不足"

    history = sorted(history, key=lambda x: int(x.get("fundingTime", 0)))
    recent = history[-HISTORICAL_FUNDING_LIMIT:]

    df = pd.DataFrame(recent)
    if "fundingRate" not in df.columns:
        return None, "缺少 fundingRate"

    df["fundingRate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
    df = df.dropna(subset=["fundingRate"])

    if len(df) < HISTORICAL_FUNDING_LIMIT:
        return None, "有效 fundingRate 不足"

    avg = float(df["fundingRate"].mean())
    std = float(df["fundingRate"].std(ddof=0))
    pos_ratio = float((df["fundingRate"] > 0).mean())
    max_abs = float(df["fundingRate"].abs().max())
    recent_avg = float(df["fundingRate"].tail(RECENT_FUNDING_CHECK_PERIODS).mean())

    stats = {
        "avg_funding_rate_7d": avg,
        "std_funding_rate_7d": std,
        "positive_ratio_7d": pos_ratio,
        "recent_avg_funding_rate": recent_avg,
    }

    fail_reasons = []

    if pos_ratio < POSITIVE_RATIO_THRESHOLD:
        fail_reasons.append(f"正費率比例不足：{pos_ratio:.2f}")

    if avg < AVG_FUNDING_RATE_THRESHOLD:
        fail_reasons.append(f"7日平均不足：{fmt_pct(avg)}")

    if std > MAX_FUNDING_STD_7D:
        fail_reasons.append(f"標準差過高：{fmt_pct(std)}")

    if avg > 0 and (std / avg) > MAX_STD_TO_AVG_RATIO:
        fail_reasons.append(f"標準差相對平均過高：{std / avg:.2f}")

    if max_abs > MAX_ABS_HISTORICAL_FUNDING_RATE:
        fail_reasons.append(f"歷史異常值過高：{fmt_pct(max_abs)}")

    if recent_avg < avg * RECENT_AVG_MIN_RATIO_TO_7D:
        fail_reasons.append("近期費率衰退")

    if current_rate < avg * CURRENT_MIN_RATIO_TO_7D:
        fail_reasons.append("當前費率低於7日平均過多")

    return stats, "；".join(fail_reasons)

def calc_daily_funding_yield(avg_funding_rate: float) -> float:
    return safe_float(avg_funding_rate, 0.0) * 3


def calc_entry_cost_rate(spot_slip: float, fut_slip: float) -> float:
    spot_slip = abs(safe_float(spot_slip, 0.0))
    fut_slip = abs(safe_float(fut_slip, 0.0))

    fee_rate = SPOT_TAKER_FEE_RATE + FUTURES_TAKER_FEE_RATE
    slippage_rate = spot_slip + fut_slip

    return fee_rate + slippage_rate


def calc_roundtrip_cost_rate(spot_slip: float, fut_slip: float) -> float:
    entry_cost = calc_entry_cost_rate(spot_slip, fut_slip)

    if INCLUDE_EXIT_COST:
        return entry_cost * 2

    return entry_cost


def calc_forward_net_metrics(avg_rate: float, spot_slip: float, fut_slip: float) -> Dict[str, float]:
    avg_rate = safe_float(avg_rate, 0.0)

    gross_apy = avg_rate * 3 * 365
    entry_cost_rate = calc_entry_cost_rate(spot_slip, fut_slip)
    roundtrip_cost_rate = calc_roundtrip_cost_rate(spot_slip, fut_slip)

    daily_funding_yield = calc_daily_funding_yield(avg_rate)
    expected_hold_days = max(safe_float(EXPECTED_HOLD_DAYS, 1.0), 1.0)
    daily_cost_drag = roundtrip_cost_rate / expected_hold_days

    if daily_funding_yield <= 0:
        payback_days = 999999.0
    else:
        payback_days = roundtrip_cost_rate / daily_funding_yield

    net_daily_yield = daily_funding_yield - daily_cost_drag
    net_apy = net_daily_yield * 365

    return {
        "apy": gross_apy,
        "gross_apy": gross_apy,
        "net_apy": net_apy,
        "entry_cost_rate": entry_cost_rate,
        "roundtrip_cost_rate": roundtrip_cost_rate,
        "daily_funding_yield": daily_funding_yield,
        "daily_cost_drag": daily_cost_drag,
        "expected_hold_days": expected_hold_days,
        "payback_days": payback_days,
    }


def classify(payback: float, net_apy: Optional[float] = None) -> str:
    net_apy = safe_float(net_apy, 0.0)

    if net_apy >= TARGET_NET_APY * 1.5 and payback <= STRONG_SIGNAL_PAYBACK_DAYS:
        return "🔥 強正向淨利訊號"

    if net_apy >= TARGET_NET_APY and payback <= MAX_PAYBACK_DAYS:
        return "✅ 正向淨利通過"

    return "❌ 不符合"


# =========================
# Telegram
# =========================
class Telegram:
    def __init__(self, session: aiohttp.ClientSession, db: RadarDB, trader: Trader):
        self.session = session
        self.db = db
        self.trader = trader
        self.offset = 0

    async def send(self, text: str) -> bool:
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            logger.warning("Telegram env 未設定，略過發送")
            return False

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            async with self.session.post(url, json=payload) as resp:
                body = await resp.text()
                if resp.status == 200:
                    return True
                logger.error(f"Telegram send failed: {body}")
                return False
        except Exception as e:
            logger.error(f"Telegram send error: {e}")
            return False

    def h(self, x: Any) -> str:
        if x is None:
            return ""
        return html.escape(str(x))

    def row_get(self, row: sqlite3.Row, key: str, default=None):
        try:
            if key in row.keys():
                return row[key]
        except Exception:
            pass
        return default

    def fmt_time(self, ts_value) -> str:
        try:
            return datetime.fromtimestamp(int(ts_value), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            return "N/A"

    def fmt_days(self, x) -> str:
        try:
            if x is None:
                return "N/A"
            return f"{float(x):.2f} 天"
        except Exception:
            return "N/A"

    def fmt_money(self, x) -> str:
        try:
            if x is None:
                return "N/A"
            return f"{float(x):,.0f}"
        except Exception:
            return "N/A"

    def fmt_price(self, x) -> str:
        try:
            if x is None:
                return "N/A"
            return f"{float(x):,.8f}"
        except Exception:
            return "N/A"

    def status_text(self, status: str) -> str:
        text = str(status).upper()

        if text == "PASS":
            return "✅ 通過"
        if text == "WATCH":
            return "⚠️ 觀察"
        if text == "FAIL":
            return "❌ 未通過"

        return self.h(status)

    def target_text(self, apy) -> str:
        if apy is None:
            return "無法判斷，因為毛年化尚未計算"

        try:
            apy_value = float(apy)
            gap = apy_value - TARGET_APY

            if gap >= 0:
                return f"🔥 毛年化已達標，超過 {gap * 100:.2f}%"
            return f"毛年化尚未達標，還差 {abs(gap) * 100:.2f}%"
        except Exception:
            return "無法判斷"

    def target_net_text(self, net_apy) -> str:
        if net_apy is None:
            return "無法判斷，因為真實淨年化尚未計算"

        try:
            net_apy_value = float(net_apy)
            gap = net_apy_value - TARGET_NET_APY

            if gap >= 0:
                return f"🔥 淨年化已達標，超過 {gap * 100:.2f}%"
            return f"淨年化尚未達標，還差 {abs(gap) * 100:.2f}%"
        except Exception:
            return "無法判斷"

    async def poll_loop(self):
        if not TELEGRAM_BOT_TOKEN:
            logger.warning("Telegram token 未設定，不啟動指令監聽")
            return

        logger.info("Telegram 指令監聽啟動")

        while True:
            try:
                await self.poll_once()
            except Exception as e:
                logger.exception(f"Telegram poll error: {e}")
            await asyncio.sleep(2)

    async def poll_once(self):
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        params = {"timeout": 10, "offset": self.offset}

        async with self.session.get(url, params=params) as resp:
            data = await resp.json()

        if not data.get("ok"):
            return

        for upd in data.get("result", []):
            self.offset = upd["update_id"] + 1
            msg = upd.get("message") or {}
            chat_id = str(msg.get("chat", {}).get("id", ""))

            if TELEGRAM_CHAT_ID and chat_id != TELEGRAM_CHAT_ID:
                continue

            text = msg.get("text", "").strip()
            if text:
                await self.handle(text)

    async def handle(self, text: str):
        parts = text.split()
        if not parts:
            return

        cmd = parts[0].lower()

        try:
            if cmd == "/help":
                await self.send(self.help_text())

            elif cmd == "/status":
                await self.cmd_status()

            elif cmd == "/pause":
                self.db.set_setting("scanner_paused", "true")
                await self.send("⏸ <b>已暫停掃描</b>")

            elif cmd == "/resume":
                self.db.set_setting("scanner_paused", "false")
                await self.send("▶️ <b>已恢復掃描</b>")

            elif cmd == "/top":
                await self.cmd_top()

            elif cmd == "/top16":
                await self.cmd_top16()

            elif cmd == "/topnet":
                await self.cmd_topnet()

            elif cmd == "/why" and len(parts) >= 2:
                symbol = norm_symbol(parts[1])
                await self.cmd_why(symbol)

            elif cmd == "/blacklist_add" and len(parts) >= 2:
                symbol = norm_symbol(parts[1])
                reason = " ".join(parts[2:]) if len(parts) > 2 else "telegram"
                self.db.add_blacklist(symbol, reason)
                await self.send(
                    f"🚫 <b>已加入黑名單</b>\n\n"
                    f"交易對：<b>{self.h(symbol)}</b>\n"
                    f"原因：<code>{self.h(reason)}</code>"
                )

            elif cmd == "/blacklist_remove" and len(parts) >= 2:
                symbol = norm_symbol(parts[1])
                self.db.remove_blacklist(symbol)
                await self.send(
                    f"✅ <b>已移除黑名單</b>\n\n"
                    f"交易對：<b>{self.h(symbol)}</b>"
                )

            elif cmd == "/order" and len(parts) >= 2:
                symbol = norm_symbol(parts[1])
                notional = float(parts[2]) if len(parts) >= 3 else DEFAULT_ORDER_NOTIONAL_USDT
                await self.cmd_order(symbol, notional)

            elif cmd == "/confirm" and len(parts) >= 2:
                await self.cmd_confirm(parts[1].upper())

            elif cmd == "/cancel" and len(parts) >= 2:
                await self.cmd_cancel(parts[1].upper())

            else:
                await self.send(
                    "未知指令，請輸入 /help 查看可用指令。\n\n"
                    "常用指令：\n"
                    "<code>/top</code>\n"
                    "<code>/topnet</code>\n"
                    "<code>/top16</code>\n"
                    "<code>/why ETHUSDT</code>"
                )

        except Exception as e:
            logger.exception(f"Telegram command error: {e}")
            await self.send(f"❌ <b>指令錯誤</b>\n\n<code>{self.h(e)}</code>")

    def help_text(self) -> str:
        return (
            "🤖 <b>Funding Radar 指令說明</b>\n\n"

            "📊 <b>監控查詢</b>\n"
            "/status - 查看系統狀態\n"
            "/top - 查看目前最新真實淨利通過訊號\n"
            "/topnet - 查看真實淨年化達標訊號\n"
            "/top16 - 查看達到目標毛年化的訊號\n"
            "/why SYMBOL - 查看某交易對為什麼通過或未通過\n\n"

            "⏸ <b>掃描控制</b>\n"
            "/pause - 暫停掃描\n"
            "/resume - 恢復掃描\n\n"

            "🚫 <b>黑名單</b>\n"
            "/blacklist_add SYMBOL reason - 加入黑名單\n"
            "/blacklist_remove SYMBOL - 移除黑名單\n\n"

            "📝 <b>半自動下單</b>\n"
            "/order SYMBOL amount - 建立半自動下單意圖\n"
            "/confirm CODE - 確認下單意圖\n"
            "/cancel CODE - 取消下單意圖\n\n"

            "📌 <b>範例</b>\n"
            "<code>/top</code>\n"
            "<code>/topnet</code>\n"
            "<code>/top16</code>\n"
            "<code>/why ETHUSDT</code>\n"
            "<code>/order DOGEUSDT 50</code>\n\n"

            f"🎯 目標毛年化：<b>{TARGET_APY * 100:.2f}%</b>\n"
            f"💰 目標真實淨年化：<b>{TARGET_NET_APY * 100:.2f}%</b>\n"
            f"⏳ 預估持倉天數：<b>{EXPECTED_HOLD_DAYS:.0f} 天</b>\n"
            f"🔁 是否計入出場成本：<b>{INCLUDE_EXIT_COST}</b>\n\n"

            "⚠️ 訊號僅供監控，不代表投資建議。"
        )

    async def cmd_status(self):
        scanner_paused = self.db.get_setting("scanner_paused", "false")
        telegram_paused = self.db.get_setting("telegram_paused", "false")

        with self.db.conn() as con:
            scan_count = con.execute("SELECT COUNT(*) FROM scan_results").fetchone()[0]
            latest_ts_row = con.execute("SELECT MAX(ts) FROM scan_results").fetchone()
            latest_ts = latest_ts_row[0] if latest_ts_row else None

            pass_count = con.execute("""
                SELECT COUNT(*)
                FROM scan_results
                WHERE status='PASS'
            """).fetchone()[0]

            net_pass_count = con.execute("""
                SELECT COUNT(*)
                FROM scan_results
                WHERE status='PASS'
                  AND net_apy IS NOT NULL
                  AND net_apy >= ?
            """, (TARGET_NET_APY,)).fetchone()[0]

            watch_count = con.execute("""
                SELECT COUNT(*)
                FROM scan_results
                WHERE status='WATCH'
            """).fetchone()[0]

        await self.send(
            "📡 <b>Funding Radar 系統狀態</b>\n\n"
            f"掃描狀態：<code>{'暫停' if scanner_paused.lower() == 'true' else '運行中'}</code>\n"
            f"Telegram 狀態：<code>{'暫停' if telegram_paused.lower() == 'true' else '運行中'}</code>\n"
            f"是否允許實盤交易：<code>{ENABLE_TRADING}</code>\n"
            f"是否模擬交易 DRY_RUN：<code>{DRY_RUN}</code>\n\n"

            f"策略方向：<b>正向套利｜買現貨 + 空合約</b>\n"
            f"目標毛年化：<b>{TARGET_APY * 100:.2f}%</b>\n"
            f"目標真實淨年化：<b>{TARGET_NET_APY * 100:.2f}%</b>\n"
            f"預估持倉天數：<b>{EXPECTED_HOLD_DAYS:.0f} 天</b>\n"
            f"完整進出場成本：<code>{INCLUDE_EXIT_COST}</code>\n"
            f"資料庫路徑：<code>{self.h(DB_PATH)}</code>\n\n"

            f"scan_results 總筆數：<b>{scan_count}</b>\n"
            f"歷史 PASS 筆數：<b>{pass_count}</b>\n"
            f"真實淨利達標 PASS 筆數：<b>{net_pass_count}</b>\n"
            f"歷史 WATCH 筆數：<b>{watch_count}</b>\n"
            f"最後更新：<code>{self.fmt_time(latest_ts)}</code>"
        )

    async def cmd_top(self):
        lookback_seconds = 24 * 60 * 60
        since_ts = now_ts() - lookback_seconds

        with self.db.conn() as con:
            con.row_factory = sqlite3.Row
            rows = con.execute("""
            WITH ranked AS (
                SELECT
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY symbol
                        ORDER BY ts DESC, id DESC
                    ) AS rn
                FROM scan_results
                WHERE ts >= ?
            )
            SELECT *
            FROM ranked
            WHERE rn = 1
              AND UPPER(status) = 'PASS'
              AND COALESCE(arb_direction, 'FORWARD') = 'FORWARD'
              AND net_apy IS NOT NULL
              AND net_apy >= ?
            ORDER BY
                COALESCE(net_apy, 0) DESC,
                COALESCE(payback_days, 999999) ASC
            LIMIT 10
            """, (since_ts, TARGET_NET_APY)).fetchall()

        if not rows:
            await self.send(
                "目前沒有真實淨利通過訊號。\n\n"
                "說明：/top 只顯示最近 24 小時內，"
                "每個交易對最新一筆仍為 PASS，且真實淨年化達標的正向套利標的。\n\n"
                "你也可以使用：\n"
                "<code>/top16</code> 查看毛年化達標標的\n"
                "<code>/why ETHUSDT</code> 查看單一交易對診斷"
            )
            return

        lines = [
            "🏆 <b>最新真實淨利通過訊號</b>",
            "<code>正向套利：買現貨 + 空合約｜最近 24 小時</code>",
            f"目標真實淨年化：<b>{TARGET_NET_APY * 100:.2f}%</b>",
        ]

        for i, r in enumerate(rows, 1):
            symbol = self.row_get(r, "symbol", "")
            signal_level = self.row_get(r, "signal_level", "") or "✅ 正向淨利通過"

            lines.append(
                f"\n#{i} <b>{self.h(symbol)}</b>｜{self.h(signal_level)}\n"
                f"時間：<code>{self.fmt_time(self.row_get(r, 'ts'))}</code>\n"
                f"方向：<b>買現貨 + 空合約</b>\n"
                f"當前資金費率：{fmt_pct(self.row_get(r, 'current_funding_rate'))}\n"
                f"7日平均資金費率：{fmt_pct(self.row_get(r, 'avg_funding_rate_7d'))}\n"
                f"毛年化：<b>{fmt_pct(self.row_get(r, 'gross_apy') or self.row_get(r, 'apy'), 2)}</b>\n"
                f"真實淨年化：<b>{fmt_pct(self.row_get(r, 'net_apy'), 2)}</b>\n"
                f"淨利目標：<b>{self.target_net_text(self.row_get(r, 'net_apy'))}</b>\n"
                f"回本天數：{self.fmt_days(self.row_get(r, 'payback_days'))}\n"
                f"完整進出場成本：{fmt_pct(self.row_get(r, 'roundtrip_cost_rate'), 4)}\n"
                f"每日 funding 收益：{fmt_pct(self.row_get(r, 'daily_funding_yield'), 4)}\n"
                f"每日成本攤提：{fmt_pct(self.row_get(r, 'daily_cost_drag'), 4)}\n"
                f"Basis：{fmt_pct(self.row_get(r, 'basis_rate'))}\n"
                f"總滑點：{fmt_pct(self.row_get(r, 'total_slippage'))}\n"
                f"24H 成交額：<code>{self.fmt_money(self.row_get(r, 'quote_volume'))}</code>\n"
                f"合約 OI 名目價值：<code>{self.fmt_money(self.row_get(r, 'open_interest_notional'))}</code>\n"
                f"半自動：<code>/order {self.h(symbol)} {DEFAULT_ORDER_NOTIONAL_USDT}</code>\n"
                f"原因查詢：<code>/why {self.h(symbol)}</code>"
            )

        await self.send("\n".join(lines))

    async def cmd_topnet(self):
        await self.cmd_top()

    async def cmd_top16(self):
        lookback_seconds = 24 * 60 * 60
        since_ts = now_ts() - lookback_seconds

        with self.db.conn() as con:
            con.row_factory = sqlite3.Row
            rows = con.execute("""
            WITH ranked AS (
                SELECT
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY symbol
                        ORDER BY ts DESC, id DESC
                    ) AS rn
                FROM scan_results
                WHERE ts >= ?
            )
            SELECT *
            FROM ranked
            WHERE rn = 1
              AND COALESCE(gross_apy, apy) IS NOT NULL
              AND COALESCE(gross_apy, apy) >= ?
            ORDER BY
                COALESCE(gross_apy, apy, 0) DESC,
                COALESCE(payback_days, 999999) ASC
            LIMIT 10
            """, (since_ts, TARGET_APY)).fetchall()

        if not rows:
            await self.send(
                "🔥 <b>目標毛年化訊號</b>\n\n"
                f"目前沒有交易對達到目標毛年化 <b>{TARGET_APY * 100:.2f}%</b>。\n\n"
                "你可以用：\n"
                "<code>/top</code> 查看真實淨利通過訊號\n"
                "<code>/why ETHUSDT</code> 查單一交易對原因"
            )
            return

        lines = [
            "🔥 <b>目標毛年化訊號</b>",
            "<code>每個交易對只顯示最新一筆｜最近 24 小時</code>",
            f"目標毛年化：<b>{TARGET_APY * 100:.2f}%</b>",
            "提醒：毛年化達標不代表真實淨年化達標。",
        ]

        for i, r in enumerate(rows, 1):
            symbol = self.row_get(r, "symbol", "")
            gross_apy = self.row_get(r, "gross_apy") or self.row_get(r, "apy")
            net_apy = self.row_get(r, "net_apy")

            gap_text = "N/A"
            try:
                gap = float(gross_apy) - TARGET_APY
                gap_text = f"+{gap * 100:.2f}%" if gap >= 0 else f"{gap * 100:.2f}%"
            except Exception:
                pass

            lines.append(
                f"\n#{i} <b>{self.h(symbol)}</b>｜🔥 毛年化達標\n"
                f"時間：<code>{self.fmt_time(self.row_get(r, 'ts'))}</code>\n"
                f"毛年化：<b>{fmt_pct(gross_apy, 2)}</b>\n"
                f"真實淨年化：<b>{fmt_pct(net_apy, 2)}</b>\n"
                f"超過毛目標：<b>{gap_text}</b>\n"
                f"當前資金費率：{fmt_pct(self.row_get(r, 'current_funding_rate'))}\n"
                f"7日平均資金費率：{fmt_pct(self.row_get(r, 'avg_funding_rate_7d'))}\n"
                f"回本天數：{self.fmt_days(self.row_get(r, 'payback_days'))}\n"
                f"完整進出場成本：{fmt_pct(self.row_get(r, 'roundtrip_cost_rate'), 4)}\n"
                f"總滑點：{fmt_pct(self.row_get(r, 'total_slippage'))}\n"
                f"原因查詢：<code>/why {self.h(symbol)}</code>"
            )

        await self.send("\n".join(lines))

    async def cmd_why(self, symbol: str):
        with self.db.conn() as con:
            con.row_factory = sqlite3.Row
            row = con.execute("""
            SELECT *
            FROM scan_results
            WHERE symbol=?
            ORDER BY ts DESC, id DESC
            LIMIT 1
            """, (symbol,)).fetchone()

        if not row:
            await self.send(
                f"🔎 <b>{self.h(symbol)} 訊號診斷</b>\n\n"
                "查無這個交易對的掃描資料。\n\n"
                "可能原因：\n"
                "1. 這個交易對尚未被掃描到\n"
                "2. 當前資金費率低於初步門檻\n"
                "3. 現貨或合約市場不符合系統條件\n"
                "4. 剛部署完成，資料還沒累積"
            )
            return

        status = str(self.row_get(row, "status", "")).upper()
        gross_apy = self.row_get(row, "gross_apy") or self.row_get(row, "apy")
        net_apy = self.row_get(row, "net_apy")
        avg_rate = self.row_get(row, "avg_funding_rate_7d")
        current_rate = self.row_get(row, "current_funding_rate")
        fail_reason = self.row_get(row, "fail_reason", "")

        explanation = []

        if status == "PASS":
            explanation.append("此交易對目前通過正向套利真實淨利篩選。")
        elif status == "WATCH":
            explanation.append("此交易對目前列為觀察，通常代表當前資金費率偏高，但歷史穩定性或其他條件尚未完全通過。")
        elif status == "FAIL":
            explanation.append("此交易對目前未通過正向套利篩選。")
        else:
            explanation.append("此交易對目前狀態不明，請檢查資料庫欄位。")

        if gross_apy is None:
            explanation.append("毛年化收益率沒有計算出來，通常代表它在計算 APY 之前就已被某個條件擋下。")

        if net_apy is None:
            explanation.append("真實淨年化沒有計算出來，通常代表它在成本計算前已被擋下。")

        if avg_rate is None:
            explanation.append("7日平均資金費率為空，可能是歷史 funding 資料不足，或尚未進入穩定性計算階段。")

        if fail_reason:
            explanation.append(f"未通過原因：{fail_reason}")

        msg = (
            f"🔎 <b>{self.h(symbol)} 訊號診斷</b>\n\n"
            f"最新狀態：<b>{self.status_text(status)}</b>\n"
            f"時間：<code>{self.fmt_time(self.row_get(row, 'ts'))}</code>\n"
            f"方向：<b>正向套利｜買現貨 + 空合約</b>\n\n"

            f"當前資金費率：{fmt_pct(current_rate)}\n"
            f"7日平均資金費率：{fmt_pct(avg_rate)}\n"
            f"正費率比例：{fmt_pct(self.row_get(row, 'positive_ratio_7d'), 2)}\n"
            f"Funding 標準差：{fmt_pct(self.row_get(row, 'std_funding_rate_7d'))}\n"
            f"近期平均資金費率：{fmt_pct(self.row_get(row, 'recent_avg_funding_rate'))}\n\n"

            f"毛年化：<b>{fmt_pct(gross_apy, 2)}</b>\n"
            f"真實淨年化：<b>{fmt_pct(net_apy, 2)}</b>\n"
            f"目標毛年化：<b>{TARGET_APY * 100:.2f}%</b>\n"
            f"目標真實淨年化：<b>{TARGET_NET_APY * 100:.2f}%</b>\n"
            f"毛年化狀態：<b>{self.target_text(gross_apy)}</b>\n"
            f"淨年化狀態：<b>{self.target_net_text(net_apy)}</b>\n\n"

            f"回本天數：{self.fmt_days(self.row_get(row, 'payback_days'))}\n"
            f"進場成本率：{fmt_pct(self.row_get(row, 'entry_cost_rate'), 4)}\n"
            f"完整進出場成本率：{fmt_pct(self.row_get(row, 'roundtrip_cost_rate'), 4)}\n"
            f"每日 funding 收益率：{fmt_pct(self.row_get(row, 'daily_funding_yield'), 4)}\n"
            f"每日成本攤提率：{fmt_pct(self.row_get(row, 'daily_cost_drag'), 4)}\n"
            f"預估持倉天數：<code>{self.row_get(row, 'expected_hold_days')}</code>\n\n"

            f"現貨合約價差：{fmt_pct(self.row_get(row, 'basis_rate'))}\n"
            f"現貨滑點：{fmt_pct(self.row_get(row, 'spot_slippage'))}\n"
            f"合約滑點：{fmt_pct(self.row_get(row, 'futures_slippage'))}\n"
            f"總滑點：{fmt_pct(self.row_get(row, 'total_slippage'))}\n"
            f"標記價格：<code>{self.fmt_price(self.row_get(row, 'mark_price'))}</code>\n"
            f"24H 成交額：<code>{self.fmt_money(self.row_get(row, 'quote_volume'))}</code>\n"
            f"合約未平倉名目價值：<code>{self.fmt_money(self.row_get(row, 'open_interest_notional'))}</code>\n\n"

            "📌 <b>診斷說明</b>\n"
            + "\n".join([f"- {self.h(x)}" for x in explanation])
        )

        await self.send(msg)

    async def cmd_order(self, symbol: str, notional: float):
        if notional <= 0 or notional > MAX_ORDER_NOTIONAL_USDT:
            await self.send(
                f"❌ <b>金額不合法</b>\n\n"
                f"下單金額：<code>{notional}</code> USDT\n"
                f"系統上限：<code>{MAX_ORDER_NOTIONAL_USDT}</code> USDT"
            )
            return

        intent = self.db.create_intent(symbol, notional, "telegram manual order")

        await self.send(
            "📝 <b>已建立半自動下單意圖</b>\n\n"
            f"交易對：<b>{self.h(intent['symbol'])}</b>\n"
            f"策略方向：<code>買現貨 + 空合約</code>\n"
            f"名目金額：<code>{notional:.2f} USDT</code>\n"
            f"是否允許實盤交易：<code>{ENABLE_TRADING}</code>\n"
            f"是否模擬交易 DRY_RUN：<code>{DRY_RUN}</code>\n\n"
            f"確認碼：<code>{self.h(intent['confirm_code'])}</code>\n\n"
            f"確認執行：<code>/confirm {self.h(intent['confirm_code'])}</code>\n"
            f"取消意圖：<code>/cancel {self.h(intent['confirm_code'])}</code>\n\n"
            "⚠️ 請確認 API 權限、倉位風險、現貨與合約數量是否能對沖。"
        )

    async def cmd_confirm(self, code: str):
        intent = self.db.get_pending_intent(code)
        if not intent:
            await self.send("❌ 找不到待確認下單意圖。")
            return

        result = await self.trader.execute_hedge_entry(intent)
        status = "EXECUTED" if result.get("ok") else "FAILED"
        self.db.update_intent(intent["id"], status, result)

        await self.send(
            "✅ <b>下單意圖處理完成</b>\n\n"
            f"交易對：<b>{self.h(intent['symbol'])}</b>\n"
            f"狀態：<code>{self.h(status)}</code>\n"
            f"模式：<code>{self.h(result.get('mode'))}</code>\n"
            f"訊息：<code>{self.h(result.get('message', ''))}</code>"
        )

    async def cmd_cancel(self, code: str):
        intent = self.db.get_pending_intent(code)
        if not intent:
            await self.send("❌ 找不到待取消下單意圖。")
            return

        self.db.update_intent(intent["id"], "CANCELLED", {"cancelled_by": "telegram"})

        await self.send(
            "✅ <b>已取消下單意圖</b>\n\n"
            f"確認碼：<code>{self.h(code)}</code>"
        )


# =========================
# Scanner
# =========================
class Scanner:
    def __init__(self, db: RadarDB, api: BinancePublic, tg: Telegram):
        self.db = db
        self.api = api
        self.tg = tg

    async def loop(self):
        while True:
            try:
                if self.db.is_paused():
                    logger.info("Scanner paused")
                else:
                    await self.run_once()
            except Exception as e:
                logger.exception(f"scanner error: {e}")

            await asyncio.sleep(SCAN_INTERVAL_SECONDS)

        async def run_once(self):
        logger.info("開始新一輪掃描")

        blacklist = self.db.blacklist()

        f_info, s_info, premium_all, ticker_all = await asyncio.gather(
            self.api.futures_exchange_info(),
            self.api.spot_exchange_info(),
            self.api.premium_all(),
            self.api.ticker_24h_all(),
        )

        f_symbols = parse_futures_symbols(f_info)
        s_symbols = parse_spot_symbols(s_info)

        ticker_map = {x.get("symbol"): x for x in ticker_all}

        candidates = []

        for p in premium_all:
            symbol = p.get("symbol")

            if symbol not in f_symbols or symbol not in s_symbols:
                continue

            if symbol in blacklist:
                continue

            ticker = ticker_map.get(symbol)
            if not ticker:
                continue

            force_track = symbol in ALWAYS_TRACK_SYMBOLS

            try:
                mark_price = float(p.get("markPrice", 0))
                current_rate = float(p.get("lastFundingRate", 0))
                quote_vol = float(ticker.get("quoteVolume", 0))
            except Exception:
                continue

            if mark_price <= 0:
                continue

            # 一般標的：必須通過初步 funding 門檻
            # ALWAYS_TRACK_SYMBOLS：即使 funding 低，也要記錄最新診斷
            if not force_track and current_rate <= CURRENT_FUNDING_RATE_THRESHOLD:
                continue

            # 一般標的：必須通過成交量門檻
            # ALWAYS_TRACK_SYMBOLS：即使成交量不足，也要記錄原因
            if not force_track and quote_vol < MIN_24H_QUOTE_VOLUME_USDT:
                continue

            candidates.append({
                "symbol": symbol,
                "mark_price": mark_price,
                "current_funding_rate": current_rate,
                "quote_volume": quote_vol,
                "force_track": force_track,
            })

        logger.info(
            f"初步候選數量：{len(candidates)} | "
            f"固定追蹤：{','.join(sorted(ALWAYS_TRACK_SYMBOLS))}"
        )

        results = await asyncio.gather(
            *[self.analyze(c) for c in candidates],
            return_exceptions=True,
        )

        passed = []
        watched = []

        for r in results:
            if isinstance(r, Exception):
                logger.warning(f"analyze exception: {r}")
                continue

            if not r:
                continue

            self.db.insert_scan(r)

            if r["status"] == "PASS":
                passed.append(r)
            elif r["status"] == "WATCH":
                watched.append(r)

        passed.sort(
            key=lambda x: (
                -(x.get("net_apy") or 0),
                x.get("payback_days") or 999999,
            )
        )

        watched.sort(
            key=lambda x: x.get("current_funding_rate") or 0,
            reverse=True,
        )

        await self.alert(passed[:10], watched[:5])
        logger.info(f"掃描完成 PASS={len(passed)} WATCH={len(watched)}")

        async def analyze(self, c: Dict[str, Any]) -> Dict[str, Any]:
        symbol = c["symbol"]
        force_track = bool(c.get("force_track", False))

        row = {
            "ts": now_ts(),
            "symbol": symbol,
            "arb_direction": "FORWARD",
            "current_funding_rate": c["current_funding_rate"],
            "quote_volume": c["quote_volume"],
            "mark_price": c["mark_price"],
        }

        fail_reasons = []

        try:
            current_rate = c["current_funding_rate"]

            if current_rate <= 0:
                fail_reasons.append(f"非正資金費率，不適合正向套利：{current_rate:.6f}")

            if current_rate < CURRENT_FUNDING_RATE_THRESHOLD:
                fail_reasons.append(f"當前資金費率不足：{current_rate:.6f}")

            if c["quote_volume"] < MIN_24H_QUOTE_VOLUME_USDT:
                fail_reasons.append(f"24H 成交額不足：{c['quote_volume']:.0f}")

            oi, spot_book, fut_book, hist = await asyncio.gather(
                self.api.open_interest(symbol),
                self.api.spot_depth(symbol),
                self.api.futures_depth(symbol),
                self.api.funding_history(symbol),
            )

            oi_notional = float(oi.get("openInterest", 0)) * c["mark_price"]
            row["open_interest_notional"] = oi_notional

            if oi_notional < MIN_OPEN_INTEREST_NOTIONAL_USDT:
                fail_reasons.append(f"合約未平倉名目價值不足：{oi_notional:.0f}")

            spot_mid = orderbook_mid(spot_book)
            fut_mid = orderbook_mid(fut_book)

            if not spot_mid or not fut_mid:
                row.update({
                    "status": "FAIL",
                    "fail_reason": "order book mid 無效",
                })
                return row

            basis = fut_mid / spot_mid - 1
            row["basis_rate"] = basis

            if abs(basis) > MAX_ABS_BASIS_RATE:
                fail_reasons.append(f"Basis 過大：{fmt_pct(basis)}")

            if USE_DYNAMIC_SLIPPAGE_COST:
                # 正向套利進場：
                # 現貨買入吃 asks
                # 合約開空等同賣出吃 bids
                spot_slip = estimate_buy_slippage(
                    spot_book,
                    SLIPPAGE_TEST_NOTIONAL_USDT,
                )

                fut_slip = estimate_sell_slippage(
                    fut_book,
                    SLIPPAGE_TEST_NOTIONAL_USDT,
                )

                if spot_slip is None or fut_slip is None:
                    row.update({
                        "status": "FAIL",
                        "fail_reason": "order book 深度不足",
                    })
                    return row
            else:
                spot_slip = 0.0
                fut_slip = 0.0

            total_slip = spot_slip + fut_slip

            row.update({
                "spot_slippage": spot_slip,
                "futures_slippage": fut_slip,
                "total_slippage": total_slip,
            })

            if total_slip > MAX_TOTAL_SLIPPAGE_RATE:
                fail_reasons.append(f"滑點過高：{fmt_pct(total_slip)}")

            stability, stability_fail_reason = analyze_history(
                hist,
                current_rate,
            )

            if stability is None:
                row.update({
                    "status": "FAIL",
                    "fail_reason": stability_fail_reason,
                })
                return row

            if stability_fail_reason:
                fail_reasons.append(stability_fail_reason)

            metrics = calc_forward_net_metrics(
                stability["avg_funding_rate_7d"],
                spot_slip,
                fut_slip,
            )

            row.update(stability)
            row.update(metrics)

            avg_rate = stability["avg_funding_rate_7d"]
            std_rate = stability.get("std_funding_rate_7d")
            positive_ratio = stability.get("positive_ratio_7d")
            net_apy = metrics.get("net_apy")
            payback_days = metrics.get("payback_days")

            # =========================
            # 額外硬性過濾
            # =========================

            if avg_rate < AVG_FUNDING_RATE_THRESHOLD:
                fail_reasons.append(f"7日平均資金費率不足：{avg_rate:.6f}")

            if positive_ratio is not None and positive_ratio < POSITIVE_RATIO_THRESHOLD:
                fail_reasons.append(f"正費率比例不足：{positive_ratio:.2f}")

            if std_rate is not None and std_rate > MAX_FUNDING_STD_7D:
                fail_reasons.append(f"資金費率波動過大：{std_rate:.6f}")

            if (
                avg_rate > 0
                and std_rate is not None
                and (std_rate / avg_rate) > MAX_STD_TO_AVG_RATIO
            ):
                fail_reasons.append(f"資金費率波動相對平均過大：{std_rate / avg_rate:.2f}")

            if payback_days is None or payback_days > MAX_PAYBACK_DAYS:
                fail_reasons.append(f"回本天數過長：{safe_float(payback_days, 999999):.2f} 天")

            if net_apy is None or net_apy < TARGET_NET_APY:
                fail_reasons.append(
                    f"扣除成本後真實淨年化不足：{safe_float(net_apy, 0.0) * 100:.2f}%"
                )

            # 去重，避免同一個原因重複出現
            clean_fail_reasons = []
            seen = set()

            for reason in fail_reasons:
                if not reason:
                    continue
                if reason in seen:
                    continue
                seen.add(reason)
                clean_fail_reasons.append(reason)

            if clean_fail_reasons:
                # 高 funding 但未完全通過，可列 WATCH
                if (
                    ENABLE_HIGH_RISK_WATCHLIST
                    and current_rate >= HIGH_RISK_CURRENT_RATE_THRESHOLD
                    and not force_track
                ):
                    row.update({
                        "status": "WATCH",
                        "signal_level": "⚠️ 高風險觀察",
                        "fail_reason": "；".join(clean_fail_reasons),
                    })
                    return row

                row.update({
                    "status": "FAIL",
                    "fail_reason": "；".join(clean_fail_reasons),
                })
                return row

            row.update({
                "status": "PASS",
                "signal_level": classify(payback_days, net_apy),
            })

            return row

        except Exception as e:
            row.update({
                "status": "FAIL",
                "fail_reason": str(e),
            })
            return row

            # =========================
            # 正向套利真實淨利版硬性過濾
            # =========================

            if current_rate <= 0:
                row.update({
                    "status": "FAIL",
                    "fail_reason": f"非正資金費率，不適合正向套利：{current_rate:.6f}",
                })
                return row

            if current_rate < CURRENT_FUNDING_RATE_THRESHOLD:
                row.update({
                    "status": "FAIL",
                    "fail_reason": f"當前資金費率不足：{current_rate:.6f}",
                })
                return row

            if avg_rate < AVG_FUNDING_RATE_THRESHOLD:
                row.update({
                    "status": "FAIL",
                    "fail_reason": f"7日平均資金費率不足：{avg_rate:.6f}",
                })
                return row

            if positive_ratio is not None and positive_ratio < POSITIVE_RATIO_THRESHOLD:
                row.update({
                    "status": "FAIL",
                    "fail_reason": f"正費率比例不足：{positive_ratio:.2f}",
                })
                return row

            if std_rate is not None and std_rate > MAX_FUNDING_STD_7D:
                row.update({
                    "status": "FAIL",
                    "fail_reason": f"資金費率波動過大：{std_rate:.6f}",
                })
                return row

            if (
                avg_rate > 0
                and std_rate is not None
                and (std_rate / avg_rate) > MAX_STD_TO_AVG_RATIO
            ):
                row.update({
                    "status": "FAIL",
                    "fail_reason": f"資金費率波動相對平均過大：{std_rate / avg_rate:.2f}",
                })
                return row

            if total_slip > MAX_TOTAL_SLIPPAGE_RATE:
                row.update({
                    "status": "FAIL",
                    "fail_reason": f"總滑點過高：{fmt_pct(total_slip)}",
                })
                return row

            if payback_days is None or payback_days > MAX_PAYBACK_DAYS:
                row.update({
                    "status": "FAIL",
                    "fail_reason": f"回本天數過長：{safe_float(payback_days, 999999):.2f} 天",
                })
                return row

            if net_apy is None or net_apy < TARGET_NET_APY:
                row.update({
                    "status": "FAIL",
                    "fail_reason": f"扣除成本後真實淨年化不足：{safe_float(net_apy, 0.0) * 100:.2f}%",
                })
                return row

            if c["quote_volume"] < MIN_24H_QUOTE_VOLUME_USDT:
                row.update({
                    "status": "FAIL",
                    "fail_reason": f"24H 成交額不足：{c['quote_volume']:.0f}",
                })
                return row

            if oi_notional < MIN_OPEN_INTEREST_NOTIONAL_USDT:
                row.update({
                    "status": "FAIL",
                    "fail_reason": f"合約未平倉名目價值不足：{oi_notional:.0f}",
                })
                return row

            row.update({
                "status": "PASS",
                "signal_level": classify(payback_days, net_apy),
            })

            return row

        except Exception as e:
            row.update({
                "status": "FAIL",
                "fail_reason": str(e),
            })
            return row

    async def alert(
        self,
        passed: List[Dict[str, Any]],
        watched: List[Dict[str, Any]],
    ):
        send_pass = []
        send_watch = []

        for t in passed:
            key = f"PASS:{t['symbol']}"
            if self.db.should_alert(key):
                self.db.mark_alert(key)
                send_pass.append(t)

        for t in watched:
            key = f"WATCH:{t['symbol']}"
            if self.db.should_alert(key):
                self.db.mark_alert(key)
                send_watch.append(t)

        if not send_pass and not send_watch:
            return

        lines = [
            "🚨 <b>Funding Radar V2｜正向套利真實淨利版</b>",
            f"時間：<code>{utc_text()}</code>",
            f"PASS：<b>{len(send_pass)}</b>",
            f"WATCH：<b>{len(send_watch)}</b>",
            "",
        ]

        if send_pass:
            lines.append("✅ <b>真實淨利通過標的</b>")

            for i, t in enumerate(send_pass, 1):
                lines.extend([
                    "",
                    f"#{i} <b>{t['symbol']}</b>｜{t.get('signal_level')}",
                    "方向：<b>買現貨 + 空合約</b>",
                    f"當前費率：<b>{fmt_pct(t.get('current_funding_rate'))}</b>",
                    f"7日平均：<b>{fmt_pct(t.get('avg_funding_rate_7d'))}</b>",
                    f"毛年化：<b>{fmt_pct(t.get('gross_apy') or t.get('apy'), 2)}</b>",
                    f"真實淨年化：<b>{fmt_pct(t.get('net_apy'), 2)}</b>",
                    f"回本：<b>{safe_float(t.get('payback_days'), 999999):.2f} 天</b>",
                    f"完整成本：<b>{fmt_pct(t.get('roundtrip_cost_rate'), 4)}</b>",
                    f"每日 funding：<b>{fmt_pct(t.get('daily_funding_yield'), 4)}</b>",
                    f"每日成本攤提：<b>{fmt_pct(t.get('daily_cost_drag'), 4)}</b>",
                    f"Basis：<b>{fmt_pct(t.get('basis_rate'))}</b>",
                    f"總滑點：<b>{fmt_pct(t.get('total_slippage'))}</b>",
                    f"半自動：<code>/order {t['symbol']} {DEFAULT_ORDER_NOTIONAL_USDT}</code>",
                    f"診斷：<code>/why {t['symbol']}</code>",
                ])

        if send_watch:
            lines.extend([
                "",
                "⚠️ <b>高風險觀察</b>",
            ])

            for i, t in enumerate(send_watch, 1):
                lines.extend([
                    "",
                    f"#{i} <b>{t['symbol']}</b>",
                    f"當前費率：<b>{fmt_pct(t.get('current_funding_rate'))}</b>",
                    f"原因：<code>{html.escape(str(t.get('fail_reason', '')))}</code>",
                    f"診斷：<code>/why {t['symbol']}</code>",
                ])

        lines.extend([
            "",
            f"🎯 目標真實淨年化：<b>{TARGET_NET_APY * 100:.2f}%</b>",
            "⚠️ 僅供監控，不代表投資建議。實盤請先 DRY_RUN。",
        ])

        await self.tg.send("\n".join(lines))


# =========================
# Main
# =========================
async def main():
    logger.info("Funding Radar V2 Net Profit starting")

    db = RadarDB(DB_PATH)

    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
    sem = asyncio.Semaphore(REQUEST_CONCURRENCY)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        http = Http(session, sem)
        api = BinancePublic(http)
        trader = Trader(http, db)
        tg = Telegram(session, db, trader)
        scanner = Scanner(db, api, tg)

        await asyncio.gather(
            scanner.loop(),
            tg.poll_loop(),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped")
