"""Self-test for the insider filter: known defects must be caught.

Mirrors the W96 rule - a tool that ships without a test proving it catches
known defects is not verified, only written.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import insider_filter as F  # noqa: E402

# Write next to this file, not into a sibling project directory: the test
# must run from a fresh clone where no tmpW98/ exists.
OUT = os.path.join(HERE, "filter_selftest.txt")


def rec(**kw):
    base = {
        "issuer": "Testco Inc.", "ticker": "TST", "insider": "DOE JOHN",
        "is_director": "0", "is_officer": "0", "is_ten_pct_owner": "0",
        "officer_title": "", "period": "2026-10-01", "filed": "2026-10-02",
        "url": "http://example.invalid", "transactions": [],
    }
    base.update(kw)
    return base


def tx(code, shares, price, date="2026-10-01"):
    return {"date": date, "code": code, "acquired_or_disposed": "A",
            "shares": str(shares), "price": str(price),
            "owned_after": "1000"}


RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def main():
    # 1. a pure S filing must produce NO signal (this is the 8:1 noise cut)
    r = rec(transactions=[tx("S", 1000, 50.0)])
    check("sell-only filing -> no signal", F.evaluate(r) is None)

    # 2. F (tax withholding) must not be a buy
    r = rec(transactions=[tx("F", 500, 50.0)])
    check("tax withholding -> no signal", F.evaluate(r) is None)

    # 3. M (option exercise) must not be a buy
    r = rec(transactions=[tx("M", 500, 50.0)])
    check("option exercise -> no signal", F.evaluate(r) is None)

    # 4. a real P buy is detected
    r = rec(transactions=[tx("P", 10000, 50.0)])
    s = F.evaluate(r)
    check("P buy -> signal", s is not None)
    check("P notional = 500,000", s and s["notional_usd"] == 500000.0,
          str(s and s["notional_usd"]))
    check("500k -> NOTABLE tier", s and s["tier"] == "NOTABLE",
          str(s and s["tier"]))

    # 5. tier boundaries
    for notional, want in ((1_000_000, "BLOCKBUSTER"), (999_999, "NOTABLE"),
                           (250_000, "NOTABLE"), (249_999, "ROUTINE")):
        r = rec(transactions=[tx("P", notional, 1.0)])
        s = F.evaluate(r)
        check("tier boundary %d -> %s" % (notional, want),
              s and s["tier"] == want, str(s and s["tier"]))

    # 6. mixed filing: one P among several S legs -> keep only the P
    r = rec(transactions=[tx("S", 100, 50.0), tx("P", 200, 60.0),
                          tx("F", 30, 50.0)])
    s = F.evaluate(r)
    check("mixed legs -> only P counted", s and s["n_buy_tx"] == 1,
          str(s and s["n_buy_tx"]))
    check("mixed legs -> notional 12,000", s and s["notional_usd"] == 12000.0,
          str(s and s["notional_usd"]))

    # 7. missing price: kept, flagged, never silently dropped
    r = rec(transactions=[{"date": "2026-10-01", "code": "P", "shares": "500",
                           "price": "", "owned_after": "1000"}])
    s = F.evaluate(r)
    check("no price -> signal kept", s is not None)
    check("no price -> flagged incomplete",
          s and s["notional_complete"] is False and s["n_unvaluable"] == 1,
          str(s and (s["notional_complete"], s["n_unvaluable"])))
    check("no price -> notional is None", s and s["notional_usd"] is None)

    # 8. flag parsing: '1'/'0' vs 'true'/'false' must both work
    for flag in ("1", "true", "True"):
        s = F.evaluate(rec(is_director=flag, transactions=[tx("P", 10, 10.0)]))
        check("director flag %r parsed" % flag, s and s["is_director"] is True)
    for flag in ("0", "false", ""):
        s = F.evaluate(rec(is_director=flag, transactions=[tx("P", 10, 10.0)]))
        check("non-director flag %r parsed" % flag,
              s and s["is_director"] is False)

    # 9. role scoring: CEO outranks plain director outranks unclassified
    ceo = F.evaluate(rec(officer_title="Chief Executive Officer",
                         transactions=[tx("P", 10, 10.0)]))
    dir_ = F.evaluate(rec(is_director="1", transactions=[tx("P", 10, 10.0)]))
    nobody = F.evaluate(rec(transactions=[tx("P", 10, 10.0)]))
    check("CEO role > director role",
          ceo["role_score"] > dir_["role_score"],
          "%s vs %s" % (ceo["role_score"], dir_["role_score"]))
    check("director role > unclassified",
          dir_["role_score"] > nobody["role_score"],
          "%s vs %s" % (dir_["role_score"], nobody["role_score"]))

    # 10. 10% owner beats director (more of their own money at stake)
    ten = F.evaluate(rec(is_ten_pct_owner="1",
                         transactions=[tx("P", 10, 10.0)]))
    check("10% owner > director", ten["role_score"] > dir_["role_score"],
          "%s vs %s" % (ten["role_score"], dir_["role_score"]))

    # 11. cluster bonus: two DISTINCT insiders buy DIFFERENT amounts.
    # (Real clusters do differ - measured ACCV 2026-10-05: 203200/10000/7000/
    # 5000/2500 shares across 5 insiders. Filers with byte-identical legs AND
    # identical resulting stake are one trade reported twice -> test 14.)
    a = F.evaluate(rec(insider="A", transactions=[tx("P", 100, 100.0)]))
    b = F.evaluate(rec(insider="B", transactions=[tx("P", 700, 100.0)]))
    solo = F.evaluate(rec(insider="A", transactions=[tx("P", 100, 100.0)]))
    F.rank([a, b])
    F.rank([solo])
    check("cluster raises score", a["score"] > solo["score"],
          "%s vs %s" % (a["score"], solo["score"]))
    check("cluster size detected", a["cluster_size"] == 2,
          str(a["cluster_size"]))
    check("cluster appears in why",
          any("2 distinct insiders" in w for w in a["why"]), str(a["why"]))

    # 12. sorting is by score descending
    sigs = F.rank([F.evaluate(rec(insider="LOW", is_director="1",
                                  transactions=[tx("P", 30, 10.0)])),
                   F.evaluate(rec(insider="HIGH", is_ten_pct_owner="1",
                                  transactions=[tx("P", 90000, 20.0)]))])
    check("sorted by score desc", sigs[0]["insider"] == "HIGH",
          str([s["insider"] for s in sigs]))

    # 14. related filers: identical legs + identical resulting stake = ONE trade
    leg = {"date": "2026-09-28", "code": "P", "shares": "10690530",
           "price": "1.51", "owned_after": "20000000"}
    g1 = F.evaluate(rec(insider="GORDON CARL L", is_ten_pct_owner="1",
                        transactions=[dict(leg)]))
    g2 = F.evaluate(rec(insider="ORBIMED ADVISORS LLC", is_ten_pct_owner="1",
                        transactions=[dict(leg)]))
    merged = F.rank([g1, g2])
    check("related filers collapse to one", len(merged) == 1, str(len(merged)))
    check("collapse keeps also_filed_by",
          merged and merged[0].get("also_filed_by"), str(merged[0].get("also_filed_by")))

    # 15. but two DIFFERENT-sized buys by different insiders must NOT collapse
    d1 = F.evaluate(rec(insider="X", transactions=[tx("P", 100, 100.0)]))
    d2 = F.evaluate(rec(insider="Y", transactions=[tx("P", 700, 100.0)]))
    check("different amounts do not collapse", len(F.rank([d1, d2])) == 2,
          str(len(F.rank([d1, d2]))))

    # 13. comma-formatted numbers parse
    s = F.evaluate(rec(transactions=[{"date": "2026-10-01", "code": "P",
                                      "shares": "1,000", "price": "10.5",
                                      "owned_after": "1,000"}]))
    check("comma shares parse", s and s["notional_usd"] == 10500.0,
          str(s and s["notional_usd"]))

    # 14. money outranks role. Regression: a $101 buy by a President ranked
    # #2 above a $199,696 buy (2026-10-06 live run) because rank_key() was
    # role_score first, notional only as a tiebreak.
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
    import render_radar as R  # noqa: E402

    tiny = rec(ticker="AAA", insider="P",
               officer_title="President", transactions=[
                   {"date": "2026-10-01", "code": "P", "shares": "1",
                    "price": "101", "owned_after": "1"}])
    big = rec(ticker="BBB", insider="D", is_director="true", transactions=[
        {"date": "2026-10-01", "code": "P", "shares": "10000",
         "price": "19.9696", "owned_after": "10000"}])
    out = R.render([x for x in (F.evaluate(tiny), F.evaluate(big)) if x],
                   top=5)
    first = out.split("#1")[1].split("\n")[0].strip() if "#1" in out else ""
    check("money outranks role", first == "BBB", "got %r" % first)
    check("tiny purchase filtered out", "#2" not in out,
          "residual: %s" % out.replace("\n", " | ")[:120])

    # 15. fund share classes and CUSIPs are not tradable tickers
    check("fund share class rejected", not R.is_listed("XIVYX"))
    check("CUSIP rejected", not R.is_listed("123456789"))
    check("real ticker kept", R.is_listed("AAPL"))
    check("placeholder rejected", not R.is_listed("N/A"))

    # 16. EMPTY STATE. A dull day must print nothing rather than promote the
    # best of a bad lot. Measured driver: 2026-10-02 had 1502 filings but only
    # 42 buys, and the largest was far below a normal day's top.
    dull = [F.evaluate(rec(ticker="Q%d" % i, insider="D",
                           is_director="true", transactions=[
                               {"date": "2026-10-01", "code": "P",
                                "shares": "100", "price": "150",
                                "owned_after": "100"}]))
            for i in range(20)]
    dull = [x for x in dull if x]
    out = R.render(dull, top=5)
    check("dull day prints empty state", "Nothing above the threshold" in out,
          out.replace("\n", " | ")[:150])
    check("empty state reports the largest seen", "15,000" in out,
          out.replace("\n", " | ")[:150])
    check("empty state emits no ranking", "#1" not in out)
    # Wording must not imply a dull day forecasts anything. The backtest found
    # no relation between notional and forward return, so the copy is checked
    # against claiming the day was bad.
    check("empty state makes no quality claim",
          "quiet" not in out.lower(), out.replace("\n", " | ")[:150])

    # 17. RELATIVE ranking. Same dollar amount must rank differently depending
    # on the day it sits in. $400k is the top of a $150-500k day but merely
    # mid-pack on a day with several $10M+ buys.
    def sig(tk, ins, price, shares="1000", title="", director="false"):
        return F.evaluate(rec(ticker=tk, insider=ins, officer_title=title,
                              is_director=director, transactions=[
                                  {"date": "2026-10-01", "code": "P",
                                   "shares": shares, "price": price,
                                   "owned_after": shares}]))

    # Quiet day: $400k is the biggest thing there is.
    quiet = [x for x in (sig("SMALL", "A", "100"), sig("MID", "B", "400"),
                         sig("ALSOMID", "C", "300")) if x]
    out_q = R.render(quiet, top=3)
    top_q = out_q.split("#1")[1].split("\n")[0].strip() if "#1" in out_q else ""
    check("quiet day: largest ranks first", top_q == "MID", "got %r" % top_q)

    # Loud day: the same $400k now competes with $10M+.
    loud = [x for x in (sig("SMALL", "A", "100"), sig("MID", "B", "400"),
                        sig("HUGE", "C", "10000"), sig("BIGGER", "D", "20000"))
            if x]
    out_l = R.render(loud, top=3)
    top_l = out_l.split("#1")[1].split("\n")[0].strip() if "#1" in out_l else ""
    check("loud day: $20M outranks $400k", top_l == "BIGGER",
          "got %r" % top_l)

    # 18. percentile helper: midpoint convention, so the largest value is
    # below 1.0 (it is not "greater than itself") and ties share a rank.
    check("percentile: largest is below 1.0",
          abs(R._percentile_rank([1, 2, 3], 3) - (2.5 / 3.0)) < 1e-9,
          str(R._percentile_rank([1, 2, 3], 3)))
    check("percentile: bottom is not zero",
          R._percentile_rank([1, 2, 3], 1) > 0.0)
    check("percentile: ties share rank",
          R._percentile_rank([5, 5, 5], 5) == 0.5)
    check("percentile: empty is safe", R._percentile_rank([], 1) == 0.0)

    # 19. BONUS CANNOT CROSS AN ORDER OF MAGNITUDE. Caps are load-bearing:
    # a 3-insider $33M cluster beat a single $157M filing when cluster bonus
    # was 12.7 against a 6.8-point money gap (2026-10-06, sample.json). One
    # order of magnitude = 10 points, so every bonus is capped below 10.
    check("role score capped below an order of magnitude",
          R.role_score("chief executive officer", False, False)
          <= R.ROLE_SCORE_MAX + 1e-9,
          str(R.role_score("chief executive officer", False, False)))
    check("cluster bonus capped below an order of magnitude",
          R.CLUSTER_BONUS_MAX < 10.0, str(R.CLUSTER_BONUS_MAX))

    # Ordering across a large multiple must follow money, not cluster:
    # a single $100M buy outranks a 5-insider $10M cluster.
    solo = sig("SOLO", "A", "100000.0", shares="1000")
    many = [sig("MANY", "P%d" % i, "2000.0", shares="1000")
            for i in range(5)]
    both = [x for x in ([solo] + many) if x]
    out_b = R.render(both, top=3)
    top_b = out_b.split("#1")[1].split("\n")[0].strip() if "#1" in out_b else ""
    check("money beats cluster across magnitudes", top_b == "SOLO",
          "got %r" % top_b)

    # 20. PERCENTILE IS A GATE, NOT A SORT KEY. Sorting by percentile
    # flattened magnitude: $157M and $33M are 4.7x apart but ~5 percentile
    # points apart, so bonuses swamped it and $2.1M ranked above $53.9M.
    check("percentile used as gate constant", 0 < R.PUSH_PERCENTILE < 1)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    lines = ["# insider_filter self-test", ""]
    for name, ok, detail in RESULTS:
        lines.append("  %s %s%s" % ("PASS" if ok else "FAIL", name,
                                    ("  <- %s" % detail) if detail and not ok
                                    else ""))
    lines.append("")
    lines.append("TOTAL %d/%d passed" % (passed, len(RESULTS)))
    text = "\n".join(lines)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
