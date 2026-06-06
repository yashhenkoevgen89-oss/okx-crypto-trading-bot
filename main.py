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

from config import WATCHLIST, TIMEFRAMES, BUY_SCORE, SELL_SCORE
from database import init_db, add_trade, add_signal
from indicators import add_indicators
from scanner import calculate_score, sort_signals, get_best_coin
from trade_manager import place_demo_buy, place_demo_sell


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OKX_API_KEY = os.getenv("OKX_API_KEY")
OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE")
OKX_FLAG = os.getenv("OKX_FLAG", "1")

TRADE_SYMBOL = os.getenv("TRADE_SYMBOL", "BTC-USDT")
TRADE_AMOUNT_USDT = float(os.getenv("TRADE_AMOUNT_USDT", "10"))

STATE_FILE = "bot_state.json"

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

market_api = MarketData.MarketAPI(flag=OKX_FLAG)
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

monitor_enabled = False
autotrade_enabled = False
trade_history = []

risk_settings = {
    "amount_usdt": TRADE_AMOUNT_USDT,
    "max_amount_usdt": 50.0,
    "stop_loss_percent": 2.0,
    "take_profit_percent": 4.0,
    "buy_score": BUY_SCORE,
    "sell_score": SELL_SCORE,
}

keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Статус"), KeyboardButton(text="💰 Баланс")],
        [KeyboardButton(text="📡 Сигнал"), KeyboardButton(text="🌐 Рынок")],
        [KeyboardButton(text="🔎 Сканер"), KeyboardButton(text="🏆 Лучшая")],
        [KeyboardButton(text="🟢 Купить DEMO"), KeyboardButton(text="🔴 Продать DEMO")],
        [KeyboardButton(text="🤖 Авто статус"), KeyboardButton(text="🛡 Риск")],
        [KeyboardButton(text="📜 История"), KeyboardButton(text="📊 Статистика")]
    ],
    resize_keyboard=True
)


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_demo():
    return OKX_FLAG == "1"


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def save_state():
    data = {
        "trade_history": trade_history,
        "risk_settings": risk_settings,
        "trade_symbol": TRADE_SYMBOL
    }

    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def load_state():
    global trade_history, risk_settings, TRADE_SYMBOL

    if not os.path.exists(STATE_FILE):
        return False

    with open(STATE_FILE, "r", encoding="utf-8") as file:
        data = json.load(file)

    trade_history = data.get("trade_history", [])
    risk_settings.update(data.get("risk_settings", {}))
    TRADE_SYMBOL = data.get("trade_symbol", TRADE_SYMBOL)

    return True


def get_market_data(symbol=None, bar="15m", limit=120):
    symbol = symbol or TRADE_SYMBOL

    result = market_api.get_candlesticks(
        instId=symbol,
        bar=bar,
        limit=str(limit)
    )

    candles = result.get("data", [])
    if not candles:
        raise Exception(f"OKX не вернул свечи: {result}")

    df = pd.DataFrame(
        candles,
        columns=[
            "ts", "open", "high", "low", "close",
            "vol", "volCcy", "volCcyQuote", "confirm"
        ]
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
        "rsi": safe_float(last["rsi"]),
        "macd": safe_float(last["macd"]),
        "macd_signal": safe_float(last["macd_signal"]),
        "bb_upper": safe_float(last["bb_upper"]),
        "bb_lower": safe_float(last["bb_lower"]),
        "vol_avg": safe_float(last["vol_avg"]),
        "atr": safe_float(last["atr"]),
    }

    score, signal_value = calculate_score(data)

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
        "signal": signal_value,
        "trend": "восходящий" if data["ema9"] > data["ema21"] else "нисходящий",
    }


def format_signal(result):
    return (
        f"📡 {result['symbol']} | {result['bar']}\n\n"
        f"Цена: {result['price']:.2f}\n"
        f"Тренд: {result['trend']}\n"
        f"RSI: {result['rsi']:.2f}\n"
        f"EMA9: {result['ema9']:.2f}\n"
        f"EMA21: {result['ema21']:.2f}\n"
        f"MACD: {result['macd']:.2f}\n"
        f"MACD Signal: {result['macd_signal']:.2f}\n"
        f"ATR: {result['atr']:.2f}\n\n"
        f"Сила: {result['score']}%\n"
        f"Сигнал: {result['signal']}"
    )


def multi_timeframe_decision():
    results = [build_signal(TRADE_SYMBOL, bar) for bar in TIMEFRAMES]

    buy_count = sum(1 for r in results if r["signal"] == "BUY")
    sell_count = sum(1 for r in results if r["signal"] == "SELL")
    avg_score = int(sum(r["score"] for r in results) / len(results))

    if buy_count >= 2 and avg_score >= risk_settings["buy_score"]:
        final_signal = "BUY"
    elif sell_count >= 2 and avg_score <= risk_settings["sell_score"]:
        final_signal = "SELL"
    else:
        final_signal = "HOLD"

    return {
        "signal": final_signal,
        "avg_score": avg_score,
        "price": results[1]["price"],
        "results": results
    }


def get_okx_balance_text():
    result = account_api.get_account_balance()

    if result.get("code") != "0":
        return f"❌ Ошибка OKX:\n{result}"

    details = result.get("data", [{}])[0].get("details", [])
    lines = ["💰 Баланс OKX Demo:\n"]

    for item in details:
        ccy = item.get("ccy")
        bal = safe_float(item.get("cashBal") or item.get("eq"))
        avail = safe_float(item.get("availBal"))

        if bal > 0 or avail > 0:
            lines.append(f"{ccy}: баланс {bal:.8f}, доступно {avail:.8f}")

    return "\n".join(lines) if len(lines) > 1 else "Активов не найдено."


def add_history(action, signal_value, price, score, result=""):
    trade_history.append({
        "time": now(),
        "action": action,
        "signal": signal_value,
        "price": price,
        "score": score,
        "result": str(result)[:300]
    })

    if len(trade_history) > 100:
        trade_history.pop(0)

    save_state()


def parse_number(text):
    parts = text.split()
    if len(parts) < 2:
        return None

    return safe_float(parts[1].replace(",", "."), None)


async def show_status(message: types.Message):
    await message.answer(
        f"✅ Статус бота\n\n"
        f"Режим: {'DEMO' if is_demo() else 'LIVE'}\n"
        f"Пара: {TRADE_SYMBOL}\n"
        f"Сумма сделки: {risk_settings['amount_usdt']} USDT\n"
        f"BUY score: {risk_settings['buy_score']}%\n"
        f"SELL score: {risk_settings['sell_score']}%\n"
        f"Автоторговля: {'включена' if autotrade_enabled else 'выключена'}\n"
        f"LIVE: заблокирован"
    )


async def show_balance(message: types.Message):
    await message.answer(get_okx_balance_text())


async def show_signal(message: types.Message):
    try:
        result = build_signal()
        add_signal(now(), result["symbol"], result["signal"], result["price"], result["score"])
        await message.answer(format_signal(result))
    except Exception as e:
        await message.answer(f"❌ Ошибка сигнала:\n{e}")


async def show_market(message: types.Message):
    try:
        decision = multi_timeframe_decision()
        text = f"🌐 Мультианализ {TRADE_SYMBOL}\n\n"

        for r in decision["results"]:
            text += f"{r['bar']}: {r['signal']} | {r['score']}% | RSI {r['rsi']:.1f}\n"

        text += (
            f"\nИтог: {decision['signal']}\n"
            f"Средняя сила: {decision['avg_score']}%"
        )

        await message.answer(text)

    except Exception as e:
        await message.answer(f"❌ Ошибка рынка:\n{e}")


async def show_scan(message: types.Message):
    results = []

    for coin in WATCHLIST:
        try:
            result = build_signal(coin, "15m")
            results.append(result)
        except Exception:
            continue

    results = sort_signals(results)

    text = "🔎 Сканер OKX\n\n"
    for r in results:
        text += f"{r['symbol']}: {r['signal']} | {r['score']}% | RSI {r['rsi']:.1f}\n"

    await message.answer(text)


async def show_best(message: types.Message):
    results = []

    for coin in WATCHLIST:
        try:
            result = build_signal(coin, "15m")
            results.append(result)
        except Exception:
            continue

    best_coin = get_best_coin(results)

    if not best_coin:
        await message.answer("Не удалось выбрать лучшую монету.")
        return

    await message.answer("🏆 Лучшая монета сейчас:\n\n" + format_signal(best_coin))


async def show_risk(message: types.Message):
    await message.answer(
        f"🛡 Риск\n\n"
        f"Сумма сделки: {risk_settings['amount_usdt']} USDT\n"
        f"Максимум: {risk_settings['max_amount_usdt']} USDT\n"
        f"SL: {risk_settings['stop_loss_percent']}%\n"
        f"TP: {risk_settings['take_profit_percent']}%\n"
        f"BUY >= {risk_settings['buy_score']}%\n"
        f"SELL <= {risk_settings['sell_score']}%"
    )


async def do_demo_buy(message: types.Message):
    if not is_demo():
        await message.answer("⛔ Покупка разрешена только в DEMO.")
        return

    try:
        amount = min(risk_settings["amount_usdt"], risk_settings["max_amount_usdt"])

        result = place_demo_buy(
            trade_api=trade_api,
            symbol=TRADE_SYMBOL,
            amount_usdt=amount,
            okx_flag=OKX_FLAG
        )

        price = build_signal()["price"]

        add_history("REAL DEMO BUY", "BUY", price, 100, result)
        add_trade(now(), TRADE_SYMBOL, "BUY", price, 0, 0, 100, "manual real demo buy")

        await message.answer(
            f"🟢 DEMO покупка отправлена в OKX\n\n"
            f"Пара: {TRADE_SYMBOL}\n"
            f"Сумма: {amount} USDT\n\n"
            f"Ответ OKX:\n{result}"
        )

    except Exception as e:
        await message.answer(f"❌ Ошибка покупки:\n{e}")


async def do_demo_sell(message: types.Message):
    if not is_demo():
        await message.answer("⛔ Продажа разрешена только в DEMO.")
        return

    try:
        result = place_demo_sell(
            trade_api=trade_api,
            account_api=account_api,
            symbol=TRADE_SYMBOL,
            okx_flag=OKX_FLAG
        )

        price = build_signal()["price"]

        add_history("REAL DEMO SELL", "SELL", price, 100, result)
        add_trade(now(), TRADE_SYMBOL, "SELL", 0, price, 0, 100, "manual real demo sell")

        await message.answer(
            f"🔴 DEMO продажа отправлена в OKX\n\n"
            f"Пара: {TRADE_SYMBOL}\n\n"
            f"Ответ OKX:\n{result}"
        )

    except Exception as e:
        await message.answer(f"❌ Ошибка продажи:\n{e}")


async def show_history(message: types.Message):
    if not trade_history:
        await message.answer("История пустая.")
        return

    text = "📜 История:\n\n"
    for item in trade_history[-10:]:
        text += (
            f"{item['time']} | {item['action']} | "
            f"{item['price']:.2f} | {item['score']}%\n"
        )

    await message.answer(text)


async def show_pnl(message: types.Message):
    buys = len([x for x in trade_history if "BUY" in x["action"]])
    sells = len([x for x in trade_history if "SELL" in x["action"]])

    await message.answer(
        f"📊 Статистика\n\n"
        f"Всего действий: {len(trade_history)}\n"
        f"BUY: {buys}\n"
        f"SELL: {sells}\n\n"
        f"Точный PnL по OKX ордерам добавим следующим шагом."
    )


@dp.message(Command(commands=["start", "старт"]))
async def start(message: types.Message):
    await message.answer(
        "🤖 OKX Crypto Trading Bot\n\n"
        "Команды:\n"
        "/статус — статус\n"
        "/баланс — баланс\n"
        "/сигнал — сигнал\n"
        "/рынок — мультианализ\n"
        "/сканер — сканер монет\n"
        "/лучшая — лучшая монета\n"
        "/купить — DEMO покупка\n"
        "/продать — DEMO продажа\n"
        "/авто_вкл — включить автоторговлю\n"
        "/авто_выкл — выключить автоторговлю\n"
        "/авто_статус — статус автоторговли\n"
        "/риск — риск\n"
        "/история — история\n"
        "/статистика — статистика",
        reply_markup=keyboard
    )


@dp.message(Command(commands=["status", "статус"]))
async def status(message: types.Message):
    await show_status(message)


@dp.message(Command(commands=["balance", "баланс"]))
async def balance(message: types.Message):
    await show_balance(message)


@dp.message(Command(commands=["signal", "сигнал"]))
async def signal(message: types.Message):
    await show_signal(message)


@dp.message(Command(commands=["market", "рынок"]))
async def market(message: types.Message):
    await show_market(message)


@dp.message(Command(commands=["scan", "scanner", "сканер"]))
async def scan(message: types.Message):
    await show_scan(message)


@dp.message(Command(commands=["best", "лучшая"]))
async def best(message: types.Message):
    await show_best(message)


@dp.message(Command(commands=["risk", "риск"]))
async def risk(message: types.Message):
    await show_risk(message)


@dp.message(Command(commands=["real_demo_buy", "buy", "купить"]))
async def real_demo_buy(message: types.Message):
    await do_demo_buy(message)


@dp.message(Command(commands=["real_demo_sell", "sell", "продать"]))
async def real_demo_sell(message: types.Message):
    await do_demo_sell(message)


@dp.message(Command(commands=["history", "история"]))
async def history(message: types.Message):
    await show_history(message)


@dp.message(Command(commands=["pnl", "stat", "статистика"]))
async def pnl(message: types.Message):
    await show_pnl(message)


@dp.message(Command(commands=["save", "сохранить"]))
async def save_cmd(message: types.Message):
    save_state()
    await message.answer("✅ Состояние сохранено.")


@dp.message(Command(commands=["load", "загрузить"]))
async def load_cmd(message: types.Message):
    ok = load_state()
    await message.answer("✅ Состояние загружено." if ok else "Файл состояния не найден.")


async def autotrade_loop(chat_id):
    global autotrade_enabled

    while autotrade_enabled:
        try:
            if not is_demo():
                autotrade_enabled = False
                await bot.send_message(chat_id, "⛔ LIVE заблокирован.")
                return

            decision = multi_timeframe_decision()

            if decision["signal"] == "BUY":
                amount = min(risk_settings["amount_usdt"], risk_settings["max_amount_usdt"])

                order = place_demo_buy(
                    trade_api=trade_api,
                    symbol=TRADE_SYMBOL,
                    amount_usdt=amount,
                    okx_flag=OKX_FLAG
                )

                add_history("AUTO REAL DEMO BUY", "BUY", decision["price"], decision["avg_score"], order)
                add_trade(now(), TRADE_SYMBOL, "BUY", decision["price"], 0, 0, decision["avg_score"], "auto demo buy")

                await bot.send_message(
                    chat_id,
                    f"🟢 AUTO DEMO BUY отправлен в OKX\n\n"
                    f"Пара: {TRADE_SYMBOL}\n"
                    f"Цена: {decision['price']:.2f}\n"
                    f"Сила: {decision['avg_score']}%\n\n"
                    f"Ответ OKX:\n{order}"
                )

            elif decision["signal"] == "SELL":
                order = place_demo_sell(
                    trade_api=trade_api,
                    account_api=account_api,
                    symbol=TRADE_SYMBOL,
                    okx_flag=OKX_FLAG
                )

                add_history("AUTO REAL DEMO SELL", "SELL", decision["price"], decision["avg_score"], order)
                add_trade(now(), TRADE_SYMBOL, "SELL", 0, decision["price"], 0, decision["avg_score"], "auto demo sell")

                await bot.send_message(
                    chat_id,
                    f"🔴 AUTO DEMO SELL отправлен в OKX\n\n"
                    f"Пара: {TRADE_SYMBOL}\n"
                    f"Цена: {decision['price']:.2f}\n"
                    f"Сила: {decision['avg_score']}%\n\n"
                    f"Ответ OKX:\n{order}"
                )

        except Exception as e:
            await bot.send_message(chat_id, f"❌ Ошибка автоторговли:\n{e}")

        await asyncio.sleep(300)


@dp.message(Command(commands=["autotrade_on", "авто_вкл"]))
async def autotrade_on(message: types.Message):
    global autotrade_enabled

    if not is_demo():
        await message.answer("⛔ Только DEMO.")
        return

    if autotrade_enabled:
        await message.answer("Автоторговля уже включена.")
        return

    autotrade_enabled = True
    await message.answer("✅ Реальная DEMO автоторговля включена.")
    asyncio.create_task(autotrade_loop(message.chat.id))


@dp.message(Command(commands=["autotrade_off", "авто_выкл"]))
async def autotrade_off(message: types.Message):
    global autotrade_enabled
    autotrade_enabled = False
    await message.answer("⛔ DEMO автоторговля выключена.")


@dp.message(Command(commands=["autotrade_status", "авто_статус"]))
async def autotrade_status(message: types.Message):
    await message.answer(
        f"🤖 Автоторговля\n\n"
        f"Статус: {'включена' if autotrade_enabled else 'выключена'}\n"
        f"Режим: {'DEMO' if is_demo() else 'LIVE'}\n"
        f"Пара: {TRADE_SYMBOL}\n"
        f"Сумма: {risk_settings['amount_usdt']} USDT"
    )


@dp.message()
async def text_router(message: types.Message):
    if not message.text:
        return

    text = message.text.lower().strip()

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
    elif "купить" in text:
        await do_demo_buy(message)
    elif "продать" in text:
        await do_demo_sell(message)
    elif "риск" in text:
        await show_risk(message)
    elif "история" in text:
        await show_history(message)
    elif "статистика" in text:
        await show_pnl(message)
    else:
        await message.answer("Я не понял команду. Нажми /старт или выбери кнопку в меню.")


async def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Нет TELEGRAM_BOT_TOKEN")

    init_db()
    load_state()

    print("OKX Crypto Trading Bot Russian version started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
