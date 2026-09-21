"""Regression tests for the three defects found by the V31.108 final audit:

1. phone+PIN login crashed (undefined `now`) - every attempt returned HTTP 500.
2. /api/library returned an empty list to approved teachers (student-only gate).
3. Legacy lesson `url` accepted javascript: and was rendered into a student href.

Runs on the in-memory FakeFirestore; no network, no real Firebase.
"""
from pathlib import Path

from tests.conftest import make_app
from teacher_routes import register_teacher_routes
from student_course_routes import register_student_course_routes

ROOT = Path(__file__).resolve().parents[1]
PHONE = "0912345678"
NORMALIZED_PHONE = "+251912345678"


# ---------------------------------------------------------------- 1. login
def _configure_firebase(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "_firebase_admin_from_env", lambda: True)


def _login(app_module, pin):
    return app_module.app.test_client().post("/api/auth/phone-pin/login", json={"phone": PHONE, "pin": pin})


class TestPhonePinLoginDoesNotCrash:
    def test_correct_pin_returns_token_and_resets_counter(self, fake_db, app_module, monkeypatch, fake_firebase_auth):
        _configure_firebase(app_module, monkeypatch)
        fake_firebase_auth.seed_user("s-uid", phone_number=NORMALIZED_PHONE)
        fake_db.seed("users", "s-uid", {"role": "student"})
        fake_db.seed("authSecrets", "s-uid", {"pinHash": app_module._hash_pin("123456"), "failedAttempts": 3})
        r = _login(app_module, "123456")
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["success"] is True
        assert fake_db.dump("authSecrets")["s-uid"]["failedAttempts"] == 0

    def test_wrong_pin_is_401_and_counts_the_failure(self, fake_db, app_module, monkeypatch, fake_firebase_auth):
        _configure_firebase(app_module, monkeypatch)
        fake_firebase_auth.seed_user("s-uid", phone_number=NORMALIZED_PHONE)
        fake_db.seed("users", "s-uid", {"role": "student"})
        fake_db.seed("authSecrets", "s-uid", {"pinHash": app_module._hash_pin("999999")})
        r = _login(app_module, "123456")
        assert r.status_code == 401
        assert fake_db.dump("authSecrets")["s-uid"]["failedAttempts"] == 1


# --------------------------------------------------------------- 2. library
def _as(app_module, monkeypatch, uid, **extra):
    monkeypatch.setattr(app_module, "_require_user_bearer", lambda: (True, {"uid": uid, **extra}))


def _titles(app_module):
    r = app_module.app.test_client().get("/api/library")
    assert r.status_code == 200
    return [i["title"] for i in r.get_json().get("items", [])]


class TestLibraryForTeachers:
    def _seed(self, fake_db):
        fake_db.seed("books", "b7", {"title": "Grade 7 Math", "className": "7"})
        fake_db.seed("books", "b8", {"title": "Grade 8 Math", "className": "8"})
        fake_db.seed("users", "stu", {"class": "7", "role": "student"})
        fake_db.seed("users", "tch", {"role": "teacher"})

    def test_approved_teacher_sees_the_library(self, fake_db, app_module, monkeypatch):
        self._seed(fake_db)
        fake_db.seed("teachers", "tch", {"approved": True, "uid": "tch"})
        _as(app_module, monkeypatch, "tch", role="teacher")
        assert sorted(_titles(app_module)) == ["Grade 7 Math", "Grade 8 Math"]

    def test_unapproved_teacher_still_gets_nothing(self, fake_db, app_module, monkeypatch):
        self._seed(fake_db)
        fake_db.seed("teachers", "tch", {"approved": False, "uid": "tch"})
        _as(app_module, monkeypatch, "tch", role="teacher")
        assert _titles(app_module) == []

    def test_student_isolation_is_unchanged(self, fake_db, app_module, monkeypatch):
        self._seed(fake_db)
        _as(app_module, monkeypatch, "stu")
        assert _titles(app_module) == ["Grade 7 Math"]

    def test_student_cannot_pick_another_grade(self, fake_db, app_module, monkeypatch):
        self._seed(fake_db)
        _as(app_module, monkeypatch, "stu")
        r = app_module.app.test_client().get("/api/library?className=8")
        assert r.get_json()["items"] == []

    def test_user_with_no_grade_and_no_teacher_record_gets_nothing(self, fake_db, app_module, monkeypatch):
        self._seed(fake_db)
        fake_db.seed("users", "nobody", {"role": "student"})
        _as(app_module, monkeypatch, "nobody")
        assert _titles(app_module) == []


# --------------------------------------------------- 3. legacy lesson links
def _teacher(db):
    db.seed("teachers", "t1", {"approved": True})
    db.seed("courses", "c1", {"title": "C", "className": "7", "teacherUid": "t1", "status": "published", "subject": "Math"})
    app = make_app(register_teacher_routes, lambda: (True, {"uid": "t1", "email": "t@x"}),
                   lambda: (True, {"uid": "a", "admin": True}), lambda: True)
    return app.test_client()


def _student(db):
    db.seed("users", "s1", {"className": "7", "class": "7", "learningMode": "Regular"})
    app = make_app(register_student_course_routes, lambda: (True, {"uid": "s1", "email": "s@x"}), lambda: True)
    return app.test_client()


def _create(client, url):
    return client.post("/api/teacher/lessons", json={
        "courseId": "c1", "title": "L", "contentType": "link", "url": url, "status": "published"})


class TestLegacyLessonLinks:
    def test_dangerous_schemes_are_rejected_on_create(self, fake_db):
        t = _teacher(fake_db)
        for bad in ("javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,<script>1</script>",
                    "//evil.example/x", "vbscript:x", "file:///etc/passwd", "ftp://example.com/a",
                    "https://user:pw@example.com/a", "https://example.com/a b"):
            r = _create(t, bad)
            assert r.status_code == 400, bad
        assert fake_db.dump("lessons") == {}

    def test_web_links_are_still_accepted_and_stored_as_typed(self, fake_db):
        t = _teacher(fake_db)
        for good in ("https://youtu.be/dQw4w9WgXcQ", "http://older-site.example/lesson"):
            assert _create(t, good).status_code == 201, good
        assert sorted(l["url"] for l in fake_db.dump("lessons").values()) == \
            ["http://older-site.example/lesson", "https://youtu.be/dQw4w9WgXcQ"]

    def test_dangerous_url_is_rejected_on_update(self, fake_db):
        t = _teacher(fake_db)
        lid = _create(t, "https://youtu.be/dQw4w9WgXcQ").get_json()["lessonId"]
        r = t.patch(f"/api/teacher/lessons/{lid}", json={"url": "javascript:alert(1)"})
        assert r.status_code == 400
        assert fake_db.dump("lessons")[lid]["url"] == "https://youtu.be/dQw4w9WgXcQ"

    def test_student_never_receives_a_stored_dangerous_url(self, fake_db):
        # Bad data can exist already (older builds, direct Firestore writes).
        s = _student(fake_db)
        fake_db.seed("courses", "c1", {"title": "C", "className": "7", "teacherUid": "t1", "status": "published",
                                       "subject": "Math", "learningModes": ["Regular"]})
        fake_db.seed("lessons", "bad", {"courseId": "c1", "teacherUid": "t1", "title": "Bad", "status": "published",
                                        "contentType": "link", "url": "javascript:alert(document.domain)", "order": 1})
        fake_db.seed("lessons", "ok", {"courseId": "c1", "teacherUid": "t1", "title": "Ok", "status": "published",
                                       "contentType": "link", "url": "https://example.com/notes", "order": 2})
        r = s.get("/api/student/courses/c1/lessons")
        assert r.status_code == 200, r.get_json()
        urls = {l["title"]: l["url"] for l in r.get_json()["lessons"]}
        assert urls == {"Bad": "", "Ok": "https://example.com/notes"}

    def test_student_js_only_puts_https_links_in_an_href(self, fake_db):
        js = (ROOT / "static" / "student.js").read_text(encoding="utf-8")
        assert "function safeWebUrl" in js
        assert 'href="${escapeAttr(l.url)}"' not in js
        assert 'href="${escapeAttr(nextLesson.url)}"' not in js
