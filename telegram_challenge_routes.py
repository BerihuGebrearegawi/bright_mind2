"""V31.64 Telegram Academic Challenge integration.

Security boundary:
- Telegram webhook is authenticated with TELEGRAM_WEBHOOK_SECRET.
- Telegram identity is mapped server-side to users.telegramUserId.
- Challenge grading/qualification remains server-authoritative.
- Bot never receives or exposes answer keys.
- This module is additive and does not modify Cloud-B Library/Scanner/Reader.
"""
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

from flask import jsonify, request


def _clean(v, n=150):
    return str(v or "").strip()[:n]


def _key(v):
    return "".join(ch for ch in str(v or "") if ch.isalnum() or ch in "._:-")[:150]


def _num(v, default=0.0):
    try:
        x = float(v)
        return x if x >= 0 else default
    except (TypeError, ValueError):
        return default


def _as_dt(v):
    if v is None:
        return None
    if hasattr(v, "tzinfo"):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None


def register_telegram_challenge_routes(app, firebase_admin_factory):
    """Register Telegram webhook endpoints.

    Required environment:
      TELEGRAM_BOT_TOKEN
      TELEGRAM_WEBHOOK_SECRET
    Optional:
      TELEGRAM_MAX_MESSAGE_LENGTH (default 3500)
    """

    def db():
        fb = firebase_admin_factory()
        if not fb:
            raise RuntimeError("Firebase server credentials are not configured.")
        from firebase_admin import firestore
        return firestore.client()

    def telegram_send(chat_id, text, reply_markup=None):
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("Telegram bot token is not configured.")
        payload = {"chat_id": str(chat_id), "text": str(text)[:int(os.getenv("TELEGRAM_MAX_MESSAGE_LENGTH", "3500"))]}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        import requests
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=10,
        )
        if r.status_code >= 400:
            raise RuntimeError("Telegram API request failed.")
        return r.json()

    def answer_callback_query(callback_query_id, text=None):
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            return
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = str(text)[:200]
        import requests
        try:
            requests.post(f"https://api.telegram.org/bot{token}/answerCallbackQuery", json=payload, timeout=10)
        except Exception:
            pass

    def map_telegram_user(store, tg_user):
        tid = str((tg_user or {}).get("id") or "").strip()
        if not tid:
            return None
        docs = store.collection("users").where("telegramUserId", "==", tid).limit(1).stream()
        snap = next(iter(docs), None)
        if snap is None:
            return None
        data = snap.to_dict() or {}
        role = str(data.get("accountType") or data.get("role") or "").lower()
        if role != "student":
            return None
        return snap.id, data

    def public_challenges(store, user):
        grade = str(user.get("className") or user.get("class") or user.get("grade") or "").strip()
        student_region = str(user.get("region") or "").strip()
        now = datetime.now(timezone.utc)
        out = []
        for snap in store.collection("academicChallenges").where("status", "==", "published").limit(100).stream():
            c = snap.to_dict() or {}
            start = _as_dt(c.get("startsAt")); end = _as_dt(c.get("endsAt"))
            if c.get("grade") and str(c.get("grade")) != grade:
                continue
            challenge_region = str(c.get("region") or "ALL").strip()
            if challenge_region and challenge_region != "ALL" and challenge_region != student_region:
                continue
            if start and now < start:
                continue
            if end and now > end:
                continue
            out.append((snap.id, c))
        out.sort(key=lambda x: str(x[1].get("title", "")).lower())
        return out

    def start_attempt(store, uid, challenge_id):
        ref = store.collection("academicChallenges").document(_key(challenge_id))
        snap = ref.get()
        if not snap.exists:
            return None, "Challenge not found."
        c = snap.to_dict() or {}
        if c.get("status") != "published":
            return None, "Challenge is not available."
        grade = ""
        us = store.collection("users").document(uid).get()
        user = us.to_dict() or {} if us.exists else {}
        grade = str(user.get("className") or user.get("class") or user.get("grade") or "").strip()
        if c.get("grade") and str(c.get("grade")) != grade:
            return None, "This challenge is not assigned to your class."
        now = datetime.now(timezone.utc)
        start = _as_dt(c.get("startsAt")); end = _as_dt(c.get("endsAt"))
        if start and now < start:
            return None, "Challenge is not open yet."
        if end and now > end:
            return None, "Challenge is closed."
        entry_fee = _num(c.get("entryFee"))
        if entry_fee > 0:
            q = store.collection("challengeEntries").where("challengeId", "==", ref.id).where("userId", "==", uid).where("status", "==", "verified").limit(1).stream()
            if next(iter(q), None) is None:
                return None, "Verified challenge entry is required before starting."
        attempt_id = hashlib.sha256(f"{uid}:{ref.id}".encode("utf-8")).hexdigest()[:40]
        aref = store.collection("challengeAttempts").document(attempt_id)
        existing = aref.get()
        if existing.exists:
            a = existing.to_dict() or {}
            if a.get("status") == "started":
                return (attempt_id, a), None
            return None, "This challenge attempt has already been submitted."
        ids = [_key(x) for x in (c.get("questionIds") or []) if _key(x)]
        if not ids or len(set(ids)) != len(ids):
            return None, "Challenge question set is invalid."
        snapshot = []
        for qid in ids:
            qs = store.collection("questionBank").document(qid).get()
            if not qs.exists:
                return None, "Challenge question data is unavailable."
            q = qs.to_dict() or {}
            if q.get("status") != "approved":
                return None, "A challenge question is not currently approved."
            snapshot.append({
                "id": qid,
                "question": _clean(q.get("question"), 2000),
                "type": _clean(q.get("type") or "mcq", 30),
                "options": q.get("options") if isinstance(q.get("options"), dict) else {},
                "points": max(0.0, _num(q.get("points"), 1.0)),
                "correctAnswer": _clean(q.get("correctAnswer"), 30).upper(),
            })
        duration = max(1, min(240, int(c.get("durationMinutes", 20) or 20)))
        deadline = now + timedelta(minutes=duration)
        aref.set({
            "challengeId": ref.id, "userId": uid, "status": "started",
            "startedAt": now, "deadlineAt": deadline, "answers": {},
            "questionIds": ids, "questionSnapshot": snapshot,
            "challengeVersion": str(c.get("updatedAt") or ""),
            "source": "telegram_bot_v31.64",
            "updatedAt": now,
        })
        return (attempt_id, {"questionSnapshot": snapshot, "deadlineAt": deadline}), None

    def _handle_inline_query(inline_query):
        """Inline Search: typing '@bmtbot <text>' in any chat lets a student
        search and share published challenges without leaving that chat."""
        query_id = inline_query.get("id")
        text = _clean(inline_query.get("query"), 100).lower()
        from_user = inline_query.get("from") or {}
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not query_id or not token:
            return jsonify({"ok": True}), 200
        try:
            store = db()
            identity = map_telegram_user(store, from_user)
            results = []
            if identity:
                _, user = identity
                for cid, c in public_challenges(store, user)[:20]:
                    title = c.get("title") or "Academic Challenge"
                    if text and text not in title.lower():
                        continue
                    results.append({
                        "type": "article",
                        "id": cid,
                        "title": title,
                        "description": f"{c.get('subject','')} · Grade {c.get('grade','-')} · /startchallenge {cid}",
                        "input_message_content": {"message_text": f"📚 {title}\nStart it with: /startchallenge {cid}"},
                    })
            import requests
            requests.post(f"https://api.telegram.org/bot{token}/answerInlineQuery",
                           json={"inline_query_id": query_id, "results": results[:20], "cache_time": 30},
                           timeout=10)
        except Exception:
            app.logger.exception("Inline query handling failed")
        return jsonify({"ok": True}), 200

    def _handle_menu_callback(callback_query):
        """Tap-menu buttons shown on /start: Challenges and My Result mirror
        the /challenges and /result text commands so a student never has to
        type a command by hand."""
        callback_id = callback_query.get("id")
        data = str(callback_query.get("data") or "")
        cb_message = callback_query.get("message") or {}
        chat_id = (cb_message.get("chat") or {}).get("id")
        tg_user = callback_query.get("from") or {}
        if callback_id:
            answer_callback_query(callback_id)
        if chat_id is None or not tg_user.get("id"):
            return jsonify({"ok": True}), 200
        try:
            store = db()
            identity = map_telegram_user(store, tg_user)
            if not identity:
                telegram_send(chat_id, "Your Telegram account is not linked to an approved student account.")
                return jsonify({"ok": True}), 200
            uid, user = identity
            if data == "menu:challenges":
                rows = public_challenges(store, user)
                if not rows:
                    telegram_send(chat_id, "No active challenges are available for your class.")
                else:
                    lines = ["Available challenges:"]
                    for cid, c in rows:
                        badge = " 🎓 SCHOLARSHIP" if c.get("isScholarshipChallenge") else ""
                        tag_bits = [b for b in [c.get("subject"), c.get("region") if c.get("region") and c.get("region") != "ALL" else ""] if b]
                        tags = f" ({' · '.join(tag_bits)})" if tag_bits else ""
                        lines.append(f"{cid} — {c.get('title') or 'Academic Challenge'}{tags}{badge}")
                    telegram_send(chat_id, "\n".join(lines))
            elif data == "menu:result":
                docs = list(store.collection("challengeAttempts").where("userId", "==", uid).where("status", "==", "submitted").limit(20).stream())
                if not docs:
                    telegram_send(chat_id, "No submitted challenge result was found.")
                else:
                    a = docs[-1].to_dict() or {}
                    telegram_send(chat_id, f"Latest result\nScore: {_num(a.get('score')):g}\nPercentage: {_num(a.get('percentage')):.1f}%\nStatus: {_clean(a.get('statusResult'), 20)}")
        except Exception:
            app.logger.exception("Menu callback handling failed")
        return jsonify({"ok": True}), 200

    @app.post("/api/telegram/webhook")
    def telegram_webhook():
        secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
        supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not secret or not hmac.compare_digest(supplied, secret):
            return jsonify({"error": "Unauthorized."}), 401
        body = request.get_json(silent=True) or {}
        inline_query = body.get("inline_query")
        if inline_query is not None:
            return _handle_inline_query(inline_query)
        callback_query = body.get("callback_query")
        if callback_query is not None:
            return _handle_menu_callback(callback_query)
        message = body.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        tg_user = message.get("from") or {}
        text = _clean(message.get("text"), 500)
        if chat_id is None or not tg_user.get("id"):
            return jsonify({"ok": True}), 200
        try:
            store = db()
            identity = map_telegram_user(store, tg_user)
            if not identity:
                telegram_send(chat_id, "Your Telegram account is not linked to an approved student account.")
                return jsonify({"ok": True}), 200
            uid, user = identity
            cmd, _, arg = text.partition(" ")
            cmd = cmd.split("@", 1)[0].lower()

            if cmd in {"/start", "/help"}:
                base_url = os.getenv("BMT_BASE_URL", "").strip().rstrip("/")
                keyboard_rows = [[{"text": "📋 Challenges", "callback_data": "menu:challenges"}, {"text": "🏆 My Result", "callback_data": "menu:result"}]]
                if base_url.startswith("https://"):
                    keyboard_rows.append([{"text": "🚀 Open BMT Mini App", "web_app": {"url": base_url + "/student"}}])
                markup = {"inline_keyboard": keyboard_rows}
                telegram_send(chat_id,
                    "BMT Academic Challenge\n\n"
                    "/challenges — list available challenges\n"
                    "/startchallenge ID — start a challenge\n"
                    "/answer QUESTION_ID OPTION — save an answer\n"
                    "/submit — submit the active attempt\n"
                    "/result — show your latest result\n\n"
                    "Tip: type @ this bot's username in any chat to search challenges (Inline Search).",
                    reply_markup=markup)
                return jsonify({"ok": True}), 200

            if cmd == "/challenges":
                rows = public_challenges(store, user)
                if not rows:
                    telegram_send(chat_id, "No active challenges are available for your class.")
                else:
                    lines = ["Available challenges:"]
                    for cid, c in rows:
                        badge = " 🎓 SCHOLARSHIP" if c.get("isScholarshipChallenge") else ""
                        tag_bits = [b for b in [c.get("subject"), c.get("region") if c.get("region") and c.get("region") != "ALL" else ""] if b]
                        tags = f" ({' · '.join(tag_bits)})" if tag_bits else ""
                        lines.append(f"{cid} — {c.get('title') or 'Academic Challenge'}{tags}{badge}")
                    telegram_send(chat_id, "\n".join(lines))
                return jsonify({"ok": True}), 200

            if cmd == "/startchallenge":
                cid = _key(arg)
                if not cid:
                    telegram_send(chat_id, "Usage: /startchallenge CHALLENGE_ID")
                    return jsonify({"ok": True}), 200
                started, err = start_attempt(store, uid, cid)
                if err:
                    telegram_send(chat_id, err)
                    return jsonify({"ok": True}), 200
                attempt_id, a = started
                qs = a.get("questionSnapshot") or []
                telegram_send(chat_id, f"Challenge started. Attempt: {attempt_id}\nTime limit is enforced by the server.\nUse /answer QUESTION_ID OPTION for each answer, then /submit.")
                for q in qs:
                    opts = q.get("options") or {}
                    option_text = "\n".join(f"{k}: {v}" for k, v in opts.items())
                    telegram_send(chat_id, f"{q['id']}\n{q['question']}\n{option_text}")
                return jsonify({"ok": True}), 200

            if cmd == "/answer":
                parts = arg.split()
                if len(parts) != 2:
                    telegram_send(chat_id, "Usage: /answer QUESTION_ID OPTION")
                    return jsonify({"ok": True}), 200
                qid, option = _key(parts[0]), _clean(parts[1], 30).upper()
                candidates = list(store.collection("challengeAttempts").where("userId", "==", uid).where("status", "==", "started").limit(10).stream())
                if not candidates:
                    telegram_send(chat_id, "No active challenge attempt.")
                    return jsonify({"ok": True}), 200
                a_snap = candidates[0]; a = a_snap.to_dict() or {}
                if a.get("deadlineAt") and _as_dt(a.get("deadlineAt")) and datetime.now(timezone.utc) > _as_dt(a.get("deadlineAt")):
                    telegram_send(chat_id, "The attempt time has expired. Use /submit.")
                    return jsonify({"ok": True}), 200
                valid = {str(q.get("id")): set((q.get("options") or {}).keys()) for q in (a.get("questionSnapshot") or [])}
                if qid not in valid or option not in valid[qid]:
                    telegram_send(chat_id, "That question or option is not valid for the active attempt.")
                    return jsonify({"ok": True}), 200
                answers = a.get("answers") or {}; answers[qid] = option
                a_snap.reference.update({"answers": answers, "updatedAt": datetime.now(timezone.utc)})
                telegram_send(chat_id, "Answer saved.")
                return jsonify({"ok": True}), 200

            if cmd == "/submit":
                candidates = list(store.collection("challengeAttempts").where("userId", "==", uid).where("status", "==", "started").limit(10).stream())
                if not candidates:
                    telegram_send(chat_id, "No active challenge attempt.")
                    return jsonify({"ok": True}), 200
                snap = candidates[0]; a = snap.to_dict() or {}
                now = datetime.now(timezone.utc)
                expired = bool(_as_dt(a.get("deadlineAt")) and now > _as_dt(a.get("deadlineAt")))
                score = total = 0.0; correct = count = 0
                answers = a.get("answers") or {}
                for q in (a.get("questionSnapshot") or []):
                    points = max(0.0, _num(q.get("points"), 1.0)); total += points; count += 1
                    if _clean(answers.get(q.get("id")), 30).upper() == _clean(q.get("correctAnswer"), 30).upper():
                        score += points; correct += 1
                percentage = round((score / total) * 100, 1) if total else 0.0
                challenge = store.collection("academicChallenges").document(_key(a.get("challengeId"))).get()
                c = challenge.to_dict() or {} if challenge.exists else {}
                pass_mark = max(0.0, min(100.0, _num(c.get("passMark"), 50.0)))
                snap.reference.update({
                    "status": "submitted", "score": score, "totalPoints": total,
                    "percentage": percentage, "correctCount": correct,
                    "questionCount": count, "expiredSubmission": expired,
                    "submittedAt": now, "statusResult": "PASS" if percentage >= pass_mark else "FAIL",
                    "gradingVersion": "V31.64", "updatedAt": now,
                })
                telegram_send(chat_id, f"Submitted.\nScore: {score:g}/{total:g}\nPercentage: {percentage:.1f}%\nResult: {'PASS' if percentage >= pass_mark else 'FAIL'}")
                return jsonify({"ok": True}), 200

            if cmd == "/result":
                docs = list(store.collection("challengeAttempts").where("userId", "==", uid).where("status", "==", "submitted").limit(20).stream())
                if not docs:
                    telegram_send(chat_id, "No submitted challenge result was found.")
                else:
                    a = docs[-1].to_dict() or {}
                    telegram_send(chat_id, f"Latest result\nScore: {_num(a.get('score')):g}\nPercentage: {_num(a.get('percentage')):.1f}%\nStatus: {_clean(a.get('statusResult'), 20)}")
                return jsonify({"ok": True}), 200

            telegram_send(chat_id, "Unknown command. Use /help.")
            return jsonify({"ok": True}), 200
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception("Telegram webhook failed")
            return jsonify({"error": "Telegram integration failed."}), 500
