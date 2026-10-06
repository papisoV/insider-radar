"""Is the negative result real, or is it instrument noise?

Three checks, in order:
  1. bucket returns by notional - if larger buys do worse, that is a finding
     about the signal, not about my screen
  2. per-day sign consistency - a real effect should be consistent in sign,
     not 5-up-3-down
  3. noise band - if the control's own p05..p95 spread is +-15pp, an effect
     of a few pp is unmeasurable at this n, and "NO-GO" would be overclaiming

Prints to a FILE, not stdout: backtest.py rewraps sys.stdout at import time
and closes the original buffer, so printing after that import raises
"ValueError: I/O operation on closed file".
"""
import json
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Layout-agnostic: works in the working repo (radar/tools/) and in a flat
# clone. backtest.py resolves the data files, so ask it rather than guessing.
for _p in (HERE, os.path.join(HERE, "tools"), os.path.dirname(HERE),
           os.path.join(os.path.dirname(HERE), "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import render_radar as R          # noqa: E402
import backtest as B              # noqa: E402

LOG = open(os.path.join(os.path.dirname(B.OUT), "diagnose.txt"), "w",
           encoding="utf-8")


def say(*a):
    LOG.write(" ".join(str(x) for x in a) + "\n")
    LOG.flush()


d = json.load(open(B.IN, encoding="utf-8"))
cache = B.load_cache()
res = json.load(open(B.OUT, encoding="utf-8"))

say("=== 1. return by notional bucket (T+5) ===")
buckets = {"(a) <$100k": [], "(b) $100k-$1M": [], "(c) >= $1M": []}
for day, buys in sorted(d.items()):
    buys = [b for b in buys if R.is_listed(b.get("ticker"))]
    agg = {}
    for b in buys:
        k = (b.get("ticker") or "").upper()
        agg[k] = agg.get(k, 0.0) + (b.get("notional_usd") or 0.0)
    for tk, v in agg.items():
        r, _ = B.fwd_return(tk, day, 5, cache)
        if r is None:
            continue
        if v < 100_000:
            buckets["(a) <$100k"].append(r)
        elif v < 1_000_000:
            buckets["(b) $100k-$1M"].append(r)
        else:
            buckets["(c) >= $1M"].append(r)

say("%-16s %5s %10s %10s" % ("bucket", "n", "mean%", "median%"))
for k, v in buckets.items():
    say("%-16s %5d %10.2f %10.2f"
        % (k, len(v), st.mean(v) if v else 0, st.median(v) if v else 0))
allv = [x for v in buckets.values() for x in v]
say("pooled           n=%d  mean=%.2f%%  median=%.2f%%"
    % (len(allv), st.mean(allv), st.median(allv)))

say("")
say("=== 2. per-day sign consistency ===")
for w in ("5", "10"):
    pd = res["windows"].get(w, {}).get("per_day", {})
    if not pd:
        continue
    up = sum(1 for p in pd.values() if p["screened_mean"] > p["universe_mean"])
    say("T+%s: screened beat same-day universe on %d of %d days"
        % (w, up, len(pd)))

say("")
say("=== 3. noise band vs effect size ===")
for w in ("5", "10", "15", "20"):
    v = res["windows"].get(w, {})
    if "error" in v:
        continue
    band = v["control_p95"] - v["control_p05"]
    say("T+%-2s n=%-3d  effect=%+.2fpp  control band (p05..p95)=%.1fpp"
        % (w, v["n_screened"],
           v["screened_mean_pct"] - v["control_mean_pct"], band))

say("")
say("=== 4. how much data would it take? ===")
v5 = res["windows"]["5"]
band = v5["control_p95"] - v5["control_p05"]
say("T+5 control band is %.1fpp wide at n=%d per day-pool."
    % (band, v5["n_screened"]))
say("A real effect worth selling is likely <= 3pp, so it needs roughly")
say("(band/effect)^2 x current n = (%.0f/3)^2 x %d = %.0f samples"
    % (band, v5["n_screened"], (band / 3.0) ** 2 * v5["n_screened"]))
LOG.close()
