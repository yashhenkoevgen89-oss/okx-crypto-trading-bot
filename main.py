import os
import json
import asyncio
from datetime import datetime, date

import pandas as pd

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

import okx.MarketData as MarketData
import okx.Account as Account
import okx.Trade as Trade


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

OKX_API_KEY = os.getenv("OKX_API_KEY")
OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE")

OKX_FLAG = os.getenv("OKX_FLAG", "1")
LIVE_TRADING_ENABLED = os.getenv("LIVE_TRADING_ENABLED", "NO")

TRADE_SYMBOL = os.getenv("TRADE_SYMBOL", "BTC-USDT")
TRADE_AMOUNT_USDT = float(os.getenv("TRADE_AMOUNT_USDT", "5"))

AUTO_INTERVAL = int(os.getenv("AUTO_INTERVAL", "300"))
STATE_FILE = "bot_state.json"

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
]


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


autotrade_enabled = False
auto_select_symbol = True

current_trade_symbol = TRADE_SYMBOL
trade_history = []
closed_trades = []
open_positions = {}

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


def save_state():
    data = {
        "autotrade_enabled": autotrade_enabled,
        "auto_select_symbol": auto_select_symbol,
        "current_trade_symbol": current_trade_symbol,
        "trade_history": trade_history[-300:],
        "closed_trades": closed_trades[-300:],
        "open_positions": open_positions,
        "risk_settings": risk_settings,
    }

    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def load_state():
    global autotrade_enabled
    global auto_select_symbol
    global current_trade_symbol
    global trade_history
    global closed_trades
    global open_positions

    if not os.path.exists(STATE_FILE):
        return False

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        autotrade_enabled = False
        auto_select_symbol = bool(data.get("auto_select_symbol", True))
        current_trade_symbol = data.get("current_trade_symbol", TRADE_SYMBOL)
        trade_history = data.get("trade_history", [])
        closed_trades = data.get("closed_trades", [])
        open_positions = data.get("open_positions", {})
        risk_settings.update(data.get("risk_settings", {}))

        return True

    except Exception:
        return False
def add_history(action, symbol, price, score, result=None):
    trade_history.append(
        {
            "time": now(),
            "date": today_str(),
            "action": action,
            "symbol": symbol,
            "price": price,
            "score": score,
            "result": str(result)[:700],
        }
    )

    if len(trade_history) > 300:
        trade_history.pop(0)

    save_state()


def add_closed_trade(symbol, entry, exit_price, amount, reason):
    pnl_percent = ((exit_price - entry) / entry) * 100 if entry > 0 else 0
    pnl_usdt = amount * pnl_percent / 100

    closed_trades.append(
        {
            "time": now(),
            "date": today_str(),
            "symbol": symbol,
            "entry": entry,
            "exit": exit_price,
            "amount": amount,
            "reason": reason,
            "pnl_percent": pnl_percent,
            "pnl_usdt": pnl_usdt,
        }
    )

    if len(closed_trades) > 300:
        closed_trades.pop(0)

    save_state()


def trades_today_count():
    return len(
        [
            item for item in trade_history
            if item.get("date") == today_str()
            and "AUTO" in item.get("action", "")
        ]
    )


def pnl_today_percent():
    trades = [
        item for item in closed_trades
        if item.get("date") == today_str()
    ]

    return sum(
        safe_float(item.get("pnl_percent"))
        for item in trades
    )


def total_pnl_percent():
    return sum(
        safe_float(item.get("pnl_percent"))
        for item in closed_trades
    )


def total_pnl_usdt():
    return sum(
        safe_float(item.get("pnl_usdt"))
        for item in closed_trades
    )


def can_trade_today():
    if trades_today_count() >= risk_settings["max_trades_day"]:
        return False, "Достигнут дневной лимит сделок"

    if pnl_today_percent() <= -abs(risk_settings["max_daily_loss_percent"]):
        return False, "Достигнут дневной лимит убытка"

    return True, "OK"


def can_open_new_position(symbol):
    if symbol in open_positions:
        return False, "По этой монете уже есть открытая позиция"

    if len(open_positions) >= risk_settings["max_open_positions"]:
        return False, "Достигнут лимит открытых позиций"

    allowed, reason = can_trade_today()

    if not allowed:
        return False, reason

    return True, "OK"


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
        f"Сила: {result['score']}%\n"
        f"Сигнал: {result['signal']}"
    )


def multi_timeframe_decision_for_symbol(symbol):
    results = [build_signal(symbol, bar) for bar in TIMEFRAMES]

    buy_count = sum(1 for item in results if item["signal"] == "BUY")
    sell_count = sum(1 for item in results if item["signal"] == "SELL")
    avg_score = int(sum(item["score"] for item in results) / len(results))

    if buy_count >= 2 and avg_score >= risk_settings["buy_score"]:
        final_signal = "BUY"
    elif sell_count >= 2 and avg_score <= risk_settings["sell_score"]:
        final_signal = "SELL"
    else:
        final_signal = "HOLD"

    price = results[1]["price"] if len(results) > 1 else results[0]["price"]

    return {
        "symbol": symbol,
        "signal": final_signal,
        "avg_score": avg_score,
        "price": price,
        "results": results,
    }


def sort_signals(results):
    return sorted(results, key=lambda item: item.get("score", 0), reverse=True)


def scan_market():
    results = []

    for coin in WATCHLIST:
        try:
            result = build_signal(coin, "15m")
            results.append(result)
        except Exception:
            continue

    return sort_signals(results)


def choose_best_symbol():
    results = scan_market()

    if not results:
        return TRADE_SYMBOL, None

    best = results[0]
    return best["symbol"], best
# =========================
# ОРДЕРА OKX
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
# ПОЗИЦИИ
# =========================

def open_position(symbol, entry_price, amount_usdt):

    stop_loss_price = entry_price * (
        1 - risk_settings["stop_loss_percent"] / 100
    )

    take_profit_price = entry_price * (
        1 + risk_settings["take_profit_percent"] / 100
    )

    open_positions[symbol] = {
        "symbol": symbol,
        "entry_price": entry_price,
        "amount_usdt": amount_usdt,
        "stop_loss_price": stop_loss_price,
        "take_profit_price": take_profit_price,
        "highest_price": entry_price,
        "time": now()
    }

    save_state()


def close_position(symbol, exit_price, reason):

    if symbol not in open_positions:
        return

    position = open_positions[symbol]

    add_closed_trade(
        symbol,
        position["entry_price"],
        exit_price,
        position["amount_usdt"],
        reason
    )

    del open_positions[symbol]

    save_state()


def update_trailing_stop(symbol, current_price):

    if symbol not in open_positions:
        return

    position = open_positions[symbol]

    if current_price > position["highest_price"]:

        position["highest_price"] = current_price

        new_stop = current_price * (
            1 - risk_settings["trailing_stop_percent"] / 100
        )

        if new_stop > position["stop_loss_price"]:
            position["stop_loss_price"] = new_stop

    save_state()


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

        f"Цена входа: "
        f"{signal_data['price']:.4f}\n"

        f"Сила сигнала: "
        f"{signal_data['score']}%"
    )


# =========================
# DEMO SELL
# =========================

async def do_demo_sell(message):

    if not open_positions:

        await message.answer(
            "📋 Открытых позиций нет."
        )

        return

    symbol = list(open_positions.keys())[0]

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

        f"Цена выхода: "
        f"{signal_data['price']:.4f}"
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

        f"Цена: "
        f"{signal_data['price']:.4f}"
    )
# =========================
# АВТОТОРГОВЛЯ
# =========================

async def autotrade_loop(chat_id):

    global autotrade_enabled
    global current_trade_symbol

    while autotrade_enabled:

        try:

            # --------------------------------
            # Проверка дневных лимитов
            # --------------------------------

            allowed, reason = can_trade_today()

            if not allowed:

                await bot.send_message(
                    chat_id,
                    f"⛔ Торговля остановлена\n\n{reason}"
                )

                autotrade_enabled = False
                save_state()

                break

            # --------------------------------
            # Сопровождение открытых позиций
            # --------------------------------

            symbols = list(open_positions.keys())

            for symbol in symbols:

                signal_data = build_signal(
                    symbol,
                    "15m"
                )

                current_price = signal_data["price"]

                update_trailing_stop(
                    symbol,
                    current_price
                )

                position = open_positions[symbol]

                # TAKE PROFIT

                if current_price >= position["take_profit_price"]:

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

                        f"{current_price:.4f}"
                    )

                    continue

                # STOP LOSS

                if current_price <= position["stop_loss_price"]:

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

                        f"{current_price:.4f}"
                    )

                    continue

                # SELL СИГНАЛ

                decision = multi_timeframe_decision_for_symbol(
                    symbol
                )

                if decision["signal"] == "SELL":

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

                        f"{current_price:.4f}"
                    )

            # --------------------------------
            # Поиск новой монеты
            # --------------------------------

            if len(open_positions) < risk_settings["max_open_positions"]:

                if auto_select_symbol:

                    trade_symbol, best = choose_best_symbol()

                else:

                    trade_symbol = current_trade_symbol

                current_trade_symbol = trade_symbol

                decision = multi_timeframe_decision_for_symbol(
                    trade_symbol
                )

                if decision["signal"] == "BUY":

                    allowed, reason = can_open_new_position(
                        trade_symbol
                    )

                    if allowed:

                        amount = min(
                            risk_settings["amount_usdt"],
                            risk_settings["max_amount_usdt"]
                        )

                        open_position(
                            trade_symbol,
                            decision["price"],
                            amount
                        )

                        add_history(
                            "AUTO BUY",
                            trade_symbol,
                            decision["price"],
                            decision["avg_score"]
                        )

                        await bot.send_message(

                            chat_id,

                            f"🟢 AUTO BUY\n\n"

                            f"{trade_symbol}\n"

                            f"Цена: "
                            f"{decision['price']:.4f}\n"

                            f"Сила сигнала: "
                            f"{decision['avg_score']}%"
                        )

        except Exception as error:

            await bot.send_message(

                chat_id,

                f"❌ Ошибка автоторговли\n\n"

                f"{error}"
            )

        await asyncio.sleep(AUTO_INTERVAL)


# =========================
# ВКЛ / ВЫКЛ АВТОТОРГОВЛИ
# =========================

async def enable_autotrade(message):

    global autotrade_enabled

    if autotrade_enabled:

        await message.answer(
            "🤖 Автоторговля уже включена."
        )

        return

    autotrade_enabled = True

    save_state()

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

    save_state()

    await message.answer(
        "🔴 Автоторговля выключена."
    )
# =========================
# LIVE SELL
# =========================

async def do_live_sell(message):

    if not is_live_allowed():
        await message.answer(
            "⛔ LIVE торговля запрещена.\n\n"
            "Установите:\n"
            "LIVE_TRADING_ENABLED=YES"
        )
        return

    if not open_positions:
        await message.answer("📋 Открытых позиций нет.")
        return

    symbol = list(open_positions.keys())[0]
    signal_data = build_signal(symbol)
    price = signal_data["price"]
    amount = open_positions[symbol]["amount_usdt"]

    result = place_market_sell(symbol, amount, price)

    close_position(symbol, price, "MANUAL LIVE SELL")

    add_history(
        "MANUAL LIVE SELL",
        symbol,
        price,
        signal_data["score"],
        result
    )

    await message.answer(
        f"🔥 LIVE SELL\n\n"
        f"{symbol}\n"
        f"Цена: {price:.4f}"
    )


# =========================
# SHOW FUNCTIONS
# =========================

async def show_status(message):

    mode = "DEMO 🧪" if is_demo() else "LIVE 🔥"

    await message.answer(
        f"📊 Статус\n\n"
        f"Режим: {mode}\n"
        f"LIVE разрешён: {'✅' if is_live_allowed() else '❌'}\n"
        f"Текущая монета: {current_trade_symbol}\n"
        f"Автоторговля: {'✅' if autotrade_enabled else '❌'}\n"
        f"Автовыбор монеты: {'✅' if auto_select_symbol else '❌'}\n"
        f"Открытых позиций: {len(open_positions)}"
    )


async def show_balance(message):

    try:
        result = account_api.get_account_balance()
        details = result.get("data", [{}])[0].get("details", [])

        text = "💰 Баланс\n\n"

        for item in details:
            balance = safe_float(item.get("cashBal"))
            available = safe_float(item.get("availBal"))

            if balance > 0 or available > 0:
                text += (
                    f"{item.get('ccy')}: "
                    f"{balance:.8f} / доступно {available:.8f}\n"
                )

        await message.answer(text if text.strip() != "💰 Баланс" else "Баланс пустой.")

    except Exception as e:
        await message.answer(f"❌ Ошибка баланса:\n{e}")


async def show_signal(message):

    try:
        result = build_signal(current_trade_symbol)
        await message.answer(format_signal(result))

    except Exception as e:
        await message.answer(f"❌ Ошибка сигнала:\n{e}")


async def show_market(message):

    try:
        decision = multi_timeframe_decision_for_symbol(current_trade_symbol)

        text = f"🌐 Мультианализ {current_trade_symbol}\n\n"

        for item in decision["results"]:
            text += f"{item['bar']} | {item['signal']} | {item['score']}%\n"

        text += (
            f"\nИтог: {decision['signal']}\n"
            f"Средняя сила: {decision['avg_score']}%"
        )

        await message.answer(text)

    except Exception as e:
        await message.answer(f"❌ Ошибка рынка:\n{e}")


async def show_scan(message):

    try:
        results = scan_market()

        if not results:
            await message.answer("❌ Нет данных сканера.")
            return

        text = "🔎 Сканер рынка\n\n"

        for item in results[:10]:
            text += (
                f"{item['symbol']} | "
                f"{item['signal']} | "
                f"{item['score']}%\n"
            )

        await message.answer(text)

    except Exception as e:
        await message.answer(f"❌ Ошибка сканера:\n{e}")


async def show_best(message):

    try:
        symbol, data = choose_best_symbol()

        if data is None:
            await message.answer("❌ Лучшая монета не найдена.")
            return

        await message.answer(
            f"🏆 Лучшая монета\n\n"
            f"{symbol}\n"
            f"Сигнал: {data['signal']}\n"
            f"Сила: {data['score']}%"
        )

    except Exception as e:
        await message.answer(f"❌ Ошибка:\n{e}")


async def show_top3(message):

    try:
        results = scan_market()

        if not results:
            await message.answer("❌ Нет данных.")
            return

        text = "🥇 ТОП-3 монеты\n\n"

        for i, item in enumerate(results[:3], start=1):
            text += (
                f"{i}. {item['symbol']} | "
                f"{item['signal']} | "
                f"{item['score']}%\n"
            )

        await message.answer(text)

    except Exception as e:
        await message.answer(f"❌ Ошибка:\n{e}")


async def show_positions(message):

    if not open_positions:
        await message.answer("📋 Открытых позиций нет.")
        return

    text = "📋 Открытые позиции\n\n"

    for symbol, position in open_positions.items():
        current_price = build_signal(symbol)["price"]

        pnl = (
            (current_price - position["entry_price"])
            / position["entry_price"]
        ) * 100

        text += (
            f"{symbol}\n"
            f"Вход: {position['entry_price']:.4f}\n"
            f"Текущая: {current_price:.4f}\n"
            f"PnL: {pnl:.2f}%\n"
            f"TP: {position['take_profit_price']:.4f}\n"
            f"SL: {position['stop_loss_price']:.4f}\n\n"
        )

    await message.answer(text)


async def show_current_symbol(message):

    await message.answer(
        f"💱 Текущая монета\n\n"
        f"{current_trade_symbol}"
    )


async def show_risk(message):

    await message.answer(
        f"🛡 Риск\n\n"
        f"Сумма сделки: {risk_settings['amount_usdt']} USDT\n"
        f"Максимум сделки: {risk_settings['max_amount_usdt']} USDT\n"
        f"SL: {risk_settings['stop_loss_percent']}%\n"
        f"TP: {risk_settings['take_profit_percent']}%\n"
        f"Trailing: {risk_settings['trailing_stop_percent']}%\n"
        f"Макс. позиций: {risk_settings['max_open_positions']}\n"
        f"Сделок в день: {risk_settings['max_trades_day']}\n"
        f"Лимит убытка в день: {risk_settings['max_daily_loss_percent']}%"
    )


async def show_history(message):

    if not trade_history:
        await message.answer("📜 История пуста.")
        return

    text = "📜 История\n\n"

    for item in trade_history[-10:]:
        text += (
            f"{item.get('time')}\n"
            f"{item.get('action')} | {item.get('symbol')}\n"
            f"Цена: {safe_float(item.get('price')):.4f}\n"
            f"Сила: {item.get('score')}%\n\n"
        )

    await message.answer(text)


async def show_statistics(message):

    buys = len([x for x in trade_history if "BUY" in x.get("action", "")])
    sells = len([x for x in trade_history if "SELL" in x.get("action", "")])

    await message.answer(
        f"📈 Статистика\n\n"
        f"Всего операций: {len(trade_history)}\n"
        f"BUY: {buys}\n"
        f"SELL: {sells}\n"
        f"Закрытых сделок: {len(closed_trades)}"
    )


async def show_pnl(message):

    await message.answer(
        f"💹 PnL\n\n"
        f"Всего закрытых сделок: {len(closed_trades)}\n"
        f"Общий PnL: {total_pnl_percent():.2f}%\n"
        f"USDT: {total_pnl_usdt():.2f}"
    )


# =========================
# COMMANDS
# =========================

@dp.message(Command(commands=["start", "старт"]))
async def start_cmd(message: types.Message):
    await message.answer(
        "🤖 OKX ULTRA PRO MAX запущен",
        reply_markup=keyboard
    )


# =========================
# TEXT ROUTER
# =========================

@dp.message()
async def text_router(message: types.Message):

    global auto_select_symbol
    global open_positions

    if not message.text:
        return

    text = message.text.lower().strip()

    try:

        if "авто статус" in text:
            await message.answer(
                f"🤖 Авто статус\n\n"
                f"Автоторговля: {'✅' if autotrade_enabled else '❌'}\n"
                f"Автовыбор монеты: {'✅' if auto_select_symbol else '❌'}\n"
                f"Текущая монета: {current_trade_symbol}\n"
                f"Открытых позиций: {len(open_positions)}"
            )

        elif "авто монета" in text:
            auto_select_symbol = not auto_select_symbol
            save_state()

            await message.answer(
                f"🧠 Автовыбор монеты: "
                f"{'включён ✅' if auto_select_symbol else 'выключен ❌'}"
            )

        elif "текущая монета" in text:
            await show_current_symbol(message)

        elif "авто вкл" in text:
            await enable_autotrade(message)

        elif "авто выкл" in text:
            await disable_autotrade(message)

        elif "топ-3" in text or "топ3" in text:
            await show_top3(message)

        elif "позиции" in text or "позиция" in text:
            await show_positions(message)

        elif "сброс" in text:
            open_positions = {}
            save_state()
            await message.answer("♻️ Все позиции сброшены.")

        elif "купить live" in text:
            await do_live_buy(message)

        elif "продать live" in text:
            await do_live_sell(message)

        elif "купить demo" in text:
            await do_demo_buy(message)

        elif "продать demo" in text:
            await do_demo_sell(message)

        elif "статус" in text:
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

        elif "риск" in text:
            await show_risk(message)

        elif "история" in text:
            await show_history(message)

        elif "статистика" in text:
            await show_statistics(message)

        elif "pnl" in text:
            await show_pnl(message)

        else:
            await message.answer("❓ Команда не распознана.\nНажми /старт")

    except Exception as e:
        await message.answer(f"❌ Ошибка:\n{e}")


# =========================
# MAIN
# =========================

async def main():

    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Нет TELEGRAM_BOT_TOKEN")

    load_state()

    print("OKX ULTRA PRO MAX started")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
