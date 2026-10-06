# Insider Radar

**Read every SEC Form 4 filed today, and print the handful that aren't noise.**

The SEC publishes ~530 insider-transaction filings per trading day. About 1 in
10 contains an actual purchase. This reads all of them, throws away what you
can't act on, and renders the rest as a short list — with the reason each one
made the cut.

Free data, no API key, no account, standard library only.

```
$ python insider_filter.py --days 1 --limit 600 --pages 8 --json signals.json
[efts] 626 unique filings (offsets walked: 6)
[filter] scanned=300 filings / 1d -> 5 purchase signals
[filter] BLOCKBUSTER=0 NOTABLE=0 ROUTINE=5  (noise cut: 98%)

$ python render_radar.py --in signals.json --top 5
```

```text
TODAY'S INSIDER RADAR

Public SEC Form 4 filings. Selection and context only -
not investment advice, not a recommendation, no performance claim.

#1  LND
    BrasilAgro - Brazilian Agricultural Real Estate Co
    $199,696
    Elsztain Alejandro Gustavo

    Why it stands out:
      - $199,696 purchase (SEC code P)
      - director
    Trade date: 2026-09-30   Filed: 2026-10-05
    SEC filing: https://www.sec.gov/Archives/edgar/data/2119764/000211976426000030/section16.xml
```

That's it. One issuer per line, why it's interesting, link to the source.

---

## Why this exists

The data is public and free. Reading it is the hard part.

| | |
| --- | --- |
| Form 4 filings per trading day | **528.6** (median 408, range 297–1502) |
| Of those, filings with a purchase | **51.8** (9.8%) |
| Purchases ≥ $1M | **9.6** |
| Purchases ≥ $250k and < $1M | **5.0** |

Nobody reads 528 filings. And if you push all 52 purchases, you are pushing
noise — the other ~475 filings that day are sales, grants, option exercises and
tax withholding, which say nothing about conviction.

So the filter *is* the product. Getting the data is a `urllib` call.

### The daily rate is not stable

This is the finding that changed the design. Across 13 consecutive trading days
the purchase rate ranged **2.8% – 20.3%**, a 7x swing:

```text
09-16 13.0%   09-22 15.8%   09-28 11.8%
09-17  9.5%   09-23 13.5%   09-29  9.1%
09-18  8.7%   09-24 12.3%   09-30 20.3%
09-21 11.5%   09-25 10.3%   10-01 10.3%
                            10-02  2.8%   <- 1502 filings, only 42 buys
```

10-02 is real, not a bug: re-queried independently, EFTS reports
`total=1502` for that day, matching the run exactly. It's a quarter-boundary
[10b5-1](https://www.sec.gov/answers/rule10b5-1.htm) wave — mostly scheduled
sales, which dilutes the purchase ratio.

**Consequence: a fixed daily count is the wrong shape.** "Top 5 every day"
means five interesting filings one day and five routine ones the next. Ranking
has to be relative to the day's own distribution, not an absolute dollar
threshold.

---

## Install

```bash
git clone https://github.com/papisoV/insider-radar.git
cd insider-radar
python test_insider_filter.py      # 51 assertions, no dependencies
```

Python 3.8+. No `pip install` needed — standard library only.

## Usage

```bash
# One day, full coverage, to JSON
python insider_filter.py --days 1 --limit 600 --pages 8 --json signals.json

# Render it
python render_radar.py --in signals.json --top 5 --out radar.txt

# Backfill N days (this is what produced the table above)
python run_20d.py --days 20 --out daily.json

# Poll on a schedule, writing only newly-seen filings
python poll_hourly.py --out polls/ --lookback 2
```

**`--pages` matters.** EFTS returns at most 100 hits per query, so
`--pages 8` covers ~800 filings. Under-paging silently truncates the day and
every number you derive from it. `--limit` is how many of those to actually
fetch and parse; discovery and parsing are separate steps.

### Scripts

| Script | What it does |
| --- | --- |
| `form4_parse.py` | Fetch one filing URL, parse the XML, print the transaction legs. |
| `form4_pipeline.py` | Discover filings by day/form via EFTS, build filing URLs. |
| `insider_filter.py` | Discover + parse + filter to purchase signals, emit JSON. |
| `render_radar.py` | Turn a signals JSON into the readable daily list. |
| `run_20d.py` | Full-coverage multi-day run; source of the table above. |
| `poll_hourly.py` | Poll recent days, write only newly seen filings. |
| `test_insider_filter.py` | Self-test (51 assertions), no framework needed. |

---

## How ranking works

Two separate decisions: **what order**, and **whether to show it at all**.

```text
order  = 10·log10(notional) + role + cluster_bonus
gate   = notional >= $100k  AND  percentile_within_day >= 0.75
```

The order is absolute. The gate is relative. Mixing them up broke it twice:

- *Percentile as the sort key* flattened magnitude — $157M and $33M are 4.7x
  apart but only ~5 percentile points apart, so the tiebreaker terms swamped
  the money term and a \$2.1M buy ranked above a \$53.9M buy.
- *Uncapped bonuses* let a 3-insider \$33M cluster beat a single \$157M
  filing, because the +12.7 bonus exceeded the 6.8-point money gap.

So every bonus is capped **below 10**, which is what one order of magnitude of
notional is worth. A bonus can reorder within a magnitude; it can never cross
one. Role (CEO vs director) is compressed to ≤6 for the same reason — a raw
role score of 40 let a \$2.1M CEO buy outrank a \$27.5M director buy.

### The gate, not the count, adapts to the day

That's what "relative" has to mean here: **how many items pass** changes with
the day, not which order they come out in. A 42-buy day promotes fewer than an
81-buy day. Within either, biggest is first.

Below 15 signals the day is too small for a percentile to mean anything, so
only the absolute floor applies.

### Empty state

If nothing clears both conditions, the output says so:

```text
Nothing worth watching today.

  42 purchase filings seen; the largest was $84,200, below the
  $100,000 floor. Relative to a typical day this is a quiet one.
```

Publishing "best of a dull day" is exactly how a filter becomes a noise
source. Silence is the honest output — and it also tells the reader the screen
is working rather than broken.

### What gets dropped, and why

Every exclusion below exists because it appeared in real output and was wrong,
not because it seemed sensible in advance:

| Dropped | Reason |
| --- | --- |
| Non-listed issuers | Private credit funds file with ticker `NONE`/`N/A`. Their buys are large, so they dominated the top of an early render — and you can't act on them. |
| Mutual-fund share classes | 5-letter symbols ending in `X` are fund shares, not equities. |
| CUSIP-shaped identifiers | A 9-char alphanumeric id where a ticker should be. |
| Purchases under \$100k | Median P-code notional is ~$78k, so this is a floor, not a filter. |
| Stale trades | Seen live: a filing submitted 2026-09-30 for a trade on 2026-04-21. Months-old news wearing a new filing date. |
| Derivative legs | Options and warrants report a *strike*, not money paid. Folding them into a notional would invent a number that doesn't exist. |

---

## What this is not

**Not investment advice.** No buy/sell language, no price targets.

**No performance claim.** The tool publishes no win rate and the wording stays
at "worth watching". Anyone claiming a high win rate on insider filings should
be asked to show the graded ledger — the one public ledger I know of shows
[619 graded alerts at a 48% hit rate](https://www.insiderwatch.com/), and
−0.2% per call against the S&P.

**Not real-time, and it can't be.** Two separate delays:

- *trade → filing*: statutory deadline is 2 business days; measured median is
  4 days. Everyone waits this, including paid services, because it's baked
  into the disclosure regime.
- *filing → you*: our polling interval. This is the only layer a tool can
  actually shrink, and it's what `poll_hourly.py` is for.

**Not complete coverage.** See the derivative-leg row above.

## Known limits

- SEC transaction code `P` means *"open market **or private** purchase, of
  non-derivative **or derivative** security"*, per the SEC's own
  [form code definitions](https://www.sec.gov/edgar/searchedgar/ownershipformcodes.html).
  So it is **not** accurate to call every `P` an open-market buy. The wording
  here says "purchase (SEC code P)" and stops there.
- One economic trade can be filed by two related filers (an individual and
  their advisory firm), which double-counts. The renderer collapses per issuer;
  the raw JSON does not deduplicate across filers.
- EFTS `page=` is a no-op — only `from=` advances. Pagination here uses `from=`.
- The 13-day sample spans a quarter boundary (2026-09-16 → 10-02), which is
  where the 10b5-1 wave showed up. Treat the averages as indicative of
  magnitude, not as a stable baseline.

## License

MIT. See [`LICENSE`](LICENSE).
