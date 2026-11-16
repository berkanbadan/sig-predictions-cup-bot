"""
bot.py: Automatischer Trading-Bot für den SIG Predictions Cup.

Strategie 1 (SWAP): Tauscht gehaltene YES-Shares einer Partei gegen NO-Shares
der Gegenpartei im selben Rennen, wenn das strikt besser ist. Braucht kein Cash.

Strategie 2 (ARB): Kauft mit freiem Cash NO auf beide Parteien eines Rennens,
wenn die Summe der besten YES-Gebote über 1 liegt.

Sicherheit: DRY_RUN, AUTO (Bestätigung), KILL-Datei, Limits, Log-Datei.
"""
import csv
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import requests

from api import api_get, api_post
from orderbook import get_orderbook
from prices import fetch_prices
from scanner import parse_title

TOURNAMENT_ID = os.getenv("SIG_TOURNAMENT_ID")
SLUG = os.getenv("SIG_TOURNAMENT_SLUG")

# ======================= EINSTELLUNGEN =======================
DRY_RUN = True           # True = nichts senden, nur anzeigen
AUTO = True              # False = vor jedem Trade nach "ja" fragen
CYCLE_SECONDS = 60        # Pause zwischen zwei Durchläufen
KILL_FILE = "KILL"        # existiert diese Datei, werden keine Orders gesendet

SWAP_ENABLED = True
SWAP_MIN_EDGE = 0.01      # Mindestgewinn pro Share beim Tausch
SWAP_MAX_ORDER = 3000     # max. Shares pro Tausch-Order

ARB_ENABLED = True       # erst aktivieren, wenn SWAP stabil läuft
ARB_MIN_EDGE = 0.015      # Mindestgewinn pro Set
ARB_MAX_SETS = 10         # max. Sets pro Arbitrage-Trade (klein anfangen!)
CASH_RESERVE = 5          # so viel Cash bleibt immer unangetastet

DEPTH_FRACTION = 0.8      # nur 80 % der angezeigten Menge nehmen
MAX_CANDIDATES = 8        # max. Rennen pro Strategie und Durchlauf
EXCLUDE_RACES = []        # z. B. ["Alaska Governor"] für Sonderfälle
LOG_FILE = "trades.csv"
# =============================================================

blocked_races = set()     # Rennen mit ungleicher Ausführung, nicht mehr anfassen


# ---------------------- Hilfsfunktionen ----------------------

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_trade(strategy, race, exchange_id, side, action, qty_req, qty_done, price):
    new_file = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["zeit", "strategie", "rennen", "exchange", "side", "action",
                        "menge_geplant", "menge_ausgefuehrt", "preis"])
        w.writerow([now_str(), strategy, race, exchange_id, side, action,
                    qty_req, qty_done, price])


def expires_in(seconds):
    t = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def place_order(exchange_id, side, action, quantity, price):
    body = {
        "idempotencyKey": str(uuid.uuid4()),
        "exchangeId": exchange_id,
        "side": side,
        "action": action,
        "quantity": int(quantity),
        "price": round(price, 3),
        "tournamentId": TOURNAMENT_ID,
        "expirationDate": expires_in(30),
    }
    print(f"    Sende: {action} {side} {int(quantity)} @ {price:.3f} auf Exchange {exchange_id}")
    return api_post("/orders", body)


def traded(result):
    return float(result.get("quantityTraded", 0) or 0)


def confirm():
    if AUTO:
        return True
    answer = input("    Ausführen? (ja/nein): ")
    return answer.strip().lower() == "ja"


def kill_switch_active():
    return os.path.exists(KILL_FILE)


# ------------------------ Daten laden ------------------------

def load_races():
    # Ergebnis: {"Kansas Senate": [{"party": ..., "exchange_id": ...}, {...}], ...}
    # Nur offene Rennen mit genau zwei Parteien.
    with open("markets.json") as f:
        markets = json.load(f)

    races = {}
    for m in markets:
        if m.get("status") != "open" or len(m["exchanges"]) == 0:
            continue
        parsed = parse_title(m["title"])
        if parsed is None:
            continue
        party, race = parsed
        races.setdefault(race, []).append({
            "party": party,
            "exchange_id": m["exchanges"][0]["id"],
        })

    return {r: legs for r, legs in races.items()
            if len(legs) == 2 and r not in EXCLUDE_RACES}


def get_cash():
    resp = api_get("/tournaments")
    for t in resp.get("data", []):
        if t.get("slug") == SLUG:
            return float(t.get("myBalance", 0))
    return 0.0


def get_positions():
    # YES-normiert: positiv = YES, negativ = NO
    resp = api_get(f"/tournaments/{SLUG}/portfolio/positions")
    positions = {}
    for p in resp.get("positions", []):
        positions[str(p.get("exchangeId"))] = float(p.get("quantity", 0))
    return positions


def best_bid_level(exchange_id):
    bids, asks = get_orderbook(exchange_id, depth=1)
    if len(bids) == 0:
        return None, 0
    return float(bids[0]["price"]), float(bids[0]["quantity"])


# ---------------------- Strategie: SWAP ----------------------

def try_swap(race, held, other, held_qty):
    b_held, q_held = best_bid_level(held["exchange_id"])
    b_other, q_other = best_bid_level(other["exchange_id"])
    if b_held is None or b_other is None:
        return

    edge = b_held + b_other - 1
    if edge < SWAP_MIN_EDGE:
        return

    qty = int(min(SWAP_MAX_ORDER, q_held * DEPTH_FRACTION, q_other * DEPTH_FRACTION, held_qty))
    if qty < 1:
        return

    print(f"  [SWAP] {race}: {qty} YES {held['party']} @ {b_held:.3f} verkaufen, "
          f"NO {other['party']} @ {1 - b_other:.3f} kaufen, Gewinn {edge:.3f}/Share")

    if DRY_RUN:
        print("    DRY_RUN: nichts gesendet.")
        return
    if kill_switch_active():
        print("    KILL-Datei vorhanden: nichts gesendet.")
        return
    if not confirm():
        print("    Übersprungen.")
        return

    res_a = place_order(held["exchange_id"], "yes", "sell", qty, b_held)
    sold = int(traded(res_a))
    log_trade("SWAP", race, held["exchange_id"], "yes", "sell", qty, sold, b_held)
    print(f"    Verkauft: {sold}")
    if sold < 1:
        return

    b_now, q_now = best_bid_level(other["exchange_id"])
    if b_now is None or b_held + b_now - 1 < 0:
        print("    Lücke inzwischen weg. Du hältst jetzt Cash statt YES.")
        return

    res_b = place_order(other["exchange_id"], "no", "buy", sold, 1 - b_now)
    bought = int(traded(res_b))
    log_trade("SWAP", race, other["exchange_id"], "no", "buy", sold, bought, round(1 - b_now, 3))
    print(f"    Gekauft: {bought} NO")
    if bought < sold:
        print(f"    WARNUNG: {sold - bought} Shares nur verkauft, nicht getauscht (Cash statt Position).")


# ---------------------- Strategie: ARB -----------------------

def try_arb(race, legs, cash):
    b1, q1 = best_bid_level(legs[0]["exchange_id"])
    b2, q2 = best_bid_level(legs[1]["exchange_id"])
    if b1 is None or b2 is None:
        return cash

    edge = b1 + b2 - 1
    if edge < ARB_MIN_EDGE:
        return cash

    cost_per_set = (1 - b1) + (1 - b2)
    budget = cash - CASH_RESERVE
    sets = int(min(ARB_MAX_SETS, q1 * DEPTH_FRACTION, q2 * DEPTH_FRACTION, budget / cost_per_set))
    if sets < 1:
        return cash

    print(f"  [ARB] {race}: {sets} Sets, NO {legs[0]['party']} @ {1 - b1:.3f} + "
          f"NO {legs[1]['party']} @ {1 - b2:.3f}, Gewinn {edge:.3f}/Set")

    if DRY_RUN:
        print("    DRY_RUN: nichts gesendet.")
        return cash
    if kill_switch_active():
        print("    KILL-Datei vorhanden: nichts gesendet.")
        return cash
    if not confirm():
        print("    Übersprungen.")
        return cash

    # Bein 1: NO auf die erste Partei kaufen
    res1 = place_order(legs[0]["exchange_id"], "no", "buy", sets, 1 - b1)
    f1 = int(traded(res1))
    log_trade("ARB", race, legs[0]["exchange_id"], "no", "buy", sets, f1, round(1 - b1, 3))
    if f1 < 1:
        print("    Bein 1 wurde nicht ausgeführt. Stopp.")
        return cash

    # Bein 2: Preis erneut prüfen, dann genau so viele NO auf die zweite Partei
    b2_now, q2_now = best_bid_level(legs[1]["exchange_id"])
    if b2_now is None or (1 - b1) + (1 - b2_now) > 1:
        print("    Lücke inzwischen weg! Bein 2 nicht gekauft.")
        print("    Rennen gesperrt. Bitte die einzelne Position manuell prüfen.")
        blocked_races.add(race)
        return cash - (1 - b1) * f1

    res2 = place_order(legs[1]["exchange_id"], "no", "buy", f1, 1 - b2_now)
    f2 = int(traded(res2))
    log_trade("ARB", race, legs[1]["exchange_id"], "no", "buy", f1, f2, round(1 - b2_now, 3))
    print(f"    Ausgeführt: {f1} und {f2}")

    # Kontrolle über das Portfolio (die API-Antwort allein reicht nicht)
    time.sleep(2)
    positions = get_positions()
    p1 = int(-positions.get(legs[0]["exchange_id"], 0))
    p2 = int(-positions.get(legs[1]["exchange_id"], 0))
    print(f"    Laut Portfolio: {p1} und {p2} NO-Shares")

    if f1 != f2 or p1 != p2:
        print("    WARNUNG: Ungleiche Positionen! Rennen wird gesperrt. Bitte im Portfolio prüfen.")
        blocked_races.add(race)

    return cash - (1 - b1) * f1 - (1 - b2_now) * f2


# ----------------------- Hauptschleife -----------------------

def run_cycle(races):
    positions = get_positions()
    cash = get_cash()

    all_ids = [leg["exchange_id"] for legs in races.values() for leg in legs]
    prices = {p["exchangeId"]: p for p in fetch_prices(all_ids)}

    print(f"\n[{now_str()}] Cash: {cash:.2f} SUSQies | {len(races)} Rennen | "
          f"gesperrt: {len(blocked_races)}")

    candidates = []
    for race, legs in races.items():
        if race in blocked_races:
            continue
        bids = [prices.get(leg["exchange_id"], {}).get("bestBid") for leg in legs]
        if None in bids:
            continue
        edge = bids[0] + bids[1] - 1
        held = [positions.get(leg["exchange_id"], 0) for leg in legs]
        candidates.append((edge, race, legs, held))
    candidates.sort(key=lambda c: c[0], reverse=True)

    # 1) SWAP: Rennen, in denen du eine YES-Position hältst
    if SWAP_ENABLED:
        checked = 0
        for edge, race, legs, held in candidates:
            if checked >= MAX_CANDIDATES or edge < SWAP_MIN_EDGE:
                break
            for i in (0, 1):
                if held[i] > 0:
                    try_swap(race, legs[i], legs[1 - i], held[i])
                    checked += 1

    # 2) ARB: Rennen ohne eigene Position, mit freiem Cash
    if ARB_ENABLED:
        checked = 0
        for edge, race, legs, held in candidates:
            if checked >= MAX_CANDIDATES or edge < ARB_MIN_EDGE:
                break
            if any(h != 0 for h in held):
                continue
            if cash - CASH_RESERVE < 1:
                break
            cash = try_arb(race, legs, cash)
            checked += 1


def main():
    races = load_races()
    print("SIG-Bot gestartet")
    print(f"  DRY_RUN={DRY_RUN}  AUTO={AUTO}  SWAP={SWAP_ENABLED}  ARB={ARB_ENABLED}")
    print(f"  {len(races)} Rennen mit genau zwei Parteien geladen")
    print("  Stoppen mit Ctrl+C. Notbremse: Datei KILL anlegen.")

    while True:
        try:
            run_cycle(races)
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
        print("\nBot gestoppt.")