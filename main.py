import os, json, asyncio
from datetime import datetime
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

TRADE_SYMBOL = os.getenv("TRADE_SYMBOL", "BTC-USDT")
STATE_FILE = "bot_state.json"

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

market_api = MarketData.MarketAPI(flag=OKX_FLAG)
account_api = Account.AccountAPI(OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE, False, OKX_FLAG)
trade_api = Trade.TradeAPI(OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE, False, OKX_FLAG)

monitor_enabled = False
autotrade_enabled = False
trade_history = []
open_virtual_position = None

risk_settings = {
    "amount_usdt": float(os.getenv("TRADE_AMOUNT_USDT", "10")),
    "max_amount_usdt": 50.0,
    "stop_loss_percent": 2.0,
    "take_profit_percent": 4.0,
    "buy_score": 70,
    "sell_score": 30
}

keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="/status"), KeyboardButton(text="/balance")],
        [KeyboardButton(text="/signal"), KeyboardButton(text="/market")],
        [KeyboardButton(text="/scan"), KeyboardButton(text="/risk")],
        [KeyboardButton(text="/autotrade_status"), KeyboardButton(text="/pnl")],
        [KeyboardButton(text="/save"), KeyboardButton(text="/load")]
    ],
    resize_keyboard=True
)


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_demo():
    return OKX_FLAG == "1"


def safe_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default


def save_state():
    data = {
        "trade_history": trade_history,
        "risk_settings": risk_settings,
        "open_virtual_position": open_virtual_position,
        "trade_symbol": TRADE_SYMBOL
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_state():
    global trade_history, risk_settings, open_virtual_position, TRADE_SYMBOL

    if not os.path.exists(STATE_FILE):
        return False

    with open(STATE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    trade_history = data.get("trade_history", [])
    risk_settings.update(data.get("risk_settings", {}))
    open_virtual_position = data.get("open_virtual_position")
    TRADE_SYMBOL = data.get("trade_symbol", TRADE_SYMBOL)
    return True


def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def get_market_data(symbol=None, bar="15m", limit=120):
    symbol = symbol or TRADE_SYMBOL

    result = market_api.get_candlesticks(instId=symbol, bar=bar, limit=str(limit))
    candles = result.get("data", [])

    if not candles:
        raise Exception(f"OKX не вернул свечи: {result}")

    df = pd.DataFrame(
        candles,
        columns=["ts", "open", "high", "low", "close", "vol", "volCcy", "volCcyQuote", "confirm"]
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

    df["tr"] = pd.concat([
        df["high"] - df["low"],
        abs(df["high"] - df["close"].shift()),
        abs(df["low"] - df["close"].shift())
    ], axis=1).max(axis=1)

    df["atr"] = df["tr"].rolling(14).mean()
    df["vol_avg"] = df["vol"].rolling(20).mean()

    return df


def build_signal(symbol=None, bar="15m"):
    symbol = symbol or TRADE_SYMBOL
    df = get_market_data(symbol, bar)
    last = df.iloc[-1]

    price = safe_float(last["close"])
    rsi = safe_float(last["rsi"])
    ema9 = safe_float(last["ema9"])
    ema21 = safe_float(last["ema21"])
    macd = safe_float(last["macd"])
    macd_signal = safe_float(last["macd_signal"])
    bb_upper = safe_float(last["bb_upper"])
    bb_lower = safe_float(last["bb_lower"])
    vol = safe_float(last["vol"])
    vol_avg = safe_float(last["vol_avg"])
    atr = safe_float(last["atr"])

    score = 50
    reasons = []

    if ema9 > ema21:
        score += 15
        reasons.append("EMA9 > EMA21 — рост")
    else:
        score -= 15
        reasons.append("EMA9 < EMA21 — снижение")

    if rsi < 30:
        score += 15
        reasons.append("RSI перепроданность")
    elif rsi > 70:
        score -= 15
        reasons.append("RSI перекупленность")
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
        reasons.append("Цена у нижней Bollinger")
    elif price >= bb_upper:
        score -= 10
        reasons.append("Цена у верхней Bollinger")

    if vol > vol_avg:
        score += 5
        reasons.append("Объём выше среднего")

    score = max(0, min(100, int(score)))

    if score >= risk_settings["buy_score"]:
        signal = "BUY"
    elif score <= risk_settings["sell_score"]:
        signal = "SELL"
    else:
        signal = "HOLD"

    return {
        "symbol": symbol,
        "bar": bar,
        "price": price,
        "rsi": rsi,
        "ema9": ema9,
        "ema21": ema21,
        "macd": macd,
        "macd_signal": macd_signal,
        "atr": atr,
        "score": score,
        "signal": signal,
        "trend": "восходящий" if ema9 > ema21 else "нисходящий",
        "reasons": reasons
    }


def format_signal(r):
    reasons = "\n".join(f"• {x}" for x in r["reasons"])

    return (
        f"📡 {r['symbol']} | {r['bar']}\n\n"
        f"Цена: {r['price']:.2f}\n"
        f"Тренд: {r['trend']}\n"
        f"RSI: {r['rsi']:.2f}\n"
        f"EMA9: {r['ema9']:.2f}\n"
        f"EMA21: {r['ema21']:.2f}\n"
        f"MACD: {r['macd']:.2f}\n"
        f"ATR: {r['atr']:.2f}\n\n"
        f"Сила: {r['score']}%\n"
        f"Сигнал: {r['signal']}\n\n"
        f"{reasons}"
    )


def multi_timeframe_decision():
    results = [build_signal(TRADE_SYMBOL, bar) for bar in ["5m", "15m", "1H"]]
    buy_count = sum(1 for r in results if r["signal"] == "BUY")
    sell_count = sum(1 for r in results if r["signal"] == "SELL")
    avg_score = int(sum(r["score"] for r in results) / len(results))

    if buy_count >= 2 and avg_score >= risk_settings["buy_score"]:
        final = "BUY"
    elif sell_count >= 2 and avg_score <= risk_settings["sell_score"]:
        final = "SELL"
    else:
        final = "HOLD"

    return {"signal": final, "avg_score": avg_score, "price": results[1]["price"], "results": results}


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


def get_available_balance(ccy):
    result = account_api.get_account_balance(ccy=ccy)
    details = result.get("data", [{}])[0].get("details", [])

    for item in details:
        if item.get("ccy") == ccy:
            return safe_float(item.get("availBal"))

    return 0.0


def place_demo_buy():
    if not is_demo():
        return {"error": "LIVE торговля заблокирована"}

    amount = min(risk_settings["amount_usdt"], risk_settings["max_amount_usdt"])

    return trade_api.place_order(
        instId=TRADE_SYMBOL,
        tdMode="cash",
        side="buy",
        ordType="market",
        sz=str(amount),
        tgtCcy="quote_ccy"
    )


def place_demo_sell():
    if not is_demo():
        return {"error": "LIVE торговля заблокирована"}

    base = TRADE_SYMBOL.split("-")[0]
    available = get_available_balance(base)

    if available <= 0:
        return {"error": f"Нет доступного баланса {base}"}

    return trade_api.place_order(
        instId=TRADE_SYMBOL,
        tdMode="cash",
        side="sell",
        ordType="market",
        sz=str(available)
    )


def add_history(action, signal, price, score, result=""):
    trade_history.append({
        "time": now(),
        "action": action,
        "signal": signal,
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


@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "🤖 OKX Crypto Trading Bot PRO MAX v3\n\n"
        "/status — статус\n"
        "/signal — сигнал\n"
        "/market — мультианализ\n"
        "/scan — сканер монет\n"
        "/balance — баланс\n"
        "/risk — риск\n"
        "/set_symbol BTC-USDT — сменить пару\n"
        "/set_amount 10 — сумма сделки\n"
        "/set_stoploss 2 — стоп-лосс\n"
        "/set_takeprofit 4 — тейк-профит\n"
        "/autotrade_on — DEMO автоторговля\n"
        "/autotrade_off — стоп\n"
        "/pnl — статистика\n"
        "/save — сохранить\n"
        "/load — загрузить\n"
        "/reset_history — очистить историю\n"
        "/reset_position — сбросить позицию",
        reply_markup=keyboard
    )


@dp.message(Command("status"))
async def status(message: types.Message):
    await message.answer(
        f"✅ PRO MAX v3\n\n"
        f"Режим: {'DEMO' if is_demo() else 'LIVE'}\n"
        f"Пара: {TRADE_SYMBOL}\n"
        f"Сумма сделки: {risk_settings['amount_usdt']} USDT\n"
        f"SL: {risk_settings['stop_loss_percent']}%\n"
        f"TP: {risk_settings['take_profit_percent']}%\n"
        f"Автоторговля: {'включена' if autotrade_enabled else 'выключена'}\n"
        f"LIVE: заблокирован"
    )


@dp.message(Command("signal"))
async def signal(message: types.Message):
    await message.answer(format_signal(build_signal()))


@dp.message(Command("market"))
async def market(message: types.Message):
    d = multi_timeframe_decision()
    text = f"🌐 Мультианализ {TRADE_SYMBOL}\n\n"

    for r in d["results"]:
        text += f"{r['bar']}: {r['signal']} | {r['score']}% | RSI {r['rsi']:.1f}\n"

    text += f"\nИтог: {d['signal']}\nСредняя сила: {d['avg_score']}%"
    await message.answer(text)


@dp.message(Command("scan"))
async def scan(message: types.Message):
    coins = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "ADA-USDT", "DOGE-USDT"]
    results = []

    for coin in coins:
        try:
            r = build_signal(coin, "15m")
            results.append(r)
        except Exception:
            continue

    results = sorted(results, key=lambda x: x["score"], reverse=True)

    text = "🔎 Сканер рынка OKX\n\n"
    for r in results:
        text += f"{r['symbol']}: {r['signal']} | сила {r['score']}% | RSI {r['rsi']:.1f}\n"

    await message.answer(text)


@dp.message(Command("balance"))
async def balance(message: types.Message):
    await message.answer(get_okx_balance_text())


@dp.message(Command("risk"))
async def risk(message: types.Message):
    await message.answer(
        f"🛡 Риск\n\n"
        f"Сумма: {risk_settings['amount_usdt']} USDT\n"
        f"Максимум: {risk_settings['max_amount_usdt']} USDT\n"
        f"SL: {risk_settings['stop_loss_percent']}%\n"
        f"TP: {risk_settings['take_profit_percent']}%\n"
        f"BUY >= {risk_settings['buy_score']}%\n"
        f"SELL <= {risk_settings['sell_score']}%"
    )


@dp.message(Command("set_amount"))
async def set_amount(message: types.Message):
    v = parse_number(message.text)
    if not v or v <= 0 or v > risk_settings["max_amount_usdt"]:
        await message.answer(f"Формат: /set_amount 10\nМаксимум {risk_settings['max_amount_usdt']} USDT")
        return

    risk_settings["amount_usdt"] = v
    save_state()
    await message.answer(f"✅ Сумма сделки: {v} USDT")


@dp.message(Command("set_stoploss"))
async def set_stoploss(message: types.Message):
    v = parse_number(message.text)
    if not v or v <= 0 or v > 20:
        await message.answer("Формат: /set_stoploss 2")
        return

    risk_settings["stop_loss_percent"] = v
    save_state()
    await message.answer(f"✅ Stop Loss: {v}%")


@dp.message(Command("set_takeprofit"))
async def set_takeprofit(message: types.Message):
    v = parse_number(message.text)
    if not v or v <= 0 or v > 50:
        await message.answer("Формат: /set_takeprofit 4")
        return

    risk_settings["take_profit_percent"] = v
    save_state()
    await message.answer(f"✅ Take Profit: {v}%")


@dp.message(Command("set_symbol"))
async def set_symbol(message: types.Message):
    global TRADE_SYMBOL

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Формат: /set_symbol BTC-USDT")
        return

    TRADE_SYMBOL = parts[1].upper()
    save_state()
    await message.answer(f"✅ Торговая пара изменена: {TRADE_SYMBOL}")


@dp.message(Command("pnl"))
async def pnl(message: types.Message):
    buys = len([x for x in trade_history if "BUY" in x["action"]])
    sells = len([x for x in trade_history if "SELL" in x["action"]])

    await message.answer(
        f"📊 PnL / статистика\n\n"
        f"Всего действий: {len(trade_history)}\n"
        f"BUY: {buys}\n"
        f"SELL: {sells}\n\n"
        f"Точный PnL по ордерам добавим в v4."
    )


@dp.message(Command("history"))
async def history(message: types.Message):
    if not trade_history:
        await message.answer("История пустая.")
        return

    text = "📜 История:\n\n"
    for x in trade_history[-10:]:
        text += f"{x['time']} | {x['action']} | {x['price']:.2f} | {x['score']}%\n"

    await message.answer(text)


@dp.message(Command("save"))
async def save_cmd(message: types.Message):
    save_state()
    await message.answer("✅ Состояние сохранено.")


@dp.message(Command("load"))
async def load_cmd(message: types.Message):
    ok = load_state()
    await message.answer("✅ Состояние загружено." if ok else "Файл состояния пока не найден.")


@dp.message(Command("reset_history"))
async def reset_history(message: types.Message):
    trade_history.clear()
    save_state()
    await message.answer("✅ История очищена.")


@dp.message(Command("reset_position"))
async def reset_position(message: types.Message):
    global open_virtual_position
    open_virtual_position = None
    save_state()
    await message.answer("✅ Позиция сброшена.")


async def autotrade_loop(chat_id):
    global autotrade_enabled, open_virtual_position

    while autotrade_enabled:
        try:
            if not is_demo():
                autotrade_enabled = False
                await bot.send_message(chat_id, "⛔ LIVE режим заблокирован.")
                return

            d = multi_timeframe_decision()
            price = d["price"]

            if d["signal"] == "BUY" and open_virtual_position is None:
                order = place_demo_buy()

                open_virtual_position = {
                    "entry": price,
                    "time": now(),
                    "sl": price * (1 - risk_settings["stop_loss_percent"] / 100),
                    "tp": price * (1 + risk_settings["take_profit_percent"] / 100)
                }

                add_history("DEMO BUY", "BUY", price, d["avg_score"], order)

                await bot.send_message(
                    chat_id,
                    f"🟢 DEMO BUY\nЦена: {price:.2f}\nSL: {open_virtual_position['sl']:.2f}\nTP: {open_virtual_position['tp']:.2f}"
                )

            elif open_virtual_position:
                entry = open_virtual_position["entry"]
                sl = open_virtual_position["sl"]
                tp = open_virtual_position["tp"]

                reason = None

                if price <= sl:
                    reason = "STOP LOSS"
                elif price >= tp:
                    reason = "TAKE PROFIT"
                elif d["signal"] == "SELL":
                    reason = "SELL SIGNAL"

                if reason:
                    order = place_demo_sell()
                    pnl_percent = ((price - entry) / entry) * 100
                    add_history(f"DEMO SELL {reason}", "SELL", price, d["avg_score"], order)

                    open_virtual_position = None
                    save_state()

                    await bot.send_message(
                        chat_id,
                        f"🔴 DEMO SELL\nПричина: {reason}\nЦена: {price:.2f}\nPnL: {pnl_percent:.2f}%"
                    )

        except Exception as e:
            await bot.send_message(chat_id, f"❌ Ошибка автоторговли:\n{e}")

        await asyncio.sleep(300)


@dp.message(Command("autotrade_on"))
async def autotrade_on(message: types.Message):
    global autotrade_enabled

    if not is_demo():
        await message.answer("⛔ Только DEMO.")
        return

    if autotrade_enabled:
        await message.answer("Автоторговля уже включена.")
        return

    autotrade_enabled = True
    await message.answer("✅ DEMO автоторговля включена.")
    asyncio.create_task(autotrade_loop(message.chat.id))


@dp.message(Command("autotrade_off"))
async def autotrade_off(message: types.Message):
    global autotrade_enabled
    autotrade_enabled = False
    await message.answer("⛔ DEMO автоторговля выключена.")


async def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Нет TELEGRAM_BOT_TOKEN")

    load_state()
    print("OKX Crypto Trading Bot PRO MAX v3 started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
