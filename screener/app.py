import os, math, time, requests, re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, date
from flask import Flask, render_template, request, jsonify, session
import yfinance as yf

# ── S&P 500 ticker fetch ──────────────────────────────────────────────────────

def fetch_sp500_tickers():
    """Fetch S&P 500 + known squeeze-prone tickers. Multiple sources with hardcoded fallback."""
    # Try Wikipedia first
    try:
        r = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        tickers = re.findall(r'<td><a[^>]*>([A-Z]{1,5})</a></td>', r.text)
        clean = list(dict.fromkeys([t for t in tickers if t and 1 < len(t) <= 5 and t.isalpha()]))
        if len(clean) >= 400:
            print(f"SP500 from Wikipedia: {len(clean)} tickers")
            return clean[:505]
    except Exception as e:
        print(f"SP500 Wikipedia error: {e}")

    # Hardcoded list — broad coverage of S&P 500 + known squeeze candidates
    print("SP500 using hardcoded list")
    return [
        # Mega cap
        "AAPL","MSFT","AMZN","NVDA","GOOGL","GOOG","META","TSLA","JPM","JNJ",
        "V","UNH","XOM","MA","PG","HD","CVX","MRK","ABBV","PEP","KO","AVGO",
        "COST","MCD","WMT","BAC","CRM","TMO","ABT","ACN","LIN","CSCO","NKE",
        "DHR","VZ","TXN","PM","ORCL","NEE","ADBE","BMY","WFC","MS","RTX",
        "QCOM","HON","UNP","LOW","CAT","GE","AMGN","SBUX","AXP","SPGI","GS",
        "BLK","ELV","DE","GILD","MDT","ADP","ISRG","BKNG","PLD","SYK","CB",
        "TJX","MMC","DUK","SO","MO","CL","IBN","INTC","AMD","PYPL","EBAY",
        # Financials
        "C","USB","PNC","TFC","KEY","RF","CFG","HBAN","FITB","MTB","ALLY",
        "SYF","DFS","COF","AIG","MET","PRU","AFL","ALL","PGR","TRV","HIG",
        # Healthcare
        "CVS","MCK","ABC","CAH","HCA","THC","CNC","MOH","HUM","CI",
        "TDOC","HIMS","NVAX","MRNA","BNTX","PFE","AZN","TEVA","AMRX","JAZZ",
        # Tech / Growth
        "IBM","DELL","HPQ","HPE","NTAP","WDC","STX","ZM","DOCU","OKTA",
        "CRWD","PANW","FTNT","DDOG","SPLK","MDB","SNOW","PLTR","COIN","HOOD",
        "SOFI","OPEN","AFRM","UPST","LC","SQ","PYPL",
        # Media / Consumer
        "DIS","NFLX","CMCSA","WBD","FOX","SPOT","MTCH","IAC","TTD",
        "SNAP","PINS","ETSY","EBAY","AMZN","SHOP","W","CHWY","CVNA","CARG",
        # Energy
        "XOM","CVX","COP","DVN","MPC","PSX","VLO","EOG","PXD","OXY","HAL",
        "SLB","BKR","APA","FANG","MRO","CVI","DKL","PARR","DINO",
        # Materials
        "NEM","FCX","AA","CLF","GOLD","AEM","AG","HL","WPM","PAAS","MP",
        "VALE","RIO","BHP","SCCO","TECK","HCC","AMR","ARCH","CEIX","METC",
        # Industrials / Airlines
        "BA","CAT","GE","HON","RTX","LMT","UPS","FDX","DE","URI","PCAR",
        "DAL","UAL","AAL","LUV","JBLU","ALK","SKYW","HA",
        # Consumer / Retail
        "CCL","RCL","NCLH","MGM","LVS","WYNN","PENN","DKNG","CZR","CHDN",
        "GME","AMC","BB","SPCE","LCID",
        "RIVN","WKHS","XL",
        # Solar / EV
        "ENPH","SEDG","FSLR","RUN","ARRY","SHLS","STEM","BE","PLUG",
        "FCEL","BLDP","CLNE","GPRE","REX","AMTX","GEVO",
        # Meme / High short
        "MARA","RIOT","CLSK","CIFR","BTBT","HUT","BITF","MSTR","COIN",
        "SOFI","OPEN","UWMC","RKT",
        # Pharma / Biotech squeeze prone
        
        # ETFs with options
        "IWM","QQQ","SPY","GLD","SLV","GDX","GDXJ","XLE","XLF","XBI",
        "ARKK","ARKG","ARKW","ARKQ","ARKF","DRIV","LIT","ICLN","TAN",
    ]

# ── Global squeeze state (must be defined before functions that use it) ────────

squeeze_state = {
    "high_short_list": [],
    "reddit_mentions": {},
    "on_watch":        [],
    "short_updated":   None,
    "reddit_updated":  None,
    "sp500":           [],
    "sp500_updated":   None,
    "reddit_enabled":  False,
}

def get_sp500():
    now = datetime.now()
    if (not squeeze_state["sp500"] or not squeeze_state["sp500_updated"] or
        (now - squeeze_state["sp500_updated"]).days > 7):
        t = fetch_sp500_tickers()
        if t:
            squeeze_state["sp500"] = t
            squeeze_state["sp500_updated"] = now
            print(f"SP500 cached: {len(t)} tickers")
    return squeeze_state["sp500"]

def _reddit_json_fallback():
    """Pull social mentions from Stocktwits and Yahoo trending (free, no auth)."""
    exclude = {"THE","AND","FOR","ARE","BUT","NOT","YOU","ALL","CAN","WAS",
               "ONE","OUR","OUT","WHO","GET","HOW","ETF","PUT","CEO","IPO",
               "SEC","NYSE","OTM","ATM","ITM","DTE","EPS","GDP","FED","IMO",
               "USD","SPY","QQQ","IWM","SPX","WSB","DD","OP","IV","PE","BB",
               "AI","EV","US","EU","UK","BTC","CALL","PUTS","YOLO","EDIT",
               "TECH","STOCK","MARKET","BULL","BEAR","SHORT","LONG","MOON"}
    ticker_re = re.compile(r"\b([A-Z]{2,5})\b")
    counts = {}

    # Stocktwits trending
    try:
        r = requests.get("https://api.stocktwits.com/api/2/trending/symbols.json",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        if r.status_code == 200:
            for s in r.json().get("symbols", []):
                sym = s.get("symbol","")
                if sym and sym.isalpha() and 1 < len(sym) <= 5 and sym not in exclude:
                    counts[sym] = counts.get(sym,0) + 10
            print(f"Stocktwits: {len(counts)} symbols")
    except Exception as e:
        print(f"Stocktwits error: {e}")

    # Yahoo Finance trending
    try:
        r = requests.get("https://query1.finance.yahoo.com/v1/finance/trending/US?count=25",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        if r.status_code == 200:
            result = r.json().get("finance",{}).get("result",[])
            for q in (result[0].get("quotes",[]) if result else []):
                sym = q.get("symbol","")
                if sym and sym.isalpha() and 1 < len(sym) <= 5 and sym not in exclude:
                    counts[sym] = counts.get(sym,0) + 8
            print(f"Yahoo trending: {len(counts)} total symbols")
    except Exception as e:
        print(f"Yahoo trending error: {e}")

    filtered = {k:v for k,v in counts.items() if v >= 3}
    squeeze_state["reddit_mentions"] = filtered
    squeeze_state["reddit_enabled"] = True
    squeeze_state["reddit_updated"] = datetime.now()
    print(f"Social mentions done: {len(filtered)} tickers")
    rebuild_watch_list()

def update_short_interest():
    """Rebuild high short interest list. Runs every 15 days.
    Uses sequential requests with delays to avoid Yahoo rate limiting."""
    print("Short interest update starting...")
    sp500 = get_sp500()
    universe_tickers = [t for s in UNIVERSE.values() for t in s]
    all_tickers = list(dict.fromkeys(sp500 + universe_tickers))
    print(f"Scanning {len(all_tickers)} tickers for short interest...")

    results = []
    errors = 0

    for sym in all_tickers:
        try:
            tk = yf.Ticker(sym)
            info = tk.fast_info  # fast_info is lighter than .info
            # fast_info doesn't have short data, fall back to info but with delay
            full_info = tk.info or {}
            sp = float(full_info.get("shortPercentOfFloat") or 0)
            dc = float(full_info.get("shortRatio") or 0)
            px = float(full_info.get("currentPrice") or
                       full_info.get("regularMarketPrice") or 0)
            if sp >= 0.10 and px >= 5:
                results.append({
                    "symbol": sym,
                    "short_pct": round(sp * 100, 1),
                    "days_cover": round(dc, 1),
                    "price": round(px, 2)
                })
                print(f"  {sym}: {round(sp*100,1)}% short float")
            time.sleep(0.3)  # 300ms between requests to avoid rate limiting
            errors = 0  # reset error counter on success
        except Exception as e:
            errors += 1
            if errors > 5:
                print(f"Too many errors, pausing 10s...")
                time.sleep(10)
                errors = 0
            time.sleep(0.5)

    results.sort(key=lambda x: -x["short_pct"])
    squeeze_state["high_short_list"] = results
    squeeze_state["short_updated"] = datetime.now()
    print(f"Short interest done: {len(results)} tickers with 10%+ short float")
    rebuild_watch_list()

def update_reddit_mentions():
    """Pull social momentum data. Calls _reddit_json_fallback which uses
    Stocktwits and Yahoo trending instead of Reddit (which blocks anonymous access)."""
    print("Updating social mentions...")
    _reddit_json_fallback()

def rebuild_watch_list():
    short_map = {r["symbol"]:r for r in squeeze_state["high_short_list"]}
    reddit    = squeeze_state["reddit_mentions"]
    all_syms  = set(short_map.keys()) | set(reddit.keys())
    watch = []
    for sym in all_syms:
        sd = short_map.get(sym,{})
        rc = reddit.get(sym,0)
        sp = sd.get("short_pct",0)
        if sp < 8 and rc < 5: continue
        score = 0
        if sp >= 25: score += 3
        elif sp >= 15: score += 2
        elif sp >= 10: score += 1
        if rc >= 20: score += 2
        elif rc >= 10: score += 1.5
        elif rc >= 5: score += 1
        elif rc >= 3: score += 0.5
        watch.append({"symbol":sym,"short_pct":sp,"days_cover":sd.get("days_cover",0),
                      "price":sd.get("price",0),"reddit":rc,"base_score":round(score,1),
                      "vol_ratio":0,"change_pct":0,"live_score":0})
    watch = [w for w in watch if w["base_score"] >= 3.0]  # only high conviction signals
    watch.sort(key=lambda x: -x["base_score"])
    squeeze_state["on_watch"] = watch[:100]
    print(f"Watch list: {len(squeeze_state['on_watch'])} tickers")

def refresh_live_squeeze_data():
    """Refresh price + volume for on_watch tickers. Called every 30 min."""
    syms = [r["symbol"] for r in squeeze_state["on_watch"]]
    if not syms: return
    def get_live(sym):
        try:
            info = yf.Ticker(sym).info or {}
            px   = float(info.get("currentPrice") or info.get("regularMarketPrice") or 0)
            prev = float(info.get("previousClose") or px)
            avgv = float(info.get("averageVolume") or 1)
            curv = float(info.get("volume") or 0)
            vr   = round(curv/avgv,1) if avgv>0 else 0
            chg  = round((px-prev)/prev*100,2) if prev else 0
            return sym,vr,chg,px
        except Exception:
            return sym,0,0,0
    with ThreadPoolExecutor(max_workers=15) as ex:
        for sym,vr,chg,px in ex.map(get_live,syms[:60]):
            for item in squeeze_state["on_watch"]:
                if item["symbol"]==sym:
                    item["vol_ratio"]=vr; item["change_pct"]=chg
                    if px>0: item["price"]=px
                    lb = 0
                    if vr>=3: lb+=2
                    elif vr>=2: lb+=1
                    elif vr>=1.5: lb+=0.5
                    if chg>=5: lb+=1
                    elif chg>=2: lb+=0.5
                    item["live_score"]=round(item["base_score"]+lb,1)
                    break
    squeeze_state["on_watch"].sort(key=lambda x:-x["live_score"])
    print(f"Live squeeze data refreshed for {len(syms)} tickers")

# ── Background scheduler ──────────────────────────────────────────────────────

def start_scheduler():
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler()
        # Short interest: every 15 days
        scheduler.add_job(update_short_interest, "interval", days=15, id="short_interest",
                          next_run_time=datetime.now())  # run immediately on startup
        # Reddit: every 2 hours
        scheduler.add_job(update_reddit_mentions, "interval", hours=2, id="reddit",
                          next_run_time=datetime.now())
        # Live data: every 30 minutes
        scheduler.add_job(refresh_live_squeeze_data, "interval", minutes=30, id="live_data")
        scheduler.start()
        print("Squeeze scheduler started")
        return scheduler
    except Exception as e:
        print(f"Scheduler error: {e}")
        return None

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production-abcd1234")

# Persistent token store
_token_store = {}

def store_token(username, token):
    _token_store["tw_token"] = token
    _token_store["tw_username"] = username

def get_stored_token():
    # Return cached token if available
    if _token_store.get("tw_token"):
        return _token_store["tw_token"]
    # Auto-login using environment variables if set
    username = os.environ.get("TASTYTRADE_USERNAME", os.environ.get("TW_USERNAME",""))
    password = os.environ.get("TASTYTRADE_PASSWORD", os.environ.get("TW_PASSWORD",""))
    if username and password:
        print("Auto-login from environment variables...")
        status, value = tw_step1(username, password)
        if status == "ok":
            _token_store["tw_token"] = value
            _token_store["tw_username"] = username
            print("Auto-login successful")
            return value
        print(f"Auto-login failed: {status} {value}")
    return None

TW_BASE = "https://api.tastyworks.com"

UNIVERSE = {
    "Energy":        ["XOM","CVX","OXY","HAL","SLB","COP","DVN","MPC","PSX","VLO","EOG","XLE"],
    "Financials":    ["JPM","BAC","GS","MS","WFC","C","AXP","BLK","COF","PNC","TFC","USB","XLF"],
    "Staples":       ["KO","PG","WMT","COST","CL","MO","PM","KMB","GIS","HSY"],
    "Discretionary": ["NKE","DIS","MCD","SBUX","HD","TGT","LOW","GM","LULU","BBY","RL","GRMN"],
    "Healthcare":    ["JNJ","PFE","ABBV","MRK","CVS","UNH","GILD","AMGN","MDT","BMY","CI","HUM"],
    "Tech":          ["MSFT","AAPL","GOOGL","META","CSCO","IBM","ORCL","QCOM","TXN","AMAT","DELL","HPQ","CRM","INTC"],
    "Materials":     ["GDX","NEM","FCX","AA","GOLD","CLF"],
    "Industrials":   ["BA","CAT","GE","HON","RTX","LMT","UPS","FDX","DE","DAL","UAL","LUV"],
    "ETFs":          ["IWM","QQQ","GLD","SPY","TLT","XBI","KRE","EEM"],
    "Other":         ["PYPL","WDAY","EBAY","GM","UBER","ABNB","PINS","ETSY","SNAP","RBLX","DASH"]
}

ETF_SYMBOLS = {"XLE","XLF","GDX","IWM","QQQ","GLD","SPY","TLT","XBI","KRE","EEM","GDXJ"}

# Fix 9: Jul 17 2026 as target expiry
TARGET_EXPIRY = date(2026, 7, 17)

# ── math ──────────────────────────────────────────────────────────────────────

def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def bs_put_delta(S, K, T, r, sigma):
    try:
        if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
            return None
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        return norm_cdf(d1) - 1
    except Exception:
        return None

# ── tastytrade ────────────────────────────────────────────────────────────────

def tw_headers(token):
    return {"Authorization": token, "Content-Type": "application/json"}

def tw_market_session(token):
    """Check if market is currently open. Returns state dict."""
    try:
        r = requests.get(f"{TW_BASE}/market-time/equities/sessions/current",
                         headers=tw_headers(token), timeout=8)
        if r.status_code == 200:
            return r.json().get("data", {})
    except Exception as e:
        print(f"market session error: {e}")
    return {}

def tw_symbol_info(token, symbol):
    """Get company name from symbol search endpoint."""
    try:
        r = requests.get(f"{TW_BASE}/symbols/search/{symbol}",
                         headers=tw_headers(token), timeout=8)
        if r.status_code == 200:
            items = r.json().get("data", {}).get("items", [])
            for item in items:
                if item.get("symbol") == symbol:
                    return {
                        "name": item.get("description", ""),
                        "has_options": item.get("options", False)
                    }
    except Exception as e:
        print(f"symbol_info {symbol}: {e}")
    return {}

def tw_symbol_info_bulk(token, symbols):
    """Fetch company names for a list of symbols. Returns {symbol: name}."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = {}
    def fetch(sym):
        info = tw_symbol_info(token, sym)
        return sym, info.get("name", "")
    with ThreadPoolExecutor(max_workers=10) as ex:
        for sym, name in ex.map(fetch, symbols):
            results[sym] = name
    return results

def tw_margin_requirement(token, account, symbol):
    """
    Get tastytrade margin rates for a symbol.
    Returns dict with rate fields, all as floats.
    naked-option-standard: e.g. 0.2 = 20% of (strike * 100)
    naked-option-floor: e.g. 250.0 = minimum $250 per contract
    """
    if not token or not account:
        return {}
    try:
        r = requests.get(
            f"{TW_BASE}/accounts/{account}/margin-requirements/{symbol}/effective",
            headers=tw_headers(token), timeout=8)
        if r.status_code == 200:
            d = r.json().get("data", {})
            return {
                "rate":  float(d.get("naked-option-standard") or 0.20),
                "floor": float(d.get("naked-option-floor") or 250),
                "min":   float(d.get("naked-option-minimum") or 0.10),
            }
    except Exception as e:
        print(f"margin_req {symbol}: {e}")
    return {}

def tw_watchlists(token):
    """Fetch user personal tastytrade watchlists. Returns list of {name, symbols}."""
    try:
        r = requests.get(f"{TW_BASE}/watchlists",
                         headers=tw_headers(token), timeout=10)
        if r.status_code == 200:
            items = r.json().get("data", {}).get("items", [])
            result = []
            for wl in items:
                entries = wl.get("watchlist-entries", [])
                if isinstance(entries, list):
                    syms = [e.get("symbol","") for e in entries
                            if e.get("instrument-type") == "Equity" and e.get("symbol")]
                else:
                    syms = []
                if syms:
                    result.append({"name": wl.get("name","Unnamed"), "symbols": syms})
            return result
    except Exception as e:
        print(f"watchlists error: {e}")
    return []

def tw_step1(username, password):
    """
    POST /sessions with credentials.
    Returns ("ok", token) | ("challenge", challenge_token) | ("error", msg)
    Challenge token is in response header X-Tastyworks-Challenge-Token.
    """
    try:
        r = requests.post(f"{TW_BASE}/sessions",
            json={"login": username, "password": password, "remember-me": True},
            headers={"Content-Type": "application/json"}, timeout=10)
        print(f"TW step1 status: {r.status_code}")

        if r.status_code == 201:
            d = r.json()["data"]
            return ("ok", d.get("session-token") or d.get("remember-token"))

        challenge_token = r.headers.get("X-Tastyworks-Challenge-Token", "")
        print(f"Challenge token: {challenge_token[:30]}...")

        try:
            err = r.json().get("error", {})
            if err.get("code") == "device_challenge_required":
                return ("challenge", challenge_token)
            return ("error", err.get("message", f"HTTP {r.status_code}"))
        except Exception:
            return ("error", f"HTTP {r.status_code}")
    except Exception as e:
        return ("error", str(e))

def tw_step2_initiate(challenge_token):
    """
    POST /device-challenge to initiate OTP flow.
    Just needs the challenge token header — no body needed.
    Returns True if successful.
    """
    try:
        r = requests.post(f"{TW_BASE}/device-challenge",
            json={},
            headers={
                "Content-Type": "application/json",
                "X-Tastyworks-Challenge-Token": challenge_token
            }, timeout=10)
        print(f"TW step2 initiate status: {r.status_code} — {r.text[:200]}")
        return r.status_code in (200, 201)
    except Exception as e:
        print(f"tw_step2_initiate error: {e}")
        return False

def tw_step3(username, password, challenge_token, totp_code):
    """
    POST /sessions again with credentials + challenge token header + OTP header.
    X-Tastyworks-OTP header carries the Google Authenticator code.
    Returns session token or None.
    """
    try:
        r = requests.post(f"{TW_BASE}/sessions",
            json={"login": username, "password": password, "remember-me": True},
            headers={
                "Content-Type": "application/json",
                "X-Tastyworks-Challenge-Token": challenge_token,
                "X-Tastyworks-OTP": totp_code
            }, timeout=10)
        print(f"TW step3 status: {r.status_code} — {r.text[:300]}")
        if r.status_code == 201:
            d = r.json()["data"]
            return d.get("session-token") or d.get("remember-token")
    except Exception as e:
        print(f"tw_step3 error: {e}")
    return None

def tw_accounts(token):
    try:
        r = requests.get(f"{TW_BASE}/customers/me/accounts",
                         headers=tw_headers(token), timeout=10)
        if r.status_code == 200:
            return r.json()["data"]["items"]
    except Exception:
        pass
    return []

def tw_balances(token, acct):
    try:
        r = requests.get(f"{TW_BASE}/accounts/{acct}/balances",
                         headers=tw_headers(token), timeout=10)
        if r.status_code == 200:
            return r.json()["data"]
    except Exception:
        pass
    return {}

def tw_positions(token, acct):
    try:
        r = requests.get(f"{TW_BASE}/accounts/{acct}/positions",
                         headers=tw_headers(token), timeout=10)
        if r.status_code == 200:
            return r.json()["data"]["items"]
    except Exception:
        pass
    return []

def tw_market_metrics(token, symbols):
    try:
        r = requests.get(f"{TW_BASE}/market-metrics",
                         params={"symbols": ",".join(symbols)},
                         headers=tw_headers(token), timeout=15)
        if r.status_code == 200:
            items = r.json()["data"]["items"]
            result = {}
            for m in items:
                sym = m.get("symbol")
                # Debug first symbol to verify field names
                if sym == symbols[0]:
                    print(f"Sample metrics for {sym}: tw-iv-rank={m.get('tw-implied-volatility-index-rank')}, iv-index-rank={m.get('implied-volatility-index-rank')}, liq={m.get('liquidity-rating')}")
                result[sym] = m
            return result
        else:
            print(f"Market metrics {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"Market metrics error: {e}")
    return {}

def get_expiry_iv_from_metrics(m, target_expiry):
    """Per-expiry IV from tastytrade, returns percentage float e.g. 26.8"""
    exps = m.get("option-expiration-implied-volatilities", [])
    target_str = target_expiry.strftime("%Y-%m-%d")
    for e in exps:
        if str(e.get("expiration-date", ""))[:10] == target_str:
            iv = e.get("implied-volatility")
            return round(float(iv) * 100, 1) if iv else None
    # closest within 14 days
    best, best_diff = None, 999
    for e in exps:
        try:
            exp_d = date.fromisoformat(str(e.get("expiration-date",""))[:10])
            diff = abs((exp_d - target_expiry).days)
            if diff < best_diff and diff <= 14:
                best_diff = diff
                iv = e.get("implied-volatility")
                best = round(float(iv) * 100, 1) if iv else None
        except Exception:
            pass
    return best

# ── yfinance helpers ──────────────────────────────────────────────────────────

def yf_price_range_vol(symbol):
    try:
        hist = yf.Ticker(symbol).history(period="1y")
        if hist.empty:
            return None, None, None, None
        return (
            round(float(hist["Close"].iloc[-1]), 2),
            round(float(hist["Low"].min()), 2),
            round(float(hist["High"].max()), 2),
            int(hist["Volume"].mean())
        )
    except Exception as e:
        print(f"price_range_vol {symbol}: {e}")
        return None, None, None, None

def yf_next_earnings(symbol):
    """Always return next upcoming earnings. ETFs return ('—', None)."""
    if symbol in ETF_SYMBOLS:
        return "—", None
    # Try multiple approaches
    try:
        tk = yf.Ticker(symbol)
        # Method 1: calendar
        try:
            cal = tk.calendar
            if cal is not None and not cal.empty and "Earnings Date" in cal.index:
                dates = cal.loc["Earnings Date"]
                d = dates.iloc[0] if hasattr(dates, "iloc") else dates
                d = d.date() if hasattr(d, "date") else d
                if hasattr(d, "strftime") and d >= date.today():
                    return d.strftime("%b %d %Y"), d
        except Exception:
            pass
        # Method 2: earnings_dates
        try:
            ed = tk.earnings_dates
            if ed is not None and not ed.empty:
                future = [i for i in ed.index if i.date() >= date.today()]
                if future:
                    d = min(future).date()
                    return d.strftime("%b %d %Y"), d
        except Exception:
            pass
        # Method 3: info nextEarningsDate
        try:
            info = tk.info
            ned = info.get("nextEarningsDate") or info.get("earningsTimestamp")
            if ned:
                d = date.fromtimestamp(ned) if isinstance(ned, (int, float)) else ned
                if hasattr(d, "strftime") and d >= date.today():
                    return d.strftime("%b %d %Y"), d
        except Exception:
            pass
    except Exception as e:
        print(f"earnings {symbol}: {e}")
    # No future date found — next quarter not yet announced
    return "Pending", None

def yf_iv_for_expiry(symbol, target_expiry, price):
    """
    Get IV from the yfinance options chain for the closest expiry to target.
    Returns (iv_pct, iv_rank_estimate) — iv_pct e.g. 28.5 means 28.5%
    """
    try:
        tk = yf.Ticker(symbol)
        avail = tk.options
        if not avail:
            return None, None
        # Find closest expiry
        best_exp = min(avail, key=lambda e: abs((date.fromisoformat(e) - target_expiry).days))
        chain = tk.option_chain(best_exp)
        puts = chain.puts
        if puts.empty:
            return None, None
        # ATM puts (within 5%)
        atm = puts[abs(puts["strike"] - price) <= price * 0.06]
        if atm.empty:
            atm = puts.iloc[len(puts)//2 - 1 : len(puts)//2 + 2]
        iv_mean = float(atm["impliedVolatility"].mean()) * 100
        return round(iv_mean, 1), None
    except Exception as e:
        print(f"yf_iv {symbol}: {e}")
        return None, None

def yf_35delta_put(symbol, price, target_expiry):
    """Closest 0.35 delta put strike. Returns (strike, bp)."""
    try:
        tk = yf.Ticker(symbol)
        avail = tk.options
        if not avail:
            return None, None
        best_exp = min(avail, key=lambda e: abs((date.fromisoformat(e) - target_expiry).days))
        if abs((date.fromisoformat(best_exp) - target_expiry).days) > 21:
            return None, None
        chain = tk.option_chain(best_exp)
        puts = chain.puts
        if puts.empty:
            return None, None
        T = max((date.fromisoformat(best_exp) - date.today()).days, 1) / 365.0
        r = 0.045
        best_strike, best_diff = None, 1.0
        for _, row in puts.iterrows():
            K = float(row["strike"])
            iv_row = float(row.get("impliedVolatility") or 0.30)
            if iv_row < 0.01:
                iv_row = 0.30
            delta = bs_put_delta(price, K, T, r, iv_row)
            if delta is None:
                continue
            diff = abs(abs(delta) - 0.35)
            if diff < best_diff:
                best_diff, best_strike = diff, K
        if best_strike is None:
            return None, None
        return round(best_strike, 2), round(best_strike * 100 * 0.20 * 1.15)
    except Exception as e:
        print(f"35delta {symbol}: {e}")
        return None, None

# ── SPY pulse (fix 1) ─────────────────────────────────────────────────────────

def get_spy_vix():
    """Use SPY for market pulse instead of SPX."""
    spy = ath = pct = vix = None
    try:
        hist = yf.Ticker("SPY").history(period="5y")
        if not hist.empty:
            spy = round(float(hist["Close"].iloc[-1]), 2)
            ath = round(float(hist["High"].max()), 2)
            pct = round((ath - spy) / ath * 100, 2) if ath else None
    except Exception as e:
        print(f"SPY error: {e}")
    try:
        hist_v = yf.Ticker("^VIX").history(period="5d")
        if not hist_v.empty:
            vix = round(float(hist_v["Close"].iloc[-1]), 2)
    except Exception as e:
        print(f"VIX error: {e}")
    return spy, ath, pct, vix

# ── screener ──────────────────────────────────────────────────────────────────

def screen_ticker(symbol, tw_token=None, target_expiry=None, tw_metrics=None):
    if target_expiry is None:
        target_expiry = TARGET_EXPIRY

    out = {
        "symbol": symbol, "sector": None,
        "price": None, "low52": None, "high52": None, "rangePct": None,
        "ivExpiry": None, "ivRank": None,
        "earnings": "—", "earningsWarn": False,
        "volume": "—", "liquid": False, "squeezed": False,
        "passes": False, "filterReasons": [],
        "flag": "", "thesis": "",
        "strike35": None, "bp": None,
        "expiry": target_expiry.strftime("%b %d"),
    }

    try:
        # 1. Price / range / volume
        price, low52, high52, vol = yf_price_range_vol(symbol)
        if price is None:
            out["thesis"] = "No price data"
            out["filterReasons"].append("No price data")
            return out

        # Skip sub-$20 stocks
        if price < 20:
            out["price"] = price
            out["thesis"] = f"Price ${price:.2f} — below $20 minimum"
            out["filterReasons"].append(f"Price ${price:.2f} below $20 minimum")
            return out

        out["price"], out["low52"], out["high52"] = price, low52, high52
        span = (high52 or 0) - (low52 or 0)
        range_pct = round((price - low52) / span * 100) if span > 0 else 50
        out["rangePct"] = range_pct

        vol_label = (f"{vol/1_000_000:.1f}M" if vol and vol >= 1_000_000
                     else f"{vol//1_000}K"    if vol and vol >= 1_000
                     else str(vol or "—"))
        out["volume"] = vol_label
        liquid = vol is not None and vol >= 500_000
        out["liquid"] = liquid

        # 2. Squeeze check
        try:
            h = yf.Ticker(symbol).history(period="1y")
            if not h.empty and len(h) >= 60:
                out["squeezed"] = price > float(h["Close"].iloc[-60]) * 1.8
        except Exception:
            pass

        # 3. Earnings
        earn_str, earn_date = yf_next_earnings(symbol)
        earn_warn = False
        if earn_date:
            days_earn = (earn_date - date.today()).days
            days_exp  = (target_expiry - date.today()).days
            earn_warn = 0 <= days_earn <= days_exp + 7
        out["earnings"], out["earningsWarn"] = earn_str, earn_warn

        # 4. IV — tastytrade first, yfinance fallback
        iv_expiry = None
        iv_rank   = None
        if tw_token:
            try:
                # Use pre-fetched metrics if available, else fetch individually
                m = tw_metrics if tw_metrics is not None else tw_market_metrics(tw_token, [symbol]).get(symbol, {})

                # IV Rank — correct field is implied-volatility-index-rank (0-1+ scale)
                # Use tastytrade's own rank (tw-implied-volatility-index-rank) preferably
                raw_rank = m.get("tw-implied-volatility-index-rank") or m.get("implied-volatility-index-rank")
                if raw_rank is not None:
                    iv_rank = min(100, round(float(raw_rank) * 100))

                # Liquidity
                liq = m.get("liquidity-rating")
                if liq is not None and int(liq) >= 1:
                    out["liquid"] = True

                # Per-expiry IV for target expiry (already in decimal, multiply by 100)
                iv_expiry = get_expiry_iv_from_metrics(m, target_expiry)

                # Earnings from TW metrics — much more reliable than yfinance
                tw_earnings = m.get("earnings", {})
                if tw_earnings:
                    exp_date_str = tw_earnings.get("expected-report-date","")
                    if exp_date_str and exp_date_str != "1970-01-01":
                        try:
                            earn_d = date.fromisoformat(exp_date_str)
                            tod = tw_earnings.get("time-of-day","")
                            tod_str = f" {tod}" if tod else ""
                            if earn_d >= date.today():
                                out["earnings"] = earn_d.strftime("%b %d %Y") + tod_str
                                days_earn = (earn_d - date.today()).days
                                days_exp  = (target_expiry - date.today()).days
                                out["earningsWarn"] = 0 <= days_earn <= days_exp + 7
                            else:
                                out["earnings"] = "Pending"
                                out["earningsWarn"] = False
                        except Exception:
                            pass

            except Exception as e:
                print(f"TW metrics {symbol}: {e}")

        # yfinance IV fallback
        if iv_expiry is None:
            iv_expiry, _ = yf_iv_for_expiry(symbol, target_expiry, price)

        out["ivExpiry"] = iv_expiry
        out["ivRank"]   = iv_rank

        # 5. 0.35 delta strike + BP
        strike35, bp35 = yf_35delta_put(symbol, price, target_expiry)
        out["strike35"] = strike35
        out["bp"] = bp35 if bp35 else round(price * 0.90 * 100 * 0.20 * 1.15)

        # 6. Pass/fail with reasons (fix 8)
        reasons = []
        iv_check_val = iv_expiry if iv_expiry is not None else (iv_rank or 0)
        iv_threshold = 25 if iv_expiry is not None else 35

        if out["squeezed"]:
            reasons.append("Recently squeezed 80%+")
        if range_pct > 40:
            reasons.append(f"52wk pos {range_pct}% (above 40% threshold)")
        if iv_check_val < iv_threshold:
            reasons.append(f"IVx {iv_check_val:.0f}% below threshold ({iv_threshold}%)")
        if not out["liquid"]:
            reasons.append("Low options volume / illiquid")

        out["filterReasons"] = reasons
        out["passes"] = len(reasons) == 0

        # 7. Thesis (fix 5 — add more detail)
        name = symbol
        if out["squeezed"]:
            out["thesis"] = f"{name} recently up 80%+ — meme/squeeze behavior"
        elif range_pct <= 15:
            out["thesis"] = f"{name} near 52wk lows — elevated IV, high margin of safety for put selling"
        elif range_pct <= 30:
            out["thesis"] = f"{name} in lower third of range — good downside cushion below current price"
        elif range_pct <= 40:
            out["thesis"] = f"{name} below midrange — reasonable put selling opportunity with IV support"
        elif range_pct <= 60:
            out["thesis"] = f"{name} above midrange — limited downside buffer, consider waiting for pullback"
        else:
            out["thesis"] = f"{name} in upper range — near highs, avoid selling puts here"

        if earn_warn and earn_str not in ("—","Unknown"):
            out["flag"] = f"Earnings {earn_str}"

    except Exception as e:
        out["thesis"] = f"Error: {e}"
        out["filterReasons"].append(str(e))
    return out

# ── routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", universe=UNIVERSE)

@app.route("/api/login", methods=["POST"])
def login():
    """Step 1: POST credentials. If challenge required, initiate device flow and return."""
    data     = request.json or {}
    username = data.get("username","")
    password = data.get("password","")
    session["tw_username"] = username
    session["tw_password"] = password

    status, value = tw_step1(username, password)

    if status == "ok":
        session["tw_token"] = value
        accts  = tw_accounts(value)
        margin = next((a["account"]["account-number"] for a in accts
                       if not a["account"].get("is-closed")), None)
        session["account"] = margin
        return jsonify({"ok": True, "account": margin})

    if status == "challenge":
        session["tw_challenge"] = value
        tw_step2_initiate(value)  # trigger the device challenge (no-op for TOTP but required)
        return jsonify({"ok": False, "challenge_required": True,
                        "message": "Open Google Authenticator and enter your current 6-digit code"})

    return jsonify({"ok": False, "error": value or "Login failed"}), 401

@app.route("/api/login/totp", methods=["POST"])
def login_totp():
    """
    Step 3: Re-POST /sessions with challenge token + OTP as headers.
    The Google Auth code goes in X-Tastyworks-OTP header.
    """
    data            = request.json or {}
    totp_code       = data.get("totp","").strip()
    challenge_token = session.get("tw_challenge","")
    username        = session.get("tw_username","")
    password        = session.get("tw_password","")

    if not challenge_token:
        return jsonify({"ok": False, "error": "Session expired — please start over"}), 400

    token = tw_step3(username, password, challenge_token, totp_code)
    if not token:
        return jsonify({"ok": False, "error": "Invalid code — check Google Authenticator and try again"}), 401

    session["tw_token"] = token
    session.permanent = True
    accts  = tw_accounts(token)
    margin = next((a["account"]["account-number"] for a in accts
                   if not a["account"].get("is-closed")), None)
    session["account"] = margin
    store_token(session.get("tw_username",""), token)
    return jsonify({"ok": True, "account": margin})

@app.route("/api/pulse")
def pulse():
    spy, ath, pct, vix = get_spy_vix()
    token = session.get("tw_token")
    nlv = deriv_bp = deployed_pct = 0
    positions = []

    if token and session.get("account"):
        try:
            bal = tw_balances(token, session["account"])
            positions = tw_positions(token, session["account"])
            nlv       = float(bal.get("net-liquidating-value") or 0)
            deriv_bp  = float(bal.get("derivative-buying-power") or 0)
            maint_req = float(bal.get("maintenance-requirement") or 0)
            deployed_pct = round(maint_req / deriv_bp * 100, 1) if deriv_bp > 0 else 0
        except Exception as e:
            print(f"Pulse balances: {e}")

    if pct is not None and vix is not None:
        if pct <= 3 or vix < 18:
            rec = {"label":"Conservative","pct":"≤30% deployed","hedge":True,"level":"danger"}
        elif pct <= 10 or vix < 25:
            rec = {"label":"Moderate","pct":"≤40% deployed","hedge":False,"level":"warning"}
        else:
            rec = {"label":"Aggressive","pct":"≤50% deployed","hedge":False,"level":"success"}
    else:
        rec = {"label":"—","pct":"Market closed or unavailable","hedge":False,"level":"secondary"}

    def fmt_pos(p):
        sym   = p.get("underlying-symbol","")
        itype = p.get("instrument-type","")
        raw   = p.get("symbol","")
        qty   = p.get("quantity",0)
        if itype == "Equity Option":
            try:
                # OCC format: "INTC  260618C00100000"
                parts = raw.strip()
                under = parts[:6].strip()
                rest  = parts[6:].strip()
                yy,mm,dd = rest[0:2],rest[2:4],rest[4:6]
                opt_type = rest[6]
                strike = int(rest[7:]) / 1000
                desc = f"${strike:.0f}{opt_type} {mm}/{dd}/20{yy}"
            except Exception:
                desc = raw.strip()[-20:]
        elif itype == "Equity":
            desc = f"Stock · {int(qty)} shares"
        else:
            desc = raw[:20]
        return {"symbol": sym, "desc": desc, "qty": qty}

    open_pos = [fmt_pos(p) for p in positions
                if p.get("instrument-type") in ("Equity Option","Equity")]

    # Market session status
    market_state = {}
    if token:
        sess = tw_market_session(token)
        market_state = {
            "state": sess.get("state", ""),
            "open_at": sess.get("open-at", ""),
            "close_at": sess.get("close-at", "")
        }

    # Watchlists
    watchlists_data = []
    if token:
        watchlists_data = tw_watchlists(token)

    return jsonify({
        "spy": spy, "ath": ath, "pct_from_ath": pct, "vix": vix,
        "rec": rec, "nlv": nlv, "deriv_bp": deriv_bp,
        "deployed_pct": deployed_pct, "positions": open_pos,
        "market": market_state,
        "watchlists": watchlists_data
    })

@app.route("/api/screen", methods=["POST"])
def screen():
    data    = request.json or {}
    sectors = data.get("sectors", list(UNIVERSE.keys()))
    custom  = [t.strip().upper() for t in data.get("custom","").split(",") if t.strip()]
    token   = session.get("tw_token")

    # Build ticker→sector map
    ticker_sector = {}
    for s in sectors:
        for t in UNIVERSE.get(s, []):
            ticker_sector[t] = s
    for t in custom:
        ticker_sector[t] = "Custom"

    # Merge in all tastytrade watchlist symbols
    token = session.get("tw_token")
    if token:
        try:
            wls = tw_watchlists(token)
            for wl in wls:
                for sym in wl.get("symbols", []):
                    if sym and sym not in ticker_sector:
                        ticker_sector[sym] = f"Watchlist: {wl['name']}"
        except Exception as e:
            print(f"Watchlist merge error: {e}")

    tickers = list(ticker_sector.keys())

    # Pre-fetch ALL tastytrade metrics in a single batch call
    tw_metrics_cache = {}
    if token:
        try:
            tw_metrics_cache = tw_market_metrics(token, tickers)
            print(f"Batch metrics fetched for {len(tw_metrics_cache)} symbols")
        except Exception as e:
            print(f"Batch metrics error: {e}")

    # Pre-fetch company names in parallel
    company_names = {}
    if token:
        try:
            company_names = tw_symbol_info_bulk(token, tickers)
            print(f"Company names fetched for {len(company_names)} symbols")
        except Exception as e:
            print(f"Company names error: {e}")

    # Pre-fetch margin requirements in parallel
    acct = session.get("account")
    margin_cache = {}
    if token and acct:
        try:
            def fetch_margin(sym):
                rates = tw_margin_requirement(token, acct, sym)
                return sym, rates
            with ThreadPoolExecutor(max_workers=8) as ex:
                for sym, rates in ex.map(fetch_margin, tickers):
                    if rates:
                        margin_cache[sym] = rates
            print(f"Margin requirements fetched for {len(margin_cache)} symbols")
        except Exception as e:
            print(f"Margin cache error: {e}")

    results = []

    def fetch_one(sym):
        r = screen_ticker(sym, token, TARGET_EXPIRY, tw_metrics_cache.get(sym))
        r["sector"] = ticker_sector.get(sym, "Custom")
        r["company_name"] = company_names.get(sym, "")
        # Use real tastytrade margin rate to compute BP
        rates = margin_cache.get(sym, {})
        strike = r.get("strike35") or (r.get("price", 50) * 0.90)
        if rates and strike:
            rate  = rates.get("rate", 0.20)
            floor = rates.get("floor", 250)
            bp_calc = max(strike * 100 * rate, floor)
            r["bp"] = round(bp_calc)
            r["bp_source"] = "tastytrade"
        else:
            r["bp_source"] = "estimated"
            if not r.get("bp") or r["bp"] == 0:
                price = r.get("price") or 50
                r["bp"] = round(price * 0.90 * 100 * 0.20 * 1.15)
        return r

    # Run up to 15 tickers in parallel
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(fetch_one, sym): sym for sym in tickers}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                sym = futures[future]
                results.append({"symbol": sym, "passes": False,
                                 "sector": ticker_sector.get(sym,""), "thesis": str(e)})

    results.sort(key=lambda x: (not x.get("passes"), x.get("rangePct") or 99))
    return jsonify({
        "results": results,
        "target_expiry": TARGET_EXPIRY.strftime("%b %d %Y")
    })


@app.route("/api/research/<symbol>")
def research(symbol):
    """
    Pull recent news via yfinance + key financials and return a structured summary.
    No API key required.
    """
    symbol = symbol.upper().strip()
    try:
        tk   = yf.Ticker(symbol)
        info = {}
        try: info = tk.info or {}
        except Exception: pass

        # News headlines
        news_items = []
        try:
            raw_news = tk.news or []
            for n in raw_news[:12]:
                title = n.get("title","")
                pub   = n.get("publisher","")
                ts    = n.get("providerPublishTime",0)
                link  = n.get("link","#")
                if title:
                    age_days = (time.time() - ts) / 86400 if ts else 99
                    news_items.append({"title":title,"publisher":pub,"age_days":round(age_days,1),"link":link})
        except Exception as e:
            print(f"news error {symbol}: {e}")

        # Key financials
        mkt_cap   = info.get("marketCap")
        pe        = info.get("trailingPE") or info.get("forwardPE")
        target    = info.get("targetMeanPrice")
        price     = info.get("currentPrice") or info.get("regularMarketPrice")
        rec       = info.get("recommendationKey","")   # "buy","hold","sell" etc
        num_analysts = info.get("numberOfAnalystOpinions",0)
        beta      = info.get("beta")
        div_yield = info.get("dividendYield")
        short_pct = info.get("shortPercentOfFloat")
        earnings_qtr_growth = info.get("earningsQuarterlyGrowth")
        revenue_growth      = info.get("revenueGrowth")

        # Keyword scan on headlines
        all_titles = " ".join(n["title"].lower() for n in news_items)
        flags = {
            "earnings_beat":  any(w in all_titles for w in ["beat","beats","topped","topped estimates","exceeded"]),
            "earnings_miss":  any(w in all_titles for w in ["miss","misses","missed","fell short","below estimates"]),
            "guidance_up":    any(w in all_titles for w in ["raised guidance","lifts guidance","raises guidance","raised outlook"]),
            "guidance_down":  any(w in all_titles for w in ["cut guidance","lowered guidance","lowers guidance","reduced outlook","cut outlook"]),
            "lawsuit":        any(w in all_titles for w in ["lawsuit","sued","sues","litigation","settlement","class action","ftc","sec charges","doj"]),
            "merger":         any(w in all_titles for w in ["merger","acquire","acquisition","buyout","takeover","deal"]),
            "upgrade":        any(w in all_titles for w in ["upgrade","upgraded","outperform","buy rating","overweight"]),
            "downgrade":      any(w in all_titles for w in ["downgrade","downgraded","underperform","sell rating","underweight"]),
            "layoffs":        any(w in all_titles for w in ["layoff","layoffs","job cut","cuts jobs","restructur"]),
            "squeeze":        any(w in all_titles for w in ["short squeeze","squeeze","short interest"]),
            "dividend":       any(w in all_titles for w in ["dividend","dividend hike","raises dividend"]),
        }

        # Build positives and risks
        positives, risks = [], []

        if flags["earnings_beat"]:   positives.append("Recent earnings beat — stock met or exceeded expectations")
        if flags["guidance_up"]:     positives.append("Guidance raised — management confident in outlook")
        if flags["upgrade"]:         positives.append("Analyst upgrade recently — improving sentiment")
        if flags["merger"]:          positives.append("M&A activity — potential premium or catalyst")
        if flags["dividend"]:        positives.append("Dividend activity — shareholder-friendly action")
        if target and price and target > price * 1.10:
            positives.append(f"Analyst mean target ${target:.0f} vs current ${price:.0f} — {round((target/price-1)*100)}% upside implied")
        if rec in ("buy","strong_buy"):
            positives.append(f"Analyst consensus: {rec.replace('_',' ').title()} ({num_analysts} analysts)")
        if short_pct and 0.10 < short_pct < 0.80:  # sanity check
            positives.append(f"High short interest ({short_pct*100:.1f}%) — short squeeze potential adds floor")
        if div_yield and 0.002 < div_yield < 0.20:  # sanity check: between 0.2% and 20%
            positives.append(f"Dividend yield {div_yield*100:.1f}% provides income support")
        if earnings_qtr_growth and earnings_qtr_growth > 0.10:
            positives.append(f"Earnings growing {earnings_qtr_growth*100:.0f}% QoQ — fundamental momentum")

        if flags["earnings_miss"]:   risks.append("Recent earnings miss — may face further selling pressure")
        if flags["guidance_down"]:   risks.append("Guidance cut — management cautious on near-term outlook")
        if flags["downgrade"]:       risks.append("Analyst downgrade recently — deteriorating sentiment")
        if flags["lawsuit"]:         risks.append("Litigation or regulatory risk mentioned in recent news")
        if flags["layoffs"]:         risks.append("Layoffs or restructuring underway — business uncertainty")
        if flags["squeeze"]:         risks.append("Short squeeze dynamics — elevated volatility risk")
        if target and price and target < price * 0.95:
            risks.append(f"Analyst mean target ${target:.0f} below current ${price:.0f} — consensus sees downside")
        if rec in ("sell","strong_sell","underperform"):
            risks.append(f"Analyst consensus: {rec.replace('_',' ').title()} — broadly negative view")
        if beta and beta > 1.5:
            risks.append(f"High beta ({beta:.1f}) — moves significantly more than the market")
        if revenue_growth and revenue_growth < -0.05:
            risks.append(f"Revenue declining {revenue_growth*100:.0f}% — top-line pressure")

        # Signal
        pos_score = len(positives)
        risk_score = len(risks)
        if flags["lawsuit"] or flags["guidance_down"] or flags["earnings_miss"]: risk_score += 1
        if pos_score > risk_score + 1:   signal = "bullish"
        elif risk_score > pos_score + 1: signal = "bearish"
        elif risk_score > 0 and flags.get("lawsuit"): signal = "caution"
        else: signal = "neutral"

        # Summary from top 3 headlines
        top_headlines = [n["title"] for n in news_items[:3]]
        if top_headlines:
            summary = "Recent headlines: " + " | ".join(top_headlines[:3])
        else:
            summary = "No recent news found via yfinance."

        # Put selling take
        if signal == "bullish" and not flags["squeeze"]:
            put_take = "Good setup — positive fundamentals support selling puts at a discount."
        elif signal == "bearish":
            put_take = "Caution — negative signals present. Wait for stabilization before selling puts."
        elif flags["lawsuit"]:
            put_take = "Legal risk is a wildcard. If selling puts, go further OTM than usual."
        elif flags["squeeze"]:
            put_take = "Squeeze dynamics suggest elevated IV — good premium but watch for sharp reversals."
        elif flags["merger"]:
            put_take = "M&A situation creates binary risk. Premium may be attractive but event outcome uncertain."
        else:
            put_take = "Neutral setup — check IV and position in range before committing."

        company_name = info.get("longName") or info.get("shortName") or ""
        return jsonify({
            "ok": True,
            "data": {
                "signal":         signal,
                "summary":        summary,
                "positives":      positives or ["No clear positive signals in recent news"],
                "risks":          risks or ["No major red flags detected in recent news"],
                "put_selling_take": put_take,
                "headlines":      news_items[:8],
                "analyst_rec":    rec,
                "pe":             round(pe, 1) if pe else None,
                "target":         round(target, 2) if target else None,
                "beta":           round(beta, 2) if beta else None,
                "company_name":   company_name,
            }
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/debug/metrics/<symbol>")
def debug_metrics(symbol):
    token = session.get("tw_token")
    if not token:
        return jsonify({"error": "Not logged in"})
    try:
        r = requests.get(f"{TW_BASE}/market-metrics",
                         params={"symbols": symbol.upper()},
                         headers=tw_headers(token), timeout=15)
        return jsonify({"status": r.status_code, "body": r.json()})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/debug/marketdata/<symbol>")
def debug_market_data(symbol):
    """Test /market-data/by-type with correct tuple param format."""
    token = session.get("tw_token")
    if not token:
        return jsonify({"error": "Not logged in"})
    sym = symbol.upper()
    results = {}

    # Use PreparedRequest to avoid URL encoding brackets
    from requests import Request, Session as RSession

    def raw_get(url):
        s = RSession()
        req = Request("GET", url, headers=tw_headers(token))
        prep = req.prepare()
        prep.url = url  # override to prevent encoding
        return s.send(prep, timeout=10)

    # equities[] raw
    try:
        r = raw_get(f"{TW_BASE}/market-data/by-type?equities[]={sym}")
        body = r.json()
        items = body.get("data",{}).get("items",[])
        results["equities_raw"] = {"status": r.status_code, "item_count": len(items), "first": items[0] if items else None}
    except Exception as e:
        results["equities_raw"] = {"error": str(e)}

    # two symbols raw
    try:
        r = raw_get(f"{TW_BASE}/market-data/by-type?equities[]={sym}&equities[]=SPY")
        body = r.json()
        items = body.get("data",{}).get("items",[])
        results["two_raw"] = {"status": r.status_code, "item_count": len(items), "first": items[0] if items else None}
    except Exception as e:
        results["two_raw"] = {"error": str(e)}

    # indices[] raw
    try:
        r = raw_get(f"{TW_BASE}/market-data/by-type?indices[]={sym}")
        body = r.json()
        items = body.get("data",{}).get("items",[])
        results["indices_raw"] = {"status": r.status_code, "item_count": len(items), "first": items[0] if items else None}
    except Exception as e:
        results["indices_raw"] = {"error": str(e)}

    # no brackets
    try:
        r = raw_get(f"{TW_BASE}/market-data/by-type?equities={sym}&equities=SPY")
        body = r.json()
        items = body.get("data",{}).get("items",[])
        results["no_brackets"] = {"status": r.status_code, "item_count": len(items), "first": items[0] if items else None}
    except Exception as e:
        results["no_brackets"] = {"error": str(e)}

    return jsonify(results)

def tw_market_data_bulk(token, symbols):
    """
    Fetch real-time market data for multiple equity symbols in one call.
    Uses raw URL to avoid bracket encoding. Returns dict keyed by symbol.
    """
    if not token or not symbols:
        return {}
    try:
        from requests import Request, Session as RSession
        qs = "&".join(f"equities[]={s}" for s in symbols)
        url = f"{TW_BASE}/market-data/by-type?{qs}"
        s = RSession()
        req = Request("GET", url, headers=tw_headers(token))
        prep = req.prepare()
        prep.url = url  # prevent re-encoding
        r = s.send(prep, timeout=15)
        print(f"tw_market_data_bulk status: {r.status_code}, url: {url[:100]}")
        if r.status_code == 200:
            items = r.json().get("data",{}).get("items",[])
            print(f"tw_market_data_bulk got {len(items)} items")
            return {item["symbol"]: item for item in items if "symbol" in item}
    except Exception as e:
        print(f"tw_market_data_bulk error: {e}")
    return {}

@app.route("/api/research/warnings", methods=["POST"])
def research_warnings():
    """
    Quick warning scan for a list of symbols.
    Returns {symbol: {signal, reasons}} for each.
    Runs in parallel, lightweight — headlines + basic info only.
    """
    symbols = request.json.get("symbols", [])
    if not symbols:
        return jsonify({})

    def scan_one(sym):
        try:
            import yfinance as yf
            tk   = yf.Ticker(sym)
            info = {}
            try: info = tk.info or {}
            except Exception: pass

            news_items = []
            try: news_items = tk.news or []
            except Exception: pass

            all_titles = " ".join(
                (n.get("title","") for n in news_items[:10])
            ).lower()

            warnings = []

            # Earnings miss / guidance cut / downgrade / lawsuit
            if any(w in all_titles for w in ["miss","misses","missed","fell short","below estimates"]):
                warnings.append("Recent earnings miss")
            if any(w in all_titles for w in ["cut guidance","lowered guidance","lowers guidance","cut outlook","reduced outlook"]):
                warnings.append("Guidance cut")
            if any(w in all_titles for w in ["downgrade","downgraded","underperform","sell rating","underweight"]):
                warnings.append("Analyst downgrade")
            if any(w in all_titles for w in ["lawsuit","sued","sues","litigation","class action","ftc","sec charges","doj","settlement"]):
                warnings.append("Legal / regulatory risk")
            if any(w in all_titles for w in ["layoff","layoffs","job cut","restructur"]):
                warnings.append("Layoffs / restructuring")
            if any(w in all_titles for w in ["short squeeze","squeeze"]):
                warnings.append("Squeeze dynamics")

            # Analyst consensus negative
            rec = info.get("recommendationKey","")
            if rec in ("sell","strong_sell","underperform"):
                warnings.append(f"Analyst consensus: {rec.replace('_',' ')}")

            # Target below price
            target = info.get("targetMeanPrice")
            price  = info.get("currentPrice") or info.get("regularMarketPrice")
            if target and price and float(target) < float(price) * 0.92:
                warnings.append(f"Analyst target ${float(target):.0f} below current ${float(price):.0f}")

            # High beta
            beta = info.get("beta")
            if beta and float(beta) > 2.0:
                warnings.append(f"High beta {float(beta):.1f} — volatile")

            return sym, warnings
        except Exception as e:
            return sym, []

    from concurrent.futures import ThreadPoolExecutor
    results = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for sym, warnings in ex.map(scan_one, symbols):
            if warnings:
                results[sym] = warnings

    return jsonify(results)

@app.route("/api/squeeze", methods=["POST"])
def squeeze_screen():
    """
    Squeeze Watch screen — identifies potential short squeeze candidates.
    Runs on demand only. Checks: short float, days to cover, volume ratio,
    borrow rate, price action.
    """
    data    = request.json or {}
    custom  = [t.strip().upper() for t in data.get("custom","").split(",") if t.strip()]
    token   = session.get("tw_token")

    # Build universe — all sectors + watchlists + custom
    tickers = list(dict.fromkeys(
        [t for s in UNIVERSE.values() for t in s] + custom
    ))

    # Add watchlist symbols
    if token:
        try:
            wls = tw_watchlists(token)
            for wl in wls:
                for sym in wl.get("symbols", []):
                    if sym and sym not in tickers:
                        tickers.append(sym)
        except Exception:
            pass

    # Batch TW metrics for borrow rate
    tw_metrics_cache = {}
    if token:
        try:
            tw_metrics_cache = tw_market_metrics(token, tickers)
        except Exception:
            pass

    def score_ticker(sym):
        try:
            import yfinance as yf
            tk   = yf.Ticker(sym)
            info = {}
            try: info = tk.info or {}
            except Exception: pass

            price     = float(info.get("currentPrice") or info.get("regularMarketPrice") or 0)
            if price < 5:
                return None  # skip penny stocks

            short_pct = float(info.get("shortPercentOfFloat") or 0)
            days_cover= float(info.get("shortRatio") or 0)
            avg_vol   = float(info.get("averageVolume") or 1)
            cur_vol   = float(info.get("volume") or info.get("regularMarketVolume") or 0)
            prev_close= float(info.get("previousClose") or price)
            change_pct= ((price - prev_close) / prev_close * 100) if prev_close else 0

            vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 0

            # Borrow rate from tastytrade
            borrow_rate = 0.0
            m = tw_metrics_cache.get(sym, {})
            try:
                borrow_rate = float(m.get("borrow-rate") or 0)
            except Exception:
                pass

            # Score 0-5
            score = 0
            signals = []

            if short_pct >= 0.15:
                score += 1
                signals.append(f"Short float {short_pct*100:.1f}%")
            elif short_pct >= 0.10:
                score += 0.5
                signals.append(f"Short float {short_pct*100:.1f}% (moderate)")

            if days_cover >= 5:
                score += 1
                signals.append(f"Days to cover {days_cover:.1f}")
            elif days_cover >= 3:
                score += 0.5
                signals.append(f"Days to cover {days_cover:.1f} (moderate)")

            if vol_ratio >= 3:
                score += 1.5
                signals.append(f"Volume {vol_ratio:.1f}x average")
            elif vol_ratio >= 2:
                score += 1
                signals.append(f"Volume {vol_ratio:.1f}x average")
            elif vol_ratio >= 1.5:
                score += 0.5
                signals.append(f"Volume {vol_ratio:.1f}x average")

            if borrow_rate >= 50:
                score += 1.5
                signals.append(f"Borrow rate {borrow_rate:.0f}% (very high)")
            elif borrow_rate >= 20:
                score += 1
                signals.append(f"Borrow rate {borrow_rate:.0f}% (elevated)")
            elif borrow_rate >= 5:
                score += 0.5
                signals.append(f"Borrow rate {borrow_rate:.1f}%")

            if change_pct >= 5:
                score += 1
                signals.append(f"Up {change_pct:.1f}% today")
            elif change_pct >= 2:
                score += 0.5
                signals.append(f"Up {change_pct:.1f}% today")
            elif change_pct < 0:
                score -= 0.5  # negative price action reduces score

            if score < 1:
                return None  # not interesting enough

            return {
                "symbol":      sym,
                "price":       round(price, 2),
                "score":       round(score, 1),
                "short_pct":   round(short_pct * 100, 1),
                "days_cover":  round(days_cover, 1),
                "vol_ratio":   round(vol_ratio, 1),
                "borrow_rate": round(borrow_rate, 1),
                "change_pct":  round(change_pct, 2),
                "signals":     signals,
            }
        except Exception as e:
            return None

    from concurrent.futures import ThreadPoolExecutor
    results = []
    with ThreadPoolExecutor(max_workers=15) as ex:
        for r in ex.map(score_ticker, tickers):
            if r is not None:
                results.append(r)

    results.sort(key=lambda x: -x["score"])
    return jsonify({"results": results[:30]})  # top 30

@app.route("/api/squeeze/state")
def squeeze_state_api():
    """Return current squeeze watch state for the frontend."""
    short_updated = squeeze_state["short_updated"]
    reddit_updated = squeeze_state["reddit_updated"]
    # Filter to meaningful scores only - use live_score if available, else base_score
    filtered_watch = [w for w in squeeze_state["on_watch"]
                      if (w.get("live_score") or w.get("base_score",0)) >= 3.0]
    return jsonify({
        "on_watch":       filtered_watch,
        "short_count":    len(squeeze_state["high_short_list"]),
        "reddit_enabled": squeeze_state["reddit_enabled"],
        "short_updated":  short_updated.strftime("%Y-%m-%d %H:%M") if short_updated else None,
        "reddit_updated": reddit_updated.strftime("%Y-%m-%d %H:%M") if reddit_updated else None,
        "reddit_count":   len(squeeze_state["reddit_mentions"]),
    })

@app.route("/api/squeeze/refresh-live", methods=["POST"])
def squeeze_refresh_live():
    """Manually trigger live data refresh for on_watch tickers."""
    refresh_live_squeeze_data()
    return jsonify({"ok": True, "count": len(squeeze_state["on_watch"])})

@app.route("/api/squeeze/configure-reddit", methods=["POST"])
def configure_reddit():
    """Store Reddit API credentials and enable Reddit scanning."""
    data = request.json or {}
    client_id     = data.get("client_id","").strip()
    client_secret = data.get("client_secret","").strip()
    if client_id and client_secret:
        os.environ["REDDIT_CLIENT_ID"]     = client_id
        os.environ["REDDIT_CLIENT_SECRET"] = client_secret
        squeeze_state["reddit_enabled"] = True
        return jsonify({"ok": True, "message": "Reddit configured — will scan on next cycle"})
    return jsonify({"ok": False, "error": "Missing credentials"}), 400

@app.route("/api/debug/margin/<symbol>")
def debug_margin(symbol):
    token = session.get("tw_token")
    acct  = session.get("account")
    if not token or not acct:
        return jsonify({"error": "Not logged in"})
    try:
        r = requests.get(
            f"{TW_BASE}/accounts/{acct}/margin-requirements/{symbol.upper()}/effective",
            headers=tw_headers(token), timeout=10)
        return jsonify({"status": r.status_code, "body": r.json()})
    except Exception as e:
        return jsonify({"error": str(e)})

# ── Tastytrade MCP Replica endpoints ─────────────────────────────────────────

@app.route("/api/tw/accounts")
def tw_get_accounts():
    token = session.get("tw_token")
    if not token: return jsonify({"error": "Not logged in"}), 401
    try:
        r = requests.get(f"{TW_BASE}/customers/me/accounts",
                         headers=tw_headers(token), timeout=10)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/tw/balances/<account>")
def tw_get_balances(account):
    token = session.get("tw_token")
    if not token: return jsonify({"error": "Not logged in"}), 401
    try:
        r = requests.get(f"{TW_BASE}/accounts/{account}/balances",
                         headers=tw_headers(token), timeout=10)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/tw/positions/<account>")
def tw_get_positions(account):
    token = session.get("tw_token")
    if not token: return jsonify({"error": "Not logged in"}), 401
    symbol = request.args.get("symbol")
    try:
        url = f"{TW_BASE}/accounts/{account}/positions"
        if symbol: url += f"?underlying-symbol={symbol}"
        r = requests.get(url, headers=tw_headers(token), timeout=10)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/tw/orders/<account>")
def tw_get_orders(account):
    token = session.get("tw_token")
    if not token: return jsonify({"error": "Not logged in"}), 401
    status = request.args.get("status", "")
    try:
        url = f"{TW_BASE}/accounts/{account}/orders"
        if status: url += f"?status={status}"
        r = requests.get(url, headers=tw_headers(token), timeout=10)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/tw/nlv-history/<account>")
def tw_get_nlv_history(account):
    token = session.get("tw_token")
    if not token: return jsonify({"error": "Not logged in"}), 401
    time_back = request.args.get("time_back", "1m")
    try:
        r = requests.get(f"{TW_BASE}/accounts/{account}/net-liq-history",
                         params={"time-back": time_back},
                         headers=tw_headers(token), timeout=10)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/tw/transactions/<account>")
def tw_get_transactions(account):
    token = session.get("tw_token")
    if not token: return jsonify({"error": "Not logged in"}), 401
    start = request.args.get("start_date", (date.today() - timedelta(days=30)).isoformat())
    end   = request.args.get("end_date", date.today().isoformat())
    try:
        r = requests.get(f"{TW_BASE}/accounts/{account}/transactions",
                         params={"start-date": start, "end-date": end, "per-page": 250},
                         headers=tw_headers(token), timeout=15)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/tw/metrics")
def tw_get_metrics():
    token = session.get("tw_token")
    if not token: return jsonify({"error": "Not logged in"}), 401
    symbols = request.args.get("symbols", "")
    try:
        r = requests.get(f"{TW_BASE}/market-metrics",
                         params={"symbols": symbols},
                         headers=tw_headers(token), timeout=15)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/tw/option-chain/<symbol>")
def tw_get_option_chain(symbol):
    token = session.get("tw_token")
    if not token: return jsonify({"error": "Not logged in"}), 401
    try:
        r = requests.get(f"{TW_BASE}/option-chains/{symbol}/nested",
                         headers=tw_headers(token), timeout=15)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/tw/watchlists")
def tw_get_watchlists_api():
    token = session.get("tw_token")
    if not token: return jsonify({"error": "Not logged in"}), 401
    try:
        r = requests.get(f"{TW_BASE}/watchlists",
                         headers=tw_headers(token), timeout=10)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)})


# ── MCP Bridge endpoints (use stored token, no browser session needed) ────────

@app.route("/mcp/status")
def mcp_status():
    """Check if a tastytrade token is stored and valid."""
    token = get_stored_token()
    if not token:
        return jsonify({"ok": False, "error": "No token stored — log in via the web UI first"})
    # Quick validation
    try:
        r = requests.get(f"{TW_BASE}/customers/me/accounts",
                         headers=tw_headers(token), timeout=8)
        if r.status_code == 200:
            accts = r.json()["data"]["items"]
            margin = next((a["account"]["account-number"] for a in accts
                           if not a["account"].get("is-closed")), None)
            return jsonify({"ok": True, "account": margin})
        return jsonify({"ok": False, "error": f"Token invalid: HTTP {r.status_code}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/mcp/balances/<account>")
def mcp_balances(account):
    token = get_stored_token()
    if not token: return jsonify({"error": "Not authenticated"}), 401
    try:
        r = requests.get(f"{TW_BASE}/accounts/{account}/balances",
                         headers=tw_headers(token), timeout=10)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/mcp/positions/<account>")
def mcp_positions(account):
    token = get_stored_token()
    if not token: return jsonify({"error": "Not authenticated"}), 401
    symbol = request.args.get("symbol")
    try:
        url = f"{TW_BASE}/accounts/{account}/positions"
        if symbol: url += f"?underlying-symbol={symbol}"
        r = requests.get(url, headers=tw_headers(token), timeout=10)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/mcp/orders/<account>")
def mcp_orders(account):
    token = get_stored_token()
    if not token: return jsonify({"error": "Not authenticated"}), 401
    try:
        r = requests.get(f"{TW_BASE}/accounts/{account}/orders",
                         headers=tw_headers(token), timeout=10)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/mcp/metrics")
def mcp_metrics():
    token = get_stored_token()
    if not token: return jsonify({"error": "Not authenticated"}), 401
    symbols = request.args.get("symbols", "")
    try:
        r = requests.get(f"{TW_BASE}/market-metrics",
                         params={"symbols": symbols},
                         headers=tw_headers(token), timeout=15)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/mcp/option-chain/<symbol>")
def mcp_option_chain(symbol):
    token = get_stored_token()
    if not token: return jsonify({"error": "Not authenticated"}), 401
    try:
        r = requests.get(f"{TW_BASE}/option-chains/{symbol}/nested",
                         headers=tw_headers(token), timeout=15)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/mcp/transactions/<account>")
def mcp_transactions(account):
    token = get_stored_token()
    if not token: return jsonify({"error": "Not authenticated"}), 401
    start = request.args.get("start_date", (date.today() - timedelta(days=30)).isoformat())
    end   = request.args.get("end_date", date.today().isoformat())
    try:
        r = requests.get(f"{TW_BASE}/accounts/{account}/transactions",
                         params={"start-date": start, "end-date": end, "per-page": 250},
                         headers=tw_headers(token), timeout=15)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/mcp/nlv-history/<account>")
def mcp_nlv_history(account):
    token = get_stored_token()
    if not token: return jsonify({"error": "Not authenticated"}), 401
    time_back = request.args.get("time_back", "1m")
    try:
        r = requests.get(f"{TW_BASE}/accounts/{account}/net-liq-history",
                         params={"time-back": time_back},
                         headers=tw_headers(token), timeout=10)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/mcp/watchlists")
def mcp_watchlists():
    token = get_stored_token()
    if not token: return jsonify({"error": "Not authenticated"}), 401
    try:
        r = requests.get(f"{TW_BASE}/watchlists",
                         headers=tw_headers(token), timeout=10)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)})


# ── MCP SSE Protocol endpoints (for Claude custom connector) ──────────────────
# Implements Model Context Protocol over Server-Sent Events
# Add as custom connector: https://put-screener1-production.up.railway.app/mcp

import time, uuid

MCP_TOOLS = [
    {
        "name": "get_balances",
        "description": "Get tastytrade account balances — NLV, buying power, deployed capital",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_number": {"type": "string", "description": "Account number, default 5WV80235"}
            }
        }
    },
    {
        "name": "get_positions",
        "description": "Get open positions with P&L and Greeks",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_number": {"type": "string"},
                "symbol": {"type": "string", "description": "Optional: filter by underlying symbol"}
            }
        }
    },
    {
        "name": "get_orders",
        "description": "Get live and historical orders",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_number": {"type": "string"}
            }
        }
    },
    {
        "name": "get_market_metrics",
        "description": "Get IV rank, IV percentile, beta, liquidity for symbols",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbols": {"type": "string", "description": "Comma-separated symbols e.g. AAPL,TSLA"}
            },
            "required": ["symbols"]
        }
    },
    {
        "name": "get_option_chain",
        "description": "Get full option chain for a symbol",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"}
            },
            "required": ["symbol"]
        }
    },
    {
        "name": "get_transaction_history",
        "description": "Get fills, fees, and transaction history",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_number": {"type": "string"},
                "start_date": {"type": "string"},
                "end_date": {"type": "string"}
            }
        }
    },
    {
        "name": "get_net_liquidating_value_history",
        "description": "Get account equity curve / NLV history",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_number": {"type": "string"},
                "time_back": {"type": "string", "description": "1d,1w,1m,3m,6m,1y,all"}
            }
        }
    },
    {
        "name": "get_watchlists",
        "description": "Get user watchlists from tastytrade",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "check_status",
        "description": "Check if tastytrade connection is active",
        "inputSchema": {"type": "object", "properties": {}}
    }
]

def dispatch_mcp_tool(name, arguments):
    """Execute a tool call and return result."""
    acct = arguments.get("account_number", "5WV80235")
    token = get_stored_token()
    if not token:
        return {"error": "Not authenticated — log into the screener web UI first at the Railway URL"}

    try:
        if name == "check_status":
            r = requests.get(f"{TW_BASE}/customers/me/accounts",
                             headers=tw_headers(token), timeout=8)
            if r.status_code == 200:
                return {"status": "connected", "accounts": [a["account"]["account-number"]
                        for a in r.json()["data"]["items"]]}
            return {"status": "token_invalid", "http_status": r.status_code}

        elif name == "get_balances":
            r = requests.get(f"{TW_BASE}/accounts/{acct}/balances",
                             headers=tw_headers(token), timeout=10)
            return r.json()

        elif name == "get_positions":
            url = f"{TW_BASE}/accounts/{acct}/positions"
            if arguments.get("symbol"):
                url += f"?underlying-symbol={arguments['symbol']}"
            r = requests.get(url, headers=tw_headers(token), timeout=10)
            return r.json()

        elif name == "get_orders":
            r = requests.get(f"{TW_BASE}/accounts/{acct}/orders",
                             headers=tw_headers(token), timeout=10)
            return r.json()

        elif name == "get_market_metrics":
            r = requests.get(f"{TW_BASE}/market-metrics",
                             params={"symbols": arguments.get("symbols","")},
                             headers=tw_headers(token), timeout=15)
            return r.json()

        elif name == "get_option_chain":
            r = requests.get(f"{TW_BASE}/option-chains/{arguments.get('symbol','SPY')}/nested",
                             headers=tw_headers(token), timeout=15)
            return r.json()

        elif name == "get_transaction_history":
            params = {}
            if arguments.get("start_date"): params["start-date"] = arguments["start_date"]
            if arguments.get("end_date"):   params["end-date"]   = arguments["end_date"]
            params["per-page"] = 250
            r = requests.get(f"{TW_BASE}/accounts/{acct}/transactions",
                             params=params, headers=tw_headers(token), timeout=15)
            return r.json()

        elif name == "get_net_liquidating_value_history":
            r = requests.get(f"{TW_BASE}/accounts/{acct}/net-liq-history",
                             params={"time-back": arguments.get("time_back","1m")},
                             headers=tw_headers(token), timeout=10)
            return r.json()

        elif name == "get_watchlists":
            r = requests.get(f"{TW_BASE}/watchlists",
                             headers=tw_headers(token), timeout=10)
            return r.json()

        else:
            return {"error": f"Unknown tool: {name}"}

    except Exception as e:
        return {"error": str(e)}

@app.route("/mcp/sse")
def mcp_sse_endpoint():
    """SSE endpoint for MCP protocol — add this URL as custom connector in Claude."""
    from flask import Response, stream_with_context

    def generate():
        # Send endpoint info
        session_id = str(uuid.uuid4())
        msg = json.dumps({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {}
        })
        yield f"data: {msg}\n\n"

        # Keep alive
        while True:
            time.sleep(15)
            yield f": keepalive\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*"
        }
    )

@app.route("/mcp", methods=["GET", "POST", "OPTIONS"])
def mcp_endpoint():
    """Main MCP JSON-RPC endpoint."""
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return resp

    if request.method == "GET":
        # Return MCP server info
        resp = jsonify({
            "name": "tastytrade",
            "version": "1.0.0",
            "description": "Tastytrade brokerage account tools",
            "capabilities": {"tools": {}}
        })
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    # POST — handle JSON-RPC requests
    try:
        body = request.get_json(force=True)
    except Exception:
        return jsonify({"jsonrpc":"2.0","error":{"code":-32700,"message":"Parse error"},"id":None}), 400

    method  = body.get("method","")
    params  = body.get("params", {})
    req_id  = body.get("id")

    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "tastytrade", "version": "1.0.0"},
            "capabilities": {"tools": {}}
        }
    elif method == "tools/list":
        result = {"tools": MCP_TOOLS}

    elif method == "tools/call":
        tool_name = params.get("name","")
        arguments  = params.get("arguments", {})
        data = dispatch_mcp_tool(tool_name, arguments)
        result = {
            "content": [{"type": "text", "text": json.dumps(data, indent=2)}]
        }

    elif method == "notifications/initialized":
        resp = jsonify({"jsonrpc":"2.0","id":req_id,"result":{}})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    else:
        resp = jsonify({
            "jsonrpc":"2.0",
            "error":{"code":-32601,"message":f"Method not found: {method}"},
            "id": req_id
        })
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    resp = jsonify({"jsonrpc":"2.0","result":result,"id":req_id})
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp

if __name__ == "__main__":
    start_scheduler()
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", debug=False, port=port)
