import os
import json
import time
import hmac
import math
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

CURRENT_FUNDING_RATE_THRESHOLD = float(os.getenv("CURRENT_FUNDING_RATE_THRESHOLD", "0.0002"))
AVG_FUNDING_RATE_THRESHOLD = float(os.getenv("AVG_FUNDING_RATE_THRESHOLD", "0.00015"))
POSITIVE_RATIO_THRESHOLD = float(os.getenv("POSITIVE_RATIO_THRESHOLD", "0.8"))
HISTORICAL_FUNDING_LIMIT = int(os.getenv("HISTORICAL_FUNDING_LIMIT", "21"))

HIGH_RISK_CURRENT_RATE_THRESHOLD = float(os.getenv("HIGH_RISK_CURRENT_RATE_THRESHOLD", "0.0005"))
ENABLE_HIGH_RISK_WATCHLIST = os.getenv("ENABLE_HIGH_RISK_WATCHLIST", "true").lower() == "true"

MAX_ABS_BASIS_RATE = float(os.getenv("MAX_ABS_BASIS_RATE", "0.0015"))

MAX_FUNDING_STD_7D = float(os.getenv("MAX_FUNDING_STD_7D", "0.0005"))
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

MAX_PAYBACK_DAYS = float(os.getenv("MAX_PAYBACK_DAYS", "4"))
STRONG_SIGNAL_PAYBACK_DAYS = float(os.getenv("STRONG_SIGNAL_PAYBACK_DAYS", "2"))

MIN_24H_QUOTE_VOLUME_USDT = float(os.getenv("MIN_24H_QUOTE_VOLUME_USDT", "10000000"))
MIN_OPEN_INTEREST_NOTIONAL_USDT = float(os.getenv("MIN_OPEN_INTEREST_NOTIONAL_USDT", "5000000"))

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
    return f"{x * 100:.{digits}f}%"


def norm_symbol(s: str) -> str:
    return s.strip().upper()


def ensure_data_dir():
    d = os.path.dirname(DB_PATH)
    if d:
        os.makedirs(d, exist_ok=True)


# =========================
# SQLite DB
# =========================
class RadarDB:
    def __init__(self, path: str):
        self.path = path
        ensure_data_dir()
        self.init_db()

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
                current_funding_rate REAL,
                avg_funding_rate_7d REAL,
                std_funding_rate_7d REAL,
                positive_ratio_7d REAL,
                recent_avg_funding_rate REAL,
                apy REAL,
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
                ts,symbol,status,signal_level,current_funding_rate,
                avg_funding_rate_7d,std_funding_rate_7d,positive_ratio_7d,
                recent_avg_funding_rate,apy,payback_days,basis_rate,
                spot_slippage,futures_slippage,total_slippage,quote_volume,
                open_interest_notional,mark_price,fail_reason
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                row.get("ts", now_ts()),
                row.get("symbol"),
                row.get("status"),
                row.get("signal_level"),
                row.get("current_funding_rate"),
                row.get("avg_funding_rate_7d"),
                row.get("std_funding_rate_7d"),
                row.get("positive_ratio_7d"),
                row.get("recent_avg_funding_rate"),
                row.get("apy"),
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

        # 注意：
        # 這裡保守地不直接實作完整實盤下單，避免沒有狀態機就發生單邊成交風險。
        # 若你確定要開實盤，下個版本應加入：
        # 1. 交易對精度解析
        # 2. 最小下單量檢查
        # 3. 一邊成交、一邊失敗的緊急補救
        # 4. 持倉 delta 校正
        return {
            "ok": False,
            "mode": "LIVE_NOT_IMPLEMENTED_SAFE_GUARD",
            "message": "安全保護：此貼上版不直接執行實盤下單。請先使用 DRY_RUN。",
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

    if pos_ratio <= POSITIVE_RATIO_THRESHOLD:
        return None, f"正費率比例不足：{pos_ratio:.2f}"

    if avg <= AVG_FUNDING_RATE_THRESHOLD:
        return None, f"7日平均不足：{fmt_pct(avg)}"

    if std > MAX_FUNDING_STD_7D:
        return None, f"標準差過高：{fmt_pct(std)}"

    if max_abs > MAX_ABS_HISTORICAL_FUNDING_RATE:
        return None, f"歷史異常值過高：{fmt_pct(max_abs)}"

    if recent_avg < avg * RECENT_AVG_MIN_RATIO_TO_7D:
        return None, "近期費率衰退"

    if current_rate < avg * CURRENT_MIN_RATIO_TO_7D:
        return None, "當前費率低於7日平均過多"

    return {
        "avg_funding_rate_7d": avg,
        "std_funding_rate_7d": std,
        "positive_ratio_7d": pos_ratio,
        "recent_avg_funding_rate": recent_avg,
    }, ""


def calc_metrics(avg_rate: float, spot_slip: float, fut_slip: float) -> Dict[str, float]:
    daily = avg_rate * 3
    total_cost = SPOT_TAKER_FEE_RATE + FUTURES_TAKER_FEE_RATE + spot_slip + fut_slip

    if daily <= 0:
        return {"apy": 0.0, "payback_days": 999999.0}

    return {
        "apy": daily * 365,
        "payback_days": total_cost / daily,
    }


def classify(payback: float) -> str:
    if payback < STRONG_SIGNAL_PAYBACK_DAYS:
        return "🔥 強訊號"
    if payback < MAX_PAYBACK_DAYS:
        return "✅ 可觀察"
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
        cmd = parts[0].lower()

        try:
            if cmd == "/help":
                await self.send(self.help_text())

            elif cmd == "/status":
                await self.send(
                    "📡 <b>Radar Status</b>\n"
                    f"scanner_paused: <code>{self.db.get_setting('scanner_paused','false')}</code>\n"
                    f"ENABLE_TRADING: <code>{ENABLE_TRADING}</code>\n"
                    f"DRY_RUN: <code>{DRY_RUN}</code>\n"
                    f"DB_PATH: <code>{DB_PATH}</code>"
                )

            elif cmd == "/pause":
                self.db.set_setting("scanner_paused", "true")
                await self.send("⏸ 已暫停掃描")

            elif cmd == "/resume":
                self.db.set_setting("scanner_paused", "false")
                await self.send("▶️ 已恢復掃描")

            elif cmd == "/top":
                await self.cmd_top()

            elif cmd == "/blacklist_add" and len(parts) >= 2:
                symbol = norm_symbol(parts[1])
                reason = " ".join(parts[2:]) if len(parts) > 2 else "telegram"
                self.db.add_blacklist(symbol, reason)
                await self.send(f"✅ 已加入黑名單：<b>{symbol}</b>")

            elif cmd == "/blacklist_remove" and len(parts) >= 2:
                symbol = norm_symbol(parts[1])
                self.db.remove_blacklist(symbol)
                await self.send(f"✅ 已移除黑名單：<b>{symbol}</b>")

            elif cmd == "/order" and len(parts) >= 2:
                symbol = norm_symbol(parts[1])
                notional = float(parts[2]) if len(parts) >= 3 else DEFAULT_ORDER_NOTIONAL_USDT
                await self.cmd_order(symbol, notional)

            elif cmd == "/confirm" and len(parts) >= 2:
                await self.cmd_confirm(parts[1].upper())

            elif cmd == "/cancel" and len(parts) >= 2:
                await self.cmd_cancel(parts[1].upper())

            else:
                await self.send("未知指令，請輸入 /help")

        except Exception as e:
            await self.send(f"❌ 指令錯誤：<code>{e}</code>")

    def help_text(self) -> str:
        return (
            "🤖 <b>Funding Radar 指令</b>\n\n"
            "/status - 查看狀態\n"
            "/top - 查看最新 PASS 訊號\n"
            "/pause - 暫停掃描\n"
            "/resume - 恢復掃描\n"
            "/blacklist_add SYMBOL reason - 加入黑名單\n"
            "/blacklist_remove SYMBOL - 移除黑名單\n"
            "/order SYMBOL amount - 建立半自動下單意圖\n"
            "/confirm CODE - 確認下單意圖\n"
            "/cancel CODE - 取消下單意圖\n\n"
            "範例：\n"
            "<code>/order SOLUSDT 50</code>"
        )

    async def cmd_top(self):
        with self.db.conn() as con:
            con.row_factory = sqlite3.Row
            rows = con.execute("""
            SELECT *
            FROM scan_results
            WHERE status='PASS'
            ORDER BY ts DESC, payback_days ASC
            LIMIT 10
            """).fetchall()

        if not rows:
            await self.send("目前沒有 PASS 訊號")
            return

        lines = ["🏆 <b>最新 PASS 訊號</b>"]

        for r in rows:
            lines.append(
                f"\n<b>{r['symbol']}</b>｜{r['signal_level']}\n"
                f"當前費率：{fmt_pct(r['current_funding_rate'])}\n"
                f"7日平均：{fmt_pct(r['avg_funding_rate_7d'])}\n"
                f"APY：{fmt_pct(r['apy'], 2)}\n"
                f"回本：{r['payback_days']:.2f} 天\n"
                f"Basis：{fmt_pct(r['basis_rate'])}\n"
                f"半自動：<code>/order {r['symbol']} {DEFAULT_ORDER_NOTIONAL_USDT}</code>"
            )

        await self.send("\n".join(lines))

    async def cmd_order(self, symbol: str, notional: float):
        if notional <= 0 or notional > MAX_ORDER_NOTIONAL_USDT:
            await self.send(f"❌ 金額不合法，上限 {MAX_ORDER_NOTIONAL_USDT} USDT")
            return

        intent = self.db.create_intent(symbol, notional, "telegram manual order")

        await self.send(
            "📝 <b>已建立半自動下單意圖</b>\n\n"
            f"Symbol：<b>{intent['symbol']}</b>\n"
            f"方向：<code>買現貨 + 空合約</code>\n"
            f"金額：<code>{notional:.2f} USDT</code>\n"
            f"ENABLE_TRADING：<code>{ENABLE_TRADING}</code>\n"
            f"DRY_RUN：<code>{DRY_RUN}</code>\n\n"
            f"確認碼：<code>{intent['confirm_code']}</code>\n\n"
            f"確認：<code>/confirm {intent['confirm_code']}</code>\n"
            f"取消：<code>/cancel {intent['confirm_code']}</code>"
        )

    async def cmd_confirm(self, code: str):
        intent = self.db.get_pending_intent(code)
        if not intent:
            await self.send("❌ 找不到待確認下單意圖")
            return

        result = await self.trader.execute_hedge_entry(intent)
        status = "EXECUTED" if result.get("ok") else "FAILED"
        self.db.update_intent(intent["id"], status, result)

        await self.send(
            "✅ <b>下單意圖處理完成</b>\n"
            f"Symbol：<b>{intent['symbol']}</b>\n"
            f"Status：<code>{status}</code>\n"
            f"Mode：<code>{result.get('mode')}</code>\n"
            f"Message：<code>{result.get('message', '')}</code>"
        )

    async def cmd_cancel(self, code: str):
        intent = self.db.get_pending_intent(code)
        if not intent:
            await self.send("找不到待取消意圖")
            return
        self.db.update_intent(intent["id"], "CANCELLED", {"cancelled_by": "telegram"})
        await self.send(f"✅ 已取消：<code>{code}</code>")


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

            try:
                mark_price = float(p.get("markPrice", 0))
                current_rate = float(p.get("lastFundingRate", 0))
                quote_vol = float(ticker.get("quoteVolume", 0))
            except Exception:
                continue

            if mark_price <= 0:
                continue

            if current_rate <= CURRENT_FUNDING_RATE_THRESHOLD:
                continue

            if quote_vol < MIN_24H_QUOTE_VOLUME_USDT:
                continue

            candidates.append({
                "symbol": symbol,
                "mark_price": mark_price,
                "current_funding_rate": current_rate,
                "quote_volume": quote_vol,
            })

        logger.info(f"初步候選數量：{len(candidates)}")

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

        passed.sort(key=lambda x: (x.get("payback_days") or 9999, -(x.get("apy") or 0)))
        watched.sort(key=lambda x: x.get("current_funding_rate") or 0, reverse=True)

        await self.alert(passed[:10], watched[:5])
        logger.info(f"掃描完成 PASS={len(passed)} WATCH={len(watched)}")

    async def analyze(self, c: Dict[str, Any]) -> Dict[str, Any]:
        symbol = c["symbol"]

        row = {
            "ts": now_ts(),
            "symbol": symbol,
            "current_funding_rate": c["current_funding_rate"],
            "quote_volume": c["quote_volume"],
            "mark_price": c["mark_price"],
        }

        try:
            oi, spot_book, fut_book, hist = await asyncio.gather(
                self.api.open_interest(symbol),
                self.api.spot_depth(symbol),
                self.api.futures_depth(symbol),
                self.api.funding_history(symbol),
            )

            oi_notional = float(oi.get("openInterest", 0)) * c["mark_price"]
            row["open_interest_notional"] = oi_notional

            if oi_notional < MIN_OPEN_INTEREST_NOTIONAL_USDT:
                row.update({"status": "FAIL", "fail_reason": "OI 名目價值不足"})
                return row

            spot_mid = orderbook_mid(spot_book)
            fut_mid = orderbook_mid(fut_book)

            if not spot_mid or not fut_mid:
                row.update({"status": "FAIL", "fail_reason": "order book mid 無效"})
                return row

            basis = fut_mid / spot_mid - 1
            row["basis_rate"] = basis

            if abs(basis) > MAX_ABS_BASIS_RATE:
                row.update({"status": "FAIL", "fail_reason": f"Basis 過大：{fmt_pct(basis)}"})
                return row

            if USE_DYNAMIC_SLIPPAGE_COST:
                spot_slip = estimate_buy_slippage(spot_book, SLIPPAGE_TEST_NOTIONAL_USDT)
                fut_slip = estimate_sell_slippage(fut_book, SLIPPAGE_TEST_NOTIONAL_USDT)
                if spot_slip is None or fut_slip is None:
                    row.update({"status": "FAIL", "fail_reason": "order book 深度不足"})
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
                row.update({"status": "FAIL", "fail_reason": f"滑點過高：{fmt_pct(total_slip)}"})
                return row

            stability, fail_reason = analyze_history(hist, c["current_funding_rate"])

            if stability is None:
                if ENABLE_HIGH_RISK_WATCHLIST and c["current_funding_rate"] >= HIGH_RISK_CURRENT_RATE_THRESHOLD:
                    row.update({
                        "status": "WATCH",
                        "signal_level": "⚠️ 高風險觀察",
                        "fail_reason": fail_reason,
                    })
                    return row

                row.update({"status": "FAIL", "fail_reason": fail_reason})
                return row

            metrics = calc_metrics(stability["avg_funding_rate_7d"], spot_slip, fut_slip)

            row.update(stability)
            row.update(metrics)

            if metrics["payback_days"] >= MAX_PAYBACK_DAYS:
                row.update({
                    "status": "FAIL",
                    "fail_reason": f"回本天數過長：{metrics['payback_days']:.2f}",
                })
                return row

            row.update({
                "status": "PASS",
                "signal_level": classify(metrics["payback_days"]),
            })
            return row

        except Exception as e:
            row.update({"status": "FAIL", "fail_reason": str(e)})
            return row

    async def alert(self, passed: List[Dict[str, Any]], watched: List[Dict[str, Any]]):
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
            "🚨 <b>Funding Radar V2</b>",
            f"時間：<code>{utc_text()}</code>",
            f"PASS：<b>{len(send_pass)}</b>",
            f"WATCH：<b>{len(send_watch)}</b>",
            "",
        ]

        if send_pass:
            lines.append("✅ <b>符合條件標的</b>")
            for i, t in enumerate(send_pass, 1):
                lines.extend([
                    "",
                    f"#{i} <b>{t['symbol']}</b>｜{t.get('signal_level')}",
                    f"當前費率：<b>{fmt_pct(t.get('current_funding_rate'))}</b>",
                    f"7日平均：<b>{fmt_pct(t.get('avg_funding_rate_7d'))}</b>",
                    f"APY：<b>{fmt_pct(t.get('apy'), 2)}</b>",
                    f"回本：<b>{t.get('payback_days', 0):.2f} 天</b>",
                    f"Basis：<b>{fmt_pct(t.get('basis_rate'))}</b>",
                    f"總滑點：<b>{fmt_pct(t.get('total_slippage'))}</b>",
                    f"半自動：<code>/order {t['symbol']} {DEFAULT_ORDER_NOTIONAL_USDT}</code>",
                ])

        if send_watch:
            lines.extend(["", "⚠️ <b>高風險觀察</b>"])
            for i, t in enumerate(send_watch, 1):
                lines.extend([
                    "",
                    f"#{i} <b>{t['symbol']}</b>",
                    f"當前費率：<b>{fmt_pct(t.get('current_funding_rate'))}</b>",
                    f"原因：<code>{t.get('fail_reason')}</code>",
                ])

        lines.extend([
            "",
            "⚠️ 僅供監控，不代表投資建議。實盤請先 DRY_RUN。",
        ])

        await self.tg.send("\n".join(lines))


# =========================
# Main
# =========================
async def main():
    logger.info("Funding Radar V2 starting")

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
