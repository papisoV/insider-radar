"""Phase 0 / step 3: full-coverage 20-trading-day run.

Why this exists: the 8.3-signals/day figure rests on 6 weekdays sampled at
--max-offsets 4 (400 filings/day) while real days carry 1200-1502. That makes
8.3 a LOWER BOUND. This run walks every offset per day so the daily average is
real, and records seasonality (quarter-end 9-30 spiked to 14 BLOCKBUSTER).

Cost (measured, not guessed): 0.31 s/filing, ~1300 filings/day -> ~2.2 h wall
clock for 20 days. $0 cash. Runs unattended.

Usage:
  python tools/run_20d.py --days 20 --out tmpW100/daily20.json
"""

import argparse
import io
import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from form4_parse import parse_form4
from form4_pipeline import EFTS, filing_url

# NOTE: the two modules disagree on fetch()'s return type - form4_parse returns
# (body, status), form4_pipeline returns body alone. Import one and be explicit.
from form4_parse import fetch as fetch_body

UA = "sec-fact-monitor/1.0 (research script; contact: example.com)"


def fetch(url, timeout=40):
    """-> body text. Wraps form4_parse.fetch and drops the status code."""
    return fetch_body(url, timeout=timeout)[0]

# SEC asks for <=10 req/s; one filing per request, so a small sleep keeps us
# well inside that and avoids hammering a free public service.
SLEEP = 0.12


def day_filings(day, max_offsets=20, form="4"):
    """Every accession of `form` filed on `day`, walking `from=` until done.

    EFTS caps any single query at 100 hits (verified 2026-10-06) and `page=` is
    a no-op, so `from=` is the only offset. We stop when a slice returns <100.
    """
    seen, rows = set(), []
    for i in range(max_offsets):
        q = urllib.parse.urlencode({
            "forms": form, "startdt": day.isoformat(),
            "enddt": day.isoformat(), "from": i * 100,
        })
        try:
            data = json.loads(fetch("%s?%s" % (EFTS, q)))
        except Exception as exc:
            # Transient 5xx / truncated body. Never treat this as "no filings":
            # a quiet failure silently deletes a whole day from the average.
            print("  efts err off=%d %s" % (i * 100, str(exc)[:70]), flush=True)
            time.sleep(2.0)
            continue
        got = data.get("hits", {}).get("hits", [])
        if not got:
            break
        for h in got:
            acc = (h.get("_id") or "").split(":")[0]
            if not acc or acc in seen:
                continue
            seen.add(acc)
            rows.append(h)
        if len(got) < 100:
            break
    return rows


def evaluate(rows):
    """Fetch + parse every filing, keep the open-market-buy ones."""
    buys, ok, err = [], 0, 0
    for h in rows:
        src = h.get("_source", {})
        url = filing_url(h.get("_id", ""), src.get("ciks") or [])
        if not url:
            continue
        try:
            body = fetch(url)
        except Exception:
            err += 1
            continue
        rec = parse_form4(body)
        if not rec.get("issuer"):
            continue
        ok += 1
        legs = [t for t in (rec.get("transactions") or []) if t["code"] == "P"]
        if not legs:
            continue
        notional = 0.0
        for t in legs:
            try:
                notional += float(str(t.get("shares") or 0).replace(",", "")) * \
                    float(str(t.get("price") or 0).replace(",", ""))
            except (TypeError, ValueError):
                pass
        buys.append({
            "ticker": rec.get("ticker") or "",
            "issuer": rec.get("issuer"),
            "insider": rec.get("insider"),
            "officer_title": rec.get("officer_title") or "",
            "is_director": str(rec.get("is_director") or "").strip() in ("1", "true"),
            "is_ten_pct_owner": str(rec.get("is_ten_pct_owner") or "").strip() in ("1", "true"),
            "period": rec.get("period"),
            "filed": (src.get("file_date") or ""),
            "notional_usd": notional,
            "url": url,
        })
        time.sleep(SLEEP)
    return buys, ok, err


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--out", default="tmpW100/daily20.json")
    a = ap.parse_args()

    out_dir = os.path.dirname(a.out)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    out = []
    end = date.today()
    for i in range(a.days, 0, -1):
        day = end - timedelta(days=i)
        rows = day_filings(day)
        if not rows:
            print("%s  no filings (weekend/holiday)" % day, flush=True)
            continue
        buys, ok, err = evaluate(rows)
        bb = sum(1 for b in buys if b["notional_usd"] >= 1_000_000)
        nt = sum(1 for b in buys if 250_000 <= b["notional_usd"] < 1_000_000)
        rec = {
            "day": day.isoformat(),
            "filings_found": len(rows),
            "parsed": ok, "fetch_err": err,
            "buy_signals": len(buys),
            "blockbuster": bb, "notable": nt,
            "bb_notional": sum(b["notional_usd"] for b in buys
                               if b["notional_usd"] >= 1_000_000),
        }
        out.append(rec)
        print("%s  found=%-5d parsed=%-5d buys=%-4d BB=%-3d NOT=%-3d"
              % (rec["day"], rec["filings_found"], rec["parsed"],
                 rec["buy_signals"], bb, nt), flush=True)
        io.open(a.out, "w", encoding="utf-8").write(
            json.dumps(out, ensure_ascii=False, indent=1))

    wd = [r for r in out if r["filings_found"] > 0]
    if wd:
        n = len(wd)
        tf = sum(r["filings_found"] for r in wd)
        tb = sum(r["buy_signals"] for r in wd)
        print()
        print("SUMMARY days=%d  total_filings=%s  buys=%s (%.1f%%)"
              % (n, "{:,}".format(tf), "{:,}".format(tb), 100 * tb / max(tf, 1)))
        for k in ("buy_signals", "blockbuster", "notable"):
            v = [r[k] for r in wd]
            print("  %-12s avg=%.1f  median=%.1f  min=%d max=%d"
                  % (k, sum(v) / n, sorted(v)[n // 2], min(v), max(v)))


if __name__ == "__main__":
    main()
