# SIG Predictions Cup Bot

Automated trading bot for the **SIG Predictions Cup**, a prediction-market
competition run by Susquehanna (SIG) on US 2026 election races (Senate,
Governor and House). Trading happens in SUSQies, the competition's virtual
currency. The bot scans the order books for mispricings between the two
parties of a race and uses Kalshi prices as an external fair-value reference.

**Result:** about 3,000 SUSQies profit from 519 trades in 57 races
(4 to 7 October 2026), see [`trades.csv`](trades.csv).

## Market structure

Every race has one YES contract per party. A contract pays 1 if that party
wins and 0 otherwise. In a two-party race exactly one party wins, so

* the fair YES prices of both parties sum to 1,
* NO on party B pays exactly the same as YES on party A.

Both strategies below exploit order books where this relation is violated.

## Strategies

### ARB: two-sided NO arbitrage (`bot.py`)

If the best YES bids of both parties satisfy $b_A + b_B > 1$, buy NO on both
parties. Buying NO is equivalent to selling YES, so it costs $1 - b$ per share:

$$\text{cost} = (1 - b_A) + (1 - b_B) = 2 - (b_A + b_B) < 1 .$$

Exactly one NO contract pays out, so every set returns 1 regardless of the
outcome. The profit per set is $b_A + b_B - 1$, locked in at execution.

### SWAP: position upgrade without cash (`bot.py`)

When holding YES on party A and $b_A + b_B - 1$ exceeds a threshold, sell YES
on A at $b_A$ and buy NO on B at $1 - b_B$. The payoff profile is unchanged
(both pay 1 if A wins) but the swap pockets $b_A + b_B - 1$ per share. This
needs no free cash and was the main source of trades.

### REBAL: Kalshi as fair value (`rebalance.py`, `kalshi.py`)

`kalshi.py` loads open Kalshi election markets through Kalshi's public API
(read-only, no orders, no account needed), matches them to the SIG races and
normalises the Kalshi mid prices to fair probabilities. `rebalance.py` uses
these to

* reduce a large position (Kansas Senate) only at prices no more than 2 cents
  below the Kalshi fair value,
* buy strong favourites (price 0.80 to 0.96) that trade on SIG at least
  4 cents below the Kalshi fair value, capped per race to limit concentration.

| Strategy | Trades |
|---|---:|
| SWAP | 353 |
| ARB | 102 |
| REBAL | 64 |

## Risk controls

* **Leg risk:** an arbitrage has two legs. Before the second leg the price is
  checked again. If the gap has closed or the fills differ, the race is
  blocked and the position is checked against the portfolio endpoint rather
  than trusting the order response alone.
* **Size:** orders take at most 80 % of the displayed depth, with limits per
  order, per cycle and per race, plus a cash reserve.
* **Switches:** `DRY_RUN` (print only), `AUTO` (ask before each order) and a
  kill switch: if a file named `KILL` exists, no orders are sent.
* **Plausibility checks:** implausibly large edges are skipped, since they
  usually indicate a matching or data error rather than an opportunity.
* **Logging:** every order is written to `trades.csv`; HTTP 429 rate limits
  trigger a pause.

## Lessons learned

* Two-leg trades on an exchange without atomic orders are not risk-free.
  Some legs filled unevenly; [`repair.py`](repair.py) was used once to
  rebalance those positions by hand.
* Matching races across platforms by title is fragile. Unmatched or
  ambiguous races are skipped and listed rather than guessed.
* Kalshi is a reference, not the truth. The edge is only as good as the
  liquidity and spread of the Kalshi market, hence the spread filter.

## Files

| File | Purpose |
|---|---|
| `api.py` | authenticated GET and POST requests to the SIG API |
| `markets.py` | downloads all markets of the tournament to `markets.json` |
| `prices.py`, `orderbook.py` | best bid/ask and order book depth |
| `scanner.py` | read-only scan for ARB opportunities |
| `bot.py` | main loop with SWAP and ARB |
| `kalshi.py` | read-only SIG vs. Kalshi comparison, writes `kalshi_vergleich.csv` |
| `rebalance.py` | REBAL strategy |
| `repair.py` | one-off fix for unevenly filled legs |
| `tournaments.py`, `test_setup.py` | API and setup checks |
| `trades.csv` | log of all executed orders |

Code comments and console output are in German.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill in your own API key
python test_setup.py
python markets.py             # creates markets.json
python scanner.py             # read-only
python kalshi.py              # read-only
```

`bot.py` and `rebalance.py` send real orders when `DRY_RUN = False`.
