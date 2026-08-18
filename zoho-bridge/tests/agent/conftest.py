# Agent-mode scenario harness.
#
# Everything is faked EXCEPT the model: FakeChatwoot records every attribute /
# label / message write for assertions; CRM + Snapmint return fixtures; the
# clock is injectable so scenario messages spread across days ("at: -4d").
# Scenarios are YAML (suites/*.yaml); each script step feeds one incoming
# message through social_agent.maybe_handle and the expectations run at the
# end — hard assertions plus an optional LLM judge (skipped with SKIP_JUDGE=1).

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import chatwoot                     # noqa: E402
import config                       # noqa: E402
import customer_profile             # noqa: E402
import product_catalog              # noqa: E402
import product_images               # noqa: E402
import snapmint                     # noqa: E402
import social_agent                 # noqa: E402
import website_search               # noqa: E402
import zoho_crm                     # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
BASE_NOW = datetime(2026, 8, 12, 15, 0, tzinfo=IST)

_OFF_RE = re.compile(r"([+-]?)(\d+)([dhm])")


def parse_offset(spec) -> timedelta:
    total, sign = timedelta(), 1
    for s, n, unit in _OFF_RE.findall(str(spec or "0m")):
        sign = -1 if s == "-" else (1 if s == "+" else sign)
        total += sign * timedelta(**{{"d": "days", "h": "hours",
                                      "m": "minutes"}[unit]: int(n)})
    return total


def load_scenarios():
    out = []
    for f in sorted((Path(__file__).parent / "suites").glob("*.yaml")):
        for sc in yaml.safe_load(f.read_text()) or []:
            sc["suite"] = f.stem
            out.append(sc)
    only = os.environ.get("SCENARIOS")
    if only:
        keys = {k.strip() for k in only.split(",")}
        out = [s for s in out if s["id"] in keys or s["suite"] in keys]
    return out


class FakeChatwoot:
    """In-memory Chatwoot: conversations, messages, contact records (profile
    persistence!), labels, sends, notes — everything recorded for asserts."""

    def __init__(self, scenario):
        self.sc = scenario
        self.contact = dict(scenario.get("contact") or {"id": 900, "name": "Test Customer"})
        self.contact_attrs: dict = {}
        self.msg_id = 1000
        self.conv_seq = 100
        self.conversations: list[dict] = []      # all convs incl. finished ones
        self.conv = None
        self.public_sends: list[dict] = []
        self.cards: list[dict] = []
        self.notes: list[str] = []
        self.offer_sends: list[dict] = []
        self.teams: list[int] = []
        self.assignments: list[dict] = []
        self.deal_calls: list = []
        self.new_conversation(scenario.get("inbox") or "durianfurniture_official",
                              comment=scenario.get("surface") == "comment")

    def new_conversation(self, inbox=None, comment=False):
        self.conv_seq += 1
        self.conv = {"id": self.conv_seq,
                     "meta": {"sender": self.contact},
                     "inbox": {"name": inbox or (self.conv["inbox"]["name"]
                                                 if self.conv else "durianfurniture_official")},
                     "custom_attributes": {}, "labels": ["comment"] if comment else [],
                     "messages": [], "created_at": None, "last_activity_at": None}
        self.conversations.append(self.conv)
        return self.conv

    def add_message(self, text, when: datetime, incoming=True, conv=None,
                    content_attributes=None, photo=False):
        conv = conv or self.conv
        self.msg_id += 1
        m = {"id": self.msg_id, "content": text,
             "message_type": 0 if incoming else 1,
             "created_at": int(when.timestamp()), "private": False,
             "content_attributes": content_attributes or {}}
        if photo:
            m["attachments"] = [{"data_url": "https://fake.local/room.jpg",
                                 "file_type": "image"}]
        conv["messages"].append(m)
        conv["created_at"] = conv["created_at"] or int(when.timestamp())
        conv["last_activity_at"] = int(when.timestamp())
        return m

    # ── chatwoot API surface ────────────────────────────────────────────
    async def get_conversation_messages_raw(self, conv_id):
        for c in self.conversations:
            if c["id"] == conv_id:
                return list(c["messages"])
        return []

    async def get_contact_conversations(self, contact_id):
        return [dict(c) for c in reversed(self.conversations)]

    async def merge_custom_attributes(self, conv_id, attrs):
        for c in self.conversations:
            if c["id"] == conv_id:
                for k, v in attrs.items():
                    if v is None:
                        c["custom_attributes"].pop(k, None)
                    else:
                        c["custom_attributes"][k] = v
        return {}

    async def add_label(self, conv_id, label):
        for c in self.conversations:
            if c["id"] == conv_id and label not in c["labels"]:
                c["labels"].append(label)
        return {}

    async def remove_label(self, conv_id, label):
        for c in self.conversations:
            if c["id"] == conv_id and label in c["labels"]:
                c["labels"].remove(label)
        return {}

    async def create_message(self, conv_id, content, message_type="outgoing",
                             private=False, content_attributes=None, **kw):
        rec = {"conv": conv_id, "content": content, "private": private,
               "attrs": content_attributes or {}}
        (self.cards if private else self.public_sends).append(rec)
        if not private:
            for c in self.conversations:
                if c["id"] == conv_id:
                    self.msg_id += 1
                    c["messages"].append({
                        "id": self.msg_id, "content": content, "message_type": 1,
                        "created_at": int(customer_profile.now().timestamp()),
                        "private": False,
                        "content_attributes": content_attributes or {}})
        return {"id": self.msg_id}

    async def post_private_note(self, conv_id, content):
        self.notes.append(content)
        return {}

    async def get_contact(self, contact_id):
        return {"id": contact_id, "name": self.contact.get("name"),
                "custom_attributes": dict(self.contact_attrs)}

    async def update_contact_attributes(self, contact_id, custom_attributes):
        self.contact_attrs.update(custom_attributes)
        return {}

    async def search_contacts(self, query):
        return list(self.sc.get("linked_contacts") or [])

    async def list_canned_responses(self):
        return _TEMPLATES

    async def get_offers(self):
        return list(self.sc.get("offers") or [])

    async def send_offer_message(self, conv_id, caption, image_url, link=""):
        self.offer_sends.append({"conv": conv_id, "caption": caption,
                                 "image_url": image_url, "link": link})
        return {"id": 1}

    async def send_image_bytes(self, conv_id, caption, content,
                               ctype="image/png"):
        self.offer_sends.append({"conv": conv_id, "caption": caption,
                                 "image_url": "generated://preview"})
        return {"id": 1}

    async def assign_team(self, conv_id, team_id):
        self.teams.append(team_id)
        return {}

    async def assign_agent(self, conv_id, assignee_id):
        self.assignments.append({"conv": conv_id, "assignee_id": assignee_id})
        for c in self.conversations:
            if c["id"] == conv_id:
                c["meta"]["assignee"] = {"id": assignee_id, "name": "DurianAI"}
        return {}

    async def get_profile(self):
        return {"id": 3, "name": "DurianAI"}


def _load_templates():
    path = Path(__file__).resolve().parents[2] / "social_templates.yaml"
    entries = (yaml.safe_load(path.read_text()) or {}).get("templates") or []
    return [{"short_code": t.get("short_code"), "content": t.get("content") or ""}
            for t in entries if t.get("short_code")]


_TEMPLATES = _load_templates()


class Engine:
    def __init__(self, scenario, monkeypatch):
        self.sc = scenario
        self.now = BASE_NOW
        self.fake = FakeChatwoot(scenario)
        self.results = []
        self.tools_called = []
        customer_profile.set_now(lambda: self.now)

        for name in ("get_conversation_messages_raw", "get_contact_conversations",
                     "merge_custom_attributes", "add_label", "remove_label",
                     "create_message", "post_private_note", "get_contact",
                     "update_contact_attributes", "search_contacts",
                     "list_canned_responses", "get_offers", "send_offer_message",
                     "send_image_bytes", "assign_team", "assign_agent",
                     "get_profile"):
            monkeypatch.setattr(chatwoot, name, getattr(self.fake, name))

        async def fake_emi(price, skuid, **kw):
            p = float(price)
            return {"emi_available": True, "down_payment": str(int(p * 0.1)),
                    "processing_fee": "0", "min_interest_rate": 0,
                    "plans": [
                        {"months": 6, "emi": str(int(p / 6)), "zero_cost": True,
                         "total_payment": str(int(p)), "interest": 0},
                        {"months": 9, "emi": str(int(p / 9)), "zero_cost": True,
                         "total_payment": str(int(p)), "interest": 0},
                        {"months": 12, "emi": str(int(p * 1.09 / 12)),
                         "zero_cost": False, "total_payment": str(int(p * 1.09)),
                         "interest": int(p * 0.09)}]}
        monkeypatch.setattr(snapmint, "get_emi", fake_emi)

        crm = scenario.get("crm") or {}

        async def fake_crm_search(phone):
            return crm.get("contact")

        async def fake_crm_deals(contact_id):
            return crm.get("deals") or []
        monkeypatch.setattr(zoho_crm, "search_contact_by_phone", fake_crm_search)
        monkeypatch.setattr(zoho_crm, "get_contact_deals", fake_crm_deals)

        self.preview_calls = []

        async def fake_preview(room_url, product_url, name, placement="",
                               swatch_url="", **_):
            self.preview_calls.append({"product": name, "placement": placement,
                                       "swatch_url": swatch_url})
            return b"fake-preview-bytes"
        monkeypatch.setattr(social_agent, "_generate_room_preview", fake_preview)

        # Reference vetting → deterministic: the variant's front image, vetted
        # usable + colour-matching unless the scenario's `reference_vet:` dict
        # overrides (usable: false = swatch-only family).
        _vet = scenario.get("reference_vet")

        async def fake_pick_reference(fam, prefer):
            ph, _ = product_images.share_set(fam, prefer=prefer or None,
                                             compare=True)
            if not ph:
                return {}
            name, url = ph[0]
            base = {"url": url, "name": name, "usable": True,
                    "matches_colour": True, "swatch": url}
            base.update(_vet or {})
            return base
        monkeypatch.setattr(social_agent, "_pick_reference",
                            fake_pick_reference)

        # Room analysis (placement obviousness) → the scenario's
        # `room_analysis:` dict verbatim; absent → {} → the skill treats the
        # room as ambiguous and asks the placement question.
        _analysis = scenario.get("room_analysis")

        async def fake_analyze(room_url, product_desc):
            return dict(_analysis or {})
        monkeypatch.setattr(social_agent, "_analyze_room", fake_analyze)

        # Live-site search (website_search.search) → deterministic storefront
        # results, priced consistently with the catalog fixtures so EMI math
        # and history text line up. `website_results:` in a scenario overrides
        # every query with that list.
        site_override = scenario.get("website_results")

        def _prod(title, cat, sp, mrp, slug, excl=False):
            return {"title": title, "category": cat, "selling_price": sp,
                    "mrp": mrp,
                    "url": f"https://www.durian.in/product/{slug}",
                    "image": f"https://images.durian.in/{slug}.jpg",
                    "in_store_exclusive": excl}
        _CORNER = [
            _prod("Benjamin Corner", "Sectional Sofas", 120480, 240960,
                  "benjamin-corner-i-ash-grey-premium-leatherette-7-seater-corner-sofa"),
            _prod("Lewis", "Sectional Sofas", 109520, 219040,
                  "lewis-dark-oak-brown-fabric-7-seater-sectional-sofa")]
        _DINING = [_prod("Esmeralda", "Dining Sets", 204880, 409760,
                         "esmeralda2-marble-dining-set-1-6")]
        _RECLINER = [_prod("Valerano", "Recliners", 144900, 289800,
                           "valerano-coffee-brown-leather-3-seater-recliner")]
        _SOFAS = [_prod("Benjamin", "Sofas", 120480, 240960,
                        "benjamin-i-leatherette-3-seater-sofa"),
                  _prod("Clarkson", "Sofas", 43800, 87600,
                        "clarkson-premium-leatherette-3-seater-camel-brown-sofa")]
        _BEDS = [_prod("Alister", "Beds", 98000, 196000,
                       "alister-upholstered-queen-bed")]
        _WARDROBE = [_prod("Hanson", "Wardrobes", 76000, 152000,
                           "hanson-4-door-wardrobe")]

        def _catalog_mirror(q, rows):
            # Per-SKU top matches, one row per family — like real Unbxd rows,
            # so "esmeralda dining set 1+6" prices the SET, not the cheapest
            # chair in the family.
            out, seen = [], set()
            for t in product_catalog.search(q, limit=rows * 3):
                fam = (t.get("family") or "").strip()
                if not fam or fam in seen:
                    continue
                seen.add(fam)
                out.append({
                    "title": fam.title(),
                    "category": (t.get("category") or "Furniture").title(),
                    "selling_price": t.get("sale_price"),
                    "mrp": t.get("mrp"),
                    "url": product_images.link(fam) or
                           "https://www.durian.in/product/" +
                           fam.lower().replace(" ", "-"),
                    "image": "", "in_store_exclusive": False})
                if len(out) >= rows:
                    break
            return out

        # First tokens of catalog family names ("vivian", "meagan"…) so a
        # customer who NAMES a product always gets that product, even when a
        # category word ("recliner") appears in the same query.
        _GENERIC = {"corner", "sofa", "sofas", "bed", "beds", "dining",
                    "recliner", "wardrobe", "door", "set", "single", "study",
                    "office", "coffee", "side", "king", "queen", "tv"}
        _FAMILY_TOKENS = {
            t for p in json.loads(
                (Path(__file__).resolve().parents[2] /
                 "data" / "product_catalog.json").read_text())["products"].values()
            for t in [((p.get("family") or "").lower().split() or [""])[0]]
            if t and t not in _GENERIC}

        def _site_rows(query, rows):
            q = (query or "").lower()
            if site_override is not None:
                return [dict(p) for p in site_override][:rows]
            # Curated rows FIRST — their prices anchor scenario histories, so
            # a re-search mid-conversation must return the same figures.
            if "lewis" in q:
                return [_CORNER[1]]
            if "clarkson" in q:
                return [_SOFAS[1]]
            if "benjamin" in q:
                return ([_CORNER[0]] if "corner" in q
                        else [_SOFAS[0], _CORNER[0]])[:rows]
            q_toks = set(re.split(r"[^a-z0-9]+", q))
            if q_toks & _FAMILY_TOKENS:      # named product wins over category
                named = _catalog_mirror(q, rows)
                if named:
                    return named
            if any(w in q for w in ("corner", "l shape", "l-shape", "lshape",
                                    "sectional")):
                return _CORNER[:rows]
            if any(w in q for w in ("esmeralda", "dining", "marble")):
                return _DINING[:rows]
            if "recliner" in q:
                return _RECLINER[:rows]
            if "wardrobe" in q:
                return _WARDROBE[:rows]
            if "bed" in q:
                return _BEDS[:rows]
            if "sofa" in q:
                return (_SOFAS + _CORNER)[:rows]
            # Anything else mirrors the catalog as storefront rows so every
            # family the photo/EMI suites use resolves consistently with the
            # sitemap + price fixtures.
            return _catalog_mirror(q, rows)

        async def fake_site_search(query, rows=4, sort="", min_price=None,
                                   max_price=None):
            # Honor the price axis the way live Unbxd does (sort + range
            # filter server-side), so price-axis scenarios assert real
            # behavior, not fake-specific behavior.
            # 0 = unset, matching website_search._params (schema-filling
            # models send min_price=0/max_price=0 on every call).
            axis = bool(sort) or bool(min_price) or bool(max_price)
            out = _site_rows(query, 40 if axis else rows)

            def _p(r):
                try:
                    return float(r.get("selling_price") or 0)
                except (TypeError, ValueError):
                    return 0.0
            if min_price:
                out = [r for r in out if _p(r) >= float(min_price)]
            if max_price:
                out = [r for r in out if _p(r) <= float(max_price)]
            if sort == "price_desc":
                out = sorted(out, key=_p, reverse=True)
            elif sort == "price_asc":
                out = sorted(out, key=_p)
            return out[:rows]
        monkeypatch.setattr(website_search, "search", fake_site_search)

        async def fake_deal_creator(conv_id, agent_name=""):
            self.fake.deal_calls.append(conv_id)
            await self.fake.merge_custom_attributes(conv_id, {"crm_deal_id": "TEST-DEAL-1"})
            return {"deal_id": "TEST-DEAL-1", "created": True}
        social_agent.set_deal_creator(fake_deal_creator)

        # config knobs (restored by monkeypatch)
        for key, val in {"SOCIAL_AGENT_ENABLED": True,
                         "SOCIAL_AGENT_CONTACT_ALLOWLIST": [],
                         "SOCIAL_AGENT_CHANNELS": ("instagram",),
                         "SOCIAL_AGENT_AUTO_DEAL": True,
                         "SOCIAL_AUTO_SEND_ENABLED": True,
                         "OFFERS_ENABLED": bool(scenario.get("offers")),
                         **(scenario.get("config") or {})}.items():
            monkeypatch.setattr(config, key, val, raising=False)

        # Prehistory: earlier conversations (memory-read tests) + profile seed.
        def _hist_attrs(m):
            # Bot history must carry the bridge's source marker, else it reads
            # as a HUMAN reply and triggers the standdown guard. `who:
            # human_agent` deliberately leaves it bare to TEST that guard.
            if m.get("content_attributes"):
                return m["content_attributes"]
            return {"source": "ai_auto_reply"} if m.get("who") == "durian" else {}

        for prior in scenario.get("prior_conversations") or []:
            conv = self.fake.new_conversation(prior.get("inbox"),
                                              comment=prior.get("comment", False))
            for m in prior.get("messages") or []:
                self.fake.add_message(m.get("text", ""),
                                      BASE_NOW + parse_offset(m.get("at", "-1d")),
                                      incoming=m.get("who", "customer") == "customer",
                                      conv=conv, content_attributes=_hist_attrs(m))
            for k, v in (prior.get("attrs") or {}).items():
                conv["custom_attributes"][k] = v
            conv["labels"] += prior.get("labels") or []
        self.fake.new_conversation(scenario.get("inbox") or "durianfurniture_official",
                                   comment=scenario.get("surface") == "comment")
        if scenario.get("conv_attrs"):
            self.fake.conv["custom_attributes"].update(scenario["conv_attrs"])
        if scenario.get("assignee"):
            self.fake.conv["meta"]["assignee"] = dict(scenario["assignee"])
        if scenario.get("profile"):
            # Deep copy: YAML anchors (&x/*x) make scenarios SHARE the parsed
            # dict, and runs mutate the seeded profile (merge_events) — a
            # shared object would leak one scenario's events into the next.
            import copy
            self.fake.contact_attrs[customer_profile.PROFILE_KEY] = \
                copy.deepcopy(scenario["profile"])
        if scenario.get("profile_bulk_events"):
            prof = self.fake.contact_attrs.get(customer_profile.PROFILE_KEY) \
                or customer_profile.empty_profile()
            n = int(scenario["profile_bulk_events"])
            for i in range(n):
                when = BASE_NOW - timedelta(days=60) + timedelta(hours=i * 7)
                prof.setdefault("events", []).append(
                    {"t": when.isoformat(timespec="seconds"), "msg": 10_000 + i,
                     "conv": 1, "inbox": "ig",
                     "kind": "note" if i % 3 else "interest",
                     "what": f"synthetic event {i}"})
            prof["events_since_consolidation"] = n
            self.fake.contact_attrs[customer_profile.PROFILE_KEY] = prof
        for m in scenario.get("history") or []:
            self.fake.add_message(m.get("text", ""),
                                  BASE_NOW + parse_offset(m.get("at", "-1h")),
                                  incoming=m.get("who", "customer") == "customer",
                                  content_attributes=_hist_attrs(m))

    async def run(self):
        self.timeline = []
        for step in self.sc.get("script") or []:
            self.now = BASE_NOW + parse_offset(step.get("at", "0m"))
            if step.get("new_conversation"):
                self.fake.new_conversation(step.get("inbox"))
            msg = self.fake.add_message(step.get("text", ""), self.now,
                                        content_attributes=step.get("content_attributes"),
                                        photo=step.get("photo", False))
            s0, c0 = len(self.fake.public_sends), len(self.fake.cards)
            o0, t0 = len(self.fake.offer_sends), len(getattr(self, "tools_trail", []))
            res = await social_agent.maybe_handle(
                self.fake.conv, "instagram",
                surface=self.sc.get("surface") or "",
                latest_message=step.get("text", ""),
                latest_msg_id=msg["id"])
            self.results.append(res)
            outputs = (
                [{"type": "sent", "text": s["content"]}
                 for s in self.fake.public_sends[s0:]] +
                [{"type": "card", "text": c["content"],
                  "hold": (res or {}).get("hold") or (res or {}).get("reason") or ""}
                 for c in self.fake.cards[c0:]] +
                [{"type": "offer", "text": o.get("caption") or "",
                  "image_url": o.get("image_url") or ""}
                 for o in self.fake.offer_sends[o0:]])
            self.timeline.append({
                "at": step.get("at", "0m"), "text": step.get("text", ""),
                "caption": (step.get("content_attributes") or {}).get("shared_post_caption"),
                "tools": list(getattr(self, "tools_trail", []))[t0:],
                "handled": (res or {}).get("handled") or (res or {}).get("reason"),
                "outputs": outputs})
        return self.results

    # ── assertions ──────────────────────────────────────────────────────
    def check(self):
        exp = self.sc.get("expect") or {}
        sent_text = "\n".join(s["content"] for s in self.fake.public_sends)
        card_text = "\n".join(c["content"] for c in self.fake.cards)
        all_text = sent_text + "\n" + card_text
        errors = []

        def want(cond, msg):
            if not cond:
                errors.append(msg)

        # Platform-wide: Instagram refuses DMs over 1000 chars (Send API
        # error 100) — no scenario may pass while producing one.
        for s in self.fake.public_sends:
            want(len(s["content"]) <= 1000,
                 f"public send over Instagram's 1000-char limit "
                 f"({len(s['content'])} chars)")

        for frag in exp.get("sent_contains") or []:
            want(re.search(frag, sent_text, re.I), f"sent missing: {frag!r}")
        for frag in exp.get("sent_not_contains") or []:
            want(not re.search(frag, sent_text, re.I), f"sent must not contain: {frag!r}")
        for frag in exp.get("card_contains") or []:
            want(re.search(frag, card_text, re.I), f"card missing: {frag!r}")
        for frag in exp.get("anywhere_contains") or []:
            want(re.search(frag, all_text, re.I), f"no output contains: {frag!r}")
        if exp.get("no_public_send"):
            want(not self.fake.public_sends, f"expected no public send, got: {sent_text[:120]!r}")
        if exp.get("handled"):
            got = ((self.results[-1] or {}).get("handled")
                   or (self.results[-1] or {}).get("reason") or "")
            want(got == exp["handled"], f"handled={got!r} != {exp['handled']!r}")
        for path, value in (exp.get("attrs") or {}).items():
            node = self.fake.conv["custom_attributes"]
            for part in path.split(".")[:-1]:
                node = (node or {}).get(part) or {}
            got = (node or {}).get(path.split(".")[-1])
            want(got == value or (value == "*" and got),
                 f"attr {path}={got!r} != {value!r}")
        for path in exp.get("attrs_absent") or []:
            node = self.fake.conv["custom_attributes"]
            for part in path.split(".")[:-1]:
                node = (node or {}).get(part) or {}
            got = (node or {}).get(path.split(".")[-1])
            want(not got, f"attr must be absent: {path}={got!r}")
        for lbl in exp.get("labels") or []:
            want(lbl in self.fake.conv["labels"], f"label missing: {lbl}")
        for lbl in exp.get("labels_absent") or []:
            want(lbl not in self.fake.conv["labels"], f"label must be absent: {lbl}")
        for spec in exp.get("tools_called") or []:
            alts = spec.split("|")
            want(any(a in self.tools_called for a in alts),
                 f"tool not called: {spec} (called: {self.tools_called})")
        for spec in exp.get("tools_not_called") or []:
            want(spec not in self.tools_called, f"tool must not be called: {spec}")
        for frag in exp.get("tool_call_matches") or []:
            # regex over the recorded call strings, e.g.
            # search_products\(.*price_desc — asserts HOW a tool was called
            want(any(re.search(frag, t, re.I) for t in self.tools_trail),
                 f"no tool call matches: {frag!r} "
                 f"(trail: {self.tools_trail})")
        if exp.get("no_pii_phone"):
            digits = re.sub(r"\D", "", str(exp["no_pii_phone"]))[-10:]
            flat = re.sub(r"\D", "", sent_text)
            want(digits not in flat, "stored phone leaked into a public send")
        if exp.get("deal_created"):
            want(self.fake.deal_calls, "expected auto-deal, none created")
        if exp.get("deal_not_created"):
            want(not self.fake.deal_calls, "deal must NOT be created")
        if exp.get("deal_created_max") is not None:
            want(len(self.fake.deal_calls) <= exp["deal_created_max"],
                 f"deal created {len(self.fake.deal_calls)}× > max")
        for k, v in (exp.get("send_attr") or {}).items():
            want(self.fake.public_sends and
                 self.fake.public_sends[-1]["attrs"].get(k) == v,
                 f"send attr {k}={v!r} missing on last public send")
        if exp.get("offer_caption_contains"):
            want(any(exp["offer_caption_contains"].lower() in
                     (o.get("caption") or "").lower()
                     for o in self.fake.offer_sends),
                 f"no offer send matching {exp['offer_caption_contains']!r}")
        if exp.get("offer_link_contains"):
            want(any(exp["offer_link_contains"].lower() in
                     (o.get("link") or "").lower()
                     for o in self.fake.offer_sends),
                 f"no offer send carried link {exp['offer_link_contains']!r}")
        if exp.get("profile_linked"):
            _p = self.fake.contact_attrs.get(customer_profile.PROFILE_KEY) or {}
            want(_p.get("linked_contacts"), "profile has no linked_contacts")
        if exp.get("offer_sent"):
            want(self.fake.offer_sends, "expected an offer send")
        if exp.get("offer_sent_max") is not None:
            want(len(self.fake.offer_sends) <= exp["offer_sent_max"],
                 f"too many offer sends: {len(self.fake.offer_sends)}")
        prof = self.fake.contact_attrs.get(customer_profile.PROFILE_KEY) or {}
        for ev in exp.get("profile_has_event") or []:
            hits = [e for e in prof.get("events") or []
                    if e.get("kind") == ev.get("kind")
                    and str(ev.get("what_contains", "")).lower()
                    in str(e.get("what", "")).lower()]
            want(hits, f"profile missing event: {ev}")
        for ev in exp.get("profile_lacks_event") or []:
            hits = [e for e in prof.get("events") or []
                    if e.get("kind") == ev.get("kind")
                    and str(ev.get("what_contains", "")).lower()
                    in str(e.get("what", "")).lower()]
            want(not hits, f"profile must NOT contain event: {ev}")
        # profile_field: {identity.phone: "9560150835"} — a `set` fact
        # landed with this value; profile_field_absent: [location.pincode]
        # — nothing was set there. Paths are section.field.
        def _pf(path):
            sec, _, fld = path.partition(".")
            return ((prof.get(sec) or {}).get(fld) or {}).get("value")
        for path, value in (exp.get("profile_field") or {}).items():
            got = _pf(path)
            want(str(got or "") == str(value),
                 f"profile field {path}={got!r} != {value!r}")
        for path in exp.get("profile_field_absent") or []:
            got = _pf(path)
            want(not got, f"profile field must be unset: {path}={got!r}")
        if exp.get("profile_consolidated"):
            want(prof.get("consolidated_at"), "profile was not consolidated")
        if exp.get("viz_used_min") is not None:
            _n = len((prof.get("ops") or {}).get("visualized_at") or [])
            want(_n >= exp["viz_used_min"], f"visualizer uses {_n} < {exp['viz_used_min']}")
        if exp.get("viz_used") is not None:
            _n = len((prof.get("ops") or {}).get("visualized_at") or [])
            want(_n == exp["viz_used"], f"visualizer uses {_n} != {exp['viz_used']}")
        for spec in exp.get("profile_event_count") or []:
            n = sum(1 for e in prof.get("events") or []
                    if e.get("kind") == spec.get("kind"))
            want(n == spec.get("count"),
                 f"event count {spec.get('kind')}={n} != {spec.get('count')}")
        if exp.get("images_sent_min") is not None:
            want(len(self.fake.offer_sends) >= exp["images_sent_min"],
                 f"images sent {len(self.fake.offer_sends)} < {exp['images_sent_min']}")
        if exp.get("images_sent_max") is not None:
            want(len(self.fake.offer_sends) <= exp["images_sent_max"],
                 f"images sent {len(self.fake.offer_sends)} > {exp['images_sent_max']}")
        img_blobs = [f"{o.get('caption') or ''} {o.get('image_url') or ''}"
                     for o in self.fake.offer_sends]
        for frag in exp.get("images_sent_contains") or []:
            want(any(re.search(frag, b, re.I) for b in img_blobs),
                 f"no image send matches: {frag!r} (sent: {img_blobs})")
        for frag in exp.get("images_sent_not_contains") or []:
            want(not any(re.search(frag, b, re.I) for b in img_blobs),
                 f"image send must not match: {frag!r}")
        if exp.get("images_unique"):
            urls = [o.get("image_url") for o in self.fake.offer_sends
                    if o.get("image_url")]
            want(len(urls) == len(set(urls)), "duplicate image URL sent")
        if exp.get("preview_swatch_used") is not None:
            used = any(c.get("swatch_url") for c in
                       getattr(self, "preview_calls", []))
            want(used == bool(exp["preview_swatch_used"]),
                 f"preview swatch used={used}, expected "
                 f"{bool(exp['preview_swatch_used'])}")
        return errors, sent_text, card_text


TOKENS = {"in": 0, "out": 0, "judge_in": 0, "judge_out": 0}


def _patch_usage_tracking():
    orig = social_agent._client.responses.create

    async def tracked(*a, **kw):
        r = await orig(*a, **kw)
        u = getattr(r, "usage", None)
        if u:
            TOKENS["in"] += getattr(u, "input_tokens", 0) or 0
            TOKENS["out"] += getattr(u, "output_tokens", 0) or 0
        return r
    social_agent._client.responses.create = tracked

    orig_tool_dispatch = {}
    for name, s in social_agent.SKILLS.items():
        orig_tool_dispatch[name] = s["handler"]
    return orig


_patch_usage_tracking()


@pytest.fixture
def engine_factory(monkeypatch):
    def make(scenario):
        eng = Engine(scenario, monkeypatch)
        orig = {n: s["handler"] for n, s in social_agent.SKILLS.items()}
        eng.tools_trail = []
        for n in social_agent.SKILLS:
            def wrap(fn, name):
                def inner(ctx, **kw):
                    eng.tools_called.append(name)
                    eng.tools_trail.append(
                        f"{name}({json.dumps(kw, ensure_ascii=False)[:150]})")
                    return fn(ctx, **kw)
                return inner
            monkeypatch.setitem(social_agent.SKILLS[n], "handler",
                                wrap(orig[n], n))
        social_agent._last_handled_msgid.clear()
        return eng
    return make


JUDGE_RUBRIC = """Score this Instagram exchange from Durian's AI assistant, 1-5:
5 = grounded (every price/fact from tools), professional-minimal (no emoji, no
fluff), never repeats or re-asks known info, moves toward a purchase or a clean
handoff, respects anything the customer declined. The assistant speaks AS the
brand, plural ("we"/"our", never "I"), and each reply is ONE composed message:
one opening, at most one closing "Regards, Team Durian".
3 = acceptable but clunky (mild repetition, missed context, wordy).
1 = invents facts, re-asks known details, ignores a decline, is rude, speaks
as "I", or pastes template letter-dressing. The tell for stitching is a
SECOND salutation or sign-off inside one message (e.g. "Dear Customer" or a
"Regards, Team Durian" appearing mid-message, then another at the end). A
single opening courtesy ("Thank you." / "Thank you for sharing your number.")
followed by substance and ONE closing sign-off is a normally composed reply.
Judge each reply on its own turn: asking for a detail BEFORE the customer
gives it is correct; only asking again AFTER it was given is a re-ask.

BUSINESS RULES the assistant is REQUIRED to follow — following them is
correct behavior, never penalize it:
- The store card's 📞 (phone) and 🗺️ (map) glyphs are the APPROVED format for
  showroom details — they are not "emoji" and are never a fault.
- Read turn ORDER carefully: asking for a pincode BEFORE the customer gives
  it is correct; only asking again AFTER it was given is a re-ask.
- Public comments never carry prices or contact details; price questions are
  redirected to DM.
- Room previews (seeing furniture in the customer's own photo) REQUIRE a
  registered enquiry first (phone + showroom routing), are capped per day, and
  beyond the cap the customer is routed to the sales team.
- Greeting rules: a customer who opens WITH clear intent is served directly,
  no preamble — no intro or welcome-back line required. The capability intro
  ("I can share prices, EMI options…") is the designed greeting ONLY for a
  first contact with no history and no stated intent. A NO-intent opener
  from a customer whose profile shows history gets the returning-customer
  opener ("Welcome back — still considering the …?", their newest interest)
  — required in that case, never penalize it.
- Offers shared in chat come from the share_offer tool (its results appear in
  the tool trail); treat them as grounded.
- Order status, dealer/franchise, bulk orders, collabs, discount haggling and
  serious complaints are escalated to humans by design.
- The transcript header names the surface. In a private DM, sharing showroom
  store cards WITH phone numbers and map links is required behavior.
- Product photos (share_product_images results in the trail) are sent as
  actual images; the text only needs the listing link, not a description of
  the photos. The designed photo policy: front-view photo per variant, up to
  3; photos for a product go out once per conversation; a NEW variant the
  customer asks for gets just that variant's photos; an explicit "send them
  again" re-sends; a comparison sends exactly one front view per product.
  All of these are correct — never penalize photo counts that follow them,
  and never penalize mentioning that more colours can be requested.
- When the photo tool reports NO photos on file for a product, the correct
  reply says photos are unavailable and pivots (showroom visit / listing
  link). Penalize invented image descriptions — never the honest pivot.
- Replies are ENGLISH ONLY by design: Hindi/Hinglish input is understood,
  but the assistant never uses Hindi words ("ji", "bilkul") in replies.
  Never penalize an English reply to a Hinglish message. Before a room
  preview generates, an automatic "about 2 minutes" wait note goes out —
  that is designed, not filler.
Return STRICT JSON: {"score": n, "reason": "one line"}"""


JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "gpt-5.6-terra")


async def judge_transcript(transcript: str) -> dict:
    # The judge always grades on OpenAI, whatever provider the agent ran on —
    # cross-provider comparisons need one consistent grader.
    from llm_client import client as _judge_client, floor_effort
    from openai import RateLimitError
    r = None
    # Generous backoff: a starved judge must WAIT, not hand out zeros — a
    # score-0 for a 429 fails the scenario for infra reasons, not quality.
    for attempt in range(7):
        try:
            r = await _judge_client.chat.completions.create(
                model=JUDGE_MODEL,
                reasoning_effort=floor_effort(JUDGE_MODEL),
                response_format={"type": "json_object"}, max_tokens=120,
                messages=[{"role": "system", "content": JUDGE_RUBRIC},
                          {"role": "user", "content": transcript[:6000]}])
            break
        except RateLimitError:
            if attempt == 6:
                return {"score": 0, "reason": "judge rate-limited"}
            await asyncio.sleep(min(2 ** attempt, 30))
    u = getattr(r, "usage", None)
    if u:
        TOKENS["judge_in"] += getattr(u, "prompt_tokens", 0) or 0
        TOKENS["judge_out"] += getattr(u, "completion_tokens", 0) or 0
    try:
        return json.loads(r.choices[0].message.content)
    except Exception:
        return {"score": 0, "reason": "judge parse failure"}


def pytest_sessionfinish(session, exitstatus):
    cost = (TOKENS["in"] + TOKENS["judge_in"]) / 1e6 * 0.20 + \
           (TOKENS["out"] + TOKENS["judge_out"]) / 1e6 * 1.20
    print(f"\n[agent-tests] tokens in={TOKENS['in'] + TOKENS['judge_in']:,} "
          f"out={TOKENS['out'] + TOKENS['judge_out']:,} "
          f"≈ ${cost:.3f} this run")
