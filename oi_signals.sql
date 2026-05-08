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
