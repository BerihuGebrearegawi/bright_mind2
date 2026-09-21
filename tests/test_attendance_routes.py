"""Behavioural tests for attendance_routes.py: teacher grade-ownership
authorization, the mandatory Regular-vs-Distance student separation, and
the student-facing history endpoint (including its Distance-mode branch).
"""
from attendance_routes import register_attendance_routes
from tests.conftest import make_app


def require_user_as(uid, email="user@example.com"):
    return lambda: (True, {"uid": uid, "email": email})


def _client(require_user, firebase_configured=True):
    factory = (lambda: True) if firebase_configured else (lambda: None)
    app = make_app(register_attendance_routes, require_user, factory)
    return app.test_client()


def _seed_teacher_owning_grade(fake_db, uid, grade):
    fake_db.seed("teachers", uid, {"approved": True})
    fake_db.seed("courses", f"course-{uid}-{grade}", {"teacherUid": uid, "className": grade})


def _seed_student(fake_db, uid, grade, mode=None, name="Student"):
    data = {"role": "student", "className": grade, "displayName": name}
    if mode is not None:
        data["learningMode"] = mode
    fake_db.seed("users", uid, data)


class TestTeacherMarkAttendance:
    def test_non_teacher_is_rejected(self, fake_db):
        client = _client(require_user_as("t1"))
        r = client.post("/api/teacher/attendance", json={
            "className": "7", "date": "2026-09-10", "records": [{"studentUid": "s1", "status": "present"}],
        })
        assert r.status_code == 403

    def test_teacher_cannot_mark_a_grade_they_dont_teach(self, fake_db):
        _seed_teacher_owning_grade(fake_db, "t1", "8")
        _seed_student(fake_db, "s1", "9")
        client = _client(require_user_as("t1"))
        r = client.post("/api/teacher/attendance", json={
            "className": "9", "date": "2026-09-10", "records": [{"studentUid": "s1", "status": "present"}],
        })
        assert r.status_code == 403

    def test_invalid_date_format_is_rejected(self, fake_db):
        _seed_teacher_owning_grade(fake_db, "t1", "7")
        client = _client(require_user_as("t1"))
        r = client.post("/api/teacher/attendance", json={
            "className": "7", "date": "10-09-2026", "records": [{"studentUid": "s1", "status": "present"}],
        })
        assert r.status_code == 400

    def test_future_date_is_rejected(self, fake_db):
        _seed_teacher_owning_grade(fake_db, "t1", "7")
        client = _client(require_user_as("t1"))
        r = client.post("/api/teacher/attendance", json={
            "className": "7", "date": "2099-01-01", "records": [{"studentUid": "s1", "status": "present"}],
        })
        assert r.status_code == 400

    def test_marks_regular_student_successfully(self, fake_db):
        _seed_teacher_owning_grade(fake_db, "t1", "7")
        _seed_student(fake_db, "s1", "7", mode="Regular", name="Abebe")
        client = _client(require_user_as("t1"))
        r = client.post("/api/teacher/attendance", json={
            "className": "7", "date": "2026-09-10", "subject": "Mathematics",
            "records": [{"studentUid": "s1", "status": "present"}],
        })
        assert r.status_code == 200
        body = r.get_json()
        assert body["markedCount"] == 1
        assert body["skipped"] == []
        stored = fake_db.dump("attendanceRecords")
        assert stored["7_Mathematics_2026-09-10_s1"]["status"] == "present"
        assert stored["7_Mathematics_2026-09-10_s1"]["teacherUid"] == "t1"

    def test_student_with_missing_learning_mode_defaults_to_regular(self, fake_db):
        """No learningMode field at all must behave like 'Regular', matching
        the default used everywhere else in the codebase - not silently
        excluded from classroom attendance."""
        _seed_teacher_owning_grade(fake_db, "t1", "7")
        _seed_student(fake_db, "s1", "7", mode=None, name="Abebe")
        client = _client(require_user_as("t1"))
        r = client.post("/api/teacher/attendance", json={
            "className": "7", "date": "2026-09-10",
            "records": [{"studentUid": "s1", "status": "present"}],
        })
        assert r.status_code == 200
        assert r.get_json()["markedCount"] == 1

    def test_distance_student_is_skipped_not_written(self, fake_db):
        """The core separation rule: even if a Distance student's UID is
        submitted, no attendanceRecords document must be created for them."""
        _seed_teacher_owning_grade(fake_db, "t1", "7")
        _seed_student(fake_db, "s1", "7", mode="Distance", name="Distance Kid")
        client = _client(require_user_as("t1"))
        r = client.post("/api/teacher/attendance", json={
            "className": "7", "date": "2026-09-10",
            "records": [{"studentUid": "s1", "status": "present"}],
        })
        assert r.status_code == 200
        body = r.get_json()
        assert body["markedCount"] == 0
        assert len(body["skipped"]) == 1
        assert body["skipped"][0]["studentUid"] == "s1"
        assert "Distance" in body["skipped"][0]["reason"]
        assert fake_db.dump("attendanceRecords") == {}

    def test_student_in_wrong_grade_is_skipped(self, fake_db):
        _seed_teacher_owning_grade(fake_db, "t1", "7")
        _seed_student(fake_db, "s1", "8", mode="Regular")
        client = _client(require_user_as("t1"))
        r = client.post("/api/teacher/attendance", json={
            "className": "7", "date": "2026-09-10",
            "records": [{"studentUid": "s1", "status": "present"}],
        })
        assert r.get_json()["markedCount"] == 0
        assert fake_db.dump("attendanceRecords") == {}

    def test_invalid_status_is_skipped(self, fake_db):
        _seed_teacher_owning_grade(fake_db, "t1", "7")
        _seed_student(fake_db, "s1", "7", mode="Regular")
        client = _client(require_user_as("t1"))
        r = client.post("/api/teacher/attendance", json={
            "className": "7", "date": "2026-09-10",
            "records": [{"studentUid": "s1", "status": "excused"}],
        })
        assert r.get_json()["markedCount"] == 0

    def test_resubmitting_same_date_updates_not_duplicates(self, fake_db):
        _seed_teacher_owning_grade(fake_db, "t1", "7")
        _seed_student(fake_db, "s1", "7", mode="Regular")
        client = _client(require_user_as("t1"))
        client.post("/api/teacher/attendance", json={
            "className": "7", "date": "2026-09-10", "records": [{"studentUid": "s1", "status": "absent"}],
        })
        client.post("/api/teacher/attendance", json={
            "className": "7", "date": "2026-09-10", "records": [{"studentUid": "s1", "status": "present"}],
        })
        stored = fake_db.dump("attendanceRecords")
        assert len(stored) == 1
        assert list(stored.values())[0]["status"] == "present"


class TestTeacherViewAndReport:
    def test_view_shows_full_roster_with_unmarked_defaults(self, fake_db):
        _seed_teacher_owning_grade(fake_db, "t1", "7")
        _seed_student(fake_db, "s1", "7", mode="Regular", name="Abebe")
        _seed_student(fake_db, "s2", "7", mode="Distance", name="Distance Kid")
        client = _client(require_user_as("t1"))
        r = client.get("/api/teacher/attendance?className=7&date=2026-09-10")
        assert r.status_code == 200
        names = {row["name"] for row in r.get_json()["students"]}
        assert "Abebe" in names
        assert "Distance Kid" not in names  # roster is Regular-only

    def test_report_aggregates_counts_and_rate(self, fake_db):
        _seed_teacher_owning_grade(fake_db, "t1", "7")
        _seed_student(fake_db, "s1", "7", mode="Regular", name="Abebe")
        client = _client(require_user_as("t1"))
        for date, status in [("2026-09-01", "present"), ("2026-09-02", "present"), ("2026-09-03", "absent")]:
            client.post("/api/teacher/attendance", json={
                "className": "7", "date": date, "records": [{"studentUid": "s1", "status": status}],
            })
        r = client.get("/api/teacher/attendance/report?className=7&from=2026-09-01&to=2026-09-03")
        assert r.status_code == 200
        row = r.get_json()["students"][0]
        assert row["present"] == 2
        assert row["absent"] == 1
        assert row["attendanceRate"] == 66.7


class TestStudentAttendanceHistory:
    def test_regular_student_sees_own_records_only(self, fake_db):
        _seed_student(fake_db, "s1", "7", mode="Regular")
        fake_db.seed("attendanceRecords", "r1", {"studentUid": "s1", "date": "2026-09-10", "status": "present", "grade": "7", "subject": ""})
        fake_db.seed("attendanceRecords", "r2", {"studentUid": "other", "date": "2026-09-10", "status": "absent", "grade": "7", "subject": ""})
        client = _client(require_user_as("s1"))
        r = client.get("/api/student/attendance")
        assert r.status_code == 200
        body = r.get_json()
        assert body["mode"] == "Regular"
        assert len(body["records"]) == 1
        assert body["records"][0]["date"] == "2026-09-10"
        assert body["summary"]["present"] == 1

    def test_distance_student_gets_empty_records_and_a_note(self, fake_db):
        """Classroom attendance must never appear to apply to Distance
        students, even if a stray record somehow existed for them."""
        _seed_student(fake_db, "s1", "7", mode="Distance")
        fake_db.seed("attendanceRecords", "r1", {"studentUid": "s1", "date": "2026-09-10", "status": "present", "grade": "7", "subject": ""})
        client = _client(require_user_as("s1"))
        r = client.get("/api/student/attendance")
        assert r.status_code == 200
        body = r.get_json()
        assert body["mode"] == "Distance"
        assert body["records"] == []
        assert body["summary"] is None
        assert "note" in body


def require_user_denied(status=401):
    return lambda: (False, ({"error": "Sign in required."}, status))


class TestSecurity:
    """The six checks the manual verification pass asked for explicitly."""

    def test_unauthenticated_request_is_rejected_on_every_endpoint(self, fake_db):
        client = _client(require_user_denied())
        assert client.post("/api/teacher/attendance", json={}).status_code == 401
        assert client.get("/api/teacher/attendance?className=7&date=2026-09-10").status_code == 401
        assert client.get("/api/teacher/attendance/report?className=7").status_code == 401
        assert client.get("/api/student/attendance").status_code == 401

    def test_unapproved_teacher_record_is_rejected(self, fake_db):
        """A teachers/{uid} doc that exists but isn't approved must be
        treated the same as no teacher record at all."""
        fake_db.seed("teachers", "t1", {"approved": False, "status": "pending"})
        _seed_student(fake_db, "s1", "7", mode="Regular")
        client = _client(require_user_as("t1"))
        r = client.post("/api/teacher/attendance", json={
            "className": "7", "date": "2026-09-10", "records": [{"studentUid": "s1", "status": "present"}],
        })
        assert r.status_code == 403

    def test_student_cannot_read_another_students_attendance(self, fake_db):
        _seed_student(fake_db, "victim", "7", mode="Regular")
        fake_db.seed("attendanceRecords", "r1", {"studentUid": "victim", "date": "2026-09-10", "status": "present", "grade": "7", "subject": ""})
        _seed_student(fake_db, "attacker", "7", mode="Regular")
        client = _client(require_user_as("attacker"))
        r = client.get("/api/student/attendance")
        assert r.get_json()["records"] == []  # sees none of "victim"'s records


class TestSaveThenReloadThroughTheAPI:
    """Mirrors the manual check: mark attendance, then reload the teacher's
    view and the student's own history through the read endpoints (not just
    by inspecting the fake DB directly) to confirm the saved status is what
    both screens would actually display."""

    def test_teacher_view_reflects_a_saved_status_after_reload(self, fake_db):
        _seed_teacher_owning_grade(fake_db, "t1", "7")
        _seed_student(fake_db, "s1", "7", mode="Regular", name="Abebe")
        client = _client(require_user_as("t1"))
        client.post("/api/teacher/attendance", json={
            "className": "7", "date": "2026-09-10", "records": [{"studentUid": "s1", "status": "late"}],
        })
        # Simulate a page reload: a fresh GET, not a re-read of the POST response.
        reload = client.get("/api/teacher/attendance?className=7&date=2026-09-10")
        row = next(r for r in reload.get_json()["students"] if r["uid"] == "s1")
        assert row["status"] == "late"

    def test_student_history_reflects_a_saved_status_after_reload(self, fake_db):
        _seed_teacher_owning_grade(fake_db, "t1", "7")
        _seed_student(fake_db, "s1", "7", mode="Regular")
        teacher_client = _client(require_user_as("t1"))
        teacher_client.post("/api/teacher/attendance", json={
            "className": "7", "date": "2026-09-10", "records": [{"studentUid": "s1", "status": "absent"}],
        })
        student_client = _client(require_user_as("s1"))
        reload = student_client.get("/api/student/attendance")
        body = reload.get_json()
        assert body["records"][0]["status"] == "absent"
        assert body["summary"]["absent"] == 1
