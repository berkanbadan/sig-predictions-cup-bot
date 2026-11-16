"""
rebalance.py: Baut Kansas schrittweise ab und kauft mit dem Erlös Favoriten,
die auf SIG günstiger sind als auf Kalshi. Sicherheit: DRY_RUN, AUTO,
KILL-Datei, Limits pro Order und pro Rennen, Log in trades.csv.
"""
import json
import time

import requests

import kalshi
from bot import place_order, traded, log_trade, get_positions, get_cash, kill_switch_active
from orderbook import get_orderbook
from prices import fetch_prices
from scanner import parse_title

# ======================= EINSTELLUNGEN =======================
DRY_RUN = True            # True = nichts senden, nur anzeigen
AUTO = True              # False = vor jeder Order nach "ja" fragen
CYCLE_SECONDS = 120       # Pause zwischen zwei Durchläufen
KALSHI_REFRESH = 10 * 60  # Kalshi-Daten alle 10 Minuten neu laden

# Verkauf
SELL_RACE = "Kansas Senate"
SELL_TOLERANCE = 0.02     # höchstens 2 Cent unter Kalshi-fair verkaufen
SELL_CHUNK = 5000         # max. Shares pro Verkaufsorder

# Kauf
BUY_MIN_PRICE = 0.80      # nur starke Favoriten
BUY_MAX_PRICE = 0.96
BUY_MIN_EDGE = 0.04       # Kalshi-fair muss mind. 4 Cent über SIG-Preis liegen
MAX_KALSHI_SPREAD = 0.03  # nur Rennen, bei denen Kalshi eng quotiert
MAX_PAYOUT_PER_RACE = 10000   # max. Auszahlung pro Rennen (Klumpenrisiko)
BUY_CHUNK_COST = 3000     # max. SUSQies pro Kauforder
CASH_RESERVE = 200        # so viel Cash bleibt immer liegen
MAX_BUY_PER_CYCLE = 8     # maximal 8 Käufe pro Durchlauf

DEPTH_FRACTION = 0.8
EXCLUDE = ["Kansas Senate"]   # hier wird nie gekauft
# =============================================================


def confirm():
    if AUTO:
        return True
    return input("    Ausführen? (ja/nein): ").strip().lower() == "ja"


def execute(label, exchange_id, side, action, qty, price):
    qty = int(qty)
    if qty < 1:
        print(f"  [{label}] {action} {side} auf Exchange {exchange_id}: "
              f"Menge im Orderbuch zu klein, übersprungen.")
        return 0
    print(f"  [{label}] {action} {side} {qty} @ {price:.3f} auf Exchange {exchange_id}")
    if DRY_RUN:
        print("    DRY_RUN: nichts gesendet.")
        return 0
    if kill_switch_active():
        print("    KILL-Datei vorhanden: nichts gesendet.")
        return 0
    if not confirm():
        print("    Übersprungen.")
        return 0
    res = place_order(exchange_id, side, action, qty, price)
    done = int(traded(res))
    fill = float(res.get("fillPrice") or price)
    log_trade("REBAL", label, exchange_id, side, action, qty, done, fill)
    print(f"    Ausgeführt: {done} zu Ø {fill:.4f}")
    return done


def load_sig():
    # {race_key: {"name": ..., "parties": {party: exchange_id}}} und Quotes je Exchange
    with open("markets.json") as f:
        markets = json.load(f)
    races, ids = {}, []
    for m in markets:
        if m.get("status") != "open" or len(m["exchanges"]) == 0:
            continue
        parsed = parse_title(m["title"])
        if parsed is None:
            continue
        party, race = parsed
        ex_id = m["exchanges"][0]["id"]
        races.setdefault(kalshi.race_key(race), {"name": race, "parties": {}})["parties"][party] = ex_id
        ids.append(ex_id)
    quotes = {p["exchangeId"]: (p.get("bestBid"), p.get("bestAsk")) for p in fetch_prices(ids)}
    return races, quotes


def fair_probs(k):
    # Kalshi-Mitten beider Parteien, auf Summe 1 normiert
    if len(k) != 2:
        return None
    mids = {p: kalshi.mid(*q) for p, q in k.items()}
    if None in mids.values():
        return None
    total = sum(mids.values())
    return {p: v / total for p, v in mids.items()}


def best(levels):
    if not levels:
        return None, 0
    return float(levels[0]["price"]), float(levels[0]["quantity"])


# ------------------------- Verkauf Kansas -------------------------

def sell_step(races, kal, positions):
    key = kalshi.race_key(SELL_RACE)
    r, k = races.get(key), kal.get(key)
    if r is None or k is None:
        print(f"  {SELL_RACE} nicht gefunden.")
        return
    fair = fair_probs(k)
    if fair is None:
        print(f"  Kein Kalshi-Preis für {SELL_RACE}.")
        return

    for party, ex_id in r["parties"].items():
        pos = positions.get(ex_id, 0)
        bids, asks = get_orderbook(ex_id, depth=1)

        if pos > 0:  # YES-Shares halten -> YES verkaufen
            bid, qty = best(bids)
            if bid is None:
                continue
            floor = fair[party] - SELL_TOLERANCE
            if bid > fair[party] + 0.15:
                print(f"  {SELL_RACE} YES {party}: Gebot {bid:.3f} unplausibel hoch, übersprungen.")
                continue
            if bid < floor:
                print(f"  {SELL_RACE} YES {party}: Gebot {bid:.3f} unter Untergrenze {floor:.3f}, warte.")
                continue
            n = min(SELL_CHUNK, pos, qty * DEPTH_FRACTION)
            execute("VERKAUF", ex_id, "yes", "sell", n, bid)

        elif pos < 0:  # NO-Shares halten -> NO verkaufen
            yes_ask, qty = best(asks)
            if yes_ask is None:
                continue
            no_bid = 1 - yes_ask
            floor = (1 - fair[party]) - SELL_TOLERANCE
            if no_bid > (1 - fair[party]) + 0.15:
                print(f"  {SELL_RACE} NO {party}: Gebot {no_bid:.3f} unplausibel hoch, übersprungen.")
                continue
            if no_bid < floor:
                print(f"  {SELL_RACE} NO {party}: Gebot {no_bid:.3f} unter Untergrenze {floor:.3f}, warte.")
                continue
            n = min(SELL_CHUNK, -pos, qty * DEPTH_FRACTION)
            execute("VERKAUF", ex_id, "no", "sell", n, no_bid)


# ------------------------- Kauf Favoriten -------------------------

def buy_step(races, quotes, kal, positions, cash):
    candidates = []
    for key, r in races.items():
        if r["name"] in EXCLUDE or len(r["parties"]) != 2 or key not in kal:
            continue
        k = kal[key]
        fair = fair_probs(k)
        if fair is None:
            continue
        parties = list(r["parties"].keys())
        fav = max(parties, key=lambda p: fair[p])
        dog = parties[0] if parties[1] == fav else parties[1]
        kb, ka = k[fav]
        if kb is None or ka is None or ka - kb > MAX_KALSHI_SPREAD:
            continue

        fav_ex, dog_ex = r["parties"][fav], r["parties"][dog]
        options = []
        fav_bid, fav_ask = quotes.get(fav_ex, (None, None))
        dog_bid, dog_ask = quotes.get(dog_ex, (None, None))
        if fav_ask is not None:
            options.append((fav_ask, "yes", fav_ex))
        if dog_bid is not None:
            options.append((1 - dog_bid, "no", dog_ex))
        if not options:
            continue
        price, side, ex_id = min(options)
        edge = fair[fav] - price
        if not (BUY_MIN_PRICE <= price <= BUY_MAX_PRICE and edge >= BUY_MIN_EDGE):
            continue

        payout_now = max(positions.get(fav_ex, 0), 0) + max(-positions.get(dog_ex, 0), 0)
        room = MAX_PAYOUT_PER_RACE - payout_now
        if room >= 1:
            candidates.append((edge, r["name"], fav, side, ex_id, fair[fav], room))

    candidates.sort(reverse=True)
    if not candidates:
        print("  Keine Kaufkandidaten mit genug Edge.")
    for edge, name, fav, side, ex_id, fair_p, room in candidates:
        budget = min(BUY_CHUNK_COST, cash - CASH_RESERVE)
        if budget < 10:
            print("  Cash aufgebraucht.")
            break

        bids, asks = get_orderbook(ex_id, depth=1)
        if side == "yes":
            price, qty = best(asks)
        else:
            bid, qty = best(bids)
            price = None if bid is None else 1 - bid
        if price is None or not (BUY_MIN_PRICE <= price <= BUY_MAX_PRICE):
            continue
        if fair_p - price < BUY_MIN_EDGE:
            continue
        if fair_p - price > 0.25:
            print(f"  {name}: Edge von {fair_p - price:.3f} ist unplausibel groß, übersprungen.")
            continue

        n = min(room, qty * DEPTH_FRACTION, budget / price)
        print(f"  {name}: Favorit {fav}, Kalshi fair {fair_p:.3f}, Edge {fair_p - price:+.3f}")
        done = execute("KAUF", ex_id, side, "buy", n, price)
        cash -= done * price


# --------------------------- Hauptschleife ---------------------------

def run_cycle():
    kalshi.CACHE_MAX_AGE = KALSHI_REFRESH
    kal = kalshi.build_kalshi(kalshi.fetch_kalshi_events())

    races, quotes = load_sig()
    positions = get_positions()
    print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Cash vorher: {get_cash():.2f}")

    sell_step(races, kal, positions)

    positions = get_positions()
    cash = get_cash()
    print(f"  Cash nach Verkauf: {cash:.2f}")
    buy_step(races, quotes, kal, positions, cash)


def main():
    print("Rebalance gestartet")
    print(f"  DRY_RUN={DRY_RUN}  AUTO={AUTO}  Verkauf: {SELL_RACE}")
    print("  Stoppen mit Ctrl+C. Notbremse: Datei KILL anlegen.")
    while True:
        try:
            run_cycle()
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response is not None else None
            if code == 429:
                print("Rate-Limit erreicht, warte 60 Sekunden.")
                time.sleep(60)
            else:
                print(f"HTTP-Fehler: {e}")
        except requests.exceptions.RequestException as e:
            print(f"Netzwerkfehler: {e}")
        print(f"Warte {CYCLE_SECONDS} Sekunden ...")
        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nRebalance gestoppt.")