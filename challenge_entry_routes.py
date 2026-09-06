"""V31.45 additive challenge-entry/payment verification bridge.

This module does not modify the existing payment implementation. It only
accepts an existing server-approved payment and, after strict challenge and
amount checks, creates a verified challengeEntries record used by award
finalization.
"""
from datetime import datetime, timezone
import hashlib
import re
from flask import jsonify, request

MAX_FEE = 1000000.0


def _key(v):
    return re.sub(r"[^A-Za-z0-9._:-]", "", str(v or ""))[:150]


def _money(v):
    try:
        x = float(v)
        if x < 0 or x > MAX_FEE:
            return None
        return round(x, 2)
    except (TypeError, ValueError):
        return None


def _entry_id(challenge_id, uid):
    return hashlib.sha256((challenge_id + ":" + uid).encode("utf-8")).hexdigest()[:40]


def register_challenge_entry_routes(app, require_user, firebase_admin_factory):
    def db():
        fb = firebase_admin_factory()
        if not fb:
            raise RuntimeError("Firebase server credentials are not configured.")
        from firebase_admin import firestore
        return firestore.client()

    def role(store, detail):
        snap = store.collection("users").document(detail["uid"]).get()
        user = snap.to_dict() or {} if snap.exists else {}
        return str(user.get("accountType") or user.get("role") or detail.get("role") or "").lower()

    @app.post("/api/challenges/<challenge_id>/entry/confirm")
    def confirm_challenge_entry(challenge_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            store = db(); uid = detail["uid"]; cid = _key(challenge_id)
            if role(store, detail) != "student":
                return jsonify({"error": "Only student accounts can register for challenges."}), 403

            body = request.get_json(silent=True) or {}
            payment_id = _key(body.get("paymentId"))

            cref = store.collection("academicChallenges").document(cid)
            cs = cref.get()
            if not cs.exists:
                return jsonify({"error": "Challenge not found."}), 404
            challenge = cs.to_dict() or {}
            if challenge.get("status") != "published":
                return jsonify({"error": "Challenge is not available for registration."}), 409

            fee = _money(challenge.get("entryFee", 0))
            currency = str(challenge.get("entryCurrency") or "ETB")[:10].upper()
            if fee is None:
                return jsonify({"error": "Invalid challenge entry fee configuration."}), 500
            if fee <= 0:
                # Free challenges create a verified zero-value entry without payment.
                eid = _entry_id(cid, uid)
                eref = store.collection("challengeEntries").document(eid)
                existing = eref.get()
                if existing.exists:
                    row = existing.to_dict() or {}
                    return jsonify({"success": True, "alreadyRegistered": True, "entryId": eid, "status": row.get("status", "verified")}), 200
                now = datetime.now(timezone.utc)
                eref.set({"challengeId": cid, "userId": uid, "paymentId": None, "amount": 0.0, "currency": currency,
                          "status": "verified", "verifiedAt": now, "source": "server_verified_free_challenge"})
                return jsonify({"success": True, "entryId": eid, "challengeId": cid, "status": "verified", "amount": 0.0, "currency": currency}), 201

            if not payment_id:
                return jsonify({"error": "paymentId is required for paid challenges."}), 400
            pref = store.collection("payments").document(payment_id)
            ps = pref.get()
            if not ps.exists:
                return jsonify({"error": "Payment not found."}), 404
            payment = ps.to_dict() or {}
            if str(payment.get("uid") or "") != uid:
                return jsonify({"error": "Payment does not belong to this account."}), 403
            payment_challenge_id = _key(payment.get("challengeId") or ((payment.get("metadata") or {}).get("challengeId") if isinstance(payment.get("metadata"), dict) else ""))
            if payment_challenge_id != cid:
                return jsonify({"error": "Payment is not scoped to this challenge."}), 409
            if str(payment.get("status") or "").lower() not in {"approved", "verified", "success", "successful", "paid", "completed"}:
                return jsonify({"error": "Payment has not been verified yet."}), 409
            plan = str(payment.get("plan") or "").strip().lower()
            if plan not in {"challenge_entry", "academic_challenge", "challenge"}:
                return jsonify({"error": "Payment is not a challenge-entry payment."}), 409
            paid = _money(payment.get("amount"))
            paid_currency = str(payment.get("currency") or payment.get("prizeCurrency") or "ETB")[:10].upper()
            if paid is None or abs(paid - fee) > 0.009 or paid_currency != currency:
                return jsonify({"error": "Verified payment amount or currency does not match the challenge entry fee."}), 409

            eid = _entry_id(cid, uid)
            eref = store.collection("challengeEntries").document(eid)
            existing = eref.get()
            if existing.exists:
                row = existing.to_dict() or {}
                return jsonify({"success": True, "alreadyRegistered": True, "entryId": eid, "status": row.get("status", "verified")}), 200

            now = datetime.now(timezone.utc)
            eref.set({
                "challengeId": cid,
                "userId": uid,
                "paymentId": payment_id,
                "amount": paid,
                "currency": currency,
                "status": "verified",
                "verifiedAt": now,
                "source": "server_verified_existing_payment",
            })
            return jsonify({"success": True, "entryId": eid, "challengeId": cid, "status": "verified", "amount": paid, "currency": currency}), 201
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception("Challenge entry confirmation failed")
            return jsonify({"error": "Unable to confirm challenge entry."}), 500

    @app.post("/api/admin/challenges/<challenge_id>/entry-fee")
    def configure_entry_fee(challenge_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            store = db(); uid = detail["uid"]
            if role(store, detail) != "admin":
                return jsonify({"error": "Admin access required."}), 403
            cid = _key(challenge_id); ref = store.collection("academicChallenges").document(cid)
            snap = ref.get()
            if not snap.exists:
                return jsonify({"error": "Challenge not found."}), 404
            body = request.get_json(silent=True) or {}
            fee = _money(body.get("entryFee"))
            currency = str(body.get("entryCurrency") or "ETB")[:10].upper()
            if fee is None or fee < 0:
                return jsonify({"error": "entryFee must be a non-negative amount."}), 400
            if not re.fullmatch(r"[A-Z]{3,10}", currency):
                return jsonify({"error": "entryCurrency must be a valid uppercase currency code."}), 400
            now = datetime.now(timezone.utc)
            ref.update({"entryFee": fee, "entryCurrency": currency, "entryFeeUpdatedAt": now, "entryFeeUpdatedBy": uid, "updatedAt": now})
            return jsonify({"success": True, "challengeId": cid, "entryFee": fee, "entryCurrency": currency}), 200
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception("Challenge entry fee configuration failed")
            return jsonify({"error": "Unable to configure challenge entry fee."}), 500
