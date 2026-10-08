"""The product layer: turn parsed Form 4 filings into ranked insider purchases.

W99 thought the only thing worth paying for was the SCREEN. That was then
tested and it is false - see README "Does the screen work? Measured. No.":
the ranking did not beat same-day random draws at T+5/10/15/20. What this
layer actually does is reduce ~530 filings/day to the handful with real money
in them, which saves reading time and nothing else.

Wording follows from that. Never "signal", "alert", "pick", "worth watching",
"conviction" - each implies the reader should act on being told, which is the
claim the measurement failed to support. Say what is IN the filing.

Why ANY screen is needed (measured, 45 filings / 184 transactions, 2026-10-06):
    code distribution: S 105 / F 32 / C 18 / P 11 / J 9 / M 8 / A 1
    filing level    : BUY 5 / SELL 21 / neutral 19   ->  buy : rest ~= 1 : 8
So pushing everything is pushing noise: ~9 in 10 filings are sales, grants,
exercises and tax withholding, not money going in. Only P (purchase) is
"their own money buying". S is often routine 10b5-1. F/M/A/C/J say nothing.
This filters by VOLUME, not by quality.

Scoring model (all weights are explicit constants, no magic inline numbers):
    notional   = shares * price            (missing price -> unvaluable, kept
                                            but flagged, never silently dropped)
    role bonus = CEO/CFO/... > director > 10% owner > officer > other
    cluster    = several distinct insiders buying the same ticker in window
    tier       = BLOCKBUSTER >= 1M / NOTABLE >= 250k / ROUTINE below

Usage:
    python tools/insider_filter.py --days 20 --limit 60
    python tools/insider_filter.py --days 20 --min-notional 1000000
    python tools/insider_filter.py --file tmpW97/form4_sample.json
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from form4_parse import parse_form4  # noqa: E402
from form4_pipeline import discover, fetch, filing_url  # noqa: E402

# --- explicit thresholds (USD) -------------------------------------------
BLOCKBUSTER_USD = 1_000_000      # matches what the top-rated app alerts on
NOTABLE_USD = 250_000
MIN_NOTIONAL_USD = 25_000        # below this a "buy" is noise

# Highest per-share price that can plausibly be a REAL per-share price.
# Set 2026-10-08 after SLBT (Ching-Dong Wang, filed 2026-10-01) put its
# AGGREGATE price into <transactionPricePerShare>: 4,545,306 shares @
# "2272653", really US$2,272,653 for the whole transfer per its own footnote
# F2. Multiplied naively -> $10.33 TRILLION, which alone produced the bogus
# "$10.3T BLOCKBUSTER day" reading. Above this line the filing has a unit
# error, so the leg is unvaluable rather than enormous.
MAX_SANE_PRICE_PER_SHARE = 100_000.0

# --- role weights ---------------------------------------------------------
ROLE_TITLES = (
    ("chief executive", 40), ("ceo", 40),
    ("chief financial", 32), ("cfo", 32),
    ("chief operating", 26), ("coo", 26),
    ("president", 24), ("chairman", 24),
    ("chief technology", 18), ("cto", 18),
    ("chief legal", 14), ("general counsel", 14),
    ("chief accounting", 14), ("controller", 12),
    ("chief", 16), ("executive vice", 12), ("evp", 12),
    ("senior vice", 8), ("svp", 8), ("vice president", 6), ("vp", 6),
)
DIRECTOR_POINTS = 20
TEN_PCT_POINTS = 28
OFFICER_POINTS = 10

CLUSTER_POINTS = 15             # per extra distinct insider on same ticker
MAX_SCORE = 100


def _bool(v):
    """Form 4 flags arrive as '1'/'0', 'true'/'false', or ''."""
    s = str(v or "").strip().lower()
    return s in ("1", "true", "yes", "y")


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def role_score(rec):
    pts = 0
    title = (rec.get("officer_title") or "").lower()
    for token, val in ROLE_TITLES:
        if token in title:
            pts = max(pts, val)
            break
    if _bool(rec.get("is_director")):
        pts = max(pts, DIRECTOR_POINTS)
    if _bool(rec.get("is_ten_pct_owner")):
        pts = max(pts, TEN_PCT_POINTS)
    if _bool(rec.get("is_officer")) and pts == 0:
        pts = OFFICER_POINTS
    return pts


def buy_transactions(rec):
    """Only purchase legs (SEC code P). See form4_parse note: P covers open
    market OR private purchases, of non-derivative OR derivative securities,
    so call it a purchase - not an open-market buy - in user-facing copy."""
    out = []
    for t in (rec.get("transactions") or []):
        if (t.get("code") or "").strip().upper() != "P":
            continue
        shares = _num(t.get("shares"))
        price = _num(t.get("price"))
        notional = None
        reliable = True
        if shares is not None and price is not None and price > 0:
            if flag_price_anomaly(price, shares):
                # Unit error in the filing: the price field holds an aggregate.
                # Keep the leg but refuse to value it - inventing a trillion
                # dollar "purchase" is worse than admitting we cannot price it.
                reliable = False
            else:
                notional = shares * price
        out.append({
            "date": t.get("date"),
            "shares": shares,
            "price": price,
            "notional": notional,
            "owned_after": _num(t.get("owned_after")),
            "valuable": notional is not None,
            "price_anomaly": not reliable,
        })
    return out


def flag_price_anomaly(price, shares):
    """True when `price` cannot be a per-share price.

    Guards against filings that put the AGGREGATE price in the per-share field
    (see MAX_SANE_PRICE_PER_SHARE). Missing/None inputs are unvaluable, not
    anomalous - they are handled downstream, so do not flag them here.
    """
    if price is None or shares is None:
        return False
    try:
        return float(price) > MAX_SANE_PRICE_PER_SHARE
    except (TypeError, ValueError):
        return False


def evaluate(rec):
    """-> signal dict or None when the filing holds no purchase."""
    buys = buy_transactions(rec)
    if not buys:
        return None

    known = [b for b in buys if b["valuable"]]
    unknown = [b for b in buys if not b["valuable"]]
    notional = sum(b["notional"] for b in known)
    shares = sum(b["shares"] for b in buys if b["shares"] is not None)

    if known:
        vw_price = sum(b["price"] * b["shares"] for b in known) / max(
            sum(b["shares"] for b in known), 1e-9)
    else:
        vw_price = None

    if notional >= BLOCKBUSTER_USD:
        tier = "BLOCKBUSTER"
    elif notional >= NOTABLE_USD:
        tier = "NOTABLE"
    else:
        tier = "ROUTINE"

    # W99b: Form 4 on non-listed / no-symbol issuers yields ticker "N/A" or
    # "NONE". Those are unactionable - flag them rather than pushing noise.
    ticker = (rec.get("ticker") or "").strip()
    listed = ticker.upper() not in ("", "N/A", "NONE", "-", "NULL")

    return {
        "ticker": ticker,
        "listed": listed,
        "_legs": buys,
        "issuer": rec.get("issuer"),
        "insider": rec.get("insider"),
        "officer_title": rec.get("officer_title") or "",
        "is_director": _bool(rec.get("is_director")),
        "is_officer": _bool(rec.get("is_officer")),
        "is_ten_pct_owner": _bool(rec.get("is_ten_pct_owner")),
        "filed": rec.get("filed"),
        "period": rec.get("period"),
        "url": rec.get("url"),
        "n_buy_tx": len(buys),
        "n_unvaluable": len(unknown),
        "shares": round(shares, 2) if shares else None,
        "vw_price": round(vw_price, 4) if vw_price else None,
        "notional_usd": round(notional, 2) if known else None,
        "notional_complete": not unknown,
        "tier": tier,
        "role_score": role_score(rec),
        "score": 0,           # filled by rank()
        "cluster_size": 1,    # filled by rank()
        "why": [],            # filled by rank()
    }


def _econ_key(s):
    """Identity of the underlying trade, not of the filer.

    W99b: related filers file SEPARATE Form 4s for one economic trade
    (measured: ADARx 2026-09-28 - GORDON CARL L and ORBIMED ADVISORS LLC filed
    two forms with byte-identical share/price/date legs, Gordon being OrbiMed's
    managing member). Naive per-filing output double-pushes the same trade.

    Caveat: identical legs across DIFFERENT 10%-owner funds can also be a real
    joint purchase by several unrelated buyers (that is what the cluster bonus
    is for). We only collapse when the legs match AND the owned-after position
    matches - two unrelated buyers would not report the same resulting stake.
    """
    legs = tuple(sorted(
        (t.get("date"), t.get("shares"), t.get("price"), t.get("owned_after"))
        for t in (s.get("_legs") or [])))
    return (s.get("ticker"), (s.get("period") or s.get("filed")), legs)


def collapse_related(signals):
    """Merge Form 4s that describe the same economic trade. Keeps the
    highest-role filer as the display name and records who else filed."""
    groups = {}
    for s in signals:
        groups.setdefault(_econ_key(s), []).append(s)
    out = []
    for _, group in groups.items():
        if len(group) == 1:
            out.append(group[0])
            continue
        group.sort(key=lambda x: -x["role_score"])
        head = dict(group[0])
        head["also_filed_by"] = [g["insider"] for g in group[1:]]
        head["n_related_filings"] = len(group)
        out.append(head)
    return out


def rank(signals):
    """Add cluster bonus, final score and human-readable reasons."""
    signals = collapse_related(signals)
    by_ticker = {}
    for s in signals:
        by_ticker.setdefault(s["ticker"], []).append(s)

    for ticker, group in by_ticker.items():
        distinct = {s["insider"] for s in group}
        for s in group:
            s["cluster_size"] = len(distinct)
            why = []
            if s["notional_usd"] is not None:
                why.append("$%s purchase (SEC code P)" % f"{s['notional_usd']:,.0f}")
            else:
                why.append("purchase, price undisclosed in filing")
            if s["is_ten_pct_owner"]:
                why.append("10% owner")
            if s["is_director"]:
                why.append("director")
            if s["officer_title"]:
                why.append(s["officer_title"])
            elif s["is_officer"]:
                why.append("officer")
            if len(distinct) > 1:
                why.append("%d distinct insiders bought %s in window"
                           % (len(distinct), ticker))
            if s["n_unvaluable"]:
                why.append("%d/%d legs lack a price"
                           % (s["n_unvaluable"], s["n_buy_tx"]))
            s["why"] = why

            # score: role + cluster + log-scaled size
            size = s["notional_usd"] or 0.0
            import math
            size_pts = 0.0
            if size >= MIN_NOTIONAL_USD:
                size_pts = min(40.0, 40.0 * math.log10(size / MIN_NOTIONAL_USD)
                               / math.log10(BLOCKBUSTER_USD / MIN_NOTIONAL_USD))
            cluster_pts = CLUSTER_POINTS * (len(distinct) - 1)
            s["score"] = round(min(MAX_SCORE,
                                   s["role_score"] + size_pts + cluster_pts), 1)

    signals.sort(key=lambda s: (-(s["score"] or 0),
                                -(s["notional_usd"] or 0)))
    return signals


def _count_file(path):
    with open(path, encoding="utf-8") as fh:
        return len(json.load(fh))


def from_file(path):
    with open(path, encoding="utf-8") as fh:
        recs = json.load(fh)
    out = [s for s in (evaluate(r) for r in recs) if s]
    return rank(out)


def from_live(days, limit, pages=1):
    found = discover(days, limit, pages=pages)
    recs = []
    for f in found:
        try:
            rec = parse_form4(fetch(f["url"]))
        except Exception:
            continue
        if not rec.get("issuer"):
            continue
        rec["filed"] = f["filed"]
        rec["url"] = f["url"]
        recs.append(rec)
    out = [s for s in (evaluate(r) for r in recs) if s]
    return rank(out), len(recs)


def render(signals, min_notional, show_all):
    shown = 0
    for s in signals:
        if not show_all and s["tier"] == "ROUTINE":
            continue
        if not show_all and not s.get("listed", True):
            continue
        if min_notional and (s["notional_usd"] or 0) < min_notional:
            continue
        shown += 1
        val = ("$%s" % f"{s['notional_usd']:,.0f}") if s["notional_usd"] else "n/a"
        print("%-12s %-24s %-22s score=%-5s %s"
              % (s["tier"], (s["issuer"] or "")[:24],
                 (s["insider"] or "")[:22], s["score"], val))
        print("             %s  %s" % (s["ticker"], " | ".join(s["why"])))
    return shown


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--pages", type=int, default=1)
    ap.add_argument("--file", default="")
    ap.add_argument("--min-notional", type=float, default=0)
    ap.add_argument("--show-all", action="store_true")
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    if a.file:
        signals = from_file(a.file)
        scanned = "file:%s" % a.file
        total = _count_file(a.file)
    else:
        signals, total = from_live(a.days, a.limit, a.pages)
        scanned = "%d filings / %dd" % (total, a.days)

    print("[filter] scanned=%s -> %d filings with a purchase"
          % (scanned, len(signals)))
    blocks = [s for s in signals if s["tier"] == "BLOCKBUSTER"]
    notable = [s for s in signals if s["tier"] == "NOTABLE"]
    routine = [s for s in signals if s["tier"] == "ROUTINE"]
    cut = round(100 * (total - len(signals)) / total) if total else 0
    unlisted = [s for s in signals if not s.get("listed", True)]
    print("[filter] BLOCKBUSTER=%d NOTABLE=%d ROUTINE=%d  (volume cut: %d%%)"
          % (len(blocks), len(notable), len(routine), cut))
    if unlisted:
        print("[filter] %d purchases have no tradeable ticker (N/A, NONE) -> "
              "hidden by default" % len(unlisted))
    print()
    n = render(signals, a.min_notional, a.show_all)
    print("\n[filter] shown=%d" % n)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(signals, fh, ensure_ascii=False, indent=1)
        print("[out] wrote %s" % a.json)


if __name__ == "__main__":
    main()
