"""B-owned Publisher/Content Bot for Telegram.

This is the third Telegram bot boundary. It is intentionally separate from the
Academic Challenge bot and Moderator/Support bot. Publisher Bot is a controlled
operator relay: approved BMT staff select a registered target, then send text or
media to the bot; the bot copies that message to the selected channel/group.
Secrets remain server-side.
"""
import hmac, os, time
from datetime import datetime, timezone
import requests
from flask import jsonify, request
from telegram_targets_config import BMT_TELEGRAM_TARGETS

SESSION_TTL = 10 * 60
MAX_TARGETS = 12


def _now(): return datetime.now(timezone.utc)

def _db(factory):
    fb = factory()
    if not fb: raise RuntimeError("Firebase server credentials are not configured.")
    from firebase_admin import firestore
    return firestore.client()

def _token():
    token = os.getenv("TELEGRAM_PUBLISHER_BOT_TOKEN", "").strip()
    if not token: raise RuntimeError("Publisher bot token is not configured.")
    return token

def _api(method, payload):
    r = requests.post(f"https://api.telegram.org/bot{_token()}/{method}", json=payload, timeout=20)
    if not r.ok or not r.json().get("ok"):
        raise RuntimeError("Telegram Publisher Bot API request failed.")
    return r.json().get("result")

def _send(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": str(text)[:3900]}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _api("sendMessage", payload)

def _staff(db, tg_id):
    tid = str(tg_id or "").strip()
    if not tid: return None
    docs = db.collection("users").where("telegramUserId", "==", tid).limit(1).stream()
    snap = next(iter(docs), None)
    if snap is None: return None
    d = snap.to_dict() or {}
    role = str(d.get("accountType") or d.get("role") or "").lower()
    if role == "admin": return snap.id, "admin"
    if role in {"teacher", "approved_teacher"}:
        t = db.collection("teachers").document(snap.id).get()
        td = t.to_dict() or {} if t.exists else {}
        if td.get("approved") or td.get("status") == "approved": return snap.id, "teacher"
    return None

def _targets(db):
    """Includes the 12 canonical BMT channels/groups (telegram_targets_config.py)
    plus any admin-added custom targets. Was previously custom-targets-only,
    which meant the teacher-facing 'Send to Telegram' picker never showed any
    of the real BMT channels/groups - only ones manually re-added here."""
    out = [(t["key"], t["name"], t["chatId"]) for t in BMT_TELEGRAM_TARGETS]
    for snap in db.collection("telegramTargets").where("enabled", "==", True).limit(50).stream():
        d=snap.to_dict() or {}
        if d.get("chatId"):
            out.append((snap.id, str(d.get("name") or snap.id), str(d.get("chatId"))))
        if len(out) >= MAX_TARGETS + len(BMT_TELEGRAM_TARGETS): break
    return out

def _copy(chat_id, from_chat_id, message_id):
    return _api("copyMessage", {"chat_id": chat_id, "from_chat_id": from_chat_id, "message_id": int(message_id)})

def register_telegram_publisher_bot_routes(app, firebase_admin_factory, require_user=None):
    sessions = {}

    def _web_staff(db, uid):
        """Web-dashboard staff check (Firebase uid), distinct from _staff()
        above which resolves Telegram-side identity via telegramUserId."""
        snap = db.collection("users").document(uid).get()
        d = snap.to_dict() or {} if snap.exists else {}
        role = str(d.get("accountType") or d.get("role") or "").lower()
        if role == "admin":
            return "admin"
        if role == "teacher":
            t = db.collection("teachers").document(uid).get()
            td = t.to_dict() or {} if t.exists else {}
            if td.get("approved") or td.get("status") == "approved":
                return "teacher"
        return None

    def _target_by_id(db, target_id):
        for key, _name, chat_id in [(t["key"], t["name"], t["chatId"]) for t in BMT_TELEGRAM_TARGETS]:
            if key == target_id:
                return chat_id
        snap = db.collection("telegramTargets").document(target_id).get()
        if not snap.exists:
            return None
        d = snap.to_dict() or {}
        if not d.get("enabled") or not d.get("chatId"):
            return None
        return str(d.get("chatId"))

    @app.post("/api/telegram/publisher/webhook")
    def publisher_webhook():
        secret = os.getenv("TELEGRAM_PUBLISHER_WEBHOOK_SECRET", "").strip()
        supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not secret or not hmac.compare_digest(supplied, secret):
            return jsonify({"error":"Unauthorized."}), 401
        body=request.get_json(silent=True) or {}
        callback_query=body.get("callback_query")
        if callback_query is not None:
            callback_id=callback_query.get("id"); data=str(callback_query.get("data") or "")
            cb_chat=((callback_query.get("message") or {}).get("chat") or {}).get("id")
            cb_user=callback_query.get("from") or {}
            if callback_id:
                try: _api("answerCallbackQuery",{"callback_query_id":callback_id})
                except Exception:
                    app.logger.debug("Telegram publisher callback acknowledgement failed", exc_info=True)
            if cb_chat is not None and cb_user.get("id") and data=="pub_show_targets":
                try:
                    db=_db(firebase_admin_factory)
                    if not _staff(db,cb_user.get("id")):
                        _send(cb_chat,"Publisher Bot is restricted to approved BMT administrators and teachers.")
                    else:
                        rows=_targets(db)
                        if not rows: _send(cb_chat,"No enabled Telegram targets are configured.")
                        else: _send(cb_chat,"Tap a target to select it:",reply_markup={"inline_keyboard":[[{"text":name,"callback_data":f"pub_target:{tid}"}] for tid,name,_ in rows]})
                except Exception:
                    app.logger.exception("Publisher show-targets button failed")
                return jsonify({"ok":True}),200
            if cb_chat is not None and cb_user.get("id") and data.startswith("pub_target:"):
                try:
                    db=_db(firebase_admin_factory)
                    staff=_staff(db,cb_user.get("id"))
                    if not staff:
                        _send(cb_chat,"Publisher Bot is restricted to approved BMT administrators and teachers.")
                        return jsonify({"ok":True}),200
                    uid,role=staff
                    target_ref=data.split(":",1)[1]
                    target=None
                    for tid,name,tchat in _targets(db):
                        if target_ref==tid: target=(tid,name,tchat); break
                    if not target:
                        _send(cb_chat,"That target is no longer available. Use /targets to see the current list.")
                    else:
                        sessions[str(cb_user.get("id"))]={"uid":uid,"role":role,"target":target,"expires":time.time()+SESSION_TTL}
                        _send(cb_chat,f"Target selected: {target[1]}. Send the content now. Selection expires in 10 minutes.")
                except Exception:
                    app.logger.exception("Publisher target-button handling failed")
            return jsonify({"ok":True}),200
        message=body.get("message") or {}
        chat=message.get("chat") or {}
        tg_user=message.get("from") or {}
        chat_id=chat.get("id"); tg_id=tg_user.get("id")
        if chat_id is None or tg_id is None: return jsonify({"ok":True}),200
        try:
            db=_db(firebase_admin_factory)
            staff=_staff(db,tg_id)
            if not staff:
                _send(chat_id,"Publisher Bot is restricted to approved BMT administrators and teachers.")
                return jsonify({"ok":True}),200
            uid,role=staff
            key=str(tg_id)
            text=str(message.get("text") or "").strip()
            cmd,_,arg=text.partition(" ")
            cmd=cmd.split("@",1)[0].lower()
            if cmd in {"/start","/help"}:
                _send(chat_id,"BMT Publisher Bot\n\nTap 📋 View Targets below, then tap a target to select it - or use /publish TARGET_ID. After selecting a target, send the text, photo, PDF, video, audio, voice, or other message you want copied to that target.",reply_markup={"inline_keyboard":[[{"text":"📋 View Targets","callback_data":"pub_show_targets"}]]})
                return jsonify({"ok":True}),200
            if cmd=="/targets":
                rows=_targets(db)
                if not rows: _send(chat_id,"No enabled Telegram targets are configured.")
                else: _send(chat_id,"Tap a target to select it:",reply_markup={"inline_keyboard":[[{"text":name,"callback_data":f"pub_target:{tid}"}] for tid,name,_ in rows]})
                return jsonify({"ok":True}),200
            if cmd=="/cancel":
                sessions.pop(key,None); _send(chat_id,"Publishing selection cancelled."); return jsonify({"ok":True}),200
            if cmd=="/publish":
                target_ref=arg.strip()
                rows=_targets(db)
                target=None
                for i,(tid,name,cid) in enumerate(rows,1):
                    if target_ref==tid or target_ref==str(i): target=(tid,name,cid); break
                if not target:
                    _send(chat_id,"Unknown target. Use /targets first, then /publish TARGET_ID.")
                else:
                    sessions[key]={"uid":uid,"role":role,"target":target,"expires":time.time()+SESSION_TTL}
                    _send(chat_id,f"Target selected: {target[1]}. Send the content now. Selection expires in 10 minutes.")
                return jsonify({"ok":True}),200
            session=sessions.get(key)
            if not session or session.get("expires",0)<time.time():
                sessions.pop(key,None); _send(chat_id,"Select a target first with /publish TARGET_ID."); return jsonify({"ok":True}),200
            target_id,target_name,target_chat=session["target"]
            result=_copy(target_chat,chat_id,message.get("message_id"))
            ref=db.collection("telegramPosts").document()
            ref.set({"source":"publisher_bot","targetId":target_id,"targetChatIds":[target_chat],"createdBy":uid,"createdRole":role,"createdAt":_now(),"successCount":1,"failureCount":0,"results":[{"chatId":target_chat,"ok":True,"telegramMessageId":(result or {}).get("message_id")} ]})
            _send(chat_id,f"Published to {target_name}.")
            return jsonify({"ok":True}),200
        except Exception:
            app.logger.exception("Publisher bot webhook failed")
            try: _send(chat_id,"Publisher Bot could not complete that action.")
            except Exception:
                app.logger.debug("Telegram publisher error notification failed", exc_info=True)
            return jsonify({"ok":True}),200

    @app.post("/api/telegram/publisher/send-quiz")
    def send_quiz_to_telegram():
        """Push selected APPROVED Question Bank questions to a Telegram
        target as native Telegram quiz polls (Bot API sendPoll, type=quiz).

        Called from the BMT web dashboard (Firebase-authenticated staff),
        not from Telegram itself - a separate concern from publisher_webhook
        above, which is the interactive in-Telegram flow authenticated with
        TELEGRAM_WEBHOOK_SECRET.
        """
        if require_user is None:
            return jsonify({"error": "Telegram quiz distribution is not available."}), 503
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(firebase_admin_factory)
            uid = str(detail.get("uid", ""))
            role = _web_staff(db, uid)
            if not role:
                return jsonify({"error": "Approved teacher or admin access required."}), 403
            body = request.get_json(silent=True) or {}
            target_id = str(body.get("targetId") or "").strip()[:150]
            question_ids = body.get("questionIds")
            if not target_id or not isinstance(question_ids, list) or not question_ids:
                return jsonify({"error": "targetId and at least one questionId are required."}), 400
            question_ids = [str(q).strip()[:150] for q in question_ids[:MAX_TARGETS * 5] if str(q or "").strip()]
            chat_id = _target_by_id(db, target_id)
            if not chat_id:
                return jsonify({"error": "Unknown or disabled Telegram target."}), 404

            sent, failed = [], []
            for qid in question_ids:
                snap = db.collection("questionBank").document(qid).get()
                if not snap.exists:
                    failed.append({"questionId": qid, "reason": "not_found"}); continue
                q = snap.to_dict() or {}
                if q.get("status") != "approved":
                    failed.append({"questionId": qid, "reason": "not_approved"}); continue
                options_map = q.get("options") if isinstance(q.get("options"), dict) else {}
                letters = sorted(k for k in options_map if options_map.get(k))
                if len(letters) < 2:
                    failed.append({"questionId": qid, "reason": "insufficient_options"}); continue
                letters = letters[:10]  # Telegram polls allow at most 10 options
                answer = str(q.get("correctAnswer", "")).strip().upper()
                if answer not in letters:
                    failed.append({"questionId": qid, "reason": "no_correct_answer"}); continue
                try:
                    result = _api("sendPoll", {
                        "chat_id": chat_id,
                        "question": str(q.get("question", ""))[:290],
                        "options": [str(options_map[k])[:97] for k in letters],
                        "type": "quiz",
                        "correct_option_id": letters.index(answer),
                        "is_anonymous": True,
                    })
                    ref = db.collection("telegramPosts").document()
                    ref.set({
                        "source": "publisher_bot_quiz", "targetId": target_id, "targetChatIds": [chat_id],
                        "createdBy": uid, "createdRole": role, "createdAt": _now(),
                        "sourceQuestionId": qid, "telegramMessageId": (result or {}).get("message_id"),
                        "successCount": 1, "failureCount": 0,
                    })
                    sent.append(qid)
                except Exception:
                    app.logger.exception("Telegram quiz send failed for question %s", qid)
                    failed.append({"questionId": qid, "reason": "send_failed"})
            if not sent:
                return jsonify({"error": "No questions could be sent to Telegram.", "failed": failed}), 502
            return jsonify({"success": True, "sentCount": len(sent), "sent": sent, "failed": failed}), 200
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception("Telegram quiz distribution failed")
            return jsonify({"error": "Unable to send questions to Telegram."}), 500

    @app.get("/api/telegram/publisher/targets")
    def list_publisher_targets():
        """Web-dashboard listing of enabled Telegram targets (id/name only -
        never the raw chatId), for populating a target picker in the UI."""
        if require_user is None:
            return jsonify({"error": "Telegram target listing is not available."}), 503
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            db = _db(firebase_admin_factory)
            if not _web_staff(db, str(detail.get("uid", ""))):
                return jsonify({"error": "Approved teacher or admin access required."}), 403
            rows = [{"id": tid, "name": name} for tid, name, _chat in _targets(db)]
            return jsonify({"targets": rows}), 200
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception("Telegram target listing failed")
            return jsonify({"error": "Unable to load Telegram targets."}), 500
