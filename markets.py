import json
import os
from api import api_get

SLUG = os.getenv("SIG_TOURNAMENT_SLUG")


def fetch_all_markets():
    all_markets = []
    params = {"limit": 100}

    while True:
        response = api_get(f"/tournaments/{SLUG}/markets", params=params)
        page = response["data"]

        if len(page) == 0:
            break

        all_markets.extend(page)

        pagination = response.get("pagination") or {}
        if not pagination.get("hasMore"):
            break

        if pagination.get("nextCursor"):
            params["cursor"] = pagination["nextCursor"]
        else:
            params["offset"] = len(all_markets)

    return all_markets


markets = fetch_all_markets()
print(f"Insgesamt {len(markets)} Märkte geladen\n")

for m in markets:
    if len(m["exchanges"]) == 0:
        continue

    ex = m["exchanges"][0]
    price = ex.get("latestPrice")
    price_text = f"{price:.3f}" if price is not None else "  -  "

    print(f'{ex["id"]:>6}  {price_text}  {m["title"]}')

with open("markets.json", "w") as f:
    json.dump(markets, f, indent=2)

print("\nGespeichert in markets.json")