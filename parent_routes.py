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
        # Every caller does `ok, detail = require_parent()`, so this must always
        # return exactly 2 items - when ok is False, `detail` itself needs to be
        # a valid Flask return value (a (body, status) tuple), matching the
        # convention require_user()/_require_admin_bearer() already use.
        # (Previously the three failure branches below returned 3 items each,
        # which crashed the 2-item unpack at every call site with an
        # unhandled ValueError - turning an intended 403/503/500 JSON error
        # into a generic unhandled 500 for any signed-in non-parent account.)
        ok, detail = require_user()
        if not ok:
            return ok, detail
        try:
            database = db()
            role = account_type(database, detail.get("uid", ""))
            if role and role != "parent":
                return False, (jsonify({"error": "Parent access required."}), 403)
        except RuntimeError:
            return False, (jsonify({"error": "Parent service is temporarily unavailable."}), 503)
        except Exception:
            app.logger.exception("Parent role check failed")
            return False, (jsonify({"error": "Unable to verify parent account."}), 500)
        return True, detail

    @app.get("/api/parent/children")
    def parent_children():
        ok, detail = require_parent()
        if not ok:
            return detail
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


    # ------------------------------------------------------------------
    # V31.78 parent-child connection, private family/teacher messaging,
    # and parent learning progress. All access is server-authoritative.
    # ------------------------------------------------------------------
    def clean(value, limit=5000):
        return str(value or '').strip()[:limit]

    def relationship_allowed(database, uid, other_uid):
        if not uid or not other_uid or uid == other_uid:
            return False
        a=account_type(database, uid); b=account_type(database, other_uid)
        pair={a,b}
        if pair == {'parent','student'}:
            parent_uid = uid if a=='parent' else other_uid
            child_uid = other_uid if a=='parent' else uid
            return verified_child(database,parent_uid,child_uid)
        if pair == {'parent','teacher'}:
            parent_uid = uid if a=='parent' else other_uid
            teacher_uid = other_uid if a=='parent' else uid
            links=database.collection('parentChildLinks').where('parentUid','==',parent_uid).where('status','==','verified').limit(100).stream()
            child_ids=[str((x.to_dict() or {}).get('childUid','')) for x in links]
            for child_uid in child_ids:
                for collection_name in ('courses','assignments','exams','liveClasses'):
                    try:
                        q=database.collection(collection_name).where('teacherUid','==',teacher_uid).limit(200).stream()
                        if any(str((x.to_dict() or {}).get('className') or '') == str((user_record(database,child_uid) or {}).get('className') or (user_record(database,child_uid) or {}).get('class') or '') for x in q):
                            return True
                    except Exception:
                        continue
            return False
        return False

    def teacher_contacts_for_child(database, child_uid):
        profile=user_record(database,child_uid) or {}
        grade=str(profile.get('className') or profile.get('class') or profile.get('grade') or '')
        out={}
        for collection_name in ('courses','assignments','exams','liveClasses'):
            try:
                docs=database.collection(collection_name).where('className','==',grade).limit(200).stream()
                for snap in docs:
                    d=snap.to_dict() or {}; tid=str(d.get('teacherUid') or '').strip()
                    if tid and tid not in out:
                        td=user_record(database,tid) or {}; out[tid]={'uid':tid,'name':td.get('name') or td.get('displayName') or d.get('teacherEmail') or 'Teacher','role':'teacher'}
            except Exception:
                pass
        return list(out.values())[:50]

    @app.get('/api/student/parent-link')
    def student_parent_link():
        ok,detail=require_user()
        if not ok:return detail
        try:
            database=db(); uid=str(detail.get('uid') or ''); role=account_type(database,uid)
            if role!='student': return jsonify({'error':'Student access required.'}),403
            profile=user_record(database,uid) or {}
            code=str(profile.get('parentLinkCode') or '').strip()
            if not code:
                import secrets
                alphabet='ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
                for _ in range(10):
                    candidate='BMT-' + ''.join(secrets.choice(alphabet) for _ in range(8))
                    exists=list(database.collection('users').where('parentLinkCode','==',candidate).limit(1).stream())
                    if not exists: code=candidate; break
                if not code:return jsonify({'error':'Could not create a link code.'}),500
                database.collection('users').document(uid).set({'parentLinkCode':code,'updatedAt':datetime.now(timezone.utc)},merge=True)
            return jsonify({'success':True,'code':code})
        except Exception:
            app.logger.exception('Student parent link code failed'); return jsonify({'error':'Unable to load your parent link code.'}),500

    @app.post('/api/student/parent-link/regenerate')
    def regenerate_student_parent_link():
        ok,detail=require_user()
        if not ok:return detail
        try:
            database=db(); uid=str(detail.get('uid') or '')
            if account_type(database,uid)!='student': return jsonify({'error':'Student access required.'}),403
            import secrets
            alphabet='ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
            code='BMT-' + ''.join(secrets.choice(alphabet) for _ in range(10))
            database.collection('users').document(uid).set({'parentLinkCode':code,'updatedAt':datetime.now(timezone.utc)},merge=True)
            return jsonify({'success':True,'code':code})
        except Exception:
            app.logger.exception('Parent link regeneration failed'); return jsonify({'error':'Unable to regenerate the link code.'}),500

    @app.post('/api/parent/link-by-code')
    def parent_link_by_code():
        ok,detail=require_parent()
        if not ok:return detail
        try:
            database=db(); code=clean((request.get_json(silent=True) or {}).get('code'),32).upper()
            if not code:return jsonify({'error':'Enter the child Parent Link Code.'}),400
            matches=list(database.collection('users').where('parentLinkCode','==',code).limit(2).stream())
            if len(matches)!=1:return jsonify({'error':'That Parent Link Code is invalid or expired.'}),404
            child=matches[0]; child_uid=child.id; child_data=child.to_dict() or {}
            if account_type(database,child_uid)!='student':return jsonify({'error':'This code does not belong to a student account.'}),409
            parent_uid=str(detail.get('uid') or '')
            existing=[x for x in database.collection('parentChildLinks').where('parentUid','==',parent_uid).limit(100).stream() if (x.to_dict() or {}).get('childUid')==child_uid][:1]
            now=datetime.now(timezone.utc)
            if existing: existing[0].reference.update({'status':'verified','verifiedAt':now})
            else: database.collection('parentChildLinks').document().set({'parentUid':parent_uid,'childUid':child_uid,'status':'verified','createdAt':now,'verifiedAt':now,'source':'student-parent-code'})
            return jsonify({'success':True,'child':{'id':child_uid,'name':child_data.get('name') or child_data.get('displayName') or 'Student','grade':child_data.get('className') or child_data.get('class') or ''}}),201
        except Exception:
            app.logger.exception('Parent link by code failed'); return jsonify({'error':'Unable to connect this child.'}),500

    @app.get('/api/student/contacts')
    def student_contacts():
        ok,detail=require_user()
        if not ok:return detail
        try:
            database=db(); uid=str(detail.get('uid') or '');
            if account_type(database,uid)!='student':return jsonify({'error':'Student access required.'}),403
            contacts=[]
            for link in database.collection('parentChildLinks').where('childUid','==',uid).where('status','==','verified').limit(50).stream():
                pid=str((link.to_dict() or {}).get('parentUid') or ''); pd=user_record(database,pid) or {}
                if pid:contacts.append({'uid':pid,'name':pd.get('name') or pd.get('displayName') or 'Parent','role':'parent'})
            for t in teacher_contacts_for_child(database,uid): contacts.append(t)
            seen=set(); unique=[]
            for c in contacts:
                if c['uid'] not in seen:seen.add(c['uid']);unique.append(c)
            return jsonify({'success':True,'contacts':unique[:80]})
        except Exception:
            app.logger.exception('Student contacts failed');return jsonify({'error':'Unable to load private contacts.'}),500

    @app.get('/api/teacher/contacts')
    def teacher_contacts():
        ok,detail=require_user()
        if not ok:return detail
        try:
            database=db(); uid=str(detail.get('uid') or '')
            if account_type(database,uid)!='teacher':return jsonify({'error':'Teacher access required.'}),403
            grades=set()
            for collection_name in ('courses','assignments','exams','liveClasses'):
                try:
                    for snap in database.collection(collection_name).where('teacherUid','==',uid).limit(300).stream():
                        d=snap.to_dict() or {}; g=str(d.get('className') or d.get('grade') or '').strip();
                        if g:grades.add(g)
                except Exception:
                    app.logger.exception("Could not load teacher class grades for parent contacts")
            contacts=[];seen=set()
            if grades:
                links=database.collection('parentChildLinks').where('status','==','verified').limit(500).stream()
                for link in links:
                    d=link.to_dict() or {}; child_uid=str(d.get('childUid') or ''); parent_uid=str(d.get('parentUid') or '')
                    cd=user_record(database,child_uid) or {}; grade=str(cd.get('className') or cd.get('class') or cd.get('grade') or '')
                    if parent_uid and grade in grades and parent_uid not in seen:
                        seen.add(parent_uid);pd=user_record(database,parent_uid) or {};contacts.append({'uid':parent_uid,'name':pd.get('name') or pd.get('displayName') or 'Parent','role':'parent','childUid':child_uid})
            return jsonify({'success':True,'contacts':contacts[:100]})
        except Exception:
            app.logger.exception('Teacher contacts failed');return jsonify({'error':'Unable to load parent contacts.'}),500

    @app.get('/api/parent/contacts')
    def parent_contacts():
        ok,detail=require_parent()
        if not ok:return detail
        try:
            database=db(); parent_uid=str(detail.get('uid') or ''); contacts=[]
            links=list(database.collection('parentChildLinks').where('parentUid','==',parent_uid).where('status','==','verified').limit(100).stream())
            seen=set()
            for link in links:
                child_uid=str((link.to_dict() or {}).get('childUid') or '')
                if not child_uid or child_uid in seen:continue
                seen.add(child_uid); cd=user_record(database,child_uid) or {}
                contacts.append({'uid':child_uid,'name':cd.get('name') or cd.get('displayName') or 'Student','role':'student','childUid':child_uid})
                for teacher in teacher_contacts_for_child(database,child_uid): contacts.append({**teacher,'childUid':child_uid})
            unique=[]; keys=set()
            for c in contacts:
                key=(c['uid'],c['role'])
                if key not in keys:keys.add(key);unique.append(c)
            return jsonify({'success':True,'contacts':unique})
        except Exception:
            app.logger.exception('Parent contacts failed'); return jsonify({'error':'Unable to load private contacts.'}),500

    @app.get('/api/messages/<other_uid>')
    def direct_messages(other_uid):
        ok,detail=require_user()
        if not ok:return detail
        try:
            database=db(); uid=str(detail.get('uid') or ''); other_uid=clean(other_uid,128)
            if not relationship_allowed(database,uid,other_uid):return jsonify({'error':'Private messaging is not available for this relationship.'}),403
            docs=database.collection('directMessages').where('participants','array_contains',uid).limit(200).stream()
            rows=[]
            for snap in docs:
                d=snap.to_dict() or {}
                if other_uid not in (d.get('participants') or []):continue
                rows.append({'id':snap.id,'senderUid':d.get('senderUid'),'senderName':d.get('senderName'),'recipientUid':d.get('recipientUid'),'message':d.get('message') or '','createdAt':iso(d.get('createdAt'))})
            rows.sort(key=lambda x:x.get('createdAt') or '')
            return jsonify({'success':True,'messages':rows[-100:]})
        except Exception:
            app.logger.exception('Direct messages lookup failed'); return jsonify({'error':'Unable to load messages.'}),500

    @app.post('/api/messages/<other_uid>')
    def send_direct_message(other_uid):
        ok,detail=require_user()
        if not ok:return detail
        try:
            database=db(); uid=str(detail.get('uid') or ''); other_uid=clean(other_uid,128); body=request.get_json(silent=True) or {}; message=clean(body.get('message'),3000)
            if not relationship_allowed(database,uid,other_uid):return jsonify({'error':'Private messaging is not available for this relationship.'}),403
            if not message:return jsonify({'error':'Message cannot be empty.'}),400
            sender=user_record(database,uid) or {}; recipient=user_record(database,other_uid) or {}; now=datetime.now(timezone.utc)
            ref=database.collection('directMessages').document(); ref.set({'senderUid':uid,'senderName':sender.get('name') or sender.get('displayName') or detail.get('email') or 'BMT User','recipientUid':other_uid,'recipientName':recipient.get('name') or recipient.get('displayName') or 'BMT User','participants':[uid,other_uid],'message':message,'createdAt':now})
            return jsonify({'success':True,'message':{'id':ref.id,'senderUid':uid,'senderName':sender.get('name') or sender.get('displayName') or 'BMT User','recipientUid':other_uid,'message':message,'createdAt':iso(now)}}),201
        except Exception:
            app.logger.exception('Direct message send failed'); return jsonify({'error':'Unable to send message.'}),500

    @app.get('/api/parent/learning')
    def parent_learning_get():
        ok,detail=require_parent()
        if not ok:return detail
        try:
            database=db(); uid=str(detail.get('uid') or ''); snap=database.collection('parentLearning').document(uid).get(); return jsonify({'success':True,'completed':(snap.to_dict() or {}).get('completed',[]) if snap.exists else []})
        except Exception:
            app.logger.exception('Parent learning load failed'); return jsonify({'error':'Unable to load learning progress.'}),500

    @app.post('/api/parent/learning')
    def parent_learning_save():
        ok,detail=require_parent()
        if not ok:return detail
        try:
            database=db(); uid=str(detail.get('uid') or ''); item=clean((request.get_json(silent=True) or {}).get('item'),120)
            if not item:return jsonify({'error':'Learning item is required.'}),400
            snap=database.collection('parentLearning').document(uid).get(); current=(snap.to_dict() or {}).get('completed',[]) if snap.exists else []
            if item not in current:current.append(item)
            database.collection('parentLearning').document(uid).set({'completed':current[-200:],'updatedAt':datetime.now(timezone.utc)},merge=True); return jsonify({'success':True,'completed':current[-200:]})
        except Exception:
            app.logger.exception('Parent learning save failed'); return jsonify({'error':'Unable to save learning progress.'}),500

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


    # Student Development Center: character, self-development, emotional/social and life skills.
    DEVELOPMENT_CATALOG = [
        {'id':'character-ethics','title':'🌟 ስነምግባር','lessons':[
            {'id':'truth-trust','title':'እውነተኝነት፣ መተማመንና ኃላፊነት','watch':'5–10 ደቂቃ ትምህርት፦ እውነተኛ መሆን መተማመንን የሚገነባው እንዴት እንደሆነ።','read':'እውነተኝነት ማለት እውነትን መናገር፣ ቃል መጠበቅ እና ስህተትን አምኖ ኃላፊነት መውሰድ ማለት ነው።','think':'እውነትን መናገር የሚከብድህ/ሽ መቼ ነው? ምን ማድረግ ትችላለህ/ትችያለሽ?','practice':'በቤት ወይም በትምህርት ቤት አንድ ኃላፊነት ምረጥ/ጭ እና ማንም ሳያስታውስህ/ሽ ራስህን/ሽን ብቻ ተጠቅመህ/ሽ ጨርስ/ሺው።','quiz':['የትኛው ተግባር መተማመንን ይገነባል?','ስህተት ከሰራህ/ሽ በኋላ ምን ማድረግ አለብህ/ሽ?'],'badge':'እውነተኝነት'},
            {'id':'respect-empathy','title':'አክብሮት፣ ርህራሄና ሌሎችን መርዳት','watch':'5–10 ደቂቃ ትምህርት፦ ሰዎችን፣ ልዩነትንና ወሰንን ማክበር።','read':'አክብሮት ወላጆችን፣ መምህራንን፣ የክፍል ጓደኞችንና የማህበረሰብ አባላትን ማክበርን ይጨምራል። ርህራሄ ማለት የሌላውን ሰው ስሜት ለመረዳት መሞከር ማለት ነው።','think':'ስህተት ስትሰራ/ሪ ሌሎች እንዴት እንዲይዙህ/ሽ ትፈልጋለህ/ትፈልጊያለሽ?','practice':'ዛሬ አንድ አጋዥ ተግባር አድርግ/ጊ እና ለአንድ ሰው በጥሞና አዳምጥ/ጪ።','quiz':['ርህራሄ ማለት ምን ማለት ነው?','የትኛው ምላሽ አክብሮትን ያሳያል?'],'badge':'ደግነት ገንቢ'},
            {'id':'fairness-patience','title':'ፍትሃዊነት፣ ትዕግስትና ራስን መቆጣጠር','watch':'5–10 ደቂቃ ትምህርት፦ ትንሽ ቁም፣ አስብ እና ፍትሃዊ ምላሽ ምረጥ።','read':'ፍትሃዊነት ማለት ደንቦችን ለሁሉም እኩል መተግበር ማለት ነው፤ ትዕግስት ደግሞ ወዲያውኑ ከመቆጣት ይልቅ በጥንቃቄ እንድንመልስ ይረዳናል።','think':'የሆነ ነገር ፍትሃዊ ያልሆነ ሲመስልህ/ሽ ምን ማድረግ ትችላለህ/ትችያለሽ?','practice':'ለአንድ አስቸጋሪ ሁኔታ ምላሽ ከመስጠትህ/ሽ በፊት ትንሽ ቁም/ቁሚ እና አስብ/ቢ የሚለውን ዘዴ ተጠቀም/ሚ።','quiz':['ፍትሃዊ ውሳኔን የሚደግፈው ምንድን ነው?','ትዕግስት ጠቃሚ የሆነው ለምንድን ነው?'],'badge':'ፍትሃዊ አስተሳሰብ'},
            {'id':'time-community','title':'ሰዓት አክባሪነትና የዕለት ተዕለት ስነምግባር','watch':'5–10 ደቂቃ ትምህርት፦ በቤት፣ በትምህርት ቤትና በማህበረሰብ ውስጥ ስነምግባራዊ ምርጫዎች።','read':'በሰዓቱ መገኘት፣ የጋራ ቦታዎችን መንከባከብ እና የተስማሙባቸውን ደንቦች መከተል የኃላፊነት ተግባራዊ መገለጫዎች ናቸው።','think':'ዘግይቶ መገኘት ሌሎችን የሚጎዳው በምን መንገድ ነው?','practice':'ነገ በሰዓቱ ለመድረስ እና ለማህበረሰብ የሚጠቅም አንድ ተግባር ለማከናወን አቅድ/ጂ።','quiz':['ሰዓት አክባሪነት አስፈላጊ የሆነው ለምንድን ነው?','የትኛው የማህበረሰብ ኃላፊነት ነው?'],'badge':'ኃላፊነት የሚሰማው ዜጋ'}]},
        {'id':'self-development','title':'🚀 ራስን ማሳደግ','lessons':[
            {'id':'self-awareness-goals','title':'ራስን ማወቅና ግብ ማውጣት','watch':'5–10 ደቂቃ ትምህርት፦ ጥንካሬዎችህን/ሽን ማወቅና ጠቃሚ ግቦችን መምረጥ።','read':'ራስን ማወቅ ጥንካሬዎችን፣ ተግዳሮቶችን፣ ፍላጎቶችንና ልማዶችን እንድታስተውል/ጪ ይረዳል። ጥሩ ግብ የተለየ፣ እውን ሊሆን የሚችልና በቁጥር የሚለካ ነው።','think':'አንድን ተግዳሮት ለማሻሻል መጠቀም የምትችለው/የምትችይው ጥንካሬ ምንድን ነው?','practice':'የአንድ ሳምንት ግብ እና ወደ እሱ የሚያደርሱ የመጀመሪያዎቹን ሶስት እርምጃዎች ጻፍ/ፊ።','quiz':['ራስን ማወቅ ምን እንድታስተውል/ጪ ይረዳል?','ግብን ጠቃሚ የሚያደርገው ምንድን ነው?'],'badge':'ግብ ጀማሪ'},
            {'id':'time-study','title':'ጊዜ አጠቃቀምና የጥናት ክህሎት','watch':'5–10 ደቂቃ ትምህርት፦ ትኩረት ያለው የጥናት ጊዜ ማቀድ።','read':'ስራን ወደ ትናንሽ ተግባራት ክፈል/ዪ፣ ቅድሚያ የሚሰጣቸውን ምረጥ/ጪ እና ትኩረት የሚከፋፍል ነገር በሌለበት የጥናት ጊዜ ተጠቀም/ሚ።','think':'ጊዜህ/ሽ በብዛት የሚጠፋው የት ላይ ነው?','practice':'በየቀኑ አንድ ትኩረት ያለው ክፍለ-ጊዜ ያለው የሰባት ቀን የጥናት እቅድ አዘጋጅ/ጂ።','quiz':['ምንን ቅድሚያ መስጠት አለብህ/ሽ?','አጭርና ትኩረት ያለው ክፍለ-ጊዜ የሚጠቅመው ለምንድን ነው?'],'badge':'የጊዜ ጌታ'},
            {'id':'focus-discipline-confidence','title':'ትኩረት፣ ተግሳፅ እና በራስ መተማመን','watch':'5–10 ደቂቃ ትምህርት፦ በትናንሽ ተግባራት ወጥነት መገንባት።','read':'ትኩረትን የሚከፋፍሉ ነገሮች ሲቀነሱ ትኩረት ይሻሻላል። ተግሳፅ ወደ ግብ ደጋግሞ መስራት ነው፤ በራስ መተማመን ደግሞ በልምምድና በማስተዋል ያድጋል።','think':'ትምህርትህን/ሽን በተደጋጋሚ የሚያቋርጠው ትኩረት የሚከፋፍል ነገር ምንድን ነው?','practice':'ለ20 ደቂቃ ትኩረት ያለው ስራ ስራ/ሪ እና የረዳህን/ሽን ነገር መዝግብ/ቢ።','quiz':['ትኩረትን የሚደግፈው ምንድን ነው?','በራስ መተማመን እንዴት ያድጋል?'],'badge':'ትኩረት ያለው ተማሪ'},
            {'id':'problem-solving-leadership','title':'ችግር መፍታት፣ ውሳኔ አሰጣጥ፣ ግንኙነት፣ አመራርና ፈጠራ','watch':'5–10 ደቂቃ ትምህርት፦ ችግርን መለየት፣ አማራጮችን ማፍለቅ እና በግልጽ መግባባት።','read':'ጠንካራ ችግር-መፍቻ ዘዴ አማራጮችንና ውጤቶቻቸውን ያነጻጽራል። ግንኙነት፣ ፈጠራና አመራር ቡድኖች ጥሩ ሃሳቦችን ተግባራዊ እንዲያደርጉ ይረዳሉ። የስሜት ብልህነት ከሌሎች ጋር በምንሰራበት ጊዜ ስሜቶቻችንን እንድናስተውልና እንድንቆጣጠር ይረዳናል።','think':'ከመወሰንህ/ሽ በፊት ሁለት ሊሆኑ የሚችሉ መፍትሄዎችን ብታስብ/ቢ ምን ይለወጣል?','practice':'ችግርን መለየት→አማራጮች→መምረጥ→መገምገም የሚለውን ዘዴ ተጠቅመህ/ሽ አንድ እውነተኛ ችግር ፍታ/ቺ።','quiz':['ችግር በሚፈታበት ጊዜ ጠቃሚ የመጀመሪያ እርምጃ ምንድን ነው?','ቡድን ወደ የጋራ ግብ እንዲሰራ የሚረዳው ምንድን ነው?'],'badge':'የዕድገት መሪ'}]},
        {'id':'emotional-social','title':'❤️ የስሜትና ማህበራዊ ዕድገት','lessons':[
            {'id':'anger-regulation','title':'ቁጣን መረዳትና ራስን መቆጣጠር','watch':'5–10 ደቂቃ ትምህርት፦ የቁጣ ምልክቶችን ማወቅና በጥንቃቄ ማቆም።','read':'ቁጣ ስሜት ነው፤ ግቡ ደህንነቱ የተጠበቀ ምላሽ መምረጥ ነው። ቁም/ሚ፣ ተንፍስ/ሺ፣ ስሜቱን ጥራው/ሪው እና አስፈላጊ ሲሆን እርዳታ ጠይቅ/ቂ።','think':'እየተቆጣህ/ሽ መሆኑን የሚነግርህ/ሽ ምልክት ምንድን ነው?','practice':'ላንድ የሚያበሳጭ ጊዜ ቁም-ተንፍስ-ስም የሚለውን ዘዴ ተጠቀም/ሚ።','quiz':['ቁጣን በምንቆጣጠርበት ጊዜ ግቡ ምንድን ነው?','ትንሽ ማቆም ምን ይፈጥራል?'],'badge':'የተረጋጋ ምርጫ'},
            {'id':'stress-resilience','title':'ጭንቀትን መቆጣጠርና ጽናትን መገንባት','watch':'5–10 ደቂቃ ትምህርት፦ ጭንቀትን ወደሚተዳደር እርምጃ መቀየር።','read':'ጭንቀት በእቅድ፣ በእረፍት፣ በእንቅስቃሴ፣ በደጋፊ ግንኙነቶች እና አስቸጋሪ ስራዎችን ወደ ትናንሽ ደረጃዎች በመክፈል ሊቀንስ ይችላል።','think':'ከአስቸጋሪ ቀን በኋላ እንድታገግም/ሚ የሚረዳህ/ሽ ጤናማ ዘዴ የትኛው ነው?','practice':'አንድ ጤናማ ተግባር፣ አንድ ደጋፊ ሰውና አንድ ቀጣይ እርምጃ ያለው ትንሽ የጭንቀት እቅድ አዘጋጅ/ጂ።','quiz':['ጤናማ የጭንቀት መቋቋሚያ ዘዴ የትኛው ነው?','ስራን ወደ ደረጃዎች መክፈል የሚጠቅመው ለምንድን ነው?'],'badge':'ጽናት ገንቢ'},
            {'id':'conflict-bullying','title':'አለመግባባትን መፍታትና ከጉልበተኝነት መጠበቅ','watch':'5–10 ደቂቃ ትምህርት፦ አለመግባባትን በሰላም መፍታትና ለጉልበተኝነት እርዳታ መጠየቅ።','read':'የተረጋጋ ንግግር ተጠቀም/ሚ፣ አድምጥ/ጪ እና ፍትሃዊ መፍትሄ ፈልግ/ጊ። ጉልበተኝነት ደጋግሞ የሚደረግ ጎጂ ድርጊት ወይም ከባድ የሃይል ልዩነት ነው፤ ለታመነ አዋቂ ንገር/ሪ እና ደህንነትን ቅድሚያ ስጥ/ጪ።','think':'ሁኔታው ደህንነት የጎደለው ሆኖ ከተሰማህ/ሽ ማንን ማነጋገር ትችላለህ/ትችያለሽ?','practice':'ላለመስማማት የሚያገለግል አንድ አክባሪ ዓረፍተ ነገር ተለማመድ/ጂ እና አንድ የታመነ አዋቂ ለይ/ዪ።','quiz':['ለጉልበተኝነት ደህንነቱ የተጠበቀ ምላሽ ምንድን ነው?','ተራ አለመግባባትን ለመፍታት የሚረዳው ምንድን ነው?'],'badge':'ደህንነቱ የተጠበቀ አጋር'},
            {'id':'empathy-teamwork-relationships','title':'ርህራሄ፣ የቡድን ስራና ጤናማ ግንኙነት','watch':'5–10 ደቂቃ ትምህርት፦ ማዳመጥ፣ መተባበርና ወሰንን ማክበር።','read':'ጤናማ ግንኙነት አክብሮት፣ እውነተኝነት፣ ግንኙነት፣ ስምምነትና ተገቢ ወሰንን ይጨምራል። የቡድን ስራ ማለት ኃላፊነትን መጋራትና ችግርን አብሮ መፍታት ማለት ነው።','think':'አንድን ሰው በጥሞና እንዳዳመጥከው/ሽው እንዴት ማሳየት ትችላለህ/ትችያለሽ?','practice':'ግልጽ ኃላፊነትና አክባሪ ንግግር በመጠቀም የጋራ ስራ ፈጽም/ሚ።','quiz':['ጤናማ ግንኙነትን የሚደግፈው ምንድን ነው?','አንድ የቡድን ስራ ልማድ ምንድን ነው?'],'badge':'የቡድን ገንቢ'}]},
        {'id':'life-skills','title':'🎯 የህይወት ክህሎቶች','lessons':[
            {'id':'financial-literacy','title':'የገንዘብ እውቀት','watch':'5–10 ደቂቃ ትምህርት፦ ፍላጎትና ጉጉት፣ ቁጠባና እቅድ ማውጣት።','read':'የገንዘብ እውቀት አስፈላጊ ነገርን ከጉጉት መለየትን፣ በጀት ማዘጋጀትን፣ መቆጠብንና ስለ ገንዘብ በመረጃ ላይ የተመሰረተ ውሳኔ መስጠትን ይጨምራል።','think':'የትኛው ወጪ አስፈላጊ ነው፣ የትኛውስ ጉጉት ብቻ ነው?','practice':'በምናብ ወይም ለዕድሜህ/ሽ በሚስማማ መጠን ቀላል የሳምንት በጀት አዘጋጅ/ጂ።','quiz':['በጀት ለምንድን ነው?','ገንዘብ የምንቆጥበው ለምንድን ነው?'],'badge':'ብልህ በገንዘብ'},
            {'id':'digital-online-safety','title':'ዲጂታል እውቀትና የኦንላይን ደህንነት','watch':'5–10 ደቂቃ ትምህርት፦ አካውንትን፣ ግላዊነትንና መሳሪያዎችን መጠበቅ።','read':'ጠንካራና ልዩ የይለፍ ቃል ተጠቀም/ሚ፣ የግል መረጃን ጠብቅ/ቂ፣ ሊንኮችን አረጋግጥ/ጪ እና ጎጂ ወይም አጠራጣሪ እንቅስቃሴን ለታመነ አዋቂ ንገር/ሪ።','think':'በይፋ ማጋራት የሌለብህ/ሽ መረጃ ምንድን ነው?','practice':'ከአንድ የታመነ አዋቂ ጋር የአንድ አካውንት ግላዊነት ቅንብር ገምግም/ሚ።','quiz':['አካውንትን የሚጠብቀው ምንድን ነው?','ለአጠራጣሪ መልዕክቶች ምን ማድረግ አለብህ/ሽ?'],'badge':'ዲጂታል ጠባቂ'},
            {'id':'critical-media-literacy','title':'ወሳኝ አስተሳሰብና የመገናኛ ብዙሃን እውቀት','watch':'5–10 ደቂቃ ትምህርት፦ መረጃን ከማጋራትህ/ሽ በፊት መጠየቅ።','read':'የአንድን መረጃ ምንጭ፣ ቀን፣ ማስረጃና ዓላማ አረጋግጥ/ጪ። መረጃን ከመቀበልህ/ሽ ወይም ከማጋራትህ/ሽ በፊት አስተማማኝ ምንጮችን አነጻጽር/ሪ።','think':'አንድን መረጃ ይበልጥ አስተማማኝ የሚያደርገው ምን ማስረጃ ነው?','practice':'አንድን የኦንላይን መረጃ ወስደህ/ሽ ከሁለት አስተማማኝ ምንጮች ጋር አረጋግጥ/ጪ።','quiz':['ስለ አንድ መረጃ ምን ማረጋገጥ አለብህ/ሽ?','ምንጮችን ማነጻጸር የሚጠቅመው ለምንድን ነው?'],'badge':'ወሳኝ አሳቢ'},
            {'id':'communication-responsibility-career','title':'ግንኙነት፣ ኃላፊነትና የወደፊት ስራ','watch':'5–10 ደቂቃ ትምህርት፦ በግልጽ መግባባትና የወደፊት እድሎችን መመልከት።','read':'ግልጽ ግንኙነት፣ ግላዊ ኃላፊነትና ስለ ስራ ማወቅ ስለ ትምህርት ቤት፣ ስራና የማህበረሰብ ህይወት በመረጃ ላይ የተመሰረተ ውሳኔ እንድትሰጥ/ጪ ይረዳሉ።','think':'የትኛው አይነት ስራ ወይም አስተዋፅኦ ያስደስትሃል/ሻል፣ ለምንድን ነው?','practice':'ስለ ጥንካሬዎችህ/ሽ፣ ፍላጎቶችህ/ሽ እና ልትመረምረው ስለምትፈልገው/ጊው አንድ የወደፊት ስራ አጭር መግቢያ ጻፍ/ፊ።','quiz':['ግልጽ ግንኙነትን የሚደግፈው ምንድን ነው?','ስራዎችን ገና ከወዲሁ መመልከት የሚጠቅመው ለምንድን ነው?'],'badge':'ለወደፊት ዝግጁ'}]}
    ]
    _DEV_INDEX = {l['id']:(cat['id'],cat['title'],l) for cat in DEVELOPMENT_CATALOG for l in cat['lessons']}

    def _development_payload(database, uid):
        snap=database.collection('studentDevelopment').document(uid).get()
        data=(snap.to_dict() or {}) if snap.exists else {}
        completed=[x for x in (data.get('completedLessons') or []) if x in _DEV_INDEX]
        progress={}
        for cat in DEVELOPMENT_CATALOG:
            ids=[x['id'] for x in cat['lessons']]; done=sum(1 for x in ids if x in completed)
            progress[cat['id']]={'title':cat['title'],'percentage':round(done*100/len(ids)) if ids else 0,'completed':done,'total':len(ids)}
        achievements=[]
        for lid in completed:
            badge=_DEV_INDEX[lid][2].get('badge')
            if badge and badge not in achievements: achievements.append(badge)
        return {'completedLessons':completed,'progress':progress,'achievements':achievements,'updatedAt':iso(data.get('updatedAt'))}

    def _catalog_with_media(database):
        """Merge admin-uploaded video/book attachments (developmentMedia
        collection, see development_content_routes.py) into the static
        lesson catalog. Additive-only - falls back to plain text if a lesson
        has no media attached yet."""
        try:
            media = {snap.id: (snap.to_dict() or {}) for snap in database.collection('developmentMedia').stream()}
        except Exception:
            media = {}
        if not media:
            return DEVELOPMENT_CATALOG
        merged = []
        for cat in DEVELOPMENT_CATALOG:
            lessons = []
            for lesson in cat['lessons']:
                m = media.get(lesson['id'])
                if m:
                    lesson = dict(lesson, videoUrl=m.get('videoUrl') or '', bookUrl=m.get('bookUrl') or '', bookName=m.get('bookName') or '')
                lessons.append(lesson)
            merged.append(dict(cat, lessons=lessons))
        return merged

    @app.get('/api/student/development')
    def student_development_get():
        ok,detail=require_user()
        if not ok:return detail
        try:
            database=db(); uid=str(detail.get('uid') or ''); role=account_type(database,uid)
            if role and role!='student':return jsonify({'error':'Student access required.'}),403
            return jsonify({'success':True,'catalog':_catalog_with_media(database),'state':_development_payload(database,uid)})
        except Exception:
            app.logger.exception('Student development load failed'); return jsonify({'error':'Unable to load development center.'}),500

    @app.post('/api/student/development')
    def student_development_save():
        ok,detail=require_user()
        if not ok:return detail
        try:
            database=db(); uid=str(detail.get('uid') or ''); body=request.get_json(silent=True) or {}; lesson=clean(body.get('lessonId'),120)
            role=account_type(database,uid)
            if role and role!='student':return jsonify({'error':'Student access required.'}),403
            if lesson not in _DEV_INDEX:return jsonify({'error':'Unknown development lesson.'}),400
            ref=database.collection('studentDevelopment').document(uid); snap=ref.get(); current=list((snap.to_dict() or {}).get('completedLessons') or []) if snap.exists else []
            if lesson not in current:current.append(lesson)
            ref.set({'completedLessons':current[-500:],'updatedAt':datetime.now(timezone.utc)},merge=True)
            return jsonify({'success':True,'state':_development_payload(database,uid)})
        except Exception:
            app.logger.exception('Student development save failed'); return jsonify({'error':'Unable to save development progress.'}),500

    @app.get('/api/parent/children/<child_uid>/development')
    def parent_child_development(child_uid):
        ok,detail=require_parent()
        if not ok:return detail
        try:
            database=db(); child_uid=clean(child_uid,128); parent_uid=str(detail.get('uid') or '')
            if not verified_child(database,parent_uid,child_uid):return jsonify({'error':'This student is not verified with your parent account.'}),403
            child=child_profile(database,child_uid); state=_development_payload(database,child_uid)
            return jsonify({'success':True,'child':child,'state':state})
        except Exception:
            app.logger.exception('Parent child development lookup failed'); return jsonify({'error':'Unable to load child development progress.'}),500
