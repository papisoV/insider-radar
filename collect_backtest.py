"""Backtest step 0 / part A: collect TICKER-LEVEL buys for days whose
forward windows have already elapsed.

Why this exists: daily20.json stored only COUNTS (673 buys across 13 days),
but no tickers. A backtest needs the ticker to fetch a price. sample.json has
tickers but every row is filed 2026-09-30 - only 6 days ago, so only T+5 is
measurable. One window is not enough to judge anything.

Window math (today = 2026-10-06):
  09-16  elapsed 20d -> T+5/10/15/20
  09-17  elapsed 19d -> T+5/10/15
  ...
  09-25  elapsed 11d -> T+5/10
Days after ~09-25 have no usable window and are skipped on purpose:
collecting them would cost the same and produce zero measurements.

NOT a T+30/60/90 study. Those windows are in the future for every filing we
have. The original plan said 30/60/90; the honest statement is that this run
measures 5/10/15/20 and nothing more.
"""
import io
import json
import os
import sys
import time
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
# Layout-agnostic: works in the working repo (radar/tools/) and in a flat
# clone.
for _p in (HERE, os.path.join(HERE, "tools"), os.path.dirname(HERE),
           os.path.join(os.path.dirname(HERE), "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from form4_parse import fetch as fetch_body, parse_form4  # noqa: E402
from form4_pipeline import EFTS, filing_url  # noqa: E402

OUT = os.path.join(HERE, "backtest_buys.json")
# Only days with an elapsed window >= 10 trading-ish days are worth the fetch.
DAYS_ALL = ["2026-09-16", "2026-09-17", "2026-09-18", "2026-09-21",
            "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25"]
# --part a/b lets each half finish inside a single command timeout, and each
# half merges into the same output file.
import sys as _s
_parts = [a for a in _s.argv if a.startswith("--part")]
if "--part=b" in _s.argv or "b" in _s.argv:
    DAYS = DAYS_ALL[4:]
else:
    DAYS = DAYS_ALL[:4]


# CAP: fetch only the first CAP filings per day. EFTS returns hits in its own
# order, which carries no relation to whether a filing contains a purchase,
# so a prefix is an unbiased sample of the day - it just yields fewer buys.
# Full coverage (~450/day at 1.4s each) hung and lost the run twice; a capped
# sample finishes and can be measured. Reported explicitly, not hidden.
CAP = 150


def day_filings(day):
    """Every Form 4 accession filed on `day`, walking from= to exhaustion."""
    import ssl
    import urllib.parse
    import urllib.request
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    UA = "sec-fact-monitor/1.0 (research script; contact: example.com)"
    seen, rows = set(), []
    for i in range(20):
        q = urllib.parse.urlencode({
            "forms": "4", "startdt": day, "enddt": day, "from": i * 100,
        })
        try:
            req = urllib.request.Request("%s?%s" % (EFTS, q),
                                         headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
                data = json.loads(r.read())
        except Exception as exc:
            print("  efts err %s" % str(exc)[:60], flush=True)
            time.sleep(2.0)
            continue
        hits = data.get("hits", {}).get("hits", [])
        if not hits:
            break
        for h in hits:
            acc = (h.get("_id") or "").split(":")[0]
            if acc and acc not in seen:
                seen.add(acc)
                rows.append(h)
        if len(hits) < 100:
            break
    return rows


def main():
    out = {}
    if os.path.exists(OUT):
        try:
            out = json.load(open(OUT, encoding="utf-8"))
        except Exception:
            out = {}
    for day in DAYS:
        rows = day_filings(day)
        buys, scanned, err = [], 0, 0
        for h in rows[:CAP]:
            src = h.get("_source", {})
            url = filing_url(h.get("_id", ""), src.get("ciks") or [])
            if not url:
                continue
            try:
                body = fetch_body(url, timeout=10)[0]
            except Exception:
                err += 1
                continue
            scanned += 1
            rec = parse_form4(body)
            legs = [t for t in (rec.get("transactions") or [])
                    if t["code"] == "P"]
            if not legs:
                continue
            total = 0.0
            for t in legs:
                try:
                    total += float(str(t.get("shares") or 0).replace(",", "")) \
                        * float(str(t.get("price") or 0).replace(",", ""))
                except (TypeError, ValueError):
                    pass
            buys.append({
                "ticker": rec.get("ticker") or "",
                "issuer": rec.get("issuer"),
                "insider": rec.get("insider"),
                "officer_title": rec.get("officer_title") or "",
                "is_director": str(rec.get("is_director") or "").strip()
                               in ("1", "true"),
                "is_ten_pct_owner": str(rec.get("is_ten_pct_owner") or "")
                                    .strip() in ("1", "true"),
                "period": rec.get("period"),
                "filed": day,
                "notional_usd": total,
                "url": url,
            })
            time.sleep(0.08)
        out[day] = buys
        print("%s  filings=%-5d scanned=%-5d err=%-3d buys=%-3d"
              % (day, len(rows), scanned, err, len(buys)), flush=True)
        # Atomic write: two concurrent runs of this script corrupted the
        # output once (interleaved json.dump -> "Extra data" on read). Write
        # to a temp file then rename, so a reader never sees a partial file.
        tmp = OUT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False)
        os.replace(tmp, OUT)

    total = sum(len(v) for v in out.values())
    print("[done] %d buys over %d days -> %s" % (total, len(DAYS), OUT))


if __name__ == "__main__":
    main()
