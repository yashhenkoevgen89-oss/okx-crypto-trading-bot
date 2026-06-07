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
    return len(
        [
            item for item in trade_history
            if item.get("date") == today_str()
            and "AUTO" in item.get("action", "")
        ]
    )


def pnl_today_percent() -> float:
    trades = [
        item for item in closed_trades
        if item.get("date") == today_str()
    ]

    return sum(
        safe_float(item.get("pnl_percent"))
        for item in trades
    )


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
# СИГНАЛЫ И РЫНОК
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
# SHOW ФУНКЦИИ
# =========================

async def show_status(message: types.Message):
    mode = "DEMO 🧪" if is_demo() else "LIVE 🔥"

    await message.answer(
        f"📊 Статус\n\n"
        f"Режим: {mode}\n"
        f"Монета: {current_trade_symbol}\n"
        f"Автоторговля: {'✅ ВКЛ' if autotrade_enabled else '❌ ВЫКЛ'}\n"
        f"Автовыбор монеты: {'✅ ВКЛ' if auto_select_symbol else '❌ ВЫКЛ'}\n"
        f"Интервал: {AUTO_INTERVAL} сек"
    )


async def show_balance(message: types.Message):
    try:
        balance_data = account_api.get_account_balance()

        usdt_balance = 0.0

        for item in balance_data["data"]:
            for detail in item["details"]:
                if detail["ccy"] == "USDT":
                    usdt_balance = safe_float(detail["cashBal"])

        await message.answer(
            f"💰 Баланс\n\n"
            f"USDT: {usdt_balance:.2f}"
        )

    except Exception as e:
        await message.answer(f"❌ Ошибка баланса:\n{e}")


async def show_signal(message: types.Message):
    try:
        result = build_signal(current_trade_symbol)

        await message.answer(format_signal(result))

    except Exception as e:
        await message.answer(f"❌ Ошибка сигнала:\n{e}")


async def show_market(message: types.Message):
    try:
        decision = multi_timeframe_decision_for_symbol(current_trade_symbol)

        text = (
            f"🌐 Мультианализ {current_trade_symbol}\n\n"
        )

        for item in decision["results"]:
            text += (
                f"{item['bar']} | "
                f"{item['signal']} | "
                f"{item['score']}%\n"
            )

        text += (
            f"\nИтог: {decision['signal']}\n"
            f"Сила: {decision['avg_score']}%"
        )

        await message.answer(text)

    except Exception as e:
        await message.answer(f"❌ Ошибка рынка:\n{e}")


async def show_scan(message: types.Message):
    try:
        results = scan_market()

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


async def show_best(message: types.Message):
    try:
        symbol, data = choose_best_symbol()

        if data is None:
            await message.answer("❌ Нет данных")
            return

        await message.answer(
            f"🏆 Лучшая монета\n\n"
            f"{symbol}\n"
            f"Сигнал: {data['signal']}\n"
            f"Сила: {data['score']}%"
        )

    except Exception as e:
        await message.answer(f"❌ Ошибка:\n{e}")


async def show_top3(message: types.Message):
    try:
        results = scan_market()

        text = "🥇 ТОП-3 монеты\n\n"

        for i, item in enumerate(results[:3], start=1):
            text += (
                f"{i}. "
                f"{item['symbol']} | "
                f"{item['signal']} | "
                f"{item['score']}%\n"
            )

        await message.answer(text)

    except Exception as e:
        await message.answer(f"❌ Ошибка:\n{e}")


async def show_position(message: types.Message):
    if open_position is None:
        await message.answer("📋 Открытых позиций нет.")
        return

    await message.answer(
        f"📋 Позиция\n\n"
        f"Монета: {open_position['symbol']}\n"
        f"Вход: {open_position['entry_price']:.4f}\n"
        f"Объём: {open_position['amount_usdt']:.2f} USDT\n"
        f"SL: {open_position['stop_loss']:.4f}\n"
        f"TP: {open_position['take_profit']:.4f}"
    )


async def show_current_symbol(message: types.Message):
    await message.answer(
        f"💱 Текущая монета\n\n"
        f"{current_trade_symbol}"
    )


async def show_risk(message: types.Message):
    await message.answer(
        f"🛡 Риск\n\n"
        f"Сумма сделки: {risk_settings['amount_usdt']} USDT\n"
        f"Макс. сумма: {risk_settings['max_amount_usdt']} USDT\n"
        f"Stop Loss: {risk_settings['stop_loss_percent']}%\n"
        f"Take Profit: {risk_settings['take_profit_percent']}%\n"
        f"Trailing Stop: {risk_settings['trailing_stop_percent']}%\n"
        f"BUY score: {risk_settings['buy_score']}\n"
        f"SELL score: {risk_settings['sell_score']}"
    )


async def show_history(message: types.Message):
    if not trade_history:
        await message.answer("📜 История пуста.")
        return

    text = "📜 Последние сделки\n\n"

    for item in trade_history[-10:]:
        text += (
            f"{item['time']}\n"
            f"{item['action']}\n"
            f"{item['symbol']}\n\n"
        )

    await message.answer(text)


async def show_statistics(message: types.Message):
    if not closed_trades:
        await message.answer("📈 Пока статистики нет.")
        return

    total = len(closed_trades)

    profit = sum(
        item["pnl_usdt"]
        for item in closed_trades
    )

    await message.answer(
        f"📈 Статистика\n\n"
        f"Закрытых сделок: {total}\n"
        f"Итоговый PnL: {profit:.2f} USDT"
    )
    # =========================
# DEMO ПОКУПКА / ПРОДАЖА
# =========================

async def do_demo_buy(message: types.Message):
    global open_position

    try:
        signal_data = build_signal(current_trade_symbol)
        price = signal_data["price"]

        open_position = {
            "symbol": current_trade_symbol,
            "entry_price": price,
            "amount_usdt": risk_settings["amount_usdt"],
            "stop_loss": price * (1 - risk_settings["stop_loss_percent"] / 100),
            "take_profit": price * (1 + risk_settings["take_profit_percent"] / 100),
            "time": now()
        }

        add_history(
            "MANUAL DEMO BUY",
            current_trade_symbol,
            price,
            signal_data["score"]
        )

        save_state()

        await message.answer(
            f"🟢 DEMO покупка\n\n"
            f"Монета: {current_trade_symbol}\n"
            f"Цена: {price:.4f}\n"
            f"Сила сигнала: {signal_data['score']}%"
        )

    except Exception as e:
        await message.answer(f"❌ Ошибка покупки:\n{e}")


async def do_demo_sell(message: types.Message):
    global open_position

    try:
        if open_position is None:
            await message.answer("📋 Открытых позиций нет.")
            return

        signal_data = build_signal(open_position["symbol"])
        exit_price = signal_data["price"]

        add_closed_trade(
            open_position["symbol"],
            open_position["entry_price"],
            exit_price,
            open_position["amount_usdt"],
            "manual sell"
        )

        add_history(
            "MANUAL DEMO SELL",
            open_position["symbol"],
            exit_price,
            signal_data["score"]
        )

        pnl = (
            (exit_price - open_position["entry_price"])
            / open_position["entry_price"]
        ) * 100

        open_position = None

        save_state()

        await message.answer(
            f"🔴 DEMO продажа\n\n"
            f"PnL: {pnl:.2f}%"
        )

    except Exception as e:
        await message.answer(f"❌ Ошибка продажи:\n{e}")


# =========================
# АВТОТОРГОВЛЯ
# =========================

async def autotrade_loop(chat_id):
    global autotrade_enabled
    global current_trade_symbol
    global open_position

    while autotrade_enabled:

        try:

            allowed, reason = can_trade_today()

            if not allowed:
                await bot.send_message(
                    chat_id,
                    f"⛔ {reason}"
                )
                autotrade_enabled = False
                save_state()
                break

            if auto_select_symbol:

                symbol, best_data = choose_best_symbol()

                current_trade_symbol = symbol

                if best_data is not None:

                    await bot.send_message(
                        chat_id,
                        f"🧠 Автовыбор монеты\n\n"
                        f"{symbol}\n"
                        f"Сигнал: {best_data['signal']}\n"
                        f"Сила: {best_data['score']}%"
                    )

            decision = multi_timeframe_decision_for_symbol(
                current_trade_symbol
            )

            if open_position is None:

                if decision["signal"] == "BUY":

                    price = decision["price"]

                    open_position = {
                        "symbol": current_trade_symbol,
                        "entry_price": price,
                        "amount_usdt": risk_settings["amount_usdt"],
                        "stop_loss": price * (
                            1 - risk_settings["stop_loss_percent"] / 100
                        ),
                        "take_profit": price * (
                            1 + risk_settings["take_profit_percent"] / 100
                        ),
                        "time": now()
                    }

                    add_history(
                        "AUTO BUY",
                        current_trade_symbol,
                        price,
                        decision["avg_score"]
                    )

                    save_state()

                    await bot.send_message(
                        chat_id,
                        f"🟢 AUTO BUY\n\n"
                        f"{current_trade_symbol}\n"
                        f"Цена: {price:.4f}\n"
                        f"Сила: {decision['avg_score']}%"
                    )

            else:

                signal_data = build_signal(
                    open_position["symbol"]
                )

                current_price = signal_data["price"]

                if (
                    current_price <= open_position["stop_loss"]
                    or current_price >= open_position["take_profit"]
                    or decision["signal"] == "SELL"
                ):

                    add_closed_trade(
                        open_position["symbol"],
                        open_position["entry_price"],
                        current_price,
                        open_position["amount_usdt"],
                        "auto close"
                    )

                    add_history(
                        "AUTO SELL",
                        open_position["symbol"],
                        current_price,
                        decision["avg_score"]
                    )

                    pnl = (
                        (current_price - open_position["entry_price"])
                        / open_position["entry_price"]
                    ) * 100

                    await bot.send_message(
                        chat_id,
                        f"🔴 AUTO SELL\n\n"
                        f"PnL: {pnl:.2f}%"
                    )

                    open_position = None
                    save_state()

        except Exception as e:

            await bot.send_message(
                chat_id,
                f"❌ Ошибка автоторговли\n{e}"
            )

        await asyncio.sleep(AUTO_INTERVAL)


# =========================
# АВТО ВКЛ / ВЫКЛ
# =========================

async def enable_autotrade(message: types.Message):
    global autotrade_enabled

    if autotrade_enabled:

        await message.answer(
            "✅ Автоторговля уже включена."
        )
        return

    autotrade_enabled = True
    save_state()

    asyncio.create_task(
        autotrade_loop(message.chat.id)
    )

    await message.answer(
        "🟢 Автоторговля включена."
    )


async def disable_autotrade(message: types.Message):
    global autotrade_enabled

    autotrade_enabled = False

    save_state()

    await message.answer(
        "🔴 Автоторговля выключена."
    )
    # =========================
# TELEGRAM КОМАНДЫ
# =========================

@dp.message(Command("start"))
async def start_cmd(message: types.Message):

    await message.answer(
        "🤖 OKX Crypto Trading Bot PRO MAX запущен",
        reply_markup=keyboard
    )


@dp.message(Command("status"))
async def status_cmd(message: types.Message):
    await show_status(message)


@dp.message(Command("balance"))
async def balance_cmd(message: types.Message):
    await show_balance(message)


@dp.message(Command("signal"))
async def signal_cmd(message: types.Message):
    await show_signal(message)


@dp.message(Command("market"))
async def market_cmd(message: types.Message):
    await show_market(message)


@dp.message(Command("scan"))
async def scan_cmd(message: types.Message):
    await show_scan(message)


@dp.message(Command("best"))
async def best_cmd(message: types.Message):
    await show_best(message)


@dp.message(Command("top3"))
async def top3_cmd(message: types.Message):
    await show_top3(message)


@dp.message(Command("position"))
async def position_cmd(message: types.Message):
    await show_position(message)


@dp.message(Command("history"))
async def history_cmd(message: types.Message):
    await show_history(message)


@dp.message(Command("statistics"))
async def statistics_cmd(message: types.Message):
    await show_statistics(message)


# =========================
# TEXT ROUTER
# =========================

@dp.message()
async def text_router(message: types.Message):

    global auto_select_symbol
    global current_trade_symbol
    global open_position

    if not message.text:
        return

    text = message.text.lower().strip()

    try:

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

        elif "топ-3" in text:
            await show_top3(message)

        elif "позиция" in text:
            await show_position(message)

        elif "купить" in text:
            await do_demo_buy(message)

        elif "продать" in text:
            await do_demo_sell(message)

        elif "авто вкл" in text:
            await enable_autotrade(message)

        elif "авто выкл" in text:
            await disable_autotrade(message)

        elif "авто монета" in text:

            auto_select_symbol = not auto_select_symbol

            save_state()

            await message.answer(
                f"🧠 Автовыбор монеты: {'✅ включён' if auto_select_symbol else '❌ выключен'}"
            )

        elif "текущая монета" in text:
            await show_current_symbol(message)

        elif "авто статус" in text:

            await message.answer(
                f"🤖 Авто статус\n\n"
                f"Автоторговля: {'✅' if autotrade_enabled else '❌'}\n"
                f"Автовыбор монеты: {'✅' if auto_select_symbol else '❌'}"
            )

        elif "риск" in text:
            await show_risk(message)

        elif "история" in text:
            await show_history(message)

        elif "статистика" in text or "pnl" in text:
            await show_statistics(message)

        elif "сброс позиции" in text:

            open_position = None

            save_state()

            await message.answer(
                "♻️ Позиция сброшена"
            )

        else:

            await message.answer(
                "❓ Команда не распознана"
            )

    except Exception as e:

        await message.answer(
            f"❌ Ошибка:\n{e}"
        )


# =========================
# MAIN
# =========================

async def main():

    load_state()

    print("OKX Crypto Trading Bot PRO MAX запущен")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
