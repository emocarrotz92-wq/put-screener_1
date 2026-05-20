# Put Screener

A desktop trading screener built around your put-selling strategy.
Screens stocks for: position in 52wk range ≤40% · IVx rank ≥35 · liquid options · not recently squeezed.

## Requirements

- Python 3.8 or higher
- Internet connection (for market data)

## Setup & Run

### Mac / Linux
```bash
chmod +x run.sh
./run.sh
```

### Windows
Double-click `run.bat`

Then open your browser to: **http://localhost:5050**

---

## What it does

**Login (optional)** — Sign in with your tastytrade credentials to unlock:
- Live account NLV, derivative buying power, deployment %
- Current open positions shown in the header bar
- Real IVx rank pulled directly from tastytrade (more accurate than estimates)

You can skip login and still run the screener — it'll use yfinance for all data.

**Market pulse** — Live S&P 500 price vs ATH, VIX, and a deployment recommendation:
- Conservative (≤30% deployed) when market is within 3% of ATH or VIX < 18
- Moderate (≤40%) when market is 3-10% off ATH
- Aggressive (≤50%) when market is 10%+ off ATH
- Hedge alert shown when Conservative regime is active

**Screener** — Select sectors, add custom tickers, click Run Screen.
Results sorted by position in range (lowest first). Passing trades shown in full, filtered trades dimmed.

**Columns:**
- Ticker — green dot = passes, red = squeezed/excluded, gray = filtered
- Price — current price
- In range — position in 52wk range (green ≤30%, amber ≤55%, red >55%)
- IVx — IV rank (green if ≥35)
- Earnings — next date, flagged with ⚠ if within your Jul 17 2026 target window
- Volume — avg daily volume
- Est BP — estimated buying power (20% × strike × 100 × 1.15)
- Thesis / flag — one-line put-selling thesis

---

## Customization

Edit `app.py` to:
- Add/remove tickers from the UNIVERSE dictionary
- Adjust screening thresholds (range_pct, iv_rank cutoffs)
- Change the target expiry date for earnings flagging

---

## Data sources
- **tastytrade API** — IVx rank, liquidity rating, account data (when logged in)
- **yfinance** — price, 52wk range, volume, earnings dates (always)

Data is fetched live each time you run a screen. Allow 1-2 minutes for a full scan.
