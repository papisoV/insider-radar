"""Phase 0 / step 1: hourly poller - closes the latency gap I can control.

Two separate delays get conflated, so state them plainly:
  [A] trade -> filing:  median 4 days (measured, n=38). Statutory 2-business-day
      window plus weekends. EVERYONE waits this, Unusual Whales included.
  [B] filing -> me:     this is my polling interval, and the only layer I own.
                        Daily manual runs made it up to ~1 day. Hourly makes it
                        ~1 hour. EFTS costs 2.3 s per query, so 24 runs/day is
                        under a minute and still $0.

This script does NOT publish anything. It writes to a local directory only.
Publishing (Reddit/Twitter/email) is gated on the privacy review.

Usage:
  python tools/poll_hourly.py --out tmpW100/poll --lookback 2
"""

import argparse
import io
import json
import os
import sys
import time
from datetime import date, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from run_20d import day_filings, evaluate


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="tmpW100/poll")
    ap.add_argument("--lookback", type=int, default=2,
                    help="how many past days to sweep per poll")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    seen_path = os.path.join(a.out, "seen.json")
    seen = set()
    if os.path.exists(seen_path):
        seen = set(json.load(io.open(seen_path, encoding="utf-8")))

    today = date.today()
    found = []
    for i in range(a.lookback + 1):
        day = today - timedelta(days=i)
        for h in day_filings(day):
            acc = (h.get("_id") or "").split(":")[0]
            if not acc or acc in seen:
                continue
            seen.add(acc)
            found.append(h)

    print("[poll] %s new filings since last run" % len(found), flush=True)
    if found:
        buys, ok, err = evaluate(found)
        print("[poll] parsed=%d err=%d purchases=%d" % (ok, err, len(buys)),
              flush=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        io.open(os.path.join(a.out, "poll-%s.json" % stamp), "w",
                encoding="utf-8").write(json.dumps(buys, ensure_ascii=False,
                                                   indent=1))
    io.open(seen_path, "w", encoding="utf-8").write(
        json.dumps(sorted(seen), ensure_ascii=False))
    print("[poll] seen=%d total" % len(seen), flush=True)


if __name__ == "__main__":
    main()
