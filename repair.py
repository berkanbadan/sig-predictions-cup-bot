from bot import place_order, best_bid_level, get_positions

# (Beschreibung, Exchange, Menge, Höchstpreis für NO)
# Höchstpreis = 1 minus der Preis, den du für die andere Hälfte bezahlt hast.
REPAIRS = [
    ("Oklahoma Senate: NO Republikaner", "975", 10, 0.110),
    ("Maine Senate: NO Demokraten", "959", 10, 0.445),
    ("Idaho Governor: NO Demokraten", "859", 10, 0.915),
]

for name, exchange_id, qty, max_price in REPAIRS:
    bid, depth = best_bid_level(exchange_id)
    if bid is None:
        print(f"{name}: kein Gebot im Buch, übersprungen.")
        continue

    no_price = round(1 - bid, 3)
    print(f"{name}: NO kostet {no_price:.3f}, Höchstpreis {max_price:.3f}")

    if no_price > max_price:
        print("  Zu teuer, übersprungen.")
        continue

    answer = input("  Kaufen? (ja/nein): ")
    if answer.strip().lower() != "ja":
        print("  Übersprungen.")
        continue

    result = place_order(exchange_id, "no", "buy", qty, no_price)
    print(f"  Ausgeführt: {result.get('quantityTraded')}")

print("\nNO-Shares nach der Reparatur (alle sollten 20 sein):")
positions = get_positions()
for exchange_id in ["1072", "975", "960", "959", "860", "859"]:
    print(f"  Exchange {exchange_id}: {-positions.get(exchange_id, 0):.0f}")