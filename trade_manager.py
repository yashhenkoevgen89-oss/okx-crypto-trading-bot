def is_demo(okx_flag):
    return okx_flag == "1"


def place_demo_buy(trade_api, symbol, amount_usdt, okx_flag):
    if not is_demo(okx_flag):
        return {"error": "LIVE торговля заблокирована"}

    return trade_api.place_order(
        instId=symbol,
        tdMode="cash",
        side="buy",
        ordType="market",
        sz=str(amount_usdt),
        tgtCcy="quote_ccy"
    )


def place_demo_sell(trade_api, account_api, symbol, okx_flag):
    if not is_demo(okx_flag):
        return {"error": "LIVE торговля заблокирована"}

    base = symbol.split("-")[0]
    balance = account_api.get_account_balance(ccy=base)
    details = balance.get("data", [{}])[0].get("details", [])

    available = 0.0

    for item in details:
        if item.get("ccy") == base:
            available = float(item.get("availBal") or 0)

    if available <= 0:
        return {"error": f"Нет доступного баланса {base}"}

    return trade_api.place_order(
        instId=symbol,
        tdMode="cash",
        side="sell",
        ordType="market",
        sz=str(available)
    )
