import os
import json
import asyncio
from datetime import datetime

import pandas as pd

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

import okx.MarketData as MarketData
import okx.Account as Account
import okx.Trade as Trade


# ============================================================
# OKX TELEGRAM TRADING BOT - ENGLISH ASCII VERSION
# DEMO MODE ONLY FOR ORDERS
# ============================================================


# -----------------------------
# Environment variables
# -----------------------------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

OKX_API_KEY = os.getenv("OKX_API_KEY")
OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE")
OKX_FLAG = os.getenv("OKX_FLAG", "1")  # 1 = demo, 0 = live

TRADE_SYMBOL = os.getenv("TRADE_SYMBOL", "BTC-USDT")
TRADE_AMOUNT_USDT = float(os.getenv("TRADE_AMOUNT_USDT", "10"))

AUTO_INTERVAL = int(os.getenv("AUTO_INTERVAL", "300"))
STATE_FILE = "bot_state.json"

BUY_SCORE = int(os.getenv("BUY_SCORE", "65"))
SELL_SCORE = int(os.getenv("SELL_SCORE", "35"))

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


# -----------------------------
# Telegram and OKX clients
# -----------------------------

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


# -----------------------------
# Runtime state
# -----------------------------

autotrade_enabled = False
auto_select_symbol = True
current_trade_symbol = TRADE_SYMBOL
trade_history = []

risk_settings = {
    "amount_usdt": TRADE_AMOUNT_USDT,
    "max_amount_usdt": 50.0,
    "stop_loss_percent": 2.0,
    "take_profit_percent": 4.0,
    "buy_score": BUY_SCORE,
    "sell_score": SELL_SCORE,
}


# -----------------------------
# Telegram keyboard
# -----------------------------

keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Status"), KeyboardButton(text="Balance")],
        [KeyboardButton(text="Signal"), KeyboardButton(text="Market")],
        [KeyboardButton(text="Scanner"), KeyboardButton(text="Best coin")],
        [KeyboardButton(text="Demo buy"), KeyboardButton(text="Demo sell")],
        [KeyboardButton(text="Auto on"), KeyboardButton(text="Auto off")],
        [KeyboardButton(text="Auto coin"), KeyboardButton(text="Current coin")],
        [KeyboardButton(text="Auto status"), KeyboardButton(text="Risk")],
        [KeyboardButton(text="History"), KeyboardButton(text="Statistics")],
    ],
    resize_keyboard=True,
)


# ============================================================
# Helpers
# ============================================================

def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_demo() -> bool:
    return str(OKX_FLAG) == "1"


def safe_float(value, default=0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def save_state() -> None:
    data = {
        "autotrade_enabled": autotrade_enabled,
        "auto_select_symbol": auto_select_symbol,
        "current_trade_symbol": current_trade_symbol,
        "trade_history": trade_history[-100:],
        "risk_settings": risk_settings,
    }

    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def load_state() -> bool:
    global autotrade_enabled
    global auto_select_symbol
    global current_trade_symbol
    global trade_history

    if not os.path.exists(STATE_FILE):
        return False

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        autotrade_enabled = False
        auto_select_symbol = bool(data.get("auto_select_symbol", True))
        current_trade_symbol = data.get("current_trade_symbol", TRADE_SYMBOL)
        trade_history = data.get("trade_history", [])
        risk_settings.update(data.get("risk_settings", {}))
        return True
    except Exception:
        return False


def add_history(action: str, symbol: str, price: float, score: int, result=None) -> None:
    trade_history.append(
        {
            "time": now(),
            "action": action,
            "symbol": symbol,
            "price": price,
            "score": score,
            "result": str(result)[:700],
        }
    )

    if len(trade_history) > 100:
        trade_history.pop(0)

    save_state()


# ============================================================
# Indicators
# ============================================================

def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()

    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss.replace(0, 1e-9)
    rsi = 100 - (100 / (1 + rs))

    return rsi.fillna(50)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df["ema9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()

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

    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr"] = true_range.rolling(14).mean().fillna(0)

    return df


# ============================================================
# Signal scoring
# ============================================================

def calculate_score(data: dict) -> tuple[int, str]:
    score = 50

    close = data["close"]
    ema9 = data["ema9"]
    ema21 = data["ema21"]
    rsi = data["rsi"]
    macd = data["macd"]
    macd_signal = data["macd_signal"]
    vol = data["vol"]
    vol_avg = data["vol_avg"]
    bb_upper = data["bb_upper"]
    bb_lower = data["bb_lower"]

    if ema9 > ema21:
        score += 15
    else:
        score -= 15

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


# ============================================================
# Market data
# ============================================================

def get_market_data(symbol=None, bar="15m", limit=100) -> pd.DataFrame:
    symbol = symbol or TRADE_SYMBOL

    result = market_api.get_candlesticks(
        instId=symbol,
        bar=bar,
        limit=str(limit),
    )

    candles = result.get("data", [])

    if not candles:
        raise Exception(f"OKX returned no candles for {symbol}")

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


def build_signal(symbol=None, bar="15m") -> dict:
    symbol = symbol or TRADE_SYMBOL

    df = get_market_data(symbol, bar)
    last = df.iloc[-1]

    data = {
        "close": safe_float(last["close"]),
        "vol": safe_float(last["vol"]),
        "ema9": safe_float(last["ema9"]),
        "ema21": safe_float(last["ema21"]),
        "rsi": safe_float(last["rsi"]),
        "macd": safe_float(last["macd"]),
        "macd_signal": safe_float(last["macd_signal"]),
        "bb_upper": safe_float(last.get("bb_upper", 0)),
        "bb_lower": safe_float(last.get("bb_lower", 0)),
        "vol_avg": safe_float(last.get("vol_avg", 0)),
        "atr": safe_float(last.get("atr", 0)),
    }

    score, signal = calculate_score(data)

    trend = "UP" if data["ema9"] > data["ema21"] else "DOWN"

    return {
        "symbol": symbol,
        "bar": bar,
        "price": data["close"],
        "rsi": data["rsi"],
        "ema9": data["ema9"],
        "ema21": data["ema21"],
        "macd": data["macd"],
        "macd_signal": data["macd_signal"],
        "atr": data["atr"],
        "score": score,
        "signal": signal,
        "trend": trend,
    }


def format_signal(result: dict) -> str:
    return (
        f"Signal: {result['symbol']} | {result['bar']}\n\n"
        f"Price: {result['price']:.4f}\n"
        f"Trend: {result['trend']}\n"
        f"RSI: {result['rsi']:.2f}\n"
        f"EMA9: {result['ema9']:.4f}\n"
        f"EMA21: {result['ema21']:.4f}\n"
        f"MACD: {result['macd']:.4f}\n"
        f"MACD Signal: {result['macd_signal']:.4f}\n"
        f"ATR: {result['atr']:.4f}\n\n"
        f"Score: {result['score']}%\n"
        f"Decision: {result['signal']}"
    )


def multi_timeframe_decision_for_symbol(symbol: str) -> dict:
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


def sort_signals(results: list[dict]) -> list[dict]:
    return sorted(results, key=lambda item: item.get("score", 0), reverse=True)


def get_best_coin(results: list[dict]) -> dict:
    return sort_signals(results)[0]


def choose_best_symbol() -> tuple[str, dict | None]:
    results = []

    for coin in WATCHLIST:
        try:
            result = build_signal(coin, "15m")
            results.append(result)
        except Exception:
            continue

    if not results:
        return TRADE_SYMBOL, None

    best = get_best_coin(results)
    return best["symbol"], best


# ============================================================
# OKX trading
# ============================================================

def place_market_buy(symbol: str, amount_usdt: float):
    if not is_demo():
        raise Exception("LIVE trading is blocked")

    return trade_api.place_order(
        instId=symbol,
        tdMode="cash",
        side="buy",
        ordType="market",
        sz=str(amount_usdt),
        tgtCcy="quote_ccy",
    )


def place_market_sell(symbol: str, amount_usdt: float, price: float):
    if not is_demo():
        raise Exception("LIVE trading is blocked")

    base_amount = amount_usdt / price

    return trade_api.place_order(
        instId=symbol,
        tdMode="cash",
        side="sell",
        ordType="market",
        sz=str(round(base_amount, 8)),
    )


def get_okx_balance_text() -> str:
    result = account_api.get_account_balance()

    if result.get("code") != "0":
        return f"OKX balance error:\n{result}"

    details = result.get("data", [{}])[0].get("details", [])

    lines = ["OKX Demo balance:\n"]

    for item in details:
        currency = item.get("ccy")
        balance = safe_float(item.get("cashBal"))
        available = safe_float(item.get("availBal"))

        if balance > 0 or available > 0:
            lines.append(
                f"{currency}: balance {balance:.8f}, available {available:.8f}"
            )

    if len(lines) == 1:
        return "Balance is empty."

    return "\n".join(lines)


# ============================================================
# Screens
# ============================================================

async def show_status(message: types.Message):
    await message.answer(
        f"Bot status\n\n"
        f"Mode: {'DEMO' if is_demo() else 'LIVE'}\n"
        f"Base symbol: {TRADE_SYMBOL}\n"
        f"Current symbol: {current_trade_symbol}\n"
        f"Trade amount: {risk_settings['amount_usdt']} USDT\n"
        f"BUY score: {risk_settings['buy_score']}%\n"
        f"SELL score: {risk_settings['sell_score']}%\n"
        f"Auto coin: {'ON' if auto_select_symbol else 'OFF'}\n"
        f"Autotrade: {'ON' if autotrade_enabled else 'OFF'}"
    )


async def show_balance(message: types.Message):
    try:
        await message.answer(get_okx_balance_text())
    except Exception as error:
        await message.answer(f"Balance error:\n{error}")


async def show_signal(message: types.Message):
    try:
        result = build_signal(TRADE_SYMBOL, "15m")

        add_history(
            "SIGNAL",
            result["symbol"],
            result["price"],
            result["score"],
            result,
        )

        await message.answer(format_signal(result))
    except Exception as error:
        await message.answer(f"Signal error:\n{error}")


async def show_market(message: types.Message):
    try:
        decision = multi_timeframe_decision_for_symbol(TRADE_SYMBOL)

        text = f"Multi-timeframe analysis {TRADE_SYMBOL}\n\n"

        for item in decision["results"]:
            text += (
                f"{item['bar']} | "
                f"{item['signal']} | "
                f"{item['score']}%\n"
            )

        text += (
            f"\nFinal: {decision['signal']}\n"
            f"Average score: {decision['avg_score']}%"
        )

        await message.answer(text)
    except Exception as error:
        await message.answer(f"Market error:\n{error}")


async def show_scan(message: types.Message):
    results = []

    for coin in WATCHLIST:
        try:
            result = build_signal(coin, "15m")
            results.append(result)
        except Exception:
            continue

    if not results:
        await message.answer("Scanner received no data.")
        return

    results = sort_signals(results)

    text = "Market scanner\n\n"

    for item in results[:10]:
        text += (
            f"{item['symbol']} | "
            f"{item['signal']} | "
            f"{item['score']}% | "
            f"{item['price']:.4f}\n"
        )

    await message.answer(text)


async def show_best(message: types.Message):
    results = []

    for coin in WATCHLIST:
        try:
            result = build_signal(coin, "15m")
            results.append(result)
        except Exception:
            continue

    if not results:
        await message.answer("Best coin was not found.")
        return

    best = get_best_coin(results)

    await message.answer(
        "Best coin:\n\n" + format_signal(best)
    )


async def show_current_symbol(message: types.Message):
    await message.answer(
        f"Current coin\n\n"
        f"Base symbol: {TRADE_SYMBOL}\n"
        f"Current trading symbol: {current_trade_symbol}\n"
        f"Auto coin: {'ON' if auto_select_symbol else 'OFF'}"
    )


async def show_risk(message: types.Message):
    await message.answer(
        f"Risk settings\n\n"
        f"Trade amount: {risk_settings['amount_usdt']} USDT\n"
        f"Max amount: {risk_settings['max_amount_usdt']} USDT\n"
        f"Stop loss: {risk_settings['stop_loss_percent']}%\n"
        f"Take profit: {risk_settings['take_profit_percent']}%"
    )


async def show_history(message: types.Message):
    if not trade_history:
        await message.answer("History is empty.")
        return

    text = "Last operations\n\n"

    for item in trade_history[-10:]:
        text += (
            f"{item.get('time')}\n"
            f"{item.get('action')} | {item.get('symbol')}\n"
            f"Price: {safe_float(item.get('price')):.4f}\n"
            f"Score: {item.get('score')}%\n\n"
        )

    await message.answer(text)


async def show_statistics(message: types.Message):
    buys = len([item for item in trade_history if "BUY" in item.get("action", "")])
    sells = len([item for item in trade_history if "SELL" in item.get("action", "")])

    await message.answer(
        f"Statistics\n\n"
        f"Total operations: {len(trade_history)}\n"
        f"BUY operations: {buys}\n"
        f"SELL operations: {sells}"
    )


# ============================================================
# Manual DEMO orders
# ============================================================

async def do_demo_buy(message: types.Message):
    if not is_demo():
        await message.answer("Buy is allowed only in DEMO mode.")
        return

    try:
        signal_data = build_signal(TRADE_SYMBOL, "15m")

        amount = min(
            risk_settings["amount_usdt"],
            risk_settings["max_amount_usdt"],
        )

        order = place_market_buy(TRADE_SYMBOL, amount)

        add_history(
            "MANUAL DEMO BUY",
            TRADE_SYMBOL,
            signal_data["price"],
            signal_data["score"],
            order,
        )

        await message.answer(
            f"DEMO BUY order was sent to OKX\n\n"
            f"Symbol: {TRADE_SYMBOL}\n"
            f"Amount: {amount} USDT\n"
            f"Price: {signal_data['price']:.4f}\n\n"
            f"OKX response:\n{order}"
        )
    except Exception as error:
        await message.answer(f"Buy error:\n{error}")


async def do_demo_sell(message: types.Message):
    if not is_demo():
        await message.answer("Sell is allowed only in DEMO mode.")
        return

    try:
        signal_data = build_signal(TRADE_SYMBOL, "15m")

        amount = min(
            risk_settings["amount_usdt"],
            risk_settings["max_amount_usdt"],
        )

        order = place_market_sell(
            TRADE_SYMBOL,
            amount,
            signal_data["price"],
        )

        add_history(
            "MANUAL DEMO SELL",
            TRADE_SYMBOL,
            signal_data["price"],
            signal_data["score"],
            order,
        )

        await message.answer(
            f"DEMO SELL order was sent to OKX\n\n"
            f"Symbol: {TRADE_SYMBOL}\n"
            f"Amount: about {amount} USDT\n"
            f"Price: {signal_data['price']:.4f}\n\n"
            f"OKX response:\n{order}"
        )
    except Exception as error:
        await message.answer(f"Sell error:\n{error}")


# ============================================================
# Autotrade
# ============================================================

async def autotrade_loop(chat_id: int):
    global autotrade_enabled
    global current_trade_symbol

    while autotrade_enabled:
        try:
            if not is_demo():
                autotrade_enabled = False
                save_state()

                await bot.send_message(
                    chat_id,
                    "LIVE mode is blocked. Autotrade stopped."
                )
                return

            trade_symbol = TRADE_SYMBOL

            if auto_select_symbol:
                trade_symbol, best_data = choose_best_symbol()

                if best_data:
                    await bot.send_message(
                        chat_id,
                        f"Auto coin selection\n\n"
                        f"Selected: {trade_symbol}\n"
                        f"Signal: {best_data['signal']}\n"
                        f"Score: {best_data['score']}%"
                    )

            current_trade_symbol = trade_symbol
            save_state()

            decision = multi_timeframe_decision_for_symbol(trade_symbol)

            if decision["signal"] == "BUY":
                amount = min(
                    risk_settings["amount_usdt"],
                    risk_settings["max_amount_usdt"],
                )

                order = place_market_buy(trade_symbol, amount)

                add_history(
                    "AUTO DEMO BUY",
                    trade_symbol,
                    decision["price"],
                    decision["avg_score"],
                    order,
                )

                await bot.send_message(
                    chat_id,
                    f"AUTO DEMO BUY\n\n"
                    f"Symbol: {trade_symbol}\n"
                    f"Price: {decision['price']:.4f}\n"
                    f"Score: {decision['avg_score']}%\n\n"
                    f"OKX response:\n{order}"
                )

            elif decision["signal"] == "SELL":
                amount = min(
                    risk_settings["amount_usdt"],
                    risk_settings["max_amount_usdt"],
                )

                order = place_market_sell(
                    trade_symbol,
                    amount,
                    decision["price"],
                )

                add_history(
                    "AUTO DEMO SELL",
                    trade_symbol,
                    decision["price"],
                    decision["avg_score"],
                    order,
                )

                await bot.send_message(
                    chat_id,
                    f"AUTO DEMO SELL\n\n"
                    f"Symbol: {trade_symbol}\n"
                    f"Price: {decision['price']:.4f}\n"
                    f"Score: {decision['avg_score']}%\n\n"
                    f"OKX response:\n{order}"
                )

        except Exception as error:
            await bot.send_message(
                chat_id,
                f"Autotrade error:\n{error}"
            )

        await asyncio.sleep(AUTO_INTERVAL)


# ============================================================
# Commands
# ============================================================

@dp.message(Command(commands=["start"]))
async def start(message: types.Message):
    await message.answer(
        "OKX Crypto Trading Bot PRO MAX started.\n\n"
        "Commands:\n"
        "/status\n"
        "/balance\n"
        "/signal\n"
        "/market\n"
        "/scan\n"
        "/best\n"
        "/buy\n"
        "/sell\n"
        "/autotrade_on\n"
        "/autotrade_off\n"
        "/autotrade_status\n"
        "/auto_coin\n"
        "/current_coin\n"
        "/risk\n"
        "/history\n"
        "/statistics",
        reply_markup=keyboard,
    )


@dp.message(Command(commands=["status"]))
async def status(message: types.Message):
    await show_status(message)


@dp.message(Command(commands=["balance"]))
async def balance(message: types.Message):
    await show_balance(message)


@dp.message(Command(commands=["signal"]))
async def signal(message: types.Message):
    await show_signal(message)


@dp.message(Command(commands=["market"]))
async def market(message: types.Message):
    await show_market(message)


@dp.message(Command(commands=["scan", "scanner"]))
async def scan(message: types.Message):
    await show_scan(message)


@dp.message(Command(commands=["best"]))
async def best(message: types.Message):
    await show_best(message)


@dp.message(Command(commands=["buy", "demo_buy"]))
async def buy_command(message: types.Message):
    await do_demo_buy(message)


@dp.message(Command(commands=["sell", "demo_sell"]))
async def sell_command(message: types.Message):
    await do_demo_sell(message)


@dp.message(Command(commands=["risk"]))
async def risk(message: types.Message):
    await show_risk(message)


@dp.message(Command(commands=["history"]))
async def history(message: types.Message):
    await show_history(message)


@dp.message(Command(commands=["statistics", "stat", "pnl"]))
async def statistics(message: types.Message):
    await show_statistics(message)


@dp.message(Command(commands=["autotrade_on"]))
async def autotrade_on(message: types.Message):
    global autotrade_enabled

    if not is_demo():
        await message.answer("Autotrade is allowed only in DEMO mode.")
        return

    if autotrade_enabled:
        await message.answer("Autotrade is already ON.")
        return

    autotrade_enabled = True
    save_state()

    await message.answer(
        f"Autotrade is ON.\n\n"
        f"The bot will check the market every {AUTO_INTERVAL // 60} minutes."
    )

    asyncio.create_task(autotrade_loop(message.chat.id))


@dp.message(Command(commands=["autotrade_off"]))
async def autotrade_off(message: types.Message):
    global autotrade_enabled

    autotrade_enabled = False
    save_state()

    await message.answer("Autotrade is OFF.")


@dp.message(Command(commands=["autotrade_status"]))
async def autotrade_status(message: types.Message):
    await message.answer(
        f"Autotrade status\n\n"
        f"Autotrade: {'ON' if autotrade_enabled else 'OFF'}\n"
        f"Auto coin: {'ON' if auto_select_symbol else 'OFF'}\n"
        f"Current coin: {current_trade_symbol}\n"
        f"Interval: {AUTO_INTERVAL} seconds\n"
        f"Trade amount: {risk_settings['amount_usdt']} USDT"
    )


@dp.message(Command(commands=["auto_coin"]))
async def auto_coin_toggle(message: types.Message):
    global auto_select_symbol

    auto_select_symbol = not auto_select_symbol
    save_state()

    await message.answer(
        f"Auto coin: {'ON' if auto_select_symbol else 'OFF'}"
    )


@dp.message(Command(commands=["current_coin"]))
async def current_coin(message: types.Message):
    await show_current_symbol(message)


@dp.message(Command(commands=["save"]))
async def save_cmd(message: types.Message):
    save_state()
    await message.answer("State saved.")


@dp.message(Command(commands=["load"]))
async def load_cmd(message: types.Message):
    ok = load_state()
    await message.answer("State loaded." if ok else "State file not found.")


# ============================================================
# Text router
# ============================================================

@dp.message()
async def text_router(message: types.Message):
    if not message.text:
        return

    text = message.text.lower().strip()

    if "auto coin" in text:
        await auto_coin_toggle(message)
    elif "current coin" in text:
        await show_current_symbol(message)
    elif "auto on" in text:
        await autotrade_on(message)
    elif "auto off" in text:
        await autotrade_off(message)
    elif "auto status" in text:
        await autotrade_status(message)
    elif "status" in text:
        await show_status(message)
    elif "balance" in text:
        await show_balance(message)
    elif "signal" in text:
        await show_signal(message)
    elif "market" in text:
        await show_market(message)
    elif "scanner" in text or "scan" in text:
        await show_scan(message)
    elif "best" in text:
        await show_best(message)
    elif "buy" in text:
        await do_demo_buy(message)
    elif "sell" in text:
        await do_demo_sell(message)
    elif "risk" in text:
        await show_risk(message)
    elif "history" in text:
        await show_history(message)
    elif "statistics" in text or "stat" in text:
        await show_statistics(message)
    else:
        await message.answer("Unknown command. Press /start.")


# ============================================================
# Main
# ============================================================

async def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

    load_state()

    print("OKX Crypto Trading Bot PRO MAX ASCII version started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
