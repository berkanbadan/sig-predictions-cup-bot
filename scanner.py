import json
from prices import fetch_prices
from orderbook import get_orderbook


def parse_title(title):
    # "Will the Democratic Party win the Kansas Senate?"
    # wird zu ("Democratic Party", "Kansas Senate")
    if not title.startswith("Will the ") or " win the " not in title:
        return None
    rest = title[len("Will the "):]
    party, race = rest.split(" win the ", 1)
    return party, race.rstrip("?")


def load_legs():
    with open("markets.json") as f:
        markets = json.load(f)

    legs = {}
    skipped = 0
    for m in markets:
        parsed = parse_title(m["title"])
        if parsed is None or len(m["exchanges"]) == 0:
            skipped += 1
            continue
        party, race = parsed
        legs[m["exchanges"][0]["id"]] = (race, party)
    return legs, skipped


def group_by_race(legs, prices):
    races = {}
    for p in prices:
        ex_id = p["exchangeId"]
        if ex_id not in legs:
            continue
        race, party = legs[ex_id]
        if race not in races:
            races[race] = []
        races[race].append({
            "exchange_id": ex_id,
            "party": party,
            "bid": p.get("bestBid"),
            "ask": p.get("bestAsk"),
        })
    return races


def depth_at_best_bid(exchange_id):
    bids, asks = get_orderbook(exchange_id, depth=1)
    if len(bids) == 0:
        return 0
    return float(bids[0]["quantity"])


if __name__ == "__main__":
    legs, skipped = load_legs()
    prices = fetch_prices(list(legs.keys()))
    races = group_by_race(legs, prices)

    results = []
    for race, entries in races.items():
        if len(entries) < 2:
            continue
        if any(e["bid"] is None for e in entries):
            continue
        bid_sum = sum(e["bid"] for e in entries)
        results.append((bid_sum, race, entries))

    results.sort(key=lambda r: r[0], reverse=True)
    arbs = [r for r in results if r[0] > 1.0001]

    print(f"{len(results)} Rennen geprüft ({skipped} Märkte übersprungen)")
    print(f"{len(arbs)} Arbitrage-Gelegenheiten gefunden\n")

    for bid_sum, race, entries in arbs[:10]:
        cost = sum(1 - e["bid"] for e in entries)
        profit = bid_sum - 1
        sets = min(depth_at_best_bid(e["exchange_id"]) for e in entries)

        print(race)
        for e in entries:
            print(f'    {e["party"]:<20} YES-Bid {e["bid"]:.3f}  ->  NO für {1 - e["bid"]:.3f}')
        print(f"    Kosten pro Set {cost:.3f}, Gewinn pro Set {profit:.3f} ({profit / cost:.1%})")
        print(f"    Menge auf bester Stufe: {sets:.0f} Sets, Gewinn ca. {sets * profit:.0f} SUSQies\n")

    print("Knapp daneben (Summe der Bids zwischen 0.98 und 1):")
    for bid_sum, race, entries in results:
        if 0.98 <= bid_sum <= 1.0001:
            print(f"    {bid_sum:.3f}  {race}")