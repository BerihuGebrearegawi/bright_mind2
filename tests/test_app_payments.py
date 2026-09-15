"""Real behavioural tests for app.py's payment surface - the highest
priority item left on the handoff list. Covers: the Chapa checkout flow
(initialize/callback/webhook, including its own signature verification and
amount/currency cross-checks against the stored payment), the
provider-agnostic submit/parent-submit/approve/reject endpoints, the
generic telebirr/cbe gateway webhook, and - the actual point of the
`_approve_payment_transaction` Firestore transaction - that approving the
same payment twice (via any of the three paths that can trigger it) never
double-grants an entitlement.

app.py is a monolithic module (routes decorate its own module-level `app`
Flask instance directly), so these tests import it via the `app_module`
fixture and monkeypatch `_require_user_bearer`/`_require_admin_bearer` on
the module itself, rather than passing fakes into a register_fn like the
other route modules.
"""
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import pytest

from tests.fakes import FakeChapaAPI


def require_user_as(app_module, monkeypatch, uid="user-1", **extra):
    detail = {"uid": uid, "email": f"{uid}@example.com", "name": "Test User", **extra}
    monkeypatch.setattr(app_module, "_require_user_bearer", lambda: (True, detail))
    return detail


def require_user_denied(app_module, monkeypatch, status=401):
    monkeypatch.setattr(app_module, "_require_user_bearer",
                         lambda: (False, ({"error": "Sign in required."}, status)))


def require_admin_as(app_module, monkeypatch, uid="admin-1"):
    detail = {"uid": uid, "admin": True}
    monkeypatch.setattr(app_module, "_require_admin_bearer", lambda: (True, detail))
    return detail


def require_admin_denied(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "_require_admin_bearer",
                         lambda: (False, ({"error": "Admin access required."}, 403)))


def _client(app_module):
    return app_module.app.test_client()


def _seed_user(fake_db, uid, **fields):
    doc = {"accountType": "student", "name": "Student"}
    doc.update(fields)
    fake_db.seed("users", uid, doc)


def _seed_verified_link(fake_db, parent_uid, child_uid, link_id=None):
    fake_db.seed("parentChildLinks", link_id or f"{parent_uid}-{child_uid}",
                 {"parentUid": parent_uid, "childUid": child_uid, "status": "verified"})


def _seed_payment(fake_db, payment_id, uid="user-1", status="Pending", plan="monthly",
                   provider="manual", amount=9.99, **extra):
    doc = {"uid": uid, "status": status, "plan": plan, "provider": provider, "amount": amount}
    doc.update(extra)
    fake_db.seed("payments", payment_id, doc)


class TestChapaInitialize:
    def test_signed_out_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_denied(app_module, monkeypatch)
        client = _client(app_module)
        r = client.post("/api/payments/chapa/initialize", json={"plan": "monthly"})
        assert r.status_code == 401

    def test_invalid_plan_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch)
        client = _client(app_module)
        r = client.post("/api/payments/chapa/initialize", json={"plan": "lifetime"})
        assert r.status_code == 400

    def test_chapa_not_configured_returns_503(self, fake_db, app_module, monkeypatch):
        monkeypatch.delenv("CHAPA_SECRET_KEY", raising=False)
        require_user_as(app_module, monkeypatch)
        client = _client(app_module)
        r = client.post("/api/payments/chapa/initialize", json={"plan": "monthly"})
        assert r.status_code == 503

    def test_self_pay_creates_pending_payment_and_returns_checkout_url(self, fake_db, app_module, monkeypatch, chapa_api):
        monkeypatch.setenv("CHAPA_SECRET_KEY", "sk_test_x")
        _seed_user(fake_db, "user-1", name="Abebe Kebede")
        require_user_as(app_module, monkeypatch, uid="user-1")
        client = _client(app_module)
        r = client.post("/api/payments/chapa/initialize", json={"plan": "monthly"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["checkoutUrl"] == "https://checkout.chapa.co/abc123"
        payment = fake_db.dump("payments")[body["paymentId"]]
        assert payment["uid"] == "user-1"
        assert payment["amount"] == 9.99
        assert payment["status"] == "Pending"
        assert payment["checkoutUrl"] == "https://checkout.chapa.co/abc123"
        # tx_ref sent to Chapa must match the one derived for payment_id
        sent = chapa_api.calls[0]["json"]
        assert sent["tx_ref"] == payment["transactionId"]
        assert sent["amount"] == "9.99"

    def test_parent_paying_for_unlinked_child_is_denied(self, fake_db, app_module, monkeypatch, chapa_api):
        monkeypatch.setenv("CHAPA_SECRET_KEY", "sk_test_x")
        _seed_user(fake_db, "parent-1", accountType="parent")
        require_user_as(app_module, monkeypatch, uid="parent-1")
        client = _client(app_module)
        r = client.post("/api/payments/chapa/initialize", json={"plan": "monthly", "studentUid": "child-1"})
        assert r.status_code == 403

    def test_non_parent_cannot_pay_for_another_uid(self, fake_db, app_module, monkeypatch, chapa_api):
        monkeypatch.setenv("CHAPA_SECRET_KEY", "sk_test_x")
        _seed_user(fake_db, "user-1", accountType="student")
        require_user_as(app_module, monkeypatch, uid="user-1")
        client = _client(app_module)
        r = client.post("/api/payments/chapa/initialize", json={"plan": "monthly", "studentUid": "child-1"})
        assert r.status_code == 403

    def test_verified_parent_can_pay_for_linked_child(self, fake_db, app_module, monkeypatch, chapa_api):
        monkeypatch.setenv("CHAPA_SECRET_KEY", "sk_test_x")
        _seed_user(fake_db, "parent-1", accountType="parent")
        _seed_user(fake_db, "child-1", name="Child One")
        _seed_verified_link(fake_db, "parent-1", "child-1")
        require_user_as(app_module, monkeypatch, uid="parent-1")
        client = _client(app_module)
        r = client.post("/api/payments/chapa/initialize", json={"plan": "monthly", "studentUid": "child-1"})
        assert r.status_code == 200
        payment = fake_db.dump("payments")[r.get_json()["paymentId"]]
        assert payment["uid"] == "child-1"

    def test_missing_checkout_url_marks_payment_failed(self, fake_db, app_module, monkeypatch):
        monkeypatch.setenv("CHAPA_SECRET_KEY", "sk_test_x")
        _seed_user(fake_db, "user-1")
        require_user_as(app_module, monkeypatch, uid="user-1")
        fake = FakeChapaAPI(checkout_url="")
        import requests as requests_module
        monkeypatch.setattr(requests_module, "request", fake.request)
        client = _client(app_module)
        r = client.post("/api/payments/chapa/initialize", json={"plan": "monthly"})
        assert r.status_code == 502
        payments = fake_db.dump("payments")
        assert list(payments.values())[0]["status"] == "InitializationFailed"

    def test_chapa_rejecting_the_request_returns_503(self, fake_db, app_module, monkeypatch):
        monkeypatch.setenv("CHAPA_SECRET_KEY", "sk_test_x")
        _seed_user(fake_db, "user-1")
        require_user_as(app_module, monkeypatch, uid="user-1")
        fake = FakeChapaAPI(ok=False, message="Duplicate tx_ref.")
        import requests as requests_module
        monkeypatch.setattr(requests_module, "request", fake.request)
        client = _client(app_module)
        r = client.post("/api/payments/chapa/initialize", json={"plan": "monthly"})
        assert r.status_code == 503


class TestChapaCallbackAndWebhook:
    def _seed_chapa_payment(self, fake_db, app_module, tx_ref="BMT-abc123", uid="user-1", amount=9.99, plan="monthly"):
        _seed_user(fake_db, uid)
        payment_id = app_module._payment_id("chapa", tx_ref)
        _seed_payment(fake_db, payment_id, uid=uid, provider="chapa", amount=amount, plan=plan,
                      transactionId=tx_ref, txRef=tx_ref, verificationSource="chapa")
        return payment_id

    def test_callback_missing_tx_ref_is_rejected(self, fake_db, app_module):
        client = _client(app_module)
        r = client.get("/api/payments/chapa/callback")
        assert r.status_code == 400

    def test_callback_unknown_payment_returns_400(self, fake_db, app_module, monkeypatch, chapa_api):
        monkeypatch.setenv("CHAPA_SECRET_KEY", "sk_test_x")
        client = _client(app_module)
        r = client.get("/api/payments/chapa/callback?tx_ref=never-seen")
        assert r.status_code == 400

    def test_callback_chapa_verify_failure_returns_502(self, fake_db, app_module, monkeypatch):
        monkeypatch.setenv("CHAPA_SECRET_KEY", "sk_test_x")
        self._seed_chapa_payment(fake_db, app_module)
        fake = FakeChapaAPI(ok=False)
        import requests as requests_module
        monkeypatch.setattr(requests_module, "request", fake.request)
        client = _client(app_module)
        r = client.get("/api/payments/chapa/callback?tx_ref=BMT-abc123")
        assert r.status_code == 502

    def test_callback_pending_status_reports_not_unlocked(self, fake_db, app_module, monkeypatch):
        monkeypatch.setenv("CHAPA_SECRET_KEY", "sk_test_x")
        self._seed_chapa_payment(fake_db, app_module)
        fake = FakeChapaAPI(verify_status="pending")
        import requests as requests_module
        monkeypatch.setattr(requests_module, "request", fake.request)
        client = _client(app_module)
        r = client.get("/api/payments/chapa/callback?tx_ref=BMT-abc123")
        assert r.status_code == 200
        assert r.get_json()["unlocked"] is False

    def test_callback_amount_mismatch_is_rejected(self, fake_db, app_module, monkeypatch):
        monkeypatch.setenv("CHAPA_SECRET_KEY", "sk_test_x")
        self._seed_chapa_payment(fake_db, app_module, amount=9.99)
        fake = FakeChapaAPI(verify_amount="1.00")
        import requests as requests_module
        monkeypatch.setattr(requests_module, "request", fake.request)
        client = _client(app_module)
        r = client.get("/api/payments/chapa/callback?tx_ref=BMT-abc123")
        assert r.status_code == 400

    def test_callback_currency_mismatch_is_rejected(self, fake_db, app_module, monkeypatch):
        monkeypatch.setenv("CHAPA_SECRET_KEY", "sk_test_x")
        monkeypatch.setenv("PAYMENT_CURRENCY", "ETB")
        self._seed_chapa_payment(fake_db, app_module, amount=9.99)
        fake = FakeChapaAPI(verify_amount="9.99", verify_currency="USD")
        import requests as requests_module
        monkeypatch.setattr(requests_module, "request", fake.request)
        client = _client(app_module)
        r = client.get("/api/payments/chapa/callback?tx_ref=BMT-abc123")
        assert r.status_code == 400

    def test_callback_success_unlocks_and_notifies(self, fake_db, app_module, monkeypatch):
        monkeypatch.setenv("CHAPA_SECRET_KEY", "sk_test_x")
        monkeypatch.setenv("PAYMENT_CURRENCY", "ETB")
        self._seed_chapa_payment(fake_db, app_module, amount=9.99)
        fake = FakeChapaAPI(verify_amount="9.99", verify_currency="ETB")
        import requests as requests_module
        monkeypatch.setattr(requests_module, "request", fake.request)
        client = _client(app_module)
        r = client.get("/api/payments/chapa/callback?tx_ref=BMT-abc123")
        assert r.status_code == 200
        body = r.get_json()
        assert body["unlocked"] is True
        user = fake_db.dump("users")["user-1"]
        assert user["isPaid"] is True
        entitlement = fake_db.dump("entitlements")["user-1"]
        assert entitlement["premium"] is True
        assert entitlement["plan"] == "monthly"
        notifications = list(fake_db.dump("notifications").values())
        assert any(n["targetUid"] == "user-1" and n["kind"] == "payment" for n in notifications)

    def test_callback_twice_is_idempotent(self, fake_db, app_module, monkeypatch):
        monkeypatch.setenv("CHAPA_SECRET_KEY", "sk_test_x")
        monkeypatch.setenv("PAYMENT_CURRENCY", "ETB")
        self._seed_chapa_payment(fake_db, app_module, amount=9.99)
        fake = FakeChapaAPI(verify_amount="9.99", verify_currency="ETB")
        import requests as requests_module
        monkeypatch.setattr(requests_module, "request", fake.request)
        client = _client(app_module)
        first = client.get("/api/payments/chapa/callback?tx_ref=BMT-abc123")
        second = client.get("/api/payments/chapa/callback?tx_ref=BMT-abc123")
        assert first.get_json()["alreadyProcessed"] is False
        assert second.get_json()["alreadyProcessed"] is True
        assert len(fake_db.dump("entitlements")) == 1

    def _chapa_sig(self, secret, raw_body):
        return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

    def test_webhook_invalid_signature_is_rejected(self, fake_db, app_module, monkeypatch):
        monkeypatch.setenv("CHAPA_WEBHOOK_SECRET", "whsec_x")
        client = _client(app_module)
        r = client.post("/api/payments/webhook/chapa", json={"tx_ref": "BMT-abc123"},
                         headers={"x-chapa-signature": "wrong"})
        assert r.status_code == 401

    def test_webhook_no_secret_configured_is_rejected(self, fake_db, app_module, monkeypatch):
        monkeypatch.delenv("CHAPA_WEBHOOK_SECRET", raising=False)
        client = _client(app_module)
        r = client.post("/api/payments/webhook/chapa", json={"tx_ref": "BMT-abc123"})
        assert r.status_code == 401

    def test_webhook_missing_tx_ref_is_ignored(self, fake_db, app_module, monkeypatch):
        monkeypatch.setenv("CHAPA_WEBHOOK_SECRET", "whsec_x")
        body = b'{}'
        sig = self._chapa_sig("whsec_x", body)
        client = _client(app_module)
        r = client.post("/api/payments/webhook/chapa", data=body, content_type="application/json",
                         headers={"x-chapa-signature": sig})
        assert r.status_code == 200
        assert r.get_json()["ignored"] is True

    def test_webhook_success_unlocks(self, fake_db, app_module, monkeypatch):
        monkeypatch.setenv("CHAPA_WEBHOOK_SECRET", "whsec_x")
        monkeypatch.setenv("CHAPA_SECRET_KEY", "sk_test_x")
        monkeypatch.setenv("PAYMENT_CURRENCY", "ETB")
        self._seed_chapa_payment(fake_db, app_module, amount=9.99)
        fake = FakeChapaAPI(verify_amount="9.99", verify_currency="ETB")
        import requests as requests_module
        monkeypatch.setattr(requests_module, "request", fake.request)
        body = b'{"tx_ref": "BMT-abc123"}'
        sig = self._chapa_sig("whsec_x", body)
        client = _client(app_module)
        r = client.post("/api/payments/webhook/chapa", data=body, content_type="application/json",
                         headers={"x-chapa-signature": sig})
        assert r.status_code == 200
        assert r.get_json()["unlocked"] is True


class TestGenericPaymentSubmit:
    def test_signed_out_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_denied(app_module, monkeypatch)
        client = _client(app_module)
        r = client.post("/api/payments/submit", json={"provider": "manual", "transactionId": "T1"})
        assert r.status_code == 401

    def test_invalid_provider_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch)
        client = _client(app_module)
        r = client.post("/api/payments/submit", json={"provider": "paypal", "transactionId": "T1"})
        assert r.status_code == 400

    def test_missing_transaction_id_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch)
        client = _client(app_module)
        r = client.post("/api/payments/submit", json={"provider": "manual", "transactionId": ""})
        assert r.status_code == 400

    def test_invalid_plan_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch)
        client = _client(app_module)
        r = client.post("/api/payments/submit", json={"provider": "manual", "transactionId": "T1", "plan": "lifetime"})
        assert r.status_code == 400

    def test_duplicate_transaction_id_from_another_user_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-2")
        payment_id = app_module._payment_id("manual", "T1")
        _seed_payment(fake_db, payment_id, uid="user-1", provider="manual")
        client = _client(app_module)
        r = client.post("/api/payments/submit", json={"provider": "manual", "transactionId": "T1"})
        assert r.status_code == 409

    def test_resubmitting_own_transaction_is_idempotent(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        payment_id = app_module._payment_id("manual", "T1")
        _seed_payment(fake_db, payment_id, uid="user-1", provider="manual", status="Approved")
        client = _client(app_module)
        r = client.post("/api/payments/submit", json={"provider": "manual", "transactionId": "T1"})
        assert r.status_code == 200
        assert r.get_json()["status"] == "Approved"

    def test_happy_path_creates_pending_payment(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        client = _client(app_module)
        r = client.post("/api/payments/submit",
                         json={"provider": "telebirr", "transactionId": "TX-99", "plan": "yearly"})
        assert r.status_code == 201
        body = r.get_json()
        payment = fake_db.dump("payments")[body["paymentId"]]
        assert payment["status"] == "Pending"
        assert payment["amount"] == 79.99
        assert payment["uid"] == "user-1"


class TestParentSubmitPayment:
    def test_missing_fields_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="parent-1")
        client = _client(app_module)
        r = client.post("/api/payments/parent-submit", json={})
        assert r.status_code == 400

    def test_unauthorized_parent_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="parent-1")
        _seed_user(fake_db, "child-1")
        client = _client(app_module)
        r = client.post("/api/payments/parent-submit",
                         json={"studentUid": "child-1", "plan": "monthly", "provider": "manual", "transactionId": "T1"})
        assert r.status_code == 403

    def test_missing_student_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="parent-1")
        _seed_verified_link(fake_db, "parent-1", "child-1")
        client = _client(app_module)
        r = client.post("/api/payments/parent-submit",
                         json={"studentUid": "child-1", "plan": "monthly", "provider": "manual", "transactionId": "T1"})
        assert r.status_code == 404

    def test_happy_path(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="parent-1")
        _seed_user(fake_db, "child-1", name="Child One")
        _seed_verified_link(fake_db, "parent-1", "child-1")
        client = _client(app_module)
        r = client.post("/api/payments/parent-submit",
                         json={"studentUid": "child-1", "plan": "monthly", "provider": "manual", "transactionId": "T1"})
        assert r.status_code == 201
        payment = fake_db.dump("payments")[r.get_json()["paymentId"]]
        assert payment["uid"] == "child-1"
        assert payment["payerUid"] == "parent-1"

    def test_duplicate_transaction_for_different_pair_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="parent-2")
        _seed_user(fake_db, "child-2")
        _seed_verified_link(fake_db, "parent-2", "child-2")
        payment_id = app_module._payment_id("manual", "T1")
        _seed_payment(fake_db, payment_id, uid="someone-else", provider="manual", payerUid="another-parent")
        client = _client(app_module)
        r = client.post("/api/payments/parent-submit",
                         json={"studentUid": "child-2", "plan": "monthly", "provider": "manual", "transactionId": "T1"})
        assert r.status_code == 409


class TestApprovePayment:
    def test_non_admin_is_rejected(self, fake_db, app_module, monkeypatch):
        require_admin_denied(app_module, monkeypatch)
        client = _client(app_module)
        r = client.post("/api/payments/approve", json={"paymentId": "x"})
        assert r.status_code == 403

    def test_missing_payment_id_is_rejected(self, fake_db, app_module, monkeypatch):
        require_admin_as(app_module, monkeypatch)
        client = _client(app_module)
        r = client.post("/api/payments/approve", json={})
        assert r.status_code == 400

    def test_unknown_payment_is_rejected(self, fake_db, app_module, monkeypatch):
        require_admin_as(app_module, monkeypatch)
        client = _client(app_module)
        r = client.post("/api/payments/approve", json={"paymentId": "nope"})
        assert r.status_code == 400

    def test_happy_path_creates_entitlement_and_unlocks_user(self, fake_db, app_module, monkeypatch):
        require_admin_as(app_module, monkeypatch, uid="admin-1")
        _seed_user(fake_db, "user-1")
        _seed_payment(fake_db, "pay1", uid="user-1", status="Pending", plan="monthly")
        client = _client(app_module)
        r = client.post("/api/payments/approve", json={"paymentId": "pay1"})
        assert r.status_code == 200
        user = fake_db.dump("users")["user-1"]
        assert user["isPaid"] is True
        assert user["freeTrial"]["isActive"] is False
        entitlement = fake_db.dump("entitlements")["user-1"]
        assert entitlement["plan"] == "monthly"
        assert entitlement["updatedBy"] == "admin-1"
        payment = fake_db.dump("payments")["pay1"]
        assert payment["status"] == "Approved"
        audit = list(fake_db.dump("adminAuditLogs").values())
        assert any(a["action"] == "payment_approved" for a in audit)

    def test_already_approved_is_idempotent(self, fake_db, app_module, monkeypatch):
        require_admin_as(app_module, monkeypatch)
        _seed_user(fake_db, "user-1")
        fake_db.seed("entitlements", "user-1", {"premium": True, "plan": "monthly"})
        _seed_payment(fake_db, "pay1", uid="user-1", status="Approved")
        client = _client(app_module)
        r = client.post("/api/payments/approve", json={"paymentId": "pay1"})
        assert r.status_code == 200
        assert r.get_json()["already_approved"] is True

    def test_rejected_payment_cannot_be_approved(self, fake_db, app_module, monkeypatch):
        require_admin_as(app_module, monkeypatch)
        _seed_user(fake_db, "user-1")
        _seed_payment(fake_db, "pay1", uid="user-1", status="Rejected")
        client = _client(app_module)
        r = client.post("/api/payments/approve", json={"paymentId": "pay1"})
        assert r.status_code == 400

    def test_renewal_extends_from_existing_unexpired_entitlement(self, fake_db, app_module, monkeypatch):
        require_admin_as(app_module, monkeypatch)
        _seed_user(fake_db, "user-1")
        future_expiry = datetime.now(timezone.utc) + timedelta(days=10)
        fake_db.seed("entitlements", "user-1", {"premium": True, "plan": "monthly",
                                                 "startsAt": datetime.now(timezone.utc) - timedelta(days=20),
                                                 "expiresAt": future_expiry})
        _seed_payment(fake_db, "pay2", uid="user-1", status="Pending", plan="monthly")
        client = _client(app_module)
        r = client.post("/api/payments/approve", json={"paymentId": "pay2"})
        assert r.status_code == 200
        entitlement = fake_db.dump("entitlements")["user-1"]
        # Renewal stacks on top of the remaining time, not from "now".
        assert entitlement["expiresAt"] > future_expiry


class TestRejectPayment:
    def test_non_admin_is_rejected(self, fake_db, app_module, monkeypatch):
        require_admin_denied(app_module, monkeypatch)
        client = _client(app_module)
        r = client.post("/api/payments/reject", json={"paymentId": "x"})
        assert r.status_code == 403

    def test_missing_payment_id_is_rejected(self, fake_db, app_module, monkeypatch):
        require_admin_as(app_module, monkeypatch)
        client = _client(app_module)
        r = client.post("/api/payments/reject", json={})
        assert r.status_code == 400

    def test_not_found_is_rejected(self, fake_db, app_module, monkeypatch):
        require_admin_as(app_module, monkeypatch)
        client = _client(app_module)
        r = client.post("/api/payments/reject", json={"paymentId": "nope"})
        assert r.status_code == 404

    def test_approved_payment_cannot_be_rejected(self, fake_db, app_module, monkeypatch):
        require_admin_as(app_module, monkeypatch)
        _seed_payment(fake_db, "pay1", status="Approved")
        client = _client(app_module)
        r = client.post("/api/payments/reject", json={"paymentId": "pay1"})
        assert r.status_code == 409

    def test_happy_path(self, fake_db, app_module, monkeypatch):
        require_admin_as(app_module, monkeypatch, uid="admin-9")
        _seed_payment(fake_db, "pay1", status="Pending")
        client = _client(app_module)
        r = client.post("/api/payments/reject", json={"paymentId": "pay1"})
        assert r.status_code == 200
        payment = fake_db.dump("payments")["pay1"]
        assert payment["status"] == "Rejected"
        assert payment["rejectedBy"] == "admin-9"


class TestGatewayWebhook:
    def _sig(self, secret, raw_body):
        return hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()

    def test_unsupported_provider_is_404(self, fake_db, app_module):
        client = _client(app_module)
        r = client.post("/api/payments/webhook/paypal", json={})
        assert r.status_code == 404

    def test_invalid_signature_is_rejected(self, fake_db, app_module, monkeypatch):
        monkeypatch.setenv("TELEBIRR_WEBHOOK_SECRET", "s3cret")
        client = _client(app_module)
        r = client.post("/api/payments/webhook/telebirr", json={"transactionId": "T1", "status": "success"},
                         headers={"X-BMT-Signature": "wrong"})
        assert r.status_code == 401

    def test_unverified_status_is_ignored(self, fake_db, app_module, monkeypatch):
        monkeypatch.setenv("TELEBIRR_WEBHOOK_SECRET", "s3cret")
        body = b'{"transactionId": "T1", "status": "pending"}'
        sig = self._sig("s3cret", body)
        client = _client(app_module)
        r = client.post("/api/payments/webhook/telebirr", data=body, content_type="application/json",
                         headers={"X-BMT-Signature": sig})
        assert r.status_code == 200
        assert r.get_json()["ignored"] is True

    def test_unknown_payment_is_404(self, fake_db, app_module, monkeypatch):
        monkeypatch.setenv("TELEBIRR_WEBHOOK_SECRET", "s3cret")
        body = b'{"transactionId": "never-submitted", "status": "success", "amount": 9.99}'
        sig = self._sig("s3cret", body)
        client = _client(app_module)
        r = client.post("/api/payments/webhook/telebirr", data=body, content_type="application/json",
                         headers={"X-BMT-Signature": sig})
        assert r.status_code == 404

    def test_missing_amount_without_override_is_rejected(self, fake_db, app_module, monkeypatch):
        monkeypatch.delenv("ALLOW_WEBHOOK_WITHOUT_AMOUNT", raising=False)
        monkeypatch.setenv("TELEBIRR_WEBHOOK_SECRET", "s3cret")
        payment_id = app_module._payment_id("telebirr", "T1")
        _seed_payment(fake_db, payment_id, uid="user-1", provider="telebirr", amount=9.99)
        body = b'{"transactionId": "T1", "status": "success"}'
        sig = self._sig("s3cret", body)
        client = _client(app_module)
        r = client.post("/api/payments/webhook/telebirr", data=body, content_type="application/json",
                         headers={"X-BMT-Signature": sig})
        assert r.status_code == 400

    def test_amount_mismatch_is_rejected(self, fake_db, app_module, monkeypatch):
        monkeypatch.setenv("TELEBIRR_WEBHOOK_SECRET", "s3cret")
        payment_id = app_module._payment_id("telebirr", "T1")
        _seed_payment(fake_db, payment_id, uid="user-1", provider="telebirr", amount=9.99)
        body = b'{"transactionId": "T1", "status": "success", "amount": 1.00}'
        sig = self._sig("s3cret", body)
        client = _client(app_module)
        r = client.post("/api/payments/webhook/telebirr", data=body, content_type="application/json",
                         headers={"X-BMT-Signature": sig})
        assert r.status_code == 409

    def test_currency_mismatch_is_rejected(self, fake_db, app_module, monkeypatch):
        monkeypatch.setenv("TELEBIRR_WEBHOOK_SECRET", "s3cret")
        monkeypatch.setenv("PAYMENT_CURRENCY", "ETB")
        _seed_user(fake_db, "user-1")
        payment_id = app_module._payment_id("telebirr", "T1")
        _seed_payment(fake_db, payment_id, uid="user-1", provider="telebirr", amount=9.99)
        body = b'{"transactionId": "T1", "status": "success", "amount": 9.99, "currency": "USD"}'
        sig = self._sig("s3cret", body)
        client = _client(app_module)
        r = client.post("/api/payments/webhook/telebirr", data=body, content_type="application/json",
                         headers={"X-BMT-Signature": sig})
        assert r.status_code == 409

    def test_happy_path_unlocks(self, fake_db, app_module, monkeypatch):
        monkeypatch.setenv("TELEBIRR_WEBHOOK_SECRET", "s3cret")
        monkeypatch.setenv("PAYMENT_CURRENCY", "ETB")
        _seed_user(fake_db, "user-1")
        payment_id = app_module._payment_id("telebirr", "T1")
        _seed_payment(fake_db, payment_id, uid="user-1", provider="telebirr", amount=9.99)
        body = b'{"transactionId": "T1", "status": "success", "amount": 9.99, "currency": "ETB"}'
        sig = self._sig("s3cret", body)
        client = _client(app_module)
        r = client.post("/api/payments/webhook/telebirr", data=body, content_type="application/json",
                         headers={"X-BMT-Signature": sig})
        assert r.status_code == 200
        assert r.get_json()["unlocked"] is True
        assert fake_db.dump("users")["user-1"]["isPaid"] is True

    def test_repeated_webhook_delivery_is_idempotent(self, fake_db, app_module, monkeypatch):
        """Payment gateways routinely redeliver the same webhook - this must
        never grant a second entitlement stack."""
        monkeypatch.setenv("TELEBIRR_WEBHOOK_SECRET", "s3cret")
        monkeypatch.setenv("PAYMENT_CURRENCY", "ETB")
        _seed_user(fake_db, "user-1")
        payment_id = app_module._payment_id("telebirr", "T1")
        _seed_payment(fake_db, payment_id, uid="user-1", provider="telebirr", amount=9.99)
        body = b'{"transactionId": "T1", "status": "success", "amount": 9.99, "currency": "ETB"}'
        sig = self._sig("s3cret", body)
        client = _client(app_module)
        results = [client.post("/api/payments/webhook/telebirr", data=body, content_type="application/json",
                                headers={"X-BMT-Signature": sig}) for _ in range(3)]
        assert [r.get_json()["alreadyProcessed"] for r in results] == [False, True, True]
        assert len(fake_db.dump("entitlements")) == 1
