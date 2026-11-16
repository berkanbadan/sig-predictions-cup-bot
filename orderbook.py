import os
from api import api_get

TOURNAMENT_ID = os.getenv("SIG_TOURNAMENT_ID")


def get_orderbook(exchange_id, depth=5):
    book = api_get(
        f"/exchanges/{exchange_id}/orderbook",
        params={"depth": depth, "tournamentId": TOURNAMENT_ID},
    )
    bids = sorted(book.get("bids", []), key=lambda lvl: float(lvl["price"]), reverse=True)
    asks = sorted(book.get("asks", []), key=lambda lvl: float(lvl["price"]))
    return bids, asks


def best_bid(bids):
    if len(bids) == 0:
        return None
    return float(bids[0]["price"])


def show_book(name, exchange_id):
    bids, asks = get_orderbook(exchange_id)

    print(f"=== {name} (Exchange {exchange_id}) ===")
    print("  Asks (Verkaufsangebote YES):")
    for lvl in reversed(asks):
        print(f'    {float(lvl["price"]):.3f}   {float(lvl["quantity"]):>10.0f}')
    print("  -------------------------")
    print("  Bids (Kaufgebote YES):")
    for lvl in bids:
        print(f'    {float(lvl["price"]):.3f}   {float(lvl["quantity"]):>10.0f}')
    print()

    return bids, asks


if __name__ == "__main__":
    rep_bids, rep_asks = show_book("Kansas Republikaner", "1046")
    dem_bids, dem_asks = show_book("Kansas Demokraten", "1045")

    b_rep = best_bid(rep_bids)
    b_dem = best_bid(dem_bids)

    if b_rep is None or b_dem is None:
        print("Mindestens ein Orderbuch hat keine Gebote.")
    else:
        summe = b_rep + b_dem
        print(f"Bestes YES-Gebot Republikaner: {b_rep:.3f}")
        print(f"Bestes YES-Gebot Demokraten:   {b_dem:.3f}")
        print(f"Summe:                         {summe:.3f}")

        if summe > 1:
            print(f"ARBITRAGE! Gewinn pro Set: {summe - 1:.3f}")
        else:
            print("Keine Arbitrage. Die Summe liegt nicht über 1.")