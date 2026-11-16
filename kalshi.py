"""
kalshi.py: Vergleicht die SIG-Preise mit Kalshi für Senats-, Gouverneurs-
und House-Rennen. Liest nur Daten, sendet keine Orders.
"""
import csv
import json
import os
import re
import time

import requests

from prices import fetch_prices
from scanner import parse_title

KALSHI_URL = "https://api.elections.kalshi.com/trade-api/v2"
CACHE_FILE = "kalshi_events.json"
CACHE_MAX_AGE = 30 * 60          # Kalshi-Daten 30 Minuten wiederverwenden
FAV_MIN, FAV_MAX = 0.70, 0.85    # Preisbereich für Favoriten-Kandidaten
OUT_FILE = "kalshi_vergleich.csv"


# ------------------------- Hilfsfunktionen -------------------------

def normalize(text):
    text = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    return " ".join(text.split())


def race_key(text):
    # "Kansas Senate winner?" und "Kansas Senate" -> "kansas senate"
    # "NY-17 House race" und "NY-17 House winner?" -> "ny 17 house"
    words = [w for w in normalize(text).split() if w not in ("winner", "race")]
    return " ".join(words)


def fmt(x):
    return f"{x:.3f}" if x is not None else "  -  "


def mid(bid, ask):
    if bid is not None and ask is not None:
        return (bid + ask) / 2
    return bid if bid is not None else ask


# ------------------------------ Kalshi ------------------------------

def fetch_kalshi_events():
    if os.path.exists(CACHE_FILE) and time.time() - os.path.getmtime(CACHE_FILE) < CACHE_MAX_AGE:
        print("Kalshi-Daten aus dem Cache geladen (jünger als 30 Minuten).")
        with open(CACHE_FILE) as f:
            return json.load(f)

    print("Lade Kalshi-Events, das kann ein bis zwei Minuten dauern ...")
    events = []
    cursor = None
    for page in range(300):
        params = {"status": "open", "limit": 200, "with_nested_markets": "true"}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(f"{KALSHI_URL}/events", params=params, timeout=20)
        if resp.status_code == 429:
            print("  Kalshi-Rate-Limit, warte 5 Sekunden ...")
            time.sleep(5)
            continue
        resp.raise_for_status()
        data = resp.json()
        for e in data.get("events", []):
            title = e.get("title", "").lower()
            if "senate" in title or "governor" in title or "house" in title:
                events.append(e)
        cursor = data.get("cursor")
        if page % 10 == 0:
            print(f"  Seite {page + 1}, bisher {len(events)} passende Events")
        if not cursor:
            break
        time.sleep(0.2)

    with open(CACHE_FILE, "w") as f:
        json.dump(events, f)
    print(f"{len(events)} Wahl-Events von Kalshi gespeichert.")
    return events


def kalshi_party(market):
    sub = str(market.get("yes_sub_title") or market.get("subtitle") or "").lower()
    title = str(market.get("title") or "").lower()
    ticker = str(market.get("ticker") or "").upper()
    for text in (sub, title):
        if "republican" in text:
            return "Republican Party"
        if "democrat" in text:
            return "Democratic Party"
    if ticker.endswith("-R") or ticker.endswith("-REP"):
        return "Republican Party"
    if ticker.endswith("-D") or ticker.endswith("-DEM"):
        return "Democratic Party"
    return None


def kalshi_quote(market):
    def price(name):
        v = market.get(name + "_dollars")
        if v not in (None, ""):
            v = float(v)
        else:
            v = market.get(name)
            if v is None:
                return None
            v = float(v) / 100
        return v if 0 < v < 1 else None

    return price("yes_bid"), price("yes_ask")


def build_kalshi(events):
    result = {}
    for e in events:
        key = race_key(e.get("title", ""))
        parties = {}
        for m in e.get("markets") or []:
            party = kalshi_party(m)
            if party:
                parties[party] = kalshi_quote(m)
        if parties:
            result[key] = parties
    return result


# -------------------------------- SIG --------------------------------

def build_sig():
    with open("markets.json") as f:
        markets = json.load(f)

    races = {}
    ids = []
    for m in markets:
        if m.get("status") != "open" or len(m["exchanges"]) == 0:
            continue
        parsed = parse_title(m["title"])
        if parsed is None:
            continue
        party, race = parsed
        ex_id = m["exchanges"][0]["id"]
        entry = races.setdefault(race_key(race), {"name": race, "parties": {}})
        entry["parties"][party] = ex_id
        ids.append(ex_id)

    prices = {p["exchangeId"]: p for p in fetch_prices(ids)}
    for r in races.values():
        for party, ex_id in list(r["parties"].items()):
            p = prices.get(ex_id, {})
            r["parties"][party] = (p.get("bestBid"), p.get("bestAsk"))
    return races


# ------------------------------- Main -------------------------------

if __name__ == "__main__":
    sig = build_sig()
    kalshi = build_kalshi(fetch_kalshi_events())

    rows = []
    unmatched = []
    for key, r in sig.items():
        k = kalshi.get(key)
        if k is None:
            unmatched.append(r["name"])
            continue
        for party, (s_bid, s_ask) in r["parties"].items():
            if party not in k:
                continue
            k_bid, k_ask = k[party]
            s_mid, k_mid = mid(s_bid, s_ask), mid(k_bid, k_ask)
            diff = s_mid - k_mid if s_mid is not None and k_mid is not None else None
            signal = ""
            if s_ask is not None and k_bid is not None and s_ask < k_bid:
                signal = f"YES auf SIG billiger um {k_bid - s_ask:.3f}"
            elif s_bid is not None and k_ask is not None and s_bid > k_ask:
                signal = f"NO auf SIG billiger um {s_bid - k_ask:.3f}"
            rows.append({
                "rennen": r["name"], "partei": party,
                "sig_bid": s_bid, "sig_ask": s_ask,
                "kalshi_bid": k_bid, "kalshi_ask": k_ask,
                "abweichung": diff, "signal": signal,
            })

    print(f"\n{len(sig)} SIG-Rennen, davon {len(sig) - len(unmatched)} bei Kalshi gefunden.")
    if unmatched:
        print("Nicht gefunden: " + ", ".join(sorted(unmatched)[:40]))

    # 1) Größte Abweichungen
    rows.sort(key=lambda x: abs(x["abweichung"] or 0), reverse=True)
    print("\n=== GRÖSSTE ABWEICHUNGEN (SIG-Mitte minus Kalshi-Mitte) ===")
    print(f"{'Rennen':<26}{'Partei':<11}{'SIG Bid/Ask':>14}{'Kalshi Bid/Ask':>17}{'Diff':>8}  Signal")
    for x in rows[:30]:
        party_short = "Rep" if x["partei"].startswith("Rep") else "Dem"
        diff_txt = f"{x['abweichung']:+.3f}" if x["abweichung"] is not None else "  -  "
        print(f"{x['rennen'][:25]:<26}{party_short:<11}"
              f"{fmt(x['sig_bid']):>7}/{fmt(x['sig_ask']):<6}"
              f"{fmt(x['kalshi_bid']):>9}/{fmt(x['kalshi_ask']):<7}"
              f"{diff_txt:>8}  {x['signal']}")

    # 2) Favoriten-Kandidaten
    favs = []
    for key, r in sig.items():
        k = kalshi.get(key)
        if not k or len(r["parties"]) != 2:
            continue
        parties = list(r["parties"].keys())
        mids = {p: mid(*k[p]) for p in parties if p in k}
        if len(mids) != 2 or None in mids.values():
            continue
        total = sum(mids.values())
        fair = {p: mids[p] / total for p in parties}
        fav = max(parties, key=lambda p: fair[p])
        dog = parties[0] if parties[1] == fav else parties[1]

        options = []
        s_bid_f, s_ask_f = r["parties"][fav]
        s_bid_d, s_ask_d = r["parties"][dog]
        if s_ask_f is not None:
            options.append((s_ask_f, f"YES {fav.split()[0]}"))
        if s_bid_d is not None:
            options.append((1 - s_bid_d, f"NO {dog.split()[0]}"))
        if not options:
            continue
        price, how = min(options)
        if FAV_MIN <= price <= FAV_MAX:
            favs.append((fair[fav] - price, r["name"], how, price, fair[fav]))

    favs.sort(reverse=True)
    print(f"\n=== FAVORITEN ZWISCHEN {FAV_MIN:.2f} UND {FAV_MAX:.2f}, SORTIERT NACH EDGE ===")
    print(f"{'Rennen':<26}{'Kauf auf SIG':<18}{'SIG-Preis':>10}{'Kalshi fair':>13}{'Edge':>8}")
    for edge, name, how, price, fair_p in favs:
        print(f"{name[:25]:<26}{how:<18}{price:>10.3f}{fair_p:>13.3f}{edge:>+8.3f}")

    with open(OUT_FILE, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["rennen"])
        w.writeheader()
        w.writerows(rows)
    print(f"\nVollständige Tabelle gespeichert in {OUT_FILE}")