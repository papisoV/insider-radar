"""Gate: no single Form 4 purchase leg may imply a per-share price above $100k.

Why this exists: 2026-10-08, one filing (SLBT / Ching-Dong Wang, filed
2026-10-01) put its AGGREGATE price into <transactionPricePerShare> --
4,545,306 shares @ "2272653", really $2,272,653 total for the whole transfer.
Multiplied naively it yields $10.33 TRILLION, which single-handedly produced
the "2026-10-01 was a $10.3T BLOCKBUSTER day" artifact. Its own footnote F2
states the truth: "for an aggregate purchase price of US$2,272,653".

A per-share price above $100k does not exist in US equity markets (the highest
real BRK.A print is ~$1.5M and it is one ticker), so anything above it is a
unit error in the filing, not a signal.

Called out args: from pytest-style harness in test_insider_filter.py.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import insider_filter as F  # noqa: E402

MAX_SANE_PRICE_PER_SHARE = F.MAX_SANE_PRICE_PER_SHARE
flag_price_anomaly = F.flag_price_anomaly


class TestNotionalSanity(unittest.TestCase):

    def test_real_prices_pass(self):
        for px in [0.50, 16.052, 61.2104, 79.6459, 194.7592, 5134.0, 99999.0]:
            self.assertFalse(flag_price_anomaly(px, 1000),
                             "%s should be a sane per-share price" % px)

    def test_unit_error_is_caught(self):
        # The actual SLBT filing value.
        self.assertTrue(flag_price_anomaly(2272653.0, 4545306))

    def test_threshold_is_explicit(self):
        self.assertEqual(MAX_SANE_PRICE_PER_SHARE, 100000.0)

    def test_boundary(self):
        self.assertFalse(flag_price_anomaly(100000.0, 1))
        self.assertTrue(flag_price_anomaly(100000.01, 1))

    def test_negtest_gate_really_fires(self):
        # A gate that has only ever passed proves nothing. These are the cases
        # that must FAIL if flag_price_anomaly or the threshold regress.
        # 1) If the threshold were deleted, every real price would look fine
        #    and nothing would be caught -> the bug would return silently.
        self.assertTrue(any(
            flag_price_anomaly(px, sh)
            for px, sh in [(2272653.0, 4545306), (5134.0, 18000)][:1]))
        # 2) A "reasonable-looking" naive implementation keying off notional
        #    size instead of per-share price would pass the real legs below
        #    and STILL let the SLBT filing through. Guard against that shape:
        #    small share counts must be caught purely on price.
        self.assertTrue(flag_price_anomaly(2_000_000.0, 1))
        # 3) The naive fix "just cap the notional" would silently drop real
        #    Berkshire-sized buys. $192.6M must stay valuable.
        self.assertFalse(flag_price_anomaly(79.6459, 2_418_637))

    def test_missing_values_do_not_crash(self):
        for sh, px in [(None, 5.0), (100.0, None), (None, None), (0, 0)]:
            self.assertFalse(flag_price_anomaly(px, sh),
                             "missing data is unvaluable, not anomalous")


if __name__ == "__main__":
    unittest.main(verbosity=2)
