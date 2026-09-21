"""Real behavioural tests for teacher_routes.py's admin approve/reject
endpoints: admin gating, status-transition guards, and the atomic batch
write that keeps the teacherRequests and teachers documents in sync.
"""
import pytest

from teacher_routes import register_teacher_routes
from tests.conftest import make_app


def require_admin_ok(uid="admin-1"):
    return lambda: (True, {"uid": uid})


def require_admin_denied():
    return lambda: (False, ({"error": "Admin access required."}, 403))


def _client(fake_db, require_admin, firebase_configured=True):
    factory = (lambda: True) if firebase_configured else (lambda: None)
    app = make_app(register_teacher_routes, lambda: (True, {"uid": "irrelevant"}), require_admin, factory)
    return app.test_client()


class TestApproveTeacher:
    def test_non_admin_is_rejected(self, fake_db):
        client = _client(fake_db, require_admin_denied())
        r = client.post("/api/admin/teachers/approve", json={"requestId": "req1"})
        assert r.status_code == 403

    def test_missing_request_id_is_rejected(self, fake_db):
        client = _client(fake_db, require_admin_ok())
        r = client.post("/api/admin/teachers/approve", json={})
        assert r.status_code == 400

    def test_unknown_request_returns_404(self, fake_db):
        client = _client(fake_db, require_admin_ok())
        r = client.post("/api/admin/teachers/approve", json={"requestId": "nope"})
        assert r.status_code == 404

    def test_request_with_no_uid_is_rejected(self, fake_db):
        fake_db.seed("teacherRequests", "req1", {"status": "pending"})
        client = _client(fake_db, require_admin_ok())
        r = client.post("/api/admin/teachers/approve", json={"requestId": "req1"})
        assert r.status_code == 400

    def test_already_approved_is_idempotent(self, fake_db):
        fake_db.seed("teacherRequests", "req1", {"status": "approved", "uid": "t1"})
        client = _client(fake_db, require_admin_ok())
        r = client.post("/api/admin/teachers/approve", json={"requestId": "req1"})
        assert r.status_code == 200
        assert r.get_json()["alreadyApproved"] is True

    def test_rejected_request_cannot_be_approved(self, fake_db):
        fake_db.seed("teacherRequests", "req1", {"status": "rejected", "uid": "t1"})
        client = _client(fake_db, require_admin_ok())
        r = client.post("/api/admin/teachers/approve", json={"requestId": "req1"})
        assert r.status_code == 409

    def test_successful_approval_updates_both_documents(self, fake_db):
        fake_db.seed("teacherRequests", "req1", {"status": "pending", "uid": "t1", "name": "Abebe"})
        client = _client(fake_db, require_admin_ok("admin-9"))
        r = client.post("/api/admin/teachers/approve", json={"requestId": "req1"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["success"] is True
        assert body["uid"] == "t1"

        request_doc = fake_db.dump("teacherRequests")["req1"]
        assert request_doc["status"] == "approved"
        assert request_doc["approvedBy"] == "admin-9"

        teacher_doc = fake_db.dump("teachers")["t1"]
        assert teacher_doc["approved"] is True
        assert teacher_doc["status"] == "approved"
        assert teacher_doc["name"] == "Abebe"  # original request fields carry over
        assert teacher_doc["permissions"]["createQuiz"] is True
        assert teacher_doc["permissions"]["approvePayments"] is False

    def test_submitted_status_can_also_be_approved(self, fake_db):
        fake_db.seed("teacherRequests", "req1", {"status": "submitted", "uid": "t1"})
        client = _client(fake_db, require_admin_ok())
        r = client.post("/api/admin/teachers/approve", json={"requestId": "req1"})
        assert r.status_code == 200
        assert fake_db.dump("teachers")["t1"]["approved"] is True


class TestRejectTeacher:
    def test_non_admin_is_rejected(self, fake_db):
        client = _client(fake_db, require_admin_denied())
        r = client.post("/api/admin/teachers/reject", json={"requestId": "req1"})
        assert r.status_code == 403

    def test_missing_request_id_is_rejected(self, fake_db):
        client = _client(fake_db, require_admin_ok())
        r = client.post("/api/admin/teachers/reject", json={})
        assert r.status_code == 400

    def test_unknown_request_returns_404(self, fake_db):
        client = _client(fake_db, require_admin_ok())
        r = client.post("/api/admin/teachers/reject", json={"requestId": "nope"})
        assert r.status_code == 404

    def test_approved_request_cannot_be_rejected(self, fake_db):
        fake_db.seed("teacherRequests", "req1", {"status": "approved", "uid": "t1"})
        client = _client(fake_db, require_admin_ok())
        r = client.post("/api/admin/teachers/reject", json={"requestId": "req1"})
        assert r.status_code == 409

    def test_already_rejected_is_idempotent(self, fake_db):
        fake_db.seed("teacherRequests", "req1", {"status": "rejected", "uid": "t1"})
        client = _client(fake_db, require_admin_ok())
        r = client.post("/api/admin/teachers/reject", json={"requestId": "req1"})
        assert r.status_code == 200
        assert r.get_json()["alreadyRejected"] is True

    def test_successful_rejection_records_reason_and_admin(self, fake_db):
        fake_db.seed("teacherRequests", "req1", {"status": "pending", "uid": "t1"})
        client = _client(fake_db, require_admin_ok("admin-9"))
        r = client.post("/api/admin/teachers/reject", json={"requestId": "req1", "reason": "Missing certificate"})
        assert r.status_code == 200
        doc = fake_db.dump("teacherRequests")["req1"]
        assert doc["status"] == "rejected"
        assert doc["rejectedBy"] == "admin-9"
        assert doc["rejectionReason"] == "Missing certificate"

    def test_rejection_reason_is_truncated_to_500_chars(self, fake_db):
        fake_db.seed("teacherRequests", "req1", {"status": "pending", "uid": "t1"})
        client = _client(fake_db, require_admin_ok())
        r = client.post("/api/admin/teachers/reject", json={"requestId": "req1", "reason": "x" * 900})
        assert r.status_code == 200
        assert len(fake_db.dump("teacherRequests")["req1"]["rejectionReason"]) == 500
