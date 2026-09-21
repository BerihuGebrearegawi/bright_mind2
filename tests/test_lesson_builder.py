"""V31.108 Interactive Lesson Builder.

Runs the real lesson_blocks.py and lesson_builder_routes.py (plus the existing
teacher_routes.py / student_course_routes.py for backward compatibility) on
the in-memory FakeFirestore. Nothing here needs the network, Firebase or app.py.

Written with plain asserts and only the `fake_db` fixture so it runs under
pytest and under the project's minimal runner alike.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import lesson_blocks as lb
from lesson_builder_routes import register_lesson_builder_routes
from student_course_routes import register_student_course_routes
from teacher_routes import register_teacher_routes
from tests.conftest import make_app

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------
def _as(uid):
    return lambda: (True, {"uid": uid, "email": f"{uid}@example.com"})


def _anonymous():
    return lambda: (False, ({"error": "Unauthorized"}, 401))


def builder_client(uid, auth=None):
    app = make_app(register_lesson_builder_routes, auth or _as(uid), lambda: True)
    return app.test_client()


def teacher_client(db, uid="t1", approved=True):
    if approved:
        db.seed("teachers", uid, {"approved": True})
    return builder_client(uid)


def student_client(db, uid="s1", grade="7", mode="Regular", paid=False, **extra):
    user = {"className": grade, "learningMode": mode}
    user.update(extra)
    db.seed("users", uid, user)
    if paid:
        db.seed("entitlements", uid, {"premium": True, "expiresAt": datetime.now(timezone.utc) + timedelta(days=30)})
    return builder_client(uid)


def seed_course(db, cid="c1", teacher="t1", grade="7", status="published", modes=None, audiences=None):
    data = {"title": "Course", "className": grade, "teacherUid": teacher, "status": status, "subject": "Mathematics"}
    if modes is not None:
        data["learningModes"] = modes
    if audiences is not None:
        data["audiences"] = audiences
    db.seed("courses", cid, data)


def quiz_block(**kw):
    q = {"prompt": "2 + 2 = ?", "options": {"A": "3", "B": "4", "C": "5"}, "answer": "B", "explanation": "Two plus two is four."}
    block = {"type": "quiz", "content": {"title": "Check", "maxAttempts": kw.get("attempts", 2), "questions": [q]}}
    return block


def mixed_blocks():
    return [
        {"type": "text", "content": {"text": "Welcome"}},
        {"type": "formula", "content": {"latex": "x^2 + y^2 = r^2"}},
        {"type": "image", "content": {"url": "https://cdn.example.com/a.png", "alt": "A"}},
        {"type": "video", "content": {"url": "https://youtu.be/dQw4w9WgXcQ"}},
        {"type": "pdf", "content": {"url": "https://files.example.com/notes.pdf", "title": "Notes"}},
        {"type": "geogebra", "content": {"materialId": "abcd1234"}},
        {"type": "simulation", "content": {"url": "https://phet.colorado.edu/sims/html/projectile-motion/latest/projectile-motion_all.html", "title": "Projectiles"}},
        {"type": "practice", "content": {"questions": [{"prompt": "1+1?", "options": {"A": "1", "B": "2"}, "answer": "B"}]}},
        quiz_block(),
    ]


def create_lesson(client, course="c1", blocks=None, status="published", **extra):
    body = {"title": "Lesson", "status": status, "blocks": blocks if blocks is not None else [{"type": "text", "content": {"text": "hi"}}]}
    body.update(extra)
    r = client.post(f"/api/teacher/courses/{course}/block-lessons", json=body)
    assert r.status_code == 201, r.get_json()
    return r.get_json()["lessonId"]


def block_ids(db, lesson_id):
    return [b["id"] for b in db.dump("lessons")[lesson_id]["blocks"]]


def raises_block_error(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except lb.BlockValidationError as exc:
        return exc
    raise AssertionError("expected BlockValidationError")


# --------------------------------------------------------------------------
# block validation
# --------------------------------------------------------------------------
class TestBlockValidation:
    def test_all_ten_block_types_validate(self, fake_db):
        blocks, keys = lb.validate_blocks(mixed_blocks() + [{"type": "assignment", "content": {"assignmentId": "a1"}}])
        assert [b["type"] for b in blocks] == ["text", "formula", "image", "video", "pdf", "geogebra", "simulation",
                                               "practice", "quiz", "assignment"]
        assert set(lb.block_type_names()) >= {b["type"] for b in blocks}
        assert len(keys) == 2  # practice + quiz only

    def test_every_block_has_type_order_id_and_content(self, fake_db):
        blocks, _ = lb.validate_blocks(mixed_blocks())
        for i, b in enumerate(blocks):
            assert set(b) == {"id", "type", "order", "content"}
            assert b["order"] == i and b["id"] and isinstance(b["content"], dict)

    def test_unknown_type_and_bad_shapes_rejected(self, fake_db):
        assert raises_block_error(lb.validate_blocks, [{"type": "script", "content": {}}]).index == 0
        raises_block_error(lb.validate_blocks, ["text"])
        raises_block_error(lb.validate_blocks, {"type": "text"})
        raises_block_error(lb.validate_blocks, [{"type": "text", "content": "hello"}])
        raises_block_error(lb.validate_blocks, [{"type": "text"}])

    def test_required_fields_enforced(self, fake_db):
        for block in ({"type": "text", "content": {"text": "   "}}, {"type": "formula", "content": {}},
                      {"type": "image", "content": {}}, {"type": "video", "content": {}},
                      {"type": "pdf", "content": {"url": "https://a.example.com/x.pdf"}},  # title required
                      {"type": "geogebra", "content": {}}, {"type": "simulation", "content": {"url": "https://a.example.com/s"}},
                      {"type": "assignment", "content": {}}, {"type": "practice", "content": {"questions": []}},
                      {"type": "quiz", "content": {}}):
            raises_block_error(lb.validate_blocks, [block])

    def test_error_names_the_offending_block(self, fake_db):
        err = raises_block_error(lb.validate_blocks, [{"type": "text", "content": {"text": "ok"}}, {"type": "formula", "content": {}}])
        assert err.index == 1 and err.field == "latex"

    def test_block_count_limit(self, fake_db):
        many = [{"type": "text", "content": {"text": "x"}}] * (lb.MAX_BLOCKS + 1)
        raises_block_error(lb.validate_blocks, many)
        assert len(lb.validate_blocks(many[:lb.MAX_BLOCKS])[0]) == lb.MAX_BLOCKS

    def test_duplicate_block_ids_rejected_and_valid_ids_kept(self, fake_db):
        raises_block_error(lb.validate_blocks, [{"id": "same", "type": "text", "content": {"text": "a"}},
                                                {"id": "same", "type": "text", "content": {"text": "b"}}])
        blocks, _ = lb.validate_blocks([{"id": "keep_me", "type": "text", "content": {"text": "a"}}])
        assert blocks[0]["id"] == "keep_me"
        blocks, _ = lb.validate_blocks([{"id": "bad id!<x>", "type": "text", "content": {"text": "a"}}])
        assert blocks[0]["id"].startswith("b_")

    def test_question_rules(self, fake_db):
        def q(**kw):
            base = {"prompt": "Q?", "options": {"A": "x", "B": "y"}, "answer": "A"}
            base.update(kw)
            return {"type": "practice", "content": {"questions": [base]}}
        lb.validate_blocks([q()])
        raises_block_error(lb.validate_blocks, [q(answer="Z")])
        raises_block_error(lb.validate_blocks, [q(options={"A": "only"})])
        raises_block_error(lb.validate_blocks, [q(prompt="  ")])
        raises_block_error(lb.validate_blocks, [q(type="essay")])
        raises_block_error(lb.validate_blocks, [q(points=0)])
        blocks, keys = lb.validate_blocks([q(type="true_false", answer="false", options=None)])
        qs = blocks[0]["content"]["questions"][0]
        assert qs["options"] == {"A": "True", "B": "False"}
        assert list(keys.values())[0]["answers"][qs["id"]] == "B"

    def test_registry_is_extensible(self, fake_db):
        class DividerBlock(lb.BlockType):
            name, label = "divider", "Divider"

            def clean(self, content):
                return {"style": "line" if (content or {}).get("style") != "dots" else "dots"}, None

            def render(self, public):
                return {"style": public.get("style", "line")}
        lb.register_block_type(DividerBlock())
        try:
            blocks, _ = lb.validate_blocks([{"type": "divider", "content": {"style": "dots"}}])
            assert lb.render_blocks(blocks)[0]["render"] == {"style": "dots"}
            assert any(t["type"] == "divider" for t in lb.block_type_catalog())
        finally:
            lb._REGISTRY.pop("divider", None)
        assert lb.get_block_type("divider") is None

    def test_type_catalog_route_needs_teacher(self, fake_db):
        r = teacher_client(fake_db).get("/api/teacher/lesson-blocks/types")
        assert r.status_code == 200 and len(r.get_json()["types"]) == 10
        assert student_client(fake_db, "plain").get("/api/teacher/lesson-blocks/types").status_code == 403


# --------------------------------------------------------------------------
# ordering
# --------------------------------------------------------------------------
class TestBlockOrdering:
    def test_order_follows_list_position_and_is_renumbered(self, fake_db):
        blocks, _ = lb.validate_blocks([{"order": 9, "type": "text", "content": {"text": "first"}},
                                        {"order": 1, "type": "text", "content": {"text": "second"}}])
        assert [b["order"] for b in blocks] == [0, 1]
        assert [b["content"]["text"] for b in blocks] == ["first", "second"]

    def test_render_uses_stored_order_and_tolerates_bad_data(self, fake_db):
        stored = [{"id": "b", "type": "text", "order": 5, "content": {"text": "B"}},
                  {"id": "a", "type": "text", "order": 2, "content": {"text": "A"}},
                  {"id": "n", "type": "text", "content": {"text": "no order"}}, "junk", None]
        rendered = lb.render_blocks(stored)
        # b(order 5), a(order 2), n(no order -> falls back to its list position 2; ties keep list order)
        assert [r["id"] for r in rendered] == ["a", "n", "b"]
        assert [r["order"] for r in rendered] == [0, 1, 2]
        assert lb.normalize_block_order("nope") == []

    def test_reorder_route(self, fake_db):
        t = teacher_client(fake_db); seed_course(fake_db)
        lid = create_lesson(t, blocks=[{"type": "text", "content": {"text": s}} for s in "abc"])
        ids = block_ids(fake_db, lid)
        r = t.post(f"/api/teacher/lessons/{lid}/blocks/reorder", json={"order": [ids[2], ids[0], ids[1]]})
        assert r.status_code == 200
        stored = fake_db.dump("lessons")[lid]["blocks"]
        assert [b["content"]["text"] for b in stored] == ["c", "a", "b"] and [b["order"] for b in stored] == [0, 1, 2]

    def test_reorder_rejects_stale_or_invalid_lists(self, fake_db):
        t = teacher_client(fake_db); seed_course(fake_db)
        lid = create_lesson(t, blocks=[{"type": "text", "content": {"text": s}} for s in "ab"])
        ids = block_ids(fake_db, lid)
        assert t.post(f"/api/teacher/lessons/{lid}/blocks/reorder", json={"order": [ids[0]]}).status_code == 409
        assert t.post(f"/api/teacher/lessons/{lid}/blocks/reorder", json={"order": [ids[0], ids[0]]}).status_code == 409
        assert t.post(f"/api/teacher/lessons/{lid}/blocks/reorder", json={"order": [ids[0], "ghost"]}).status_code == 409
        assert t.post(f"/api/teacher/lessons/{lid}/blocks/reorder", json={"order": []}).status_code == 400
        assert t.post(f"/api/teacher/lessons/{lid}/blocks/reorder", json={}).status_code == 400

    def test_student_sees_blocks_in_saved_order(self, fake_db):
        t = teacher_client(fake_db); seed_course(fake_db)
        lid = create_lesson(t, blocks=[{"type": "text", "content": {"text": s}} for s in ("one", "two", "three")])
        ids = block_ids(fake_db, lid)
        t.post(f"/api/teacher/lessons/{lid}/blocks/reorder", json={"order": [ids[1], ids[2], ids[0]]})
        r = student_client(fake_db).get(f"/api/student/lessons/{lid}")
        assert [b["render"]["text"] for b in r.get_json()["blocks"]] == ["two", "three", "one"]

    def test_put_replaces_blocks_and_order(self, fake_db):
        t = teacher_client(fake_db); seed_course(fake_db)
        lid = create_lesson(t)
        r = t.put(f"/api/teacher/lessons/{lid}/blocks", json={"blocks": [
            {"type": "text", "content": {"text": "B"}}, {"type": "text", "content": {"text": "A"}}]})
        assert r.status_code == 200 and r.get_json()["blockCount"] == 2
        assert fake_db.dump("lessons")[lid]["blockCount"] == 2


# --------------------------------------------------------------------------
# teacher authorization
# --------------------------------------------------------------------------
class TestTeacherAuthorization:
    def test_anonymous_is_rejected_everywhere(self, fake_db):
        anon = builder_client("x", _anonymous())
        for method, path in (("get", "/api/teacher/lesson-blocks/types"), ("post", "/api/teacher/courses/c1/block-lessons"),
                             ("get", "/api/teacher/lessons/l1/blocks"), ("put", "/api/teacher/lessons/l1/blocks"),
                             ("post", "/api/teacher/lessons/l1/blocks/reorder"), ("get", "/api/student/lessons/l1"),
                             ("post", "/api/student/lessons/l1/blocks/b1/answers")):
            assert getattr(anon, method)(path, json={}).status_code == 401

    def test_unapproved_teacher_and_student_cannot_author(self, fake_db):
        seed_course(fake_db)
        fake_db.seed("teachers", "pending", {"approved": False, "status": "pending"})
        for client in (builder_client("pending"), student_client(fake_db, "stu")):
            assert client.post("/api/teacher/courses/c1/block-lessons", json={"title": "x", "blocks": []}).status_code == 403
        assert fake_db.count("lessons") == 0

    def test_teacher_cannot_create_in_another_teachers_course(self, fake_db):
        seed_course(fake_db, teacher="t1")
        other = teacher_client(fake_db, "t2")
        r = other.post("/api/teacher/courses/c1/block-lessons", json={"title": "x", "blocks": []})
        assert r.status_code == 403 and fake_db.count("lessons") == 0

    def test_teacher_cannot_read_or_change_another_teachers_lesson(self, fake_db):
        seed_course(fake_db)
        lid = create_lesson(teacher_client(fake_db, "t1"), blocks=[quiz_block()])
        other = teacher_client(fake_db, "t2")
        assert other.get(f"/api/teacher/lessons/{lid}/blocks").status_code == 404
        assert other.put(f"/api/teacher/lessons/{lid}/blocks", json={"blocks": []}).status_code == 404
        assert other.post(f"/api/teacher/lessons/{lid}/blocks/reorder", json={"order": ["x"]}).status_code == 404
        assert len(fake_db.dump("lessons")[lid]["blocks"]) == 1

    def test_lesson_in_course_no_longer_owned_is_refused(self, fake_db):
        seed_course(fake_db, teacher="t1")
        t = teacher_client(fake_db, "t1")
        lid = create_lesson(t)
        fake_db.seed("courses", "c1", dict(fake_db.dump("courses")["c1"], teacherUid="someone_else"))
        assert t.get(f"/api/teacher/lessons/{lid}/blocks").status_code == 403

    def test_owner_gets_answer_keys_back_for_editing(self, fake_db):
        seed_course(fake_db)
        t = teacher_client(fake_db)
        lid = create_lesson(t, blocks=[quiz_block()])
        q = t.get(f"/api/teacher/lessons/{lid}/blocks").get_json()["blocks"][0]["content"]["questions"][0]
        assert q["answer"] == "B" and q["explanation"] == "Two plus two is four."

    def test_assignment_block_must_reference_own_assignment_for_course_grade(self, fake_db):
        seed_course(fake_db, grade="7")
        t = teacher_client(fake_db)
        fake_db.seed("assignments", "mine", {"teacherUid": "t1", "className": "7", "status": "published", "title": "HW"})
        fake_db.seed("assignments", "theirs", {"teacherUid": "t2", "className": "7", "status": "published", "title": "HW"})
        fake_db.seed("assignments", "wrong_grade", {"teacherUid": "t1", "className": "8", "status": "published", "title": "HW"})
        fake_db.seed("assignments", "other_course", {"teacherUid": "t1", "className": "7", "courseId": "zzz", "status": "published"})

        def attempt(aid):
            return t.post("/api/teacher/courses/c1/block-lessons",
                          json={"title": "L", "blocks": [{"type": "assignment", "content": {"assignmentId": aid}}]})
        assert attempt("mine").status_code == 201
        for bad in ("theirs", "wrong_grade", "other_course", "missing"):
            r = attempt(bad)
            assert r.status_code == 403 and r.get_json()["blockIndex"] == 0
        assert fake_db.count("lessons") == 1

    def test_unit_must_belong_to_the_course(self, fake_db):
        seed_course(fake_db); seed_course(fake_db, "c2")
        fake_db.seed("units", "u2", {"courseId": "c2", "teacherUid": "t1", "status": "published"})
        r = teacher_client(fake_db).post("/api/teacher/courses/c1/block-lessons", json={"title": "L", "unitId": "u2", "blocks": []})
        assert r.status_code == 403

    def test_bad_input_leaves_nothing_written(self, fake_db):
        seed_course(fake_db)
        t = teacher_client(fake_db)
        r = t.post("/api/teacher/courses/c1/block-lessons", json={"title": "L", "blocks": [{"type": "geogebra", "content": {"materialId": "<script>"}}]})
        assert r.status_code == 400 and r.get_json()["blockIndex"] == 0
        assert fake_db.count("lessons") == 0 and fake_db.count("lessonBlockKeys") == 0
        assert t.post("/api/teacher/courses/c1/block-lessons", json={"title": "  ", "blocks": []}).status_code == 400
        assert t.post("/api/teacher/courses/c1/block-lessons", json={"title": "L", "status": "archived"}).status_code == 400


# --------------------------------------------------------------------------
# student access
# --------------------------------------------------------------------------
class TestStudentAccess:
    def _lesson(self, db, **course_kw):
        seed_course(db, **course_kw)
        return create_lesson(teacher_client(db), blocks=mixed_blocks()[:2])

    def test_student_reads_own_grade_published_lesson(self, fake_db):
        lid = self._lesson(fake_db)
        r = student_client(fake_db, grade="7").get(f"/api/student/lessons/{lid}")
        body = r.get_json()
        assert r.status_code == 200 and [b["type"] for b in body["blocks"]] == ["text", "formula"]
        assert body["lesson"]["id"] == lid and body["course"]["className"] == "7" and body["lesson"]["completed"] is False

    def test_other_grade_gets_403_and_no_content(self, fake_db):
        lid = self._lesson(fake_db, grade="7")
        r = student_client(fake_db, grade="8").get(f"/api/student/lessons/{lid}")
        assert r.status_code == 403 and "Welcome" not in r.get_data(as_text=True)

    def test_student_without_a_grade_gets_nothing(self, fake_db):
        lid = self._lesson(fake_db)
        fake_db.seed("users", "nog", {"learningMode": "Regular"})
        assert builder_client("nog").get(f"/api/student/lessons/{lid}").status_code == 403

    def test_draft_lesson_unpublished_course_and_unit_are_hidden(self, fake_db):
        seed_course(fake_db); t = teacher_client(fake_db)
        draft = create_lesson(t, status="draft")
        s = student_client(fake_db)
        assert s.get(f"/api/student/lessons/{draft}").status_code == 403
        fake_db.seed("units", "u1", {"courseId": "c1", "teacherUid": "t1", "status": "draft"})
        in_draft_unit = create_lesson(t, unitId="u1")
        assert s.get(f"/api/student/lessons/{in_draft_unit}").status_code == 403
        fake_db.seed("courses", "c1", dict(fake_db.dump("courses")["c1"], status="draft"))
        fake_db.seed("lessons", "direct", {"courseId": "c1", "teacherUid": "t1", "status": "published", "blocks": []})
        assert s.get("/api/student/lessons/direct").status_code == 403

    def test_learning_mode_and_audience_are_enforced_via_course(self, fake_db):
        lid = self._lesson(fake_db, modes=["Distance"], audiences=["Distance"])
        assert student_client(fake_db, "reg", mode="Regular").get(f"/api/student/lessons/{lid}").status_code == 403
        assert student_client(fake_db, "dist", mode="Distance").get(f"/api/student/lessons/{lid}").status_code == 200

    def test_paid_audience_requires_active_entitlement(self, fake_db):
        lid = self._lesson(fake_db, modes=["Regular"], audiences=["Paid Regular"])
        assert student_client(fake_db, "free").get(f"/api/student/lessons/{lid}").status_code == 403
        assert student_client(fake_db, "paid", paid=True).get(f"/api/student/lessons/{lid}").status_code == 200

    def test_missing_and_malformed_ids(self, fake_db):
        s = student_client(fake_db)
        assert s.get("/api/student/lessons/nope").status_code == 404
        assert s.get("/api/student/lessons/bad%20id").status_code == 404

    def test_student_cannot_call_teacher_routes_or_change_content(self, fake_db):
        lid = self._lesson(fake_db)
        s = student_client(fake_db)
        for r in (s.put(f"/api/teacher/lessons/{lid}/blocks", json={"blocks": []}),
                  s.get(f"/api/teacher/lessons/{lid}/blocks"),
                  s.post("/api/teacher/courses/c1/block-lessons", json={"title": "x"})):
            assert r.status_code in (403, 404)
        assert len(fake_db.dump("lessons")[lid]["blocks"]) == 2
        assert s.put(f"/api/student/lessons/{lid}", json={"blocks": []}).status_code == 405

    def test_assignment_block_shows_only_visible_assignments(self, fake_db):
        seed_course(fake_db)
        fake_db.seed("assignments", "a_ok", {"teacherUid": "t1", "className": "7", "status": "published", "title": "Homework 1", "description": "Do it", "dueAt": "2099-01-01"})
        fake_db.seed("assignments", "a_dist", {"teacherUid": "t1", "className": "7", "status": "published", "title": "Distance HW", "learningModes": ["Distance"], "audiences": ["Distance"]})
        t = teacher_client(fake_db)
        lid = create_lesson(t, blocks=[{"type": "assignment", "content": {"assignmentId": "a_ok"}},
                                       {"type": "assignment", "content": {"assignmentId": "a_dist"}}])
        fake_db.seed("assignmentSubmissions", "sub1", {"assignmentId": "a_ok", "studentUid": "s1", "status": "submitted"})
        blocks = student_client(fake_db).get(f"/api/student/lessons/{lid}").get_json()["blocks"]
        assert blocks[0]["render"]["available"] is True and blocks[0]["render"]["submissionStatus"] == "submitted"
        assert blocks[1]["render"] == {"available": False, "note": ""}


# --------------------------------------------------------------------------
# answer keys + grading
# --------------------------------------------------------------------------
class TestAnswerKeys:
    def _setup(self, db, attempts=2):
        seed_course(db)
        lid = create_lesson(teacher_client(db), blocks=[quiz_block(attempts=attempts),
                                                        {"type": "practice", "content": {"questions": [
                                                            {"prompt": "1+1?", "options": {"A": "1", "B": "2"}, "answer": "B", "explanation": "Add."}]}}])
        quiz_id, prac_id = block_ids(db, lid)
        return lid, quiz_id, prac_id

    def test_keys_never_stored_in_the_lesson_document(self, fake_db):
        lid, quiz_id, _ = self._setup(fake_db)
        raw = json.dumps(fake_db.dump("lessons")[lid], default=str)
        for needle in ('"answer"', "explanation", "correctAnswer", "Two plus two is four"):
            assert needle not in raw
        keys = fake_db.dump("lessonBlockKeys")[lid]["blocks"][quiz_id]
        assert list(keys["answers"].values()) == ["B"]

    def test_student_payload_has_no_keys(self, fake_db):
        lid, *_ = self._setup(fake_db)
        raw = student_client(fake_db).get(f"/api/student/lessons/{lid}").get_data(as_text=True)
        for needle in ('"answer"', "explanation", "correctAnswer", "Two plus two is four", "Add."):
            assert needle not in raw

    def test_key_written_into_stored_block_directly_still_never_reaches_student(self, fake_db):
        lid, quiz_id, _ = self._setup(fake_db)
        lesson = fake_db.dump("lessons")[lid]
        blocks = [dict(b) for b in lesson["blocks"]]
        blocks[0]["content"] = dict(blocks[0]["content"])
        blocks[0]["content"]["questions"] = [dict(q, answer="B", correctAnswer="B", explanation="LEAK") for q in blocks[0]["content"]["questions"]]
        fake_db.seed("lessons", lid, dict(lesson, blocks=blocks))
        raw = student_client(fake_db).get(f"/api/student/lessons/{lid}").get_data(as_text=True)
        assert "LEAK" not in raw and '"answer"' not in raw and "correctAnswer" not in raw

    def _qid(self, db, lid):
        return db.dump("lessons")[lid]["blocks"][0]["content"]["questions"][0]["id"]

    def test_quiz_grading_attempt_limit_and_delayed_reveal(self, fake_db):
        lid, quiz_id, _ = self._setup(fake_db, attempts=2)
        qid = self._qid(fake_db, lid); s = student_client(fake_db)
        url = f"/api/student/lessons/{lid}/blocks/{quiz_id}/answers"
        first = s.post(url, json={"answers": {qid: "A"}}).get_json()
        assert first["score"] == 0 and first["attemptsLeft"] == 1 and first["answersRevealed"] is False
        assert "correctAnswer" not in first["results"][0] and "explanation" not in first["results"][0]
        second = s.post(url, json={"answers": {qid: "B"}}).get_json()
        assert second["percentage"] == 100.0 and second["attemptsLeft"] == 0 and second["answersRevealed"] is True
        assert second["results"][0]["correctAnswer"] == "B" and second["bestPercentage"] == 100.0
        assert s.post(url, json={"answers": {qid: "B"}}).status_code == 409
        stored = fake_db.dump("lessonBlockResults")["s1_%s_%s" % (lid, quiz_id)]
        assert stored["attempts"] == 2 and stored["studentUid"] == "s1"

    def test_attempt_is_counted_inside_a_transaction(self, fake_db):
        """The counter read, the limit check and the write must all go through
        one Firestore transaction (the fake has no real contention, so this
        checks the wiring: the read and the write both use the transaction)."""
        lid, quiz_id, _ = self._setup(fake_db, attempts=2)
        qid = self._qid(fake_db, lid)
        seen = {"tx": 0, "get_with_tx": 0, "set_with_tx": 0}
        real_transaction = fake_db.transaction

        def spy_transaction():
            tx = real_transaction()
            seen["tx"] += 1
            real_set = tx.set
            tx.set = lambda ref, data, merge=False: (seen.__setitem__("set_with_tx", seen["set_with_tx"] + 1), real_set(ref, data, merge=merge))[1]
            return tx
        fake_db.transaction = spy_transaction
        import tests.fakes as fakes
        orig_get = fakes.FakeDocRef.get

        def spy_get(self, transaction=None):
            if transaction is not None and self._collection_name == "lessonBlockResults":
                seen["get_with_tx"] += 1
            return orig_get(self, transaction=transaction)
        fakes.FakeDocRef.get = spy_get
        try:
            r = student_client(fake_db).post(f"/api/student/lessons/{lid}/blocks/{quiz_id}/answers", json={"answers": {qid: "B"}})
        finally:
            fakes.FakeDocRef.get = orig_get
        assert r.status_code == 200
        # V31.108 Lesson Builder/Progress: a quiz attempt also writes a
        # quizEvidence entry to courseProgress inside this same transaction
        # (assessment evidence only - see TestQuizEvidenceOnCourseProgress),
        # so a quiz submission now performs 2 transactional sets, not 1.
        assert seen == {"tx": 1, "get_with_tx": 1, "set_with_tx": 2}

    def test_no_attempt_is_written_when_the_limit_is_reached(self, fake_db):
        lid, quiz_id, _ = self._setup(fake_db, attempts=1)
        qid = self._qid(fake_db, lid); s = student_client(fake_db)
        url = f"/api/student/lessons/{lid}/blocks/{quiz_id}/answers"
        assert s.post(url, json={"answers": {qid: "A"}}).status_code == 200
        assert s.post(url, json={"answers": {qid: "B"}}).status_code == 409
        stored = fake_db.dump("lessonBlockResults")["s1_%s_%s" % (lid, quiz_id)]
        assert stored["attempts"] == 1 and stored["bestPercentage"] == 0.0

    def test_practice_reveals_key_and_is_unlimited(self, fake_db):
        lid, _quiz, prac_id = self._setup(fake_db)
        pq = fake_db.dump("lessons")[lid]["blocks"][1]["content"]["questions"][0]["id"]
        s = student_client(fake_db)
        for _ in range(5):
            r = s.post(f"/api/student/lessons/{lid}/blocks/{prac_id}/answers", json={"answers": {pq: "A"}})
            body = r.get_json()
            assert r.status_code == 200 and body["results"][0]["correctAnswer"] == "B" and body["results"][0]["explanation"] == "Add."

    def test_grading_ignores_junk_and_unknown_questions(self, fake_db):
        lid, quiz_id, _ = self._setup(fake_db)
        s = student_client(fake_db)
        r = s.post(f"/api/student/lessons/{lid}/blocks/{quiz_id}/answers", json={"answers": {"ghost": "B", "x": {"a": 1}}})
        assert r.status_code == 200 and r.get_json()["score"] == 0
        assert s.post(f"/api/student/lessons/{lid}/blocks/{quiz_id}/answers", json={"answers": "B"}).status_code == 400
        assert s.post(f"/api/student/lessons/{lid}/blocks/nope/answers", json={"answers": {}}).status_code == 404

    def test_wrong_grade_student_cannot_submit_and_no_result_written(self, fake_db):
        lid, quiz_id, _ = self._setup(fake_db)
        qid = self._qid(fake_db, lid)
        r = student_client(fake_db, "s8", grade="8").post(f"/api/student/lessons/{lid}/blocks/{quiz_id}/answers", json={"answers": {qid: "B"}})
        assert r.status_code == 403 and fake_db.count("lessonBlockResults") == 0

    def test_editing_keeps_keys_in_sync(self, fake_db):
        lid, quiz_id, _ = self._setup(fake_db)
        t = teacher_client(fake_db)
        content = t.get(f"/api/teacher/lessons/{lid}/blocks").get_json()["blocks"]
        content[0]["content"]["questions"][0]["answer"] = "C"
        assert t.put(f"/api/teacher/lessons/{lid}/blocks", json={"blocks": content}).status_code == 200
        qid = self._qid(fake_db, lid)
        assert fake_db.dump("lessonBlockKeys")[lid]["blocks"][quiz_id]["answers"][qid] == "C"
        content = [b for b in content if b["type"] != "quiz"]
        t.put(f"/api/teacher/lessons/{lid}/blocks", json={"blocks": content})
        assert quiz_id not in fake_db.dump("lessonBlockKeys")[lid]["blocks"]

    def test_pure_grading_rules(self, fake_db):
        render = {"questions": [{"id": "q1", "options": {"A": "x", "B": "y"}, "points": 2}, {"id": "q2", "options": {"A": "x", "B": "y"}, "points": 1}]}
        key = {"answers": {"q1": "A", "q2": "B"}, "explanations": {"q1": "why"}}
        out = lb.grade_answers(render, key, {"q1": "a", "q2": "A"}, reveal=False)
        assert (out["score"], out["totalPoints"], out["correctCount"]) == (2, 3, 1) and out["percentage"] == 66.7
        assert "correctAnswer" not in out["results"][0]
        assert lb.grade_answers(render, key, None, reveal=True)["score"] == 0


# --------------------------------------------------------------------------
# XSS / unsafe input
# --------------------------------------------------------------------------
BAD_URLS = ["javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html;base64,PHNjcmlwdD4=", "vbscript:x", "file:///etc/passwd",
            "http://example.com/a.png", "//example.com/a.png", "ftp://example.com/a.png", "https://user:pw@example.com/a.png",
            "https://example.com:8443/a.png", "https://localhost/a.png", "https://127.0.0.1/a.png", "https://10.0.0.5/a.png",
            "https://[::1]/a.png", "https://2130706433/a.png", "https://printer.local/a.png", "https://intranet/a.png",
            "https://example.com/a.png\"onerror=\"alert(1)", "https://example.com/<script>.png", "https://example.com/%3Cscript%3E.png",
            "https://example.com/a b.png", "https://exa\u200bmple.com/a.png", "", "   ", None, 123, ["https://a.example.com/x.png"]]


class TestUnsafeInput:
    def test_text_html_is_stripped(self, fake_db):
        cases = {"<script>alert(1)</script>Hello": "Hello", "Hi <b>there</b>": "Hi there",
                 "<img src=x onerror=alert(1)>ok": "ok", "<iframe src='//evil'></iframe>safe": "safe",
                 "<style>*{display:none}</style>visible": "visible", "a<!-- hidden -->b": "ab",
                 "x < 5 and y > 3": "x < 5 and y > 3",
                 "<svg onload=alert(1)>": "", "line1\r\nline2\x00\x07": "line1\nline2", "a\u202eb": "ab"}
        for raw, expected in cases.items():
            assert lb.sanitize_text(raw) == expected, raw
        # re-assembled / nested markup can never leave a complete script tag behind
        for nested in ("<scr<script>ipt>alert(1)</script>", "<scr<script></script>ipt>alert(1)</script>",
                       "<<script>script>alert(1)<</script>/script>"):
            cleaned = lb.sanitize_text(nested)
            assert "<script" not in cleaned.lower() and "</script" not in cleaned.lower() and ">" not in cleaned, nested
        assert lb.sanitize_text(None) == "" and lb.sanitize_text(["<b>"]) == ""
        assert len(lb.sanitize_text("x" * 50_000, limit=100)) == 100

    def test_maths_style_comparisons_survive_but_attribute_bearing_tags_do_not(self, fake_db):
        keep = ["if a<b and c>d then", "x<y>z", "1 < 2 and 3 > 2", "a<b, c>d", "x <= 5 && y >= 2"]
        for text in keep:
            assert lb.sanitize_text(text) == text, text
        drop = {"<x onerror=alert(1)>go": "go", "<a href=\"javascript:1\">l</a>": "l", "<img/src=x onerror=alert(1)>": "",
                "<p style='x:y'>t</p>": "t", "<BR/>z": "z", "<b>bold</b>": "bold"}
        for raw, expected in drop.items():
            assert lb.sanitize_text(raw) == expected, raw

    def test_stripped_text_that_becomes_empty_is_rejected(self, fake_db):
        raises_block_error(lb.validate_blocks, [{"type": "text", "content": {"text": "<script>alert(1)</script>"}}])

    def test_every_free_text_field_is_sanitised(self, fake_db):
        evil = "<script>alert(1)</script>Safe"
        blocks, _ = lb.validate_blocks([
            {"type": "image", "content": {"url": "https://cdn.example.com/a.png", "alt": evil, "caption": evil}},
            {"type": "video", "content": {"url": "https://youtu.be/dQw4w9WgXcQ", "title": evil}},
            {"type": "pdf", "content": {"url": "https://a.example.com/x.pdf", "title": evil, "notes": evil}},
            {"type": "simulation", "content": {"url": "https://a.example.com/s", "title": evil, "description": evil}},
            {"type": "quiz", "content": {"title": evil, "questions": [{"prompt": evil, "options": {"A": evil, "B": "b"}, "answer": "A", "explanation": evil}]}},
        ])
        assert "<" not in json.dumps(blocks) and "alert" not in json.dumps(blocks)

    def test_bad_urls_rejected_by_every_url_block(self, fake_db):
        for url in BAD_URLS:
            for block in ({"type": "image", "content": {"url": url}}, {"type": "video", "content": {"url": url}},
                          {"type": "pdf", "content": {"url": url, "title": "t"}},
                          {"type": "simulation", "content": {"url": url, "title": "t"}}):
                err = raises_block_error(lb.validate_blocks, [block])
                assert err.index == 0, (url, block["type"])

    def test_html_and_macros_rejected_in_formulas(self, fake_db):
        for latex in ("<script>alert(1)</script>", "x<img src=x onerror=alert(1)>", "\\href{javascript:alert(1)}{x}",
                      "\\url{https://evil.example}", "\\includegraphics{a.png}", "\\htmlClass{x}{y}", "\\def\\a{b}", "cost $5$"):
            raises_block_error(lb.validate_blocks, [{"type": "formula", "content": {"latex": latex}}])
        blocks, _ = lb.validate_blocks([{"type": "formula", "content": {"latex": "a < b \\text{ and } c > d \\$5"}}])
        assert blocks[0]["content"]["latex"].startswith("a < b")

    def test_lesson_title_and_description_are_sanitised(self, fake_db):
        seed_course(fake_db)
        lid = create_lesson(teacher_client(fake_db), title="<b>Fractions</b><script>x</script>", description="<img src=x onerror=1>Intro")
        stored = fake_db.dump("lessons")[lid]
        assert stored["title"] == "Fractions" and stored["description"] == "Intro"

    def test_tampered_stored_blocks_are_dropped_on_student_read(self, fake_db):
        seed_course(fake_db)
        fake_db.seed("lessons", "evil", {"courseId": "c1", "teacherUid": "t1", "status": "published", "title": "x", "blocks": [
            {"id": "b1", "type": "video", "order": 0, "content": {"url": "javascript:alert(1)"}},
            {"id": "b2", "type": "image", "order": 1, "content": {"url": "https://example.com/a.png\" onerror=\"alert(1)"}},
            {"id": "b3", "type": "geogebra", "order": 2, "content": {"materialId": "<script>alert(1)</script>"}},
            {"id": "b4", "type": "iframe", "order": 3, "content": {"src": "https://evil.example"}},
            {"id": "b5", "type": "text", "order": 4, "content": {"text": "<script>alert(1)</script>Kept"}},
        ]})
        r = student_client(fake_db).get("/api/student/lessons/evil")
        body = r.get_json()
        assert r.status_code == 200 and [b["id"] for b in body["blocks"]] == ["b5"]
        assert body["blocks"][0]["render"]["text"] == "Kept"
        assert "javascript:" not in r.get_data(as_text=True) and "<script" not in r.get_data(as_text=True)

    def test_responses_are_json_never_html(self, fake_db):
        seed_course(fake_db)
        lid = create_lesson(teacher_client(fake_db), blocks=[{"type": "text", "content": {"text": "x < y"}}])
        r = student_client(fake_db).get(f"/api/student/lessons/{lid}")
        assert r.mimetype == "application/json"

    def test_legacy_resource_link_is_validated(self, fake_db):
        seed_course(fake_db)
        fake_db.seed("lessons", "leg_bad", {"courseId": "c1", "teacherUid": "t1", "status": "published", "title": "x",
                                             "contentType": "link", "url": "javascript:alert(1)"})
        assert student_client(fake_db).get("/api/student/lessons/leg_bad").get_json()["legacyResource"] is None


# --------------------------------------------------------------------------
# GeoGebra
# --------------------------------------------------------------------------
class TestGeoGebra:
    def test_ids_and_links_reduce_to_a_material_id(self, fake_db):
        for raw in ("abcd1234", " abcd1234 ", "https://www.geogebra.org/m/abcd1234", "https://geogebra.org/classic/abcd1234",
                    "https://www.geogebra.org/graphing/abcd1234?lang=en", "https://www.geogebra.org/material/iframe/id/abcd1234/width/800",
                    "https://www.geogebra.org/material/show/id/abcd1234"):
            assert lb.parse_geogebra(raw) == "abcd1234", raw

    def test_invalid_references_rejected(self, fake_db):
        for raw in ("", "ab", "a" * 40, "abc def", "abc-1234", "<script>alert(1)</script>", "javascript:alert(1)",
                    "https://evil.example/m/abcd1234", "https://www.geogebra.org.evil.example/m/abcd1234",
                    "https://www.geogebra.org/", "https://www.geogebra.org/m/", "http://www.geogebra.org/m/abcd1234", None, 5, {"id": "abcd1234"}):
            raises_block_error(lb.parse_geogebra, raw)

    def test_only_the_reference_is_stored_never_code(self, fake_db):
        blocks, _ = lb.validate_blocks([{"type": "geogebra", "content": {
            "materialId": "abcd1234", "ggbBase64": "UEsDBBQ=", "html": "<script>alert(1)</script>", "onload": "x()", "appletParameters": {"a": 1},
            "height": "600"}}])
        assert blocks[0]["content"] == {"materialId": "abcd1234", "title": "", "height": 600}

    def test_render_uses_fixed_geogebra_host(self, fake_db):
        r = lb.render_blocks(lb.validate_blocks([{"type": "geogebra", "content": {"materialId": "abcd1234"}}])[0])[0]["render"]
        assert r["embedUrl"] == "https://www.geogebra.org/material/iframe/id/abcd1234" and r["openUrl"] == "https://www.geogebra.org/m/abcd1234"
        assert r["height"] == 480

    def test_height_is_bounded(self, fake_db):
        raises_block_error(lb.validate_blocks, [{"type": "geogebra", "content": {"materialId": "abcd1234", "height": 5000}}])
        raises_block_error(lb.validate_blocks, [{"type": "geogebra", "content": {"materialId": "abcd1234", "height": "tall"}}])


# --------------------------------------------------------------------------
# external simulations, video, image / Canva
# --------------------------------------------------------------------------
class TestExternalUrls:
    PHET = "https://phet.colorado.edu/sims/html/projectile-motion/latest/projectile-motion_all.html"

    def test_phet_sim_is_embeddable_only_on_the_fixed_host(self, fake_db):
        info = lb.parse_simulation(self.PHET)
        assert info == {"provider": "phet", "url": self.PHET, "embeddable": True}

    def test_phet_landing_page_and_other_hosts_are_link_only(self, fake_db):
        assert lb.parse_simulation("https://phet.colorado.edu/en/simulations/projectile-motion")["embeddable"] is False
        other = lb.parse_simulation("https://simulations.example.org/sims/thing.html")
        assert other["provider"] == "external" and other["embeddable"] is False
        assert lb.parse_simulation("https://phet.colorado.edu.evil.example/sims/x.html")["embeddable"] is False

    def test_simulation_render_makes_no_phet_io_claim(self, fake_db):
        blocks, _ = lb.validate_blocks([{"type": "simulation", "content": {"url": self.PHET, "title": "Projectiles"}}])
        r = lb.render_blocks(blocks)[0]["render"]
        assert "phetio" not in json.dumps(r).lower() and "phet-io" not in json.dumps(r).lower()

    def test_url_normalisation(self, fake_db):
        assert lb.validate_external_url("https://Example.COM/a?b=1#frag") == "https://example.com/a?b=1"
        assert lb.validate_external_url("https://example.com:443/x") == "https://example.com/x"
        assert lb.validate_external_url("https://example.com") == "https://example.com/"
        raises_block_error(lb.validate_external_url, "https://example.com/x", allowed_hosts=("other.com",))
        assert lb.validate_external_url("https://sub.other.com/x", allowed_hosts=("other.com",))
        raises_block_error(lb.validate_external_url, "https://" + "a" * 2100 + ".com/")

    def test_video_providers_map_to_fixed_embed_hosts(self, fake_db):
        assert lb.parse_video("https://www.youtube.com/watch?v=dQw4w9WgXcQ")["embedUrl"] == "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"
        assert lb.parse_video("https://youtu.be/dQw4w9WgXcQ?t=5")["videoId"] == "dQw4w9WgXcQ"
        assert lb.parse_video("https://vimeo.com/123456789")["embedUrl"] == "https://player.vimeo.com/video/123456789"
        d = lb.parse_video("https://drive.google.com/file/d/1AbCdEfGhIjKlMn/view?usp=sharing")
        assert d["embedUrl"] == "https://drive.google.com/file/d/1AbCdEfGhIjKlMn/preview"
        assert lb.parse_video("https://cdn.example.com/lesson.mp4")["provider"] == "direct"
        assert lb.parse_video("https://res.cloudinary.com/x/video/upload/v1/lesson")["provider"] == "direct"
        for bad in ("https://www.youtube.com/watch?v=short", "https://youtu.be/", "https://vimeo.com/notanumber",
                    "https://drive.google.com/drive/folders/abc", "https://evil.example/page.html",
                    "https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ"):
            raises_block_error(lb.parse_video, bad)

    def test_canva_is_embed_or_upload_only(self, fake_db):
        ok = lb.parse_image("https://www.canva.com/design/DAFabc12345/AbCdEf123456/view")
        assert ok == {"provider": "canva", "embedUrl": "https://www.canva.com/design/DAFabc12345/AbCdEf123456/view?embed"}
        for bad in ("https://www.canva.com/design/DAFabc12345/AbCdEf123456/edit", "https://www.canva.com/login",
                    "https://www.canva.com.evil.example/design/DAFabc12345/AbCdEf123456/view", "https://evil.example/page"):
            raises_block_error(lb.parse_image, bad)
        assert lb.parse_image("https://cdn.example.com/photo.JPG")["provider"] == "image"
        assert lb.parse_image("https://res.cloudinary.com/demo/image/upload/sample")["provider"] == "image"

    def test_iframe_hosts_match_what_the_parsers_can_emit(self, fake_db):
        emitted = {"www.youtube-nocookie.com", "player.vimeo.com", "drive.google.com", "www.geogebra.org", "phet.colorado.edu", "www.canva.com"}
        assert emitted == set(lb.IFRAME_HOSTS)
        front_end = (ROOT / "static" / "lesson_builder.js").read_text(encoding="utf-8")
        for host in lb.IFRAME_HOSTS:
            assert host in front_end


# --------------------------------------------------------------------------
# backward compatibility
# --------------------------------------------------------------------------
class TestBackwardCompatibility:
    def test_version_and_registration_are_intact(self, fake_db):
        app_py = (ROOT / "app.py").read_text(encoding="utf-8")
        assert 'BMT_VERSION = os.getenv("BMT_VERSION", "31.108").strip() or "31.108"' in app_py
        assert "register_lesson_builder_routes(app, _require_user_bearer, _firebase_admin_from_env)" in app_py
        for existing in ("register_student_course_routes(", "register_teacher_routes(", "register_learning_challenge_routes("):
            assert existing in app_py

    def test_existing_lessons_keep_their_exact_row_shape(self, fake_db):
        seed_course(fake_db)
        fake_db.seed("lessons", "old", {"courseId": "c1", "teacherUid": "t1", "title": "Old", "contentType": "youtube",
                                         "url": "https://content.example/old", "description": "d", "order": 0, "status": "published"})
        app = make_app(register_student_course_routes, _as("s1"), lambda: True)
        fake_db.seed("users", "s1", {"className": "7", "learningMode": "Regular"})
        row = app.test_client().get("/api/student/courses/c1/lessons").get_json()["lessons"][0]
        assert set(row) == {"id", "title", "contentType", "url", "description", "order", "completed", "unitId"}  # no hasBlocks key
        assert row["url"] == "https://content.example/old"

    def test_block_lessons_are_flagged_in_the_course_listing(self, fake_db):
        seed_course(fake_db)
        create_lesson(teacher_client(fake_db))
        fake_db.seed("users", "s1", {"className": "7", "learningMode": "Regular"})
        app = make_app(register_student_course_routes, _as("s1"), lambda: True)
        row = app.test_client().get("/api/student/courses/c1/lessons").get_json()["lessons"][0]
        assert row["hasBlocks"] is True and row["contentType"] == "blocks"

    def test_legacy_lesson_still_opens_through_the_new_reader(self, fake_db):
        seed_course(fake_db)
        fake_db.seed("lessons", "old", {"courseId": "c1", "teacherUid": "t1", "title": "Old", "contentType": "pdf",
                                         "url": "https://content.example/old.pdf", "status": "published"})
        body = student_client(fake_db).get("/api/student/lessons/old").get_json()
        assert body["blocks"] == [] and body["legacyResource"] == {"contentType": "pdf", "url": "https://content.example/old.pdf"}

    def test_existing_teacher_lesson_route_is_unchanged(self, fake_db):
        seed_course(fake_db)
        fake_db.seed("teachers", "t1", {"approved": True})
        app = make_app(register_teacher_routes, _as("t1"), lambda: (True, {}), lambda: True)
        c = app.test_client()
        r = c.post("/api/teacher/lessons", json={"courseId": "c1", "title": "Legacy", "contentType": "youtube", "url": "https://youtu.be/x"})
        assert r.status_code == 201 and r.get_json()["status"] == "published"
        stored = fake_db.dump("lessons")[r.get_json()["lessonId"]]
        assert "blocks" not in stored and stored["url"] == "https://youtu.be/x"
        assert c.post("/api/teacher/lessons", json={"courseId": "c1", "title": "x", "contentType": "blocks", "url": "https://a.example.com"}).status_code == 400

    def test_existing_lesson_management_works_on_block_lessons(self, fake_db):
        seed_course(fake_db)
        fake_db.seed("teachers", "t1", {"approved": True})
        lid = create_lesson(builder_client("t1"), status="draft")
        app = make_app(register_teacher_routes, _as("t1"), lambda: (True, {}), lambda: True)
        assert app.test_client().patch(f"/api/teacher/lessons/{lid}", json={"status": "published", "title": "Renamed"}).status_code == 200
        stored = fake_db.dump("lessons")[lid]
        assert stored["status"] == "published" and len(stored["blocks"]) == 1

    def test_progress_completion_still_works_for_block_lessons(self, fake_db):
        import firebase_admin.firestore as fs
        if not hasattr(fs, "ArrayUnion"):
            fs.ArrayUnion = lambda values: list(values)
        seed_course(fake_db)
        lid = create_lesson(teacher_client(fake_db))
        fake_db.seed("users", "s1", {"className": "7", "learningMode": "Regular"})
        app = make_app(register_student_course_routes, _as("s1"), lambda: True)
        r = app.test_client().post(f"/api/student/lessons/{lid}/complete")
        assert r.status_code == 200 and r.get_json()["courseCompleted"] is True
        assert student_client(fake_db).get(f"/api/student/lessons/{lid}").get_json()["lesson"]["completed"] is True

    def test_firestore_rules_lock_new_collections_and_block_writes(self, fake_db):
        rules = (ROOT / "firestore.rules").read_text(encoding="utf-8")
        assert "match /lessonBlockKeys/{lessonId} {\n      allow read, write: if false;" in rules
        assert "match /lessonBlockResults/{resultId} {\n      allow read, write: if false;" in rules
        assert "hasAny(['blocks', 'blockSchemaVersion', 'blockCount'])" in rules
        assert rules.count("match /{document=**}") == 1 and "allow read, write: if false;" in rules.split("match /{document=**}")[1]


# --------------------------------------------------------------------------
# front end wiring (static checks - no browser in CI)
# --------------------------------------------------------------------------
class TestFrontEndWiring:
    def _js(self):
        return (ROOT / "static" / "lesson_builder.js").read_text(encoding="utf-8")

    def test_builder_js_never_uses_html_injection_sinks(self, fake_db):
        js = self._js()
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "srcdoc", "setAttribute('on"):
            assert sink not in js, sink
        assert "sandbox:" in js and "textContent" in js

    def test_dashboards_load_the_builder_and_have_containers(self, fake_db):
        teacher = (ROOT / "templates" / "teacher.html").read_text(encoding="utf-8")
        student = (ROOT / "templates" / "student.html").read_text(encoding="utf-8")
        assert 'id="lessonBuilderMount"' in teacher and "lesson_builder.js" in teacher
        assert 'id="blockLessonViewer"' in student and "lesson_builder.js" in student

    def test_student_js_opens_block_lessons_and_keeps_legacy_links(self, fake_db):
        js = (ROOT / "static" / "student.js").read_text(encoding="utf-8")
        assert js.count("BMTOpenBlockLesson(") == 2
        assert js.count('target="_blank" rel="noopener noreferrer">Open</a>') >= 1  # legacy Open link untouched


# --------------------------------------------------------------------------
# Lesson Builder/Progress: Quiz evidence on courseProgress (assessment
# evidence only - never drives lesson/course completion) and the small,
# dedicated teacher views. Deliberately kept separate from
# Exam/Marklist/Gradebook: no exam or marklist collection is touched.
# --------------------------------------------------------------------------
class TestQuizEvidenceOnCourseProgress:
    def _setup(self, db, attempts=2):
        seed_course(db)
        lid = create_lesson(teacher_client(db), blocks=[
            quiz_block(attempts=attempts),
            {"type": "practice", "content": {"questions": [
                {"prompt": "1+1?", "options": {"A": "1", "B": "2"}, "answer": "B"}]}},
        ])
        quiz_id, prac_id = block_ids(db, lid)
        return lid, quiz_id, prac_id

    def test_quiz_submission_records_evidence_without_completing_lesson(self, fake_db):
        lid, quiz_id, _ = self._setup(fake_db)
        qid = fake_db.dump("lessons")[lid]["blocks"][0]["content"]["questions"][0]["id"]
        s = student_client(fake_db)
        r = s.post(f"/api/student/lessons/{lid}/blocks/{quiz_id}/answers", json={"answers": {qid: "B"}})
        assert r.status_code == 200
        progress = fake_db.dump("courseProgress")["s1_c1"]
        assert progress["quizEvidence"][lid][quiz_id]["bestPercentage"] == 100.0
        assert progress["quizEvidence"][lid][quiz_id]["attempts"] == 1
        # Evidence only: a quiz attempt must never mark the lesson or course complete.
        assert "completedLessonIds" not in progress
        assert "percent" not in progress
        assert "completedAt" not in progress

    def test_practice_submission_never_writes_course_progress(self, fake_db):
        lid, _quiz, prac_id = self._setup(fake_db)
        pq = fake_db.dump("lessons")[lid]["blocks"][1]["content"]["questions"][0]["id"]
        s = student_client(fake_db)
        r = s.post(f"/api/student/lessons/{lid}/blocks/{prac_id}/answers", json={"answers": {pq: "B"}})
        assert r.status_code == 200
        assert fake_db.count("courseProgress") == 0

    def test_evidence_from_two_quiz_blocks_does_not_clobber_each_other(self, fake_db):
        seed_course(fake_db)
        lid = create_lesson(teacher_client(fake_db), blocks=[quiz_block(attempts=3), quiz_block(attempts=3)])
        q1, q2 = block_ids(fake_db, lid)
        qid1 = fake_db.dump("lessons")[lid]["blocks"][0]["content"]["questions"][0]["id"]
        qid2 = fake_db.dump("lessons")[lid]["blocks"][1]["content"]["questions"][0]["id"]
        s = student_client(fake_db)
        s.post(f"/api/student/lessons/{lid}/blocks/{q1}/answers", json={"answers": {qid1: "B"}})
        s.post(f"/api/student/lessons/{lid}/blocks/{q2}/answers", json={"answers": {qid2: "A"}})
        evidence = fake_db.dump("courseProgress")["s1_c1"]["quizEvidence"][lid]
        assert evidence[q1]["bestPercentage"] == 100.0
        assert evidence[q2]["bestPercentage"] == 0.0  # wrong answer, still recorded as evidence
        assert set(evidence) == {q1, q2}

    def test_mark_complete_still_works_and_does_not_erase_quiz_evidence(self, fake_db):
        import firebase_admin.firestore as fs
        if not hasattr(fs, "ArrayUnion"):
            fs.ArrayUnion = lambda values: list(values)
        lid, quiz_id, _ = self._setup(fake_db)
        qid = fake_db.dump("lessons")[lid]["blocks"][0]["content"]["questions"][0]["id"]
        s = student_client(fake_db)
        s.post(f"/api/student/lessons/{lid}/blocks/{quiz_id}/answers", json={"answers": {qid: "A"}})
        # Quiz evidence alone does not complete the lesson - Mark Complete is unchanged.
        assert s.get(f"/api/student/lessons/{lid}").get_json()["lesson"]["completed"] is False
        app = make_app(register_student_course_routes, _as("s1"), lambda: True)
        r = app.test_client().post(f"/api/student/lessons/{lid}/complete")
        assert r.status_code == 200
        progress = fake_db.dump("courseProgress")["s1_c1"]
        assert progress["completedLessonIds"] == [lid]
        assert progress["quizEvidence"][lid][quiz_id]["attempts"] == 1


class TestTeacherLessonProgressViews:
    def _setup(self, db):
        seed_course(db)
        lid = create_lesson(teacher_client(db), blocks=[quiz_block(attempts=2)])
        quiz_id, = block_ids(db, lid)
        qid = db.dump("lessons")[lid]["blocks"][0]["content"]["questions"][0]["id"]
        return lid, quiz_id, qid

    def test_teacher_sees_own_lesson_block_results(self, fake_db):
        lid, quiz_id, qid = self._setup(fake_db)
        student_client(fake_db, name="Sami K.").post(
            f"/api/student/lessons/{lid}/blocks/{quiz_id}/answers", json={"answers": {qid: "B"}})
        r = teacher_client(fake_db).get(f"/api/teacher/lessons/{lid}/block-results")
        assert r.status_code == 200
        body = r.get_json()
        assert body["students"][0]["studentUid"] == "s1"
        assert body["students"][0]["name"] == "Sami K."
        assert body["students"][0]["blocks"][0]["blockType"] == "quiz"
        assert body["students"][0]["blocks"][0]["bestPercentage"] == 100.0

    def test_teacher_who_does_not_own_the_lesson_is_denied(self, fake_db):
        lid, quiz_id, qid = self._setup(fake_db)
        student_client(fake_db).post(f"/api/student/lessons/{lid}/blocks/{quiz_id}/answers", json={"answers": {qid: "B"}})
        other = teacher_client(fake_db, uid="t2")
        assert other.get(f"/api/teacher/lessons/{lid}/block-results").status_code in (403, 404)

    def test_unapproved_teacher_is_denied(self, fake_db):
        lid, _quiz_id, _qid = self._setup(fake_db)
        unapproved = teacher_client(fake_db, uid="t3", approved=False)
        assert unapproved.get(f"/api/teacher/lessons/{lid}/block-results").status_code == 403
        assert unapproved.get("/api/teacher/courses/c1/lesson-progress").status_code == 403

    def test_course_lesson_progress_summary(self, fake_db):
        lid, quiz_id, qid = self._setup(fake_db)
        student_client(fake_db).post(f"/api/student/lessons/{lid}/blocks/{quiz_id}/answers", json={"answers": {qid: "B"}})
        r = teacher_client(fake_db).get("/api/teacher/courses/c1/lesson-progress")
        assert r.status_code == 200
        row = next(l for l in r.get_json()["lessons"] if l["id"] == lid)
        assert row["hasQuiz"] is True
        assert row["quizAttemptCount"] == 1
        assert row["quizAveragePercentage"] == 100.0
        assert row["completedCount"] == 0  # Mark Complete was never called

    def test_teacher_who_does_not_own_the_course_is_denied(self, fake_db):
        self._setup(fake_db)
        other = teacher_client(fake_db, uid="t2")
        assert other.get("/api/teacher/courses/c1/lesson-progress").status_code == 403

    def test_lesson_progress_views_never_touch_exam_or_marklist_collections(self, fake_db):
        lid, quiz_id, qid = self._setup(fake_db)
        student_client(fake_db).post(f"/api/student/lessons/{lid}/blocks/{quiz_id}/answers", json={"answers": {qid: "B"}})
        t = teacher_client(fake_db)
        t.get(f"/api/teacher/lessons/{lid}/block-results")
        t.get("/api/teacher/courses/c1/lesson-progress")
        assert fake_db.count("examAttempts") == 0
        assert fake_db.count("marklist") == 0


# --------------------------------------------------------------------------
# front end wiring - Lesson Progress panel (teacher dashboard only; the
# student dashboard and lesson_builder.js are untouched by this phase)
# --------------------------------------------------------------------------
class TestLessonProgressFrontEndWiring:
    def test_teacher_dashboard_has_a_lesson_progress_panel(self, fake_db):
        teacher = (ROOT / "templates" / "teacher.html").read_text(encoding="utf-8")
        for needle in ('id="lessonProgressCard"', 'id="lessonProgressCourse"',
                       'id="lessonProgressList"', 'id="lessonProgressDetail"',
                       'id="refreshLessonProgressBtn"'):
            assert needle in teacher, needle

    def test_teacher_js_calls_the_new_dedicated_endpoints(self, fake_db):
        js = (ROOT / "static" / "teacher.js").read_text(encoding="utf-8")
        assert "/lesson-progress" in js
        assert "/block-results" in js
        # The new Lesson Progress code itself is kept separate from the
        # exam/marklist system - it never references those endpoints.
        marker = "V31.108 Lesson Builder/Progress:"
        assert marker in js
        new_section = js[js.index(marker):]
        assert "examAttempts" not in new_section and "/marklist" not in new_section
