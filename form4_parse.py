"""Parse insider-buying signals out of SEC Form 4 XML.

End-to-end verified W98: EFTS discovers the filing -> Archives serves the XML
-> this parses it into a usable insider signal.

Verified samples (all http=200, fetched 2026-10-06):
  .../data/1087939/000119312526409116/ownership.xml
      Kodiak Sciences Inc. (KOD) / BAKER BROS. ADVISORS LP / code P
      856 shares @ $61.2104 / director=true / 10% owner=true
  .../data/1869814/000186981426000009/primarydocument.xml
      Xometry, Inc. (XMTR) / Altschuler Randolph / code S / 2026-10-01

Transaction codes (SEC Form 4, Table II):
  P purchase  S sale  A award/grant  D disposition to issuer
  F tax withholding  G gift  M option exercise  X option exercise
  J other acquisition/disposition

CORRECTED 2026-10-06: SEC officially defines P as "Open market OR PRIVATE
purchase of non-derivative OR DERIVATIVE security" (verified against
sec.gov/edgar/searchedgar/ownershipformcodes.html). So we must NOT describe
every P as an "open-market buy" in user-facing copy - that overstates what the
filing proves. Same for S.
"""

import re
import ssl
import sys
import urllib.request

# SEC blocks browser UAs (403) and accepts declared script UAs. Verified
# 2026-10-06: urllib + this UA = 200/68257B, while curl -sL with the same UA
# and a browser UA both = 403. So fetch via urllib, not subprocess curl.
UA = "sec-fact-monitor/1.0 (research script; contact: example.com)"

BUY_CODES = {"P"}
SELL_CODES = {"S", "D"}


def fetch(url, timeout=40):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Encoding": "identity"}
    )
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read().decode("utf-8", "replace"), str(resp.status)


def _tag(body, tag):
    """First value of <tag>...</tag>, inner tags stripped."""
    m = re.search(r"<%s[^>]*>(.*?)</%s>" % (tag, tag), body, re.S)
    if not m:
        return ""
    return re.sub(r"<[^>]+>", "", m.group(1)).strip()


def _all(block, tag):
    return [re.sub(r"<[^>]+>", "", v).strip()
            for v in re.findall(r"<%s[^>]*>(.*?)</%s>" % (tag, tag), block, re.S)]


def parse_form4(xml):
    """Return a dict describing one Form 4. Empty dict if not parseable."""
    issuer = _tag(xml, "issuerName")
    if not issuer:
        return {}
    rec = {
        "issuer": issuer,
        "ticker": _tag(xml, "issuerTradingSymbol"),
        "insider": _tag(xml, "rptOwnerName"),
        "is_director": _tag(xml, "isDirector"),
        "is_officer": _tag(xml, "isOfficer"),
        "is_ten_pct_owner": _tag(xml, "isTenPercentOwner"),
        "officer_title": _tag(xml, "officerTitle"),
        "period": _tag(xml, "periodOfReport"),
    }
    # Non-derivative legs only. Derivative legs (options/warrants) live in
    # <derivativeTransaction> and are deliberately NOT parsed: their "price"
    # is a strike, not what was paid, so folding them into a notional would
    # invent money that never changed hands. Measured 2026-10-06 on one
    # quarter-end day (2026-09-30, n=210): 23% of Form 4s carry a derivative
    # block, and 6% of P-code filings have at least one P inside one - i.e. a
    # small tail where we see only part of the purchase.
    txs = []
    for blk in re.findall(r"<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>",
                          xml, re.S):
        code = _tag(blk, "transactionCode")
        shares = _all(blk, "transactionShares")
        price = _all(blk, "transactionPricePerShare")
        date = _all(blk, "transactionDate")
        after = _all(blk, "sharesOwnedFollowingTransaction")
        acq = _tag(blk, "transactionAcquiredDisposedCode")
        txs.append({
            "date": date[0] if date else "",
            "code": code,
            "acquired_or_disposed": acq,          # A = acquired, D = disposed
            "shares": shares[0] if shares else "",
            "price": price[0] if price else "",
            "owned_after": after[0] if after else "",
        })
    rec["transactions"] = txs
    rec["is_open_market_buy"] = any(
        t["code"] in BUY_CODES for t in txs)
    rec["is_open_market_sell"] = any(
        t["code"] in SELL_CODES for t in txs)
    return rec


def main(urls):
    for u in urls:
        body, code = fetch(u)
        if code != "200" or len(body) < 500:
            print("### %s\n  http=%s bytes=%d  -> BLOCKED/EMPTY" % (u, code, len(body)))
            continue
        rec = parse_form4(body)
        if not rec:
            print("### %s\n  http=%s bytes=%d -> not a parseable Form 4" % (u, code, len(body)))
            continue
        print("### %s" % u)
        print("  %s (%s)  insider=%s" % (rec["issuer"], rec["ticker"], rec["insider"]))
        print("  director=%s officer=%s 10%%owner=%s title=%s"
              % (rec["is_director"], rec["is_officer"],
                 rec["is_ten_pct_owner"], rec["officer_title"] or "-"))
        for t in rec["transactions"][:5]:
            print("    %s  code=%s (%s)  shares=%s @ %s  owned_after=%s"
                  % (t["date"], t["code"], t["acquired_or_disposed"],
                     t["shares"], t["price"], t["owned_after"]))
        print("  PURCHASE(P)=%s  SALE(S)=%s"
              % (rec["is_open_market_buy"], rec["is_open_market_sell"]))
        print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python form4_parse.py <form4-xml-url> [<url2> ...]")
        print("example:")
        print("  python form4_parse.py \\")
        print("    https://www.sec.gov/Archives/edgar/data/1087939/000119312526409116/ownership.xml")
        sys.exit(1)
    main(sys.argv[1:])
