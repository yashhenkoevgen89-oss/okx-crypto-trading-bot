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

risk_settings = {
    "amount_usdt": TRADE_AMOUNT_USDT,
    "max_amount_usdt": 50.0,
    "stop_loss_percent": 2.0,
    "take_profit_percent": 4.0,
    "buy_score": 70,
    "sell_score": 30,
}

trade_history = []
open_virtual_position = None


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_demo():
    return OKX_FLAG == "1"


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


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

    price = safe_float(last["close"])
    rsi = safe_float(last["rsi"])
    ema9 = safe_float(last["ema9"])
    ema21 = safe_float(last["ema21"])
    macd = safe_float(last["macd"])
    macd_signal = safe_float(last["macd_signal"])
    bb_upper = safe_float(last["bb_upper"])
    bb_lower = safe_float(last["bb_lower"])
    atr = safe_float(last["atr"])
    vol = safe_float(last["vol"])
    vol_avg = safe_float(last["vol_avg"])

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

    if score >= risk_settings["buy_score"]:
        signal = "BUY"
    elif score <= risk_settings["sell_score"]:
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
        "reasons": reasons,
    }


def multi_timeframe_decision():
    bars = ["5m", "15m", "1H"]
    results = [build_signal(bar) for bar in bars]

    buy_count = sum(1 for r in results if r["signal"] == "BUY")
    sell_count = sum(1 for r in results if r["signal"] == "SELL")

    avg_score = int(sum(r["score"] for r in results) / len(results))
    price = results[1]["price"]

    if buy_count >= 2 and avg_score >= risk_settings["buy_score"]:
        final_signal = "BUY"
    elif sell_count >= 2 and avg_score <= risk_settings["sell_score"]:
        final_signal = "SELL"
    else:
        final_signal = "HOLD"

    return {
        "signal": final_signal,
        "avg_score": avg_score,
        "price": price,
        "results": results,
    }


def format_signal(result):
    reasons = "\n".join([f"• {x}" for x in result["reasons"]])

    return (
        f"📡 Сигнал {TRADE_SYMBOL} | {result['bar']}\n\n"
        f"Цена: {result['price']:.2f}\n"
        f"Тренд: {result['trend']}\n"
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
        bal = safe_float(item.get("cashBal") or item.get("eq"))
        avail = safe_float(item.get("availBal"))

        if bal > 0 or avail > 0:
            lines.append(f"{ccy}: баланс {bal:.8f}, доступно {avail:.8f}")

    return "\n".join(lines) if len(lines) > 1 else "Баланс OKX Demo: активов не найдено."


def get_available_balance(ccy):
    result = account_api.get_account_balance(ccy=ccy)
    details = result.get("data", [{}])[0].get("details", [])

    for item in details:
        if item.get("ccy") == ccy:
            return safe_float(item.get("availBal"))

    return 0.0


def place_demo_buy():
    if not is_demo():
        return {"error": "LIVE торговля заблокирована."}

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
        return {"error": "LIVE торговля заблокирована."}

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
        "result": str(result)[:300],
    })

    if len(trade_history) > 50:
        trade_history.pop(0)


def calculate_virtual_pnl():
    buys = [x for x in trade_history if "BUY" in x["action"]]
    sells = [x for x in trade_history if "SELL" in x["action"]]

    total_trades = len(trade_history)
    return {
        "total": total_trades,
        "buys": len(buys),
        "sells": len(sells),
    }


def parse_number(message_text):
    parts = message_text.split()
    if len(parts) < 2:
        return None
    try:
        return float(parts[1].replace(",", "."))
    except Exception:
        return None


@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "🤖 OKX Crypto Trading Bot PRO MAX v2 запущен.\n\n"
        "/status — статус\n"
        "/balance — баланс\n"
        "/signal — сигнал 15m\n"
        "/market — мультианализ\n"
        "/risk — риск-настройки\n"
        "/set_amount 10 — сумма сделки\n"
        "/set_stoploss 2 — стоп-лосс %\n"
        "/set_takeprofit 4 — тейк-профит %\n"
        "/monitor_on — автоанализ\n"
        "/monitor_off — стоп автоанализа\n"
        "/autotrade_on — DEMO автоторговля\n"
        "/autotrade_off — стоп автоторговли\n"
        "/autotrade_status — статус\n"
        "/positions — активы\n"
        "/history — история\n"
        "/pnl — статистика\n"
        "/strategy — стратегия"
    )


@dp.message(Command("status"))
async def status(message: types.Message):
    await message.answer(
        f"✅ Статус PRO MAX v2\n\n"
        f"Режим OKX: {'DEMO' if is_demo() else 'LIVE'}\n"
        f"OKX API: {'подключён' if OKX_API_KEY else 'не подключён'}\n"
        f"Пара: {TRADE_SYMBOL}\n"
        f"Сумма сделки: {risk_settings['amount_usdt']} USDT\n"
        f"Stop Loss: {risk_settings['stop_loss_percent']}%\n"
        f"Take Profit: {risk_settings['take_profit_percent']}%\n"
        f"Автоанализ: {'включён' if monitor_enabled else 'выключен'}\n"
        f"DEMO автоторговля: {'включена' if autotrade_enabled else 'выключена'}\n"
        f"LIVE торговля: заблокирована"
    )


@dp.message(Command("risk"))
async def risk(message: types.Message):
    await message.answer(
        f"🛡 Риск-настройки\n\n"
        f"Сумма сделки: {risk_settings['amount_usdt']} USDT\n"
        f"Максимум сделки: {risk_settings['max_amount_usdt']} USDT\n"
        f"Stop Loss: {risk_settings['stop_loss_percent']}%\n"
        f"Take Profit: {risk_settings['take_profit_percent']}%\n"
        f"BUY при силе >= {risk_settings['buy_score']}%\n"
        f"SELL при силе <= {risk_settings['sell_score']}%\n\n"
        f"Команды:\n"
        f"/set_amount 10\n"
        f"/set_stoploss 2\n"
        f"/set_takeprofit 4"
    )


@dp.message(Command("set_amount"))
async def set_amount(message: types.Message):
    value = parse_number(message.text)
    if value is None or value <= 0:
        await message.answer("Используй формат: /set_amount 10")
        return

    if value > risk_settings["max_amount_usdt"]:
        await message.answer(f"Максимальная сумма сделки: {risk_settings['max_amount_usdt']} USDT")
        return

    risk_settings["amount_usdt"] = value
    await message.answer(f"✅ Сумма сделки изменена: {value} USDT")


@dp.message(Command("set_stoploss"))
async def set_stoploss(message: types.Message):
    value = parse_number(message.text)
    if value is None or value <= 0 or value > 20:
        await message.answer("Используй формат: /set_stoploss 2")
        return

    risk_settings["stop_loss_percent"] = value
    await message.answer(f"✅ Stop Loss установлен: {value}%")


@dp.message(Command("set_takeprofit"))
async def set_takeprofit(message: types.Message):
    value = parse_number(message.text)
    if value is None or value <= 0 or value > 50:
        await message.answer("Используй формат: /set_takeprofit 4")
        return

    risk_settings["take_profit_percent"] = value
    await message.answer(f"✅ Take Profit установлен: {value}%")


@dp.message(Command("balance"))
async def balance(message: types.Message):
    await message.answer(get_okx_balance_text())


@dp.message(Command("positions"))
async def positions(message: types.Message):
    await message.answer(get_okx_balance_text())


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
        decision = multi_timeframe_decision()
        text = f"🌐 Мультианализ {TRADE_SYMBOL}\n\n"

        for r in decision["results"]:
            text += (
                f"{r['bar']}: {r['signal']} | "
                f"сила {r['score']}% | "
                f"RSI {r['rsi']:.1f} | "
                f"{r['trend']}\n"
            )

        text += (
            f"\nИтог: {decision['signal']}\n"
            f"Средняя сила: {decision['avg_score']}%\n"
            f"Цена: {decision['price']:.2f}"
        )

        await message.answer(text)
    except Exception as e:
        await message.answer(f"❌ Ошибка market:\n{e}")


@dp.message(Command("history"))
async def history(message: types.Message):
    if not trade_history:
        await message.answer("История пока пустая.")
        return

    text = "📜 Последние действия:\n\n"
    for item in trade_history[-10:]:
        text += (
            f"{item['time']}\n"
            f"{item['action']} | {item['signal']} | "
            f"цена {item['price']:.2f} | сила {item['score']}%\n\n"
        )

    await message.answer(text)


@dp.message(Command("pnl"))
async def pnl(message: types.Message):
    stats = calculate_virtual_pnl()
    await message.answer(
        f"📊 Статистика DEMO\n\n"
        f"Всего действий: {stats['total']}\n"
        f"BUY: {stats['buys']}\n"
        f"SELL: {stats['sells']}\n\n"
        f"Расчёт прибыли в v2 пока виртуальный.\n"
        f"В следующей версии добавим точный PnL по ордерам OKX."
    )


@dp.message(Command("strategy"))
async def strategy(message: types.Message):
    await message.answer(
        "🧠 Стратегия PRO MAX v2\n\n"
        "Индикаторы:\n"
        "• EMA9 / EMA21\n"
        "• RSI\n"
        "• MACD\n"
        "• Bollinger Bands\n"
        "• Объём\n"
        "• ATR\n\n"
        "Вход BUY:\n"
        "• сила сигнала >= 70%\n"
        "• подтверждение минимум 2 из 3 таймфреймов\n\n"
        "Вход SELL:\n"
        "• сила сигнала <= 30%\n"
        "• подтверждение минимум 2 из 3 таймфреймов\n\n"
        "LIVE торговля заблокирована."
    )


async def monitor_loop(chat_id):
    global monitor_enabled

    while monitor_enabled:
        try:
            decision = multi_timeframe_decision()

            if decision["signal"] != "HOLD":
                await bot.send_message(
                    chat_id,
                    f"🔔 Автосигнал {TRADE_SYMBOL}\n\n"
                    f"Итог: {decision['signal']}\n"
                    f"Сила: {decision['avg_score']}%\n"
                    f"Цена: {decision['price']:.2f}"
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
    await message.answer("✅ Автоанализ включён.")
    asyncio.create_task(monitor_loop(message.chat.id))


@dp.message(Command("monitor_off"))
async def monitor_off(message: types.Message):
    global monitor_enabled
    monitor_enabled = False
    await message.answer("⛔ Автоанализ выключен.")


async def autotrade_loop(chat_id):
    global autotrade_enabled
    global open_virtual_position

    while autotrade_enabled:
        try:
            if not is_demo():
                autotrade_enabled = False
                await bot.send_message(chat_id, "⛔ LIVE режим заблокирован.")
                return

            decision = multi_timeframe_decision()
            signal_value = decision["signal"]
            price = decision["price"]
            score = decision["avg_score"]

            if signal_value == "BUY" and open_virtual_position is None:
                order = place_demo_buy()

                open_virtual_position = {
                    "side": "BUY",
                    "entry": price,
                    "time": now(),
                    "sl": price * (1 - risk_settings["stop_loss_percent"] / 100),
                    "tp": price * (1 + risk_settings["take_profit_percent"] / 100),
                }

                add_history("DEMO BUY", signal_value, price, score, order)

                await bot.send_message(
                    chat_id,
                    f"🟢 DEMO BUY\n\n"
                    f"Цена входа: {price:.2f}\n"
                    f"SL: {open_virtual_position['sl']:.2f}\n"
                    f"TP: {open_virtual_position['tp']:.2f}\n"
                    f"Сила: {score}%\n\n"
                    f"Ответ OKX:\n{order}"
                )

            elif open_virtual_position is not None:
                entry = open_virtual_position["entry"]
                sl = open_virtual_position["sl"]
                tp = open_virtual_position["tp"]

                should_sell = False
                reason = ""

                if price <= sl:
                    should_sell = True
                    reason = "Stop Loss"
                elif price >= tp:
                    should_sell = True
                    reason = "Take Profit"
                elif signal_value == "SELL":
                    should_sell = True
                    reason = "SELL signal"

                if should_sell:
                    order = place_demo_sell()
                    pnl_percent = ((price - entry) / entry) * 100

                    add_history(f"DEMO SELL {reason}", signal_value, price, score, order)
                    open_virtual_position = None

                    await bot.send_message(
                        chat_id,
                        f"🔴 DEMO SELL\n\n"
                        f"Причина: {reason}\n"
                        f"Цена выхода: {price:.2f}\n"
                        f"PnL: {pnl_percent:.2f}%\n\n"
                        f"Ответ OKX:\n{order}"
                    )

        except Exception as e:
            await bot.send_message(chat_id, f"❌ Ошибка автоторговли:\n{e}")

        await asyncio.sleep(300)


@dp.message(Command("autotrade_on"))
async def autotrade_on(message: types.Message):
    global autotrade_enabled

    if not is_demo():
        await message.answer("⛔ Автоторговля разрешена только в DEMO.")
        return

    if autotrade_enabled:
        await message.answer("DEMO автоторговля уже включена.")
        return

    autotrade_enabled = True
    await message.answer("✅ DEMO автоторговля PRO MAX v2 включена.")
    asyncio.create_task(autotrade_loop(message.chat.id))


@dp.message(Command("autotrade_off"))
async def autotrade_off(message: types.Message):
    global autotrade_enabled
    autotrade_enabled = False
    await message.answer("⛔ DEMO автоторговля выключена.")


@dp.message(Command("autotrade_status"))
async def autotrade_status(message: types.Message):
    position_text = "нет"
    if open_virtual_position:
        position_text = (
            f"BUY от {open_virtual_position['entry']:.2f}\n"
            f"SL: {open_virtual_position['sl']:.2f}\n"
            f"TP: {open_virtual_position['tp']:.2f}"
        )

    await message.answer(
        f"🤖 Автоторговля PRO MAX v2\n\n"
        f"DEMO автоторговля: {'включена' if autotrade_enabled else 'выключена'}\n"
        f"Автоанализ: {'включён' if monitor_enabled else 'выключен'}\n"
        f"Режим: {'DEMO' if is_demo() else 'LIVE'}\n"
        f"Пара: {TRADE_SYMBOL}\n"
        f"Сумма сделки: {risk_settings['amount_usdt']} USDT\n"
        f"Позиция: {position_text}"
    )


async def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Не найден TELEGRAM_BOT_TOKEN")

    print("OKX Crypto Trading Bot PRO MAX v2 started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
