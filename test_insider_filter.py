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
