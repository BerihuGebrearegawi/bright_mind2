from datetime import datetime, timedelta, timezone

from targeting_access import student_audience_set


def test_expired_entitlement_does_not_grant_paid_regular():
    expired = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert student_audience_set(
        {"learningMode": "Regular", "isPaid": True},
        {"premium": True, "expiresAt": expired},
    ) == {"Free Regular"}


def test_active_entitlement_grants_paid_regular():
    active = datetime.now(timezone.utc) + timedelta(days=1)
    assert student_audience_set(
        {"learningMode": "Regular", "isPaid": False},
        {"premium": True, "expiresAt": active},
    ) == {"Paid Regular"}


def test_legacy_is_paid_without_entitlement_expiry_still_works():
    assert student_audience_set(
        {"learningMode": "Regular", "isPaid": True},
        {},
    ) == {"Paid Regular"}

def test_premium_entitlement_without_expiry_does_not_grant_paid_regular():
    assert student_audience_set(
        {"learningMode": "Regular", "isPaid": False},
        {"premium": True},
    ) == {"Free Regular"}
