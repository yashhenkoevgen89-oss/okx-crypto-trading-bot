import os
import json
import sqlite3
import asyncio
from datetime import datetime, date, timedelta

import pandas as pd
import requests

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

DUST_LIMIT_USDT = float(os.getenv("DUST_LIMIT_USDT", "5"))


WATCHLIST = [
    "BTC-USDT",
    "ETH-USDT",
    "SOL-USDT",
    "TON-USDT",
]


TIMEFRAMES = [
    "5m",
    "15m",
    "1H",
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

    "stop_loss_percent": 0.6,
    "take_profit_percent": 1.2,

    "trailing_stop_percent": 0.35,
    "trailing_start_profit_percent": 0.45,

    "buy_score":85,
    "sell_score": 25,
    "min_adx": 22,

    "max_open_positions": 5,
    "max_trades_day": 15,

    "auto_amount_enabled": True,
    "balance_usage_percent": 5.0,
    "min_trade_usdt": 5.0,
    "max_trade_usdt": 15.0,

    "cooldown_after_loss_minutes": 180,
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


def okx_order_success(result):

    if result == "LIVE OFF":
        return True

    try:
        if isinstance(result, dict):
            return str(result.get("code")) == "0"

        if isinstance(result, str):
            return "'code': '0'" in result or '"code": "0"' in result

    except Exception:
        pass

    return False
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

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS balance_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TEXT,
            date TEXT,
            total_usdt REAL
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
# OKX API / BALANCE
# =========================

def get_okx_balance():
    try:
        data = account_api.get_account_balance()

        if not data or "data" not in data:
            return []

        details = data["data"][0]["details"]
        balances = []

        for item in details:
            balances.append(
                {
                    "ccy": item["ccy"],
                    "eq_usd": safe_float(item.get("eqUsd", 0)),
                    "avail_bal": safe_float(item.get("availBal", 0)),
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


def get_total_balance_usdt():
    balances = get_okx_balance()
    total = 0.0

    for item in balances:
        total += safe_float(item.get("eq_usd", 0))

    return total


def get_usdt_rub_rate():
    try:
        response = requests.get(
            "https://open.er-api.com/v6/latest/USD",
            timeout=10
        )

        data = response.json()

        return safe_float(
            data.get("rates", {}).get("RUB", 80),
            80
        )

    except Exception:
        return 80.0


def save_balance_snapshot():
    total = get_total_balance_usdt()

    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO balance_snapshots (time, date, total_usdt)
        VALUES (?, ?, ?)
        """,
        (now(), today_str(), total),
    )

    conn.commit()
    conn.close()

    return total


def get_day_balance_change():
    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT total_usdt
        FROM balance_snapshots
        WHERE date = ?
        ORDER BY id ASC
        LIMIT 1
        """,
        (today_str(),),
    )

    first = cur.fetchone()

    cur.execute(
        """
        SELECT total_usdt
        FROM balance_snapshots
        WHERE date = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (today_str(),),
    )

    last = cur.fetchone()

    conn.close()

    if not first or not last:
        return None

    start_balance = safe_float(first[0])
    current_balance = safe_float(last[0])

    diff = current_balance - start_balance

    diff_percent = (
        diff / start_balance * 100
        if start_balance > 0
        else 0
    )

    return start_balance, current_balance, diff, diff_percent


def get_current_price(symbol):
    try:
        ticker = market_api.get_ticker(instId=symbol)
        return safe_float(ticker["data"][0]["last"])
    except Exception:
        return 0.0
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

        return result

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

        balance = get_okx_asset_balance(symbol)

        if balance <= 0:
            return "NO ASSET"

        result = trade_api.place_order(
            instId=symbol,
            tdMode="cash",
            side="sell",
            ordType="market",
            sz=str(balance),
        )

        return result

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

    delete_open_position(symbol)


def update_trailing_stop(symbol, current_price):

    positions = get_open_positions()

    if symbol not in positions:
        return

    position = positions[symbol]

    entry_price = position["entry_price"]

    if entry_price <= 0:
        return

    pnl_percent = (
        (current_price - entry_price)
        / entry_price
    ) * 100

    if current_price > position["highest_price"]:
        position["highest_price"] = current_price

    if pnl_percent >= risk_settings["trailing_start_profit_percent"]:

        new_stop = (
            position["highest_price"]
            * (
                1
                - risk_settings["trailing_stop_percent"] / 100
            )
        )

        if new_stop > position["stop_loss_price"]:
            position["stop_loss_price"] = new_stop

    update_open_position(
        symbol,
        position
    )
# =========================
# ORDER CHECK
# =========================

def okx_order_success(result):

    if result == "LIVE OFF":
        return False

    try:
        if isinstance(result, dict):
            return str(result.get("code")) == "0"

        if isinstance(result, str):
            return (
                '"code":"0"' in result
                or '"code": "0"' in result
                or "'code': '0'" in result
            )

    except Exception:
        return False

    return False
# =========================
# SYNC / LIMITS
# =========================

def sync_positions_with_okx():
    positions = get_open_positions()
    balances = get_okx_balance()
    real_assets = set()

    for item in balances:
        if item["ccy"] == "USDT" or item["eq_usd"] < DUST_LIMIT_USDT:
            continue

        symbol = currency_to_symbol(item["ccy"])
        real_assets.add(symbol)

    for symbol in list(positions.keys()):
        if symbol not in real_assets:
            delete_open_position(symbol)
            sell_signal_locks.discard(symbol)

    for symbol in real_assets:
        if symbol in positions:
            continue

        current_price = get_current_price(symbol)
        amount_usdt = 0

        for item in balances:
            if currency_to_symbol(item["ccy"]) == symbol:
                amount_usdt = item["eq_usd"]
                break

        if current_price > 0 and amount_usdt >= DUST_LIMIT_USDT:
            save_open_position(
                symbol,
                current_price,
                amount_usdt,
                current_price * 0.98,
                current_price * 1.035,
                current_price,
            )


def unlock_missing_positions():
    positions = get_open_positions()

    for symbol in list(sell_signal_locks):
        if symbol not in positions:
            sell_signal_locks.discard(symbol)


def can_trade_today():
    if trades_today_count() >= risk_settings["max_trades_day"]:
        return False, "Достигнут лимит сделок"

    return True, "OK"


def can_open_new_position(symbol):
    positions = get_open_positions()

    if symbol in positions:
        return False, "Позиция уже существует"

    if len(positions) >= risk_settings["max_open_positions"]:
        return False, "Достигнут лимит позиций"

    in_cd, reason = is_symbol_in_cooldown(symbol)

    if in_cd:
        return False, reason

    allowed, reason = can_trade_today()

    if not allowed:
        return False, reason

    return True, "OK"


# =========================
# INDICATORS / SIGNALS
# =========================

def get_candles(symbol, timeframe="15m", limit=300):
    try:
        result = market_api.get_candlesticks(
            instId=symbol,
            bar=timeframe,
            limit=str(limit),
        )

        if not result or "data" not in result:
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

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)

        return df

    except Exception:
        return pd.DataFrame()


def add_indicators(df):
    if len(df) < 200:
        return df

    close = df["close"]

    df["ema50"] = close.ewm(span=50).mean()
    df["ema200"] = close.ewm(span=200).mean()

    delta = close.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()

    rs = avg_gain / avg_loss.replace(0, 1e-9)
    df["rsi"] = 100 - (100 / (1 + rs))

    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()

    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9).mean()

    tr1 = df["high"] - df["low"]
    tr2 = abs(df["high"] - close.shift())
    tr3 = abs(df["low"] - close.shift())

    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = true_range.rolling(14).mean()

    plus_dm = df["high"].diff()
    minus_dm = -df["low"].diff()

    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)

    tr14 = true_range.rolling(14).sum()

    plus_di = 100 * (plus_dm.rolling(14).sum() / tr14.replace(0, 1e-9))
    minus_di = 100 * (minus_dm.rolling(14).sum() / tr14.replace(0, 1e-9))

    dx = (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, 1e-9)) * 100

    df["adx"] = dx.rolling(14).mean().fillna(0)

    return df
def build_signal(symbol, timeframe="15m"):
    df = get_candles(symbol, timeframe)

    if len(df) < 200:
        return None

    df = add_indicators(df)

    last = df.iloc[-1]
    prev = df.iloc[-2]

    score = 0

    if 45 <= last["rsi"] <= 72:
        score += 20

    if last["macd"] > last["macd_signal"]:
        score += 20

    if last["ema50"] > last["ema200"]:
        score += 25

    if last["ema50"] > prev["ema50"]:
        score += 15

    if last["adx"] >= risk_settings["min_adx"]:
        score += 20

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
        "ema50_prev": safe_float(prev["ema50"]),
        "adx": safe_float(last["adx"]),
        "atr": safe_float(last["atr"]),
    }


def multi_timeframe_decision_for_symbol(symbol):
    results = []

    for tf in TIMEFRAMES:
        signal_data = build_signal(symbol, tf)

        if signal_data:
            results.append(signal_data)

    if not results:
        return {
            "signal": "HOLD",
            "avg_score": 0,
            "price": 0,
        }

    avg_score = sum(x["score"] for x in results) / len(results)

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


def btc_market_filter_ok():
    btc = build_signal("BTC-USDT", "15m")

    if not btc:
        return False, "BTC данные недоступны"

    if btc["ema50"] <= btc["ema200"]:
        return False, "BTC не в восходящем тренде"

    if btc["ema50"] <= btc["ema50_prev"]:
        return False, "EMA50 BTC не растет"

    if btc["adx"] < risk_settings["min_adx"]:
        return False, "BTC во флэте"

    if btc["signal"] == "SELL":
        return False, "BTC показывает SELL"

    return True, "BTC рынок OK"


def is_strong_buy(symbol, decision, signal_data):
    btc_ok, btc_reason = btc_market_filter_ok()

    if symbol != "BTC-USDT" and not btc_ok:
        return False, btc_reason

    if decision["signal"] != "BUY":
        return False, "Нет BUY"

    if decision["avg_score"] < risk_settings["buy_score"]:
        return False, "Слабый сигнал"

    if signal_data["ema50"] <= signal_data["ema200"]:
        return False, "Нет восходящего тренда"

    if signal_data["ema50"] <= signal_data["ema50_prev"]:
        return False, "EMA50 не растет"

    if signal_data["adx"] < risk_settings["min_adx"]:
        return False, "ADX слабый / флэт"

    return True, "OK"


# =========================
# SYMBOL SELECTION
# =========================

def choose_best_symbol():
    candidates = []

    for symbol in WATCHLIST:
        try:
            decision = multi_timeframe_decision_for_symbol(symbol)
            signal_data = build_signal(symbol, "15m")

            if not signal_data:
                continue

            candidates.append(
                {
                    "symbol": symbol,
                    "score": decision["avg_score"],
                    "adx": signal_data["adx"],
                    "signal": decision["signal"],
                }
            )

        except Exception as e:
            print(f"choose_best_symbol error {symbol}: {e}")
            continue

    if not candidates:
        return (
            TRADE_SYMBOL,
            {
                "score": 0,
                "adx": 0,
                "signal": "HOLD",
            },
        )

    candidates = sorted(
        candidates,
        key=lambda x: (x["score"], x["adx"]),
        reverse=True,
    )

    best = candidates[0]

    return best["symbol"], best


def get_top3_symbols():
    candidates = []

    for symbol in WATCHLIST:
        try:
            decision = multi_timeframe_decision_for_symbol(symbol)
            signal_data = build_signal(symbol, "15m")

            if not signal_data:
                continue

            candidates.append(
                {
                    "symbol": symbol,
                    "score": decision["avg_score"],
                    "adx": signal_data["adx"],
                    "signal": decision["signal"],
                }
            )

        except Exception as e:
            print(f"get_top3_symbols error {symbol}: {e}")
            continue

    candidates = sorted(
        candidates,
        key=lambda x: (x["score"], x["adx"]),
        reverse=True,
    )

    return candidates[:3]
# =========================
# STATISTICS
# =========================

def calculate_stats(period="all"):

    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            time,
            date,
            symbol,
            entry_price,
            exit_price,
            amount_usdt,
            pnl_percent,
            pnl_usdt,
            reason
        FROM closed_trades
        ORDER BY id DESC
        """
    )

    rows = cur.fetchall()

    conn.close()

    today = date.today()
    week_start = today - timedelta(days=7)
    month_start = today.replace(day=1)

    filtered = []

    for row in rows:

        trade_date_raw = row[1]

        try:
            trade_date = datetime.strptime(
                trade_date_raw,
                "%Y-%m-%d"
            ).date()

        except Exception:

            try:
                trade_date = datetime.strptime(
                    row[0],
                    "%Y-%m-%d %H:%M:%S"
                ).date()

            except Exception:
                continue

        if period == "day":
            if trade_date != today:
                continue

        elif period == "week":
            if trade_date < week_start or trade_date > today:
                continue

        elif period == "month":
            if trade_date < month_start or trade_date > today:
                continue

        filtered.append(row)

    total = len(filtered)

    wins = len(
        [
            row
            for row in filtered
            if safe_float(row[7]) > 0
        ]
    )

    losses = total - wins

    pnl_usdt = sum(
        safe_float(row[7])
        for row in filtered
    )

    pnl_percent = sum(
        safe_float(row[6])
        for row in filtered
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


def build_period_report(title, period):

    stats = calculate_stats(period)

    rub_rate = get_usdt_rub_rate()

    pnl_rub = (
        stats["pnl_usdt"]
        * rub_rate
    )

    return (

        f"{title}\n\n"

        f"Сделок: {stats['trades']}\n"

        f"Прибыльных: {stats['wins']}\n"

        f"Убыточных: {stats['losses']}\n\n"

        f"WinRate: {stats['winrate']:.2f}%\n\n"

        f"PnL:\n"

        f"{stats['pnl_usdt']:.4f} USDT\n"

        f"{pnl_rub:,.0f} ₽\n"

        f"{stats['pnl_percent']:.2f}%"

    )


# =========================
# TELEGRAM SHOW FUNCTIONS
# =========================

async def show_status(message):

    sync_positions_with_okx()

    positions = get_open_positions()

    mode = (
        "LIVE 🔥"
        if is_live_allowed()
        else "DEMO 🧪"
    )

    await message.answer(

        f"📊 Статус\n\n"

        f"Режим: {mode}\n"

        f"Автоторговля: "
        f"{'🟢 ВКЛ' if autotrade_enabled else '🔴 ВЫКЛ'}\n"

        f"Автовыбор монеты: "
        f"{'✅' if auto_select_symbol else '❌'}\n\n"

        f"Текущая монета:\n"
        f"{current_trade_symbol}\n\n"

        f"Открытых позиций: "
        f"{len(positions)}",

        reply_markup=keyboard
    )


async def show_balance(message):

    total = save_balance_snapshot()

    rub_rate = get_usdt_rub_rate()

    total_rub = (
        total
        * rub_rate
    )

    balances = get_okx_balance()

    text = (

        "💰 Баланс OKX\n\n"

        f"Общий баланс:\n"

        f"{total:.2f} USDT\n"

        f"{total_rub:,.0f} ₽\n\n"

    )

    for item in balances:

        if item["eq_usd"] >= 0.01:

            asset_rub = (
                item["eq_usd"]
                * rub_rate
            )

            text += (

                f"{item['ccy']}\n"

                f"{item['avail_bal']:.8f}\n"

                f"≈ {item['eq_usd']:.2f} USDT\n"

                f"≈ {asset_rub:,.0f} ₽\n\n"

            )

    day = get_day_balance_change()

    if day:

        (
            start_balance,
            current_balance,
            diff,
            diff_percent
        ) = day

        diff_rub = (
            diff
            * rub_rate
        )

        emoji = (
            "🟢"
            if diff >= 0
            else "🔴"
        )

        text += (

            "📅 Изменение за день\n\n"

            f"{emoji} "

            f"{diff:+.2f} USDT\n"

            f"{diff_rub:+,.0f} ₽\n"

            f"({diff_percent:+.2f}%)"

        )

    await message.answer(
        text,
        reply_markup=keyboard
    )
async def show_signal(message):

    signal = build_signal(
        current_trade_symbol,
        "15m"
    )

    if not signal:

        await message.answer(
            "📡 Сигнал недоступен.",
            reply_markup=keyboard
        )

        return

    await message.answer(

        f"📡 Сигнал\n\n"

        f"{signal['symbol']}\n"

        f"Цена: {signal['price']:.4f}\n"

        f"Решение: {signal['signal']}\n"

        f"Сила: {signal['score']}%\n\n"

        f"RSI: {signal['rsi']:.2f}\n"

        f"ADX: {signal['adx']:.2f}",

        reply_markup=keyboard
    )


async def show_best_symbol(message):

    symbol, data = choose_best_symbol()

    await message.answer(

        f"🏆 Лучшая монета\n\n"

        f"{symbol}\n"

        f"Сила: {data.get('score',0)}%\n"

        f"ADX: {data.get('adx',0):.2f}",

        reply_markup=keyboard
    )


async def show_top3(message):

    top = get_top3_symbols()

    if not top:

        await message.answer(
            "🥇 TOP-3 пуст.",
            reply_markup=keyboard
        )

        return

    text = "🥇 TOP-3\n\n"

    for i, row in enumerate(top, start=1):

        text += (

            f"{i}. {row['symbol']}\n"

            f"Сила: {row['score']}%\n"

            f"ADX: {row['adx']:.2f}\n\n"

        )

    await message.answer(
        text,
        reply_markup=keyboard
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

                    current_price = get_current_price(symbol)

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
                    ) * 100 if position["entry_price"] > 0 else 0

                    # =====================
                    # TRAILING STOP
                    # =====================

                    if current_price <= position["stop_loss_price"]:

                        if symbol in sell_signal_locks:
                            continue

                        sell_signal_locks.add(symbol)

                        result = place_market_sell(
                            symbol,
                            position["amount_usdt"],
                            current_price
                        )

                        if okx_order_success(result):

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

                        sell_signal_locks.discard(symbol)

                        continue

                    # =====================
                    # SELL SIGNAL
                    # =====================

                    decision = (
                        multi_timeframe_decision_for_symbol(
                            symbol
                        )
                    )

                    if decision["signal"] == "SELL":

                        if symbol in sell_signal_locks:
                            continue

                        sell_signal_locks.add(symbol)

                        result = place_market_sell(
                            symbol,
                            position["amount_usdt"],
                            current_price
                        )

                        if okx_order_success(result):

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

                        sell_signal_locks.discard(symbol)

                except Exception as e:

                    await bot.send_message(
                        chat_id,
                        f"⚠️ Ошибка позиции\n\n"
                        f"{symbol}\n\n{e}"
                    )

            # =====================
            # OPEN NEW POSITION
            # =====================

            positions = get_open_positions()

            if len(positions) < risk_settings["max_open_positions"]:

                if auto_select_symbol:

                    symbol, best = choose_best_symbol()

                else:

                    symbol = current_trade_symbol

                current_trade_symbol = symbol

                decision = multi_timeframe_decision_for_symbol(
                    symbol
                )

                signal_data = build_signal(
                    symbol,
                    "15m"
                )

                if signal_data:

                    buy_ok, buy_reason = is_strong_buy(
                        symbol,
                        decision,
                        signal_data
                    )

                    if buy_ok:

                        allowed, reason = can_open_new_position(
                            symbol
                        )

                        if allowed:

                            amount = get_trade_amount_usdt()

                            result = place_market_buy(
                                symbol,
                                amount
                            )

                            # =====================
                            # BUY SUCCESS
                            # =====================

                            if okx_order_success(result):

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
                                    f"ADX: {signal_data['adx']:.2f}\n"
                                    f"BTC фильтр: OK\n"
                                    f"Сумма: {amount:.2f} USDT\n"
                                    f"Trailing включится после "
                                    f"+{risk_settings['trailing_start_profit_percent']}%"
                                )

                            else:

                                await bot.send_message(
                                    chat_id,
                                    f"⚠️ BUY отклонён\n\n"
                                    f"{symbol}\n\n"
                                    f"{result}"
                                )

            save_runtime_settings()

        except Exception as e:

            await bot.send_message(
                chat_id,
                f"❌ Ошибка автоторговли\n\n{e}"
            )

        await asyncio.sleep(
            AUTO_INTERVAL
        )

# =========================
# TELEGRAM EXTRA FUNCTIONS
# =========================

async def show_market(message):

    decision = multi_timeframe_decision_for_symbol(
        current_trade_symbol
    )

    signal = build_signal(
        current_trade_symbol,
        "15m"
    )

    if not signal:

        await message.answer(
            "🌐 Рынок недоступен.",
            reply_markup=keyboard
        )

        return

    trend = (
        "📈 Восходящий"
        if signal["ema50"] > signal["ema200"]
        else "📉 Нисходящий"
    )

    flat = (
        "Нет ✅"
        if signal["adx"] >= risk_settings["min_adx"]
        else "Да ⚠️"
    )

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

            found += 1

            text += (
                f"{symbol}\n"
                f"Итог: {decision['signal']}\n"
                f"Сила: {decision['avg_score']}%\n"
                f"ADX: {signal['adx']:.2f}\n\n"
            )

        except Exception as e:
            print(f"scanner error {symbol}: {e}")
            continue

    if found == 0:
        text += "Нет данных по монетам."

    await message.answer(
        text,
        reply_markup=keyboard
    )


async def show_positions(message):

    sync_positions_with_okx()

    positions = get_open_positions()

    if not positions:

        await message.answer(
            "📋 Нет открытых позиций",
            reply_markup=keyboard
        )

        return

    text = "📋 Позиции\n\n"

    for symbol, position in positions.items():

        current_price = get_current_price(
            symbol
        )

        pnl = (
            (
                current_price
                - position["entry_price"]
            )
            / position["entry_price"]
        ) * 100 if position["entry_price"] > 0 else 0

        text += (

            f"{symbol}\n"

            f"Вход: {position['entry_price']:.4f}\n"

            f"Текущая: {current_price:.4f}\n"

            f"Trailing stop: {position['stop_loss_price']:.4f}\n"

            f"PnL: {pnl:.2f}%\n\n"

        )

    await message.answer(
        text,
        reply_markup=keyboard
    )


async def show_history(message):

    rows = get_history(20)

    if not rows:

        await message.answer(
            "📜 История пуста.",
            reply_markup=keyboard
        )

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

    await message.answer(
        text,
        reply_markup=keyboard
    )


async def show_auto_status(message):

    positions = get_open_positions()

    await message.answer(

        f"🤖 Авто статус\n\n"

        f"Автоторговля: "
        f"{'🟢 ВКЛ' if autotrade_enabled else '🔴 ВЫКЛ'}\n"

        f"Автовыбор монеты: "
        f"{'✅' if auto_select_symbol else '❌'}\n"

        f"Текущая монета:\n"
        f"{current_trade_symbol}\n\n"

        f"Открытых позиций: "
        f"{len(positions)}",

        reply_markup=keyboard
    )


async def show_current_symbol(message):

    await message.answer(

        f"💱 Текущая монета\n\n"

        f"{current_trade_symbol}",

        reply_markup=keyboard
    )


async def show_risk(message):

    await message.answer(

        f"🛡 Риск\n\n"

        f"BUY от:\n"
        f"{risk_settings['buy_score']}%\n\n"

        f"SELL до:\n"
        f"{risk_settings['sell_score']}%\n\n"

        f"ADX минимум:\n"
        f"{risk_settings['min_adx']}\n\n"

        f"Trailing stop:\n"
        f"{risk_settings['trailing_stop_percent']}%\n\n"

        f"Макс. позиций:\n"
        f"{risk_settings['max_open_positions']}\n\n"

        f"Макс. сделок/день:\n"
        f"{risk_settings['max_trades_day']}",

        reply_markup=keyboard
    )

# =========================
# REPORTS / STATISTICS
# =========================

async def show_statistics(message):

    stats = calculate_stats("all")

    rub_rate = get_usdt_rub_rate()

    pnl_rub = (
        stats["pnl_usdt"]
        * rub_rate
    )

    await message.answer(

        f"📈 Статистика\n\n"

        f"Всего сделок:\n"
        f"{stats['trades']}\n\n"

        f"Прибыльных:\n"
        f"{stats['wins']}\n\n"

        f"Убыточных:\n"
        f"{stats['losses']}\n\n"

        f"WinRate:\n"
        f"{stats['winrate']:.2f}%\n\n"

        f"PnL:\n"
        f"{stats['pnl_usdt']:.4f} USDT\n"

        f"{pnl_rub:,.0f} ₽\n"

        f"{stats['pnl_percent']:.2f}%",

        reply_markup=keyboard
    )


async def show_pnl(message):

    stats = calculate_stats("all")

    rub_rate = get_usdt_rub_rate()

    pnl_rub = (
        stats["pnl_usdt"]
        * rub_rate
    )

    await message.answer(

        f"💹 PnL\n\n"

        f"{stats['pnl_usdt']:.4f} USDT\n"

        f"{pnl_rub:,.0f} ₽\n"

        f"{stats['pnl_percent']:.2f}%",

        reply_markup=keyboard
    )


async def show_daily_report(message):

    await message.answer(

        build_period_report(
            "📅 Отчет за день",
            "day"
        ),

        reply_markup=keyboard
    )


async def show_weekly_report(message):

    await message.answer(

        build_period_report(
            "🗓 Отчет за последние 7 дней",
            "week"
        ),

        reply_markup=keyboard
    )


async def show_monthly_report(message):

    await message.answer(

        build_period_report(
            "📆 Отчет за текущий месяц",
            "month"
        ),

        reply_markup=keyboard
    )

# =========================
# START
# =========================

@dp.message(Command("start"))
async def start_cmd(message: types.Message):

    await message.answer(
        "🤖 OKX ULTRA PRO MAX V7.1 запущен",
        reply_markup=keyboard
    )


# =========================
# TEXT ROUTER
# =========================

@dp.message()
async def text_router(message: types.Message):

    global autotrade_enabled
    global auto_select_symbol

    text = message.text or ""

    if "📊" in text or "статус" in text.lower() and "авто" not in text.lower():
        await show_status(message)

    elif "💰" in text or "баланс" in text.lower():
        await show_balance(message)

    elif "📡" in text or "сигнал" in text.lower():
        await show_signal(message)

    elif "🌐" in text or "рынок" in text.lower():
        await show_market(message)

    elif "🔎" in text or "сканер" in text.lower():
        await show_scanner(message)

    elif "🏆" in text or "лучшая" in text.lower():
        await show_best_symbol(message)

    elif "🥇" in text or "топ" in text.lower():
        await show_top3(message)

    elif "📋" in text or "позиц" in text.lower():
        await show_positions(message)

    elif "📜" in text or "история" in text.lower():
        await show_history(message)

    elif "🤖" in text or "авто статус" in text.lower():
        await show_auto_status(message)

    elif "💱" in text or "текущ" in text.lower():
        await show_current_symbol(message)

    elif "🛡" in text or "риск" in text.lower():
        await show_risk(message)

    elif "📈" in text or "статист" in text.lower():
        await show_statistics(message)

    elif "💹" in text or "pnl" in text.lower():
        await show_pnl(message)

    elif "📅" in text or "днев" in text.lower():
        await show_daily_report(message)

    elif "🗓" in text or "недель" in text.lower():
        await show_weekly_report(message)

    elif "📆" in text or "месяч" in text.lower() or "месяц" in text.lower():
        await show_monthly_report(message)

    elif "🧠" in text or "авто монета" in text.lower():

        auto_select_symbol = not auto_select_symbol

        save_runtime_settings()

        await message.answer(
            f"🧠 Авто монета\n\n"
            f"{'✅ ВКЛ' if auto_select_symbol else '❌ ВЫКЛ'}",
            reply_markup=keyboard
        )

    elif "🟢" in text or "авто вкл" in text.lower():

        if not autotrade_enabled:

            autotrade_enabled = True

            save_runtime_settings()

            asyncio.create_task(
                autotrade_loop(message.chat.id)
            )

        await message.answer(
            "🟢 Автоторговля включена",
            reply_markup=keyboard
        )

    elif "🔴" in text or "авто выкл" in text.lower():

        autotrade_enabled = False

        save_runtime_settings()

        await message.answer(
            "🔴 Автоторговля выключена",
            reply_markup=keyboard
        )

    elif "🔄" in text or "синх" in text.lower():

        sync_positions_with_okx()

        await message.answer(
            "🔄 OKX синхронизирован",
            reply_markup=keyboard
        )

    elif "♻️" in text or "сброс" in text.lower():

        clear_open_positions()

        sync_positions_with_okx()

        await message.answer(
            "♻️ Позиции очищены",
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

    init_db()

    load_runtime_settings()

    sync_positions_with_okx()

    autotrade_enabled = False

    print(
        "OKX ULTRA PRO MAX V7.1 STARTED"
    )

    await dp.start_polling(
        bot
    )


if __name__ == "__main__":

    asyncio.run(
        main()
    )
