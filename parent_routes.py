"""Server-authoritative parent portal and admin parent-child linking."""
from datetime import datetime, timezone
from flask import jsonify, request


def register_parent_routes(app, require_user, firebase_admin_factory, require_admin=None):
    def db():
        fb = firebase_admin_factory()
        if not fb:
            raise RuntimeError("Firebase server credentials are not configured.")
        from firebase_admin import firestore
        return firestore.client()

    def iso(value):
        return value.isoformat() if hasattr(value, "isoformat") else value

    def user_record(database, uid):
        snap = database.collection("users").document(uid).get()
        return (snap.to_dict() or {}) if snap.exists else None

    def account_type(database, uid):
        data = user_record(database, uid) or {}
        return str(data.get("accountType") or "").lower()

    def verified_child(database, parent_uid, child_uid):
        docs = database.collection("parentChildLinks").where("parentUid", "==", parent_uid).limit(100).stream()
        return any((d.to_dict() or {}).get("childUid") == child_uid and (d.to_dict() or {}).get("status") == "verified" for d in docs)

    def child_profile(database, child_uid):
        data = user_record(database, child_uid) or {}
        return {
            "id": child_uid,
            "name": data.get("displayName") or data.get("name") or data.get("fullName") or "Student",
            "grade": str(data.get("className") or data.get("class") or data.get("grade") or ""),
            "className": str(data.get("className") or data.get("class") or ""),
            "photoURL": data.get("photoURL") or data.get("photoUrl") or "",
        }

    def analytics(database, child_uid):
        snap = database.collection("studentAnalytics").document(child_uid).get()
        return (snap.to_dict() or {}) if snap.exists else {}

    def require_parent():
        ok, detail = require_user()
        if not ok:
            return ok, detail
        try:
            database = db()
            role = account_type(database, detail.get("uid", ""))
            if role and role != "parent":
                return False, jsonify({"error": "Parent access required."}), 403
        except RuntimeError:
            return False, jsonify({"error": "Parent service is temporarily unavailable."}), 503
        except Exception:
            app.logger.exception("Parent role check failed")
            return False, jsonify({"error": "Unable to verify parent account."}), 500
        return True, detail

    @app.get("/api/parent/children")
    def parent_children():
        ok, detail = require_parent()
        if not ok:
            return detail if len((detail if isinstance(detail, tuple) else (detail,))) == 1 else detail
        try:
            database = db(); parent_uid = detail.get("uid", "")
            links = list(database.collection("parentChildLinks").where("parentUid", "==", parent_uid).limit(100).stream())
            children=[]; seen=set()
            for link in links:
                data=link.to_dict() or {}; child_uid=str(data.get("childUid", "")).strip()
                if not child_uid or child_uid in seen: continue
                seen.add(child_uid); profile=child_profile(database, child_uid); a=analytics(database, child_uid)
                children.append({**profile,"riskLevel":a.get("riskLevel") or "UNKNOWN","courseProgress":a.get("courseProgress"),"assessmentAverage":a.get("assessmentAverage"),"attendanceRate":a.get("attendanceRate"),"assignmentCompletion":a.get("assignmentCompletion"),"updatedAt":iso(a.get("updatedAt"))})
            return jsonify({"success":True,"children":children})
        except RuntimeError:
            return jsonify({"error":"Parent service is temporarily unavailable."}),503
        except Exception:
            app.logger.exception("Parent children lookup failed"); return jsonify({"error":"Unable to load linked students."}),500

    def require_child(parent_uid, child_uid):
        database=db()
        if not verified_child(database,parent_uid,child_uid): return None,jsonify({"error":"You are not authorized to access this student."}),403
        return database,None,None

    @app.get("/api/parent/children/<child_id>/progress")
    def parent_child_progress(child_id):
        ok, detail=require_parent()
        if not ok: return detail
        try:
            database,error,status=require_child(detail.get("uid",""),child_id)
            if error:return error,status
            a=analytics(database,child_id)
            return jsonify({"success":True,"child":child_profile(database,child_id),"progress":{"courseProgress":a.get("courseProgress",{}),"lessonCompletion":a.get("lessonCompletion"),"assignmentCompletion":a.get("assignmentCompletion"),"attendanceRate":a.get("attendanceRate")},"updatedAt":iso(a.get("updatedAt"))})
        except Exception:
            app.logger.exception("Parent progress lookup failed"); return jsonify({"error":"Unable to load student progress."}),500

    @app.get("/api/parent/children/<child_id>/analytics")
    def parent_child_analytics(child_id):
        ok, detail=require_parent()
        if not ok:return detail
        try:
            database,error,status=require_child(detail.get("uid",""),child_id)
            if error:return error,status
            a=analytics(database,child_id)
            return jsonify({"success":True,"child":child_profile(database,child_id),"analytics":{"courseProgress":a.get("courseProgress"),"assessmentAverage":a.get("assessmentAverage"),"attendanceRate":a.get("attendanceRate"),"assignmentCompletion":a.get("assignmentCompletion"),"topicMastery":a.get("topicMastery",{}),"riskLevel":a.get("riskLevel","UNKNOWN"),"recentActivity":a.get("recentActivity",[])},"updatedAt":iso(a.get("updatedAt"))})
        except Exception:
            app.logger.exception("Parent analytics lookup failed"); return jsonify({"error":"Unable to load student analytics."}),500

    @app.get("/api/parent/children/<child_id>/assessments")
    def parent_child_assessments(child_id):
        ok, detail=require_parent()
        if not ok:return detail
        try:
            database,error,status=require_child(detail.get("uid",""),child_id)
            if error:return error,status
            rows=[]
            docs=database.collection("examAttempts").where("userId","==",child_id).limit(200).stream()
            for snap in docs:
                x=snap.to_dict() or {}
                if x.get("status") != "submitted":
                    continue
                total=float(x.get("totalPoints") or x.get("total") or 0); score=float(x.get("score") or 0); pct=x.get("percentage")
                if pct is None:pct=round(score/total*100,1) if total else 0
                rows.append({"id":snap.id,"title":x.get("examTitle") or x.get("title") or "Assessment","percentage":float(pct),"submittedAt":iso(x.get("submittedAt"))})
            rows.sort(key=lambda x:x.get("submittedAt") or "",reverse=True)
            return jsonify({"success":True,"assessments":rows[:50]})
        except Exception:
            app.logger.exception("Parent assessment lookup failed"); return jsonify({"error":"Unable to load assessments."}),500

    @app.get("/api/parent/children/<child_id>/exams")
    def parent_child_exams(child_id):
        """Server-authoritative exam results for a verified parent-child link."""
        ok, detail = require_parent()
        if not ok:
            return detail
        try:
            database, error, status = require_child(detail.get("uid", ""), child_id)
            if error:
                return error, status
            rows = []
            docs = database.collection("examAttempts").where("userId", "==", child_id).where("status", "==", "submitted").limit(200).stream()
            for snap in docs:
                x = snap.to_dict() or {}
                rows.append({
                    "id": snap.id,
                    "examId": x.get("examId"),
                    "title": x.get("examTitle") or "Exam",
                    "score": x.get("score", 0),
                    "totalPoints": x.get("totalPoints", 0),
                    "percentage": x.get("percentage", 0),
                    "passMark": x.get("passMark", 50),
                    "status": x.get("statusResult") or ("PASS" if float(x.get("percentage", 0) or 0) >= float(x.get("passMark", 50) or 50) else "FAIL"),
                    "weakTopics": x.get("weakTopics") or [],
                    "submittedAt": iso(x.get("submittedAt")),
                })
            rows.sort(key=lambda row: row.get("submittedAt") or "", reverse=True)
            return jsonify({"success": True, "results": rows[:100]})
        except Exception:
            app.logger.exception("Parent exam results lookup failed")
            return jsonify({"error": "Unable to load child exam results."}), 500

    @app.post("/api/admin/parent-links")
    def admin_parent_link():
        if require_admin is None:
            return jsonify({"error":"Admin linking is unavailable."}),503
        ok, detail=require_admin()
        if not ok:return detail
        try:
            body=request.get_json(silent=True) or {}
            parent_email=str(body.get("parentEmail") or "").strip().lower(); student_email=str(body.get("studentEmail") or "").strip().lower()
            if not parent_email or not student_email:return jsonify({"error":"Parent email and student email are required."}),400
            database=db(); users=list(database.collection("users").where("email","in",[parent_email,student_email]).limit(20).stream())
            by_email={str((x.to_dict() or {}).get("email","")).lower():x for x in users}
            parent=by_email.get(parent_email); student=by_email.get(student_email)
            if not parent or not student:return jsonify({"error":"Parent or student account was not found."}),404
            pd=parent.to_dict() or {}; sd=student.to_dict() or {}
            if pd.get("accountType")!="parent":return jsonify({"error":"The parent account is not a parent account."}),409
            if sd.get("accountType") not in {None,"","student"}:return jsonify({"error":"The selected child is not a student account."}),409
            parent_uid=parent.id; child_uid=student.id
            existing=[x for x in database.collection("parentChildLinks").where("parentUid","==",parent_uid).limit(100).stream() if (x.to_dict() or {}).get("childUid") == child_uid][:1]
            now=datetime.now(timezone.utc)
            if existing:
                existing[0].reference.update({"status":"verified","verifiedAt":now,"verifiedBy":detail.get("uid")})
                link_id=existing[0].id
            else:
                ref=database.collection("parentChildLinks").document(); ref.set({"parentUid":parent_uid,"childUid":child_uid,"status":"verified","createdAt":now,"verifiedAt":now,"verifiedBy":detail.get("uid")}); link_id=ref.id
            return jsonify({"success":True,"linkId":link_id,"parentUid":parent_uid,"childUid":child_uid}),201
        except RuntimeError:return jsonify({"error":"Firebase server credentials are not configured."}),503
        except Exception:
            app.logger.exception("Admin parent-child link failed"); return jsonify({"error":"Unable to link parent and student."}),500
