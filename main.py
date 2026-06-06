import os
import asyncio
from datetime import datetime

import pandas as pd
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

import okx.MarketData as MarketData
import okx.Account as Account
import okx.Trade as Trade


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

OKX_API_KEY = os.getenv("OKX_API_KEY")
OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE")

OKX_FLAG = os.getenv("OKX_FLAG", "1")
TRADE_SYMBOL = os.getenv("TRADE_SYMBOL", "BTC-USDT")
TRADE_AMOUNT_USDT = float(os.getenv("TRADE_AMOUNT_USDT", "10"))

MAX_SIGNAL_BUY = 70
MAX_SIGNAL_SELL = 30
MONITOR_INTERVAL = 300
AUTOTRADE_INTERVAL = 300

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


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_demo():
    return OKX_FLAG == "1"


def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def get_market_data(bar="15m", limit=120):
    result = market_api.get_candlesticks(
        instId=TRADE_SYMBOL,
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

    df["ema9"] = df["close"].ewm(span=9).mean()
    df["ema21"] = df["close"].ewm(span=21).mean()
    df["rsi"] = calculate_rsi(df["close"])

    df["macd"] = df["close"].ewm(span=12).mean() - df["close"].ewm(span=26).mean()
    df["macd_signal"] = df["macd"].ewm(span=9).mean()

    df["bb_mid"] = df["close"].rolling(20).mean()
    df["bb_std"] = df["close"].rolling(20).std()
    df["bb_upper"] = df["bb_mid"] + 2 * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - 2 * df["bb_std"]

    df["tr1"] = df["high"] - df["low"]
    df["tr2"] = abs(df["high"] - df["close"].shift())
    df["tr3"] = abs(df["low"] - df["close"].shift())
    df["tr"] = df[["tr1", "tr2", "tr3"]].max(axis=1)
    df["atr"] = df["tr"].rolling(14).mean()

    df["vol_avg"] = df["vol"].rolling(20).mean()

    return df


def build_signal(bar="15m"):
    df = get_market_data(bar=bar)
    last = df.iloc[-1]

    price = last["close"]
    rsi = last["rsi"]
    ema9 = last["ema9"]
    ema21 = last["ema21"]
    macd = last["macd"]
    macd_signal = last["macd_signal"]
    bb_upper = last["bb_upper"]
    bb_lower = last["bb_lower"]
    atr = last["atr"]
    vol = last["vol"]
    vol_avg = last["vol_avg"]

    score = 50
    reasons = []

    if ema9 > ema21:
        score += 15
        reasons.append("EMA9 выше EMA21 — рост")
    else:
        score -= 15
        reasons.append("EMA9 ниже EMA21 — снижение")

    if rsi < 30:
        score += 15
        reasons.append("RSI ниже 30 — перепроданность")
    elif rsi > 70:
        score -= 15
        reasons.append("RSI выше 70 — перекупленность")
    else:
        reasons.append("RSI нейтральный")

    if macd > macd_signal:
        score += 10
        reasons.append("MACD bullish")
    else:
        score -= 10
        reasons.append("MACD bearish")

    if price <= bb_lower:
        score += 10
        reasons.append("Цена у нижней Bollinger Band")
    elif price >= bb_upper:
        score -= 10
        reasons.append("Цена у верхней Bollinger Band")

    if vol > vol_avg:
        score += 5
        reasons.append("Объём выше среднего")
    else:
        reasons.append("Объём обычный")

    score = max(0, min(100, int(score)))

    if score >= MAX_SIGNAL_BUY:
        signal = "BUY"
    elif score <= MAX_SIGNAL_SELL:
        signal = "SELL"
    else:
        signal = "HOLD"

    trend = "восходящий" if ema9 > ema21 else "нисходящий"

    return {
        "bar": bar,
        "price": price,
        "rsi": rsi,
        "ema9": ema9,
        "ema21": ema21,
        "macd": macd,
        "macd_signal": macd_signal,
        "atr": atr,
        "trend": trend,
        "score": score,
        "signal": signal,
        "reasons": reasons
    }


def format_signal(result):
    reasons = "\n".join([f"• {x}" for x in result["reasons"]])
    return (
        f"📡 Сигнал {TRADE_SYMBOL} | {result['bar']}\n\n"
        f"Цена: {result['price']:.2f}\n"
        f"Тренд: {result['trend']}\n\n"
        f"RSI: {result['rsi']:.2f}\n"
        f"EMA9: {result['ema9']:.2f}\n"
        f"EMA21: {result['ema21']:.2f}\n"
        f"MACD: {result['macd']:.2f}\n"
        f"MACD Signal: {result['macd_signal']:.2f}\n"
        f"ATR: {result['atr']:.2f}\n\n"
        f"Сила сигнала: {result['score']}%\n"
        f"Рекомендация: {result['signal']}\n\n"
        f"Причины:\n{reasons}"
    )


def get_okx_balance_text():
    result = account_api.get_account_balance()

    if result.get("code") != "0":
        return f"❌ Ошибка OKX API:\n{result}"

    details = result.get("data", [{}])[0].get("details", [])
    if not details:
        return "Баланс OKX Demo: активов не найдено."

    lines = ["💰 Баланс OKX Demo:\n"]

    for item in details:
        ccy = item.get("ccy")
        bal = float(item.get("cashBal") or item.get("eq") or 0)
        avail = float(item.get("availBal") or 0)

        if bal > 0 or avail > 0:
            lines.append(f"{ccy}: баланс {bal:.8f}, доступно {avail:.8f}")

    return "\n".join(lines) if len(lines) > 1 else "Баланс OKX Demo: активов не найдено."


def get_available_balance(ccy):
    result = account_api.get_account_balance(ccy=ccy)
    details = result.get("data", [{}])[0].get("details", [])

    for item in details:
        if item.get("ccy") == ccy:
            return float(item.get("availBal") or 0)

    return 0.0


def place_demo_buy():
    if not is_demo():
        return {"error": "LIVE торговля заблокирована. Разрешён только DEMO режим."}

    return trade_api.place_order(
        instId=TRADE_SYMBOL,
        tdMode="cash",
        side="buy",
        ordType="market",
        sz=str(TRADE_AMOUNT_USDT),
        tgtCcy="quote_ccy"
    )


def place_demo_sell():
    if not is_demo():
        return {"error": "LIVE торговля заблокирована. Разрешён только DEMO режим."}

    base_ccy = TRADE_SYMBOL.split("-")[0]
    available = get_available_balance(base_ccy)

    if available <= 0:
        return {"error": f"Нет доступного баланса {base_ccy} для продажи."}

    return trade_api.place_order(
        instId=TRADE_SYMBOL,
        tdMode="cash",
        side="sell",
        ordType="market",
        sz=str(available)
    )


def add_history(action, signal, price, score, result):
    trade_history.append({
        "time": now(),
        "action": action,
        "signal": signal,
        "price": price,
        "score": score,
        "result": str(result)[:300]
    })

    if len(trade_history) > 20:
        trade_history.pop(0)


@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "🤖 OKX Crypto Trading Bot MAX запущен.\n\n"
        "/status — статус\n"
        "/balance — баланс OKX Demo\n"
        "/signal — расширенный сигнал\n"
        "/market — 5m / 15m / 1H анализ\n"
        "/monitor_on — автоанализ\n"
        "/monitor_off — стоп автоанализа\n"
        "/autotrade_on — DEMO автоторговля\n"
        "/autotrade_off — стоп автоторговли\n"
        "/autotrade_status — статус автоторговли\n"
        "/positions — активы\n"
        "/history — история действий\n"
        "/help — помощь"
    )


@dp.message(Command("help"))
async def help_command(message: types.Message):
    await message.answer(
        "⚠️ Бот работает только в DEMO режиме.\n\n"
        "LIVE торговля заблокирована кодом.\n"
        "Перед реальной торговлей нужно долго тестировать стратегию."
    )


@dp.message(Command("status"))
async def status(message: types.Message):
    mode = "DEMO" if is_demo() else "LIVE"
    api_status = "подключён" if OKX_API_KEY and OKX_SECRET_KEY and OKX_PASSPHRASE else "не подключён"

    await message.answer(
        f"✅ Статус бота\n\n"
        f"Режим OKX: {mode}\n"
        f"OKX API: {api_status}\n"
        f"Пара: {TRADE_SYMBOL}\n"
        f"Размер сделки: {TRADE_AMOUNT_USDT} USDT\n"
        f"Автоанализ: {'включён' if monitor_enabled else 'выключен'}\n"
        f"DEMO автоторговля: {'включена' if autotrade_enabled else 'выключена'}\n"
        f"LIVE торговля: заблокирована"
    )


@dp.message(Command("balance"))
async def balance(message: types.Message):
    try:
        await message.answer(get_okx_balance_text())
    except Exception as e:
        await message.answer(f"❌ Ошибка баланса:\n{e}")


@dp.message(Command("signal"))
async def signal(message: types.Message):
    try:
        result = build_signal("15m")
        await message.answer(format_signal(result))
    except Exception as e:
        await message.answer(f"❌ Ошибка сигнала:\n{e}")


@dp.message(Command("market"))
async def market(message: types.Message):
    try:
        text = f"🌐 Мультианализ {TRADE_SYMBOL}\n\n"

        for bar in ["5m", "15m", "1H"]:
            result = build_signal(bar)
            text += (
                f"{bar}: {result['signal']} | "
                f"сила {result['score']}% | "
                f"RSI {result['rsi']:.1f} | "
                f"{result['trend']}\n"
            )

        await message.answer(text)
    except Exception as e:
        await message.answer(f"❌ Ошибка market:\n{e}")


@dp.message(Command("positions"))
async def positions(message: types.Message):
    await message.answer(get_okx_balance_text())


@dp.message(Command("history"))
async def history(message: types.Message):
    if not trade_history:
        await message.answer("История пока пустая.")
        return

    text = "📜 История действий:\n\n"
    for item in trade_history[-10:]:
        text += (
            f"{item['time']}\n"
            f"{item['action']} | {item['signal']} | "
            f"цена {item['price']:.2f} | сила {item['score']}%\n\n"
        )

    await message.answer(text)


async def monitor_loop(chat_id):
    global monitor_enabled

    while monitor_enabled:
        try:
            result = build_signal("15m")

            if result["signal"] != "HOLD":
                await bot.send_message(
                    chat_id,
                    "🔔 Автосигнал найден\n\n" + format_signal(result)
                )

        except Exception as e:
            await bot.send_message(chat_id, f"❌ Ошибка автоанализа:\n{e}")

        await asyncio.sleep(MONITOR_INTERVAL)


@dp.message(Command("monitor_on"))
async def monitor_on(message: types.Message):
    global monitor_enabled

    if monitor_enabled:
        await message.answer("Автоанализ уже включён.")
        return

    monitor_enabled = True
    await message.answer("✅ Автоанализ включён. Проверка каждые 5 минут.")
    asyncio.create_task(monitor_loop(message.chat.id))


@dp.message(Command("monitor_off"))
async def monitor_off(message: types.Message):
    global monitor_enabled
    monitor_enabled = False
    await message.answer("⛔ Автоанализ выключен.")


async def autotrade_loop(chat_id):
    global autotrade_enabled

    while autotrade_enabled:
        try:
            if not is_demo():
                autotrade_enabled = False
                await bot.send_message(chat_id, "⛔ LIVE режим заблокирован. Автоторговля остановлена.")
                return

            result = build_signal("15m")
            signal_value = result["signal"]

            if signal_value == "BUY":
                order = place_demo_buy()
                add_history("DEMO BUY", signal_value, result["price"], result["score"], order)
                await bot.send_message(
                    chat_id,
                    f"🟢 DEMO BUY выполнен\n\n{format_signal(result)}\n\nОтвет OKX:\n{order}"
                )

            elif signal_value == "SELL":
                order = place_demo_sell()
                add_history("DEMO SELL", signal_value, result["price"], result["score"], order)
                await bot.send_message(
                    chat_id,
                    f"🔴 DEMO SELL выполнен\n\n{format_signal(result)}\n\nОтвет OKX:\n{order}"
                )

        except Exception as e:
            await bot.send_message(chat_id, f"❌ Ошибка DEMO автоторговли:\n{e}")

        await asyncio.sleep(AUTOTRADE_INTERVAL)


@dp.message(Command("autotrade_on"))
async def autotrade_on(message: types.Message):
    global autotrade_enabled

    if not is_demo():
        await message.answer("⛔ Автоторговля разрешена только при OKX_FLAG=1.")
        return

    if autotrade_enabled:
        await message.answer("DEMO автоторговля уже включена.")
        return

    autotrade_enabled = True
    await message.answer(
        "✅ DEMO автоторговля включена.\n\n"
        "Бот будет проверять рынок каждые 5 минут.\n"
        "LIVE торговля заблокирована."
    )
    asyncio.create_task(autotrade_loop(message.chat.id))


@dp.message(Command("autotrade_off"))
async def autotrade_off(message: types.Message):
    global autotrade_enabled
    autotrade_enabled = False
    await message.answer("⛔ DEMO автоторговля выключена.")


@dp.message(Command("autotrade_status"))
async def autotrade_status(message: types.Message):
    await message.answer(
        f"🤖 Статус автоторговли\n\n"
        f"DEMO автоторговля: {'включена' if autotrade_enabled else 'выключена'}\n"
        f"Автоанализ: {'включён' if monitor_enabled else 'выключен'}\n"
        f"Режим OKX: {'DEMO' if is_demo() else 'LIVE'}\n"
        f"Пара: {TRADE_SYMBOL}\n"
        f"Сумма сделки: {TRADE_AMOUNT_USDT} USDT\n"
        f"BUY если сила сигнала >= {MAX_SIGNAL_BUY}%\n"
        f"SELL если сила сигнала <= {MAX_SIGNAL_SELL}%"
    )


async def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Не найден TELEGRAM_BOT_TOKEN")

    print("OKX Crypto Trading Bot MAX started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
