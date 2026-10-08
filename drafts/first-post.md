# Insider Radar — first post draft

A tiny tool I built that reads every SEC Form 4 filed each day and prints the
few with real money in them.

I also measured whether its ranking beat picking at random. It doesn't. That's
the interesting part, so I published it.

## The arithmetic

~530 insider-transaction filings hit EDGAR every trading day. Roughly 1 in 10
contains an actual purchase — the rest are sales, grants, option exercises and
tax withholding.

Nobody reads 530 filings. Getting the data is a urllib call; cutting it to the
handful with money going in is the actual work.

## What I got wrong

I started out believing three things. All three broke when I checked.

**That "top 5 every day" is the right shape.** Across 13 trading days the
purchase rate swung from 2.8% to 20.3% — a 7x range. On 2026-10-02 there were
1,502 filings and only 42 purchases, because it was a quarter-boundary 10b5-1
wave (mostly scheduled sales). A fixed count means five interesting items one
day and five routine ones the next. What adapts to the day has to be how many
pass a gate, not a hardcoded N.

The flip side is worth saying: a low purchase *rate* doesn't mean small deals.
Sampling 300 of those 1,502 filings turned up 3 purchases, and one was a $5.56M
buy by a VP at Pampa Energy. The wave added sales; it didn't shrink the buys
that were there. So the gate had to sit on absolute size, not on the day's
ratio — which is the opposite of what I'd assumed.

**That percentile should decide the order.** My first ranking sorted by
within-day percentile, and it quietly flattened magnitude: $157M and $33M are
4.7x apart but only about 5 percentile points apart, so a $2.1M filing ranked
above a $53.9M one. Percentile is now a gate — it decides *whether* something
shows up, not where. Order is absolute.

**That the bonuses were free.** I'd scored CEO-ness and multi-insider clusters
without caps, and a 3-insider $33M cluster beat a single $157M filing because
the +12.7 bonus exceeded the 6.8-point money gap. One order of magnitude of
notional is worth 10 points in this scale, so every bonus is now capped below
10. It can reorder within a magnitude; it can never cross one.

## Then I tested whether the filter was any good

This is the part nobody publishes.

I took 405 purchases across 8 trading days, ran my own screen over them, and
compared against 2,000 random draws from **the same day** — same count, no
screen. The control being same-day is the whole design: comparing against "the
market" would confound my ranking with whatever insider buying did that week.
The question was narrow — does my ordering add anything over buying everything?

The pass bar was fixed before I saw numbers: the screened set had to land at or
above the 95th percentile of the control distribution.

| Window | n | screened | control | percentile |
| --- | --- | --- | --- | --- |
| T+5 | 68 | −0.77% | −0.16% | 0.42 |
| T+10 | 68 | −1.73% | −0.46% | 0.48 |
| T+15 | 41 | −0.88% | +0.94% | 0.59 |
| T+20 | 13 | −1.38% | −0.79% | 0.43 |

Four windows, four negatives. Not "failed to clear the bar" — in the bottom
half of random.

**Being fair to my own result:** with 68 samples and a control spread of 8.2pp,
an effect of 3pp is unmeasurable. It would take about 508 samples to see it. So
eight days can't rule out a small edge, and I'm not going to claim it does.
What eight days can say is that nothing large is showing up, because something
large would have cleared that bar easily.

Two unrelated readings agree the effect is near zero: the pooled mean here was
+0.05% (median −0.33%), and InsiderWatch publishes its own graded ledger — 619
alerts, 48% hit rate, −0.2% per call against the S&P.

## What that means for the tool

It saves reading time. It does not pick.

So I went through the output and deleted every phrase that implied otherwise.
"Worth watching" is gone — it claims we chose well, which is exactly what
failed. So is "conviction" (an inference about motive; the filing has a
transaction, not a state of mind), "smart money", "alert", "pick", and
"signal" — each says act-on-being-told. Each entry now says what the filing
actually contains and nothing else.

That's held by tests, not intentions: the test suite renders real output and
asserts those strings don't appear. Every one of them was in the output
before the measurement. They're all plausible-sounding, which is why they'd
come back.

## The output

One real run — 2026-10-06, 67 filings that day, 6 purchases, 4 clearing
the floor. Trimmed to the top 3 here; each entry links to its source
filing on sec.gov.

```text
TODAY'S INSIDER RADAR

Public SEC Form 4 filings. Sorted by size within the day -
not advice, not a recommendation, not a prediction.
Measured: this ordering does not beat random - see README.

  6 purchase filings today. Largest first, ranked within the
  day (small sample - absolute order). Sorted by size - not by expected outcome.

#1  AVR
    Anteris Technologies Global Corp.
    $6,708,731  [BLOCKBUSTER]
    L1 Capital Pty Ltd

    Why it stands out today:
      - $6,708,731 purchase (SEC code P)
      - 10% owner
    Trade date: 2026-10-02   Filed: 2026-10-06
    SEC filing: https://www.sec.gov/Archives/edgar/data/1817646/000181764626000029/primary_doc.xml

#2  BORR
    Borr Drilling Ltd
    $6,197,100  [BLOCKBUSTER]
    Troim Tor Olav

    Why it stands out today:
      - $6,197,100 purchase (SEC code P)
      - director
    Trade date: 2026-10-05   Filed: 2026-10-06
    SEC filing: https://www.sec.gov/Archives/edgar/data/1709630/000162828026065174/wk-form4_1791286191.xml

#3  SAH
    SONIC AUTOMOTIVE INC
    $1,287,878  [BLOCKBUSTER]
    Rusnak Paul P.

    Why it stands out today:
      - $1,287,878 purchase (SEC code P)
      - 10% owner
    Trade date: 2026-10-01   Filed: 2026-10-06
    SEC filing: https://www.sec.gov/Archives/edgar/data/1460471/000146047126000005/primary_doc.xml
```

## Try it

```bash
git clone https://github.com/papisoV/insider-radar
cd insider-radar
python insider_filter.py --days 1 --limit 600 --pages 8 --json signals.json
python render_radar.py --in signals.json --top 5
```

If you're considering paying anyone for insider-transaction notifications, the
honest thing I can offer is the measurement rather than the tool. It's in the
README, along with what it does and doesn't rule out.

Source: https://github.com/papisoV/insider-radar
