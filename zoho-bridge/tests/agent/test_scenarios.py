# Runs every YAML scenario in suites/ through the agent with real model calls.
# Filter: SCENARIOS=<suite-or-id>[,..] pytest tests/agent  ·  SKIP_JUDGE=1 to
# skip the delight judge.  Each scenario asserts hard checks first, judge last.

import asyncio
import os

import pytest

from conftest import judge_transcript, load_scenarios

SCENARIOS = load_scenarios()


@pytest.mark.parametrize("scenario", SCENARIOS,
                         ids=[s["id"] for s in SCENARIOS])
def test_scenario(scenario, engine_factory):
    eng = engine_factory(scenario)
    asyncio.get_event_loop().run_until_complete(eng.run())
    errors, sent, cards = eng.check()
    dump = os.environ.get("DUMP_TRANSCRIPTS")
    if dump:
        import json as _json
        import re as _re
        import config as _config
        prof = scenario.get("profile") or {}
        ident, loc = prof.get("identity") or {}, prof.get("location") or {}
        summary = ", ".join(x for x in [
            ("phone …" + _re.sub(r"\D", "", (ident.get("phone") or {}).get("value", ""))[-4:])
            if ident.get("phone") else "",
            (loc.get("city") or {}).get("value", ""),
            (loc.get("pincode") or {}).get("value", ""),
            f"{len(prof.get('events') or [])} events" if prof.get("events") else "",
            f"{scenario['profile_bulk_events']} bulk events"
            if scenario.get("profile_bulk_events") else ""] if x)
        context = {
            "profile_set": bool(prof or scenario.get("profile_bulk_events")),
            "profile_summary": summary,
            "history": scenario.get("history") or [],
            "prior_conversations": [
                {"comment": bool(p.get("comment")), "messages": p.get("messages") or []}
                for p in scenario.get("prior_conversations") or []],
            "offers": [o.get("caption") for o in scenario.get("offers") or []],
            "crm_fixture": bool((scenario.get("crm") or {}).get("contact")),
            "linked": len(scenario.get("linked_contacts") or [])}
        with open(dump, "a", encoding="utf-8") as f:
            f.write(_json.dumps({
                "suite": scenario["suite"], "id": scenario["id"],
                "model": _config.SOCIAL_AGENT_MODEL,
                "surface": scenario.get("surface") or "dm",
                "passed": not errors, "errors": errors, "context": context,
                "steps": getattr(eng, "timeline", [])}, ensure_ascii=False) + "\n")
    assert not errors, f"[{scenario['suite']}/{scenario['id']}] " + \
        "; ".join(errors) + f"\n--- sent ---\n{sent}\n--- cards ---\n{cards}"

    exp = scenario.get("expect") or {}
    judge = exp.get("judge")
    if judge and not os.environ.get("SKIP_JUDGE"):
        surface = scenario.get("surface") or "dm"
        convo = ["Surface: public comment reply under a post"
                 if surface == "comment" else "Surface: private DM"]
        prof = scenario.get("profile") or {}
        facts = []
        if (prof.get("identity") or {}).get("phone"):
            facts.append("phone number on file")
        shr = ((prof.get("commercial") or {}).get("showroom") or {}).get("value")
        if shr:
            facts.append(f"already routed to showroom {shr} — enquiry registered")
        city = ((prof.get("location") or {}).get("city") or {}).get("value")
        if city:
            facts.append(f"city {city}")
        for ev in (prof.get("events") or [])[-3:]:
            if ev.get("what"):
                facts.append(f"{ev.get('kind')}: {ev.get('what')}")
        if facts:
            convo.append("Customer profile on file BEFORE this exchange: "
                         + ", ".join(facts))
        for h in scenario.get("history") or []:
            who = ("Customer" if h.get("who", "customer") == "customer"
                   else "Assistant")
            convo.append(f"[earlier in this conversation] {who}: "
                         f"{h.get('text', '')}")
        for step in scenario.get("script") or []:
            convo.append(f"Customer: {step.get('text', '')}")
        if eng.tools_trail:
            convo.append("--- tools the assistant called (results are ground "
                         "truth; facts from them are NOT invented) ---")
            convo.extend(eng.tools_trail)
        convo.append("--- assistant output ---")
        convo.append(sent or cards)
        verdict = asyncio.get_event_loop().run_until_complete(
            judge_transcript("\n".join(convo)))
        assert verdict.get("score", 0) >= judge.get("min", 3), \
            f"judge {verdict.get('score')} < {judge.get('min', 3)}: " \
            f"{verdict.get('reason')}\n--- sent ---\n{sent}"


def test_skills_md_fresh():
    import social_agent
    from pathlib import Path
    disk = Path(social_agent.__file__).parent / "SKILLS.md"
    assert disk.read_text(encoding="utf-8") == social_agent.generate_skills_md(), \
        "SKILLS.md is stale — run social_agent.write_skills_md()"
