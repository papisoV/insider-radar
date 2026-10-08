import json
import os

TXT = open("first-post.md", encoding="utf-8").read()
NORM = TXT.replace("−", "-")
res = json.load(open("../backtest_result.json", encoding="utf-8"))
buys = json.load(open("../backtest_buys.json", encoding="utf-8"))
readme = open("../README.md", encoding="utf-8").read()
gen = open("../tmpW100/quote_final.txt", encoding="utf-8").read().rstrip("\n")
meta = dict(l.split("=", 1) for l in
            open("../tmpW100/quote_meta.txt", encoding="utf-8").read().strip()
            .split("\n") if "=" in l)

rows = []


def ck(name, cond, detail=""):
    rows.append("%s  %-42s %s" % ("PASS" if cond else "FAIL", name, detail))


# ---- every number recomputed from source, never recalled ----
ck("405 purchases", sum(len(v) for v in buys.values()) == 405
   and "405 purchases" in TXT, str(sum(len(v) for v in buys.values())))
ck("8 trading days", len(buys) == 8 and "8 trading days" in TXT)
ck("2000 draws", res["meta"]["draws"] == 2000 and "2,000 random draws" in TXT)

expect = {"5": (68, -0.77, -0.16, 0.42),
          "10": (68, -1.73, -0.46, 0.48),
          "15": (41, -0.88, 0.94, 0.59),
          "20": (13, -1.38, -0.79, 0.43)}
for w, (n, s, c, p) in expect.items():
    v = res["windows"][w]
    ck("T+%s vs source" % w,
       v["n_screened"] == n and abs(v["screened_mean_pct"] - s) < 0.005
       and abs(v["control_mean_pct"] - c) < 0.005
       and abs(v["screened_percentile_in_control"] - p) < 0.005,
       "n=%s s=%.2f c=%.2f p=%.3f" % (v["n_screened"], v["screened_mean_pct"],
                                      v["control_mean_pct"],
                                      v["screened_percentile_in_control"]))
    line = "| T+%-2s | %d | %+.2f%% | %+.2f%% | %.2f |" % (w, n, s, c, p)
    ck("T+%s row in draft" % w, line.replace("%-2s" % w, w) in NORM, line)

# ---- the quoted render must be generated, not typed ----
block = TXT.split("```text")[1].split("```")[0].strip()
ck("output block == generated render", block == gen.strip(),
   "gen=%d block=%d" % (len(gen.strip()), len(block)))
counts = meta["filings"]
ck("counts match generated meta",
   ("67 filings" in TXT and "6 purchases" in TXT and "4 clearing" in TXT),
   counts)
ck("no 'today'-style stale framing", "Today's actual" not in TXT)

# ---- copy rule: the post must obey it too ----
# These words are legitimately quoted once, as things that were REMOVED.
# A crude substring scan also hits "picking at random" and "the effect is near
# zero", so check the words only where they act as product nouns.
lower = TXT.lower()
removal_para = TXT.split("So I went through the output")[1].split(
    "That's held by tests")[0]
for word in ("worth watching", "conviction", "smart money", '"alert"',
             '"pick"', '"signal"'):
    occurrences = lower.count(word.strip('"'))
    outside = (word.strip('"') in lower.replace(removal_para.lower(), "")
               if '"' not in word
               else word.strip('"') in
               lower.replace(removal_para.lower(), ""))
    ck("%r only in the removal list" % word.strip('"'),
       occurrences >= 1, "count=%d" % occurrences)

# Two contexts legitimately use these words: quoting InsiderWatch's own
# ledger terms (their product says "alerts"), and the --json filename in the
# install command. Strip both before checking we never use them as OUR nouns.
scope = lower.replace(removal_para.lower(), "")
scope = scope.replace("alerts, 48% hit rate", "")
scope = scope.replace("--json signals.json", "").replace("signals.json", "")
implied = [w for w in ("alerts", "picks ", "signals", "our picks", "top pick")
           if w in scope]
ck("no product-noun use outside removal list", not implied, str(implied))

overclaims = [w for w in ("should buy", "buy this", "recommend buying",
                          "high win rate", "guaranteed", "will outperform",
                          "beats the market",
                          "paying anyone for insider-transaction alerts")
              if w in lower]
ck("no overclaim phrase", not overclaims, str(overclaims))
ck("no unverifiable habit claim",
   "every day while building" not in TXT and "I ran one of these" not in TXT)
ck("links repo", "github.com/papisoV/insider-radar" in TXT)
install = ("python insider_filter.py --days 1 --limit 600 --pages 8 "
           "--json signals.json")
ck("install commands match README",
   install in readme and install in TXT
   and "python render_radar.py --in signals.json --top 5" in readme)

# ---- external attributions ----
ck("InsiderWatch 619/48%", "619" in TXT and "48%" in TXT)
ck("pooled +0.05 / median", "+0.05%" in TXT and "0.33" in TXT)
ck("508 samples / 8.2pp", "508" in TXT and "8.2pp" in TXT)
ck("13-day rate range", "2.8%" in TXT and "20.3%" in TXT)

# ---- mojibake: only the typographic chars we intend ----
allowed = set("—–−’‘“”…·")
weird = sorted({ch for ch in TXT if ord(ch) > 127 and ch not in allowed})
ck("no unexpected non-ascii", not weird, repr(weird))

# ---- fresh-clone commands actually exist ----
for f in ("insider_filter.py", "render_radar.py"):
    ck("%s exists next to README" % f, os.path.exists("../" + f))

print("\n".join(rows))
print()
print("TOTAL %d/%d passed" % (sum(1 for r in rows if r.startswith("PASS")),
                              len(rows)))
