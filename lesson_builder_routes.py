"""V31.108 Interactive Lesson Builder - server-authoritative routes.

Teacher side (approved teacher who owns the course + lesson):
  GET  /api/teacher/lesson-blocks/types
  POST /api/teacher/courses/<course_id>/block-lessons
  GET  /api/teacher/lessons/<lesson_id>/blocks
  PUT  /api/teacher/lessons/<lesson_id>/blocks
  POST /api/teacher/lessons/<lesson_id>/blocks/reorder
  GET  /api/teacher/lessons/<lesson_id>/block-results        (V31.108 Lesson Builder/Progress)
  GET  /api/teacher/courses/<course_id>/lesson-progress      (V31.108 Lesson Builder/Progress)
  GET  /api/teacher/courses/<course_id>/progress-summary     (V31.108 Learning Progress & Analytics)

The three "Lesson Builder/Progress"/"Learning Progress & Analytics" routes
above are a small, dedicated view of `lessonBlockResults` (Practice/Quiz
attempts recorded by this module), of `courseProgress` (self-paced lesson
completion, written by student_course_routes.py) and, for
`progress-summary` only, of the existing `users` grade/targeting model
(targeting_access.content_target_allowed(), the same check
student_course_routes.student_list_courses() already applies) to count
students eligible for the course - no new enrollment record is introduced.
They are intentionally separate from the Exam/Marklist/Gradebook system - no
exam, marklist or gradebook data is read or written by them, and they never
accept data from anyone but an approved teacher who owns the course and
lesson in question.

Student side (same access gates as completing a lesson: own grade, published
course + lesson, open unit, Learning Mode / Audience of the course):
  GET  /api/student/lessons/<lesson_id>
  POST /api/student/lessons/<lesson_id>/blocks/<block_id>/answers

Storage: block content lives on the existing `lessons` document
(`blocks`, `blockSchemaVersion`, `blockCount`) so every existing lesson,
course and progress path keeps working. Practice/Quiz answer keys and
explanations live ONLY in `lessonBlockKeys/{lessonId}`; per-student scores in
`lessonBlockResults/{studentUid}_{lessonId}_{blockId}`. Both collections are
denied to every client by firestore.rules - only these routes (Admin SDK)
touch them. A passed/failed Quiz also records a `quizEvidence` entry on the
student's existing `courseProgress/{studentUid}_{courseId}` document (merge
only) as assessment evidence; it never sets or changes `completedLessonIds`,
`percent` or `completedAt` - lesson completion stays exactly the existing,
student-driven "Mark Complete" action. Practice never writes courseProgress.

Nothing here changes an existing route's contract. Lessons/units/courses are
created and edited by the existing teacher routes; this module only adds the
block layer on top.
"""
import re
from datetime import datetime, timezone

from flask import jsonify, request

import lesson_blocks as lb
from targeting_access import content_target_allowed, content_visible_or_untargeted

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,150}$")


def _safe_id(value):
    value = str(value or "").strip()
    return value if _SAFE_ID_RE.match(value) else ""


def register_lesson_builder_routes(app, require_user, firebase_admin_factory):
    def _db():
        fb = firebase_admin_factory()
        if not fb:
            raise RuntimeError("Firebase server credentials are not configured.")
        from firebase_admin import firestore
        return firestore.client()

    def _teacher(db, uid):
        snap = db.collection("teachers").document(uid).get()
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        if not data.get("approved") and data.get("status") != "approved":
            return None
        return data

    def _grade_of(doc):
        return str(doc.get("className") or doc.get("grade") or "").strip()

    def _iso(v):
        return v.isoformat() if hasattr(v, "isoformat") else v

    def _entitlement(db, uid):
        """Same best-effort entitlement lookup already used everywhere else
        content_target_allowed() is called (teacher_routes.py,
        learning_challenge_routes.py, student_course_routes.py) - missing or
        unreadable entitlement never blocks anything, it just means the
        student is treated as non-premium."""
        try:
            snap = db.collection("entitlements").document(uid).get()
            return snap.to_dict() or {} if snap.exists else {}
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # teacher helpers
    # ------------------------------------------------------------------
    def _teacher_gate(db, uid):
        if not _teacher(db, uid):
            return jsonify({"error": "Approved teacher access required."}), 403
        return None

    def _owned_lesson(db, uid, lesson_id):
        """(ref, lesson, course, error_response). The teacher must own BOTH
        the lesson and its course."""
        lesson_id = _safe_id(lesson_id)
        if not lesson_id:
            return None, None, None, (jsonify({"error": "Lesson not found or not owned by this teacher."}), 404)
        ref = db.collection("lessons").document(lesson_id)
        snap = ref.get()
        lesson = snap.to_dict() or {}
        if not snap.exists or lesson.get("teacherUid") != uid:
            return None, None, None, (jsonify({"error": "Lesson not found or not owned by this teacher."}), 404)
        course_snap = db.collection("courses").document(str(lesson.get("courseId") or "_")).get()
        course = course_snap.to_dict() or {}
        if not course_snap.exists or course.get("teacherUid") != uid:
            return None, None, None, (jsonify({"error": "Course not found or not owned by this teacher."}), 403)
        return ref, lesson, course, None

    def _check_references(db, uid, blocks, course_id, course_grade):
        """Blocks that point at other records (assignments) may only point at
        records this teacher owns, for this course's grade."""
        for index, block in enumerate(blocks):
            bt = lb.get_block_type(block["type"])
            for kind, ref_id in bt.references(block["content"]):
                if kind != "assignment":
                    continue
                snap = db.collection("assignments").document(_safe_id(ref_id) or "_").get()
                a = snap.to_dict() or {}
                if (not snap.exists or a.get("teacherUid") != uid or _grade_of(a) != course_grade
                        or (a.get("courseId") and a.get("courseId") != course_id)):
                    return (jsonify({"error": "Assignment not found, not owned by this teacher, or not for this course's grade.",
                                     "blockIndex": index, "field": "assignmentId"}), 403)
        return None

    def _teacher_view(blocks, keys_by_block):
        """Stored blocks with answer keys merged back in - for the owning
        teacher's editor only."""
        out = []
        for b in lb.normalize_block_order(blocks):
            content = dict(b.get("content") or {})
            k = (keys_by_block or {}).get(b.get("id")) or {}
            if isinstance(content.get("questions"), list):
                qs = []
                for q in content["questions"]:
                    q = dict(q)
                    if q.get("id") in (k.get("answers") or {}):
                        q["answer"] = k["answers"][q["id"]]
                    if q.get("id") in (k.get("explanations") or {}):
                        q["explanation"] = k["explanations"][q["id"]]
                    qs.append(q)
                content["questions"] = qs
            out.append({"id": b.get("id"), "type": b.get("type"), "order": b.get("order"), "content": content})
        return out

    def _log_invalid(lesson_id):
        def cb(block, reason):
            app.logger.warning("Dropped invalid lesson block lesson=%s block=%s reason=%s", lesson_id, block.get("id"), reason)
        return cb

    # ------------------------------------------------------------------
    # teacher routes
    # ------------------------------------------------------------------
    @app.get("/api/teacher/lesson-blocks/types")
    def lesson_block_types():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db()
            denied = _teacher_gate(db, detail["uid"])
            if denied:
                return denied
            return jsonify({"types": lb.block_type_catalog(),
                            "limits": {"maxBlocks": lb.MAX_BLOCKS, "maxQuestions": lb.MAX_QUESTIONS,
                                       "maxTextLength": lb.MAX_TEXT_LENGTH}}), 200
        except Exception:
            app.logger.exception("Lesson block types failed")
            return jsonify({"error": "Unable to load block types."}), 500

    @app.post("/api/teacher/courses/<course_id>/block-lessons")
    def teacher_create_block_lesson(course_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail["uid"]
            denied = _teacher_gate(db, uid)
            if denied:
                return denied
            course_id = _safe_id(course_id)
            course_snap = db.collection("courses").document(course_id or "_").get()
            course = course_snap.to_dict() or {}
            if not course_id or not course_snap.exists or course.get("teacherUid") != uid:
                return jsonify({"error": "Course not found or not owned by this teacher."}), 403
            body = request.get_json(silent=True) or {}
            title = lb.sanitize_text(body.get("title"), limit=160, allow_newlines=False)
            if not title:
                return jsonify({"error": "Lesson title is required."}), 400
            description = lb.sanitize_text(body.get("description"), limit=2000)
            try:
                order = int(body.get("order", 0) or 0)
            except (TypeError, ValueError):
                order = 0
            status = str(body.get("status") or "draft").strip().lower()
            if status not in {"draft", "published"}:
                return jsonify({"error": "Status must be one of: published, draft."}), 400
            unit_id = str(body.get("unitId") or "").strip()
            if unit_id:
                u = db.collection("units").document(_safe_id(unit_id) or "_").get()
                udata = u.to_dict() or {}
                if not u.exists or udata.get("teacherUid") != uid or udata.get("courseId") != course_id:
                    return jsonify({"error": "Unit not found, not owned by this teacher, or does not belong to the given course."}), 403
            try:
                blocks, keys = lb.validate_blocks(body.get("blocks") or [])
            except lb.BlockValidationError as exc:
                return jsonify(exc.to_dict()), 400
            denied = _check_references(db, uid, blocks, course_id, _grade_of(course))
            if denied:
                return denied
            now = datetime.now(timezone.utc)
            ref = db.collection("lessons").document()
            batch = db.batch()
            batch.set(ref, {
                "courseId": course_id, "unitId": unit_id or None, "teacherUid": uid, "title": title,
                "contentType": "blocks", "url": "", "description": description, "order": order,
                "status": status, "blocks": blocks, "blockSchemaVersion": lb.BLOCK_SCHEMA_VERSION,
                "blockCount": len(blocks), "createdAt": now, "updatedAt": now,
            })
            batch.set(db.collection("lessonBlockKeys").document(ref.id), {
                "lessonId": ref.id, "courseId": course_id, "teacherUid": uid, "blocks": keys, "updatedAt": now,
            })
            batch.commit()
            return jsonify({"success": True, "lessonId": ref.id, "unitId": unit_id or None, "status": status,
                            "blocks": _teacher_view(blocks, keys)}), 201
        except Exception:
            app.logger.exception("Teacher block lesson creation failed")
            return jsonify({"error": "Unable to create lesson."}), 500

    @app.get("/api/teacher/lessons/<lesson_id>/blocks")
    def teacher_get_lesson_blocks(lesson_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail["uid"]
            denied = _teacher_gate(db, uid)
            if denied:
                return denied
            ref, lesson, course, err = _owned_lesson(db, uid, lesson_id)
            if err:
                return err
            stored = lesson.get("blocks") if isinstance(lesson.get("blocks"), list) else []
            key_snap = db.collection("lessonBlockKeys").document(ref.id).get()
            keys = (key_snap.to_dict() or {}).get("blocks") or {} if key_snap.exists else {}
            return jsonify({
                "lesson": {"id": ref.id, "courseId": lesson.get("courseId"), "unitId": lesson.get("unitId"),
                           "title": lesson.get("title"), "description": lesson.get("description") or "",
                           "status": lesson.get("status"), "order": lesson.get("order", 0),
                           "contentType": lesson.get("contentType"), "url": lesson.get("url") or ""},
                "blocks": _teacher_view(stored, keys),
                "preview": lb.render_blocks(stored, on_invalid=_log_invalid(ref.id)),
            }), 200
        except Exception:
            app.logger.exception("Teacher lesson blocks load failed")
            return jsonify({"error": "Unable to load lesson blocks."}), 500

    @app.put("/api/teacher/lessons/<lesson_id>/blocks")
    def teacher_put_lesson_blocks(lesson_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail["uid"]
            denied = _teacher_gate(db, uid)
            if denied:
                return denied
            ref, lesson, course, err = _owned_lesson(db, uid, lesson_id)
            if err:
                return err
            body = request.get_json(silent=True) or {}
            if "blocks" not in body:
                return jsonify({"error": "blocks is required."}), 400
            try:
                blocks, keys = lb.validate_blocks(body.get("blocks"))
            except lb.BlockValidationError as exc:
                return jsonify(exc.to_dict()), 400
            denied = _check_references(db, uid, blocks, lesson.get("courseId"), _grade_of(course))
            if denied:
                return denied
            now = datetime.now(timezone.utc)
            batch = db.batch()
            batch.update(ref, {"blocks": blocks, "blockSchemaVersion": lb.BLOCK_SCHEMA_VERSION,
                               "blockCount": len(blocks), "updatedAt": now})
            batch.set(db.collection("lessonBlockKeys").document(ref.id), {
                "lessonId": ref.id, "courseId": lesson.get("courseId"), "teacherUid": uid, "blocks": keys, "updatedAt": now,
            })
            batch.commit()
            return jsonify({"success": True, "blockCount": len(blocks), "blocks": _teacher_view(blocks, keys)}), 200
        except Exception:
            app.logger.exception("Teacher lesson blocks save failed")
            return jsonify({"error": "Unable to save lesson blocks."}), 500

    @app.post("/api/teacher/lessons/<lesson_id>/blocks/reorder")
    def teacher_reorder_lesson_blocks(lesson_id):
        """Body: {"order": [blockId, ...]} - every block of the lesson, in the
        new order. Same contract as the unit/lesson reorder routes: the list
        must be exactly the current set, otherwise 409."""
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail["uid"]
            denied = _teacher_gate(db, uid)
            if denied:
                return denied
            ref, lesson, _course, err = _owned_lesson(db, uid, lesson_id)
            if err:
                return err
            ids = (request.get_json(silent=True) or {}).get("order")
            if not isinstance(ids, list) or not ids or not all(isinstance(x, str) for x in ids):
                return jsonify({"error": "order must be a non-empty list of block ids."}), 400
            stored = lesson.get("blocks") if isinstance(lesson.get("blocks"), list) else []
            reordered = lb.reorder_blocks(stored, [x.strip() for x in ids])
            if reordered is None:
                return jsonify({"error": "The block list has changed. Reload and try again."}), 409
            ref.update({"blocks": reordered, "updatedAt": datetime.now(timezone.utc)})
            return jsonify({"success": True, "order": [b["id"] for b in reordered]}), 200
        except Exception:
            app.logger.exception("Teacher lesson block reorder failed")
            return jsonify({"error": "Unable to reorder blocks."}), 500

    # ------------------------------------------------------------------
    # teacher routes - Lesson Builder/Progress (Practice/Quiz results and
    # lesson completion). Deliberately separate from Exam/Marklist/Gradebook:
    # reads only lessonBlockResults + courseProgress, never exams/marklist
    # collections, and never accepts a courseId/lessonId the requesting
    # teacher does not own.
    # ------------------------------------------------------------------
    @app.get("/api/teacher/lessons/<lesson_id>/block-results")
    def teacher_lesson_block_results(lesson_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail["uid"]
            denied = _teacher_gate(db, uid)
            if denied:
                return denied
            ref, lesson, _course, err = _owned_lesson(db, uid, lesson_id)
            if err:
                return err
            stored = lesson.get("blocks") if isinstance(lesson.get("blocks"), list) else []
            block_types = {b.get("id"): b.get("type") for b in stored
                           if isinstance(b, dict) and b.get("type") in ("quiz", "practice")}
            if not block_types:
                return jsonify({"lessonId": ref.id, "title": lesson.get("title"), "students": []}), 200
            by_student = {}
            for doc in db.collection("lessonBlockResults").where("lessonId", "==", ref.id).stream():
                r = doc.to_dict() or {}
                block_id = r.get("blockId")
                student_uid = r.get("studentUid")
                if block_id not in block_types or not student_uid:
                    continue
                entry = by_student.setdefault(student_uid, {"studentUid": student_uid, "blocks": []})
                entry["blocks"].append({
                    "blockId": block_id, "blockType": r.get("blockType") or block_types.get(block_id),
                    "attempts": r.get("attempts", 0), "bestPercentage": r.get("bestPercentage"),
                    "lastPercentage": r.get("lastPercentage"), "lastAttemptAt": _iso(r.get("lastAttemptAt")),
                })
            for student_uid, entry in by_student.items():
                u_snap = db.collection("users").document(student_uid).get()
                x = u_snap.to_dict() or {} if u_snap.exists else {}
                entry["name"] = x.get("name") or x.get("displayName") or x.get("fullName") or "Student"
            students = sorted(by_student.values(), key=lambda e: str(e.get("name") or "").lower())
            return jsonify({"lessonId": ref.id, "title": lesson.get("title"), "students": students}), 200
        except Exception:
            app.logger.exception("Teacher lesson block results failed")
            return jsonify({"error": "Unable to load lesson results."}), 500

    @app.get("/api/teacher/courses/<course_id>/lesson-progress")
    def teacher_course_lesson_progress(course_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail["uid"]
            denied = _teacher_gate(db, uid)
            if denied:
                return denied
            course_id = _safe_id(course_id)
            course_snap = db.collection("courses").document(course_id or "_").get()
            course = course_snap.to_dict() or {}
            if not course_id or not course_snap.exists or course.get("teacherUid") != uid:
                return jsonify({"error": "Course not found or not owned by this teacher."}), 403
            lessons = []
            for d in db.collection("lessons").where("courseId", "==", course_id).where("teacherUid", "==", uid).stream():
                l = d.to_dict() or {}
                blocks = l.get("blocks") if isinstance(l.get("blocks"), list) else []
                lessons.append({"id": d.id, "title": l.get("title") or "Lesson", "order": l.get("order", 0),
                                 "status": l.get("status"),
                                 "hasQuiz": any(isinstance(b, dict) and b.get("type") == "quiz" for b in blocks)})
            lesson_ids = [l["id"] for l in lessons]
            completed_counts = {lid: 0 for lid in lesson_ids}
            total_students = 0
            for d in db.collection("courseProgress").where("courseId", "==", course_id).stream():
                total_students += 1
                p = d.to_dict() or {}
                for lid in (p.get("completedLessonIds") or []):
                    if lid in completed_counts:
                        completed_counts[lid] += 1
            for l in lessons:
                attempts = 0; total_pct = 0.0
                if l["hasQuiz"]:
                    for doc in (db.collection("lessonBlockResults")
                                .where("lessonId", "==", l["id"]).where("blockType", "==", "quiz").stream()):
                        r = doc.to_dict() or {}
                        attempts += 1
                        total_pct += float(r.get("bestPercentage") or 0)
                l["completedCount"] = completed_counts.get(l["id"], 0)
                l["totalStudents"] = total_students
                l["quizAttemptCount"] = attempts
                l["quizAveragePercentage"] = round(total_pct / attempts, 1) if attempts else None
            lessons.sort(key=lambda l: l.get("order", 0))
            return jsonify({"courseId": course_id, "title": course.get("title"), "lessons": lessons}), 200
        except Exception:
            app.logger.exception("Teacher course lesson progress failed")
            return jsonify({"error": "Unable to load lesson progress."}), 500

    @app.get("/api/teacher/courses/<course_id>/progress-summary")
    def teacher_course_progress_summary(course_id):
        """V31.108 Learning Progress & Analytics: a single course-level
        summary card (Students enrolled / Started / In progress / Completed
        / Average score) for the Teacher Dashboard, sitting alongside the
        existing per-lesson lesson-progress/block-results views above.

        "Enrolled" is NOT a new enrollment record - per the existing BMT
        access model, it is every student whose grade and Learning
        Mode/Audience make this course visible to them today
        (targeting_access.content_target_allowed(), the same check
        student_course_routes.student_list_courses() already uses to build
        a student's own course list). Started/In progress/Completed come
        straight from the existing courseProgress documents
        student_course_routes.py already writes (startedAt/completedAt) -
        no new progress state. Average score is the mean of
        lessonBlockResults' bestPercentage across this course's Quiz blocks
        only (Practice is formative/ungraded and is intentionally excluded,
        matching how Practice is already excluded from every other
        Quiz-specific aggregate in this file); it is evidence only and,
        like everywhere else in this module, never marks a lesson complete
        or touches the Exam/Marklist/Gradebook system.
        """
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail["uid"]
            denied = _teacher_gate(db, uid)
            if denied:
                return denied
            course_id = _safe_id(course_id)
            course_snap = db.collection("courses").document(course_id or "_").get()
            course = course_snap.to_dict() or {}
            if not course_id or not course_snap.exists or course.get("teacherUid") != uid:
                return jsonify({"error": "Course not found or not owned by this teacher."}), 403
            course_grade = _grade_of(course)

            # Enrolled: eligible-by-grade-and-targeting, same student-role +
            # grade scan already used for a teacher's roster count in
            # teacher_routes.py's admin_teacher_roster(), plus the same
            # per-student targeting check student_list_courses() applies.
            enrolled = 0
            if course_grade:
                for s in db.collection("users").limit(5000).stream():
                    x = s.to_dict() or {}
                    role = str(x.get("role") or x.get("userType") or x.get("accountType") or "").lower()
                    if role not in {"student", "learner", ""} or x.get("isAdmin"):
                        continue
                    if str(x.get("className") or x.get("class") or x.get("grade") or "").strip() != course_grade:
                        continue
                    if content_target_allowed(x, course, _entitlement(db, s.id)):
                        enrolled += 1

            # Started / In progress / Completed: read straight off the
            # existing courseProgress documents - no new field, no
            # recomputation of lesson visibility (completedAt is only ever
            # set by student_complete_lesson() once every visible lesson is
            # done, so it already reflects the same truth the student
            # dashboard and certificate issuance rely on).
            started = 0; completed = 0
            for d in db.collection("courseProgress").where("courseId", "==", course_id).stream():
                p = d.to_dict() or {}
                if p.get("startedAt"):
                    started += 1
                if p.get("completedAt"):
                    completed += 1
            in_progress = max(started - completed, 0)

            # Average score: Quiz-block attempts only, aggregated across
            # every lesson this teacher owns in this course.
            lesson_ids = [d.id for d in db.collection("lessons")
                          .where("courseId", "==", course_id).where("teacherUid", "==", uid).stream()]
            quiz_attempts = 0; total_pct = 0.0
            for lesson_id in lesson_ids:
                for doc in (db.collection("lessonBlockResults")
                            .where("lessonId", "==", lesson_id).where("blockType", "==", "quiz").stream()):
                    r = doc.to_dict() or {}
                    quiz_attempts += 1
                    total_pct += float(r.get("bestPercentage") or 0)
            average_score = round(total_pct / quiz_attempts, 1) if quiz_attempts else None

            return jsonify({
                "courseId": course_id, "title": course.get("title"),
                "enrolled": enrolled, "started": started, "inProgress": in_progress,
                "completed": completed, "quizAttempts": quiz_attempts, "averageScore": average_score,
            }), 200
        except Exception:
            app.logger.exception("Teacher course progress summary failed")
            return jsonify({"error": "Unable to load course progress summary."}), 500

    # ------------------------------------------------------------------
    # student helpers
    # ------------------------------------------------------------------
    def _student_gate(db, uid, detail, lesson_id):
        """Returns (ctx, error_response). Same rules as
        student_complete_lesson(), applied to reading a lesson."""
        lesson_id = _safe_id(lesson_id)
        if not lesson_id:
            return None, (jsonify({"error": "Lesson not found."}), 404)
        lesson_snap = db.collection("lessons").document(lesson_id).get()
        if not lesson_snap.exists:
            return None, (jsonify({"error": "Lesson not found."}), 404)
        lesson = lesson_snap.to_dict() or {}
        course_id = lesson.get("courseId")
        course_snap = db.collection("courses").document(str(course_id or "_")).get()
        if not course_snap.exists:
            return None, (jsonify({"error": "Course not found."}), 404)
        course = course_snap.to_dict() or {}
        user_snap = db.collection("users").document(uid).get()
        user = user_snap.to_dict() or {} if user_snap.exists else {}
        try:
            ent_snap = db.collection("entitlements").document(uid).get()
            entitlement = ent_snap.to_dict() or {} if ent_snap.exists else {}
        except Exception:
            entitlement = {}
        grade = str(user.get("className") or user.get("class") or user.get("grade") or detail.get("grade") or "").strip()
        # Grade first, strictly: a student with no grade on file, or the wrong
        # grade, never reads a course's lesson content.
        if not grade or _grade_of(course) != grade:
            return None, (jsonify({"error": "You do not have access to this course."}), 403)
        if str(course.get("status") or "").lower() != "published":
            return None, (jsonify({"error": "This course is not available."}), 403)
        if str(lesson.get("status") or "").lower() != "published":
            return None, (jsonify({"error": "This lesson is not available."}), 403)
        unit_id = lesson.get("unitId")
        if unit_id:
            unit_snap = db.collection("units").document(_safe_id(unit_id) or "_").get()
            # Same rule as student_course_routes._unit_is_open(): an unknown
            # unit id (legacy data) does not hide the lesson.
            if unit_snap.exists and (unit_snap.to_dict() or {}).get("status") != "published":
                return None, (jsonify({"error": "This lesson is not available."}), 403)
        if not content_target_allowed(user, course, entitlement):
            return None, (jsonify({"error": "You do not have access to this course."}), 403)
        return {"lessonId": lesson_id, "lesson": lesson, "courseId": course_id, "course": course,
                "user": user, "entitlement": entitlement, "grade": grade}, None

    def _assignment_render(db, uid, ctx, block_render):
        aid = _safe_id(block_render.get("assignmentId"))
        snap = db.collection("assignments").document(aid or "_").get()
        a = snap.to_dict() or {}
        visible = (snap.exists and a.get("status") == "published" and _grade_of(a) == ctx["grade"]
                   and content_visible_or_untargeted(ctx["user"], a, ctx["entitlement"]))
        if not visible:
            return {"available": False, "note": block_render.get("note") or ""}
        status = "not_submitted"
        for s in db.collection("assignmentSubmissions").where("studentUid", "==", uid).where("assignmentId", "==", aid).stream():
            status = (s.to_dict() or {}).get("status") or "submitted"
            break
        return {"available": True, "assignmentId": aid, "title": a.get("title") or "Assignment",
                "description": lb.sanitize_text(a.get("description"), limit=4000),
                "dueAt": a.get("dueAt"), "note": block_render.get("note") or "", "submissionStatus": status}

    # ------------------------------------------------------------------
    # student routes
    # ------------------------------------------------------------------
    @app.get("/api/student/lessons/<lesson_id>")
    def student_get_lesson(lesson_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail["uid"]
            ctx, err = _student_gate(db, uid, detail, lesson_id)
            if err:
                return err
            lesson, course = ctx["lesson"], ctx["course"]
            stored = lesson.get("blocks") if isinstance(lesson.get("blocks"), list) else []
            blocks = lb.render_blocks(stored, on_invalid=_log_invalid(ctx["lessonId"]))
            for block in blocks:
                if block["type"] == "assignment":
                    block["render"] = _assignment_render(db, uid, ctx, block["render"])
                elif block["type"] in ("quiz", "practice"):
                    res = db.collection("lessonBlockResults").document(f"{uid}_{ctx['lessonId']}_{block['id']}").get()
                    if res.exists:
                        r = res.to_dict() or {}
                        block["render"]["myResult"] = {"attempts": r.get("attempts", 0),
                                                       "bestPercentage": r.get("bestPercentage"),
                                                       "lastPercentage": r.get("lastPercentage")}
            prog = db.collection("courseProgress").document(f"{uid}_{ctx['courseId']}").get()
            completed = ctx["lessonId"] in ((prog.to_dict() or {}).get("completedLessonIds") or []) if prog.exists else False
            legacy = None
            if lesson.get("url") and lesson.get("contentType") != "blocks":
                try:
                    legacy = {"contentType": str(lesson.get("contentType") or "link"),
                              "url": lb.validate_external_url(lesson.get("url"))}
                except lb.BlockValidationError:
                    legacy = None
            return jsonify({
                "lesson": {"id": ctx["lessonId"], "title": lesson.get("title"), "description": lesson.get("description") or "",
                           "order": lesson.get("order", 0), "unitId": lesson.get("unitId"), "completed": completed},
                "course": {"id": ctx["courseId"], "title": course.get("title"), "subject": course.get("subject") or "",
                           "className": course.get("className")},
                "blocks": blocks,
                "legacyResource": legacy,
            }), 200
        except Exception:
            app.logger.exception("Student lesson load failed")
            return jsonify({"error": "Unable to load this lesson."}), 500

    @app.post("/api/student/lessons/<lesson_id>/blocks/<block_id>/answers")
    def student_submit_block_answers(lesson_id, block_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail["uid"]
            ctx, err = _student_gate(db, uid, detail, lesson_id)
            if err:
                return err
            block_id = _safe_id(block_id)
            stored = ctx["lesson"].get("blocks") if isinstance(ctx["lesson"].get("blocks"), list) else []
            block = next((b for b in stored if isinstance(b, dict) and b.get("id") == block_id
                          and b.get("type") in ("quiz", "practice")), None)
            if not block_id or block is None:
                return jsonify({"error": "Question block not found."}), 404
            answers = (request.get_json(silent=True) or {}).get("answers")
            if not isinstance(answers, dict) or len(answers) > lb.MAX_QUESTIONS:
                return jsonify({"error": "answers must be an object of questionId to option."}), 400
            try:
                rendered = lb.get_block_type(block["type"]).render(block.get("content") or {})
            except lb.BlockValidationError:
                return jsonify({"error": "This block is not available."}), 409
            key_snap = db.collection("lessonBlockKeys").document(ctx["lessonId"]).get()
            key = ((key_snap.to_dict() or {}).get("blocks") or {}).get(block_id) if key_snap.exists else None
            if not key or not key.get("answers"):
                return jsonify({"error": "This block has no answer key yet."}), 409
            res_ref = db.collection("lessonBlockResults").document(f"{uid}_{ctx['lessonId']}_{block_id}")
            graded_quiz = block["type"] == "quiz"
            max_attempts = rendered.get("maxAttempts") if graded_quiz else None
            # Quiz results are recorded on the student's existing courseProgress
            # document as assessment evidence only - see module docstring.
            # Practice never touches courseProgress.
            progress_ref = (db.collection("courseProgress").document(f"{uid}_{ctx['courseId']}")
                             if graded_quiz else None)
            from firebase_admin import firestore

            class _NoAttemptsLeft(Exception):
                pass

            # The read of the attempt counter, the limit check and the write of
            # the new count happen in ONE transaction, so two simultaneous
            # submissions cannot both be counted as the same attempt (Firestore
            # retries the loser against the fresh count).
            @firestore.transactional
            def _record_attempt(transaction):
                # All reads happen before any writes in this transaction
                # (Firestore requirement): read the attempt counter and,
                # for a quiz, the existing courseProgress doc too.
                res_snap = res_ref.get(transaction=transaction)
                prev_progress = ({} if progress_ref is None
                                  else (progress_ref.get(transaction=transaction).to_dict() or {}))
                prev = res_snap.to_dict() or {} if res_snap.exists else {}
                attempts = int(prev.get("attempts") or 0)
                if graded_quiz and attempts >= max_attempts:
                    raise _NoAttemptsLeft()
                attempts += 1
                attempts_left = (max_attempts - attempts) if graded_quiz else None
                # Practice always shows the key; a quiz shows it only once no
                # attempts remain, so retries cannot be answered from the reveal.
                reveal = (not graded_quiz) or attempts_left == 0
                result = lb.grade_answers(rendered, key, answers, reveal=reveal)
                now = datetime.now(timezone.utc)
                best = max(float(prev.get("bestPercentage") or 0), result["percentage"])
                record = {"studentUid": uid, "lessonId": ctx["lessonId"], "blockId": block_id, "courseId": ctx["courseId"],
                          "blockType": block["type"], "attempts": attempts, "bestPercentage": best,
                          "lastScore": result["score"], "lastTotal": result["totalPoints"],
                          "lastPercentage": result["percentage"], "lastAttemptAt": now}
                if not res_snap.exists:
                    record["createdAt"] = now
                transaction.set(res_ref, record, merge=True)
                if progress_ref is not None:
                    # Assessment evidence only: never touches completedLessonIds,
                    # percent or completedAt, so it cannot mark the lesson (or
                    # course) complete on its own - "Mark Complete" is unchanged.
                    # Merged in Python (not relying on Firestore's merge to do
                    # a deep merge of the nested map) so this can never
                    # overwrite evidence already recorded for another
                    # lesson/block of the same student.
                    quiz_evidence = dict(prev_progress.get("quizEvidence") or {})
                    lesson_evidence = dict(quiz_evidence.get(ctx["lessonId"]) or {})
                    lesson_evidence[block_id] = {"attempts": attempts, "bestPercentage": best,
                                                 "lastPercentage": result["percentage"], "lastAttemptAt": now}
                    quiz_evidence[ctx["lessonId"]] = lesson_evidence
                    transaction.set(progress_ref, {
                        "studentUid": uid, "courseId": ctx["courseId"], "quizEvidence": quiz_evidence,
                    }, merge=True)
                result.update({"success": True, "attempts": attempts, "attemptsLeft": attempts_left,
                               "answersRevealed": reveal, "bestPercentage": best})
                return result

            try:
                result = _record_attempt(db.transaction())
            except _NoAttemptsLeft:
                return jsonify({"error": "You have used all attempts for this quiz."}), 409
            return jsonify(result), 200
        except Exception:
            app.logger.exception("Student block answer submission failed")
            return jsonify({"error": "Unable to check your answers."}), 500
