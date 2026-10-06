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

# A purchase below this is not news, and no amount of relative ranking can
# rescue it. Measured 2026-10-06 on 61 pooled signals: median P-code notional
# is ~$78k, so this is a floor well below typical - it is not the filter, it
# is the point at which "best of a bad day" becomes dishonest to publish.
MIN_NOTIONAL_USD = 100_000

# With fewer than this many purchase signals in the day, a percentile is
# statistically meaningless (the top decile of 6 items is one item). Below
# the threshold we fall back to absolute ordering and say so in the output.
MIN_SIGNALS_FOR_PERCENTILE = 15

# Corroboration bonus for multiple insiders filing on the same issuer.
#
# CAP IS LOAD-BEARING, not a tuning knob. One order of magnitude of notional
# is worth 10 points (10 x log10). A bonus above 10 can therefore move an
# issuer across a whole order of magnitude, which inverts the ranking: a
# 3-insider $33M cluster (bonus 12.7) beat a single $157M filing (82.0 vs
# 75.2 on the money term) even though the money gap was 4.7x.
# So: capped at 4, which can never flip one order of magnitude.
CLUSTER_BONUS_SCALE = 4.0
CLUSTER_BONUS_MAX = 4.0

# Same reasoning for role. A raw role score of 40 (CEO) versus 20 (director)
# could move an issuer two orders of magnitude - measured live, a $2.1M CEO
# buy outranked a $27.5M director buy. Role is a tiebreaker within a
# magnitude, never a way to cross one.
ROLE_SCORE_MAX = 6.0
# ...and the raw role scale is 0..40, so this is the multiplier that maps it
# onto the 0..ROLE_SCORE_MAX range.
ROLE_CAP_PTS = ROLE_SCORE_MAX

# Relative gate: on a rich day, mid-pack is not news. A filing must sit in
# the top quarter of its own day's issuer totals to be pushed at all. Below
# MIN_SIGNALS_FOR_PERCENTILE signals the day is too small for a percentile to
# mean anything, and only the absolute floor applies.
PUSH_PERCENTILE = 0.75

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
    # Rescale to ROLE_SCORE_MAX. See ROLE_SCORE_MAX: role must never be worth
    # an order of magnitude of notional, so the raw 0..40 scale has to be
    # compressed before it enters the rank key.
    return ROLE_CAP_PTS * (pts / 40.0) if pts else 0.0


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


def _percentile_rank(values, v):
    """Where `v` sits in `values`, 0.0 (smallest) .. 1.0 (largest).

    Uses the midpoint convention: tied values share a rank, and the smallest
    is not 0.0 so that "bottom of the day" is distinguishable from "no data".
    """
    if not values:
        return 0.0
    n = len(values)
    below = sum(1 for x in values if x < v)
    tied = sum(1 for x in values if x == v)
    return (below + 0.5 * tied) / n


def render(signals, top):
    """-> text. Ranking is RELATIVE to the day, absolute only as a floor.

    Why relative: the daily purchase rate swings 2.8%-20.3% (measured over
    13 trading days, 6,872 filings). On 2026-10-02 there were 1502 filings
    and only 42 buys - a quarter-boundary 10b5-1 wave. An absolute $250k
    threshold behaves completely differently on a 42-buy day than on an
    81-buy day, so "top N" by absolute dollars ships routine filings on quiet
    days. Percentile-within-the-day is stable across both.

    Why still absolute: percentile alone would happily promote the best of a
    genuinely dull day. MIN_NOTIONAL_USD is the floor below which we print
    nothing at all rather than pretend.
    """
    from datetime import date
    today = date.today()
    signals = [s for s in signals
               if is_listed(s.get("ticker")) and is_fresh(s, today)]

    by_ticker = {}
    for s in signals:
        by_ticker.setdefault((s.get("ticker") or "").upper(), []).append(s)

    # Collapse to ONE entry per issuer first: five directors of the same
    # company buying on the same day is one story ("FUL insiders bought"),
    # not five. Without this one clustered issuer eats the whole radar.
    #
    # This MUST happen before percentiles are computed. Ranking a collapsed
    # total against a distribution of individual filings is a unit mismatch:
    # it put a $157M issuer at #5 behind a $499k one, because the collapsed
    # total was never in the population it was being compared against.
    def cluster_bonus(n_insiders):
        """LOG-scaled and capped corroboration bonus.

        The 2nd insider filing on the same issuer is real corroboration; the
        6th adds almost nothing over the 5th. Linear (15 x n) let a
        5-director $499k cluster outrank a $33M single filing, because +60
        exceeded the entire 0..100 percentile spread.
        """
        if n_insiders <= 1:
            return 0.0
        return min(CLUSTER_BONUS_MAX,
                   CLUSTER_BONUS_SCALE * _math.log(n_insiders, 2))

    grouped = {}
    for ticker, group in by_ticker.items():
        distinct = {s.get("insider") for s in group}
        # Head of the group = the largest single filing: that is the one whose
        # officer title and date we display.
        head = max(group, key=lambda s: s.get("notional_usd") or 0)
        grouped[ticker] = {
            "s": head,
            "also": [s for s in group if s is not head],
            "total": sum(s.get("notional_usd") or 0 for s in group),
            "distinct": distinct,
            "score": role_score(head.get("officer_title"),
                                head.get("is_director"),
                                head.get("is_ten_pct_owner"))
                     + cluster_bonus(len(distinct)),
            "cluster": len(distinct),
        }

    # Percentile over COLLAPSED issuer totals - same units as the values
    # being ranked against. Computing it over individual filings while
    # ranking collapsed totals is a unit mismatch: it put a $157M issuer at
    # #5 behind a $499k one.
    day_values = sorted(g["total"] for g in grouped.values())
    use_pct = len(day_values) >= MIN_SIGNALS_FOR_PERCENTILE

    # ORDER BY SIZE, GATE BY PERCENTILE.
    #
    # Percentile is a *gate*, not a sort key. Sorting by percentile throws
    # away magnitude: $157M and $33M are 4.7x apart but only ~5 percentile
    # points apart, so role and cluster bonuses (0..60) swamp the money term
    # and the order inverts. Measured: percentile-as-key put $2.1M above
    # $53.9M.
    #
    # What "relative" actually needs to mean: how many items pass the gate
    # adapts to the day, not which order they come out in. A 42-buy day
    # promotes fewer than an 81-buy day; within either, biggest is first.
    def rank_key(g):
        n = g["total"]
        money = 0.0 if n <= 0 else 10.0 * _math.log10(n)
        return -(money + g["score"])

    def worth_pushing(g):
        """Two independent conditions, both required."""
        if g["total"] < MIN_NOTIONAL_USD:
            # Absolute floor: never promote the best of a genuinely dull day.
            return False
        if use_pct and _percentile_rank(day_values, g["total"]) < PUSH_PERCENTILE:
            # Relative gate: on a rich day, mid-pack is not news.
            return False
        return True

    ordered = sorted(grouped.values(), key=rank_key)
    ordered = [g for g in ordered if worth_pushing(g)]

    L = []
    L.append("TODAY'S INSIDER RADAR")
    L.append("")
    L.append("Public SEC Form 4 filings. Selection and context only -")
    L.append("not investment advice, not a recommendation, no performance claim.")
    L.append("")

    if not ordered:
        # The empty state is a feature, not a fallback. Publishing "best of a
        # dull day" is how a filter becomes a noise source; saying nothing is
        # the honest output, and it is also the signal that tells the reader
        # the screen is working rather than broken.
        L.append("Nothing worth watching today.")
        L.append("")
        L.append("  %d purchase filings seen; the largest was $%s, below the"
                 % (len(day_values),
                    "{:,.0f}".format(max(day_values) if day_values else 0)))
        L.append("  $%s floor. Relative to a typical day this is a quiet one."
                 % "{:,.0f}".format(MIN_NOTIONAL_USD))
        L.append("")
        return "\n".join(L)

    # Tell the reader how the day compares, so "3 items" is not read as
    # "3 items every day". Uses absolute tiers, which are stable and
    # comparable across days even though ranking itself is relative.
    L.append("  %d purchase filings today. Ranked within the day%s."
             % (len(day_values),
                "" if use_pct else " (small sample - absolute order)"))
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
