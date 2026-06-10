import os
import json
import sqlite3
import asyncio
from datetime import datetime, date, timedelta

import pandas as pd

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

import okx.MarketData as MarketData
import okx.Account as Account
import okx.Trade as Trade


# =========================
# CONFIG
# =========================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

OKX_API_KEY = os.getenv("OKX_API_KEY")
OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE")

OKX_FLAG = os.getenv("OKX_FLAG", "0")
LIVE_TRADING_ENABLED = os.getenv("LIVE_TRADING_ENABLED", "NO")

TRADE_SYMBOL = os.getenv("TRADE_SYMBOL", "BTC-USDT")
TRADE_AMOUNT_USDT = float(os.getenv("TRADE_AMOUNT_USDT", "5"))

AUTO_INTERVAL = int(os.getenv("AUTO_INTERVAL", "300"))
DB_FILE = "bot.db"

BUY_SCORE = int(os.getenv("BUY_SCORE", "80"))
SELL_SCORE = int(os.getenv("SELL_SCORE", "35"))

MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "5"))
MAX_TRADES_DAY = int(os.getenv("MAX_TRADES_DAY", "15"))

MIN_ADX = float(os.getenv("MIN_ADX", "20"))
COOLDOWN_AFTER_LOSS_MINUTES = int(os.getenv("COOLDOWN_AFTER_LOSS_MINUTES", "60"))

DUST_LIMIT_USDT = float(os.getenv("DUST_LIMIT_USDT", "5"))

TIMEFRAMES = ["5m", "15m", "1H"]

WATCHLIST = [
    "BTC-USDT",
    "ETH-USDT",
    "SOL-USDT",
    "XRP-USDT",
    "DOGE-USDT",
    "AVAX-USDT",
    "LINK-USDT",
    "SUI-USDT",
    "ADA-USDT",
    "TON-USDT",
    "DOT-USDT",
    "APT-USDT",
    "NEAR-USDT",
    "LTC-USDT",
    "BCH-USDT",
    "TRX-USDT",
    "ATOM-USDT",
    "OP-USDT",
    "FIL-USDT",
    "ETC-USDT",
]


# =========================
# TELEGRAM / OKX
# =========================

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

market_api = MarketData.MarketAPI(flag=OKX_FLAG)

account_api = Account.AccountAPI(
    OKX_API_KEY,
    OKX_SECRET_KEY,
    OKX_PASSPHRASE,
    False,
    OKX_FLAG,
)

trade_api = Trade.TradeAPI(
    OKX_API_KEY,
    OKX_SECRET_KEY,
    OKX_PASSPHRASE,
    False,
    OKX_FLAG,
)


# =========================
# GLOBAL STATE
# =========================

autotrade_enabled = False
auto_select_symbol = True
current_trade_symbol = TRADE_SYMBOL

sell_signal_locks = set()

risk_settings = {
    "amount_usdt": TRADE_AMOUNT_USDT,
    "max_amount_usdt": 25.0,

    "stop_loss_percent": 2.5,
    "take_profit_percent": 4.0,
    "trailing_stop_percent": 1.2,

    "buy_score": BUY_SCORE,
    "sell_score": SELL_SCORE,
    "min_adx": MIN_ADX,

    "max_open_positions": MAX_OPEN_POSITIONS,
    "max_trades_day": MAX_TRADES_DAY,

    "auto_amount_enabled": True,
    "balance_usage_percent": 10.0,
    "min_trade_usdt": 5.0,
    "max_trade_usdt": 25.0,

    "cooldown_after_loss_minutes": COOLDOWN_AFTER_LOSS_MINUTES,
}


# =========================
# KEYBOARD
# =========================

keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Статус"), KeyboardButton(text="💰 Баланс")],
        [KeyboardButton(text="📡 Сигнал"), KeyboardButton(text="🌐 Рынок")],
        [KeyboardButton(text="🔎 Сканер"), KeyboardButton(text="🏆 Лучшая")],
        [KeyboardButton(text="🥇 Топ-3"), KeyboardButton(text="📋 Позиции")],
        [KeyboardButton(text="🟢 Авто ВКЛ"), KeyboardButton(text="🔴 Авто ВЫКЛ")],
        [KeyboardButton(text="🧠 Авто монета"), KeyboardButton(text="💱 Текущая монета")],
        [KeyboardButton(text="🤖 Авто статус"), KeyboardButton(text="🛡 Риск")],
        [KeyboardButton(text="📜 История"), KeyboardButton(text="📈 Статистика")],
        [KeyboardButton(text="💹 PnL"), KeyboardButton(text="📅 Дневной отчет")],
        [KeyboardButton(text="🗓 Недельный отчет"), KeyboardButton(text="📆 Месячный отчет")],
        [KeyboardButton(text="🔄 Синхронизация OKX"), KeyboardButton(text="♻️ Сброс позиций")],
    ],
    resize_keyboard=True,
)


# =========================
# HELPERS
# =========================

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str():
    return date.today().isoformat()


def is_live_allowed():
    return str(OKX_FLAG) == "0" and LIVE_TRADING_ENABLED == "YES"


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def symbol_to_currency(symbol):
    return symbol.split("-")[0]


def currency_to_symbol(currency):
    return f"{currency}-USDT"
# =========================
# DATABASE
# =========================

def db_connect():
    return sqlite3.connect(DB_FILE)


def init_db():
    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TEXT,
            date TEXT,
            action TEXT,
            symbol TEXT,
            price REAL,
            score INTEGER,
            result TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS closed_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TEXT,
            date TEXT,
            symbol TEXT,
            entry_price REAL,
            exit_price REAL,
            amount_usdt REAL,
            pnl_percent REAL,
            pnl_usdt REAL,
            reason TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS open_positions (
            symbol TEXT PRIMARY KEY,
            entry_price REAL,
            amount_usdt REAL,
            stop_loss_price REAL,
            take_profit_price REAL,
            highest_price REAL,
            time TEXT
        )
        """
    )

    conn.commit()
    conn.close()


def db_set(key, value):
    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT OR REPLACE INTO settings (key, value)
        VALUES (?, ?)
        """,
        (key, json.dumps(value, ensure_ascii=False)),
    )

    conn.commit()
    conn.close()


def db_get(key, default=None):
    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        "SELECT value FROM settings WHERE key = ?",
        (key,),
    )

    row = cur.fetchone()
    conn.close()

    if not row:
        return default

    try:
        return json.loads(row[0])
    except Exception:
        return default


def save_runtime_settings():
    db_set("autotrade_enabled", autotrade_enabled)
    db_set("auto_select_symbol", auto_select_symbol)
    db_set("current_trade_symbol", current_trade_symbol)
    db_set("risk_settings", risk_settings)


def load_runtime_settings():
    global autotrade_enabled
    global auto_select_symbol
    global current_trade_symbol
    global risk_settings

    autotrade_enabled = False
    auto_select_symbol = bool(db_get("auto_select_symbol", True))
    current_trade_symbol = db_get("current_trade_symbol", TRADE_SYMBOL)

    saved_risk = db_get("risk_settings", {})
    if isinstance(saved_risk, dict):
        risk_settings.update(saved_risk)


def add_history(action, symbol, price, score, result=None):
    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO history (time, date, action, symbol, price, score, result)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now(),
            today_str(),
            action,
            symbol,
            safe_float(price),
            int(score),
            str(result)[:1500],
        ),
    )

    conn.commit()
    conn.close()


def get_history(limit=20):
    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT time, action, symbol, price, score
        FROM history
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )

    rows = cur.fetchall()
    conn.close()

    return rows


def add_closed_trade(symbol, entry_price, exit_price, amount_usdt, reason):
    pnl_percent = ((exit_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
    pnl_usdt = amount_usdt * pnl_percent / 100

    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO closed_trades
        (time, date, symbol, entry_price, exit_price, amount_usdt, pnl_percent, pnl_usdt, reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now(),
            today_str(),
            symbol,
            safe_float(entry_price),
            safe_float(exit_price),
            safe_float(amount_usdt),
            safe_float(pnl_percent),
            safe_float(pnl_usdt),
            reason,
        ),
    )

    conn.commit()
    conn.close()


def get_closed_trades(limit=1000):
    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT time, symbol, entry_price, exit_price, amount_usdt, pnl_percent, pnl_usdt, reason
        FROM closed_trades
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )

    rows = cur.fetchall()
    conn.close()

    return rows


def trades_today_count():
    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT COUNT(*)
        FROM history
        WHERE date = ? AND action LIKE '%AUTO%'
        """,
        (today_str(),),
    )

    count = cur.fetchone()[0]
    conn.close()

    return int(count)
# =========================
# POSITIONS
# =========================

def save_open_position(symbol, entry_price, amount_usdt, stop_loss_price, take_profit_price, highest_price):
    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT OR REPLACE INTO open_positions
        (symbol, entry_price, amount_usdt, stop_loss_price, take_profit_price, highest_price, time)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            symbol,
            safe_float(entry_price),
            safe_float(amount_usdt),
            safe_float(stop_loss_price),
            safe_float(take_profit_price),
            safe_float(highest_price),
            now(),
        ),
    )

    conn.commit()
    conn.close()


def update_open_position(symbol, position):
    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE open_positions
        SET entry_price = ?, amount_usdt = ?, stop_loss_price = ?,
            take_profit_price = ?, highest_price = ?
        WHERE symbol = ?
        """,
        (
            safe_float(position["entry_price"]),
            safe_float(position["amount_usdt"]),
            safe_float(position["stop_loss_price"]),
            safe_float(position["take_profit_price"]),
            safe_float(position["highest_price"]),
            symbol,
        ),
    )

    conn.commit()
    conn.close()


def delete_open_position(symbol):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM open_positions WHERE symbol = ?", (symbol,))
    conn.commit()
    conn.close()


def get_open_positions():
    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT symbol, entry_price, amount_usdt, stop_loss_price,
               take_profit_price, highest_price, time
        FROM open_positions
        """
    )

    rows = cur.fetchall()
    conn.close()

    positions = {}

    for row in rows:
        positions[row[0]] = {
            "symbol": row[0],
            "entry_price": safe_float(row[1]),
            "amount_usdt": safe_float(row[2]),
            "stop_loss_price": safe_float(row[3]),
            "take_profit_price": safe_float(row[4]),
            "highest_price": safe_float(row[5]),
            "time": row[6],
        }

    return positions


def clear_open_positions():
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM open_positions")
    conn.commit()
    conn.close()


def set_symbol_cooldown(symbol, reason="LOSS"):
    cooldowns = db_get("symbol_cooldowns", {})

    cooldowns[symbol] = {
        "until": (
            datetime.now()
            + timedelta(minutes=risk_settings["cooldown_after_loss_minutes"])
        ).timestamp(),
        "reason": reason,
    }

    db_set("symbol_cooldowns", cooldowns)


def is_symbol_in_cooldown(symbol):
    cooldowns = db_get("symbol_cooldowns", {})

    if symbol not in cooldowns:
        return False, ""

    until = cooldowns[symbol].get("until", 0)

    if datetime.now().timestamp() >= until:
        cooldowns.pop(symbol, None)
        db_set("symbol_cooldowns", cooldowns)
        return False, ""

    minutes_left = int((until - datetime.now().timestamp()) / 60)

    return True, f"Cooldown после убытка: {minutes_left} мин."
# =========================
# OKX API
# =========================

def get_okx_balance():
    try:
        data = account_api.get_account_balance()

        if not data or "data" not in data:
            return []

        details = data["data"][0]["details"]

        balances = []

        for item in details:

            ccy = item["ccy"]

            eq_usd = safe_float(item.get("eqUsd", 0))

            avail_bal = safe_float(item.get("availBal", 0))

            balances.append(
                {
                    "ccy": ccy,
                    "eq_usd": eq_usd,
                    "avail_bal": avail_bal,
                }
            )

        return balances

    except Exception:
        return []


def get_okx_asset_balance(symbol):

    currency = symbol_to_currency(symbol)

    balances = get_okx_balance()

    for item in balances:

        if item["ccy"] == currency:

            return item["avail_bal"]

    return 0.0


def get_usdt_balance():

    balances = get_okx_balance()

    for item in balances:

        if item["ccy"] == "USDT":

            return item["avail_bal"]

    return 0.0


def get_current_price(symbol):

    try:
        ticker = market_api.get_ticker(instId=symbol)

        return safe_float(
            ticker["data"][0]["last"]
        )

    except Exception:

        return 0.0


def get_okx_fills(limit=100):

    try:
        result = trade_api.get_fills()

        if (
            result
            and "data" in result
        ):

            return result["data"][:limit]

    except Exception:
        pass

    return []


def sync_positions_with_okx():

    positions = get_open_positions()

    balances = get_okx_balance()

    real_assets = set()

    for item in balances:

        if (
            item["ccy"] == "USDT"
            or item["eq_usd"] < DUST_LIMIT_USDT
        ):
            continue

        symbol = currency_to_symbol(
            item["ccy"]
        )

        real_assets.add(symbol)

    # удалить отсутствующие активы

    for symbol in list(positions.keys()):

        if symbol not in real_assets:

            delete_open_position(symbol)

            sell_signal_locks.discard(
                symbol
            )

    # добавить отсутствующие позиции

    for symbol in real_assets:

        if symbol in positions:
            continue

        current_price = get_current_price(
            symbol
        )

        amount_usdt = 0

        for item in balances:

            if (
                currency_to_symbol(
                    item["ccy"]
                )
                == symbol
            ):

                amount_usdt = item["eq_usd"]

                break

        save_open_position(
            symbol,
            current_price,
            amount_usdt,
            current_price * 0.975,
            current_price * 1.04,
            current_price,
        )


def unlock_missing_positions():

    positions = get_open_positions()

    for symbol in list(
        sell_signal_locks
    ):

        if symbol not in positions:

            sell_signal_locks.discard(
                symbol
            )


def can_trade_today():

    trades = trades_today_count()

    if (
        trades
        >= risk_settings[
            "max_trades_day"
        ]
    ):

        return (
            False,
            "Достигнут лимит сделок"
        )

    return True, "OK"


def can_open_new_position(
    symbol
):

    positions = get_open_positions()

    if symbol in positions:

        return (
            False,
            "Позиция уже существует"
        )

    if (
        len(positions)
        >= risk_settings[
            "max_open_positions"
        ]
    ):

        return (
            False,
            "Достигнут лимит позиций"
        )

    in_cd, reason = (
        is_symbol_in_cooldown(
            symbol
        )
    )

    if in_cd:

        return (
            False,
            reason
        )

    allowed, reason = (
        can_trade_today()
    )

    if not allowed:

        return (
            False,
            reason
        )

    return True, "OK"
# =========================
# INDICATORS
# =========================

def get_candles(symbol, timeframe="15m", limit=300):

    try:

        result = market_api.get_candlesticks(
            instId=symbol,
            bar=timeframe,
            limit=str(limit),
        )

        if (
            not result
            or "data" not in result
        ):
            return pd.DataFrame()

        rows = result["data"]

        rows.reverse()

        df = pd.DataFrame(
            rows,
            columns=[
                "ts",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "vol_ccy",
                "vol_ccy_quote",
                "confirm",
            ],
        )

        for col in [
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]:

            df[col] = (
                df[col]
                .astype(float)
            )

        return df

    except Exception:

        return pd.DataFrame()


def add_indicators(df):

    if len(df) < 200:

        return df

    close = df["close"]

    # EMA

    df["ema50"] = (
        close
        .ewm(span=50)
        .mean()
    )

    df["ema200"] = (
        close
        .ewm(span=200)
        .mean()
    )

    # RSI

    delta = close.diff()

    gain = (
        delta.where(
            delta > 0,
            0
        )
    )

    loss = (
        -delta.where(
            delta < 0,
            0
        )
    )

    avg_gain = (
        gain
        .rolling(14)
        .mean()
    )

    avg_loss = (
        loss
        .rolling(14)
        .mean()
    )

    rs = (
        avg_gain
        / avg_loss.replace(
            0,
            1e-9
        )
    )

    df["rsi"] = (
        100
        - (
            100
            / (1 + rs)
        )
    )

    # MACD

    ema12 = (
        close
        .ewm(span=12)
        .mean()
    )

    ema26 = (
        close
        .ewm(span=26)
        .mean()
    )

    df["macd"] = (
        ema12 - ema26
    )

    df["macd_signal"] = (
        df["macd"]
        .ewm(span=9)
        .mean()
    )

    # ATR

    tr1 = (
        df["high"]
        - df["low"]
    )

    tr2 = abs(
        df["high"]
        - close.shift()
    )

    tr3 = abs(
        df["low"]
        - close.shift()
    )

    true_range = pd.concat(
        [
            tr1,
            tr2,
            tr3,
        ],
        axis=1,
    ).max(axis=1)

    df["atr"] = (
        true_range
        .rolling(14)
        .mean()
    )

    # ADX

    plus_dm = (
        df["high"]
        .diff()
    )

    minus_dm = (
        -df["low"]
        .diff()
    )

    plus_dm = plus_dm.where(
        (
            plus_dm > minus_dm
        )
        &
        (
            plus_dm > 0
        ),
        0,
    )

    minus_dm = minus_dm.where(
        (
            minus_dm > plus_dm
        )
        &
        (
            minus_dm > 0
        ),
        0,
    )

    tr14 = (
        true_range
        .rolling(14)
        .sum()
    )

    plus_di = (
        100
        * (
            plus_dm
            .rolling(14)
            .sum()
            / tr14.replace(
                0,
                1e-9
            )
        )
    )

    minus_di = (
        100
        * (
            minus_dm
            .rolling(14)
            .sum()
            / tr14.replace(
                0,
                1e-9
            )
        )
    )

    dx = (
        abs(
            plus_di
            - minus_di
        )
        /
        (
            plus_di
            + minus_di
        ).replace(
            0,
            1e-9
        )
    ) * 100

    df["adx"] = (
        dx
        .rolling(14)
        .mean()
        .fillna(0)
    )

    return df
# =========================
# SIGNALS
# =========================

def build_signal(symbol, timeframe="15m"):

    df = get_candles(symbol, timeframe)

    if len(df) < 200:
        return None

    df = add_indicators(df)

    last = df.iloc[-1]

    score = 0

    # RSI

    if (
        45
        <= last["rsi"]
        <= 70
    ):
        score += 25

    # MACD

    if (
        last["macd"]
        > last["macd_signal"]
    ):
        score += 25

    # EMA trend

    if (
        last["ema50"]
        > last["ema200"]
    ):
        score += 25

    # ADX trend strength

    if (
        last["adx"]
        >= risk_settings["min_adx"]
    ):
        score += 25

    signal = "HOLD"

    if score >= risk_settings["buy_score"]:
        signal = "BUY"

    elif score <= risk_settings["sell_score"]:
        signal = "SELL"

    return {
        "symbol": symbol,
        "signal": signal,
        "score": score,
        "price": safe_float(last["close"]),

        "rsi": safe_float(last["rsi"]),
        "macd": safe_float(last["macd"]),
        "macd_signal": safe_float(last["macd_signal"]),

        "ema50": safe_float(last["ema50"]),
        "ema200": safe_float(last["ema200"]),

        "adx": safe_float(last["adx"]),
        "atr": safe_float(last["atr"]),
    }


def multi_timeframe_decision_for_symbol(symbol):

    results = []

    for tf in TIMEFRAMES:

        signal_data = build_signal(
            symbol,
            tf
        )

        if signal_data:

            results.append(
                signal_data
            )

    if not results:

        return {
            "signal": "HOLD",
            "avg_score": 0,
            "price": 0,
        }

    avg_score = (
        sum(
            x["score"]
            for x in results
        )
        / len(results)
    )

    signal = "HOLD"

    if avg_score >= risk_settings["buy_score"]:

        signal = "BUY"

    elif avg_score <= risk_settings["sell_score"]:

        signal = "SELL"

    return {
        "signal": signal,
        "avg_score": round(avg_score, 2),
        "price": results[-1]["price"],
    }
# =========================
# SYMBOL SELECTION
# =========================

def choose_best_symbol():

    candidates = []

    for symbol in WATCHLIST:

        try:

            decision = (
                multi_timeframe_decision_for_symbol(
                    symbol
                )
            )

            signal_data = (
                build_signal(
                    symbol,
                    "15m"
                )
            )

            if (
                decision["signal"] == "BUY"
                and signal_data
                and signal_data["ema50"] > signal_data["ema200"]
                and signal_data["adx"] >= risk_settings["min_adx"]
            ):

                candidates.append(
                    {
                        "symbol": symbol,
                        "score": decision["avg_score"],
                        "adx": signal_data["adx"],
                    }
                )

        except Exception:
            pass

    if not candidates:

        return (
            TRADE_SYMBOL,
            {
                "score": 0,
                "adx": 0,
            },
        )

    candidates = sorted(
        candidates,
        key=lambda x: (
            x["score"],
            x["adx"],
        ),
        reverse=True,
    )

    best = candidates[0]

    return (
        best["symbol"],
        best,
    )


def get_top3_symbols():

    candidates = []

    for symbol in WATCHLIST:

        try:

            decision = (
                multi_timeframe_decision_for_symbol(
                    symbol
                )
            )

            signal_data = (
                build_signal(
                    symbol,
                    "15m"
                )
            )

            if (
                signal_data
                and signal_data["ema50"] > signal_data["ema200"]
                and signal_data["adx"] >= risk_settings["min_adx"]
            ):

                candidates.append(
                    {
                        "symbol": symbol,
                        "score": decision["avg_score"],
                        "adx": signal_data["adx"],
                    }
                )

        except Exception:
            pass

    candidates = sorted(
        candidates,
        key=lambda x: (
            x["score"],
            x["adx"],
        ),
        reverse=True,
    )

    return candidates[:3]
# =========================
# TRADE FUNCTIONS
# =========================

def get_trade_amount_usdt():

    if not risk_settings["auto_amount_enabled"]:
        return risk_settings["amount_usdt"]

    balance = get_usdt_balance()

    amount = (
        balance
        * risk_settings["balance_usage_percent"]
        / 100
    )

    amount = max(
        amount,
        risk_settings["min_trade_usdt"]
    )

    amount = min(
        amount,
        risk_settings["max_trade_usdt"]
    )

    return round(amount, 2)


def place_market_buy(
    symbol,
    amount_usdt
):

    if not is_live_allowed():

        return "LIVE OFF"

    try:

        result = trade_api.place_order(
            instId=symbol,
            tdMode="cash",
            side="buy",
            ordType="market",
            sz=str(amount_usdt),
        )

        return str(result)

    except Exception as e:

        return str(e)


def place_market_sell(
    symbol,
    amount_usdt,
    current_price
):

    if not is_live_allowed():

        return "LIVE OFF"

    try:

        balance = get_okx_asset_balance(
            symbol
        )

        if balance <= 0:

            return "NO ASSET"

        result = trade_api.place_order(
            instId=symbol,
            tdMode="cash",
            side="sell",
            ordType="market",
            sz=str(balance),
        )

        return str(result)

    except Exception as e:

        return str(e)


def open_position(
    symbol,
    entry_price,
    amount_usdt
):

    stop_loss_price = (
        entry_price
        * (
            1
            - risk_settings["stop_loss_percent"] / 100
        )
    )

    take_profit_price = (
        entry_price
        * (
            1
            + risk_settings["take_profit_percent"] / 100
        )
    )

    save_open_position(
        symbol,
        entry_price,
        amount_usdt,
        stop_loss_price,
        take_profit_price,
        entry_price,
    )


def close_position(
    symbol,
    exit_price,
    reason
):

    positions = get_open_positions()

    if symbol not in positions:
        return

    position = positions[symbol]

    add_closed_trade(
        symbol,
        position["entry_price"],
        exit_price,
        position["amount_usdt"],
        reason,
    )

    delete_open_position(
        symbol
    )


def update_trailing_stop(
    symbol,
    current_price
):

    positions = get_open_positions()

    if symbol not in positions:
        return

    position = positions[symbol]

    if (
        current_price
        > position["highest_price"]
    ):

        position["highest_price"] = (
            current_price
        )

        position["stop_loss_price"] = (
            current_price
            * (
                1
                - risk_settings[
                    "trailing_stop_percent"
                ]
                / 100
            )
        )

        update_open_position(
            symbol,
            position
        )
# =========================
# AUTOTRADE
# =========================

async def autotrade_loop(chat_id):

    global autotrade_enabled
    global current_trade_symbol

    while autotrade_enabled:

        try:

            sync_positions_with_okx()
            unlock_missing_positions()

            positions = get_open_positions()

            # =====================
            # CHECK OPEN POSITIONS
            # =====================

            for symbol, position in positions.items():

                try:

                    current_price = get_current_price(
                        symbol
                    )

                    if current_price <= 0:
                        continue

                    update_trailing_stop(
                        symbol,
                        current_price
                    )

                    positions = get_open_positions()

                    if symbol not in positions:
                        continue

                    position = positions[symbol]

                    pnl_percent = (
                        (
                            current_price
                            - position["entry_price"]
                        )
                        / position["entry_price"]
                    ) * 100

                    # TRAILING STOP

                    if (
                        current_price
                        <= position["stop_loss_price"]
                    ):

                        if symbol in sell_signal_locks:
                            continue

                        sell_signal_locks.add(
                            symbol
                        )

                        result = (
                            place_market_sell(
                                symbol,
                                position["amount_usdt"],
                                current_price
                            )
                        )

                        close_position(
                            symbol,
                            current_price,
                            "TRAILING STOP"
                        )

                        set_symbol_cooldown(
                            symbol,
                            "TRAILING STOP"
                        )

                        sync_positions_with_okx()

                        add_history(
                            "AUTO TRAILING STOP",
                            symbol,
                            current_price,
                            100,
                            result
                        )

                        await bot.send_message(
                            chat_id,
                            f"🛡 TRAILING STOP\n\n"
                            f"{symbol}\n"
                            f"PnL: {pnl_percent:.2f}%"
                        )

                        sell_signal_locks.discard(
                            symbol
                        )

                        continue

                    # SELL SIGNAL

                    decision = (
                        multi_timeframe_decision_for_symbol(
                            symbol
                        )
                    )

                    if (
                        decision["signal"]
                        == "SELL"
                    ):

                        if symbol in sell_signal_locks:
                            continue

                        sell_signal_locks.add(
                            symbol
                        )

                        result = (
                            place_market_sell(
                                symbol,
                                position["amount_usdt"],
                                current_price
                            )
                        )

                        close_position(
                            symbol,
                            current_price,
                            "SELL SIGNAL"
                        )

                        sync_positions_with_okx()

                        add_history(
                            "AUTO SELL",
                            symbol,
                            current_price,
                            decision["avg_score"],
                            result
                        )

                        await bot.send_message(
                            chat_id,
                            f"🔴 SELL SIGNAL\n\n"
                            f"{symbol}\n"
                            f"PnL: {pnl_percent:.2f}%"
                        )

                        sell_signal_locks.discard(
                            symbol
                        )

                except Exception as e:

                    await bot.send_message(
                        chat_id,
                        f"⚠️ Ошибка позиции\n\n"
                        f"{symbol}\n\n"
                        f"{e}"
                    )

            # =====================
            # OPEN NEW POSITION
            # =====================

            positions = get_open_positions()

            if (
                len(positions)
                < risk_settings[
                    "max_open_positions"
                ]
            ):

                if auto_select_symbol:

                    symbol, best = (
                        choose_best_symbol()
                    )

                else:

                    symbol = (
                        current_trade_symbol
                    )

                current_trade_symbol = symbol

                decision = (
                    multi_timeframe_decision_for_symbol(
                        symbol
                    )
                )

                signal_data = (
                    build_signal(
                        symbol,
                        "15m"
                    )
                )

                if signal_data:

                    strong_buy = (

                        decision["signal"]
                        == "BUY"

                        and decision["avg_score"]
                        >= 80

                        and signal_data["ema50"]
                        >
                        signal_data["ema200"]

                        and signal_data["adx"]
                        >= risk_settings["min_adx"]

                    )

                    if strong_buy:

                        allowed, reason = (
                            can_open_new_position(
                                symbol
                            )
                        )

                        if allowed:

                            amount = (
                                get_trade_amount_usdt()
                            )

                            result = (
                                place_market_buy(
                                    symbol,
                                    amount
                                )
                            )

                            open_position(
                                symbol,
                                decision["price"],
                                amount
                            )

                            sync_positions_with_okx()

                            add_history(
                                "AUTO BUY",
                                symbol,
                                decision["price"],
                                decision["avg_score"],
                                result
                            )

                            await bot.send_message(
                                chat_id,
                                f"🟢 AUTO BUY\n\n"
                                f"{symbol}\n"
                                f"Цена: {decision['price']:.4f}\n"
                                f"Сила сигнала: {decision['avg_score']}%\n"
                                f"ADX: {signal_data['adx']:.2f}"
                            )

        except Exception as e:

            await bot.send_message(
                chat_id,
                f"❌ Ошибка автоторговли\n\n{e}"
            )

        await asyncio.sleep(
            AUTO_INTERVAL
        )
# =========================
# STATISTICS
# =========================

def calculate_stats(period="all"):

    trades = get_closed_trades(10000)

    today = date.today()
    week_start = today - timedelta(days=7)
    month_start = today.replace(day=1)

    filtered = []

    for row in trades:

        trade_date = datetime.strptime(
            row[0],
            "%Y-%m-%d %H:%M:%S"
        ).date()

        if period == "day":
            if trade_date != today:
                continue

        elif period == "week":
            if trade_date < week_start:
                continue

        elif period == "month":
            if trade_date < month_start:
                continue

        filtered.append(row)

    total = len(filtered)

    wins = len(
        [
            x
            for x in filtered
            if x[6] > 0
        ]
    )

    losses = total - wins

    pnl_usdt = sum(
        x[6]
        for x in filtered
    )

    pnl_percent = sum(
        x[5]
        for x in filtered
    )

    winrate = (
        wins / total * 100
        if total > 0
        else 0
    )

    return {
        "trades": total,
        "wins": wins,
        "losses": losses,
        "winrate": winrate,
        "pnl_usdt": pnl_usdt,
        "pnl_percent": pnl_percent,
    }


def build_period_report(
    title,
    period
):

    stats = calculate_stats(
        period
    )

    return (
        f"{title}\n\n"

        f"Сделок: "
        f"{stats['trades']}\n"

        f"Прибыльных: "
        f"{stats['wins']}\n"

        f"Убыточных: "
        f"{stats['losses']}\n\n"

        f"WinRate: "
        f"{stats['winrate']:.2f}%\n\n"

        f"PnL:\n"

        f"{stats['pnl_usdt']:.4f} USDT\n"

        f"{stats['pnl_percent']:.2f}%"
    )


async def show_statistics(message):

    stats = calculate_stats()

    text = (

        "📈 Статистика\n\n"

        f"Всего сделок: "
        f"{stats['trades']}\n"

        f"Прибыльных: "
        f"{stats['wins']}\n"

        f"Убыточных: "
        f"{stats['losses']}\n\n"

        f"WinRate: "
        f"{stats['winrate']:.2f}%\n\n"

        f"PnL:\n"

        f"{stats['pnl_usdt']:.4f} USDT\n"

        f"{stats['pnl_percent']:.2f}%"

    )

    await message.answer(
        text,
        reply_markup=keyboard
    )


async def show_daily_report(
    message
):

    await message.answer(
        build_period_report(
            "📅 Отчет за день",
            "day"
        ),
        reply_markup=keyboard
    )


async def show_weekly_report(
    message
):

    await message.answer(
        build_period_report(
            "🗓 Отчет за неделю",
            "week"
        ),
        reply_markup=keyboard
    )


async def show_monthly_report(
    message
):

    await message.answer(
        build_period_report(
            "📆 Отчет за месяц",
            "month"
        ),
        reply_markup=keyboard
    )


async def show_pnl(
    message
):

    stats = calculate_stats()

    await message.answer(

        "💹 PnL\n\n"

        f"{stats['pnl_usdt']:.4f} USDT\n"

        f"{stats['pnl_percent']:.2f}%",

        reply_markup=keyboard

    )
# =========================
# TELEGRAM SHOW FUNCTIONS
# =========================

async def show_status(message):
    sync_positions_with_okx()
    positions = get_open_positions()

    mode = "LIVE 🔥" if is_live_allowed() else "DEMO 🧪"

    await message.answer(
        f"📊 Статус\n\n"
        f"Режим: {mode}\n"
        f"Автоторговля: {'🟢 ВКЛ' if autotrade_enabled else '🔴 ВЫКЛ'}\n"
        f"Автовыбор монеты: {'✅' if auto_select_symbol else '❌'}\n\n"
        f"Текущая монета:\n{current_trade_symbol}\n\n"
        f"Открытых позиций: {len(positions)}",
        reply_markup=keyboard
    )


async def show_balance(message):
    balances = get_okx_balance()

    text = "💰 Баланс\n\n"

    for item in balances:
        if item["eq_usd"] >= 0.01:
            text += (
                f"{item['ccy']}: "
                f"{item['avail_bal']:.8f} "
                f"≈ {item['eq_usd']:.2f} USDT\n"
            )

    await message.answer(text, reply_markup=keyboard)


async def show_signal(message):
    signal = build_signal(current_trade_symbol, "15m")

    if not signal:
        await message.answer("📡 Сигнал недоступен.", reply_markup=keyboard)
        return

    await message.answer(
        f"📡 Сигнал\n\n"
        f"{signal['symbol']}\n"
        f"Цена: {signal['price']:.4f}\n"
        f"Решение: {signal['signal']}\n"
        f"Сила: {signal['score']}%\n\n"
        f"RSI: {signal['rsi']:.2f}\n"
        f"MACD: {signal['macd']:.4f}\n"
        f"EMA50: {signal['ema50']:.4f}\n"
        f"EMA200: {signal['ema200']:.4f}\n"
        f"ADX: {signal['adx']:.2f}",
        reply_markup=keyboard
    )


async def show_market(message):
    decision = multi_timeframe_decision_for_symbol(current_trade_symbol)
    signal = build_signal(current_trade_symbol, "15m")

    if not signal:
        await message.answer("🌐 Рынок недоступен.", reply_markup=keyboard)
        return

    trend = "Восходящий 📈" if signal["ema50"] > signal["ema200"] else "Нисходящий 📉"
    flat = "Нет ✅" if signal["adx"] >= risk_settings["min_adx"] else "Да ⚠️"

    await message.answer(
        f"🌐 Рынок\n\n"
        f"{current_trade_symbol}\n\n"
        f"Тренд: {trend}\n"
        f"Флэт: {flat}\n"
        f"ADX: {signal['adx']:.2f}\n\n"
        f"Итог: {decision['signal']}\n"
        f"Сила: {decision['avg_score']}%",
        reply_markup=keyboard
    )


async def show_scanner(message):
    text = "🔎 Сканер\n\n"
    found = 0

    for symbol in WATCHLIST:
        try:
            decision = multi_timeframe_decision_for_symbol(symbol)
            signal = build_signal(symbol, "15m")

            if not signal:
                continue

            if signal["ema50"] > signal["ema200"] and signal["adx"] >= risk_settings["min_adx"]:
                text += (
                    f"{symbol} | {decision['signal']} | "
                    f"{decision['avg_score']}% | ADX {signal['adx']:.1f}\n"
                )
                found += 1

        except Exception:
            continue

    if found == 0:
        text += "Подходящих монет сейчас нет."

    await message.answer(text, reply_markup=keyboard)


async def show_best_symbol(message):
    symbol, data = choose_best_symbol()

    await message.answer(
        f"🏆 Лучшая монета\n\n"
        f"{symbol}\n"
        f"Сила: {data.get('score', 0)}%\n"
        f"ADX: {data.get('adx', 0):.2f}",
        reply_markup=keyboard
    )


async def show_top3(message):
    top = get_top3_symbols()

    if not top:
        await message.answer("🥇 TOP-3 пуст.", reply_markup=keyboard)
        return

    text = "🥇 TOP-3\n\n"

    for i, row in enumerate(top, start=1):
        text += (
            f"{i}. {row['symbol']}\n"
            f"Сила: {row['score']}%\n"
            f"ADX: {row['adx']:.2f}\n\n"
        )

    await message.answer(text, reply_markup=keyboard)


async def show_positions(message):
    sync_positions_with_okx()
    positions = get_open_positions()

    if not positions:
        await message.answer("📋 Нет открытых позиций", reply_markup=keyboard)
        return

    text = "📋 Позиции\n\n"

    for symbol, position in positions.items():
        current_price = get_current_price(symbol)

        pnl = (
            (current_price - position["entry_price"])
            / position["entry_price"]
        ) * 100 if position["entry_price"] > 0 else 0

        text += (
            f"{symbol}\n"
            f"Вход: {position['entry_price']:.4f}\n"
            f"Текущая: {current_price:.4f}\n"
            f"Trailing stop: {position['stop_loss_price']:.4f}\n"
            f"PnL: {pnl:.2f}%\n\n"
        )

    await message.answer(text, reply_markup=keyboard)


async def show_history(message):
    rows = get_history(20)

    if not rows:
        await message.answer("📜 История пуста.", reply_markup=keyboard)
        return

    text = "📜 История\n\n"

    for row in rows:
        text += (
            f"{row[0]}\n"
            f"{row[1]}\n"
            f"{row[2]}\n"
            f"Цена: {row[3]:.4f}\n"
            f"Сила: {row[4]}%\n\n"
        )

    await message.answer(text, reply_markup=keyboard)


async def show_auto_status(message):
    positions = get_open_positions()

    await message.answer(
        f"🤖 Авто статус\n\n"
        f"Автоторговля: {'🟢 ВКЛ' if autotrade_enabled else '🔴 ВЫКЛ'}\n"
        f"Автовыбор монеты: {'✅' if auto_select_symbol else '❌'}\n"
        f"Текущая монета: {current_trade_symbol}\n"
        f"Открытых позиций: {len(positions)}",
        reply_markup=keyboard
    )


async def show_current_symbol(message):
    await message.answer(
        f"💱 Текущая монета\n\n{current_trade_symbol}",
        reply_markup=keyboard
    )


async def show_risk(message):
    await message.answer(
        f"🛡 Риск\n\n"
        f"BUY от: {risk_settings['buy_score']}%\n"
        f"SELL до: {risk_settings['sell_score']}%\n"
        f"ADX минимум: {risk_settings['min_adx']}\n"
        f"Trailing stop: {risk_settings['trailing_stop_percent']}%\n"
        f"Cooldown после убытка: {risk_settings['cooldown_after_loss_minutes']} мин.\n"
        f"Макс. позиций: {risk_settings['max_open_positions']}\n"
        f"Макс. сделок/день: {risk_settings['max_trades_day']}",
        reply_markup=keyboard
    )


@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer(
        "🤖 OKX ULTRA PRO MAX V6 запущен",
        reply_markup=keyboard
    )


# =========================
# TEXT ROUTER
# =========================

@dp.message()
async def text_router(message: types.Message):
    global autotrade_enabled
    global auto_select_symbol

    text = message.text.lower().strip() if message.text else ""

    if "статус" in text and "авто" not in text:
        await show_status(message)

    elif "баланс" in text:
        await show_balance(message)

    elif "сигнал" in text:
        await show_signal(message)

    elif "рынок" in text:
        await show_market(message)

    elif "сканер" in text:
        await show_scanner(message)

    elif "лучшая" in text:
        await show_best_symbol(message)

    elif "топ" in text:
        await show_top3(message)

    elif "позиц" in text:
        await show_positions(message)

    elif "история" in text:
        await show_history(message)

    elif "статист" in text:
        await show_statistics(message)

    elif "pnl" in text:
        await show_pnl(message)

    elif "днев" in text:
        await show_daily_report(message)

    elif "недель" in text:
        await show_weekly_report(message)

    elif "месяч" in text or "месяц" in text:
        await show_monthly_report(message)

    elif "риск" in text:
        await show_risk(message)

    elif "авто статус" in text:
        await show_auto_status(message)

    elif "авто монета" in text:
        auto_select_symbol = not auto_select_symbol
        save_runtime_settings()

        await message.answer(
            f"🧠 Авто монета\n\n"
            f"Автовыбор монеты: {'✅ ВКЛ' if auto_select_symbol else '❌ ВЫКЛ'}",
            reply_markup=keyboard
        )

    elif "текущ" in text:
        await show_current_symbol(message)

    elif "синх" in text:
        sync_positions_with_okx()

        await message.answer(
            "🔄 OKX синхронизирован",
            reply_markup=keyboard
        )

    elif "сброс" in text:
        clear_open_positions()
        sync_positions_with_okx()

        await message.answer(
            "♻️ Позиции очищены и синхронизированы",
            reply_markup=keyboard
        )

    elif "авто вкл" in text:
        if autotrade_enabled:
            await message.answer(
                "🟢 Автоторговля уже включена",
                reply_markup=keyboard
            )
            return

        autotrade_enabled = True
        save_runtime_settings()

        asyncio.create_task(
            autotrade_loop(message.chat.id)
        )

        await message.answer(
            "🟢 Автоторговля включена",
            reply_markup=keyboard
        )

    elif "авто выкл" in text:
        autotrade_enabled = False
        save_runtime_settings()

        await message.answer(
            "🔴 Автоторговля выключена",
            reply_markup=keyboard
        )

    elif "купить" in text or "продать" in text or "demo" in text:
        await message.answer(
            "⛔ Ручная покупка/продажа и DEMO-команды отключены.\n"
            "Бот работает только через автоторговлю.",
            reply_markup=keyboard
        )

    else:
        await message.answer(
            "❓ Команда не распознана",
            reply_markup=keyboard
        )
# =========================
# MAIN
# =========================

async def main():

    global autotrade_enabled

    # создать таблицы
    init_db()

    # загрузить настройки
    load_runtime_settings()

    # синхронизировать реальные активы OKX
    sync_positions_with_okx()

    # после перезапуска Railway
    # автоторговля всегда выключена
    autotrade_enabled = False

    print(
        "OKX ULTRA PRO MAX V6 STARTED"
    )

    await dp.start_polling(
        bot
    )


if __name__ == "__main__":

    asyncio.run(
        main()
    )
