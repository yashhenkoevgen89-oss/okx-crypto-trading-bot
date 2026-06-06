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


def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def get_market_data():
    result = market_api.get_candlesticks(
        instId=TRADE_SYMBOL,
        bar="15m",
        limit="100"
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

    df["close"] = df["close"].astype(float)
    df = df.iloc[::-1].reset_index(drop=True)

    df["ema_fast"] = df["close"].ewm(span=9).mean()
    df["ema_slow"] = df["close"].ewm(span=21).mean()
    df["rsi"] = calculate_rsi(df["close"])

    return df


def analyze_market():
    df = get_market_data()
    last = df.iloc[-1]

    if last["ema_fast"] > last["ema_slow"] and last["rsi"] < 70:
        signal = "BUY"
    elif last["ema_fast"] < last["ema_slow"] and last["rsi"] > 30:
        signal = "SELL"
    else:
        signal = "HOLD"

    return {
        "price": last["close"],
        "ema_fast": last["ema_fast"],
        "ema_slow": last["ema_slow"],
        "rsi": last["rsi"],
        "signal": signal
    }


def get_okx_balance():
    result = account_api.get_account_balance()

    if result.get("code") != "0":
        return f"Ошибка OKX API:\n{result}"

    data = result.get("data", [])
    if not data:
        return "Баланс пуст или OKX не вернул данные."

    details = data[0].get("details", [])
    if not details:
        return "На аккаунте нет активов."

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

    if len(lines) == 1:
        return "Баланс OKX Demo: активов не найдено."

    return "\n".join(lines)


@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "🤖 OKX Crypto Trading Bot запущен.\n\n"
        "Команды:\n"
        "/status — статус бота\n"
        "/analyze — анализ рынка OKX\n"
        "/balance — баланс OKX Demo\n"
        "/help — помощь"
    )


@dp.message(Command("help"))
async def help_command(message: types.Message):
    await message.answer(
        "📌 Команды:\n\n"
        "/start — запуск\n"
        "/status — статус\n"
        "/analyze — анализ BTC-USDT\n"
        "/balance — баланс OKX Demo\n\n"
        "Сейчас бот работает безопасно: анализ и просмотр баланса, без сделок."
    )


@dp.message(Command("status"))
async def status(message: types.Message):
    mode = "DEMO" if OKX_FLAG == "1" else "LIVE"

    api_status = "подключён" if OKX_API_KEY and OKX_SECRET_KEY and OKX_PASSPHRASE else "не подключён"

    await message.answer(
        f"✅ Бот работает\n\n"
        f"Режим OKX: {mode}\n"
        f"OKX API: {api_status}\n"
        f"Торговая пара: {TRADE_SYMBOL}\n"
        f"Размер сделки: {TRADE_AMOUNT_USDT} USDT\n"
        f"Автоторговля: выключена"
    )


@dp.message(Command("analyze"))
async def analyze(message: types.Message):
    try:
        result = analyze_market()
        await message.answer(
            f"📊 Анализ {TRADE_SYMBOL}\n\n"
            f"Цена: {result['price']}\n"
            f"RSI: {result['rsi']:.2f}\n"
            f"EMA 9: {result['ema_fast']:.2f}\n"
            f"EMA 21: {result['ema_slow']:.2f}\n\n"
            f"Сигнал: {result['signal']}"
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка анализа:\n{e}")


@dp.message(Command("balance"))
async def balance(message: types.Message):
    try:
        result = get_okx_balance()
        await message.answer(result)
    except Exception as e:
        await message.answer(f"❌ Ошибка баланса:\n{e}")


async def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Не найден TELEGRAM_BOT_TOKEN")

    print("OKX Crypto Trading Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
