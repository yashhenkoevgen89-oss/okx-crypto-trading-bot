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
AUTO_INTERVAL = 300

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

autotrade_enabled = False
trade_history = []

auto_select_symbol = True
current_trade_symbol = TRADE_SYMBOL

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
        [KeyboardButton(text="🟢 Авто ВКЛ"), KeyboardButton(text="🔴 Авто ВЫКЛ")],
        [KeyboardButton(text="🧠 Авто монета"), KeyboardButton(text="💱 Текущая монета")],
        [KeyboardButton(text="🤖 Авто статус"), KeyboardButton(text="🛡 Риск")],
        [KeyboardButton(text="📜 История"), KeyboardButton(text="📈 Статистика")]
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
        "trade_symbol": TRADE_SYMBOL,
        "autotrade_enabled": autotrade_enabled
    }

    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def load_state():
    global trade_history, risk_settings, TRADE_SYMBOL, autotrade_enabled

    if not os.path.exists(STATE_FILE):
        return False

    with open(STATE_FILE, "r", encoding="utf-8") as file:
        data = json.load(file)

    trade_history = data.get("trade_history", [])
    risk_settings.update(data.get("risk_settings", {}))
    TRADE_SYMBOL = data.get("trade_symbol", TRADE_SYMBOL)
    autotrade_enabled = False

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
            "ts",
            "open",
            "high",
            "low",
            "close",
            "vol",
            "volCcy",
            "volCcyQuote",
            "confirm"
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
    
def multi_timeframe_decision_for_symbol(symbol):
    results = [build_signal(symbol, bar) for bar in TIMEFRAMES]

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
        "symbol": symbol,
        "signal": final_signal,
        "avg_score": avg_score,
        "price": results[1]["price"],
        "results": results
    }


def choose_best_symbol():
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
            lines.append(
                f"{ccy}: баланс {bal:.8f}, доступно {avail:.8f}"
            )

    return "\n".join(lines)


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


async def show_status(message: types.Message):
    await message.answer(
        f"🤖 Статус бота\n\n"
        f"Режим: {'DEMO' if is_demo() else 'LIVE'}\n"
        f"Пара: {TRADE_SYMBOL}\n"
        f"Сумма сделки: {risk_settings['amount_usdt']} USDT\n"
        f"BUY score: {risk_settings['buy_score']}%\n"
        f"SELL score: {risk_settings['sell_score']}%\n"
        f"Автоторговля: {'включена ✅' if autotrade_enabled else 'выключена ❌'}"
    )


async def show_balance(message: types.Message):
    await message.answer(get_okx_balance_text())


async def show_signal(message: types.Message):
    try:
        result = build_signal()

        add_signal(
            now(),
            result["symbol"],
            result["signal"],
            result["price"],
            result["score"]
        )

        await message.answer(format_signal(result))

    except Exception as e:
        await message.answer(f"❌ Ошибка сигнала:\n{e}")


async def show_market(message: types.Message):
    try:
        decision = multi_timeframe_decision_for_symbol(TRADE_SYMBOL)

        text = f"🌐 Мультианализ {TRADE_SYMBOL}\n\n"

        for r in decision["results"]:
            text += (
                f"{r['bar']} | "
                f"{r['signal']} | "
                f"{r['score']}%\n"
            )

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
        except:
            pass

    results = sort_signals(results)

    text = "🔎 Сканер рынка\n\n"

    for r in results:
        text += (
            f"{r['symbol']}\n"
            f"{r['signal']} | {r['score']}%\n\n"
        )

    await message.answer(text)


async def show_best(message: types.Message):
    results = []

    for coin in WATCHLIST:
        try:
            result = build_signal(coin, "15m")
            results.append(result)
        except:
            pass

    best_coin = get_best_coin(results)

    if not best_coin:
        await message.answer("Не удалось выбрать монету.")
        return

    await message.answer(
        "🏆 Лучшая монета:\n\n" +
        format_signal(best_coin)
    )


async def show_current_symbol(message: types.Message):
    await message.answer(
        f"💱 Текущая монета\n\n"
        f"Основная пара: {TRADE_SYMBOL}\n"
        f"Торговая пара сейчас: {current_trade_symbol}\n"
        f"Автовыбор монеты: {'включён ✅' if auto_select_symbol else 'выключен ❌'}"
    )
async def show_risk(message: types.Message):
    await message.answer(
        f"🛡 Риск\n\n"
        f"Сделка: {risk_settings['amount_usdt']} USDT\n"
        f"Максимум: {risk_settings['max_amount_usdt']} USDT\n"
        f"SL: {risk_settings['stop_loss_percent']}%\n"
        f"TP: {risk_settings['take_profit_percent']}%"
    )


async def show_history(message: types.Message):
    if not trade_history:
        await message.answer("История пустая.")
        return

    text = "📜 Последние операции\n\n"

    for item in trade_history[-10:]:
        text += (
            f"{item['time']}\n"
            f"{item['action']}\n"
            f"{item['price']:.2f}\n\n"
        )

    await message.answer(text)
async def show_statistics(message: types.Message):
    buys = len([x for x in trade_history if "BUY" in x["action"]])
    sells = len([x for x in trade_history if "SELL" in x["action"]])

    await message.answer(
        f"📈 Статистика\n\n"
        f"Всего операций: {len(trade_history)}\n"
        f"BUY: {buys}\n"
        f"SELL: {sells}"
    )


async def do_demo_buy(message: types.Message):


    if not is_demo():
        await message.answer("⛔ Покупка разрешена только в DEMO.")
        return

    try:
        amount = min(
            risk_settings["amount_usdt"],
            risk_settings["max_amount_usdt"]
        )

        order = place_demo_buy(
            trade_api=trade_api,
            symbol=TRADE_SYMBOL,
            amount_usdt=amount,
            okx_flag=OKX_FLAG
        )

        signal_data = build_signal()
        price = signal_data["price"]

        add_history(
            "MANUAL DEMO BUY",
            "BUY",
            price,
            signal_data["score"],
            order
        )

        add_trade(
            now(),
            TRADE_SYMBOL,
            "BUY",
            price,
            0,
            0,
            signal_data["score"],
            "manual demo buy"
        )

        await message.answer(
            f"🟢 DEMO покупка отправлена в OKX\n\n"
            f"Пара: {TRADE_SYMBOL}\n"
            f"Сумма: {amount} USDT\n"
            f"Цена: {price:.2f}\n\n"
            f"Ответ OKX:\n{order}"
        )

    except Exception as e:
        await message.answer(f"❌ Ошибка покупки:\n{e}")


async def do_demo_sell(message: types.Message):
    if not is_demo():
        await message.answer("⛔ Продажа разрешена только в DEMO.")
        return

    try:
        order = place_demo_sell(
            trade_api=trade_api,
            account_api=account_api,
            symbol=TRADE_SYMBOL,
            okx_flag=OKX_FLAG
        )

        signal_data = build_signal()
        price = signal_data["price"]

        add_history(
            "MANUAL DEMO SELL",
            "SELL",
            price,
            signal_data["score"],
            order
        )

        add_trade(
            now(),
            TRADE_SYMBOL,
            "SELL",
            0,
            price,
            0,
            signal_data["score"],
            "manual demo sell"
        )

        await message.answer(
            f"🔴 DEMO продажа отправлена в OKX\n\n"
            f"Пара: {TRADE_SYMBOL}\n"
            f"Цена: {price:.2f}\n\n"
            f"Ответ OKX:\n{order}"
        )

    except Exception as e:
        await message.answer(f"❌ Ошибка продажи:\n{e}")


async def autotrade_loop(chat_id):
    global autotrade_enabled

while autotrade_enabled:
    try:
        if not is_demo():
            autotrade_enabled = False
            await bot.send_message(
                chat_id,
                "⛔ LIVE режим заблокирован."
            )
            return

        trade_symbol = TRADE_SYMBOL

        if auto_select_symbol:
            trade_symbol, best_data = choose_best_symbol()

            await bot.send_message(
                chat_id,
                f"🧠 Автовыбор монеты\n\n"
                f"Выбрана: {trade_symbol}\n"
                f"Сигнал: {best_data['signal']}\n"
                f"Сила: {best_data['score']}%"
            )

        decision = multi_timeframe_decision_for_symbol(trade_symbol)

        if decision["signal"] == "BUY":
            ...
                amount = min(
                    risk_settings["amount_usdt"],
                    risk_settings["max_amount_usdt"]
                )

                order = place_demo_buy(
                    trade_api=trade_api,
                    symbol=trade_symbol,
                    amount_usdt=amount,
                    okx_flag=OKX_FLAG
                )

                add_history(
                    "AUTO DEMO BUY",
                    "BUY",
                    decision["price"],
                    decision["avg_score"],
                    order
                )

                add_trade(
                    now(),
                    trade_symbol,
                    "BUY",
                    decision["price"],
                    0,
                    0,
                    decision["avg_score"],
                    "auto demo buy"
                )

                await bot.send_message(
                    chat_id,
                    f"🟢 AUTO DEMO BUY\n\n"
                    f"Пара: {trade_symbol}\n"
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

                add_history(
                    "AUTO DEMO SELL",
                    "SELL",
                    decision["price"],
                    decision["avg_score"],
                    order
                )

                add_trade(
                    now(),trade_symbol,
                    "SELL",
                    0,
                    decision["price"],
                    0,
                    decision["avg_score"],
                    "auto demo sell"
                )

                await bot.send_message(
                    chat_id,
                    f"🔴 AUTO DEMO SELL\n\n"
                    f"Пара: {TRADE_SYMBOL}\n"
                    f"Цена: {decision['price']:.2f}\n"
                    f"Сила: {decision['avg_score']}%\n\n"
                    f"Ответ OKX:\n{order}"
                )

            else:
                await bot.send_message(
                    chat_id,
                    f"⏳ Автоторговля проверила рынок\n\n"
                    f"Пара: {TRADE_SYMBOL}\n"
                    f"Сигнал: HOLD\n"
                    f"Сила: {decision['avg_score']}%"
                )

        except Exception as e:
            await bot.send_message(
                chat_id,
                f"❌ Ошибка автоторговли:\n{e}"
            )

        await asyncio.sleep(AUTO_INTERVAL)


@dp.message(Command(commands=["start", "старт"]))
async def start(message: types.Message):
    await message.answer(
        "🤖 OKX Crypto Trading Bot PRO MAX v5\n\n"
        "Кнопки доступны ниже.\n\n"
        "Команды:\n"
        "/статус\n"
        "/баланс\n"
        "/сигнал\n"
        "/рынок\n"
        "/сканер\n"
        "/лучшая\n"
        "/купить\n"
        "/продать\n"
        "/авто_вкл\n"
        "/авто_выкл\n"
        "/авто_статус\n"
        "/риск\n"
        "/история\n"
        "/статистика",
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


@dp.message(Command(commands=["history", "история"]))
async def history(message: types.Message):
    await show_history(message)


@dp.message(Command(commands=["pnl", "stat", "stats", "статистика"]))
async def statistics(message: types.Message):
    await show_statistics(message)


@dp.message(Command(commands=["real_demo_buy", "buy", "купить"]))
async def buy_command(message: types.Message):
    await do_demo_buy(message)


@dp.message(Command(commands=["real_demo_sell", "sell", "продать"]))
async def sell_command(message: types.Message):
    await do_demo_sell(message)


@dp.message(Command(commands=["autotrade_on", "авто_вкл"]))
async def autotrade_on(message: types.Message):
    global autotrade_enabled

    if not is_demo():
        await message.answer("⛔ Автоторговля разрешена только в DEMO.")
        return

    if autotrade_enabled:
        await message.answer("Автоторговля уже включена.")
        return

    autotrade_enabled = True
    save_state()

    await message.answer(
        "✅ Автоторговля включена.\n\n"
        "Бот будет проверять рынок каждые 5 минут."
    )

    asyncio.create_task(autotrade_loop(message.chat.id))


@dp.message(Command(commands=["autotrade_off", "авто_выкл"]))
async def autotrade_off(message: types.Message):
    global autotrade_enabled

    autotrade_enabled = False
    save_state()

    await message.answer("⛔ Автоторговля выключена.")


@dp.message(Command(commands=["autotrade_status", "авто_статус"]))
async def autotrade_status(message: types.Message):
    await message.answer(
        f"🤖 Авто статус\n\n"
        f"Автоторговля: {'включена ✅' if autotrade_enabled else 'выключена ❌'}\n"
        f"Режим: {'DEMO' if is_demo() else 'LIVE'}\n"
        f"Пара: {TRADE_SYMBOL}\n"
        f"Интервал: {AUTO_INTERVAL} секунд\n"
        f"Сумма сделки: {risk_settings['amount_usdt']} USDT"
    )
@dp.message(Command(commands=["auto_coin", "авто_монета"]))
async def auto_coin_toggle(message: types.Message):
    global auto_select_symbol

    auto_select_symbol = not auto_select_symbol
    save_state()

    await message.answer(
        f"🧠 Автовыбор монеты: {'включён ✅' if auto_select_symbol else 'выключен ❌'}"
    )


@dp.message(Command(commands=["current_coin", "текущая_монета"]))
async def current_coin(message: types.Message):
    await show_current_symbol(message)

@dp.message(Command(commands=["save", "сохранить"]))
async def save_cmd(message: types.Message):
    save_state()
    await message.answer("✅ Состояние сохранено.")


@dp.message(Command(commands=["load", "загрузить"]))
async def load_cmd(message: types.Message):
    ok = load_state()
    await message.answer(
        "✅ Состояние загружено." if ok else "Файл состояния не найден."
    )


@dp.message()
async def text_router(message: types.Message):
    if not message.text:
        return

    text = message.text.lower().strip()

    if "авто монета" in text or "авто_монета" in text:
        await auto_coin_toggle(message)
    elif "текущая монета" in text or "текущая_монета" in text:
        await show_current_symbol(message)
    elif "авто вкл" in text or "авто_вкл" in text:
        await autotrade_on(message)
    elif "авто выкл" in text or "авто_выкл" in text:
        await autotrade_off(message)
    elif "авто статус" in text or "авто_статус" in text:
        await autotrade_status(message)
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
    elif "риск" in text:
        await show_risk(message)
    elif "история" in text:
        await show_history(message)
    elif "статистика" in text:
        await show_statistics(message)
    else:
        await message.answer("Я не понял команду. Нажми /старт")
        


async def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Нет TELEGRAM_BOT_TOKEN")

    init_db()
    load_state()

    print("OKX Crypto Trading Bot PRO MAX v5 started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
