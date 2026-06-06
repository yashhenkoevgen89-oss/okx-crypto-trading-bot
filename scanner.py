from config import WATCHLIST


def calculate_score(data):
    score = 50

    if data["ema9"] > data["ema21"]:
        score += 15

    if data["rsi"] < 30:
        score += 15
    elif data["rsi"] > 70:
        score -= 15

    if data["macd"] > data["macd_signal"]:
        score += 10
    else:
        score -= 10

    if data["close"] <= data["bb_lower"]:
        score += 10
    elif data["close"] >= data["bb_upper"]:
        score -= 10

    if data["vol"] > data["vol_avg"]:
        score += 5

    score = max(0, min(100, int(score)))

    if score >= 65:
        signal = "BUY"
    elif score <= 35:
        signal = "SELL"
    else:
        signal = "HOLD"

    return score, signal


def sort_signals(signals):
    return sorted(
        signals,
        key=lambda x: x["score"],
        reverse=True
    )


def get_best_coin(signals):
    signals = sort_signals(signals)

    if len(signals) == 0:
        return None

    return signals[0]
