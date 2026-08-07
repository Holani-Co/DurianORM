import unittest
from unittest.mock import AsyncMock, patch

import main
import social_store_templates


class SocialStoreTemplateTests(unittest.TestCase):
    def test_goregaon_pincode_uses_exact_mumbai_locality(self):
        reply = social_store_templates.resolve_store_reply(
            "furniture", pincode="400063")

        self.assertEqual(reply["store"], "Goregaon")
        self.assertEqual(reply["city"], "mumbai")
        self.assertEqual(reply["location"], "goregaon")
        self.assertIn("Goregaon Showroom", reply["text"])
        self.assertNotIn("Gurgaon Showroom", reply["text"])


class SocialStoreDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_store_reply_is_queued_with_delivery_marker(self):
        gate_result = {
            "is_store_enquiry": True,
            "wants_to_buy": False,
            "vertical": "furniture",
            "pincode": "400063",
            "city": "",
            "location": "",
        }
        with (
            patch.object(main.config, "SOCIAL_STORE_TEMPLATES_ENABLED", True),
            patch.object(
                main, "_social_store_gate_llm",
                new=AsyncMock(return_value=gate_result)),
            patch.object(
                main.chatwoot, "send_outgoing_message",
                new=AsyncMock(return_value={"id": 901})) as send,
            patch.object(main, "_flag_agent_needed", new=AsyncMock()) as handoff,
            patch.object(main.chatwoot, "post_private_note", new=AsyncMock()) as note,
        ):
            result = await main._maybe_social_store_or_deal(
                77, "instagram", "400063", "Customer", {})

        self.assertEqual(result["handled"], "social_store_reply_queued")
        self.assertEqual(result["message_id"], 901)
        marker = send.await_args.kwargs["content_attributes"]
        self.assertEqual(marker["source"], main._STORE_DELIVERY_SOURCE)
        self.assertEqual(marker["store"], "Goregaon")
        handoff.assert_not_awaited()
        note.assert_not_awaited()

    async def test_failed_meta_send_is_handed_to_an_agent(self):
        data = self._delivery_event(
            status="failed", source_id=None,
            external_error="508 - Invalid message id")
        with self._delivery_patches() as mocks:
            result = await main.handle_message_updated(data)

        self.assertEqual(result["handled"], "social_store_delivery_failed")
        mocks["label"].assert_any_await(77, main._STORE_DELIVERY_FAILED_LABEL)
        mocks["handoff"].assert_awaited_once_with(77, "instagram")
        self.assertIn("508 - Invalid message id", mocks["note"].await_args.args[1])
        receipt = mocks["merge"].await_args.args[1][
            main._STORE_DELIVERY_RECEIPT_ATTR]
        self.assertEqual(receipt["status"], "failed")

    async def test_meta_source_id_records_success_after_a_failed_retry(self):
        data = self._delivery_event(
            status="sent", source_id="mid.abc", external_error=None)
        previous = {
            main._STORE_DELIVERY_RECEIPT_ATTR: {
                "message_id": 901,
                "status": "failed",
            }
        }
        with self._delivery_patches(previous) as mocks:
            result = await main.handle_message_updated(data)

        self.assertEqual(result["handled"], "social_store_delivery_sent")
        mocks["clear_handoff"].assert_awaited_once()
        mocks["label"].assert_awaited_once_with(77, "store-enquiry")
        self.assertIn("sent Goregaon", mocks["note"].await_args.args[1])
        receipt = mocks["merge"].await_args.args[1][
            main._STORE_DELIVERY_RECEIPT_ATTR]
        self.assertEqual(receipt["status"], "sent")

    @staticmethod
    def _delivery_event(status, source_id, external_error):
        return {
            "id": 901,
            "status": status,
            "source_id": source_id,
            "external_error": external_error,
            "content_attributes": {
                "source": main._STORE_DELIVERY_SOURCE,
                "channel": "instagram",
                "store": "Goregaon",
                "how": "pincode:exact",
            },
            "conversation": {
                "id": 77,
                "labels": [],
                "custom_attributes": {},
            },
        }

    @staticmethod
    def _delivery_patches(custom_attributes=None):
        current = {
            "id": 77,
            "labels": ["agent-needed", "agent-needed-instagram"],
            "custom_attributes": custom_attributes or {},
        }
        get_conversation = patch.object(
            main.chatwoot, "get_conversation",
            new=AsyncMock(return_value=current))
        label = patch.object(main, "_label_conversation", new=AsyncMock())
        handoff = patch.object(main, "_flag_agent_needed", new=AsyncMock())
        clear_handoff = patch.object(main, "_clear_agent_needed", new=AsyncMock())
        note = patch.object(main.chatwoot, "post_private_note", new=AsyncMock())
        merge = patch.object(
            main.chatwoot, "merge_custom_attributes", new=AsyncMock())
        remove_label = patch.object(main.chatwoot, "remove_label", new=AsyncMock())

        class DeliveryPatches:
            def __enter__(self):
                get_conversation.start()
                return {
                    "label": label.start(),
                    "handoff": handoff.start(),
                    "clear_handoff": clear_handoff.start(),
                    "note": note.start(),
                    "merge": merge.start(),
                    "remove_label": remove_label.start(),
                }

            def __exit__(self, exc_type, exc_value, traceback):
                remove_label.stop()
                merge.stop()
                note.stop()
                clear_handoff.stop()
                handoff.stop()
                label.stop()
                get_conversation.stop()

        return DeliveryPatches()


if __name__ == "__main__":
    unittest.main()
