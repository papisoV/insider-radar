# Insider Radar

Read public SEC insider filings (Form 4) and material-event filings (8-K),
keep the ones that stand out, and print them in a readable list.

Nothing here is investment advice. Nothing here predicts anything.
See [What this is not](#what-this-is-not).

## What it does

Every weekday the SEC publishes several hundred Form 4 filings — disclosures
of insider transactions. Roughly 1 in 8 contains an actual purchase
(SEC transaction code `P`). This project:

1. **Finds** every Form 4 filed on a given day via the SEC's full-text search
   endpoint (EFTS), walking pagination so the daily count is complete rather
   than capped.
2. **Parses** the filing XML and keeps only purchase legs, computing a notional
   from `price × shares`.
3. **Filters** out what cannot be acted on: unlisted issuers, stale trades
   (a filing submitted in September for an April trade), and filings where we
   can only see part of the transaction.
4. **Renders** a short list, one entry per issuer, with the reason each filing
   stands out and a link to the source document.

Data comes from `efts.sec.gov` and `www.sec.gov`. No API key, no account,
no cost.

## Measured, not estimated

Full-coverage run over 13 consecutive trading days (2026-09-16 → 2026-10-02),
6,872 filings fetched and parsed:

| Per trading day | mean | median | range |
| --- | --- | --- | --- |
| Form 4 filings | 528.6 | 408 | 297–1502 |
| Filings containing a purchase (`P`) | 51.8 | 44 | 36–81 |
| Purchases ≥ $1M | 9.6 | 8 | 4–17 |
| Purchases ≥ $250k and < $1M | 5.0 | 5 | 2–7 |

Pooled purchase rate: 9.8% (673 of 6,872).

**The daily purchase rate is not stable** — it ranged from 2.8% to 20.3%
across those 13 days:

```
09-16 13.0%   09-22 15.8%   09-28 11.8%
09-17  9.5%   09-23 13.5%   09-29  9.1%
09-18  8.7%   09-24 12.3%   09-30 20.3%
09-21 11.5%   09-25 10.3%   10-01 10.3%
                            10-02  2.8%   <- 1502 filings, only 42 buys
```

10-02 is real, not a collection artifact: re-queried independently, EFTS
reports `total=1502` for that day, matching the run exactly. It is a
quarter-boundary 10b5-1 routine-filing wave — mostly sales, which dilutes
the purchase ratio.

Practical consequence: **a fixed count is the wrong product shape.** "Top 5
per day" means five interesting filings one day and five routine ones the
next. Ranking and thresholds have to be relative to the day, not absolute.

These numbers are why the tool filters at all: ~420 filings/day is not
something a human reads. The filter is the product; the data is free.

An earlier estimate of "8.3 pushable per day" came from a run that hit the
EFTS 100-hit-per-query cap and undercounted. The table above supersedes it.

## Scripts

| Script | What it does |
| --- | --- |
| `form4_parse.py` | Fetch one filing URL, parse the XML, print the transaction legs. |
| `form4_pipeline.py` | Discover filings by day/form via EFTS, build filing URLs, emit JSON. |
| `insider_filter.py` | Discover + parse + filter to purchase signals, emit JSON. |
| `render_radar.py` | Turn a signals JSON into the readable daily list. |
| `run_20d.py` | Full-coverage multi-day run; the source of the table above. |
| `poll_hourly.py` | Poll recent days on a schedule and write only newly seen filings. |
| `test_insider_filter.py` | Self-contained self-test (38 assertions). No framework needed. |

### Ranking

Money is the signal; role only breaks ties. The rank key is
`10·log10(notional) + role_score + 15·(distinct_insiders − 1)`.

An earlier version ranked on `role_score` with notional as a tiebreak, which
put a **$101 purchase by a President at #2**, above a $199,696 purchase. The
log scale keeps a $50M buy from flattening every smaller difference while
still ordering them correctly.

Issuers below \$100k total, unlisted issuers, mutual-fund share classes
(5-letter symbols ending in X), stale trades, and CUSIP-shaped identifiers
are all dropped. Each of those exclusions exists because it appeared in real
output and was wrong.

### Usage

```bash
# One day of purchases, full coverage, as JSON
python insider_filter.py --days 1 --limit 300 --pages 6 --json signals.json

# Render the top 5
python render_radar.py --in signals.json --top 5 --out radar.txt

# Full-coverage backfill over N days
python run_20d.py --days 20 --out daily.json
```

`--pages` matters: EFTS returns at most 100 hits per query, so `--pages 6`
covers ~600 filings. Under-paging silently truncates the day.

## Output

```
#1  FUL
    FULLER H B CO
    $499,068  [NOTABLE]
    FLORNESS DANIEL L
    5 distinct insiders, $499,068 total

    Why it stands out:
      - $100,160 purchase (SEC code P)
      - director
      - 5 distinct insiders filed on FUL in the window
    Trade date: 2026-09-28   Filed: 2026-09-30
    SEC filing: https://www.sec.gov/Archives/edgar/data/...
```

## What this is not

- **Not investment advice.** No buy/sell language, no price targets.
- **No performance claim.** The tool publishes no win rate, and the wording
  stays at "worth watching". Anyone claiming a high win rate on insider
  filings should be asked to show the graded ledger.
- **Not real-time, and cannot be.** There are two separate delays:
  the trade→filing gap (statutory deadline is 2 business days; measured
  median is 4 days), which nobody can remove from public data; and the
  filing→us gap, which is just our polling interval. This project only
  affects the second one.
- **Not complete coverage of every transaction.** Only non-derivative legs
  are parsed. Derivative legs (options, warrants) are skipped on purpose:
  their "price" is a strike, not money paid, so folding them into a notional
  would invent a number that does not exist.

## Known limits

- SEC transaction code `P` means *"open market **or private** purchase, of
  non-derivative **or derivative** security"* per the SEC's own
  [form code definitions](https://www.sec.gov/edgar/searchedgar/ownershipformcodes.html).
  It is therefore **not** accurate to describe every `P` as an open-market
  buy. The wording here says "purchase (SEC code P)" and stops there.
- One economic trade can be filed by two related filers (e.g. an individual
  and their advisory firm), which double-counts. The renderer collapses
  per issuer; the raw JSON does not deduplicate across filers.
- EFTS `page=` is a no-op — only `from=` advances. Pagination here uses
  `from=`.

## Requirements

Python 3.8+, standard library only.

## License

MIT. See `LICENSE`.
