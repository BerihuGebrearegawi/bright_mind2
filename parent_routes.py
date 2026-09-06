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
                except Exception:pass
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
        {'id':'character-ethics','title':'🌟 Character & Ethics','lessons':[
            {'id':'truth-trust','title':'Truth, Trust & Responsibility','watch':'5–10 minute lesson: why honesty builds trust.','read':'Honesty means telling the truth, keeping promises and taking responsibility for mistakes.','think':'When is it difficult to tell the truth? What could you do?','practice':'Choose one responsibility at home or school and complete it without being reminded.','quiz':['Which action builds trust?','What should you do after making a mistake?'],'badge':'Truth & Trust'},
            {'id':'respect-empathy','title':'Respect, Empathy & Helping Others','watch':'5–10 minute lesson: respect people, differences and boundaries.','read':'Respect includes parents, teachers, classmates and community members. Empathy means trying to understand another person’s feelings.','think':'How would you want others to treat you when you make a mistake?','practice':'Do one helpful action and listen carefully to one person today.','quiz':['What is empathy?','Which response shows respect?'],'badge':'Kindness Builder'},
            {'id':'fairness-patience','title':'Fairness, Patience & Self-Control','watch':'5–10 minute lesson: pause, think and choose a fair response.','read':'Fairness means applying rules consistently; patience helps us respond thoughtfully instead of reacting quickly.','think':'What can you do when you feel something is unfair?','practice':'Use a pause-and-think strategy before responding to one difficult situation.','quiz':['What supports a fair decision?','Why is patience useful?'],'badge':'Fair Mind'},
            {'id':'time-community','title':'Punctuality & Everyday Ethics','watch':'5–10 minute lesson: ethical choices at home, school and in the community.','read':'Being on time, caring for shared spaces and following agreed rules are practical forms of responsibility.','think':'Where does being late affect other people?','practice':'Plan tomorrow so you arrive on time and complete one community-minded action.','quiz':['Why does punctuality matter?','Which is a community responsibility?'],'badge':'Responsible Citizen'}]},
        {'id':'self-development','title':'🚀 Self Development','lessons':[
            {'id':'self-awareness-goals','title':'Self-Awareness & Goal Setting','watch':'5–10 minute lesson: know your strengths and choose useful goals.','read':'Self-awareness helps you notice strengths, challenges, interests and habits. Good goals are specific, realistic and measurable.','think':'What is one strength you can use to improve a challenge?','practice':'Write one weekly goal and the first three steps toward it.','quiz':['What does self-awareness help you notice?','What makes a goal useful?'],'badge':'Goal Starter'},
            {'id':'time-study','title':'Time Management & Study Skills','watch':'5–10 minute lesson: plan focused study time.','read':'Break work into small tasks, choose priorities and use distraction-free study blocks.','think':'Where does your time disappear most often?','practice':'Create a seven-day study plan with one focused block each day.','quiz':['What should you prioritize?','Why use short focused blocks?'],'badge':'Time Master'},
            {'id':'focus-discipline-confidence','title':'Concentration, Discipline & Confidence','watch':'5–10 minute lesson: build consistency through small actions.','read':'Concentration improves when distractions are reduced. Discipline is repeated action toward a goal; confidence grows through practice and reflection.','think':'What distraction most often interrupts your learning?','practice':'Complete one 20-minute focused task and record what helped.','quiz':['What supports concentration?','How does confidence grow?'],'badge':'Focused Learner'},
            {'id':'problem-solving-leadership','title':'Problem Solving, Decisions, Communication, Leadership & Creativity','watch':'5–10 minute lesson: define a problem, generate options and communicate clearly.','read':'Strong problem solving compares options and consequences. Communication, creativity and leadership help teams act on good ideas. Emotional intelligence helps you notice and manage emotions while working with others.','think':'What would change if you considered two possible solutions before deciding?','practice':'Solve one real problem using: define → options → choose → review.','quiz':['What is a useful first step in problem solving?','What helps a team work toward a shared goal?'],'badge':'Growth Leader'}]},
        {'id':'emotional-social','title':'❤️ Emotional & Social Development','lessons':[
            {'id':'anger-regulation','title':'Understanding Anger & Self-Regulation','watch':'5–10 minute lesson: notice anger signals and pause safely.','read':'Anger is an emotion; the goal is to choose a safe response. Pause, breathe, name the feeling and seek help when needed.','think':'What signs tell you that you are becoming angry?','practice':'Use a pause-breathe-name strategy during one frustrating moment.','quiz':['What is the goal when managing anger?','What can a pause create?'],'badge':'Calm Choice'},
            {'id':'stress-resilience','title':'Managing Stress & Building Resilience','watch':'5–10 minute lesson: turn stress into manageable steps.','read':'Stress can be reduced by planning, rest, movement, supportive relationships and breaking difficult tasks into smaller steps.','think':'Which healthy strategy helps you recover after a stressful day?','practice':'Make a small stress plan with one healthy action, one support person and one next step.','quiz':['Which is a healthy stress strategy?','Why break a task into steps?'],'badge':'Resilience Builder'},
            {'id':'conflict-bullying','title':'Conflict Resolution & Bullying Safety','watch':'5–10 minute lesson: solve conflict safely and seek help for bullying.','read':'Use calm communication, listen and look for fair solutions. Bullying is repeated harmful behavior or a serious power imbalance; tell a trusted adult and prioritize safety.','think':'Who can you contact if a situation feels unsafe?','practice':'Practice one respectful sentence for disagreeing and identify a trusted adult.','quiz':['What is a safe response to bullying?','What helps resolve ordinary conflict?'],'badge':'Safe Teammate'},
            {'id':'empathy-teamwork-relationships','title':'Empathy, Teamwork & Healthy Relationships','watch':'5–10 minute lesson: listen, cooperate and respect boundaries.','read':'Healthy relationships include respect, honesty, communication, consent and appropriate boundaries. Teamwork means sharing roles and solving problems together.','think':'How can you show someone you listened?','practice':'Complete a shared task using clear roles and a respectful check-in.','quiz':['What supports a healthy relationship?','What is one teamwork habit?'],'badge':'Team Builder'}]},
        {'id':'life-skills','title':'🎯 Life Skills','lessons':[
            {'id':'financial-literacy','title':'Financial Literacy','watch':'5–10 minute lesson: needs, wants, saving and planning.','read':'Financial literacy includes distinguishing needs from wants, budgeting, saving and making informed choices about money.','think':'Which expense is a need and which is a want?','practice':'Create a simple weekly budget using imaginary or age-appropriate amounts.','quiz':['What is a budget for?','Why save money?'],'badge':'Money Smart'},
            {'id':'digital-online-safety','title':'Digital Literacy & Online Safety','watch':'5–10 minute lesson: protect accounts, privacy and devices.','read':'Use strong unique passwords, protect private information, check links and report harmful or suspicious activity to a trusted adult.','think':'What information should you avoid sharing publicly?','practice':'Review one account’s privacy settings with a trusted adult.','quiz':['What protects an account?','What should you do with suspicious messages?'],'badge':'Digital Guardian'},
            {'id':'critical-media-literacy','title':'Critical Thinking & Media Literacy','watch':'5–10 minute lesson: question information before sharing it.','read':'Check the source, date, evidence and purpose of a claim. Compare reliable sources before accepting or sharing information.','think':'What evidence would make a claim more trustworthy?','practice':'Take one online claim and check it against two reliable sources.','quiz':['What should you check about a claim?','Why compare sources?'],'badge':'Critical Thinker'},
            {'id':'communication-responsibility-career','title':'Communication, Responsibility & Future Careers','watch':'5–10 minute lesson: communicate clearly and explore future possibilities.','read':'Clear communication, personal responsibility and career awareness help you make informed choices about school, work and community life.','think':'What kind of work or contribution interests you, and why?','practice':'Write a short introduction about your strengths, interests and one future career to explore.','quiz':['What supports clear communication?','Why explore careers early?'],'badge':'Future Ready'}]}
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

    @app.get('/api/student/development')
    def student_development_get():
        ok,detail=require_user()
        if not ok:return detail
        try:
            database=db(); uid=str(detail.get('uid') or ''); role=account_type(database,uid)
            if role and role!='student':return jsonify({'error':'Student access required.'}),403
            return jsonify({'success':True,'catalog':DEVELOPMENT_CATALOG,'state':_development_payload(database,uid)})
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
