import io
import json
import unittest
from unittest.mock import patch

from openpyxl import Workbook

from app.recipient_files import Recipient, parse_recipient_file
from app.routers import messaging


class RecipientFileParserTests(unittest.TestCase):
    def test_csv_mixed_phone_username_deduplicates(self):
        data = (
            "Số điện thoại,Username\n"
            "+84 912 345 678,alice_user\n"
            "84912345678,@bob_user\n"
            "+84912345678,alice_user\n"
            "not-a-phone,bad user\n"
        ).encode("utf-8")
        parsed = parse_recipient_file("targets.csv", data)
        self.assertEqual(parsed["valid"], 3)
        self.assertEqual(parsed["phones"], 1)
        self.assertEqual(parsed["usernames"], 2)
        self.assertEqual(parsed["duplicates"], 3)
        self.assertEqual(parsed["invalid"], 2)

    def test_xlsx_recognizes_usname_and_phone_headers(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["Usname", "Phone"])
        ws.append(["charlie_user", "+1 (416) 555-0100"])
        ws.append(["@delta_user", "442071838750"])
        buf = io.BytesIO()
        wb.save(buf)
        wb.close()

        parsed = parse_recipient_file("targets.xlsx", buf.getvalue())
        values = {r.value for r in parsed["recipients"]}
        self.assertEqual(parsed["valid"], 4)
        self.assertIn("@charlie_user", values)
        self.assertIn("@delta_user", values)
        self.assertIn("+14165550100", values)
        self.assertIn("+442071838750", values)

    def test_excel_numeric_phone_float_is_not_given_extra_zero(self):
        from app.recipient_files import normalize_recipient

        rec = normalize_recipient(84912345678.0, hint="phone")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.value, "+84912345678")


class RecipientRoundRobinTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_recipient_is_assigned_once_in_round_robin_order(self):
        class FakeClient:
            def __init__(self, name):
                self.name = name

        clients = {1: FakeClient("a1"), 2: FakeClient("a2")}
        sent = []

        async def fake_send(cli, recipient, text):
            sent.append((cli.name, recipient.value, text))

        async def no_delay(*args, **kwargs):
            return None

        recipients = [
            Recipient("@user_one", "username"),
            Recipient("+14165550100", "phone"),
            Recipient("@user_three", "username"),
            Recipient("+442071838750", "phone"),
        ]
        accounts = [(1, "+100", "A1"), (2, "+200", "A2")]

        with (
            patch.object(messaging.manager, "get", side_effect=lambda aid: clients.get(aid)),
            patch.object(messaging, "_send_recipient", new=fake_send),
            patch.object(messaging, "jitter_delay", new=no_delay),
        ):
            events = []
            async for line in messaging._recipient_list_stream(accounts, recipients, "hello", 0, 0):
                events.append(json.loads(line))

        self.assertEqual([x[1] for x in sent if x[0] == "a1"], ["@user_one", "@user_three"])
        self.assertEqual([x[1] for x in sent if x[0] == "a2"], ["+14165550100", "+442071838750"])
        self.assertEqual(len(sent), 4)
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["success"], 4)
        self.assertEqual(events[-1]["failed"], 0)

    async def test_balanced_assignment_differs_by_at_most_one_and_has_no_overlap(self):
        accounts = [(1, "+100", "A1"), (2, "+200", "A2"), (3, "+300", "A3")]
        recipients = [Recipient(f"@user_{i:02d}", "username") for i in range(10)]
        assignments = messaging._balanced_recipient_assignments(accounts, recipients)
        counts = [len(assignments[aid]) for aid, _phone, _name in accounts]
        flattened = [item.value for aid, _phone, _name in accounts for item in assignments[aid]]
        self.assertEqual(counts, [4, 3, 3])
        self.assertLessEqual(max(counts) - min(counts), 1)
        self.assertEqual(len(flattened), len(set(flattened)))
        self.assertEqual(set(flattened), {r.value for r in recipients})

    async def test_delay_is_applied_between_messages_per_session_only(self):
        class FakeClient:
            pass

        clients = {1: FakeClient(), 2: FakeClient()}
        calls = []

        async def fake_send(cli, recipient, text):
            return None

        async def capture_delay(lo, hi):
            calls.append((lo, hi))

        accounts = [(1, "+100", "A1"), (2, "+200", "A2")]
        recipients = [Recipient(f"@delay_{i:02d}", "username") for i in range(4)]
        with (
            patch.object(messaging.manager, "get", side_effect=lambda aid: clients.get(aid)),
            patch.object(messaging, "_send_recipient", new=fake_send),
            patch.object(messaging, "jitter_delay", new=capture_delay),
        ):
            async for _line in messaging._recipient_list_stream(accounts, recipients, "hello", 2.5, 4.5):
                pass
        self.assertEqual(calls, [(2.5, 4.5), (2.5, 4.5)])


if __name__ == "__main__":
    unittest.main()


class RecipientPreviewApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_preview_multipart_route_parses_csv(self):
        import httpx

        from app.auth import require_auth
        from app.main import app

        app.dependency_overrides[require_auth] = lambda: True
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                response = await client.post(
                    "/api/messaging/recipient_list_preview",
                    files={"file": ("targets.csv", b"Phone,Username\n+84912345678,test_user\n", "text/csv")},
                )
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["valid"], 2)
            self.assertEqual(body["phones"], 1)
            self.assertEqual(body["usernames"], 1)
        finally:
            app.dependency_overrides.clear()
