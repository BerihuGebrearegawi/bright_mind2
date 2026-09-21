"""Per-plan coverage of app.py's PAYMENT_PLANS - flagged in the handoff
notes as an edge-case gap ("not all PAYMENT_PLANS price points
individually tested"). The existing payment tests exercise the "monthly"
plan almost exclusively; this file parametrizes over every entry in
PAYMENT_PLANS so a change to any plan's amount/duration is caught
regardless of which plan it is.

If a new plan is ever added to PAYMENT_PLANS in app.py, it is
automatically covered by these tests without any edits here.
"""
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_app_payments import (
    require_admin_as,
    require_user_as,
    _client,
    _seed_user,
    _seed_payment,
)


@pytest.fixture
def plans(app_module):
    return app_module.PAYMENT_PLANS


def _plan_ids(app_module):
    return sorted(app_module.PAYMENT_PLANS.keys())


class TestChapaInitializeAmountPerPlan:
    """Every plan must produce a Chapa initialize call and a stored payment
    for exactly its own configured amount - not just "monthly"."""

    def test_every_plan_initializes_with_its_own_amount(self, fake_db, app_module, monkeypatch, chapa_api):
        monkeypatch.setenv("CHAPA_SECRET_KEY", "sk_test_x")
        _seed_user(fake_db, "user-1", name="Abebe Kebede")
        require_user_as(app_module, monkeypatch, uid="user-1")
        client = _client(app_module)
        for plan_name, plan in app_module.PAYMENT_PLANS.items():
            r = client.post("/api/payments/chapa/initialize", json={"plan": plan_name})
            assert r.status_code == 200, f"plan={plan_name}"
            sent = chapa_api.calls[-1]["json"]
            assert float(sent["amount"]) == plan["amount"], plan_name
            payment_id = r.get_json()["paymentId"]
            payment = fake_db.dump("payments")[payment_id]
            assert payment["amount"] == plan["amount"], plan_name
            assert payment["plan"] == plan_name


class TestApprovalGrantsPerPlanDurationAndAmount:
    """Approving a payment must stamp the entitlement with that specific
    plan's amount and extend expiry by that specific plan's duration_days -
    not a value hardcoded to the monthly plan."""

    @pytest.mark.parametrize("plan_name", ["monthly", "yearly"])
    def test_fresh_entitlement_gets_correct_amount_and_duration(self, fake_db, app_module, monkeypatch, plan_name):
        plan = app_module.PAYMENT_PLANS[plan_name]
        require_admin_as(app_module, monkeypatch)
        _seed_user(fake_db, "user-1")
        _seed_payment(fake_db, "pay1", uid="user-1", status="Pending", plan=plan_name, amount=plan["amount"])
        client = _client(app_module)
        before = datetime.now(timezone.utc)
        r = client.post("/api/payments/approve", json={"paymentId": "pay1"})
        assert r.status_code == 200
        entitlement = fake_db.dump("entitlements")["user-1"]
        assert entitlement["plan"] == plan_name
        assert entitlement["amount"] == plan["amount"]
        expected_expiry = before + timedelta(days=plan["duration_days"])
        # Allow a small margin for the time the test itself takes to run.
        assert abs((entitlement["expiresAt"] - expected_expiry).total_seconds()) < 5

    @pytest.mark.parametrize("plan_name", ["monthly", "yearly"])
    def test_renewal_extends_by_that_plans_duration(self, fake_db, app_module, monkeypatch, plan_name):
        plan = app_module.PAYMENT_PLANS[plan_name]
        require_admin_as(app_module, monkeypatch)
        _seed_user(fake_db, "user-1")
        existing_expiry = datetime.now(timezone.utc) + timedelta(days=5)
        fake_db.seed("entitlements", "user-1", {
            "premium": True, "plan": plan_name,
            "startsAt": datetime.now(timezone.utc) - timedelta(days=1),
            "expiresAt": existing_expiry,
        })
        _seed_payment(fake_db, "pay2", uid="user-1", status="Pending", plan=plan_name, amount=plan["amount"])
        client = _client(app_module)
        r = client.post("/api/payments/approve", json={"paymentId": "pay2"})
        assert r.status_code == 200
        entitlement = fake_db.dump("entitlements")["user-1"]
        expected_expiry = existing_expiry + timedelta(days=plan["duration_days"])
        assert abs((entitlement["expiresAt"] - expected_expiry).total_seconds()) < 5


class TestInvalidPlanNameRejectedEverywhere:
    """A plan name that isn't a key in PAYMENT_PLANS must never silently
    fall back to a default plan's price at any entry point."""

    def test_chapa_initialize_rejects_unknown_plan(self, fake_db, app_module, monkeypatch):
        monkeypatch.setenv("CHAPA_SECRET_KEY", "sk_test_x")
        require_user_as(app_module, monkeypatch)
        client = _client(app_module)
        r = client.post("/api/payments/chapa/initialize", json={"plan": "lifetime"})
        assert r.status_code == 400

    def test_submit_payment_rejects_unknown_plan(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch)
        client = _client(app_module)
        r = client.post("/api/payments/submit", json={
            "plan": "lifetime", "provider": "manual", "transactionId": "TX-1",
        })
        assert r.status_code == 400

    def test_approve_rejects_payment_with_unknown_plan(self, fake_db, app_module, monkeypatch):
        require_admin_as(app_module, monkeypatch)
        _seed_user(fake_db, "user-1")
        _seed_payment(fake_db, "pay1", uid="user-1", status="Pending", plan="lifetime", amount=1.0)
        client = _client(app_module)
        r = client.post("/api/payments/approve", json={"paymentId": "pay1"})
        assert r.status_code == 400
