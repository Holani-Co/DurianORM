"""Offline smoke test for identity_matcher.score_pair (no network, no LLM).

Exercises the scenarios from the design discussion:
  1. Same person, shared email           -> near-certain
  2. Gift: two different people, same     -> NO MATCH (Gate 1 shut)
     order, sibling surname, diff phones
  3. Shared family phone, different names -> surfaced but capped + cautioned
  4. Name-only fuzzy match               -> possible + cautioned
  5. Returning customer, diff orders,     -> near-certain (email stable)
     same email across channels
  6. Placeholder names ("Instagram User") -> must not open the name gate

Run:  python scripts/smoke_test_identity_match.py
Exit code 0 = all assertions passed.
"""
import os
import sys

# score_pair is pure, but importing the module pulls in config (which needs
# env). Stub the few vars config._required() insists on so the import works
# without a real .env.
os.environ.setdefault("ZOHO_CLIENT_ID", "x")
os.environ.setdefault("ZOHO_CLIENT_SECRET", "x")
os.environ.setdefault("ZOHO_REFRESH_TOKEN", "x")
os.environ.setdefault("ZOHO_ORG_ID", "x")
os.environ.setdefault("ZOHO_DEPARTMENT_ID", "x")
os.environ.setdefault("CHATWOOT_API_TOKEN", "x")
os.environ.setdefault("OPENAI_API_KEY", "x")
os.environ.setdefault("TEAM_ID_LEGAL", "1")
os.environ.setdefault("TEAM_ID_MARKETING", "2")
os.environ.setdefault("TEAM_ID_HR", "3")
os.environ.setdefault("TEAM_ID_SUPPORT", "4")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import identity_matcher as im  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")


def sig(name="", emails=None, phones=None):
    return {
        "name": name,
        "emails": set(emails or []),
        "phones": set(phones or []),
    }


print("1. Same person, shared email + name")
r = im.score_pair(
    sig("Rahul Kumar", ["rahul@gmail.com"], ["9876543210"]),
    sig("Rahul Kumar", ["rahul@gmail.com"], ["9876543210"]),
)
check("scored", r is not None)
check("near_certain", r and r["band"] == "near_certain")
check("score >= 86", r and r["score"] >= 86)
print(f"     -> score={r['score']} band={r['band']}")

print("2. Gift scenario: brother vs sister, same order, diff identifiers")
# Same order id is NOT a signal here (booster-only / deferred) — the point is
# their personal identifiers all differ, so Gate 1 must stay shut.
r = im.score_pair(
    sig("Rahul Sharma", ["rahul@gmail.com"], ["9876543210"]),
    sig("Priya Sharma", ["priya@gmail.com"], ["9990001112"]),
)
check("NO MATCH (Gate 1 shut)", r is None)

print("3. Shared family phone, different first names")
r = im.score_pair(
    sig("Rahul Sharma", [], ["9876543210"]),
    sig("Priya Sharma", [], ["9876543210"]),
)
check("scored (phone opened gate)", r is not None)
check("not near_certain (phone-only cap)", r and r["band"] != "near_certain")
check("has caution note", r and "family" in (r["note"] or "").lower())
print(f"     -> score={r['score']} band={r['band']} note={r['note']!r}")

print("4. Name-only fuzzy match, no contact details")
r = im.score_pair(
    sig("Rahul Kumar", [], []),
    sig("Rahuul Kumar", [], []),
)
check("scored (name opened gate)", r is not None)
check("possible band", r and r["band"] == "possible")
check("has caution note", r and bool(r["note"]))
print(f"     -> score={r['score']} band={r['band']}")

print("5. Returning customer: diff orders/channels, same email")
r = im.score_pair(
    sig("R. Kumar", ["rahul@gmail.com"], []),          # IG, sparse name
    sig("Rahul Kumar", ["rahul@gmail.com"], ["9876543210"]),
)
check("scored", r is not None)
# Email-only is "likely" by design (near-certain needs email + corroboration).
check("high confidence (>=61)", r and r["score"] >= 61)
print(f"     -> score={r['score']} band={r['band']}")

print("6. Placeholder names must not open the name gate")
r = im.score_pair(
    sig("Instagram User", [], []),
    sig("Instagram User", [], []),
)
check("NO MATCH (placeholder names ignored)", r is None)

print("7. Token-reorder + email -> still strong")
r = im.score_pair(
    sig("Kumar Rahul", ["rahul@gmail.com"], []),
    sig("Rahul Kumar", ["rahul@gmail.com"], []),
)
check("scored", r is not None)
check("name counted (exact via tokens)", r and any(m["type"] == "name" for m in r["matched"]))
print(f"     -> score={r['score']} band={r['band']}")

print()
print(f"RESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
