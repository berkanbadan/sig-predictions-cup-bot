import json
import os
from api import api_get

TOURNAMENT_ID = os.getenv("SIG_TOURNAMENT_ID")


def load_exchange_ids():
    with open("markets.json") as f:
        markets = json.load(f)

    ids = []
    for m in markets:
        for ex in m["exchanges"]:
            ids.append(ex["id"])
    return ids


def fetch_prices(exchange_ids):
    results = []
    for i in range(0, len(exchange_ids), 100):
        chunk = exchange_ids[i:i + 100]
        response = api_get(
            "/exchanges/prices",
            params={"ids": ",".join(chunk), "tournamentId": TOURNAMENT_ID},
        )
        results.extend(response["data"])
    return results


if __name__ == "__main__":
    ids = load_exchange_ids()
    print(f"{len(ids)} Exchanges gefunden")

    prices = fetch_prices(ids)
    print(f"{len(prices)} Preise erhalten\n")

    print("Beispiel-Eintrag:")
    print(json.dumps(prices[0], indent=2))