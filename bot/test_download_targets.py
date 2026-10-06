"""Selected-file delivery regressions: no Telegram requests or database writes."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bot.loader import load_bot_symbols

load_bot_symbols()

from bot import callback_handlers as callbacks
from bot import content_delivery as delivery
from bot import features, message_handlers as messages
from bot.download_targets import encode_delivery_target, parse_delivery_target


class DownloadPayloadTests(unittest.TestCase):
    def test_legacy_and_selected_targets(self):
        self.assertEqual(parse_delivery_target("10"), (10, None))
        self.assertEqual(parse_delivery_target("10_2"), (10, 2))
        self.assertEqual(encode_delivery_target(10, 2), "10_2")

    def test_malformed_and_overlong_targets_are_rejected(self):
        for value in (None, "", "0", "-1", "10_0", "10_file", "10_2_3",
                      "10/2", "10_2 ", "1" * 5000):
            with self.subTest(value=str(value)[:40]):
                self.assertIsNone(parse_delivery_target(value))

    def test_payloads_fit_telegram_limits(self):
        target = encode_delivery_target(2**63 - 1, 2**63 - 1)
        self.assertLessEqual(len(f"file_{target}"), 64)
        self.assertLessEqual(len(f"notif_decline_{target}".encode()), 64)


class HandoffCollection:
    def __init__(self, handoff):
        self.handoff = handoff
        self.updates = []

    def find_one_and_update(self, query, update, **kwargs):
        self.claim_query = query
        return self.handoff

    def update_one(self, query, update):
        self.updates.append((query, update))


class SelectedFileDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.files = [
            {"id": 1, "button_id": 10, "type": "file", "file_id": "first-file",
             "content": "الجزء الأول", "group_id": "same-album"},
            {"id": 2, "button_id": 10, "type": "document", "file_id": "second-file",
             "content": "الجزء الثاني", "group_id": "same-album"},
        ]
        self.button = {"id": 10, "type": "content", "no_caption": 1, "no_btn_caption": 1}
        self.bot = SimpleNamespace(
            send_document=AsyncMock(), send_message=AsyncMock(),
            send_photo=AsyncMock(), delete_message=AsyncMock(),
        )
        self.message = SimpleNamespace(
            chat_id=777, message_id=55,
            reply_text=AsyncMock(return_value=SimpleNamespace(chat_id=777, message_id=55)),
            reply_document=AsyncMock(),
            reply_photo=AsyncMock(), delete=AsyncMock(), get_bot=lambda: self.bot,
        )
        self.query = SimpleNamespace(
            from_user=SimpleNamespace(id=99), message=self.message,
            data="", answer=AsyncMock(), edit_message_reply_markup=AsyncMock(),
        )
        self.update = SimpleNamespace(
            effective_user=SimpleNamespace(id=99), message=self.message,
            callback_query=self.query,
        )
        self.ctx = SimpleNamespace(args=[], user_data={}, bot=self.bot)
        setting = lambda key, default="": default
        module_patches = [
            (delivery, {
                "is_admin": Mock(return_value=False),
                "get_file_block_remaining": Mock(return_value=0),
                "get_pending_notif": Mock(return_value=0),
                "is_subscribed": AsyncMock(return_value=True),
                "get_items_user": Mock(return_value=self.files),
                "get_items": Mock(return_value=self.files),
                "get_btn": Mock(return_value=self.button),
                "inc_click_count": Mock(),
                "get_user_ratings_hidden": Mock(return_value=True),
                "get_global_caption": Mock(return_value=""),
                "get_caption_buttons": Mock(return_value=[]),
                "get_random_phrase": Mock(return_value=None),
                "get_setting": Mock(side_effect=setting),
                "record_channel_subscription": Mock(), "clear_file_block": Mock(),
                "reset_notif_no_count": Mock(), "clear_pending_notif": Mock(),
                "set_pending_notif": Mock(), "inc_user_opens": Mock(),
                "should_notify": Mock(return_value=True),
            }),
            (messages, {"get_btn": Mock(return_value=self.button)}),
            (features, {
                "get_setting": Mock(side_effect=setting),
                "mark_notif_sent": Mock(), "set_pending_notif": Mock(),
            }),
            (callbacks, {
                "is_admin": Mock(return_value=False),
                "check_rate_limit": Mock(return_value=True),
                "is_subscribed": AsyncMock(return_value=True),
                "get_setting": Mock(side_effect=setting),
                "record_channel_subscription": Mock(), "clear_pending_notif": Mock(),
                "reset_notif_no_count": Mock(), "clear_file_block": Mock(),
                "get_notif_no_count": Mock(return_value=4),
                "set_notif_no_count": Mock(), "set_force_next_notif": Mock(),
                "inc_notif_no_count": Mock(return_value=1),
                "block_user_files": Mock(),
            }),
        ]
        for module, replacements in module_patches:
            mocked = patch.multiple(module, **replacements)
            mocked.start()
            self.addCleanup(mocked.stop)

    async def test_start_dispatches_selected_item_not_the_whole_button(self):
        self.ctx.args = ["file_10_2"]
        with patch.object(messages, "send_items", new_callable=AsyncMock) as send:
            await messages.cmd_start(self.update, self.ctx)
        send.assert_awaited_once_with(self.message, 10, uid=99, bot=self.bot, item_id=2)

    async def test_web_handoff_is_marked_delivered_only_after_file_send(self):
        token = "a1b2c3d4e5f6g7h8"
        collection = HandoffCollection({
            "_id": token, "button_id": 10, "item_id": 2,
        })
        self.ctx.args = [f"handoff_{token}"]
        with patch.object(
            messages, "get_mongo_db",
            return_value={"telegram_download_handoffs": collection},
        ), patch.object(messages, "get_btn", return_value=self.button), \
                patch.object(messages, "send_items", new_callable=AsyncMock, return_value=True) as send:
            await messages.cmd_start(self.update, self.ctx)

        send.assert_awaited_once_with(
            self.message, 10, uid=99, bot=self.bot, item_id=2
        )
        self.assertEqual(
            collection.updates[-1][1]["$set"]["status"], "delivered"
        )

    async def test_web_handoff_stays_open_when_the_bot_did_not_send_a_file(self):
        token = "a1b2c3d4e5f6g7h8"
        collection = HandoffCollection({
            "_id": token, "button_id": 10, "item_id": 2,
        })
        self.ctx.args = [f"handoff_{token}"]
        with patch.object(
            messages, "get_mongo_db",
            return_value={"telegram_download_handoffs": collection},
        ), patch.object(messages, "get_btn", return_value=self.button), \
                patch.object(messages, "send_items", new_callable=AsyncMock, return_value=False):
            await messages.cmd_start(self.update, self.ctx)

        self.assertEqual(
            collection.updates[-1][1]["$set"]["status"], "not_sent"
        )

    async def test_legacy_start_still_dispatches_the_whole_button(self):
        self.ctx.args = ["btn_10"]
        with patch.object(messages, "send_items", new_callable=AsyncMock) as send:
            await messages.cmd_start(self.update, self.ctx)
        send.assert_awaited_once_with(self.message, 10, uid=99, bot=self.bot)

    async def test_start_to_document_delivery_uses_the_selected_file_end_to_end(self):
        self.ctx.args = ["file_10_2"]
        await messages.cmd_start(self.update, self.ctx)
        self.message.reply_document.assert_awaited_once_with(
            "second-file", caption="الجزء الثاني")

    async def test_invalid_or_deleted_start_never_falls_back_to_group_delivery(self):
        with patch.object(messages, "send_items", new_callable=AsyncMock) as send:
            for payload in ("file_10", "file_10_0", "file_10_2_extra"):
                self.ctx.args = [payload]
                await messages.cmd_start(self.update, self.ctx)
            messages.get_btn.return_value = None
            self.ctx.args = ["file_10_2"]
            await messages.cmd_start(self.update, self.ctx)
        send.assert_not_awaited()
        self.assertEqual(self.message.reply_text.await_count, 4)

    async def test_real_sender_delivers_only_the_selected_document(self):
        delivered = await delivery.send_items(
            self.message, 10, uid=99, bot=self.bot, item_id=2
        )
        self.assertTrue(delivered)
        self.message.reply_document.assert_awaited_once_with(
            "second-file", caption="الجزء الثاني")
        delivery.get_items.assert_not_called()
        self.message.reply_photo.assert_not_awaited()

    async def test_admin_also_gets_only_the_selected_file(self):
        delivery.is_admin.return_value = True
        await delivery.send_items(self.message, 10, uid=99, bot=self.bot, item_id=1)
        self.message.reply_document.assert_awaited_once_with("first-file", caption="الجزء الأول")
        delivery.is_subscribed.assert_not_awaited()

    async def test_missing_or_wrong_type_item_never_sends_another_file(self):
        self.files.append({"id": 3, "button_id": 10, "type": "photo", "file_id": "photo"})
        for item_id in (999, 3):
            await delivery.send_items(self.message, 10, uid=99, bot=self.bot, item_id=item_id)
        self.message.reply_document.assert_not_awaited()
        self.assertEqual(self.message.reply_text.await_count, 2)

    async def test_unselected_bot_navigation_still_sends_both_files(self):
        self.files[1]["type"] = "file"
        await delivery.send_items(self.message, 10, uid=99, bot=self.bot)
        self.assertEqual(
            [call.args[0] for call in self.message.reply_document.await_args_list],
            ["first-file", "second-file"],
        )

    async def test_new_notification_gate_carries_the_item_identifier(self):
        delivery.is_subscribed.return_value = False
        with patch.object(delivery, "send_notif_gate", new_callable=AsyncMock) as gate:
            delivered = await delivery.send_items(
                self.message, 10, uid=99, bot=self.bot, item_id=2
            )
        gate.assert_awaited_once_with(self.message, 99, 10, item_id=2)
        self.message.reply_document.assert_not_awaited()
        self.assertFalse(delivered)

    async def test_pending_gate_is_reissued_for_the_current_selected_item(self):
        delivery.get_pending_notif.return_value = 10
        with patch.object(delivery, "resend_notif_gate", new_callable=AsyncMock) as gate:
            await delivery.send_items(self.message, 10, uid=99, bot=self.bot, item_id=2)
        gate.assert_awaited_once_with(self.message, 99, 10, item_id=2)
        self.message.reply_document.assert_not_awaited()

    async def test_gate_buttons_retain_item_and_pending_state_remains_a_button_id(self):
        await features.send_notif_gate(self.message, 99, 10, item_id=2)
        markup = self.message.reply_text.await_args.kwargs["reply_markup"]
        self.assertEqual(
            [button.callback_data for row in markup.inline_keyboard for button in row],
            ["notif_ok_10_2", "notif_skip_10_2"],
        )
        features.set_pending_notif.assert_called_once_with(99, 10, chat_id=777, msg_id=55)

    async def test_reissued_gate_has_the_selected_file_in_both_callbacks(self):
        with patch.object(delivery, "get_pending_notif_gate", return_value=(10, 777, 44)):
            await delivery.resend_notif_gate(self.message, 99, 10, item_id=2)
        markup = self.message.reply_text.await_args.kwargs["reply_markup"]
        self.assertEqual(
            [button.callback_data for row in markup.inline_keyboard for button in row],
            ["notif_ok_10_2", "notif_skip_10_2"],
        )
        delivery.set_pending_notif.assert_called_once_with(99, 10, chat_id=777, msg_id=55)

    async def test_active_block_is_not_bypassed_by_a_selected_file_link(self):
        delivery.get_file_block_remaining.return_value = 60
        delivery.is_subscribed.return_value = False
        await delivery.send_items(self.message, 10, uid=99, bot=self.bot, item_id=2)
        self.message.reply_document.assert_not_awaited()
        self.message.reply_text.assert_awaited_once()

    async def test_subscription_during_block_still_delivers_only_selected_file(self):
        delivery.get_file_block_remaining.return_value = 60
        await delivery.send_items(self.message, 10, uid=99, bot=self.bot, item_id=2)
        self.message.reply_document.assert_awaited_once_with(
            "second-file", caption="الجزء الثاني")
        delivery.clear_file_block.assert_called_once_with(99)

    async def test_delayed_delivery_uses_only_selected_file(self):
        await delivery.deliver_denied_content(self.bot, 777, "10_2")
        self.bot.send_document.assert_awaited_once_with(
            chat_id=777, document="second-file", caption="الجزء الثاني")

    async def test_missing_delayed_file_never_sends_the_rest_of_the_button(self):
        await delivery.deliver_denied_content(self.bot, 777, "10_999")
        self.bot.send_document.assert_not_awaited()
        self.bot.send_message.assert_awaited_once()

    async def test_confirmation_and_final_declines_keep_the_selected_target(self):
        for prefix in ("notif_ok_", "notif_check_", "notif_check2_", "notif_subok_",
                       "notif_decline_", "notif_anger_", "notif_skip_"):
            with self.subTest(prefix=prefix):
                self.bot.send_document.reset_mock()
                self.query.data = prefix + "10_2"
                await callbacks.cb_manage(self.update, self.ctx)
                self.bot.send_document.assert_awaited_once_with(
                    chat_id=777, document="second-file", caption="الجزء الثاني")

    async def test_intermediate_decline_stages_keep_selected_target_in_buttons(self):
        callbacks.get_setting.side_effect = (
            lambda key, default="": "@test-channel" if key == "notif_channel" else default
        )
        for stage in (3, 4, 5):
            with self.subTest(stage=stage):
                self.bot.send_message.reset_mock()
                callbacks.inc_notif_no_count.return_value = stage
                self.query.data = "notif_skip_10_2"
                await callbacks.cb_manage(self.update, self.ctx)
                markup = self.bot.send_message.await_args.kwargs["reply_markup"]
                targets = [button.callback_data for row in markup.inline_keyboard for button in row]
                self.assertTrue(targets)
                self.assertTrue(all(target.endswith("_10_2") for target in targets))
        self.bot.send_document.assert_not_awaited()

    async def test_legacy_confirmation_behavior_is_unchanged(self):
        self.query.data = "notif_ok_10"
        await callbacks.cb_manage(self.update, self.ctx)
        self.bot.send_document.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()