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

BOT_VERSION = "V7.2 FINAL"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

OKX_API_KEY = os.getenv("OKX_API_KEY")
OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE")

OKX_FLAG = os.getenv("OKX_FLAG", "0")

LIVE_TRADING_ENABLED = os.getenv(
    "LIVE_TRADING_ENABLED",
    "NO"
)

TRADE_SYMBOL = os.getenv(
    "TRADE_SYMBOL",
    "BTC-USDT"
)

TRADE_AMOUNT_USDT = float(
    os.getenv(
        "TRADE_AMOUNT_USDT",
        "5"
    )
)

AUTO_INTERVAL = int(
    os.getenv(
        "AUTO_INTERVAL",
        "300"
    )
)

DB_FILE = "bot.db"

DUST_LIMIT_USDT = float(
    os.getenv(
        "DUST_LIMIT_USDT",
        "1"
    )
)


WATCHLIST = [

    "BTC-USDT",
    "ETH-USDT",
    "SOL-USDT",
    "TON-USDT",
    "TRX-USDT",

    "XRP-USDT",
    "DOGE-USDT",
    "ADA-USDT",
    "AVAX-USDT",
    "LINK-USDT",

    "DOT-USDT",
    "APT-USDT",
    "ATOM-USDT",
    "ARB-USDT",
    "OP-USDT",

    "SUI-USDT",
    "NEAR-USDT",
    "ETC-USDT",
    "BCH-USDT",
    "LTC-USDT",

    "FIL-USDT",
    "INJ-USDT",
    "SEI-USDT",
    "UNI-USDT",
    "AAVE-USDT",

    "HBAR-USDT",
    "FET-USDT",
    "PEPE-USDT",
    "SHIB-USDT",
    "WIF-USDT"

]


TIMEFRAMES = [
    "5m",
    "15m",
    "1H"
]


# =========================
# TELEGRAM / OKX
# =========================

bot = Bot(
    token=TELEGRAM_BOT_TOKEN
)

dp = Dispatcher()

market_api = MarketData.MarketAPI(
    flag=OKX_FLAG
)

account_api = Account.AccountAPI(
    OKX_API_KEY,
    OKX_SECRET_KEY,
    OKX_PASSPHRASE,
    False,
    OKX_FLAG
)

trade_api = Trade.TradeAPI(
    OKX_API_KEY,
    OKX_SECRET_KEY,
    OKX_PASSPHRASE,
    False,
    OKX_FLAG
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

    "buy_score": 75,

    "sell_score": 35,

    "min_adx": 18,

    "max_open_positions": 10,

    "max_trades_day": 50,

    "auto_amount_enabled": True,

    "balance_usage_percent": 5.0,

    "min_trade_usdt": 5.0,

    "max_trade_usdt": 15.0,

    "cooldown_after_loss_minutes": 180,
    "hold_losing_position_hours": 36,
    "break_even_enabled": True,
    "break_even_min_hold_hours": 24,
    "break_even_plus_percent": 0.15,
    "emergency_stop_percent": 3.0,
}

# =========================
# KEYBOARD
# =========================

keyboard = ReplyKeyboardMarkup(
    keyboard=[

        [
            KeyboardButton(text="📊 Статус"),
            KeyboardButton(text="💰 Баланс")
        ],

        [
            KeyboardButton(text="📡 Сигнал"),
            KeyboardButton(text="🌐 Рынок")
        ],

        [
            KeyboardButton(text="🔎 Сканер"),
            KeyboardButton(text="🏆 Лучшая")
        ],

        [
            KeyboardButton(text="🥇 Топ-3"),
            KeyboardButton(text="📋 Позиции")
        ],

        [
            KeyboardButton(text="🟢 Авто ВКЛ"),
            KeyboardButton(text="🔴 Авто ВЫКЛ")
        ],

        [
            KeyboardButton(text="🧠 Авто монета"),
            KeyboardButton(text="💱 Текущая монета")
        ],

        [
            KeyboardButton(text="🤖 Авто статус"),
            KeyboardButton(text="🛡 Риск")
        ],

        [
            KeyboardButton(text="📜 История"),
            KeyboardButton(text="📈 Статистика")
        ],

        [
            KeyboardButton(text="💹 PnL"),
            KeyboardButton(text="📅 Отчет за сутки")
        ],

        [
            KeyboardButton(text="🔄 Синхронизация OKX")
        ]

    ],
    resize_keyboard=True
)


# =========================
# HELPERS
# =========================

def now():
    return datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def today_str():
    return date.today().isoformat()


def safe_float(value, default=0.0):

    try:
        return float(value)

    except Exception:
        return default


def symbol_to_currency(symbol):

    return symbol.split("-")[0]


def currency_to_symbol(currency):

    return f"{currency}-USDT"


def is_live_allowed():

    return (
        str(OKX_FLAG) == "0"
        and
        LIVE_TRADING_ENABLED == "YES"
    )
# =========================
# DATABASE
# =========================

def db_connect():

    return sqlite3.connect(
        DB_FILE
    )


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
            score REAL,
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


def db_set(
    key,
    value
):

    conn = db_connect()

    cur = conn.cursor()

    cur.execute(
        """
        INSERT OR REPLACE INTO settings
        (
            key,
            value
        )
        VALUES
        (
            ?,
            ?
        )
        """,
        (
            key,
            json.dumps(
                value,
                ensure_ascii=False
            )
        )
    )

    conn.commit()

    conn.close()


def db_get(
    key,
    default=None
):

    conn = db_connect()

    cur = conn.cursor()

    cur.execute(
        """
        SELECT value
        FROM settings
        WHERE key = ?
        """,
        (
            key,
        )
    )

    row = cur.fetchone()

    conn.close()

    if not row:

        return default

    try:

        return json.loads(
            row[0]
        )

    except Exception:

        return default


def save_runtime_settings():

    db_set(
        "autotrade_enabled",
        autotrade_enabled
    )

    db_set(
        "auto_select_symbol",
        auto_select_symbol
    )

    db_set(
        "current_trade_symbol",
        current_trade_symbol
    )

    db_set(
        "risk_settings",
        risk_settings
    )


def load_runtime_settings():

    global autotrade_enabled
    global auto_select_symbol
    global current_trade_symbol
    global risk_settings

    autotrade_enabled = bool(
        db_get(
            "autotrade_enabled",
            False
        )
    )

    auto_select_symbol = bool(
        db_get(
            "auto_select_symbol",
            True
        )
    )

    current_trade_symbol = db_get(
        "current_trade_symbol",
        TRADE_SYMBOL
    )

    saved_risk = db_get(
        "risk_settings",
        {}
    )

    if isinstance(
        saved_risk,
        dict
    ):

        risk_settings.update(
            saved_risk
        )

# =========================
# CLOSED TRADES / REPORTS
# =========================

def add_closed_trade(symbol, entry_price, exit_price, amount_usdt, reason):

    pnl_percent = (
        (exit_price - entry_price)
        / entry_price
        * 100
        if entry_price > 0
        else 0
    )

    pnl_usdt = amount_usdt * pnl_percent / 100

    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO closed_trades
        (
            time,
            date,
            symbol,
            entry_price,
            exit_price,
            amount_usdt,
            pnl_percent,
            pnl_usdt,
            reason
        )
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
            reason
        )
    )

    conn.commit()
    conn.close()

    return {
        "symbol": symbol,
        "entry_price": safe_float(entry_price),
        "exit_price": safe_float(exit_price),
        "amount_usdt": safe_float(amount_usdt),
        "pnl_percent": safe_float(pnl_percent),
        "pnl_usdt": safe_float(pnl_usdt),
        "reason": reason
    }


def get_closed_trades_today():

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
        WHERE date = ?
        ORDER BY id DESC
        """,
        (
            today_str(),
        )
    )

    rows = cur.fetchall()

    conn.close()

    return rows


def build_daily_bot_report():

    rows = get_closed_trades_today()

    total_trades = len(rows)

    profit_usdt = sum(
        safe_float(row[6])
        for row in rows
        if safe_float(row[6]) > 0
    )

    loss_usdt = sum(
        safe_float(row[6])
        for row in rows
        if safe_float(row[6]) < 0
    )

    total_pnl = profit_usdt + loss_usdt

    wins = len([row for row in rows if safe_float(row[6]) > 0])
    losses = len([row for row in rows if safe_float(row[6]) < 0])

    winrate = (
        wins / total_trades * 100
        if total_trades > 0
        else 0
    )

    rub_rate = get_usdt_rub_rate()

    return (
        f"📅 Отчёт за сутки\n\n"
        f"Закрытых сделок: {total_trades}\n"
        f"Прибыльных: {wins}\n"
        f"Убыточных: {losses}\n"
        f"WinRate: {winrate:.2f}%\n\n"
        f"Заработано:\n"
        f"+{profit_usdt:.4f} USDT\n"
        f"≈ +{profit_usdt * rub_rate:,.0f} ₽\n\n"
        f"Потеряно:\n"
        f"{loss_usdt:.4f} USDT\n"
        f"≈ {loss_usdt * rub_rate:,.0f} ₽\n\n"
        f"Итог за сутки:\n"
        f"{total_pnl:+.4f} USDT\n"
        f"≈ {total_pnl * rub_rate:+,.0f} ₽"
    )

    for row in rows[:10]:

        trade_time = row[0]
        symbol = row[1]
        entry_price = safe_float(row[2])
        exit_price = safe_float(row[3])
        pnl_percent = safe_float(row[5])
        pnl_usdt = safe_float(row[6])
        reason = row[7]

        emoji = "🟢" if pnl_usdt >= 0 else "🔴"

        text += (
            f"{emoji} {symbol}\n"
            f"Вход: {entry_price:.6f}\n"
            f"Выход: {exit_price:.6f}\n"
            f"PnL: {pnl_usdt:+.4f} USDT / {pnl_percent:+.2f}%\n"
            f"Причина: {reason}\n"
            f"Время: {trade_time[-8:]}\n\n"
        )

    return text


def format_closed_trade_message(trade_result):

    if not trade_result:
        return "Сделка закрыта, но данные не найдены."

    pnl_emoji = "🟢" if trade_result["pnl_usdt"] >= 0 else "🔴"

    return (
        f"{pnl_emoji} SELL / ЗАКРЫТИЕ\n\n"
        f"{trade_result['symbol']}\n\n"
        f"Причина продажи:\n"
        f"{trade_result['reason']}\n\n"
        f"Купил по:\n"
        f"{trade_result['entry_price']:.6f}\n\n"
        f"Продал по:\n"
        f"{trade_result['exit_price']:.6f}\n\n"
        f"Сумма сделки:\n"
        f"{trade_result['amount_usdt']:.2f} USDT\n\n"
        f"Результат:\n"
        f"{trade_result['pnl_usdt']:+.4f} USDT\n"
        f"{trade_result['pnl_percent']:+.2f}%"
    )


def format_buy_message(symbol, decision, amount):

    return (
        f"🟢 AUTO BUY / ПОКУПКА\n\n"
        f"{symbol}\n\n"
        f"Причина покупки:\n"
        f"Сигнал BUY по 5m + 15m + 1H\n\n"
        f"Цена покупки:\n"
        f"{decision['price']:.6f}\n\n"
        f"Сила сигнала:\n"
        f"{decision['avg_score']}%\n\n"
        f"Сумма:\n"
        f"{amount:.2f} USDT\n\n"
        f"Trailing включится после:\n"
        f"+{risk_settings['trailing_start_profit_percent']}%"
    )
# =========================
# POSITIONS
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
            time
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            symbol,
            safe_float(entry_price),
            safe_float(amount_usdt),
            safe_float(stop_loss_price),
            safe_float(take_profit_price),
            safe_float(highest_price),
            now()
        )
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
            entry_price=?,
            amount_usdt=?,
            stop_loss_price=?,
            take_profit_price=?,
            highest_price=?
        WHERE symbol=?
        """,
        (
            safe_float(position["entry_price"]),
            safe_float(position["amount_usdt"]),
            safe_float(position["stop_loss_price"]),
            safe_float(position["take_profit_price"]),
            safe_float(position["highest_price"]),
            symbol
        )
    )

    conn.commit()
    conn.close()


def delete_open_position(symbol):

    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        "DELETE FROM open_positions WHERE symbol=?",
        (symbol,)
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

            "time": row[6]

        }

    return positions


def position_age_hours(position):

    try:

        opened_time = datetime.strptime(
            position["time"],
            "%Y-%m-%d %H:%M:%S"
        )

        return (
            datetime.now()
            - opened_time
        ).total_seconds() / 3600

    except Exception:

        return 999


def get_position_pnl_percent(
    position,
    current_price
):

    entry_price = position["entry_price"]

    if entry_price <= 0:
        return 0

    return (
        (
            current_price
            - entry_price
        )
        / entry_price
    ) * 100


def should_emergency_close(
    position,
    current_price
):

    pnl_percent = get_position_pnl_percent(
        position,
        current_price
    )

    return (
        pnl_percent
        <=
        -risk_settings[
            "emergency_stop_percent"
        ]
    )


def should_break_even_close(
    position,
    current_price
):

    if not risk_settings.get(
        "break_even_enabled",
        True
    ):
        return False

    age_hours = position_age_hours(
        position
    )

    if age_hours < risk_settings[
        "break_even_min_hold_hours"
    ]:
        return False

    target_price = (
        position["entry_price"]
        *
        (
            1
            +
            risk_settings[
                "break_even_plus_percent"
            ]
            / 100
        )
    )

    return current_price >= target_price


def can_close_position_now(
    position,
    current_price
):

    pnl_percent = get_position_pnl_percent(
        position,
        current_price
    )

    if pnl_percent >= 0:

        return True

    if should_emergency_close(
        position,
        current_price
    ):

        return True

    return False


def clear_open_positions():

    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        "DELETE FROM open_positions"
    )

    conn.commit()
    conn.close()

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

            balances.append(
                {
                    "ccy": item["ccy"],
                    "eq_usd": safe_float(item.get("eqUsd", 0)),
                    "avail_bal": safe_float(item.get("availBal", 0)),
                }
            )

        return balances

    except Exception as e:
        print(f"get_okx_balance error: {e}")
        return []


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
            data.get("rates", {}).get("RUB", 90),
            90
        )

    except Exception as e:
        print(f"get_usdt_rub_rate error: {e}")
        return 90.0


def get_current_price(symbol):

    try:
        ticker = market_api.get_ticker(
            instId=symbol
        )

        return safe_float(
            ticker["data"][0]["last"]
        )

    except Exception as e:
        print(f"get_current_price error {symbol}: {e}")
        return 0.0


def get_okx_fills(limit=100):

    try:
        result = trade_api.get_fills(
            limit=str(limit)
        )

        if not result or "data" not in result:
            return []

        return result["data"]

    except Exception as e:
        print(f"get_okx_fills error: {e}")
        return []

def estimate_entry_price_from_fills(symbol, current_amount_usdt):

    try:
        fills = get_okx_fills(100)

        buys = []

        for item in fills:
            if item.get("instId") != symbol:
                continue

            if item.get("side") != "buy":
                continue

            price = safe_float(item.get("fillPx", 0))
            size = safe_float(item.get("fillSz", 0))

            if price > 0 and size > 0:
                buys.append((price, size))

        if not buys:
            return 0

        total_qty = sum(size for price, size in buys)
        total_cost = sum(price * size for price, size in buys)

        if total_qty <= 0:
            return 0

        return total_cost / total_qty

    except Exception as e:
        print(f"estimate_entry_price_from_fills error {symbol}: {e}")
        return 0


def get_okx_trade_statistics(limit=100):

    fills = get_okx_fills(limit)

    trades = []

    for item in fills:

        symbol = item.get("instId", "")
        side = item.get("side", "")
        price = safe_float(item.get("fillPx", 0))
        size = safe_float(item.get("fillSz", 0))
        fee = safe_float(item.get("fee", 0))

        amount_usdt = price * size

        trades.append(
            {
                "symbol": symbol,
                "side": side,
                "price": price,
                "size": size,
                "amount_usdt": amount_usdt,
                "fee": fee,
            }
        )

    return trades

# =========================
# TRADE FUNCTIONS
# =========================

def okx_order_success(
    result
):

    try:

        if isinstance(
            result,
            dict
        ):

            return (
                str(
                    result.get(
                        "code"
                    )
                )
                ==
                "0"
            )

        if isinstance(
            result,
            str
        ):

            return (
                '"code":"0"' in result
                or
                '"code": "0"' in result
                or
                "'code': '0'" in result
            )

    except Exception:

        pass

    return False
def get_trade_amount_usdt():

    if not risk_settings[
        "auto_amount_enabled"
    ]:

        return risk_settings[
            "amount_usdt"
        ]

    balance = get_usdt_balance()

    amount = (

        balance

        *

        risk_settings[
            "balance_usage_percent"
        ]

        / 100

    )

    amount = max(

        amount,

        risk_settings[
            "min_trade_usdt"
        ]

    )

    amount = min(

        amount,

        risk_settings[
            "max_trade_usdt"
        ]

    )

    return round(
        amount,
        2
    )
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

            sz=str(
                amount_usdt
            )

        )

        return result

    except Exception as e:

        return str(
            e
        )
def place_market_sell(
    symbol
):

    if not is_live_allowed():

        return "LIVE OFF"

    try:

        balance = 0

        for item in get_okx_balance():

            if item["ccy"] == symbol_to_currency(
                symbol
            ):

                balance = item[
                    "avail_bal"
                ]

                break

        if balance <= 0:

            return "NO ASSET"

        result = trade_api.place_order(

            instId=symbol,

            tdMode="cash",

            side="sell",

            ordType="market",

            sz=str(
                balance
            )

        )

        return result

    except Exception as e:

        return str(
            e
        )
def open_position(
    symbol,
    entry_price,
    amount_usdt
):

    stop_loss_price = (

        entry_price

        *

        (

            1

            -

            risk_settings[
                "stop_loss_percent"
            ]

            / 100

        )

    )

    take_profit_price = (

        entry_price

        *

        (

            1

            +

            risk_settings[
                "take_profit_percent"
            ]

            / 100

        )

    )

    save_open_position(

        symbol,

        entry_price,

        amount_usdt,

        stop_loss_price,

        take_profit_price,

        entry_price

    )
def close_position(symbol, exit_price=None, reason="CLOSE"):

    positions = get_open_positions()

    if symbol not in positions:
        return None

    position = positions[symbol]

    if exit_price is None:
        exit_price = get_current_price(symbol)

    trade_result = add_closed_trade(
        symbol,
        position["entry_price"],
        exit_price,
        position["amount_usdt"],
        reason
    )

    delete_open_position(symbol)

    return trade_result
def update_trailing_stop(
    symbol,
    current_price
):

    positions = get_open_positions()

    if symbol not in positions:

        return

    position = positions[
        symbol
    ]

    entry_price = position[
        "entry_price"
    ]

    if current_price > position[
        "highest_price"
    ]:

        position[
            "highest_price"
        ] = current_price

    pnl_percent = (

        (

            current_price

            -

            entry_price

        )

        /

        entry_price

    ) * 100

    if pnl_percent >= risk_settings[
        "trailing_start_profit_percent"
    ]:

        new_stop = (

            position[
                "highest_price"
            ]

            *

            (

                1

                -

                risk_settings[
                    "trailing_stop_percent"
                ]

                / 100

            )

        )

        if new_stop > position[
            "stop_loss_price"
        ]:

            position[
                "stop_loss_price"
            ] = new_stop

        save_open_position(

            symbol,

            position[
                "entry_price"
            ],

            position[
                "amount_usdt"
            ],

            position[
                "stop_loss_price"
            ],

            position[
                "take_profit_price"
            ],

            position[
                "highest_price"
            ]

        )

# =========================
# SYNC
# =========================

def sync_positions_with_okx():

    positions = get_open_positions()
    balances = get_okx_balance()

    real_assets = set()

    for item in balances:

        ccy = item["ccy"]
        eq_usd = safe_float(item["eq_usd"])

        if ccy == "USDT":
            continue

        if eq_usd < DUST_LIMIT_USDT:
            continue

        symbol = currency_to_symbol(ccy)

        current_price = get_current_price(symbol)

        if current_price <= 0:
            continue

        real_assets.add(symbol)

        if symbol in positions:
            position = positions[symbol]
            position["amount_usdt"] = eq_usd
            update_open_position(symbol, position)
            continue

        entry_price = estimate_entry_price_from_fills(
            symbol,
            eq_usd
        )

        if entry_price <= 0:
            entry_price = current_price

        save_open_position(
            symbol,
            entry_price,
            eq_usd,
            entry_price * (1 - risk_settings["stop_loss_percent"] / 100),
            entry_price * (1 + risk_settings["take_profit_percent"] / 100),
            max(entry_price, current_price)
        )

    for symbol in list(positions.keys()):

        if symbol not in real_assets:
            delete_open_position(symbol)


def can_trade_today():

    return (
        True,
        "OK"
    )


def can_open_new_position(symbol):

    positions = get_open_positions()

    if symbol in positions:
        return False, "Уже есть позиция в базе"

    balances = get_okx_balance()

    base_currency = symbol_to_currency(symbol)

    for item in balances:
        if item["ccy"] == base_currency and item["eq_usd"] >= DUST_LIMIT_USDT:
            return False, "Монета уже куплена на OKX"

    if len(positions) >= risk_settings["max_open_positions"]:
        return False, "Достигнут лимит позиций"

    return True, "OK"

# =========================
# INDICATORS
# =========================

def get_candles(
    symbol,
    timeframe="15m",
    limit=300
):

    try:

        result = market_api.get_candlesticks(

            instId=symbol,

            bar=timeframe,

            limit=str(
                limit
            )
        )

        if (
            not result
            or
            "data" not in result
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
                "confirm"
            ]
        )

        for col in [

            "open",
            "high",
            "low",
            "close",
            "volume"

        ]:

            df[col] = df[col].astype(
                float
            )

        return df

    except Exception:

        return pd.DataFrame()
def add_indicators(
    df
):

    close = df["close"]

    df["ema50"] = close.ewm(
        span=50
    ).mean()

    df["ema200"] = close.ewm(
        span=200
    ).mean()

    delta = close.diff()

    gain = delta.where(
        delta > 0,
        0
    )

    loss = -delta.where(
        delta < 0,
        0
    )

    avg_gain = gain.rolling(
        14
    ).mean()

    avg_loss = loss.rolling(
        14
    ).mean()

    rs = avg_gain / avg_loss.replace(
        0,
        1e-9
    )

    df["rsi"] = 100 - (
        100 / (1 + rs)
    )

    ema12 = close.ewm(
        span=12
    ).mean()

    ema26 = close.ewm(
        span=26
    ).mean()

    df["macd"] = ema12 - ema26

    df["macd_signal"] = df[
        "macd"
    ].ewm(
        span=9
    ).mean()

    return df

# =========================
# SIGNALS
# =========================

def build_signal(
    symbol,
    timeframe="15m"
):

    df = get_candles(
        symbol,
        timeframe
    )

    if len(df) < 200:

        return None

    df = add_indicators(
        df
    )

    last = df.iloc[-1]

    score = 0

    if 45 <= last["rsi"] <= 72:

        score += 20

    if last["macd"] > last["macd_signal"]:

        score += 20

    if last["ema50"] > last["ema200"]:

        score += 35

    score += 25

    signal = "HOLD"

    if score >= risk_settings[
        "buy_score"
    ]:

        signal = "BUY"

    elif score <= risk_settings[
        "sell_score"
    ]:

        signal = "SELL"

    return {

        "symbol": symbol,

        "signal": signal,

        "score": score,

        "price": safe_float(
            last["close"]
        ),

        "rsi": safe_float(
            last["rsi"]
        )
    }
def multi_timeframe_decision_for_symbol(
    symbol
):

    results = []

    for tf in TIMEFRAMES:

        signal = build_signal(
            symbol,
            tf
        )

        if signal:

            results.append(
                signal
            )

    if not results:

        return {

            "signal": "HOLD",

            "avg_score": 0,

            "price": 0

        }

    avg_score = sum(
        x["score"]
        for x in results
    ) / len(results)

    signal = "HOLD"

    if avg_score >= risk_settings[
        "buy_score"
    ]:

        signal = "BUY"

    elif avg_score <= risk_settings[
        "sell_score"
    ]:

        signal = "SELL"

    return {

        "signal": signal,

        "avg_score": round(
            avg_score,
            2
        ),

        "price": results[-1][
            "price"
        ]
    }

# =========================
# BUY LOGIC
# =========================

def btc_market_filter_ok():

    return (
        True,
        "OK"
    )


def is_strong_buy(
    symbol,
    decision,
    signal_data
):

    if decision["signal"] != "BUY":

        return (
            False,
            "NO BUY"
        )

    if decision["avg_score"] < risk_settings[
        "buy_score"
    ]:

        return (
            False,
            "LOW SCORE"
        )

    return (
        True,
        "OK"
    )

# =========================
# SYMBOL SELECTION
# =========================

def choose_best_symbol():

    candidates = []

    for symbol in WATCHLIST:

        try:

            decision = multi_timeframe_decision_for_symbol(
                symbol
            )

            if decision["signal"] == "BUY":

                candidates.append(

                    (
                        symbol,
                        decision["avg_score"]
                    )

                )

        except Exception:

            continue

    if not candidates:

        return (
            current_trade_symbol,
            {
                "score": 0
            }
        )

    candidates.sort(

        key=lambda x: x[1],

        reverse=True

    )

    best = candidates[0]

    return (

        best[0],

        {

            "score": best[1]

        }

    )

# =========================
# SHOW FUNCTIONS
# =========================

async def show_status(message):

    positions = get_open_positions()

    mode = (
        "LIVE 🔥"
        if is_live_allowed()
        else "DEMO 🧪"
    )

    await message.answer(

        f"📊 Статус\n\n"

        f"Версия:\n"
        f"{BOT_VERSION}\n\n"

        f"Режим:\n"
        f"{mode}\n\n"

        f"Автоторговля:\n"
        f"{'🟢 ВКЛ' if autotrade_enabled else '🔴 ВЫКЛ'}\n\n"

        f"Монета:\n"
        f"{current_trade_symbol}\n\n"

        f"Открытых позиций:\n"
        f"{len(positions)}",

        reply_markup=keyboard
    )
async def show_balance(message):

    balances = get_okx_balance()
    rub_rate = get_usdt_rub_rate()

    total_usdt = 0.0
    assets_text = ""

    for item in balances:

        if item["eq_usd"] < 0.01:
            continue

        total_usdt += item["eq_usd"]

        rub_value = item["eq_usd"] * rub_rate

        assets_text += (
            f"{item['ccy']}\n"
            f"Доступно: {item['avail_bal']:.8f}\n"
            f"≈ {item['eq_usd']:.2f} USDT\n"
            f"≈ {rub_value:,.0f} ₽\n\n"
        )

    total_rub = total_usdt * rub_rate

    text = (
        f"💰 Баланс OKX\n\n"
        f"Общий баланс:\n"
        f"{total_usdt:.2f} USDT\n"
        f"≈ {total_rub:,.0f} ₽\n\n"
        f"Активы:\n\n"
        f"{assets_text if assets_text else 'Активов нет.'}"
    )

    await message.answer(
        text,
        reply_markup=keyboard
    )

async def show_signal(message):

    decision = multi_timeframe_decision_for_symbol(
        current_trade_symbol
    )

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

        f"{current_trade_symbol}\n\n"

        f"Цена:\n"

        f"{decision['price']:.4f}\n\n"

        f"Решение:\n"

        f"{decision['signal']}\n\n"

        f"Сила:\n"

        f"{decision['avg_score']}%\n\n"

        f"RSI:\n"

        f"{signal['rsi']:.2f}",

        reply_markup=keyboard
    )

async def show_market(message):

    decision = multi_timeframe_decision_for_symbol(
        current_trade_symbol
    )

    await message.answer(

        f"🌐 Рынок\n\n"

        f"{current_trade_symbol}\n\n"

        f"Решение:\n"

        f"{decision['signal']}\n\n"

        f"Сила:\n"

        f"{decision['avg_score']}%",

        reply_markup=keyboard
    )

async def show_scanner(message):

    text = "🔎 Сканер\n\n"

    count = 0

    for symbol in WATCHLIST:

        try:

            decision = multi_timeframe_decision_for_symbol(
                symbol
            )

            if decision["signal"] == "BUY":

                count += 1

                text += (

                    f"{symbol}\n"

                    f"{decision['avg_score']}%\n\n"

                )

        except Exception:

            continue

    if count == 0:

        text += "BUY сигналов нет."

    await message.answer(
        text,
        reply_markup=keyboard
    )

async def show_best_symbol(message):

    symbol, data = choose_best_symbol()

    await message.answer(

        f"🏆 Лучшая монета\n\n"

        f"{symbol}\n\n"

        f"Сила:\n"

        f"{data['score']}%",

        reply_markup=keyboard
    )

async def show_positions(message):

    positions = get_open_positions()

    if not positions:

        await message.answer(

            "📋 Открытых позиций нет.",

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
                -
                position["entry_price"]
            )

            /

            position["entry_price"]

        ) * 100

        text += (

            f"{symbol}\n"

            f"PnL: {pnl:.2f}%\n\n"

        )

    await message.answer(
        text,
        reply_markup=keyboard
    )

async def show_history(message):

    await message.answer(

        "📜 История доступна в статистике.",

        reply_markup=keyboard

    )

async def show_statistics(message):

    rows = get_closed_trades_today()
    rub_rate = get_usdt_rub_rate()

    total_trades = len(rows)

    profit_usdt = sum(
        safe_float(row[6])
        for row in rows
        if safe_float(row[6]) > 0
    )

    loss_usdt = sum(
        safe_float(row[6])
        for row in rows
        if safe_float(row[6]) < 0
    )

    total_pnl = profit_usdt + loss_usdt

    wins = len([row for row in rows if safe_float(row[6]) > 0])
    losses = len([row for row in rows if safe_float(row[6]) < 0])

    winrate = (
        wins / total_trades * 100
        if total_trades > 0
        else 0
    )

    await message.answer(

        f"📈 Статистика бота\n\n"
        f"Закрытых сделок: {total_trades}\n"
        f"Прибыльных: {wins}\n"
        f"Убыточных: {losses}\n"
        f"WinRate: {winrate:.2f}%\n\n"
        f"Заработано:\n"
        f"+{profit_usdt:.4f} USDT\n"
        f"≈ +{profit_usdt * rub_rate:,.0f} ₽\n\n"
        f"Потеряно:\n"
        f"{loss_usdt:.4f} USDT\n"
        f"≈ {loss_usdt * rub_rate:,.0f} ₽\n\n"
        f"Общий итог:\n"
        f"{total_pnl:+.4f} USDT\n"
        f"≈ {total_pnl * rub_rate:+,.0f} ₽",

        reply_markup=keyboard
    )

async def show_pnl(message):

    trades = get_okx_trade_statistics(100)
    total_balance = get_total_balance_usdt()
    rub_rate = get_usdt_rub_rate()

    volume_usdt = sum(
        trade["amount_usdt"]
        for trade in trades
    )

    fees_usdt = sum(
        trade["fee"]
        for trade in trades
    )

    await message.answer(

        f"💹 PnL / OKX\n\n"
        f"Текущий баланс:\n"
        f"{total_balance:.2f} USDT\n"
        f"≈ {total_balance * rub_rate:,.0f} ₽\n\n"
        f"Оборот последних исполнений:\n"
        f"{volume_usdt:.2f} USDT\n"
        f"≈ {volume_usdt * rub_rate:,.0f} ₽\n\n"
        f"Комиссии:\n"
        f"{fees_usdt:.4f} USDT\n"
        f"≈ {fees_usdt * rub_rate:,.0f} ₽",

        reply_markup=keyboard
    )

async def show_daily_report(message):

    await message.answer(
        build_daily_bot_report(),
        reply_markup=keyboard
    )

# =========================
# AUTOTRADE
# =========================

async def autotrade_loop(chat_id):

    global current_trade_symbol

    while autotrade_enabled:

        try:

            sync_positions_with_okx()

            positions = get_open_positions()

            # =====================
            # CHECK OPEN POSITIONS
            # =====================

            for symbol, position in positions.items():

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

                # =====================
                # BREAK EVEN CLOSE
                # =====================

                if should_break_even_close(
                    position,
                    current_price
                ):

                    result = place_market_sell(symbol)

                    if okx_order_success(result):

                        trade_result = close_position(
                            symbol,
                            current_price,
                            "BREAK EVEN"
                        )

                        await bot.send_message(
                            chat_id,
                            format_closed_trade_message(
                                trade_result
                            )
                        )

                    continue

                # =====================
                # TRAILING STOP
                # =====================

                if (
                    current_price <= position["stop_loss_price"]
                    and can_close_position_now(
                        position,
                        current_price
                    )
                ):

                    result = place_market_sell(symbol)

                    if okx_order_success(result):

                        trade_result = close_position(
                            symbol,
                            current_price,
                            "TRAILING STOP"
                        )

                        await bot.send_message(
                            chat_id,
                            format_closed_trade_message(
                                trade_result
                            )
                        )

                    continue

                # =====================
                # SELL SIGNAL
                # =====================

                decision = multi_timeframe_decision_for_symbol(
                    symbol
                )

                if (
                    decision["signal"] == "SELL"
                    and can_close_position_now(
                        position,
                        current_price
                    )
                ):

                    result = place_market_sell(symbol)

                    if okx_order_success(result):

                        trade_result = close_position(
                            symbol,
                            current_price,
                            "SELL SIGNAL"
                        )

                        await bot.send_message(
                            chat_id,
                            format_closed_trade_message(
                                trade_result
                            )
                        )

                    continue

            # =====================
            # OPEN NEW POSITION
            # =====================

            positions = get_open_positions()

            if len(positions) < risk_settings["max_open_positions"]:

                if auto_select_symbol:
                    symbol, _ = choose_best_symbol()
                else:
                    symbol = current_trade_symbol

                current_trade_symbol = symbol

                decision = multi_timeframe_decision_for_symbol(
                    symbol
                )

                signal = build_signal(
                    symbol,
                    "15m"
                )

                if signal:

                    buy_ok, _ = is_strong_buy(
                        symbol,
                        decision,
                        signal
                    )

                    if buy_ok:

                        allowed, _ = can_open_new_position(
                            symbol
                        )

                        if allowed:

                            amount = get_trade_amount_usdt()

                            result = place_market_buy(
                                symbol,
                                amount
                            )

                            if okx_order_success(result):

                                open_position(
                                    symbol,
                                    decision["price"],
                                    amount
                                )

                                await bot.send_message(
                                    chat_id,
                                    format_buy_message(
                                        symbol,
                                        decision,
                                        amount
                                    )
                                )

            save_runtime_settings()

        except Exception as e:

            try:
                await bot.send_message(
                    chat_id,
                    f"⚠️ Ошибка автоторговли\n\n{e}"
                )
            except Exception:
                pass

        await asyncio.sleep(AUTO_INTERVAL)

# =========================
# START
# =========================

@dp.message(Command("start"))
async def start_cmd(message: types.Message):

    db_set(
        "last_chat_id",
        message.chat.id
    )

    await message.answer(

        f"🤖 OKX ULTRA PRO MAX {BOT_VERSION}",

        reply_markup=keyboard

    )

# =========================
# TEXT ROUTER
# =========================

@dp.message()
async def text_router(message: types.Message):

    global autotrade_enabled
    global auto_select_symbol
    global current_trade_symbol

    text = message.text or ""
    text_lower = text.lower()

    if "📊" in text or "статус" in text_lower and "авто" not in text_lower:

        await show_status(message)

    elif "💰" in text or "баланс" in text_lower:

        await show_balance(message)

    elif "📡" in text or "сигнал" in text_lower:

        await show_signal(message)

    elif "🌐" in text or "рынок" in text_lower:

        await show_market(message)

    elif "🔎" in text or "сканер" in text_lower:

        await show_scanner(message)

    elif "🏆" in text or "лучшая" in text_lower:

        await show_best_symbol(message)

    elif "🥇" in text or "топ" in text_lower:

        if "show_top3" in globals():
            await show_top3(message)
        else:
            await message.answer(
                "🥇 TOP-3 пока недоступен.",
                reply_markup=keyboard
            )

    elif "📋" in text or "позиц" in text_lower:

        sync_positions_with_okx()

        await show_positions(message)

    elif "📜" in text or "история" in text_lower:

        await show_history(message)

    elif "📈" in text or "статист" in text_lower:

        await show_statistics(message)

    elif "💹" in text or "pnl" in text_lower:

        await show_pnl(message)

    elif "📅" in text or "отчет за сутки" in text_lower or "отчёт за сутки" in text_lower:

        await show_daily_report(message)

    elif "🤖" in text or "авто статус" in text_lower:

        if "show_auto_status" in globals():
            await show_auto_status(message)
        else:
            await show_status(message)

    elif "🛡" in text or "риск" in text_lower:

        if "show_risk" in globals():
            await show_risk(message)
        else:
            await message.answer(
                "🛡 Риск\n\n"
                f"BUY от: {risk_settings['buy_score']}%\n"
                f"SELL до: {risk_settings['sell_score']}%\n"
                f"ADX минимум: {risk_settings['min_adx']}\n"
                f"Trailing stop: {risk_settings['trailing_stop_percent']}%\n"
                f"Break-even: {'ВКЛ' if risk_settings.get('break_even_enabled', True) else 'ВЫКЛ'}",
                reply_markup=keyboard
            )

    elif "💱" in text or "текущ" in text_lower:

        await message.answer(
            f"💱 Текущая монета\n\n{current_trade_symbol}",
            reply_markup=keyboard
        )

    elif "🧠" in text or "авто монета" in text_lower:

        auto_select_symbol = not auto_select_symbol

        save_runtime_settings()

        await message.answer(
            f"🧠 Авто монета\n\n"
            f"{'✅ ВКЛ' if auto_select_symbol else '❌ ВЫКЛ'}",
            reply_markup=keyboard
        )

    elif "🟢" in text or "авто вкл" in text_lower:

        if not autotrade_enabled:

            autotrade_enabled = True

            db_set(
                "last_chat_id",
                message.chat.id
            )

            save_runtime_settings()

            asyncio.create_task(
                autotrade_loop(message.chat.id)
            )

        await message.answer(
            "🟢 Автоторговля включена",
            reply_markup=keyboard
        )

    elif "🔴" in text or "авто выкл" in text_lower:

        autotrade_enabled = False

        save_runtime_settings()

        await message.answer(
            "🔴 Автоторговля выключена",
            reply_markup=keyboard
        )

    elif "🔄" in text or "синх" in text_lower:

        sync_positions_with_okx()

        positions = get_open_positions()

        await message.answer(

            f"✅ Синхронизация OKX выполнена\n\n"
            f"Активов под управлением: {len(positions)}\n\n"
            f"Теперь активы OKX добавлены в открытые позиции бота.",

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

    init_db()

    load_runtime_settings()

    sync_positions_with_okx()

    print(

        f"OKX ULTRA PRO MAX {BOT_VERSION} STARTED"

    )

    if autotrade_enabled:

        chat_id = db_get(
            "last_chat_id",
            None
        )

        if chat_id:

            asyncio.create_task(

                autotrade_loop(
                    chat_id
                )

            )

    await dp.start_polling(
        bot
    )


if __name__ == "__main__":

    asyncio.run(
        main()
    )
