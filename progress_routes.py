"""Student learning analytics built from existing Firestore records.
No binary content is copied into analytics.
"""
from datetime import datetime, timezone
from flask import jsonify

def register_progress_routes(app, require_user, firebase_admin_factory):
    def db():
        fb=firebase_admin_factory()
        if not fb: raise RuntimeError("Firebase server credentials are not configured.")
        from firebase_admin import firestore
        return firestore.client()

    def iso(v): return v.isoformat() if hasattr(v, "isoformat") else v

    @app.get('/api/student/progress')
    def student_progress():
        ok, detail=require_user()
        if not ok: return detail
        try:
            database=db(); uid=detail['uid']; now=datetime.now(timezone.utc)
            user_snap=database.collection('users').document(uid).get()
            user=user_snap.to_dict() or {} if user_snap.exists else {}
            grade=str(user.get('className') or user.get('grade') or detail.get('grade') or '')
            progress=user.get('progress') or {}; videos=progress.get('videos') or {}; quizzes=progress.get('quizzes') or {}
            watched=sum(1 for v in videos.values() if isinstance(v,dict) and (v.get('watched') or float(v.get('progress',0) or 0)>=95))
            quiz_scores=[]
            for q in quizzes.values():
                if isinstance(q,dict) and q.get('total'):
                    quiz_scores.append(round(float(q.get('score',0))/float(q.get('total',1))*100,1))
            exam_docs=list(database.collection('examAttempts').where('userId','==',uid).where('status','==','submitted').limit(100).stream())
            exams=[]
            for d in exam_docs:
                x=d.to_dict() or {}; total=float(x.get('totalPoints') or x.get('total') or 0); score=float(x.get('score') or 0)
                pct=x.get('percentage'); pct=float(pct) if pct is not None else (round(score/total*100,1) if total else 0)
                exams.append({'id':d.id,'examTitle':x.get('examTitle') or 'Exam','percentage':pct,'submittedAt':iso(x.get('submittedAt'))})
            exams.sort(key=lambda x:x.get('submittedAt') or '', reverse=True)
            avg=round(sum(float(e['percentage']) for e in exams)/len(exams),1) if exams else 0

            # Build lightweight personalized recommendations from existing records.
            # No new paid storage service is required.
            topic_scores={}
            for d in exam_docs:
                x=d.to_dict() or {}
                for topic, stat in (x.get('topicStats') or {}).items():
                    try:
                        total=float(stat.get('total',0) or 0); score=float(stat.get('score',0) or 0)
                        if total:
                            item=topic_scores.setdefault(str(topic), {'score':0.0,'total':0.0})
                            item['score'] += score; item['total'] += total
                    except Exception:
                        continue
            weak_topics=[]
            for topic, stat in topic_scores.items():
                pct=round(stat['score']/stat['total']*100,1) if stat['total'] else 0
                if pct < 70:
                    weak_topics.append({'topic':topic,'percentage':pct})
            weak_topics.sort(key=lambda x:x['percentage'])

            recommendations=[]
            for item in weak_topics[:3]:
                recommendations.append({
                    'type':'topic', 'priority':'high' if item['percentage'] < 50 else 'medium',
                    'title':f"Review {item['topic']}",
                    'reason':f"Your average performance on this topic is {item['percentage']}%.",
                    'action':'practice'
                })
            if avg and avg < 60:
                recommendations.append({'type':'exam','priority':'high','title':'Take a focused practice exam','reason':'Your recent exam average is below 60%.','action':'exam'})
            elif exams and avg < 75:
                recommendations.append({'type':'exam','priority':'medium','title':'Strengthen your exam technique','reason':f'Your current exam average is {avg}%.','action':'exam'})
            if not recommendations:
                recommendations.append({'type':'general','priority':'low','title':'Keep your learning streak','reason':'Your current results show no major weak area yet. Continue practicing regularly.','action':'practice'})
            course_docs=list(database.collection('courses').stream())
            relevant=[]
            for d in course_docs:
                c=d.to_dict() or {}; c['id']=d.id
                if not grade or str(c.get('className',''))==grade: relevant.append(c)
            lesson_count=0
            completed_courses=0
            for c in relevant:
                lesson_count += len(list(database.collection('lessons').where('courseId','==', c.get('id','')).stream())) if c.get('id') else 0
            completed_courses=sum(1 for c in relevant if c.get('completedStudents') and uid in (c.get('completedStudents') or []))
            ai_doc=database.collection('aiUsage').document(f'{uid}_{now.strftime("%Y-%m-%d")}').get()
            ai=ai_doc.to_dict() or {} if ai_doc.exists else {}
            live=list(database.collection('liveClasses').where('grade','==',grade).limit(20).stream()) if grade else []
            upcoming=[]
            for d in live:
                x=d.to_dict() or {}; st=x.get('startAt')
                if hasattr(st,'timestamp') and st.timestamp() >= now.timestamp() and x.get('status','scheduled')!='cancelled':
                    upcoming.append({'id':d.id,'title':x.get('title','Live class'),'subject':x.get('subject',''),'startAt':iso(st),'meetingUrl':x.get('meetingUrl','')})
            upcoming.sort(key=lambda x:x.get('startAt') or '')
            return jsonify({'grade':grade,'stats':{'videosWatched':watched,'quizAverage':round(sum(quiz_scores)/len(quiz_scores),1) if quiz_scores else 0,'examsTaken':len(exams),'examAverage':avg,'coursesAvailable':len(relevant),'coursesCompleted':completed_courses,'aiUsedToday':int(ai.get('used',0) or 0)},'examHistory':exams[:10],'upcomingLiveClasses':upcoming[:5],
                    'weakTopics':weak_topics[:5], 'recommendations':recommendations[:5]})
        except Exception as exc:
            app.logger.exception('Progress analytics failure')
            return jsonify({'error':'Unable to load learning progress.'}),500
