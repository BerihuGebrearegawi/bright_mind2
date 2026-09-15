"""Real behavioural tests for parent_routes.py's authorization boundary:
require_parent() role gating, the verified-parentChildLinks check that
gates access to a specific child's data, and the link-by-code flow parents
use to connect to a student account.
"""
import pytest

from parent_routes import register_parent_routes
from tests.conftest import make_app


def require_user_as(uid):
    return lambda: (True, {"uid": uid})


def _client(fake_db, uid, firebase_configured=True):
    factory = (lambda: True) if firebase_configured else (lambda: None)
    app = make_app(register_parent_routes, require_user_as(uid), factory)
    return app.test_client()


class TestParentRoleGating:
    def test_non_parent_account_is_rejected(self, fake_db):
        fake_db.seed("users", "u1", {"accountType": "student"})
        client = _client(fake_db, "u1")
        r = client.get("/api/parent/children")
        assert r.status_code == 403

    def test_account_with_no_role_set_is_allowed(self, fake_db):
        """A user record with no accountType yet doesn't fail the parent
        check (role is falsy) - this is existing behavior worth pinning down
        so a future change to this gate is a deliberate decision, not a
        silent regression."""
        client = _client(fake_db, "u1")
        r = client.get("/api/parent/children")
        assert r.status_code == 200

    def test_firebase_not_configured_returns_503(self, fake_db):
        client = _client(fake_db, "u1", firebase_configured=False)
        r = client.get("/api/parent/children")
        assert r.status_code == 503


class TestChildAccessRequiresVerifiedLink:
    def test_pending_link_does_not_grant_access(self, fake_db):
        fake_db.seed("users", "p1", {"accountType": "parent"})
        fake_db.seed("parentChildLinks", "l1", {"parentUid": "p1", "childUid": "c1", "status": "pending"})
        client = _client(fake_db, "p1")
        r = client.get("/api/parent/children/c1/progress")
        assert r.status_code == 403

    def test_verified_link_grants_access(self, fake_db):
        fake_db.seed("users", "p1", {"accountType": "parent"})
        fake_db.seed("parentChildLinks", "l1", {"parentUid": "p1", "childUid": "c1", "status": "verified"})
        fake_db.seed("users", "c1", {"displayName": "Kid"})
        client = _client(fake_db, "p1")
        r = client.get("/api/parent/children/c1/progress")
        assert r.status_code == 200

    def test_cannot_access_an_unlinked_child_via_another_verified_link(self, fake_db):
        """A parent verified for one child must not be able to read a
        different child's data just by guessing/changing the child_id."""
        fake_db.seed("users", "p1", {"accountType": "parent"})
        fake_db.seed("parentChildLinks", "l1", {"parentUid": "p1", "childUid": "other-kid", "status": "verified"})
        client = _client(fake_db, "p1")
        r = client.get("/api/parent/children/c1/progress")
        assert r.status_code == 403


class TestLinkByCode:
    def test_empty_code_is_rejected(self, fake_db):
        fake_db.seed("users", "p1", {"accountType": "parent"})
        client = _client(fake_db, "p1")
        r = client.post("/api/parent/link-by-code", json={"code": ""})
        assert r.status_code == 400

    def test_unknown_code_is_rejected(self, fake_db):
        fake_db.seed("users", "p1", {"accountType": "parent"})
        client = _client(fake_db, "p1")
        r = client.post("/api/parent/link-by-code", json={"code": "BMT-NOPE1234"})
        assert r.status_code == 404

    def test_code_belonging_to_non_student_is_rejected(self, fake_db):
        fake_db.seed("users", "p1", {"accountType": "parent"})
        fake_db.seed("users", "t1", {"accountType": "teacher", "parentLinkCode": "BMT-ABC12345"})
        client = _client(fake_db, "p1")
        r = client.post("/api/parent/link-by-code", json={"code": "BMT-ABC12345"})
        assert r.status_code == 409

    def test_successful_link_is_case_insensitive_and_marked_verified(self, fake_db):
        fake_db.seed("users", "p1", {"accountType": "parent"})
        fake_db.seed("users", "s1", {"accountType": "student", "parentLinkCode": "BMT-ABC12345", "name": "Kid1"})
        client = _client(fake_db, "p1")
        r = client.post("/api/parent/link-by-code", json={"code": "bmt-abc12345"})
        assert r.status_code == 201
        links = fake_db.dump("parentChildLinks")
        assert len(links) == 1
        link = list(links.values())[0]
        assert link["status"] == "verified"
        assert link["parentUid"] == "p1"
        assert link["childUid"] == "s1"

    def test_ambiguous_duplicate_code_is_rejected(self, fake_db):
        """Two accounts somehow sharing the same code must not silently link
        to whichever one the query happens to return first."""
        fake_db.seed("users", "p1", {"accountType": "parent"})
        fake_db.seed("users", "s1", {"accountType": "student", "parentLinkCode": "BMT-DUPDUP12"})
        fake_db.seed("users", "s2", {"accountType": "student", "parentLinkCode": "BMT-DUPDUP12"})
        client = _client(fake_db, "p1")
        r = client.post("/api/parent/link-by-code", json={"code": "BMT-DUPDUP12"})
        assert r.status_code == 404
