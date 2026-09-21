"""Coverage for GET /api/admin/preview-users (admin_control_routes.py),
added alongside the V31.107 Dashboard Preview rework. This route feeds the
admin panel's real-account picker, so admin.js can let an admin choose one
real student/teacher/parent to preview instead of showing fabricated data.

admin_control_routes.py captures `require_admin` and `firebase_admin_factory`
as plain arguments at registration time (see app.py: `register_admin_control_
routes(app, _require_admin_bearer, _firebase_admin_from_env)`), so patching
app.py's module-level functions afterwards has no effect here (this mirrors
the note in conftest.py's app_module fixture). Instead, each test registers
the routes on its own fresh Flask app with fakes supplied directly.
"""
from flask import Flask

from admin_control_routes import register_admin_control_routes


def _make_app(fake_db, admin_ok=True, firebase_configured=True):
    app = Flask(__name__)
    require_admin = (lambda: (True, {"uid": "admin-1"})) if admin_ok else (
        lambda: (False, ({"error": "Admin access required."}, 403))
    )
    register_admin_control_routes(app, require_admin, lambda: (True if firebase_configured else None))
    return app.test_client()


class TestAdminPreviewUsers:
    def test_non_admin_is_rejected(self, fake_db):
        client = _make_app(fake_db, admin_ok=False)
        r = client.get("/api/admin/preview-users?role=student")
        assert r.status_code == 403

    def test_invalid_role_is_rejected(self, fake_db):
        client = _make_app(fake_db)
        r = client.get("/api/admin/preview-users?role=admin")
        assert r.status_code == 400

    def test_lists_real_accounts_by_role(self, fake_db):
        fake_db.seed("users", "s1", {"role": "student", "displayName": "Abebe Kebede", "className": "Grade 8"})
        fake_db.seed("users", "s2", {"role": "", "displayName": "Legacy Student"})  # blank role == student
        fake_db.seed("users", "t1", {"role": "teacher", "displayName": "Alemu Tesfaye", "subject": "Math"})
        fake_db.seed("users", "p1", {"role": "parent", "displayName": "Fatuma Ali"})
        fake_db.seed("users", "admin1", {"role": "admin", "isAdmin": True, "displayName": "Root Admin"})
        client = _make_app(fake_db)

        students = client.get("/api/admin/preview-users?role=student").get_json()["users"]
        assert {u["uid"] for u in students} == {"s1", "s2"}

        teachers = client.get("/api/admin/preview-users?role=teacher").get_json()["users"]
        assert [u["uid"] for u in teachers] == ["t1"]
        assert teachers[0]["label"] == "Math"

        parents = client.get("/api/admin/preview-users?role=parent").get_json()["users"]
        assert [u["uid"] for u in parents] == ["p1"]

        # Admin accounts are never offered as preview targets.
        assert "admin1" not in {u["uid"] for u in students + teachers + parents}

    def test_search_query_filters_results(self, fake_db):
        fake_db.seed("users", "t1", {"role": "teacher", "displayName": "Alemu Tesfaye"})
        fake_db.seed("users", "t2", {"role": "teacher", "displayName": "Sara Bekele"})
        client = _make_app(fake_db)
        r = client.get("/api/admin/preview-users?role=teacher&q=alemu")
        users = r.get_json()["users"]
        assert [u["uid"] for u in users] == ["t1"]
