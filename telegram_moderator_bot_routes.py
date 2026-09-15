"""B-owned Moderator / Support Bot.

Dedicated third-party Telegram identity for group safety and basic support.
It does not perform academic grading and does not publish content.
"""
import hmac, os
from datetime import datetime, timezone
import requests
from flask import jsonify, request


def _db(factory):
    fb=factory()
    if not fb: raise RuntimeError("Firebase server credentials are not configured.")
    from firebase_admin import firestore
    return firestore.client()

def _api(method,payload):
    token=os.getenv("TELEGRAM_MODERATOR_BOT_TOKEN","").strip()
    if not token: raise RuntimeError("Moderator bot token is not configured.")
    r=requests.post(f"https://api.telegram.org/bot{token}/{method}",json=payload,timeout=15)
    if not r.ok or not r.json().get("ok"): raise RuntimeError("Telegram Moderator Bot API request failed.")
    return r.json().get("result")

def _send(chat_id,text): return _api("sendMessage",{"chat_id":chat_id,"text":str(text)[:3900]})

def _delete(chat_id,message_id):
    try: return bool(_api("deleteMessage",{"chat_id":chat_id,"message_id":int(message_id)}))
    except Exception: return False

def _is_bad(text):
    low=str(text or "").lower()
    urls=low.count("http://")+low.count("https://")+low.count("t.me/")
    mentions=str(text or "").count("@")
    suspicious=any(x in low for x in ("free crypto","send money to claim","guaranteed profit","giveaway winner","verify your wallet"))
    return suspicious or urls>=4 or mentions>=8, ("suspicious_link_or_spam" if suspicious or urls>=4 else "excessive_mentions")

def _operator(db,tg_id):
    docs=db.collection("users").where("telegramUserId","==",str(tg_id)).limit(1).stream()
    s=next(iter(docs),None)
    if s is None:return None
    d=s.to_dict() or {}; role=str(d.get("accountType") or d.get("role") or "").lower()
    if role=="admin": return s.id,"admin"
    return None

def _restrict(chat_id, user_id, seconds):
    until = int(datetime.now(timezone.utc).timestamp()) + seconds
    try:
        return bool(_api("restrictChatMember", {"chat_id": chat_id, "user_id": user_id,
                                                 "permissions": {"can_send_messages": False},
                                                 "until_date": until}))
    except Exception:
        return False

def _ban(chat_id, user_id):
    try:
        return bool(_api("banChatMember", {"chat_id": chat_id, "user_id": user_id}))
    except Exception:
        return False

def _recent_violation_count(db, chat_id, user_id):
    """Advanced Moderator escalation: counts this user's recent flagged/deleted
    messages in this chat (from the same telegramModerationLogs already being
    written) instead of a new collection, and escalates delete -> mute -> ban."""
    count = 0
    for snap in db.collection("telegramModerationLogs").where("chatId", "==", str(chat_id)).where("userId", "==", str(user_id)).limit(50).stream():
        x = snap.to_dict() or {}
        if x.get("action") in {"deleted", "flagged", "muted"}:
            count += 1
    return count

def register_telegram_moderator_bot_routes(app,firebase_admin_factory):
    @app.post("/api/telegram/moderator/webhook")
    def moderator_webhook():
        secret=os.getenv("TELEGRAM_MODERATOR_WEBHOOK_SECRET","").strip()
        supplied=request.headers.get("X-Telegram-Bot-Api-Secret-Token","")
        if not secret or not hmac.compare_digest(supplied,secret): return jsonify({"error":"Unauthorized."}),401
        body=request.get_json(silent=True) or {}
        callback_query=body.get("callback_query")
        if callback_query is not None:
            callback_id=callback_query.get("id"); data=str(callback_query.get("data") or "")
            cb_chat=((callback_query.get("message") or {}).get("chat") or {}).get("id")
            if callback_id:
                try: _api("answerCallbackQuery",{"callback_query_id":callback_id})
                except Exception:
                    app.logger.debug("Telegram callback acknowledgement failed", exc_info=True)
            if cb_chat is not None:
                try:
                    if data=="menu:rules":
                        _send(cb_chat,"BMT Group Safety Rules\n\n• Be respectful - no harassment, hate speech, or spam.\n• No sharing of exam answers or cheating material.\n• No unrelated advertising or external links.\n• Repeated violations lead to a temporary mute, then a ban.")
                    elif data=="menu:contact":
                        _send(cb_chat,"Need help from a human? Contact BMT Support:\n📞 0914404472\n✉️ berihu144@gmail.com")
                except Exception:
                    app.logger.exception("Moderator menu callback failed")
            return jsonify({"ok":True}),200
        msg=body.get("message") or {}; chat=msg.get("chat") or {}; cid=chat.get("id")
        if cid is None:return jsonify({"ok":True}),200
        try:
            db=_db(firebase_admin_factory); text=msg.get("text") or msg.get("caption") or ""
            if chat.get("type") in {"group","supergroup"}:
                bad,reason=_is_bad(text)
                if bad:
                    from_user = (msg.get("from") or {})
                    user_id = from_user.get("id")
                    deleted=_delete(cid,msg.get("message_id"))
                    action = "deleted" if deleted else "flagged"
                    db.collection("telegramModerationLogs").document().set({"chatId":str(cid),"messageId":msg.get("message_id"),"userId":str(user_id or ""),"bot":"moderator","action":action,"reason":reason,"createdAt":datetime.now(timezone.utc)})
                    # Escalation: repeated offenders get muted, then banned.
                    if user_id:
                        prior = _recent_violation_count(db, cid, user_id)
                        if prior >= 5 and _ban(cid, user_id):
                            db.collection("telegramModerationLogs").document().set({"chatId":str(cid),"userId":str(user_id),"bot":"moderator","action":"banned","reason":"repeated_violations","createdAt":datetime.now(timezone.utc)})
                            _send(cid,"BMT Moderator Bot removed a repeat offender from this group.")
                        elif prior >= 2 and _restrict(cid, user_id, 3600):
                            db.collection("telegramModerationLogs").document().set({"chatId":str(cid),"userId":str(user_id),"bot":"moderator","action":"muted","reason":"repeated_violations","createdAt":datetime.now(timezone.utc)})
                            _send(cid,"BMT Moderator Bot muted a member for 1 hour after repeated safety violations.")
                        elif deleted:
                            _send(cid,"BMT Moderator Bot removed a message that matched group safety rules.")
                    return jsonify({"ok":True}),200
            cmd=str(text).partition(" ")[0].split("@",1)[0].lower()
            if cmd in {"/start","/help"}:
                base_url=os.getenv("BMT_BASE_URL","").strip().rstrip("/")
                keyboard_rows=[[{"text":"📜 Group Rules","callback_data":"menu:rules"},{"text":"📞 Contact Support","callback_data":"menu:contact"}]]
                if base_url.startswith("https://"):
                    keyboard_rows.append([{"text":"🚀 Open BMT App","web_app":{"url":base_url+"/student"}}])
                _api("sendMessage",{"chat_id":cid,"text":"BMT Moderator / Support Bot\n\nThis bot protects BMT groups from spam and suspicious content.\n\nGroup administrators can contact BMT support for moderation issues.","reply_markup":{"inline_keyboard":keyboard_rows}})
            elif cmd=="/status":
                _send(cid,"BMT Moderator Bot is active. Automated safety checks are enabled for this group.")
            return jsonify({"ok":True}),200
        except Exception:
            app.logger.exception("Moderator bot webhook failed")
            return jsonify({"ok":True}),200
