"""B-owned Telegram Communication & Content Platform.

Admin/approved-teacher publishing boundary, multi-channel publishing, scheduled
publishing queue, smart lists/buttons, moderation log intake, and analytics.
Actual Telegram channels/groups remain external deployment resources; their chat
IDs are stored as server-side configuration and are never exposed as bot tokens.
"""
import hmac, os, re
from datetime import datetime, timezone
import requests
from flask import jsonify, request
from telegram_targets_config import BMT_TELEGRAM_TARGETS, smart_targets_for

CONTENT_TYPES = {"text","photo","document","video","audio","voice","album","poll"}
MAX_TEXT = 4000
MAX_ITEMS = 10

def _clean(v, n=500): return str(v or "").strip()[:n]
def _key(v): return re.sub(r"[^A-Za-z0-9_:\-.]", "", str(v or ""))[:150]
def _now(): return datetime.now(timezone.utc)

def _db(factory):
    fb = factory()
    if not fb: raise RuntimeError("Firebase server credentials are not configured.")
    from firebase_admin import firestore
    return firestore.client()

def _telegram_call(method, payload):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token: raise RuntimeError("Telegram bot token is not configured.")
    r = requests.post(f"https://api.telegram.org/bot{token}/{method}", json=payload, timeout=20)
    if not r.ok:
        raise RuntimeError("Telegram API request failed.")
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError("Telegram API rejected the request.")
    return data.get("result")

def _staff(db, detail):
    if detail.get("admin") is True or str(detail.get("role","")).lower()=="admin": return "admin"
    uid = str(detail.get("uid", ""))
    snap = db.collection("teachers").document(uid).get()
    if snap.exists:
        d = snap.to_dict() or {}
        if d.get("approved") or d.get("status")=="approved": return "teacher"
    return None

def _targets(body):
    targets = body.get("targetChatIds")
    if not isinstance(targets, list) or not targets or len(targets)>6: raise ValueError("Choose 1–6 Telegram targets.")
    out=[]
    for x in targets:
        s=_clean(x,100)
        if not s or s in out: continue
        out.append(s)
    if not out: raise ValueError("At least one Telegram target is required.")
    return out

def _markup(body):
    buttons = body.get("buttons")
    if not isinstance(buttons,list) or not buttons: return None
    rows=[]
    for b in buttons[:12]:
        if not isinstance(b,dict): continue
        text=_clean(b.get("text"),80); url=_clean(b.get("url"),500)
        if text and url.startswith(("https://","http://","tg://")): rows.append([{"text":text,"url":url}])
    return {"inline_keyboard":rows} if rows else None

def _send(content, chat_id):
    t=content.get("type","text"); caption=_clean(content.get("caption"),MAX_TEXT); text=_clean(content.get("text"),MAX_TEXT); markup=_markup(content)
    if t=="text":
        p={"chat_id":chat_id,"text":text};
        if markup:p["reply_markup"]=markup
        return _telegram_call("sendMessage",p)
    field={"photo":"photo","document":"document","video":"video","audio":"audio","voice":"voice"}.get(t)
    if field:
        media=_clean(content.get("mediaUrl"),1500)
        if not media: raise ValueError(f"{t} requires mediaUrl.")
        p={"chat_id":chat_id,field:media}
        if caption:p["caption"]=caption
        if markup:p["reply_markup"]=markup
        return _telegram_call({"photo":"sendPhoto","document":"sendDocument","video":"sendVideo","audio":"sendAudio","voice":"sendVoice"}[t],p)
    if t=="poll":
        opts=content.get("options") if isinstance(content.get("options"),list) else []
        opts=[_clean(x,100) for x in opts[:10] if _clean(x,100)]
        if len(opts)<2: raise ValueError("Poll requires at least two options.")
        return _telegram_call("sendPoll",{"chat_id":chat_id,"question":text,"options":opts,"is_anonymous":bool(content.get("anonymous",True))})
    if t=="album":
        media=content.get("media") if isinstance(content.get("media"),list) else []
        arr=[]
        for x in media[:10]:
            if not isinstance(x,dict): continue
            kind=_clean(x.get("type"),20); url=_clean(x.get("mediaUrl"),1500)
            if kind in {"photo","video"} and url: arr.append({"type":kind,"media":url,"caption":_clean(x.get("caption"),1000)})
        if len(arr)<2: raise ValueError("Album requires at least two photo/video items.")
        return _telegram_call("sendMediaGroup",{"chat_id":chat_id,"media":arr})
    raise ValueError("Unsupported Telegram content type.")

def register_telegram_platform_routes(app, require_user, require_admin, firebase_admin_factory):
    @app.get("/api/admin/telegram/targets")
    def telegram_targets():
        ok,detail=require_user()
        if not ok:return detail
        try:
            db=_db(firebase_admin_factory); role=_staff(db,detail)
            if role not in {"admin","teacher"}: return jsonify({"error":"Admin or approved teacher access required."}),403
            rows=[{"id":t["key"],"name":t["name"],"kind":t["kind"],"chatId":t["chatId"],"purpose":t.get("purpose",""),"grades":t.get("grades",[])} for t in BMT_TELEGRAM_TARGETS]
            for s in db.collection("telegramTargets").where("enabled","==",True).limit(50).stream():
                d=s.to_dict() or {}; rows.append({"id":s.id,"name":d.get("name",""),"kind":d.get("kind","channel"),"chatId":d.get("chatId",""),"purpose":d.get("purpose",""),"grades":d.get("grades",[])})
            return jsonify({"targets":rows}),200
        except Exception:
            return jsonify({"error":"Unable to load Telegram targets."}),500

    @app.get("/api/admin/telegram/smart-targets")
    def telegram_smart_targets():
        """Smart List Builder: recommends which of the 12 channels/groups (plus
        any admin-added custom ones) should receive an announcement, given a
        grade and/or purpose, instead of admin picking manually every time."""
        ok,detail=require_user()
        if not ok:return detail
        try:
            db=_db(firebase_admin_factory); role=_staff(db,detail)
            if role not in {"admin","teacher"}: return jsonify({"error":"Admin or approved teacher access required."}),403
            grade=_clean(request.args.get("grade"),20); purpose=_clean(request.args.get("purpose"),20).lower()
            custom=[]
            for s in db.collection("telegramTargets").where("enabled","==",True).limit(50).stream():
                d=s.to_dict() or {}; custom.append({"key":s.id,"name":d.get("name",""),"kind":d.get("kind","channel"),"chatId":d.get("chatId",""),"purpose":d.get("purpose",""),"grades":d.get("grades",[])})
            picked=smart_targets_for(grade=grade, purpose=purpose or None, extra_targets=custom)
            return jsonify({"success":True,"targets":[{"id":t["key"],"name":t["name"],"kind":t["kind"],"chatId":t["chatId"]} for t in picked]}),200
        except Exception:
            app.logger.exception("Smart target selection failed")
            return jsonify({"error":"Unable to compute smart targets."}),500

    @app.post("/api/admin/telegram/targets")
    def telegram_create_target():
        ok,detail=require_user()
        if not ok:return detail
        if not require_admin()[0]: return jsonify({"error":"Admin access required."}),403
        try:
            db=_db(firebase_admin_factory); b=request.get_json(silent=True) or {}
            chat_id=_clean(b.get("chatId"),100); name=_clean(b.get("name"),120); kind=_clean(b.get("kind"),20).lower()
            if not chat_id or not name or kind not in {"channel","group"}: return jsonify({"error":"name, chatId and kind are required."}),400
            ref=db.collection("telegramTargets").document(); ref.set({"name":name,"chatId":chat_id,"kind":kind,"enabled":True,"createdBy":detail.get("uid"),"createdAt":_now(),"updatedAt":_now()})
            return jsonify({"success":True,"id":ref.id}),201
        except Exception:return jsonify({"error":"Unable to save Telegram target."}),500

    @app.post("/api/admin/telegram/publish")
    def telegram_publish():
        ok,detail=require_user()
        if not ok:return detail
        try:
            db=_db(firebase_admin_factory); role=_staff(db,detail)
            if role not in {"admin","teacher"}: return jsonify({"error":"Admin or approved teacher access required."}),403
            b=request.get_json(silent=True) or {}; targets=_targets(b); typ=_clean(b.get("type"),20).lower()
            if typ not in CONTENT_TYPES: return jsonify({"error":"Unsupported content type."}),400
            content={k:b.get(k) for k in ("type","text","caption","mediaUrl","buttons","options","anonymous","media")}
            content["type"]=typ
            results=[]; success=0
            for chat_id in targets:
                try:
                    result=_send(content,chat_id); success+=1; results.append({"chatId":chat_id,"ok":True,"telegramMessageId":result.get("message_id") if isinstance(result,dict) else None})
                except Exception:
                    app.logger.exception("Telegram publish target failed")
                    results.append({"chatId":chat_id,"ok":False})
            ref=db.collection("telegramPosts").document(); ref.set({"content":content,"targetChatIds":targets,"createdBy":detail.get("uid"),"createdRole":role,"createdAt":_now(),"successCount":success,"failureCount":len(targets)-success,"results":results})
            return jsonify({"success":success>0,"postId":ref.id,"results":results}),201 if success else 502
        except (ValueError,RuntimeError) as exc:return jsonify({"error":str(exc)}),400 if isinstance(exc,ValueError) else 503
        except Exception:return jsonify({"error":"Telegram publish failed."}),500

    @app.post("/api/admin/telegram/schedules")
    def telegram_schedule():
        ok,detail=require_user()
        if not ok:return detail
        if not require_admin()[0]: return jsonify({"error":"Admin access required."}),403
        try:
            db=_db(firebase_admin_factory); b=request.get_json(silent=True) or {}; targets=_targets(b)
            typ=_clean(b.get("type"),20).lower(); run_at=_clean(b.get("runAt"),40)
            if typ not in CONTENT_TYPES or not run_at:return jsonify({"error":"type and runAt are required."}),400
            dt=datetime.fromisoformat(run_at.replace("Z","+00:00"))
            if dt.tzinfo is None: return jsonify({"error":"runAt must include timezone."}),400
            ref=db.collection("telegramSchedules").document(); ref.set({"content":{k:b.get(k) for k in ("type","text","caption","mediaUrl","buttons","options","anonymous","media")},"targetChatIds":targets,"runAt":dt,"status":"scheduled","createdBy":detail.get("uid"),"createdAt":_now(),"updatedAt":_now()})
            return jsonify({"success":True,"scheduleId":ref.id,"status":"scheduled"}),201
        except Exception:return jsonify({"error":"Unable to schedule Telegram post."}),500

    @app.post("/api/admin/telegram/scheduler/tick")
    def telegram_scheduler_tick():
        supplied=request.headers.get("X-BMT-SCHEDULER-SECRET",""); secret=os.getenv("BMT_SCHEDULER_SECRET","")
        if not secret or not hmac.compare_digest(supplied,secret): return jsonify({"error":"Unauthorized."}),401
        try:
            db=_db(firebase_admin_factory); now=_now(); done=0
            docs=db.collection("telegramSchedules").where("status","==","scheduled").limit(50).stream()
            for snap in docs:
                d=snap.to_dict() or {}; run=d.get("runAt")
                if hasattr(run,"tzinfo") and run.tzinfo is None: run=run.replace(tzinfo=timezone.utc)
                if not run or run>now: continue
                ok=0
                for chat in d.get("targetChatIds") or []:
                    try:_send(d.get("content") or {},str(chat));ok+=1
                    except Exception:app.logger.exception("Scheduled Telegram send failed")
                snap.reference.update({"status":"sent" if ok else "failed","sentCount":ok,"updatedAt":now})
                done+=1
            return jsonify({"success":True,"processed":done}),200
        except Exception:return jsonify({"error":"Scheduler tick failed."}),500

    @app.get("/api/admin/telegram/analytics")
    def telegram_analytics():
        ok,detail=require_user()
        if not ok:return detail
        if not require_admin()[0]: return jsonify({"error":"Admin access required."}),403
        try:
            db=_db(firebase_admin_factory); posts=list(db.collection("telegramPosts").limit(500).stream())
            sent=sum(int((s.to_dict() or {}).get("successCount",0) or 0) for s in posts); failed=sum(int((s.to_dict() or {}).get("failureCount",0) or 0) for s in posts)
            mods=list(db.collection("telegramModerationLogs").limit(500).stream())
            deleted=sum(1 for s in mods if (s.to_dict() or {}).get("action")=="deleted")
            return jsonify({"posts":len(posts),"postsSent":sent,"postsFailed":failed,"moderationActions":len(mods),"moderationDeleted":deleted}),200
        except Exception:return jsonify({"error":"Unable to load Telegram analytics."}),500

    @app.get("/api/admin/telegram/media-library")
    def telegram_media_library():
        """Reuses telegramPosts (every publish is already logged there) rather
        than a new collection - lists past media so admin can resend it
        without re-uploading."""
        ok,detail=require_user()
        if not ok:return detail
        if not require_admin()[0]: return jsonify({"error":"Admin access required."}),403
        try:
            db=_db(firebase_admin_factory)
            rows=[]
            for snap in db.collection("telegramPosts").limit(300).stream():
                x=snap.to_dict() or {}; c=x.get("content") or {}
                if not c.get("mediaUrl"): continue
                rows.append({"id":snap.id,"type":c.get("type"),"mediaUrl":c.get("mediaUrl"),"caption":c.get("caption") or c.get("text") or "","createdAt":x.get("createdAt").isoformat() if hasattr(x.get("createdAt"),"isoformat") else x.get("createdAt")})
            rows.sort(key=lambda r: r.get("createdAt") or "", reverse=True)
            return jsonify({"success":True,"media":rows[:100]}),200
        except Exception:
            app.logger.exception("Media library list failed")
            return jsonify({"error":"Unable to load media library."}),500

    @app.get("/api/admin/telegram/calendar")
    def telegram_content_calendar():
        """Content Calendar view: the same telegramSchedules queue, grouped by
        date instead of a flat list, for the admin UI."""
        ok,detail=require_user()
        if not ok:return detail
        if not require_admin()[0]: return jsonify({"error":"Admin access required."}),403
        try:
            db=_db(firebase_admin_factory)
            by_day={}
            for snap in db.collection("telegramSchedules").limit(300).stream():
                x=snap.to_dict() or {}; run=x.get("runAt")
                day=run.date().isoformat() if hasattr(run,"date") else str(run)[:10]
                c=x.get("content") or {}
                by_day.setdefault(day,[]).append({"id":snap.id,"time":run.isoformat() if hasattr(run,"isoformat") else str(run),"type":c.get("type"),"preview":(c.get("text") or c.get("caption") or "")[:80],"status":x.get("status"),"targetCount":len(x.get("targetChatIds") or [])})
            days=[{"date":d,"items":sorted(v,key=lambda i:i["time"])} for d,v in sorted(by_day.items())]
            return jsonify({"success":True,"days":days}),200
        except Exception:
            app.logger.exception("Content calendar list failed")
            return jsonify({"error":"Unable to load the content calendar."}),500

    @app.get("/api/admin/telegram/moderation-logs")
    def telegram_moderation_logs():
        """Was previously write-only - the moderator bot logged every action to
        telegramModerationLogs, but nothing let admin read it back."""
        ok,detail=require_user()
        if not ok:return detail
        if not require_admin()[0]: return jsonify({"error":"Admin access required."}),403
        try:
            db=_db(firebase_admin_factory)
            rows=[]
            for snap in db.collection("telegramModerationLogs").limit(500).stream():
                x=snap.to_dict() or {}
                rows.append({"id":snap.id,"chatId":x.get("chatId"),"userId":x.get("userId"),"bot":x.get("bot"),"action":x.get("action"),"reason":x.get("reason"),"createdAt":x.get("createdAt").isoformat() if hasattr(x.get("createdAt"),"isoformat") else x.get("createdAt")})
            rows.sort(key=lambda r: r.get("createdAt") or "", reverse=True)
            rows = rows[:200]
            return jsonify({"success":True,"logs":rows}),200
        except Exception:
            app.logger.exception("Moderation log list failed")
            return jsonify({"error":"Unable to load moderation logs."}),500
