"""V31.108 Phase C: student-facing consumption of the existing courses/lessons
collections (created via /api/teacher/courses and /api/teacher/lessons).

Before this file, courses/lessons could be created by a teacher and *counted*
in /api/student/progress, but there was no endpoint that let a student
actually list a course's lessons, open one, or mark it complete. This file
adds exactly that, reusing targeting_access.content_target_allowed() so a
course flagged Distance/Scholarship/Competition/etc. is only visible to
students who are actually eligible - the same server-side check already
proven for Question Bank and Challenge content in learning_challenge_routes.py.

Nothing here changes how teachers create courses/lessons, and nothing here
touches the existing /api/student/progress aggregate endpoint.

V31.108 Course Structure Completion: student_course_lessons() below now also
returns the course's Units (new `units` collection, teacher_routes.py) and
each lesson's optional `unitId`, additively - existing fields are unchanged
and a lesson with no unit (unitId=None, true for every lesson created before
this change) behaves exactly as before.

V31.108 Course/Unit/Lesson Foundation: student access is now enforced the same
way on every course/unit/lesson read and write. Opening a course applies the
same grade + published + Learning Mode/Audience gates that completing a lesson
already did; a lesson inside an unpublished unit is neither listed, openable
nor completable and is left out of progress totals; progress only counts
completed lessons that are still visible (so unpublishing a completed lesson
can never push a course above 100%) while every stored completion is kept, so
re-publishing restores it; and the course response gains `nextLessonId` for
"continue learning". Everything else in the responses is unchanged.
"""
from datetime import datetime, timezone
from flask import jsonify, request

from targeting_access import content_target_allowed
import lesson_blocks as _lb


def _safe_legacy_url(value):
    """V31.108 audit fix: legacy lessons store a free-text `url` (older builds
    and direct Firestore writes never validated it). Only hand a student a
    plain http(s) link; anything else (javascript:, data: ...) becomes ""."""
    return _lb.safe_web_link(value)



def register_student_course_routes(app, require_user, firebase_admin_factory):
    def _db():
        fb = firebase_admin_factory()
        if not fb:
            raise RuntimeError("Firebase server credentials are not configured.")
        from firebase_admin import firestore
        return firestore.client()

    def _iso(v):
        return v.isoformat() if hasattr(v, "isoformat") else v

    def _user_and_entitlement(db, uid, detail):
        snap = db.collection("users").document(uid).get()
        user = snap.to_dict() or {} if snap.exists else {}
        try:
            ent_snap = db.collection("entitlements").document(uid).get()
            entitlement = ent_snap.to_dict() or {} if ent_snap.exists else {}
        except Exception:
            entitlement = {}
        grade = str(user.get("className") or user.get("class") or user.get("grade") or detail.get("grade") or "").strip()
        return user, entitlement, grade

    def _progress_ref(db, uid, course_id):
        return db.collection("courseProgress").document(f"{uid}_{course_id}")

    def _certificate_id(uid, course_id):
        import hashlib, secrets
        return "BMT-COURSE-CERT-" + hashlib.sha256(f"{uid}:{course_id}:{secrets.token_hex(8)}".encode()).hexdigest()[:20].upper()

    def _unit_map(db, course_id):
        """Every unit of the course (any status), keyed by id."""
        return {s.id: (s.to_dict() or {}) for s in db.collection("units").where("courseId", "==", course_id).stream()}

    def _unit_is_open(unit_map, unit_id):
        """A lesson is student-reachable unless its unit exists and is not
        published. No unit, or a unit id that no longer resolves (legacy
        data), keeps the lesson visible exactly as before units existed."""
        if not unit_id or unit_id not in unit_map:
            return True
        return unit_map[unit_id].get("status") == "published"

    def _visible_lessons(db, course_id, unit_map):
        """[(lessonId, lessonDict)] a student may see: published lessons
        whose unit (if any) is published."""
        rows = []
        for snap in db.collection("lessons").where("courseId", "==", course_id).where("status", "==", "published").stream():
            l = snap.to_dict() or {}
            if _unit_is_open(unit_map, l.get("unitId")):
                rows.append((snap.id, l))
        return rows

    def _next_lesson_id(lessons, units):
        """First incomplete lesson in the order students see them: lessons
        with no (known) unit first, then each published unit in unit order."""
        known = {u["id"] for u in units}
        ordered = [l for l in lessons if l.get("unitId") not in known]
        for u in units:
            ordered.extend(l for l in lessons if l.get("unitId") == u["id"])
        for l in ordered:
            if not l["completed"]:
                return l["id"]
        return None

    @app.get("/api/student/courses")
    def student_list_courses():
        """Courses for this student's grade that this student is eligible
        for, per Learning Mode + Target Audience - not just all courses for
        the grade like the old /api/student/progress count did."""
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail["uid"]
            user, entitlement, grade = _user_and_entitlement(db, uid, detail)
            rows = []
            query = db.collection("courses").where("status", "==", "published")
            for snap in query.stream():
                c = snap.to_dict() or {}; c["id"] = snap.id
                if grade and str(c.get("className") or "") != grade:
                    continue
                if not content_target_allowed(user, c, entitlement):
                    continue
                visible_ids = {lid for lid, _l in _visible_lessons(db, snap.id, _unit_map(db, snap.id))}
                lesson_count = len(visible_ids)
                prog = _progress_ref(db, uid, snap.id).get()
                completed = len(set((prog.to_dict() or {}).get("completedLessonIds", [])) & visible_ids) if prog.exists else 0
                percent = round(completed / lesson_count * 100, 1) if lesson_count else 0
                rows.append({
                    "id": snap.id, "title": c.get("title"), "subject": c.get("subject") or "",
                    "className": c.get("className"), "description": c.get("description") or "",
                    "learningModes": c.get("learningModes") or ["Regular"],
                    "audiences": c.get("audiences") or ["Free Regular", "Paid Regular"],
                    "lessonCount": lesson_count, "completedCount": completed, "percent": percent,
                })
            rows.sort(key=lambda x: x["title"] or "")
            return jsonify({"courses": rows}), 200
        except Exception:
            app.logger.exception("Student course list failed")
            return jsonify({"error": "Unable to load your courses."}), 500

    @app.get("/api/student/courses/<course_id>/lessons")
    def student_course_lessons(course_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail["uid"]
            user, entitlement, grade = _user_and_entitlement(db, uid, detail)
            course_snap = db.collection("courses").document(course_id).get()
            if not course_snap.exists:
                return jsonify({"error": "Course not found."}), 404
            c = course_snap.to_dict() or {}
            # V31.108 Course/Unit/Lesson Foundation: this route used to apply
            # only the Learning Mode/Audience check, so a student could open
            # any grade's course (or an unpublished one) by id even though
            # /api/student/courses never listed it and .../complete refused
            # it. Same rules as those two routes now: grade (skipped only
            # when the student has no grade on file, exactly like the list
            # route) and published status.
            course_grade = str(c.get("className") or c.get("grade") or "").strip()
            if grade and course_grade != grade:
                return jsonify({"error": "You do not have access to this course."}), 403
            if str(c.get("status") or "").lower() != "published":
                return jsonify({"error": "This course is not available."}), 403
            if not content_target_allowed(user, c, entitlement):
                return jsonify({"error": "You do not have access to this course."}), 403
            prog_snap = _progress_ref(db, uid, course_id).get()
            completed_ids = set((prog_snap.to_dict() or {}).get("completedLessonIds", [])) if prog_snap.exists else set()
            # V31.108 Course Structure Completion: Unit layer added between
            # Course and Lesson. Additive only - "units" is a new field in
            # this response, and each lesson gains "unitId" (None for every
            # lesson created before this change, or one with no unit set,
            # meaning it is ungrouped within the course). completedIds/
            # percent math below is unchanged and still counts every
            # published lesson regardless of unit.
            unit_map = _unit_map(db, course_id)
            units = []
            for unit_id, u in unit_map.items():
                if u.get("status") != "published":
                    continue
                units.append({"id": unit_id, "title": u.get("title"), "description": u.get("description") or "", "order": u.get("order", 0)})
            units.sort(key=lambda x: (x["order"], x["title"] or ""))
            lessons = []
            # Lessons inside an unpublished unit are not returned (nor
            # completable, nor counted) - see _visible_lessons().
            for lesson_id, l in _visible_lessons(db, course_id, unit_map):
                row = {
                    "id": lesson_id, "title": l.get("title"), "contentType": l.get("contentType"),
                    "url": _safe_legacy_url(l.get("url")), "description": l.get("description") or "",
                    "order": l.get("order", 0), "completed": lesson_id in completed_ids,
                    "unitId": l.get("unitId"),
                }
                # V31.108 Interactive Lesson Builder: block lessons carry
                # hasBlocks=True (open via /api/student/lessons/<id>). The key is
                # deliberately ABSENT for every pre-existing lesson so the legacy
                # response shape is byte-for-byte unchanged.
                if l.get("blocks"):
                    row["hasBlocks"] = True
                lessons.append(row)
            lessons.sort(key=lambda x: (x["order"], x["title"] or ""))
            return jsonify({
                "course": {"id": course_id, "title": c.get("title"), "subject": c.get("subject") or "", "className": c.get("className")},
                "units": units,
                "lessons": lessons,
                # "Continue learning": first incomplete lesson in display order,
                # None when every visible lesson is complete (or there are none).
                "nextLessonId": _next_lesson_id(lessons, units),
            }), 200
        except Exception:
            app.logger.exception("Student course lessons load failed")
            return jsonify({"error": "Unable to load this course."}), 500

    @app.post("/api/student/lessons/<lesson_id>/complete")
    def student_complete_lesson(lesson_id):
        """Self-paced completion: the student marks a lesson watched/read.
        When every published lesson in the course is complete, the course is
        marked completed for this student (feeds the existing
        completedStudents array progress_routes.py already reads) and a
        certificate is issued automatically, once per student per course."""
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail["uid"]
            lesson_snap = db.collection("lessons").document(lesson_id).get()
            if not lesson_snap.exists:
                return jsonify({"error": "Lesson not found."}), 404
            lesson = lesson_snap.to_dict() or {}
            course_id = lesson.get("courseId")
            course_snap = db.collection("courses").document(course_id).get()
            if not course_snap.exists:
                return jsonify({"error": "Course not found."}), 404
            c = course_snap.to_dict() or {}
            user, entitlement, grade = _user_and_entitlement(db, uid, detail)
            if str(c.get('className') or c.get('grade') or '').strip() != grade:
                return jsonify({"error": "You do not have access to this course."}), 403
            if str(c.get('status') or '').lower() != 'published':
                return jsonify({"error": "This course is not available."}), 403
            if str(lesson.get('status') or '').lower() != 'published':
                return jsonify({"error": "This lesson is not available."}), 403
            unit_map = _unit_map(db, course_id)
            if not _unit_is_open(unit_map, lesson.get('unitId')):
                return jsonify({"error": "This lesson is not available."}), 403
            if not content_target_allowed(user, c, entitlement):
                return jsonify({"error": "You do not have access to this course."}), 403

            now = datetime.now(timezone.utc)
            ref = _progress_ref(db, uid, course_id)
            snap = ref.get()
            data = snap.to_dict() or {} if snap.exists else {}
            completed_ids = set(data.get("completedLessonIds", []))
            completed_ids.add(lesson_id)
            # Every stored completion is kept (re-publishing a lesson restores
            # it), but progress only counts completions of lessons a student
            # can currently see - otherwise unpublishing a completed lesson
            # would push the percentage above 100.
            visible_ids = {lid for lid, _l in _visible_lessons(db, course_id, unit_map)}
            total = len(visible_ids)
            completed_visible = len(completed_ids & visible_ids)
            percent = round(completed_visible / total * 100, 1) if total else 0
            payload = {
                "studentUid": uid, "courseId": course_id,
                "completedLessonIds": list(completed_ids), "percent": percent,
                "lastActivityAt": now,
            }
            if not snap.exists:
                payload["startedAt"] = now
            course_completed_now = False
            certificate = None
            if total and completed_visible >= total and not data.get("completedAt"):
                payload["completedAt"] = now
                course_completed_now = True
            ref.set(payload, merge=True)

            if course_completed_now:
                from firebase_admin import firestore
                db.collection("courses").document(course_id).set(
                    {"completedStudents": firestore.ArrayUnion([uid])}, merge=True
                )
                cert_ref = db.collection("courseCertificates").document(f"{uid}_{course_id}")
                if not cert_ref.get().exists:
                    certificate = {
                        "certificateId": _certificate_id(uid, course_id),
                        "studentUid": uid, "courseId": course_id,
                        "courseTitle": c.get("title") or "Course", "issuedAt": now,
                    }
                    cert_ref.set(certificate)

            return jsonify({
                "success": True, "completedCount": completed_visible, "totalLessons": total,
                "percent": percent, "courseCompleted": bool(total and completed_visible >= total),
                "certificateIssued": bool(certificate),
            }), 200
        except Exception:
            app.logger.exception("Student lesson completion failed")
            return jsonify({"error": "Unable to update your progress."}), 500

    @app.get("/api/student/certificates")
    def student_list_certificates():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(); uid = detail["uid"]
            rows = []
            for snap in db.collection("courseCertificates").where("studentUid", "==", uid).stream():
                x = snap.to_dict() or {}
                rows.append({
                    "certificateId": x.get("certificateId"), "courseId": x.get("courseId"),
                    "courseTitle": x.get("courseTitle"), "issuedAt": _iso(x.get("issuedAt")),
                })
            rows.sort(key=lambda x: x.get("issuedAt") or "", reverse=True)
            return jsonify({"certificates": rows}), 200
        except Exception:
            app.logger.exception("Student certificate list failed")
            return jsonify({"error": "Unable to load your certificates."}), 500
