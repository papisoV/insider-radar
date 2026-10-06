"""Form 4 insider-signal pipeline: EFTS discovery -> Archives fetch -> parse.

End-to-end, all free (no key, no login):
  1. efts.sec.gov full-text search finds Form 4 filings in a date window
  2. www.sec.gov/Archives serves the raw ownership XML
  3. form4_parse turns it into an insider signal

W98 note: the Archives URL needs THREE segments - filer CIK (from
_source.ciks[0], NOT the accession prefix which is the filing agent),
the accession folder, and the bare document name.

Usage:
  python tools/form4_pipeline.py --days 10 --limit 15
  python tools/form4_pipeline.py --days 10 --buys-only
"""

import argparse
import json
import os
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from form4_parse import UA, parse_form4
except ImportError:
    from form4_parse import UA, parse_form4

EFTS = "https://efts.sec.gov/LATEST/search-index"


def fetch(url, timeout=40):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Encoding": "identity"}
    )
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read().decode("utf-8", "replace")


def filing_url(_id, ciks):
    """_id = '<accession>:<doc>'. Returns the 3-segment Archives URL."""
    if not _id or ":" not in _id:
        return ""
    acc, doc = _id.split(":", 1)
    cik = ""
    if ciks:
        cik = str(ciks[0]).lstrip("0")
    if not cik:
        cik = acc.split("-")[0].lstrip("0")
    bare = doc.split("/")[-1]
    return ("https://www.sec.gov/Archives/edgar/data/%s/%s/%s"
            % (cik, acc.replace("-", ""), bare))


def discover(days, limit, form="4", pages=1):
    """Find Form 4 filings in a date window.

    W99: prefer `forms=` over a quoted `q="Form 4"` full-text query. The full
    text form intermittently 500s and under-reports (measured 2026-10-06 on the
    same 25d window: q="Form 4" -> 5060, forms=4 -> 9618). The W97 note about
    `forms=` + date range silently returning zero applies to some OTHER form
    types (13F/SC 13D), NOT to Form 4 or 8-K - verified live.
    """
    end = date.today()
    start = end - timedelta(days=days)
    hits, seen = [], set()
    # EFTS hard-caps a single query at 100 hits (verified 2026-10-06) and a
    # Form 4 day carries ~600-900 filings, so a multi-day window only ever
    # surfaces the newest ~100. Walk day by day: each day slice gets its own
    # 100-hit budget, so coverage scales with days instead of saturating.
    day_slices = [start + timedelta(days=i) for i in range(days + 1)]
    # W99: `page=` is a NO-OP on EFTS - every page returns the same first 100
    # hits (verified: page=1/2/3/5 all returned the identical 100 accessions
    # with total=4168). The only working offset is `from=`. So: slice by day
    # AND walk `from=` within each day, because EFTS caps any single query at
    # 100 hits regardless of total.
    offsets = list(range(0, max(pages, 1) * 100, 100))
    for day in day_slices:
      for offset in offsets:
        q = urllib.parse.urlencode({
            "forms": form,
            "startdt": day.isoformat(),
            "enddt": day.isoformat(),
            "from": offset,
        })
        try:
            data = json.loads(fetch("%s?%s" % (EFTS, q)))
        except Exception as exc:
            print("[efts] ERROR day=%s from=%d %s" % (day, offset, str(exc)[:110]))
            break
        got = data.get("hits", {}).get("hits", [])
        for h in got:
            # W99: EFTS returns one hit PER DOCUMENT, so a single filing with
            # several documents yields several hits with the same accession.
            # Dedupe on accession or we count the same filing 6x (measured:
            # 18 "signals" were really 3 filings repeated 6 times).
            acc = (h.get("_id") or "").split(":")[0]
            if not acc or acc in seen:
                continue
            seen.add(acc)
            hits.append(h)
        if len(got) < 100:      # exhausted this window
            break
    print("[efts] %d unique filings (offsets walked: %d)"
          % (len(hits), len(offsets)))
    out = []
    for h in hits:
        src = h.get("_source", {})
        form = (src.get("file_type") or src.get("form") or "")
        if "4" not in form.replace("SCHEDULE", "").strip():
            continue
        if src.get("form") not in ("4", "4/A") and "FORM 4" not in form.upper():
            continue
        url = filing_url(h.get("_id", ""), src.get("ciks") or [])
        if not url:
            continue
        out.append({"url": url, "form": src.get("form"),
                    "filed": src.get("file_date"),
                    "names": (src.get("display_names") or [""])[0][:60]})
        if len(out) >= limit:
            break
    return out


def _num_sum(values):
    """Shares/prices arrive as strings or floats depending on the filing."""
    total = 0.0
    for v in values:
        try:
            total += float(v)
        except (TypeError, ValueError):
            continue
    return int(total) if total.is_integer() else round(total, 4)


def run(days, limit, buys_only, pages=1):
    found = discover(days, limit, pages=pages)
    print("[pipeline] discovered=%d window=%dd" % (len(found), days))
    ok = skipped = 0
    signals = []
    for f in found:
        try:
            body = fetch(f["url"])
        except Exception as exc:
            skipped += 1
            print("  SKIP http-err %s" % str(exc)[:50])
            continue
        rec = parse_form4(body)
        if not rec.get("issuer"):
            skipped += 1
            continue
        ok += 1
        rec["filed"] = f["filed"]
        rec["url"] = f["url"]
        signals.append(rec)
        if buys_only and not rec.get("is_open_market_buy"):
            continue
        flag = "BUY " if rec.get("is_open_market_buy") else (
            "SELL" if rec.get("is_open_market_sell") else "    ")
        txs = rec.get("transactions") or []
        tot = _num_sum(t.get("shares") for t in txs)
        px = txs[0].get("price") if txs else ""
        print("  %s %-26s %-24s %d tx %s sh @ %s" % (
            flag, (rec.get("issuer") or "")[:26],
            (rec.get("insider") or "")[:24], len(txs), tot, px))

    print("\n[pipeline] parsed=%d skipped=%d" % (ok, skipped))
    buys = [s for s in signals if s.get("is_open_market_buy")]
    print("[pipeline] PURCHASE (code P) filings = %d / %d" % (len(buys), ok))
    return signals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--buys-only", action="store_true")
    ap.add_argument("--pages", type=int, default=1)
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    sigs = run(a.days, a.limit, a.buys_only, a.pages)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(sigs, fh, ensure_ascii=False, indent=1)
        print("[out] wrote %s" % a.json)


if __name__ == "__main__":
    main()
