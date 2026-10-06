# Insider Radar

**Read every SEC Form 4 filed today, and print the handful worth looking at.**

The SEC publishes ~530 insider-transaction filings per trading day. About 1 in
10 contains an actual purchase. This reads all of them, drops what you can't
act on, and renders the rest as a short list — with the reason each one is
there.

What it does not do is pick winners. I measured whether the ranking selects
anything better than random, and it doesn't — see
[Does the screen work?](#does-the-screen-work-measured-no). The value here is
that nobody reads 530 filings; that's arithmetic, and it holds. Sorting by
size is a reasonable way to order a list, not a reason to expect the top of
it to outperform.

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

So the filter is the whole job. Getting the data is a `urllib` call. Sorting it
is where the work is.

**One caveat, stated up front because it's the interesting part:** the filter
reduces volume. It does not, as measured, improve selection. See
[Does the screen work?](#does-the-screen-work-measured-no) — that section is
the reason this README has a results table instead of a claim.

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
means five interesting filings one day and five routine ones the next. What
adapts to the day has to be *how many items pass a gate*, not a hardcoded N.

But a low-ratio day does **not** mean a low-quality one. Sampling 300 of
10-02's filings turned up only 3 purchases — and one was a **\$5.56M buy by a
VP at Pampa Energy**. A low purchase *rate* means few buys, not small ones:
the 10b5-1 wave adds sales, it doesn't shrink the purchases that are there.
So the gate has to be on absolute size, not on the day's ratio. Measured,
because my first design assumed the opposite.

---

## Install

```bash
git clone https://github.com/papisoV/insider-radar.git
cd insider-radar
python test_insider_filter.py      # 52 assertions, no dependencies
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
| `test_insider_filter.py` | Self-test (52 assertions), no framework needed. |
| `collect_backtest.py` | Collect ticker-level purchases for backtesting. |
| `backtest.py` | Does the screen beat random draws from the same day? |
| `diagnose.py` | Separate "no effect" from "can't measure it". |
| `backtest_buys.json` | The 405 purchases the result is computed from. |
| `backtest_result.json` | The measured result, so you don't have to re-run it. |

Reproduce the negative result:

```bash
python backtest.py     # writes tmpW100/backtest_result.json (needs network for prices)
python diagnose.py     # writes tmpW100/diagnose.txt
```

---

## Does the screen work? Measured. No.

This is the part most projects don't publish. The ranking above is defensible
as engineering — but I tested whether it actually selects better, and it does
not.

**Method** ([`backtest.py`](backtest.py), seed fixed at 20261006):

- population: 405 P-code purchases across 8 trading days (2026-09-16 → 09-25),
  collapsed per issuer
- screened: the ones passing this repo's own gate (≥ \$100k and top quartile
  of their day)
- control: 2,000 random draws from **the same day**, same count, without the
  screen — so any difference is the screen, not the day, the market, or the
  sector
- measure: forward return from filing date, T+5/10/15/20 calendar days
- **pass threshold, fixed before seeing results**: screened mean must sit at
  or above the 95th percentile of the control distribution

**Result:**

| Window | n | screened | control | percentile | verdict |
| --- | --- | --- | --- | --- | --- |
| T+5 | 68 | −0.77% | −0.16% | 0.42 | NO-GO |
| T+10 | 68 | −1.73% | −0.46% | 0.48 | NO-GO |
| T+15 | 41 | −0.88% | +0.94% | 0.59 | NO-GO |
| T+20 | 13 | −1.38% | −0.79% | 0.43 | NO-GO |

Four windows, four negatives, all below the control. Not "failed to clear
0.95" — sitting in the bottom half.

**Is that real, or just instrument noise?** [`diagnose.py`](diagnose.py) exists
to separate those two answers, and the honest reading is *both*:

```text
return by notional bucket (T+5)     n     mean%   median%
  (a) <$100k                      187     -0.05     -0.35
  (b) $100k-$1M                    63     +0.66     +0.96
  (c) >= $1M                       45     -0.37     -0.84
  pooled                          295     +0.05%    -0.33%

T+5:  screened beat same-day universe on 3 of 8 days
T+10: screened beat same-day universe on 3 of 8 days

T+5  n=68  effect=-0.61pp   control band (p05..p95)=8.2pp
```

Size does not order returns — the buckets are non-monotonic, and the middle
bucket is the only positive one. The sign flips 5 days out of 8. And the
control's own spread is 13x the measured effect, so at n=68 a 3pp effect is
unmeasurable: it would take roughly **508 samples** to see it. That last point
is the real limitation — 8 days cannot rule out a small edge, and I'm not
going to claim it does. What 8 days *can* say is that nothing large is showing
up, because a large effect would have cleared this bar easily.

Two independent readings agree the underlying signal is near zero: pooled mean
**+0.05%**, median −0.33% here, and InsiderWatch's public ledger at 619 graded
alerts, 48% hit rate, −0.2% per call against the S&P.

**So: the ranking is a convenience, not an edge.** What survives the
measurement is that ~530 filings a day is more than a human will read. That is
arithmetic, and it holds. "The filter is the product" was my claim and the
data does not support it.

Sorting by size is still a reasonable way to order a list. It is not a reason
to believe the top of the list will outperform.

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
Nothing above the threshold today.

  42 purchase filings seen; the largest was $84,200, below the
  $100,000 floor. Nothing here implies anything about tomorrow.
```

Publishing "best of a dull day" is exactly how a filter becomes a noise
source. Silence is the honest output — and it also tells the reader the screen
is working rather than broken.

The wording avoids calling a small day *bad*. The [backtest](#does-the-screen-work-measured-no)
found no relation between deal size and forward return, so "quiet day" would be
a judgement the data does not support.

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
- The backtest sample is 8 days and capped at 150 filings/day, so it is a
  sample, not full coverage: 405 purchases, 295 with prices. Prices come from
  Yahoo's public chart endpoint, which is not adjusted for splits or dividends
  in this code and returns nothing for 28 of the names — reported as
  `coverage_screened` / `coverage_universe` per day in the result JSON (worst
  case 0.78 / 0.94) rather than silently dropped.

## License

MIT. See [`LICENSE`](LICENSE).
