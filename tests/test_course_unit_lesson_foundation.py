"""V31.108 Course -> Unit -> Lesson foundation.

Covers, against the real route modules (teacher_routes.py and
student_course_routes.py) running on the in-memory FakeFirestore:

  * course / unit / lesson access for students
  * teacher ownership / authorization on every teacher route
  * student grade isolation
  * Learning Mode / Target Audience targeting
  * teacher lesson management (edit, publish/unpublish, move, reorder)
  * progress retention and "continue learning"
  * backward compatibility with pre-Unit courses and lessons

Nothing here touches app.py; both modules are registered on throw-away Flask
apps exactly like tests/test_teacher_apply_courses_exams.py does.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from student_course_routes import register_student_course_routes
from teacher_routes import register_teacher_routes
from tests.conftest import make_app

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _firestore_array_union(monkeypatch):
    """The shared firebase_admin stub in conftest predates ArrayUnion, which
    student_complete_lesson uses when a course is finished."""
    import firebase_admin.firestore as fs
    if not hasattr(fs, "ArrayUnion"):
        monkeypatch.setattr(fs, "ArrayUnion", lambda values: list(values), raising=False)


# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------
def _as(uid):
    return lambda: (True, {"uid": uid, "email": f"{uid}@example.com"})


def _anonymous():
    return lambda: (False, ({"error": "Unauthorized"}, 401))


def teacher_client(db, uid="t1", approved=True):
    if approved:
        db.seed("teachers", uid, {"approved": True})
    app = make_app(register_teacher_routes, _as(uid), lambda: (True, {}), lambda: True)
    return app.test_client()


def anonymous_teacher_client():
    app = make_app(register_teacher_routes, _anonymous(), lambda: (True, {}), lambda: True)
    return app.test_client()


def student_client(db, uid="s1", grade="7", mode="Regular", paid=False, **user_extra):
    user = {"className": grade, "learningMode": mode}
    user.update(user_extra)
    db.seed("users", uid, user)
    if paid:
        db.seed("entitlements", uid, {"premium": True, "expiresAt": datetime.now(timezone.utc) + timedelta(days=30)})
    app = make_app(register_student_course_routes, _as(uid), lambda: True)
    return app.test_client()


def anonymous_student_client():
    app = make_app(register_student_course_routes, _anonymous(), lambda: True)
    return app.test_client()


def seed_course(db, cid="c1", teacher="t1", grade="7", status="published", modes=None, audiences=None, title="Course"):
    data = {"title": title, "className": grade, "teacherUid": teacher, "status": status, "subject": "Mathematics"}
    if modes is not None:
        data["learningModes"] = modes
    if audiences is not None:
        data["audiences"] = audiences
    db.seed("courses", cid, data)


def seed_unit(db, unit_id, course="c1", teacher="t1", title=None, order=0, status="published"):
    db.seed("units", unit_id, {"courseId": course, "teacherUid": teacher, "title": title or unit_id,
                               "description": "", "order": order, "status": status})


def seed_lesson(db, lesson_id, course="c1", unit="__legacy__", teacher="t1", title=None, order=0, status="published",
                content_type="youtube"):
    data = {"courseId": course, "teacherUid": teacher, "title": title or lesson_id, "contentType": content_type,
            "url": f"https://content.example/{lesson_id}", "description": "", "order": order, "status": status}
    if unit != "__legacy__":  # legacy lessons have no unitId key at all
        data["unitId"] = unit
    db.seed("lessons", lesson_id, data)


def open_course(client, cid="c1"):
    return client.get(f"/api/student/courses/{cid}/lessons")


def lesson_ids(resp):
    return [l["id"] for l in resp.get_json()["lessons"]]


# --------------------------------------------------------------------------
# course access + student grade isolation
# --------------------------------------------------------------------------
class TestCourseAccessAndGradeIsolation:
    def test_student_lists_only_own_grade_published_courses(self, fake_db):
        seed_course(fake_db, "g7", grade="7")
        seed_course(fake_db, "g8", grade="8")
        seed_course(fake_db, "g7-draft", grade="7", status="draft")
        r = student_client(fake_db, grade="7").get("/api/student/courses")
        assert r.status_code == 200
        assert [c["id"] for c in r.get_json()["courses"]] == ["g7"]

    def test_student_opens_own_grade_course(self, fake_db):
        seed_course(fake_db)
        seed_lesson(fake_db, "l1")
        r = open_course(student_client(fake_db, grade="7"))
        assert r.status_code == 200
        assert lesson_ids(r) == ["l1"]

    def test_student_cannot_open_another_grades_course_by_id(self, fake_db):
        # Regression: this route used to skip the grade check entirely.
        seed_course(fake_db, "g7", grade="7")
        seed_lesson(fake_db, "l1", course="g7")
        r = open_course(student_client(fake_db, grade="8"), "g7")
        assert r.status_code == 403
        assert "content.example" not in r.get_data(as_text=True)

    def test_student_cannot_complete_another_grades_lesson(self, fake_db):
        seed_course(fake_db, "g7", grade="7")
        seed_lesson(fake_db, "l1", course="g7")
        r = student_client(fake_db, grade="8").post("/api/student/lessons/l1/complete")
        assert r.status_code == 403
        assert fake_db.count("courseProgress") == 0

    def test_grade_isolation_is_symmetric(self, fake_db):
        seed_course(fake_db, "g7", grade="7")
        seed_course(fake_db, "g8", grade="8")
        seed_lesson(fake_db, "a", course="g7")
        seed_lesson(fake_db, "b", course="g8")
        c7 = student_client(fake_db, "s7", grade="7")
        c8 = student_client(fake_db, "s8", grade="8")
        assert open_course(c7, "g7").status_code == 200 and open_course(c7, "g8").status_code == 403
        assert open_course(c8, "g8").status_code == 200 and open_course(c8, "g7").status_code == 403

    def test_unpublished_course_cannot_be_opened_or_completed(self, fake_db):
        # Regression: opening used to ignore course status.
        seed_course(fake_db, status="draft")
        seed_lesson(fake_db, "l1")
        client = student_client(fake_db)
        assert open_course(client).status_code == 403
        assert client.post("/api/student/lessons/l1/complete").status_code == 403

    def test_unknown_course_is_404_and_unknown_lesson_is_404(self, fake_db):
        client = student_client(fake_db)
        assert open_course(client, "nope").status_code == 404
        assert client.post("/api/student/lessons/nope/complete").status_code == 404

    def test_anonymous_student_is_rejected(self, fake_db):
        seed_course(fake_db)
        seed_lesson(fake_db, "l1")
        client = anonymous_student_client()
        assert client.get("/api/student/courses").status_code == 401
        assert open_course(client).status_code == 401
        assert client.post("/api/student/lessons/l1/complete").status_code == 401

    def test_student_with_no_grade_on_file_keeps_list_behavior(self, fake_db):
        # Documented existing behavior, kept for backward compatibility: the
        # list route only filters by grade when the student HAS one, so the
        # open route now follows the list route rather than becoming stricter.
        seed_course(fake_db, "g7", grade="7")
        seed_lesson(fake_db, "l1", course="g7")
        client = student_client(fake_db, grade="")
        assert [c["id"] for c in client.get("/api/student/courses").get_json()["courses"]] == ["g7"]
        assert open_course(client, "g7").status_code == 200


# --------------------------------------------------------------------------
# Learning Mode / Target Audience targeting
# --------------------------------------------------------------------------
class TestLearningModeTargeting:
    def test_regular_student_cannot_reach_distance_only_course(self, fake_db):
        seed_course(fake_db, modes=["Distance"], audiences=["Distance"])
        seed_lesson(fake_db, "l1")
        client = student_client(fake_db, mode="Regular")
        assert client.get("/api/student/courses").get_json()["courses"] == []
        assert open_course(client).status_code == 403
        assert client.post("/api/student/lessons/l1/complete").status_code == 403

    def test_distance_student_reaches_distance_course(self, fake_db):
        seed_course(fake_db, modes=["Distance"], audiences=["Distance"])
        seed_lesson(fake_db, "l1")
        client = student_client(fake_db, mode="Distance")
        assert [c["id"] for c in client.get("/api/student/courses").get_json()["courses"]] == ["c1"]
        assert open_course(client).status_code == 200
        assert client.post("/api/student/lessons/l1/complete").status_code == 200

    def test_distance_student_cannot_reach_untargeted_regular_course(self, fake_db):
        # A course with no explicit targeting defaults to Regular only.
        seed_course(fake_db)
        seed_lesson(fake_db, "l1")
        client = student_client(fake_db, mode="Distance")
        assert client.get("/api/student/courses").get_json()["courses"] == []
        assert open_course(client).status_code == 403

    def test_paid_only_audience_blocks_free_student(self, fake_db):
        seed_course(fake_db, modes=["Regular"], audiences=["Paid Regular"])
        seed_lesson(fake_db, "l1")
        assert open_course(student_client(fake_db, "free", paid=False)).status_code == 403
        assert open_course(student_client(fake_db, "paid", paid=True)).status_code == 200

    def test_scholarship_audience_requires_scholarship_flag(self, fake_db):
        seed_course(fake_db, modes=["Regular"], audiences=["Scholarship"])
        seed_lesson(fake_db, "l1")
        assert open_course(student_client(fake_db, "plain")).status_code == 403
        assert open_course(student_client(fake_db, "sch", scholarshipEligible=True)).status_code == 200

    def test_lessons_and_units_inherit_the_courses_targeting(self, fake_db):
        seed_course(fake_db, modes=["Distance"], audiences=["Distance"])
        seed_unit(fake_db, "u1")
        seed_lesson(fake_db, "l1", unit="u1")
        regular = student_client(fake_db, "reg", mode="Regular")
        body = open_course(regular).get_data(as_text=True)
        assert "content.example" not in body and '"units"' not in body
        assert regular.post("/api/student/lessons/l1/complete").status_code == 403


# --------------------------------------------------------------------------
# unit access
# --------------------------------------------------------------------------
class TestUnitAccess:
    def test_published_units_are_returned_in_order_with_their_lessons(self, fake_db):
        seed_course(fake_db)
        seed_unit(fake_db, "u2", title="Second", order=2)
        seed_unit(fake_db, "u1", title="First", order=1)
        seed_lesson(fake_db, "a", unit="u1")
        seed_lesson(fake_db, "b", unit="u2")
        body = open_course(student_client(fake_db)).get_json()
        assert [u["id"] for u in body["units"]] == ["u1", "u2"]
        assert {l["id"]: l["unitId"] for l in body["lessons"]} == {"a": "u1", "b": "u2"}

    def test_draft_unit_and_its_lessons_are_hidden_from_students(self, fake_db):
        seed_course(fake_db)
        seed_unit(fake_db, "open")
        seed_unit(fake_db, "hidden", status="draft")
        seed_lesson(fake_db, "in-open", unit="open")
        seed_lesson(fake_db, "in-hidden", unit="hidden")
        resp = open_course(student_client(fake_db))
        body = resp.get_json()
        assert [u["id"] for u in body["units"]] == ["open"]
        assert lesson_ids(resp) == ["in-open"]
        assert "in-hidden" not in resp.get_data(as_text=True)
        assert "content.example/in-hidden" not in resp.get_data(as_text=True)

    def test_lesson_in_draft_unit_cannot_be_completed(self, fake_db):
        seed_course(fake_db)
        seed_unit(fake_db, "hidden", status="draft")
        seed_lesson(fake_db, "l1", unit="hidden")
        client = student_client(fake_db)
        assert client.post("/api/student/lessons/l1/complete").status_code == 403
        assert fake_db.count("courseProgress") == 0

    def test_lessons_in_draft_units_are_excluded_from_totals(self, fake_db):
        seed_course(fake_db)
        seed_unit(fake_db, "hidden", status="draft")
        seed_lesson(fake_db, "a")
        seed_lesson(fake_db, "b")
        seed_lesson(fake_db, "c", unit="hidden")
        client = student_client(fake_db)
        assert client.get("/api/student/courses").get_json()["courses"][0]["lessonCount"] == 2
        client.post("/api/student/lessons/a/complete")
        last = client.post("/api/student/lessons/b/complete").get_json()
        assert last["totalLessons"] == 2 and last["courseCompleted"] is True and last["percent"] == 100

    def test_publishing_a_draft_unit_reveals_its_lessons(self, fake_db):
        seed_course(fake_db)
        seed_unit(fake_db, "u1", status="draft")
        seed_lesson(fake_db, "l1", unit="u1")
        student = student_client(fake_db)
        assert lesson_ids(open_course(student)) == []
        teacher = teacher_client(fake_db)
        assert teacher.patch("/api/teacher/units/u1", json={"status": "published"}).status_code == 200
        assert lesson_ids(open_course(student)) == ["l1"]

    def test_lesson_pointing_at_a_missing_unit_stays_visible_and_ungrouped(self, fake_db):
        seed_course(fake_db)
        seed_lesson(fake_db, "l1", unit="deleted-unit")
        resp = open_course(student_client(fake_db))
        assert lesson_ids(resp) == ["l1"]
        assert resp.get_json()["nextLessonId"] == "l1"


# --------------------------------------------------------------------------
# lesson access
# --------------------------------------------------------------------------
class TestLessonAccess:
    def test_draft_lesson_is_hidden_and_not_completable(self, fake_db):
        seed_course(fake_db)
        seed_lesson(fake_db, "live")
        seed_lesson(fake_db, "draft", status="draft")
        client = student_client(fake_db)
        resp = open_course(client)
        assert lesson_ids(resp) == ["live"]
        assert "content.example/draft" not in resp.get_data(as_text=True)
        assert client.post("/api/student/lessons/draft/complete").status_code == 403

    def test_completing_marks_the_lesson_and_is_idempotent(self, fake_db):
        seed_course(fake_db)
        seed_lesson(fake_db, "a")
        seed_lesson(fake_db, "b")
        client = student_client(fake_db)
        first = client.post("/api/student/lessons/a/complete").get_json()
        again = client.post("/api/student/lessons/a/complete").get_json()
        assert first["completedCount"] == again["completedCount"] == 1
        assert first["percent"] == again["percent"] == 50
        assert first["courseCompleted"] is False


# --------------------------------------------------------------------------
# teacher ownership / authorization
# --------------------------------------------------------------------------
NEW_TEACHER_ROUTES = [
    ("get", "/api/teacher/courses/c1/lessons", None),
    ("patch", "/api/teacher/lessons/l1", {"title": "x"}),
    ("post", "/api/teacher/courses/c1/units/reorder", {"order": ["u1"]}),
    ("post", "/api/teacher/courses/c1/lessons/reorder", {"order": ["l1"]}),
]


class TestTeacherOwnership:
    @pytest.mark.parametrize("method,path,body", NEW_TEACHER_ROUTES)
    def test_anonymous_is_rejected(self, fake_db, method, path, body):
        r = getattr(anonymous_teacher_client(), method)(path, json=body) if body else getattr(anonymous_teacher_client(), method)(path)
        assert r.status_code == 401

    @pytest.mark.parametrize("method,path,body", NEW_TEACHER_ROUTES)
    def test_unapproved_teacher_is_rejected(self, fake_db, method, path, body):
        seed_course(fake_db)
        seed_unit(fake_db, "u1")
        seed_lesson(fake_db, "l1")
        client = teacher_client(fake_db, "intruder", approved=False)
        r = getattr(client, method)(path, json=body) if body else getattr(client, method)(path)
        assert r.status_code == 403

    def test_teacher_cannot_list_lessons_of_someone_elses_course(self, fake_db):
        seed_course(fake_db, teacher="t1")
        seed_lesson(fake_db, "l1", teacher="t1")
        r = teacher_client(fake_db, "t2").get("/api/teacher/courses/c1/lessons")
        assert r.status_code == 403
        assert "content.example" not in r.get_data(as_text=True)

    def test_teacher_cannot_edit_or_publish_someone_elses_lesson(self, fake_db):
        seed_course(fake_db, teacher="t1")
        seed_lesson(fake_db, "l1", teacher="t1", title="Original")
        r = teacher_client(fake_db, "t2").patch("/api/teacher/lessons/l1", json={"title": "Hijacked", "status": "draft"})
        assert r.status_code == 404
        stored = fake_db.dump("lessons")["l1"]
        assert stored["title"] == "Original" and stored["status"] == "published"

    def test_teacher_cannot_create_unit_in_or_reorder_someone_elses_course(self, fake_db):
        seed_course(fake_db, teacher="t1")
        seed_unit(fake_db, "u1", teacher="t1", order=5)
        seed_lesson(fake_db, "l1", teacher="t1", order=5)
        t2 = teacher_client(fake_db, "t2")
        assert t2.post("/api/teacher/courses/c1/units", json={"title": "Sneaky"}).status_code == 403
        assert t2.post("/api/teacher/courses/c1/units/reorder", json={"order": ["u1"]}).status_code == 403
        assert t2.post("/api/teacher/courses/c1/lessons/reorder", json={"order": ["l1"]}).status_code == 403
        assert fake_db.dump("units")["u1"]["order"] == 5 and fake_db.dump("lessons")["l1"]["order"] == 5

    def test_teacher_cannot_move_a_lesson_into_someone_elses_unit(self, fake_db):
        seed_course(fake_db, "mine", teacher="t1")
        seed_course(fake_db, "theirs", teacher="t2")
        seed_unit(fake_db, "their-unit", course="theirs", teacher="t2")
        seed_lesson(fake_db, "l1", course="mine", teacher="t1")
        r = teacher_client(fake_db, "t1").patch("/api/teacher/lessons/l1", json={"unitId": "their-unit"})
        assert r.status_code == 403
        assert fake_db.dump("lessons")["l1"].get("unitId") is None

    def test_teacher_cannot_move_a_lesson_into_a_unit_of_another_of_their_courses(self, fake_db):
        seed_course(fake_db, "a", teacher="t1")
        seed_course(fake_db, "b", teacher="t1")
        seed_unit(fake_db, "unit-in-b", course="b", teacher="t1")
        seed_lesson(fake_db, "l1", course="a", teacher="t1")
        r = teacher_client(fake_db, "t1").patch("/api/teacher/lessons/l1", json={"unitId": "unit-in-b"})
        assert r.status_code == 403

    def test_foreign_lesson_ids_in_a_reorder_are_rejected_and_nothing_is_written(self, fake_db):
        seed_course(fake_db, "mine", teacher="t1")
        seed_course(fake_db, "theirs", teacher="t2")
        seed_lesson(fake_db, "m1", course="mine", teacher="t1", order=7)
        seed_lesson(fake_db, "x1", course="theirs", teacher="t2", order=9)
        before = fake_db.dump("lessons")
        r = teacher_client(fake_db, "t1").post("/api/teacher/courses/mine/lessons/reorder", json={"order": ["m1", "x1"]})
        assert r.status_code == 409
        assert fake_db.dump("lessons") == before


# --------------------------------------------------------------------------
# teacher lesson management: list, edit, publish/unpublish, move, create-as-draft
# --------------------------------------------------------------------------
class TestLessonManagement:
    def test_teacher_lists_own_lessons_including_drafts_in_order(self, fake_db):
        seed_course(fake_db)
        seed_lesson(fake_db, "b", order=2)
        seed_lesson(fake_db, "a", order=1)
        seed_lesson(fake_db, "d", order=3, status="draft")
        r = teacher_client(fake_db).get("/api/teacher/courses/c1/lessons")
        assert r.status_code == 200
        assert [l["id"] for l in r.get_json()["lessons"]] == ["a", "b", "d"]

    def test_edit_lesson_fields(self, fake_db):
        seed_course(fake_db)
        seed_lesson(fake_db, "l1")
        r = teacher_client(fake_db).patch("/api/teacher/lessons/l1", json={
            "title": " New title ", "description": "Desc", "url": "https://new.example/v", "contentType": "PDF", "order": 4,
        })
        assert r.status_code == 200
        stored = fake_db.dump("lessons")["l1"]
        assert (stored["title"], stored["description"], stored["url"], stored["contentType"], stored["order"]) == \
            ("New title", "Desc", "https://new.example/v", "pdf", 4)
        assert stored["updatedAt"] is not None

    @pytest.mark.parametrize("body", [
        {}, {"title": "  "}, {"url": ""}, {"contentType": "mp4"}, {"status": "archived"}, {"order": "abc"},
    ])
    def test_edit_lesson_validation(self, fake_db, body):
        seed_course(fake_db)
        seed_lesson(fake_db, "l1")
        before = fake_db.dump("lessons")
        assert teacher_client(fake_db).patch("/api/teacher/lessons/l1", json=body).status_code == 400
        assert fake_db.dump("lessons") == before

    def test_unpublish_hides_from_students_and_republish_restores(self, fake_db):
        seed_course(fake_db)
        seed_lesson(fake_db, "l1")
        teacher, student = teacher_client(fake_db), student_client(fake_db)
        assert teacher.patch("/api/teacher/lessons/l1", json={"status": "draft"}).status_code == 200
        assert lesson_ids(open_course(student)) == []
        assert student.post("/api/student/lessons/l1/complete").status_code == 403
        assert teacher.patch("/api/teacher/lessons/l1", json={"status": "published"}).status_code == 200
        assert lesson_ids(open_course(student)) == ["l1"]

    def test_move_lesson_into_a_unit_and_back_out(self, fake_db):
        seed_course(fake_db)
        seed_unit(fake_db, "u1")
        seed_lesson(fake_db, "l1")
        teacher = teacher_client(fake_db)
        assert teacher.patch("/api/teacher/lessons/l1", json={"unitId": "u1"}).status_code == 200
        assert fake_db.dump("lessons")["l1"]["unitId"] == "u1"
        assert teacher.patch("/api/teacher/lessons/l1", json={"unitId": None}).status_code == 200
        assert fake_db.dump("lessons")["l1"]["unitId"] is None

    def test_create_lesson_as_draft_then_publish(self, fake_db):
        seed_course(fake_db)
        teacher, student = teacher_client(fake_db), student_client(fake_db)
        r = teacher.post("/api/teacher/lessons", json={"courseId": "c1", "title": "Prep", "url": "https://x", "contentType": "youtube", "status": "draft"})
        assert r.status_code == 201 and r.get_json()["status"] == "draft"
        lesson_id = r.get_json()["lessonId"]
        assert lesson_ids(open_course(student)) == []
        teacher.patch(f"/api/teacher/lessons/{lesson_id}", json={"status": "published"})
        assert lesson_ids(open_course(student)) == [lesson_id]

    def test_create_lesson_rejects_unknown_status(self, fake_db):
        seed_course(fake_db)
        r = teacher_client(fake_db).post("/api/teacher/lessons", json={"courseId": "c1", "title": "T", "url": "https://x", "contentType": "youtube", "status": "archived"})
        assert r.status_code == 400 and fake_db.count("lessons") == 0

    def test_teacher_can_still_create_a_unit_and_a_lesson_inside_it(self, fake_db):
        seed_course(fake_db)
        teacher = teacher_client(fake_db)
        unit = teacher.post("/api/teacher/courses/c1/units", json={"title": "Fractions"}).get_json()["unit"]["id"]
        r = teacher.post("/api/teacher/lessons", json={"courseId": "c1", "unitId": unit, "title": "Intro", "url": "https://x", "contentType": "youtube"})
        assert r.status_code == 201 and r.get_json()["unitId"] == unit


# --------------------------------------------------------------------------
# reorder
# --------------------------------------------------------------------------
class TestReorder:
    def test_units_with_tied_orders_are_renumbered_and_students_see_the_new_order(self, fake_db):
        seed_course(fake_db)
        for uid_ in ("ua", "ub", "uc"):
            seed_unit(fake_db, uid_, title=uid_, order=0)  # ties, like real legacy data
        teacher = teacher_client(fake_db)
        r = teacher.post("/api/teacher/courses/c1/units/reorder", json={"order": ["uc", "ua", "ub"]})
        assert r.status_code == 200
        assert [fake_db.dump("units")[u]["order"] for u in ("uc", "ua", "ub")] == [0, 1, 2]
        assert [u["id"] for u in open_course(student_client(fake_db)).get_json()["units"]] == ["uc", "ua", "ub"]

    def test_lessons_reorder_is_scoped_to_one_unit(self, fake_db):
        seed_course(fake_db)
        seed_unit(fake_db, "u1")
        seed_unit(fake_db, "u2")
        seed_lesson(fake_db, "a1", unit="u1", order=0)
        seed_lesson(fake_db, "a2", unit="u1", order=0)
        seed_lesson(fake_db, "b1", unit="u2", order=9)
        r = teacher_client(fake_db).post("/api/teacher/courses/c1/lessons/reorder", json={"order": ["a2", "a1"], "unitId": "u1"})
        assert r.status_code == 200
        stored = fake_db.dump("lessons")
        assert (stored["a2"]["order"], stored["a1"]["order"]) == (0, 1)
        assert stored["b1"]["order"] == 9  # other unit untouched

    def test_lessons_reorder_without_unit_targets_only_unitless_lessons(self, fake_db):
        seed_course(fake_db)
        seed_unit(fake_db, "u1")
        seed_lesson(fake_db, "loose1", order=0)
        seed_lesson(fake_db, "loose2", order=0, unit=None)
        seed_lesson(fake_db, "in-unit", unit="u1", order=5)
        teacher = teacher_client(fake_db)
        assert teacher.post("/api/teacher/courses/c1/lessons/reorder", json={"order": ["loose2", "loose1"]}).status_code == 200
        stored = fake_db.dump("lessons")
        assert (stored["loose2"]["order"], stored["loose1"]["order"]) == (0, 1)
        assert stored["in-unit"]["order"] == 5
        # a list that leaks a unit's lesson into the unitless scope is refused
        assert teacher.post("/api/teacher/courses/c1/lessons/reorder", json={"order": ["loose1", "loose2", "in-unit"]}).status_code == 409

    def test_reorder_includes_drafts_so_the_teachers_list_always_matches(self, fake_db):
        seed_course(fake_db)
        seed_lesson(fake_db, "pub", order=0)
        seed_lesson(fake_db, "dra", order=0, status="draft")
        assert teacher_client(fake_db).post("/api/teacher/courses/c1/lessons/reorder", json={"order": ["dra", "pub"]}).status_code == 200
        assert fake_db.dump("lessons")["dra"]["order"] == 0 and fake_db.dump("lessons")["pub"]["order"] == 1

    @pytest.mark.parametrize("body", [
        {}, {"order": []}, {"order": "u1"}, {"order": ["u1", "u1"]}, {"order": [1, 2]}, {"order": ["", "u1"]},
    ])
    def test_malformed_reorder_is_400_and_writes_nothing(self, fake_db, body):
        seed_course(fake_db)
        seed_unit(fake_db, "u1", order=3)
        seed_lesson(fake_db, "l1", order=3)
        teacher = teacher_client(fake_db)
        before = (fake_db.dump("units"), fake_db.dump("lessons"))
        assert teacher.post("/api/teacher/courses/c1/units/reorder", json=body).status_code == 400
        assert teacher.post("/api/teacher/courses/c1/lessons/reorder", json=body).status_code == 400
        assert (fake_db.dump("units"), fake_db.dump("lessons")) == before

    def test_stale_or_partial_lists_are_409_and_write_nothing(self, fake_db):
        seed_course(fake_db)
        seed_unit(fake_db, "u1", order=3)
        seed_unit(fake_db, "u2", order=4)
        teacher = teacher_client(fake_db)
        before = fake_db.dump("units")
        assert teacher.post("/api/teacher/courses/c1/units/reorder", json={"order": ["u1"]}).status_code == 409
        assert teacher.post("/api/teacher/courses/c1/units/reorder", json={"order": ["u1", "u2", "ghost"]}).status_code == 409
        assert fake_db.dump("units") == before

    def test_reorder_scoped_to_someone_elses_unit_is_403(self, fake_db):
        seed_course(fake_db, "mine", teacher="t1")
        seed_course(fake_db, "theirs", teacher="t2")
        seed_unit(fake_db, "their-unit", course="theirs", teacher="t2")
        r = teacher_client(fake_db, "t1").post("/api/teacher/courses/mine/lessons/reorder", json={"order": ["x"], "unitId": "their-unit"})
        assert r.status_code == 403


# --------------------------------------------------------------------------
# progress retention + continue learning
# --------------------------------------------------------------------------
class TestProgressAndContinueLearning:
    def test_progress_is_retained_across_reopening(self, fake_db):
        seed_course(fake_db)
        seed_lesson(fake_db, "a", order=1)
        seed_lesson(fake_db, "b", order=2)
        client = student_client(fake_db)
        client.post("/api/student/lessons/a/complete")
        again = client.get("/api/student/courses").get_json()["courses"][0]
        assert (again["completedCount"], again["lessonCount"], again["percent"]) == (1, 2, 50.0)
        assert {l["id"]: l["completed"] for l in open_course(client).get_json()["lessons"]} == {"a": True, "b": False}

    def test_next_lesson_follows_display_order_loose_first_then_units(self, fake_db):
        seed_course(fake_db)
        seed_unit(fake_db, "u1", order=1)
        seed_unit(fake_db, "u2", order=2)
        seed_lesson(fake_db, "loose", order=5)
        seed_lesson(fake_db, "u1-b", unit="u1", order=2)
        seed_lesson(fake_db, "u1-a", unit="u1", order=1)
        seed_lesson(fake_db, "u2-a", unit="u2", order=1)
        client = student_client(fake_db)
        seen = []
        for _ in range(4):
            nxt = open_course(client).get_json()["nextLessonId"]
            seen.append(nxt)
            client.post(f"/api/student/lessons/{nxt}/complete")
        assert seen == ["loose", "u1-a", "u1-b", "u2-a"]
        assert open_course(client).get_json()["nextLessonId"] is None

    def test_next_lesson_is_none_for_an_empty_course(self, fake_db):
        seed_course(fake_db)
        assert open_course(student_client(fake_db)).get_json()["nextLessonId"] is None

    def test_next_lesson_follows_a_teacher_reorder(self, fake_db):
        seed_course(fake_db)
        seed_lesson(fake_db, "a", order=0)
        seed_lesson(fake_db, "b", order=0)
        student = student_client(fake_db)
        teacher_client(fake_db).post("/api/teacher/courses/c1/lessons/reorder", json={"order": ["b", "a"]})
        assert open_course(student).get_json()["nextLessonId"] == "b"

    def test_unpublishing_a_completed_lesson_never_exceeds_100_and_republishing_restores_it(self, fake_db):
        seed_course(fake_db)
        for lid in ("a", "b", "c"):
            seed_lesson(fake_db, lid)
        student, teacher = student_client(fake_db), teacher_client(fake_db)
        for lid in ("a", "b", "c"):
            student.post(f"/api/student/lessons/{lid}/complete")
        teacher.patch("/api/teacher/lessons/c", json={"status": "draft"})
        listed = student.get("/api/student/courses").get_json()["courses"][0]
        assert (listed["completedCount"], listed["lessonCount"], listed["percent"]) == (2, 2, 100.0)
        # the stored completion is kept, so republishing restores it
        assert set(fake_db.dump("courseProgress")["s1_c1"]["completedLessonIds"]) == {"a", "b", "c"}
        teacher.patch("/api/teacher/lessons/c", json={"status": "published"})
        listed = student.get("/api/student/courses").get_json()["courses"][0]
        assert (listed["completedCount"], listed["lessonCount"], listed["percent"]) == (3, 3, 100.0)

    def test_completion_response_never_exceeds_100_after_an_unpublish(self, fake_db):
        seed_course(fake_db)
        for lid in ("a", "b", "c"):
            seed_lesson(fake_db, lid)
        student, teacher = student_client(fake_db), teacher_client(fake_db)
        for lid in ("a", "b"):
            student.post(f"/api/student/lessons/{lid}/complete")
        teacher.patch("/api/teacher/lessons/b", json={"status": "draft"})
        out = student.post("/api/student/lessons/c/complete").get_json()
        assert out["totalLessons"] == 2 and out["completedCount"] == 2 and out["percent"] == 100

    def test_certificate_is_issued_once_on_course_completion(self, fake_db):
        seed_course(fake_db)
        seed_lesson(fake_db, "a")
        seed_lesson(fake_db, "b")
        client = student_client(fake_db)
        assert client.post("/api/student/lessons/a/complete").get_json()["certificateIssued"] is False
        last = client.post("/api/student/lessons/b/complete").get_json()
        assert last["courseCompleted"] is True and last["certificateIssued"] is True
        assert client.post("/api/student/lessons/b/complete").get_json()["certificateIssued"] is False
        assert fake_db.count("courseCertificates") == 1
        assert len(client.get("/api/student/certificates").get_json()["certificates"]) == 1

    def test_progress_is_per_student(self, fake_db):
        seed_course(fake_db)
        seed_lesson(fake_db, "a")
        one, two = student_client(fake_db, "s1"), student_client(fake_db, "s2")
        one.post("/api/student/lessons/a/complete")
        assert two.get("/api/student/courses").get_json()["courses"][0]["completedCount"] == 0
        assert open_course(two).get_json()["nextLessonId"] == "a"


# --------------------------------------------------------------------------
# backward compatibility
# --------------------------------------------------------------------------
class TestBackwardCompatibility:
    def test_legacy_course_without_units_keeps_its_response_shape(self, fake_db):
        seed_course(fake_db)  # no units collection at all
        seed_lesson(fake_db, "b", order=2)  # legacy lessons: no unitId key
        seed_lesson(fake_db, "a", order=1)
        body = open_course(student_client(fake_db)).get_json()
        assert {"course", "units", "lessons"} <= set(body)  # every baseline key still present
        assert body["units"] == []
        assert [l["id"] for l in body["lessons"]] == ["a", "b"]
        assert set(body["lessons"][0]) == {"id", "title", "contentType", "url", "description", "order", "completed", "unitId"}
        assert body["lessons"][0]["unitId"] is None
        assert set(body["course"]) == {"id", "title", "subject", "className"}

    def test_legacy_progress_record_still_yields_the_same_numbers(self, fake_db):
        seed_course(fake_db)
        seed_lesson(fake_db, "a")
        seed_lesson(fake_db, "b")
        fake_db.seed("courseProgress", "s1_c1", {"studentUid": "s1", "courseId": "c1", "completedLessonIds": ["a"], "percent": 50.0})
        row = student_client(fake_db).get("/api/student/courses").get_json()["courses"][0]
        assert (row["lessonCount"], row["completedCount"], row["percent"]) == (2, 1, 50.0)

    def test_creating_a_lesson_with_the_old_payload_behaves_exactly_as_before(self, fake_db):
        seed_course(fake_db)
        r = teacher_client(fake_db).post("/api/teacher/lessons", json={
            "courseId": "c1", "title": "Intro", "url": "http://video", "contentType": "youtube",
        })
        assert r.status_code == 201
        body = r.get_json()
        assert body["success"] is True and body["unitId"] is None and body["lessonId"]
        stored = fake_db.dump("lessons")[body["lessonId"]]
        assert stored["status"] == "published" and stored["unitId"] is None and stored["teacherUid"] == "t1"

    def test_existing_unit_routes_are_unchanged(self, fake_db):
        seed_course(fake_db)
        teacher = teacher_client(fake_db)
        created = teacher.post("/api/teacher/courses/c1/units", json={"title": "U", "description": "d", "order": 3})
        assert created.status_code == 201 and created.get_json()["unit"]["status"] == "published"
        unit_id = created.get_json()["unit"]["id"]
        assert teacher.patch(f"/api/teacher/units/{unit_id}", json={"order": 1, "status": "draft"}).status_code == 200
        assert [u["id"] for u in teacher.get("/api/teacher/courses/c1/units").get_json()["units"]] == [unit_id]

    @pytest.mark.parametrize("content_type,valid", [
        ("youtube", True), ("telegram", True), ("pdf", True), ("drive", True), ("cloudinary", True), ("link", True),
        ("video", False), ("mp4", False), ("geogebra", False), ("", False),
    ])
    def test_create_and_edit_accept_exactly_the_same_content_types(self, fake_db, content_type, valid):
        seed_course(fake_db)
        seed_lesson(fake_db, "l1")
        teacher = teacher_client(fake_db)
        made = teacher.post("/api/teacher/lessons", json={"courseId": "c1", "title": "T", "url": "https://x", "contentType": content_type})
        edited = teacher.patch("/api/teacher/lessons/l1", json={"contentType": content_type})
        assert (made.status_code == 201) == valid
        assert (edited.status_code == 200) == valid

    def test_version_is_still_31_108(self):
        app_py = (ROOT / "app.py").read_text(encoding="utf-8")
        assert 'BMT_VERSION = os.getenv("BMT_VERSION", "31.108").strip() or "31.108"' in app_py


# --------------------------------------------------------------------------
# student UI wiring (static: the dashboards cannot be executed in pytest)
# --------------------------------------------------------------------------
class TestStudentUiWiring:
    def test_student_js_renders_units_and_continue_and_keeps_the_flat_fallback(self):
        js = (ROOT / "static" / "student.js").read_text(encoding="utf-8")
        assert "renderDistanceLessons(d.lessons||[], d.units||[], d.nextLessonId||null)" in js
        assert "function renderDistanceLessons(lessons, units, nextLessonId)" in js
        assert "Continue learning" in js
        assert "No lessons published in this course yet." in js  # flat/empty fallback kept
        assert 'target="_blank" rel="noopener noreferrer">Open</a>' in js  # existing Open link kept
