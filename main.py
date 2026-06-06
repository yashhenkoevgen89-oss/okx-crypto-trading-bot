import os
import asyncio
import pandas as pd

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

import okx.MarketData as MarketData
import okx.Account as Account


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

OKX_API_KEY = os.getenv("OKX_API_KEY")
OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE")

OKX_FLAG = os.getenv("OKX_FLAG", "1")
TRADE_SYMBOL = os.getenv("TRADE_SYMBOL", "BTC-USDT")
TRADE_AMOUNT_USDT = float(os.getenv("TRADE_AMOUNT_USDT", "10"))

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

monitor_enabled = False


def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def get_market_data(bar="15m", limit=100):
    result = market_api.get_candlesticks(
        instId=TRADE_SYMBOL,
        bar=bar,
        limit=str(limit)
    )

    candles = result.get("data", [])
    if not candles:
        raise Exception("OKX не вернул данные свечей")

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

    df["ema_fast"] = df["close"].ewm(span=9).mean()
    df["ema_slow"] = df["close"].ewm(span=21).mean()
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
    ema_fast = last["ema_fast"]
    ema_slow = last["ema_slow"]
    macd = last["macd"]
    macd_signal = last["macd_signal"]
    bb_upper = last["bb_upper"]
    bb_lower = last["bb_lower"]
    atr = last["atr"]
    vol = last["vol"]
    vol_avg = last["vol_avg"]

    score = 50
    reasons = []

    if ema_fast > ema_slow:
        score += 15
        reasons.append("EMA показывает рост")
    else:
        score -= 15
        reasons.append("EMA показывает снижение")

    if rsi < 30:
        score += 15
        reasons.append("RSI зона перепроданности")
    elif rsi > 70:
        score -= 15
        reasons.append("RSI зона перекупленности")
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

    score = max(0, min(100, score))

    if score >= 65:
        signal = "BUY"
    elif score <= 35:
        signal = "SELL"
    else:
        signal = "HOLD"

    trend = "восходящий" if ema_fast > ema_slow else "нисходящий"

    return {
        "bar": bar,
        "price": price,
        "rsi": rsi,
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "macd": macd,
        "macd_signal": macd_signal,
        "atr": atr,
        "trend": trend,
        "score": score,
        "signal": signal,
        "reasons": reasons
    }


def get_okx_balance():
    result = account_api.get_account_balance()

    if result.get("code") != "0":
        return f"Ошибка OKX API:\n{result}"

    details = result.get("data", [{}])[0].get("details", [])
    if not details:
        return "Баланс OKX Demo: активов не найдено."

    lines = ["💰 Баланс OKX Demo:\n"]

    for item in details:
        ccy = item.get("ccy")
        bal = item.get("cashBal") or item.get("eq") or "0"
        avail = item.get("availBal") or "0"

        try:
            bal_float = float(bal)
            avail_float = float(avail)
        except ValueError:
            continue

        if bal_float > 0 or avail_float > 0:
            lines.append(
                f"{ccy}: баланс {bal_float:.8f}, доступно {avail_float:.8f}"
            )

    return "\n".join(lines) if len(lines) > 1 else "Баланс OKX Demo: активов не найдено."


def format_signal(result):
    reasons_text = "\n".join([f"• {r}" for r in result["reasons"]])

    return (
        f"📡 Сигнал {TRADE_SYMBOL} | {result['bar']}\n\n"
        f"Цена: {result['price']:.2f}\n"
        f"Тренд: {result['trend']}\n\n"
        f"RSI: {result['rsi']:.2f}\n"
        f"EMA 9: {result['ema_fast']:.2f}\n"
        f"EMA 21: {result['ema_slow']:.2f}\n"
        f"MACD: {result['macd']:.2f}\n"
        f"MACD Signal: {result['macd_signal']:.2f}\n"
        f"ATR: {result['atr']:.2f}\n\n"
        f"Сила сигнала: {result['score']}%\n"
        f"Рекомендация: {result['signal']}\n\n"
        f"Причины:\n{reasons_text}"
    )


@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "🤖 OKX Crypto Trading Bot запущен.\n\n"
        "/status — статус\n"
        "/analyze — быстрый анализ\n"
        "/signal — расширенный сигнал\n"
        "/market — анализ 5m / 15m / 1H\n"
        "/balance — баланс OKX Demo\n"
        "/monitor_on — включить автоанализ\n"
        "/monitor_off — выключить автоанализ\n"
        "/help — помощь"
    )


@dp.message(Command("help"))
async def help_command(message: types.Message):
    await message.answer(
        "📌 Бот работает в безопасном режиме.\n\n"
        "Он анализирует рынок, показывает баланс и отправляет сигналы.\n"
        "Покупка и продажа пока отключены."
    )


@dp.message(Command("status"))
async def status(message: types.Message):
    mode = "DEMO" if OKX_FLAG == "1" else "LIVE"
    api_status = "подключён" if OKX_API_KEY and OKX_SECRET_KEY and OKX_PASSPHRASE else "не подключён"

    await message.answer(
        f"✅ Бот работает\n\n"
        f"Режим OKX: {mode}\n"
        f"OKX API: {api_status}\n"
        f"Пара: {TRADE_SYMBOL}\n"
        f"Размер сделки: {TRADE_AMOUNT_USDT} USDT\n"
        f"Автоанализ: {'включён' if monitor_enabled else 'выключен'}\n"
        f"Автоторговля: выключена"
    )


@dp.message(Command("analyze"))
async def analyze(message: types.Message):
    try:
        result = build_signal("15m")
        await message.answer(
            f"📊 Анализ {TRADE_SYMBOL}\n\n"
            f"Цена: {result['price']:.2f}\n"
            f"RSI: {result['rsi']:.2f}\n"
            f"EMA 9: {result['ema_fast']:.2f}\n"
            f"EMA 21: {result['ema_slow']:.2f}\n\n"
            f"Сигнал: {result['signal']}"
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка анализа:\n{e}")


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
        bars = ["5m", "15m", "1H"]
        text = f"🌐 Мультианализ {TRADE_SYMBOL}\n\n"

        for bar in bars:
            result = build_signal(bar)
            text += (
                f"{bar}: {result['signal']} | "
                f"сила {result['score']}% | "
                f"RSI {result['rsi']:.1f} | "
                f"тренд {result['trend']}\n"
            )

        await message.answer(text)

    except Exception as e:
        await message.answer(f"❌ Ошибка market:\n{e}")


@dp.message(Command("balance"))
async def balance(message: types.Message):
    try:
        await message.answer(get_okx_balance())
    except Exception as e:
        await message.answer(f"❌ Ошибка баланса:\n{e}")


async def monitor_loop(chat_id):
    global monitor_enabled

    while monitor_enabled:
        try:
            result = build_signal("15m")

            if result["signal"] != "HOLD":
                await bot.send_message(
                    chat_id,
                    "🔔 Найден торговый сигнал\n\n" + format_signal(result)
                )

        except Exception as e:
            await bot.send_message(chat_id, f"❌ Ошибка автоанализа:\n{e}")

        await asyncio.sleep(300)


@dp.message(Command("monitor_on"))
async def monitor_on(message: types.Message):
    global monitor_enabled

    if monitor_enabled:
        await message.answer("Автоанализ уже включён.")
        return

    monitor_enabled = True
    await message.answer("✅ Автоанализ включён. Проверка рынка каждые 5 минут.")
    asyncio.create_task(monitor_loop(message.chat.id))


@dp.message(Command("monitor_off"))
async def monitor_off(message: types.Message):
    global monitor_enabled
    monitor_enabled = False
    await message.answer("⛔ Автоанализ выключен.")


async def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Не найден TELEGRAM_BOT_TOKEN")

    print("OKX Crypto Trading Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
