import os
import json
import sqlite3
import asyncio
from datetime import datetime, date

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

OKX_FLAG = os.getenv("OKX_FLAG", "1")
LIVE_TRADING_ENABLED = os.getenv("LIVE_TRADING_ENABLED", "NO")

TRADE_SYMBOL = os.getenv("TRADE_SYMBOL", "BTC-USDT")
TRADE_AMOUNT_USDT = float(os.getenv("TRADE_AMOUNT_USDT", "5"))

AUTO_INTERVAL = int(os.getenv("AUTO_INTERVAL", "300"))
DB_FILE = "bot.db"

BUY_SCORE = int(os.getenv("BUY_SCORE", "68"))
SELL_SCORE = int(os.getenv("SELL_SCORE", "35"))

MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "3"))
MAX_TRADES_DAY = int(os.getenv("MAX_TRADES_DAY", "10"))
MAX_DAILY_LOSS_PERCENT = float(os.getenv("MAX_DAILY_LOSS_PERCENT", "5"))

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

risk_settings = {
    "amount_usdt": TRADE_AMOUNT_USDT,
    "max_amount_usdt": 20.0,
    "stop_loss_percent": 2.0,
    "take_profit_percent": 4.0,
    "trailing_stop_percent": 1.5,
    "buy_score": BUY_SCORE,
    "sell_score": SELL_SCORE,
    "max_open_positions": MAX_OPEN_POSITIONS,
    "max_trades_day": MAX_TRADES_DAY,
    "max_daily_loss_percent": MAX_DAILY_LOSS_PERCENT,
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
        [KeyboardButton(text="🟢 Купить DEMO"), KeyboardButton(text="🔴 Продать DEMO")],
        [KeyboardButton(text="🟢 Купить LIVE"), KeyboardButton(text="🔴 Продать LIVE")],
        [KeyboardButton(text="🟢 Авто ВКЛ"), KeyboardButton(text="🔴 Авто ВЫКЛ")],
        [KeyboardButton(text="🧠 Авто монета"), KeyboardButton(text="💱 Текущая монета")],
        [KeyboardButton(text="🤖 Авто статус"), KeyboardButton(text="🛡 Риск")],
        [KeyboardButton(text="📜 История"), KeyboardButton(text="📈 Статистика")],
        [KeyboardButton(text="💹 PnL"), KeyboardButton(text="♻️ Сброс позиций")],
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


def is_demo():
    return str(OKX_FLAG) == "1"


def is_live_allowed():
    return str(OKX_FLAG) == "0" and LIVE_TRADING_ENABLED == "YES"


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


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
            partial_1_done INTEGER,
            partial_2_done INTEGER,
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
            str(result)[:1000],
        ),
    )

    conn.commit()
    conn.close()


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
            entry_price,
            exit_price,
            amount_usdt,
            pnl_percent,
            pnl_usdt,
            reason,
        ),
    )

    conn.commit()
    conn.close()
# =========================
# DATABASE POSITIONS / STATS
# =========================

def save_open_position(
    symbol,
    entry_price,
    amount_usdt,
    stop_loss_price,
    take_profit_price,
    highest_price
):
    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT OR REPLACE INTO open_positions
        (
            symbol,
            entry_price,
            amount_usdt,
            stop_loss_price,
            take_profit_price,
            highest_price,
            partial_1_done,
            partial_2_done,
            time
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            symbol,
            entry_price,
            amount_usdt,
            stop_loss_price,
            take_profit_price,
            highest_price,
            0,
            0,
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
        SET
            entry_price = ?,
            amount_usdt = ?,
            stop_loss_price = ?,
            take_profit_price = ?,
            highest_price = ?,
            partial_1_done = ?,
            partial_2_done = ?
        WHERE symbol = ?
        """,
        (
            position["entry_price"],
            position["amount_usdt"],
            position["stop_loss_price"],
            position["take_profit_price"],
            position["highest_price"],
            position["partial_1_done"],
            position["partial_2_done"],
            symbol,
        ),
    )

    conn.commit()
    conn.close()


def delete_open_position(symbol):
    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        "DELETE FROM open_positions WHERE symbol = ?",
        (symbol,),
    )

    conn.commit()
    conn.close()


def get_open_positions():
    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            symbol,
            entry_price,
            amount_usdt,
            stop_loss_price,
            take_profit_price,
            highest_price,
            partial_1_done,
            partial_2_done,
            time
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
            "partial_1_done": int(row[6]),
            "partial_2_done": int(row[7]),
            "time": row[8],
        }

    return positions


def clear_open_positions():
    conn = db_connect()
    cur = conn.cursor()

    cur.execute("DELETE FROM open_positions")

    conn.commit()
    conn.close()


def get_history(limit=10):
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


def get_closed_trades(limit=300):
    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            time,
            symbol,
            entry_price,
            exit_price,
            amount_usdt,
            pnl_percent,
            pnl_usdt,
            reason
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


def pnl_today_percent():
    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT COALESCE(SUM(pnl_percent), 0)
        FROM closed_trades
        WHERE date = ?
        """,
        (today_str(),),
    )

    value = cur.fetchone()[0]
    conn.close()

    return safe_float(value)


def total_pnl_percent():
    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT COALESCE(SUM(pnl_percent), 0)
        FROM closed_trades
        """
    )

    value = cur.fetchone()[0]
    conn.close()

    return safe_float(value)


def total_pnl_usdt():
    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT COALESCE(SUM(pnl_usdt), 0)
        FROM closed_trades
        """
    )

    value = cur.fetchone()[0]
    conn.close()

    return safe_float(value)


def winrate():
    trades = get_closed_trades(1000)

    if not trades:
        return 0.0

    wins = 0

    for trade in trades:
        pnl_usdt = safe_float(trade[6])
        if pnl_usdt > 0:
            wins += 1

    return wins / len(trades) * 100


def can_trade_today():
    if trades_today_count() >= risk_settings["max_trades_day"]:
        return False, "Достигнут дневной лимит сделок"

    if pnl_today_percent() <= -abs(risk_settings["max_daily_loss_percent"]):
        return False, "Достигнут дневной лимит убытка"

    return True, "OK"


def can_open_new_position(symbol):
    positions = get_open_positions()

    if symbol in positions:
        return False, "По этой монете уже есть открытая позиция"

    if len(positions) >= risk_settings["max_open_positions"]:
        return False, "Достигнут лимит открытых позиций"

    allowed, reason = can_trade_today()

    if not allowed:
        return False, reason

    return True, "OK"
# =========================
# INDICATORS
# =========================

def calculate_rsi(series, period=14):
    delta = series.diff()

    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss.replace(0, 1e-9)
    rsi = 100 - (100 / (1 + rs))

    return rsi.fillna(50)


def add_indicators(df):
    df["ema9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

    df["rsi"] = calculate_rsi(df["close"])

    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()

    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()

    df["bb_mid"] = df["close"].rolling(20).mean()
    df["bb_std"] = df["close"].rolling(20).std()
    df["bb_upper"] = df["bb_mid"] + 2 * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - 2 * df["bb_std"]

    df["vol_avg"] = df["vol"].rolling(20).mean()

    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()

    true_range = pd.concat(
        [high_low, high_close, low_close],
        axis=1
    ).max(axis=1)

    df["atr"] = true_range.rolling(14).mean().fillna(0)

    return df


# =========================
# MARKET DATA / SIGNALS
# =========================

def calculate_score(data):
    score = 50

    close = data["close"]
    ema9 = data["ema9"]
    ema21 = data["ema21"]
    ema50 = data["ema50"]
    ema200 = data["ema200"]
    rsi = data["rsi"]
    macd = data["macd"]
    macd_signal = data["macd_signal"]
    vol = data["vol"]
    vol_avg = data["vol_avg"]
    bb_upper = data["bb_upper"]
    bb_lower = data["bb_lower"]

    if ema9 > ema21:
        score += 10
    else:
        score -= 10

    if ema50 > ema200:
        score += 15
    else:
        score -= 15

    if close > ema50:
        score += 5
    else:
        score -= 5

    if macd > macd_signal:
        score += 10
    else:
        score -= 10

    if rsi < 30:
        score += 15
    elif rsi > 70:
        score -= 15
    elif 45 <= rsi <= 60:
        score += 5

    if vol_avg > 0 and vol > vol_avg:
        score += 5

    if bb_lower > 0 and close <= bb_lower:
        score += 10

    if bb_upper > 0 and close >= bb_upper:
        score -= 10

    score = max(0, min(100, int(score)))

    if score >= risk_settings["buy_score"]:
        signal = "BUY"
    elif score <= risk_settings["sell_score"]:
        signal = "SELL"
    else:
        signal = "HOLD"

    return score, signal


def get_market_data(symbol=None, bar="15m", limit=250):
    symbol = symbol or TRADE_SYMBOL

    result = market_api.get_candlesticks(
        instId=symbol,
        bar=bar,
        limit=str(limit),
    )

    candles = result.get("data", [])

    if not candles:
        raise Exception(f"OKX не вернул свечи для {symbol}")

    df = pd.DataFrame(
        candles,
        columns=[
            "ts",
            "open",
            "high",
            "low",
            "close",
            "vol",
            "volCcy",
            "volCcyQuote",
            "confirm",
        ],
    )

    for col in ["open", "high", "low", "close", "vol"]:
        df[col] = df[col].astype(float)

    df = df.iloc[::-1].reset_index(drop=True)
    df = add_indicators(df)

    return df


def build_signal(symbol=None, bar="15m"):
    symbol = symbol or TRADE_SYMBOL

    df = get_market_data(symbol, bar)
    last = df.iloc[-1]

    data = {
        "close": safe_float(last["close"]),
        "vol": safe_float(last["vol"]),
        "ema9": safe_float(last["ema9"]),
        "ema21": safe_float(last["ema21"]),
        "ema50": safe_float(last["ema50"]),
        "ema200": safe_float(last["ema200"]),
        "rsi": safe_float(last["rsi"]),
        "macd": safe_float(last["macd"]),
        "macd_signal": safe_float(last["macd_signal"]),
        "bb_upper": safe_float(last.get("bb_upper", 0)),
        "bb_lower": safe_float(last.get("bb_lower", 0)),
        "vol_avg": safe_float(last.get("vol_avg", 0)),
        "atr": safe_float(last.get("atr", 0)),
    }

    score, signal = calculate_score(data)

    if data["ema9"] > data["ema21"] and data["ema50"] > data["ema200"]:
        trend = "сильный восходящий"
    elif data["ema9"] > data["ema21"]:
        trend = "восходящий"
    elif data["ema9"] < data["ema21"] and data["ema50"] < data["ema200"]:
        trend = "сильный нисходящий"
    else:
        trend = "нисходящий"

    return {
        "symbol": symbol,
        "bar": bar,
        "price": data["close"],
        "rsi": data["rsi"],
        "ema9": data["ema9"],
        "ema21": data["ema21"],
        "ema50": data["ema50"],
        "ema200": data["ema200"],
        "macd": data["macd"],
        "macd_signal": data["macd_signal"],
        "atr": data["atr"],
        "score": score,
        "signal": signal,
        "trend": trend,
    }
# =========================
# FORMAT SIGNAL
# =========================

def format_signal(result):

    return (
        f"📡 {result['symbol']} | {result['bar']}\n\n"

        f"Цена: {result['price']:.4f}\n"

        f"Тренд: {result['trend']}\n"

        f"RSI: {result['rsi']:.2f}\n"

        f"EMA9: {result['ema9']:.4f}\n"

        f"EMA21: {result['ema21']:.4f}\n"

        f"EMA50: {result['ema50']:.4f}\n"

        f"EMA200: {result['ema200']:.4f}\n"

        f"MACD: {result['macd']:.4f}\n"

        f"MACD Signal: {result['macd_signal']:.4f}\n"

        f"ATR: {result['atr']:.4f}\n\n"

        f"Сила сигнала: {result['score']}%\n"

        f"Решение: {result['signal']}"
    )


# =========================
# MULTI TIMEFRAME
# =========================

def multi_timeframe_decision_for_symbol(symbol):

    results = []

    for timeframe in TIMEFRAMES:

        signal = build_signal(
            symbol,
            timeframe
        )

        results.append(signal)

    buy_count = sum(
        1 for item in results
        if item["signal"] == "BUY"
    )

    sell_count = sum(
        1 for item in results
        if item["signal"] == "SELL"
    )

    avg_score = int(
        sum(item["score"] for item in results)
        / len(results)
    )

    if (
        buy_count >= 2
        and avg_score >= risk_settings["buy_score"]
    ):
        final_signal = "BUY"

    elif (
        sell_count >= 2
        and avg_score <= risk_settings["sell_score"]
    ):
        final_signal = "SELL"

    else:
        final_signal = "HOLD"

    return {
        "symbol": symbol,
        "signal": final_signal,
        "avg_score": avg_score,
        "price": results[1]["price"],
        "results": results,
    }


# =========================
# MARKET SCANNER
# =========================

def scan_market():

    results = []

    for symbol in WATCHLIST:

        try:

            signal = build_signal(
                symbol,
                "15m"
            )

            results.append(signal)

        except Exception:
            pass

    results = sorted(
        results,
        key=lambda x: x["score"],
        reverse=True
    )

    return results


# =========================
# TOP-3
# =========================

def get_top3():

    results = scan_market()

    return results[:3]


# =========================
# BEST SYMBOL
# =========================

def choose_best_symbol():

    results = scan_market()

    if not results:

        return TRADE_SYMBOL, None

    best = results[0]

    return (
        best["symbol"],
        best
    )


# =========================
# POSITION HELPERS
# =========================

def open_position(
    symbol,
    entry_price,
    amount_usdt
):

    stop_loss_price = entry_price * (
        1 -
        risk_settings["stop_loss_percent"] / 100
    )

    take_profit_price = entry_price * (
        1 +
        risk_settings["take_profit_percent"] / 100
    )

    highest_price = entry_price

    save_open_position(
        symbol,
        entry_price,
        amount_usdt,
        stop_loss_price,
        take_profit_price,
        highest_price
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
        reason
    )

    delete_open_position(symbol)


# =========================
# TRAILING STOP
# =========================

def update_trailing_stop(
    symbol,
    current_price
):

    positions = get_open_positions()

    if symbol not in positions:
        return

    position = positions[symbol]

    if current_price > position["highest_price"]:

        position["highest_price"] = current_price

        new_stop = current_price * (
            1 -
            risk_settings["trailing_stop_percent"]
            / 100
        )

        if new_stop > position["stop_loss_price"]:

            position["stop_loss_price"] = new_stop

        update_open_position(
            symbol,
            position
        )
# =========================
# OKX ORDERS
# =========================

def place_market_buy(symbol, amount_usdt):

    if not is_demo() and not is_live_allowed():
        raise Exception(
            "LIVE торговля запрещена.\n"
            "Установите LIVE_TRADING_ENABLED=YES"
        )

    return trade_api.place_order(
        instId=symbol,
        tdMode="cash",
        side="buy",
        ordType="market",
        sz=str(amount_usdt),
        tgtCcy="quote_ccy"
    )


def place_market_sell(symbol, amount_usdt, price):

    if not is_demo() and not is_live_allowed():
        raise Exception(
            "LIVE торговля запрещена.\n"
            "Установите LIVE_TRADING_ENABLED=YES"
        )

    base_amount = amount_usdt / price

    return trade_api.place_order(
        instId=symbol,
        tdMode="cash",
        side="sell",
        ordType="market",
        sz=str(round(base_amount, 8))
    )


# =========================
# DEMO BUY
# =========================

async def do_demo_buy(message):

    symbol = current_trade_symbol

    allowed, reason = can_open_new_position(symbol)

    if not allowed:
        await message.answer(f"⛔ {reason}")
        return

    signal_data = build_signal(symbol)

    amount = min(
        risk_settings["amount_usdt"],
        risk_settings["max_amount_usdt"]
    )

    open_position(
        symbol,
        signal_data["price"],
        amount
    )

    add_history(
        "MANUAL DEMO BUY",
        symbol,
        signal_data["price"],
        signal_data["score"]
    )

    await message.answer(
        f"🟢 DEMO BUY\n\n"
        f"Монета: {symbol}\n"
        f"Цена: {signal_data['price']:.4f}\n"
        f"Сила сигнала: {signal_data['score']}%"
    )


# =========================
# DEMO SELL
# =========================

async def do_demo_sell(message):

    positions = get_open_positions()

    if not positions:

        await message.answer(
            "📋 Открытых позиций нет."
        )

        return

    symbol = list(positions.keys())[0]

    signal_data = build_signal(symbol)

    close_position(
        symbol,
        signal_data["price"],
        "MANUAL DEMO SELL"
    )

    add_history(
        "MANUAL DEMO SELL",
        symbol,
        signal_data["price"],
        signal_data["score"]
    )

    await message.answer(
        f"🔴 DEMO SELL\n\n"
        f"{symbol}\n"
        f"Цена: {signal_data['price']:.4f}"
    )


# =========================
# LIVE BUY
# =========================

async def do_live_buy(message):

    if not is_live_allowed():

        await message.answer(
            "⛔ LIVE торговля запрещена.\n\n"
            "Установите:\n"
            "LIVE_TRADING_ENABLED=YES"
        )

        return

    symbol = current_trade_symbol

    allowed, reason = can_open_new_position(symbol)

    if not allowed:

        await message.answer(
            f"⛔ {reason}"
        )

        return

    signal_data = build_signal(symbol)

    amount = min(
        risk_settings["amount_usdt"],
        risk_settings["max_amount_usdt"]
    )

    result = place_market_buy(
        symbol,
        amount
    )

    open_position(
        symbol,
        signal_data["price"],
        amount
    )

    add_history(
        "MANUAL LIVE BUY",
        symbol,
        signal_data["price"],
        signal_data["score"],
        result
    )

    await message.answer(
        f"🔥 LIVE BUY\n\n"
        f"{symbol}\n"
        f"Цена: {signal_data['price']:.4f}"
    )


# =========================
# LIVE SELL
# =========================

async def do_live_sell(message):

    if not is_live_allowed():

        await message.answer(
            "⛔ LIVE торговля запрещена."
        )

        return

    positions = get_open_positions()

    if not positions:

        await message.answer(
            "📋 Открытых позиций нет."
        )

        return

    symbol = list(positions.keys())[0]

    position = positions[symbol]

    signal_data = build_signal(symbol)

    result = place_market_sell(
        symbol,
        position["amount_usdt"],
        signal_data["price"]
    )

    close_position(
        symbol,
        signal_data["price"],
        "MANUAL LIVE SELL"
    )

    add_history(
        "MANUAL LIVE SELL",
        symbol,
        signal_data["price"],
        signal_data["score"],
        result
    )

    await message.answer(
        f"🔥 LIVE SELL\n\n"
        f"{symbol}\n"
        f"Цена: {signal_data['price']:.4f}"
    )
# =========================
# AUTOTRADE LOOP
# =========================

async def autotrade_loop(chat_id):
    global autotrade_enabled
    global current_trade_symbol

    while autotrade_enabled:
        try:
            allowed, reason = can_trade_today()

            if not allowed:
                await bot.send_message(
                    chat_id,
                    f"⛔ Автоторговля остановлена\n\n{reason}"
                )
                autotrade_enabled = False
                save_runtime_settings()
                break

            positions = get_open_positions()

            for symbol, position in positions.items():
                signal_data = build_signal(symbol, "15m")
                current_price = signal_data["price"]

                update_trailing_stop(symbol, current_price)

                positions = get_open_positions()
                position = positions[symbol]

                pnl_percent = (
                    (current_price - position["entry_price"])
                    / position["entry_price"]
                ) * 100

                if current_price >= position["take_profit_price"]:
                    if not is_demo():
                        place_market_sell(
                            symbol,
                            position["amount_usdt"],
                            current_price
                        )

                    close_position(
                        symbol,
                        current_price,
                        "TAKE PROFIT"
                    )

                    add_history(
                        "AUTO TP",
                        symbol,
                        current_price,
                        signal_data["score"]
                    )

                    await bot.send_message(
                        chat_id,
                        f"🎯 TAKE PROFIT\n\n"
                        f"{symbol}\n"
                        f"PnL: {pnl_percent:.2f}%"
                    )

                    continue

                if current_price <= position["stop_loss_price"]:
                    if not is_demo():
                        place_market_sell(
                            symbol,
                            position["amount_usdt"],
                            current_price
                        )

                    close_position(
                        symbol,
                        current_price,
                        "STOP LOSS"
                    )

                    add_history(
                        "AUTO SL",
                        symbol,
                        current_price,
                        signal_data["score"]
                    )

                    await bot.send_message(
                        chat_id,
                        f"🛑 STOP LOSS\n\n"
                        f"{symbol}\n"
                        f"PnL: {pnl_percent:.2f}%"
                    )

                    continue

                decision = multi_timeframe_decision_for_symbol(symbol)

                if decision["signal"] == "SELL":
                    if not is_demo():
                        place_market_sell(
                            symbol,
                            position["amount_usdt"],
                            current_price
                        )

                    close_position(
                        symbol,
                        current_price,
                        "SELL SIGNAL"
                    )

                    add_history(
                        "AUTO SELL",
                        symbol,
                        current_price,
                        decision["avg_score"]
                    )

                    await bot.send_message(
                        chat_id,
                        f"🔴 SELL SIGNAL\n\n"
                        f"{symbol}\n"
                        f"PnL: {pnl_percent:.2f}%"
                    )

            positions = get_open_positions()

            if len(positions) < risk_settings["max_open_positions"]:
                if auto_select_symbol:
                    symbol, best_data = choose_best_symbol()
                else:
                    symbol = current_trade_symbol

                current_trade_symbol = symbol

                decision = multi_timeframe_decision_for_symbol(symbol)

                if decision["signal"] == "BUY":
                    allowed, reason = can_open_new_position(symbol)

                    if allowed:
                        amount = min(
                            risk_settings["amount_usdt"],
                            risk_settings["max_amount_usdt"]
                        )

                        if not is_demo():
                            place_market_buy(
                                symbol,
                                amount
                            )

                        open_position(
                            symbol,
                            decision["price"],
                            amount
                        )

                        add_history(
                            "AUTO BUY",
                            symbol,
                            decision["price"],
                            decision["avg_score"]
                        )

                        await bot.send_message(
                            chat_id,
                            f"🟢 AUTO BUY\n\n"
                            f"{symbol}\n"
                            f"Цена: {decision['price']:.4f}\n"
                            f"Сила сигнала: {decision['avg_score']}%"
                        )

            save_runtime_settings()

        except Exception as e:
            await bot.send_message(
                chat_id,
                f"❌ Ошибка автоторговли\n\n{e}"
            )

        await asyncio.sleep(AUTO_INTERVAL)

# =========================
# AUTO ON / OFF
# =========================

async def enable_autotrade(message):

    global autotrade_enabled

    if autotrade_enabled:

        await message.answer(
            "🤖 Автоторговля уже включена."
        )

        return

    autotrade_enabled = True

    save_runtime_settings()

    asyncio.create_task(
        autotrade_loop(
            message.chat.id
        )
    )

    await message.answer(
        "🟢 Автоторговля включена."
    )


async def disable_autotrade(message):

    global autotrade_enabled

    autotrade_enabled = False

    save_runtime_settings()

    await message.answer(
        "🔴 Автоторговля выключена."
    )
# =========================
# SHOW FUNCTIONS
# =========================

async def show_status(message):

    positions = get_open_positions()

    mode = "DEMO 🧪" if is_demo() else "LIVE 🔥"

    await message.answer(

        f"📊 Статус\n\n"

        f"Режим: {mode}\n"

        f"LIVE разрешён: "

        f"{'✅' if is_live_allowed() else '❌'}\n\n"

        f"Автоторговля: "

        f"{'✅' if autotrade_enabled else '❌'}\n"

        f"Автовыбор монеты: "

        f"{'✅' if auto_select_symbol else '❌'}\n"

        f"Текущая монета:\n"

        f"{current_trade_symbol}\n\n"

        f"Открытых позиций: "

        f"{len(positions)}"
    )


async def show_balance(message):

    try:

        result = account_api.get_account_balance()

        details = result["data"][0]["details"]

        text = "💰 Баланс\n\n"

        for item in details:

            balance = safe_float(
                item.get("cashBal")
            )

            if balance > 0:

                text += (
                    f"{item['ccy']}: "
                    f"{balance:.8f}\n"
                )

        await message.answer(text)

    except Exception as e:

        await message.answer(
            f"❌ {e}"
        )


async def show_signal(message):

    signal = build_signal(
        current_trade_symbol
    )

    await message.answer(
        format_signal(signal)
    )


async def show_market(message):

    decision = multi_timeframe_decision_for_symbol(
        current_trade_symbol
    )

    text = (
        f"🌐 Рынок\n\n"

        f"{current_trade_symbol}\n\n"

        f"Сигнал: "
        f"{decision['signal']}\n"

        f"Сила: "
        f"{decision['avg_score']}%"
    )

    await message.answer(text)


async def show_scan(message):

    results = scan_market()

    text = "🔎 Сканер\n\n"

    for item in results[:10]:

        text += (
            f"{item['symbol']} | "
            f"{item['signal']} | "
            f"{item['score']}%\n"
        )

    await message.answer(text)


async def show_best(message):

    symbol, data = choose_best_symbol()

    await message.answer(

        f"🏆 Лучшая монета\n\n"

        f"{symbol}\n\n"

        f"Сигнал: "

        f"{data['signal']}\n"

        f"Сила: "

        f"{data['score']}%"
    )


async def show_top3(message):

    results = get_top3()

    text = "🥇 ТОП-3\n\n"

    for i, item in enumerate(
        results,
        start=1
    ):

        text += (
            f"{i}. "
            f"{item['symbol']} | "
            f"{item['score']}%\n"
        )

    await message.answer(text)


async def show_positions(message):

    positions = get_open_positions()

    if not positions:

        await message.answer(
            "📋 Позиции отсутствуют."
        )

        return

    text = "📋 Позиции\n\n"

    for symbol, position in positions.items():

        current_price = build_signal(
            symbol
        )["price"]

        pnl = (
            (
                current_price
                - position["entry_price"]
            )
            /
            position["entry_price"]
        ) * 100

        text += (

            f"{symbol}\n"

            f"Вход: "
            f"{position['entry_price']:.4f}\n"

            f"Текущая: "
            f"{current_price:.4f}\n"

            f"PnL: "
            f"{pnl:.2f}%\n\n"
        )

    await message.answer(text)


async def show_history(message):

    rows = get_history()

    if not rows:

        await message.answer(
            "📜 История пуста."
        )

        return

    text = "📜 История\n\n"

    for row in rows:

        text += (
            f"{row[0]}\n"
            f"{row[1]}\n"
            f"{row[2]}\n\n"
        )

    await message.answer(text)


async def show_statistics(message):

    trades = get_closed_trades(
        1000
    )

    await message.answer(

        f"📈 Статистика\n\n"

        f"Сделок: "

        f"{len(trades)}\n"

        f"WinRate: "

        f"{winrate():.2f}%"
    )


async def show_pnl(message):

    await message.answer(

        f"💹 PnL\n\n"

        f"{total_pnl_percent():.2f}%\n\n"

        f"{total_pnl_usdt():.2f} USDT"
    )


async def show_risk(message):

    await message.answer(

        f"🛡 Риск\n\n"

        f"Сделка: "

        f"{risk_settings['amount_usdt']} USDT\n"

        f"SL: "

        f"{risk_settings['stop_loss_percent']}%\n"

        f"TP: "

        f"{risk_settings['take_profit_percent']}%\n"

        f"Trailing: "

        f"{risk_settings['trailing_stop_percent']}%"
    )


async def show_current_symbol(message):

    await message.answer(

        f"💱 Текущая монета\n\n"

        f"{current_trade_symbol}"
    )


# =========================
# TEXT ROUTER
# =========================

@dp.message()
async def text_router(message: types.Message):

    global auto_select_symbol

    text = (
        message.text.lower().strip()
        if message.text
        else ""
    )

    if "статус" in text:
        await show_status(message)

    elif "баланс" in text:
        await show_balance(message)

    elif "сигнал" in text:
        await show_signal(message)

    elif "рынок" in text:
        await show_market(message)

    elif "сканер" in text:
        await show_scan(message)

    elif "лучшая" in text:
        await show_best(message)

    elif "топ" in text:
        await show_top3(message)

    elif "позиц" in text:
        await show_positions(message)

    elif "история" in text:
        await show_history(message)

    elif "статистика" in text:
        await show_statistics(message)

    elif "pnl" in text:
        await show_pnl(message)

    elif "риск" in text:
        await show_risk(message)

    elif "текущая" in text:
        await show_current_symbol(message)

    elif "купить demo" in text:
        await do_demo_buy(message)

    elif "продать demo" in text:
        await do_demo_sell(message)

    elif "купить live" in text:
        await do_live_buy(message)

    elif "продать live" in text:
        await do_live_sell(message)

    elif "авто вкл" in text:
        await enable_autotrade(message)

    elif "авто выкл" in text:
        await disable_autotrade(message)

    elif "авто монета" in text:

        auto_select_symbol = (
            not auto_select_symbol
        )

        save_runtime_settings()

        await message.answer(
            "🧠 Автовыбор переключён."
        )

    else:

        await message.answer(
            "❓ Команда не распознана."
        )


# =========================
# MAIN
# =========================

async def main():

    init_db()

    load_runtime_settings()

    print(
        "OKX ULTRA PRO MAX v2 started"
    )

    await dp.start_polling(bot)


if __name__ == "__main__":

    asyncio.run(main())
