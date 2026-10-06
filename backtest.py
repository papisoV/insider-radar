"""Backtest step 0 / part B: does the SCREEN have information?

Question: do filings my ranking pushes beat random purchases from the same
day? If yes, the filtering is worth paying for. If no, the product is just
labour and the subscription model does not stand up.

Design (fixed BEFORE seeing results - see JUDGE below):

  population  all P-code purchases on a given day, collapsed per issuer
  screened    the ones that pass the same gate render_radar uses
              (>= $100k AND within-day percentile >= 0.75)
  control     random draws from the same day's population, same count,
              WITHOUT the screen - so any difference is the screen, not the
              day, the market, or the sector.
  measure     forward return from the FILING date to T+N calendar days

Control is drawn from the same day on purpose: comparing screened names to
"the market" would confound the screen with whatever insider buying as a
whole did in that week. The question is specifically whether *my* ordering
adds anything over *buying everything*.

JUDGE (stated in advance, not chosen after seeing the numbers):
  The screen passes only if the screened mean beats the control mean by a
  margin larger than the control's own spread across repeated draws - i.e.
  the screened value must sit outside the bulk of the random distribution,
  not merely above its average. Concretely: percentile of screened mean
  within the control distribution >= 0.95.
  Anything below that is a NO-GO on the "the filter is the product" claim.

Windows are T+5/10/15/20. NOT 30/60/90 - every filing we have is less than
21 days old, so longer windows are in the future. Stated explicitly because
the original plan said 30/60/90 and that cannot be measured yet.
"""
import io
import json
import os
import random
import ssl
import sys
import time
import urllib.request
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
# Layout-agnostic: this file works both inside the working repo
# (radar/tools/) and in a flat clone where it sits at the top level.
for _p in (HERE, os.path.join(HERE, "tools"), os.path.dirname(HERE),
           os.path.join(os.path.dirname(HERE), "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import render_radar as R  # noqa: E402

DATA = os.path.join(HERE, "tmpW100")
os.makedirs(DATA, exist_ok=True)

# The published artifact is backtest_buys.json next to this file; the working
# repo keeps the intermediate file in a scratch dir. Take whichever exists.
_CANDIDATES = [os.path.join(HERE, "backtest_buys.json"),
               os.path.join(DATA, "buys_clean.json"),
               os.path.join(DATA, "backtest_buys.json")]
IN = next((p for p in _CANDIDATES if os.path.exists(p)), _CANDIDATES[0])
OUT = os.path.join(DATA, "backtest_result.json")
CACHE = os.path.join(DATA, "pricecache.json")

WINDOWS = (5, 10, 15, 20)
DRAWS = 2000          # control draws per (day, window)
SEED = 20261006       # fixed so the result is reproducible
PASS_PERCENTILE = 0.95

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/%s?period1=%d&period2=%d&interval=1d"
UA = "Mozilla/5.0"


def load_cache():
    if os.path.exists(CACHE):
        return json.load(open(CACHE, encoding="utf-8"))
    return {}


def save_cache(c):
    with open(CACHE, "w", encoding="utf-8") as fh:
        json.dump(c, fh, ensure_ascii=False)


def fetch_series(ticker, cache):
    """-> [(epoch, close)] sorted ascending, or None. Cached across runs."""
    if ticker in cache:
        return cache[ticker]
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    end = int(time.time())
    start = end - 400 * 86400
    try:
        req = urllib.request.Request(YAHOO % (ticker, start, end),
                                     headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=25, context=ctx) as r:
            data = json.loads(r.read())
        res = data["chart"]["result"][0]
        ts = res["timestamp"]
        close = res["indicators"]["quote"][0]["close"]
        ser = sorted((t, c) for t, c in zip(ts, close)
                     if c is not None)
        cache[ticker] = ser
    except Exception:
        cache[ticker] = None
    time.sleep(0.15)
    return cache[ticker]


def close_on_or_after(series, target_epoch):
    """First close at/after target. Insider filing days can be holidays, so
    an exact-match lookup would silently drop most samples."""
    for t, c in series:
        if t >= target_epoch:
            return c
    return None


def fwd_return(ticker, filed_iso, days, cache):
    """-> (return_pct, reason_skipped)."""
    ser = fetch_series(ticker, cache)
    if not ser:
        return None, "no_price_data"
    base_epoch = int(time.mktime(
        time.strptime(filed_iso + " 00:00:00", "%Y-%m-%d %H:%M:%S")))
    p0 = close_on_or_after(ser, base_epoch)
    p1 = close_on_or_after(ser, base_epoch + days * 86400)
    if p0 is None or p1 is None or p0 <= 0:
        return None, "window_in_future_or_gap"
    return 100.0 * (p1 - p0) / p0, ""


def collapse(buys):
    """Per-issuer totals: the same population render_radar ranks."""
    g = {}
    for b in buys:
        k = (b.get("ticker") or "").upper()
        g[k] = g.get(k, 0.0) + (b.get("notional_usd") or 0.0)
    return g


def screened_set(agg):
    """The gate from render_radar: >= floor AND top quartile of the day."""
    vals = sorted(agg.values())
    keep = set()
    for k, v in agg.items():
        if v < R.MIN_NOTIONAL_USD:
            continue
        if len(vals) >= R.MIN_SIGNALS_FOR_PERCENTILE and \
                R._percentile_rank(vals, v) < R.PUSH_PERCENTILE:
            continue
        keep.add(k)
    return keep


def main():
    data = json.load(open(IN, encoding="utf-8"))
    cache = load_cache()
    rng = random.Random(SEED)

    # Returns per (day, ticker, window)
    rets = {}
    missing = {}
    for day, buys in sorted(data.items()):
        buys = [b for b in buys if R.is_listed(b.get("ticker"))]
        agg = collapse(buys)
        for tk in agg:
            for w in WINDOWS:
                r, why = fwd_return(tk, day, w, cache)
                if r is None:
                    missing[why] = missing.get(why, 0) + 1
                    continue
                rets[(day, tk, w)] = r
        print("%s  issuers=%d  measured=%d  missing=%s"
              % (day, len(agg), sum(1 for k in rets if k[0] == day),
                 missing), flush=True)
        save_cache(cache)

    result = {"windows": {}, "meta": {
        "seed": SEED, "draws": DRAWS,
        "min_notional": R.MIN_NOTIONAL_USD,
        "push_percentile": R.PUSH_PERCENTILE,
        "missing": missing,
    }}

    for w in WINDOWS:
        screened_all, control_means = [], []
        per_day = {}
        for day, buys in sorted(data.items()):
            buys = [b for b in buys if R.is_listed(b.get("ticker"))]
            agg = collapse(buys)
            keep = screened_set(agg)
            have = {tk: rets[(day, tk, w)] for tk in agg
                    if (day, tk, w) in rets}
            if not have:
                continue
            scr = [v for tk, v in have.items() if tk in keep]
            if not scr:
                continue
            # Coverage check: if large screened names are much more likely to
            # HAVE price data than the rest of the day, "screened beat
            # control" could just mean "big-caps outperformed small-caps in
            # this window". Report both rates so the result can be read
            # honestly; the control is still drawn only from names that have
            # prices.
            scr_have = sum(1 for tk in keep if tk in have)
            scr_total = len(keep)
            all_have = len(have)
            all_total = len(agg)
            per_day[day] = {
                "n_universe": len(have), "n_screened": len(scr),
                "screened_mean": sum(scr) / len(scr),
                "universe_mean": sum(have.values()) / len(have),
                "n_screened_total": scr_total,
                "coverage_screened": (round(scr_have / scr_total, 3)
                                      if scr_total else None),
                "coverage_universe": (round(all_have / all_total, 3)
                                      if all_total else None),
            }
            screened_all.extend(scr)
            # Control: same day, same count, no screen.
            pool = list(have.values())
            for _ in range(DRAWS):
                pick = rng.sample(pool, min(len(scr), len(pool)))
                control_means.append(sum(pick) / len(pick))

        if not screened_all or not control_means:
            result["windows"][w] = {"error": "insufficient data"}
            continue

        s_mean = sum(screened_all) / len(screened_all)
        c_vals = sorted(control_means)
        below = sum(1 for v in c_vals if v < s_mean)
        pct = below / len(c_vals)
        result["windows"][w] = {
            "n_screened": len(screened_all),
            "screened_mean_pct": round(s_mean, 3),
            "control_mean_pct": round(sum(c_vals) / len(c_vals), 3),
            "control_p05": round(c_vals[int(0.05 * len(c_vals))], 3),
            "control_p95": round(c_vals[int(0.95 * len(c_vals))], 3),
            "screened_percentile_in_control": round(pct, 4),
            "verdict": "PASS" if pct >= PASS_PERCENTILE else "NO-GO",
            "per_day": per_day,
        }
        print("T+%d  screened=%.2f%% (n=%d)  control=%.2f%%  pct=%.3f  %s"
              % (w, s_mean, len(screened_all),
                 sum(c_vals) / len(c_vals), pct,
                 result["windows"][w]["verdict"]), flush=True)

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    print("[wrote] %s" % OUT)


if __name__ == "__main__":
    main()
