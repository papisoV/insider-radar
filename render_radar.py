"""Phase 0 / step 2: render the Daily Insider Radar. LOCAL FILE ONLY.

Wording is load-bearing here, not cosmetics:
  * SEC defines code P as "Open market OR PRIVATE purchase, of non-derivative
    OR DERIVATIVE security" (verified against sec.gov ownership form codes).
    So never say "open-market buy" - the filing does not prove that.
  * Never state or imply a win rate. InsiderWatch publishes its own ledger:
    619 graded alerts, 48% hit rate, -0.2%/call vs the S&P. Anyone claiming
    "high win rate" gets refuted by public numbers.
    Allowed: "worth watching", "why this filing stands out".
    Banned: buy / sell / target / win rate / guaranteed alpha.
  * China: selling signals or alpha subscriptions crosses the illegal
    investment-advisory line. This renders public filing facts only.

Usage:
  python tools/render_radar.py --in tmpW100/poll/poll-*.json --top 5
"""

import argparse
import glob
import io
import json
import math as _math
import os
import re as _re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

BLOCKBUSTER_USD = 1_000_000
NOTABLE_USD = 250_000

# A purchase below this is not news. Measured 2026-10-06 on 61 pooled
# signals: the median P-code notional is $78k, so a $101 purchase is ~3
# orders of magnitude below typical and was ranking #2 on role score alone.
# Role score must not outrank money - see rank_key().
MIN_NOTIONAL_USD = 100_000

# Fund share classes are not equities a user can act on. Nasdaq mutual-fund
# symbols are 5 letters ending in X (XIVYX, ABCRX); CUSIP-like 9-char ids and
# anything with a dot (BRK.A is fine, but .U/.W/.R units are not) also slip
# through the "not N/A" check. Seen live: XIVYX at #2 with a $101 buy.
_FUND_RE = _re.compile(r"^[A-Z]{4}X$")          # mutual fund share class
_CUSIP_RE = _re.compile(r"^[A-Z0-9]{9}$")       # CUSIP, not a ticker


def is_listed(ticker):
    """Keep only symbols a user could actually look up and trade.

    Three things get dropped, each seen in live output:
      * placeholders   - non-listed issuers (private credit funds, closed-end
        vehicles) file with ticker NONE / N/A / "-". They dominated an early
        render because fund buys are large, and they are unactionable.
      * fund shares    - 5-letter symbols ending in X are mutual-fund share
        classes, not equities.
      * CUSIPs         - a 9-char alphanumeric id where a ticker should be.
    """
    t = str(ticker or "").strip().upper()
    if t in ("", "N/A", "NONE", "-", "NULL"):
        return False
    if _FUND_RE.match(t) or _CUSIP_RE.match(t):
        return False
    return True

ROLE_POINTS = [
    ("chief executive", 40), ("ceo", 40), ("chief financial", 32),
    ("cfo", 32), ("chief operating", 26), ("coo", 26), ("president", 24),
    ("chairman", 24), ("chief technology", 18), ("cto", 18),
    ("chief legal", 14), ("general counsel", 14), ("chief", 16),
    ("executive vice", 12), ("evp", 12), ("senior vice", 8), ("svp", 8),
    ("vice president", 6), ("vp", 6),
]


def role_score(title, is_director, is_ten_pct):
    t = (title or "").lower()
    pts = 0
    for key, val in ROLE_POINTS:
        if key in t:
            pts = max(pts, val)
            break
    if is_ten_pct:
        pts = max(pts, 28)
    elif is_director:
        pts = max(pts, 20)
    return pts


def why_lines(s, cluster_size):
    """Human-readable reasons. No predictions, no recommendations."""
    out = []
    n = s.get("notional_usd") or 0
    if n:
        out.append("$%s purchase (SEC code P)" % "{:,.0f}".format(n))
    else:
        out.append("purchase, price not disclosed in the filing")
    t = (s.get("officer_title") or "").strip()
    if t:
        out.append(t)
    elif s.get("is_director"):
        out.append("director")
    if s.get("is_ten_pct_owner"):
        out.append("10% owner")
    if cluster_size > 1:
        out.append("%d distinct insiders filed on %s in the window"
                   % (cluster_size, s.get("ticker") or "this issuer"))
    return out


MAX_TRADE_AGE_DAYS = 45


def _parse_date(x):
    try:
        from datetime import date
        return date.fromisoformat(str(x)[:10])
    except Exception:
        return None


def is_fresh(s, today=None):
    """Drop stale trade dates.

    Seen in live data: a filing submitted 2026-09-30 for a trade on 2026-04-21.
    That is months-old news wearing a new filing date, and it is not a "radar".
    """
    from datetime import date
    today = today or date.today()
    d = _parse_date(s.get("period"))
    if not d:
        return False
    return (today - d).days <= MAX_TRADE_AGE_DAYS


def render(signals, top):
    from datetime import date
    today = date.today()
    signals = [s for s in signals
               if is_listed(s.get("ticker")) and is_fresh(s, today)]
    by_ticker = {}
    for s in signals:
        by_ticker.setdefault(s.get("ticker") or "", []).append(s)

    ranked = []
    for ticker, group in by_ticker.items():
        distinct = {s.get("insider") for s in group}
        for s in group:
            ranked.append((role_score(s.get("officer_title"),
                                      s.get("is_director"),
                                      s.get("is_ten_pct_owner"))
                           + 15 * (len(distinct) - 1), s, len(distinct)))
    # Role score alone is the WRONG primary key: it put a $101 purchase at #2
    # (2026-10-06 live run) because the buyer was a President. Money is the
    # signal; role only breaks ties. Log-scale the notional so a $50M buy does
    # not flatten every other difference, then add role + cluster bonus.
    def rank_key(item):
        score, s, cluster = item
        n = s.get("notional_usd") or 0
        money = 0.0 if n <= 0 else 10.0 * _math.log10(n)
        return -(money + score)

    ranked.sort(key=rank_key)

    # Collapse to ONE entry per issuer: five directors of the same company
    # buying on the same day is one story ("FUL insiders bought"), not five.
    # Without this, one clustered issuer eats the whole radar (seen with FUL).
    grouped = {}
    for score, s, cluster in ranked:
        key = (s.get("ticker") or "").upper()
        if key in grouped:
            head = grouped[key]
            head["also"].append(s)
            head["total"] += (s.get("notional_usd") or 0)
            head["distinct"].add(s.get("insider"))
        else:
            grouped[key] = {"s": s, "score": score, "cluster": cluster,
                            "also": [], "total": (s.get("notional_usd") or 0),
                            "distinct": {s.get("insider")}}
    ordered = sorted(grouped.values(), key=lambda g: (-g["score"], -g["total"]))
    ordered = [g for g in ordered if g["total"] >= MIN_NOTIONAL_USD]

    L = []
    L.append("TODAY'S INSIDER RADAR")
    L.append("")
    L.append("Public SEC Form 4 filings. Selection and context only -")
    L.append("not investment advice, not a recommendation, no performance claim.")
    L.append("")
    for i, g in enumerate(ordered[:top], 1):
        s, score, cluster = g["s"], g["score"], g["cluster"]
        n = g["total"]
        L.append("#%d  %s" % (i, s.get("ticker") or "(no ticker)"))
        L.append("    %s" % (s.get("issuer") or ""))
        if n >= BLOCKBUSTER_USD:
            L.append("    $%s  [BLOCKBUSTER]" % "{:,.0f}".format(n))
        elif n >= NOTABLE_USD:
            L.append("    $%s  [NOTABLE]" % "{:,.0f}".format(n))
        else:
            L.append("    $%s" % "{:,.0f}".format(n))
        who = s.get("insider") or ""
        title = (s.get("officer_title") or "").strip()
        L.append("    %s%s" % (who, ("  - " + title) if title else ""))
        if len(g["distinct"]) > 1:
            L.append("    %d distinct insiders, $%s total"
                     % (len(g["distinct"]), "{:,.0f}".format(g["total"])))
        L.append("")
        L.append("    Why it stands out:")
        for w in why_lines(s, len(g["distinct"])):
            L.append("      - %s" % w)
        if g["also"]:
            L.append("    Also filed by: %s" % ", ".join(
                (x.get("insider") or "")[:24] for x in g["also"][:4]))
        if s.get("period"):
            L.append("    Trade date: %s   Filed: %s"
                     % (s.get("period"), s.get("filed") or ""))
        if s.get("url"):
            L.append("    SEC filing: %s" % s["url"])
        L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--out", default="tmpW100/radar.txt")
    a = ap.parse_args()

    paths = sorted(glob.glob(a.inp))
    if not paths:
        print("no input matched %s" % a.inp)
        return
    sigs = []
    for p in paths:
        sigs.extend(json.load(io.open(p, encoding="utf-8")))
    txt = render(sigs, a.top)
    io.open(a.out, "w", encoding="utf-8").write(txt)
    print(txt)
    print("[render] %d signals -> %s" % (len(sigs), a.out))


if __name__ == "__main__":
    main()
