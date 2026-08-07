import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import main


class RetailRoutingTests(unittest.IsolatedAsyncioTestCase):
    def test_every_pincode_store_tag_maps_to_a_retail_owner(self):
        data = json.loads(
            (Path(__file__).parent / "data" / "pincode_tags.json").read_text())
        representative_pins = {}
        for pin, tag_index in data["pins"].items():
            representative_pins.setdefault(tag_index, pin)

        failures = []
        for tag_index, tag in enumerate(data["tags"]):
            pin = representative_pins.get(tag_index)
            if not pin:
                continue
            resolved = main.social_store_templates.resolve_store_reply(
                "furniture", pincode=pin)
            city = main.retail.lookup_city((resolved or {}).get("city") or "")
            chosen = None
            if city:
                _, city_data = city
                hint = " ".join(
                    x for x in (resolved.get("location"), resolved.get("store"))
                    if x)
                chosen = main.retail.match_showroom(city_data, hint)
            if not chosen or not chosen.get("owner_id"):
                failures.append((tag, pin, resolved))

        self.assertEqual(failures, [])

    async def test_pincode_reply_routes_to_exact_showroom_owner(self):
        with (
            patch.object(main, "_retail_gate_llm", new=AsyncMock()) as gate,
            patch.object(main, "_retail_send", new=AsyncMock(return_value=True)),
            patch.object(main, "_label_conversation", new=AsyncMock()),
            patch.object(main, "_flag_agent_needed", new=AsyncMock()) as handoff,
            patch.object(
                main.chatwoot, "merge_custom_attributes", new=AsyncMock()) as merge,
            patch.object(main.chatwoot, "remove_label", new=AsyncMock()),
            patch.object(main.chatwoot, "post_private_note", new=AsyncMock()),
        ):
            audit = await main._run_retail_gate(
                5201, "Customer", "", "", "i live at 110054",
                attempt=25, city_key="delhi", phone_on_file=True,
                channel="instagram")

        gate.assert_not_awaited()
        handoff.assert_not_awaited()
        owner_updates = [
            call.args[1]["retail_deal_owner"]
            for call in merge.await_args_list
            if call.args[1].get("retail_deal_owner")
        ]
        self.assertEqual(owner_updates[0]["location"], "Delhi - Kirti Nagar")
        self.assertEqual(owner_updates[0]["owner_id"], "3608871000000333444")
        self.assertIn("Retail routed to Delhi - Kirti Nagar", audit[0])

    async def test_retail_ask_has_no_retry_handoff(self):
        with (
            patch.object(main, "_retail_send", new=AsyncMock(return_value=True)),
            patch.object(main, "_label_conversation", new=AsyncMock()),
            patch.object(main, "_flag_agent_needed", new=AsyncMock()) as handoff,
            patch.object(
                main.chatwoot, "merge_custom_attributes", new=AsyncMock()) as merge,
        ):
            audit = await main._retail_ask(
                5201, "", "Please choose a showroom.",
                {"stage": "showroom", "city_key": "delhi"},
                attempt=999, what="a showroom choice", channel="instagram")

        handoff.assert_not_awaited()
        pending = merge.await_args.args[1]["pending_retail"]
        self.assertEqual(pending["attempts"], 1000)
        self.assertEqual(pending["city_key"], "delhi")
        self.assertIn("attempt 1000", audit[0])


if __name__ == "__main__":
    unittest.main()
