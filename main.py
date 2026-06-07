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


# =========================
# НАСТРОЙКИ
# =========================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

OKX_API_KEY = os.getenv("OKX_API_KEY")
OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE")
OKX_FLAG = os.getenv("OKX_FLAG", "1")

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
# СОСТОЯНИЕ
# =========================

autotrade_enabled = False
auto_select_symbol = True

current_trade_symbol = TRADE_SYMBOL
trade_history = []
closed_trades = []
open_position = None

risk_settings = {
    "amount_usdt": TRADE_AMOUNT_USDT,
    "max_amount_usdt": 50.0,
    "stop_loss_percent": 2.0,
    "take_profit_percent": 4.0,
    "trailing_stop_percent": 1.5,
    "buy_score": BUY_SCORE,
    "sell_score": SELL_SCORE,
    "max_trades_day": 10,
    "max_loss_day_percent": 5.0,
}


# =========================
# КНОПКИ
# =========================

keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Статус"), KeyboardButton(text="💰 Баланс")],
        [KeyboardButton(text="📡 Сигнал"), KeyboardButton(text="🌐 Рынок")],
        [KeyboardButton(text="🔎 Сканер"), KeyboardButton(text="🏆 Лучшая")],
        [KeyboardButton(text="🥇 Топ-3"), KeyboardButton(text="📋 Позиция")],
        [KeyboardButton(text="🟢 Купить DEMO"), KeyboardButton(text="🔴 Продать DEMO")],
        [KeyboardButton(text="🟢 Авто ВКЛ"), KeyboardButton(text="🔴 Авто ВЫКЛ")],
        [KeyboardButton(text="🧠 Авто монета"), KeyboardButton(text="💱 Текущая монета")],
        [KeyboardButton(text="🤖 Авто статус"), KeyboardButton(text="🛡 Риск")],
        [KeyboardButton(text="📜 История"), KeyboardButton(text="📈 Статистика")],
        [KeyboardButton(text="💹 PnL"), KeyboardButton(text="♻️ Сброс позиции")],
    ],
    resize_keyboard=True,
)


# =========================
# ПОМОЩНИКИ
# =========================

def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str() -> str:
    return date.today().isoformat()


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
        "trade_history": trade_history[-200:],
        "closed_trades": closed_trades[-200:],
        "open_position": open_position,
        "risk_settings": risk_settings,
    }

    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def load_state() -> bool:
    global autotrade_enabled
    global auto_select_symbol
    global current_trade_symbol
    global trade_history
    global closed_trades
    global open_position

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
        open_position = data.get("open_position")
        risk_settings.update(data.get("risk_settings", {}))

        return True

    except Exception:
        return False


def add_history(action: str, symbol: str, price: float, score: int, result=None) -> None:
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

    if len(trade_history) > 200:
        trade_history.pop(0)

    save_state()


def add_closed_trade(symbol: str, entry: float, exit_price: float, amount: float, reason: str) -> None:
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

    if len(closed_trades) > 200:
        closed_trades.pop(0)

    save_state()


def trades_today_count() -> int:
    return len([x for x in trade_history if x.get("date") == today_str() and "AUTO" in x.get("action", "")])


def pnl_today_percent() -> float:
    trades = [x for x in closed_trades if x.get("date") == today_str()]
    return sum(safe_float(x.get("pnl_percent")) for x in trades)


def can_trade_today() -> tuple[bool, str]:
    if trades_today_count() >= risk_settings["max_trades_day"]:
        return False, "Достигнут дневной лимит сделок"

    if pnl_today_percent() <= -abs(risk_settings["max_loss_day_percent"]):
        return False, "Достигнут дневной лимит убытка"

    return True, "OK"
# =========================
# ИНДИКАТОРЫ
# =========================

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


# =========================
# СИГНАЛЫ
# =========================

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


def get_market_data(symbol=None, bar="15m", limit=100) -> pd.DataFrame:
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
    trend = "восходящий" if data["ema9"] > data["ema21"] else "нисходящий"

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
        f"📡 {result['symbol']} | {result['bar']}\n\n"
        f"Цена: {result['price']:.4f}\n"
        f"Тренд: {result['trend']}\n"
        f"RSI: {result['rsi']:.2f}\n"
        f"EMA9: {result['ema9']:.4f}\n"
        f"EMA21: {result['ema21']:.4f}\n"
        f"MACD: {result['macd']:.4f}\n"
        f"MACD Signal: {result['macd_signal']:.4f}\n"
        f"ATR: {result['atr']:.4f}\n\n"
        f"Сила: {result['score']}%\n"
        f"Сигнал: {result['signal']}"
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


def scan_market() -> list[dict]:
    results = []

    for coin in WATCHLIST:
        try:
            result = build_signal(coin, "15m")
            results.append(result)
        except Exception:
            continue

    return sort_signals(results)


def choose_best_symbol() -> tuple[str, dict | None]:
    results = scan_market()

    if not results:
        return TRADE_SYMBOL, None

    best = results[0]
    return best["symbol"], best
# =========================
# OKX СДЕЛКИ
# =========================

def place_market_buy(symbol: str, amount_usdt: float):

    if not is_demo():
        raise Exception("LIVE торговля заблокирована")

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
        raise Exception("LIVE торговля заблокирована")

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
        return f"❌ Ошибка OKX:\n{result}"

    details = result.get("data", [{}])[0].get("details", [])

    text = "💰 Баланс OKX DEMO\n\n"

    for item in details:

        ccy = item.get("ccy")
        balance = safe_float(item.get("cashBal"))

        if balance > 0:
            text += f"{ccy}: {balance:.8f}\n"

    return text


# =========================
# РАБОТА С ПОЗИЦИЕЙ
# =========================

def open_new_position(
    symbol: str,
    entry_price: float,
    amount_usdt: float
):

    global open_position

    stop_loss_price = entry_price * (
        1 - risk_settings["stop_loss_percent"] / 100
    )

    take_profit_price = entry_price * (
        1 + risk_settings["take_profit_percent"] / 100
    )

    open_position = {
        "symbol": symbol,
        "entry_price": entry_price,
        "amount_usdt": amount_usdt,
        "stop_loss_price": stop_loss_price,
        "take_profit_price": take_profit_price,
        "highest_price": entry_price,
        "time": now(),
    }

    save_state()


def close_position(
    exit_price: float,
    reason: str
):

    global open_position

    if open_position is None:
        return

    add_closed_trade(
        open_position["symbol"],
        open_position["entry_price"],
        exit_price,
        open_position["amount_usdt"],
        reason
    )

    open_position = None

    save_state()


def update_trailing_stop(current_price: float):

    global open_position

    if open_position is None:
        return

    if current_price > open_position["highest_price"]:

        open_position["highest_price"] = current_price

        new_stop = current_price * (
            1 - risk_settings["trailing_stop_percent"] / 100
        )

        if new_stop > open_position["stop_loss_price"]:
            open_position["stop_loss_price"] = new_stop

    save_state()


# =========================
# PNL
# =========================

def total_pnl_percent():

    return sum(
        safe_float(x.get("pnl_percent"))
        for x in closed_trades
    )


def total_pnl_usdt():

    return sum(
        safe_float(x.get("pnl_usdt"))
        for x in closed_trades
    )


# =========================
# ТОП МОНЕТЫ
# =========================

def top_three_coins():

    results = scan_market()

    return results[:3]


# =========================
# ПОКАЗ ПОЗИЦИИ
# =========================

async def show_position(message: types.Message):

    if open_position is None:

        await message.answer(
            "📋 Открытых позиций нет."
        )

        return

    symbol = open_position["symbol"]

    current_price = build_signal(
        symbol,
        "15m"
    )["price"]

    pnl_percent = (
        (
            current_price
            -
            open_position["entry_price"]
        )
        /
        open_position["entry_price"]
    ) * 100

    await message.answer(

        f"📋 Открытая позиция\n\n"

        f"Пара: {symbol}\n"

        f"Цена входа: "
        f"{open_position['entry_price']:.4f}\n"

        f"Текущая цена: "
        f"{current_price:.4f}\n"

        f"PnL: {pnl_percent:.2f}%\n"

        f"SL: "
        f"{open_position['stop_loss_price']:.4f}\n"

        f"TP: "
        f"{open_position['take_profit_price']:.4f}"
    )


# =========================
# СТАТИСТИКА
# =========================

async def show_pnl(message: types.Message):

    await message.answer(

        f"💹 PnL\n\n"

        f"Всего закрытых сделок: "
        f"{len(closed_trades)}\n\n"

        f"Общий результат:\n"

        f"{total_pnl_percent():.2f}%\n"

        f"{total_pnl_usdt():.2f} USDT"
    )


async def show_top3(message: types.Message):

    results = top_three_coins()

    if not results:

        await message.answer(
            "❌ Нет данных."
        )

        return

    text = "🥇 ТОП-3 монеты\n\n"

    place = 1

    for item in results:

        text += (
            f"{place}. "
            f"{item['symbol']} | "
            f"{item['signal']} | "
            f"{item['score']}%\n"
        )

        place += 1

    await message.answer(text)
# =========================
# АВТОТОРГОВЛЯ PRO MAX
# =========================

async def autotrade_loop(chat_id: int):

    global autotrade_enabled
    global current_trade_symbol
    global open_position

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

                return


            # --------------------------------
            # Автосопровождение позиции
            # --------------------------------

            if open_position is not None:

                symbol = open_position["symbol"]

                current_price = build_signal(
                    symbol,
                    "15m"
                )["price"]

                update_trailing_stop(current_price)


                # TAKE PROFIT

                if current_price >= open_position["take_profit_price"]:

                    amount = open_position["amount_usdt"]

                    place_market_sell(
                        symbol,
                        amount,
                        current_price
                    )

                    close_position(
                        current_price,
                        "TAKE PROFIT"
                    )

                    await bot.send_message(
                        chat_id,
                        f"🎯 TAKE PROFIT\n\n"
                        f"{symbol}\n"
                        f"{current_price:.4f}"
                    )

                    await asyncio.sleep(AUTO_INTERVAL)
                    continue


                # STOP LOSS

                if current_price <= open_position["stop_loss_price"]:

                    amount = open_position["amount_usdt"]

                    place_market_sell(
                        symbol,
                        amount,
                        current_price
                    )

                    close_position(
                        current_price,
                        "STOP LOSS"
                    )

                    await bot.send_message(
                        chat_id,
                        f"🛑 STOP LOSS\n\n"
                        f"{symbol}\n"
                        f"{current_price:.4f}"
                    )

                    await asyncio.sleep(AUTO_INTERVAL)
                    continue


            # --------------------------------
            # Если позиция уже открыта
            # новую не открываем
            # --------------------------------

            if open_position is not None:

                await asyncio.sleep(AUTO_INTERVAL)
                continue


            # --------------------------------
            # Автовыбор монеты
            # --------------------------------

            trade_symbol = TRADE_SYMBOL

            if auto_select_symbol:

                trade_symbol, best = choose_best_symbol()

                if best:

                    await bot.send_message(
                        chat_id,
                        f"🧠 Выбрана монета\n\n"
                        f"{trade_symbol}\n"
                        f"Сигнал: {best['signal']}\n"
                        f"Сила: {best['score']}%"
                    )

            current_trade_symbol = trade_symbol

            save_state()


            # --------------------------------
            # Multi timeframe
            # --------------------------------

            decision = multi_timeframe_decision_for_symbol(
                trade_symbol
            )


            # --------------------------------
            # BUY
            # --------------------------------

            if decision["signal"] == "BUY":

                amount = min(
                    risk_settings["amount_usdt"],
                    risk_settings["max_amount_usdt"]
                )

                order = place_market_buy(
                    trade_symbol,
                    amount
                )

                open_new_position(
                    trade_symbol,
                    decision["price"],
                    amount
                )

                add_history(
                    "AUTO DEMO BUY",
                    trade_symbol,
                    decision["price"],
                    decision["avg_score"],
                    order
                )

                await bot.send_message(

                    chat_id,

                    f"🟢 AUTO BUY\n\n"

                    f"Пара: {trade_symbol}\n"

                    f"Цена: "
                    f"{decision['price']:.4f}\n"

                    f"Сила: "
                    f"{decision['avg_score']}%\n"

                    f"TP: "
                    f"{open_position['take_profit_price']:.4f}\n"

                    f"SL: "
                    f"{open_position['stop_loss_price']:.4f}"
                )


            # --------------------------------
            # SELL без позиции не нужен
            # --------------------------------

        except Exception as error:

            await bot.send_message(
                chat_id,
                f"❌ Ошибка автоторговли\n\n{error}"
            )

        await asyncio.sleep(AUTO_INTERVAL)
# =========================
# КОМАНДЫ
# =========================

@dp.message(Command(commands=["start", "старт"]))
async def start(message: types.Message):

    await message.answer(
        "🤖 OKX Crypto Trading Bot PRO MAX запущен",
        reply_markup=keyboard
    )


@dp.message(Command(commands=["авто_вкл"]))
async def autotrade_on(message: types.Message):

    global autotrade_enabled

    if autotrade_enabled:

        await message.answer(
            "🤖 Автоторговля уже включена."
        )

        return

    autotrade_enabled = True

    save_state()

    await message.answer(
        "✅ Автоторговля включена."
    )

    asyncio.create_task(
        autotrade_loop(
            message.chat.id
        )
    )


@dp.message(Command(commands=["авто_выкл"]))
async def autotrade_off(message: types.Message):

    global autotrade_enabled

    autotrade_enabled = False

    save_state()

    await message.answer(
        "⛔ Автоторговля выключена."
    )


@dp.message(Command(commands=["позиция"]))
async def position_command(message: types.Message):

    await show_position(message)


@dp.message(Command(commands=["pnl"]))
async def pnl_command(message: types.Message):

    await show_pnl(message)


@dp.message(Command(commands=["топ3"]))
async def top3_command(message: types.Message):

    await show_top3(message)


@dp.message(Command(commands=["сброс"]))
async def reset_position(message: types.Message):

    global open_position

    open_position = None

    save_state()

    await message.answer(
        "♻️ Позиция сброшена."
    )


# =========================
# TEXT ROUTER
# =========================

@@dp.message()
async def text_router(message: types.Message):

    global auto_select_symbol
    global open_position

    if not message.text:
        return

    text = message.text.lower().strip()

    # Сначала специальные команды

    if "авто статус" in text:
        await message.answer(
            f"🤖 Автостатус\n\n"
            f"Автоторговля: {'включена ✅' if autotrade_enabled else 'выключена ❌'}\n"
            f"Автовыбор монеты: {'включён ✅' if auto_select_symbol else 'выключен ❌'}\n"
            f"Текущая монета: {current_trade_symbol}"
        )

    elif "авто монета" in text:
        

        auto_select_symbol = not auto_select_symbol
        save_state()

        await message.answer(
            f"🧠 Автовыбор монеты: {'включён ✅' if auto_select_symbol else 'выключен ❌'}"
        )

    elif "текущая монета" in text:
        await show_current_symbol(message)

    elif "авто вкл" in text:
        await autotrade_on(message)

    elif "авто выкл" in text:
        await autotrade_off(message)

    elif "топ-3" in text or "топ3" in text:
        await show_top3(message)

    elif "позиция" in text:
        await show_position(message)

    elif "pnl" in text:
        await show_pnl(message)

    elif "сброс" in text:
        

        open_position = None
        save_state()

        await message.answer("♻️ Позиция сброшена.")

    # Потом обычные команды

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

    elif "купить" in text:
        await do_demo_buy(message)

    elif "продать" in text:
        await do_demo_sell(message)

    elif "история" in text:
        await show_history(message)

    elif "статистика" in text:
        await show_statistics(message)

    elif "риск" in text:
        await show_risk(message)

    else:
        await message.answer("❓ Команда не распознана.\n\nНажми /старт"
        )


# =========================
# MAIN
# =========================

async def main():

    if not TELEGRAM_BOT_TOKEN:

        raise RuntimeError(
            "Нет TELEGRAM_BOT_TOKEN"
        )

    load_state()

    print(
        "OKX Crypto Trading Bot PRO MAX started"
    )

    await dp.start_polling(bot)


if __name__ == "__main__":

    asyncio.run(main())
