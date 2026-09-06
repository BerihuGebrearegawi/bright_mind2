"""V31.53/V31.56 server-authoritative 5-or-7 round qualification policy.

Additive module for Academic Challenge. Policy is stored on the challenge document,
not trusted from the client at grading time. Default: 7 rounds, 70% advancement.
An administrator may publish a qualification round of 5 or 7 only.
"""
from datetime import datetime, timezone
import re
from flask import jsonify, request

MAX_SUPPORTED_ROUND = 7
ALLOWED_QUALIFICATION_ROUNDS = {5, 7}
DEFAULT_QUALIFICATION_ROUND = 7
DEFAULT_ADVANCEMENT_PERCENTAGE = 70.0


def _key(v):
    return re.sub(r"[^A-Za-z0-9._:-]", "", str(v or ""))[:150]


def _pct(v):
    try:
        x = float(v)
        return max(0.0, min(100.0, x))
    except (TypeError, ValueError):
        return 0.0


def _round_number(v):
    try:
        x = int(v)
    except (TypeError, ValueError):
        return 1
    return max(1, min(MAX_SUPPORTED_ROUND, x))


def policy_from_challenge(challenge):
    qround = _round_number(challenge.get("qualificationRound", DEFAULT_QUALIFICATION_ROUND))
    if qround not in ALLOWED_QUALIFICATION_ROUNDS:
        qround = DEFAULT_QUALIFICATION_ROUND
    threshold = _pct(challenge.get("advancementPercentage", DEFAULT_ADVANCEMENT_PERCENTAGE))
    return {"qualificationRound": qround, "advancementPercentage": threshold}


def evaluate_rounds(round_scores, policy):
    """Return deterministic qualification state from server-derived round scores."""
    target = policy["qualificationRound"]
    threshold = policy["advancementPercentage"]
    scores = {int(k): _pct(v) for k, v in (round_scores or {}).items()}
    passed = []
    failed = []
    for r in range(1, target + 1):
        score = scores.get(r)
        if score is None:
            return {
                "qualified": False,
                "qualificationRound": target,
                "advancementPercentage": threshold,
                "completedThroughRound": r - 1,
                "nextRequiredRound": r,
                "passedRounds": passed,
                "failedRounds": failed,
                "reason": "Complete the required round before advancing.",
            }
        if score < threshold:
            failed.append(r)
            return {
                "qualified": False,
                "qualificationRound": target,
                "advancementPercentage": threshold,
                "completedThroughRound": r - 1,
                "nextRequiredRound": r,
                "passedRounds": passed,
                "failedRounds": failed,
                "reason": "The round score is below the published advancement threshold.",
            }
        passed.append(r)
    return {
        "qualified": True,
        "qualificationRound": target,
        "advancementPercentage": threshold,
        "completedThroughRound": target,
        "nextRequiredRound": None,
        "passedRounds": passed,
        "failedRounds": failed,
        "reason": "All required rounds meet the published advancement threshold.",
    }


def register_qualification_policy_routes(app, require_user, firebase_admin_factory):
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

    @app.get("/api/challenges/<challenge_id>/qualification")
    def challenge_qualification(challenge_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            store = db(); cid = _key(challenge_id)
            cs = store.collection("academicChallenges").document(cid).get()
            if not cs.exists:
                return jsonify({"error": "Challenge not found."}), 404
            challenge = cs.to_dict() or {}
            policy = policy_from_challenge(challenge)

            if _pct(challenge.get("entryFee", 0)) > 0:
                entry = None
                for es in store.collection("challengeEntries").where("challengeId", "==", cid).where("userId", "==", detail["uid"]).where("status", "==", "verified").limit(1).stream():
                    entry = es
                    break
                if entry is None:
                    return jsonify({"error": "Verified challenge entry required."}), 403

            attempts = {}
            for snap in store.collection("challengeAttempts").where("userId", "==", detail["uid"]).where("status", "==", "submitted").limit(500).stream():
                x = snap.to_dict() or {}
                if _key(x.get("challengeId")) == cid:
                    r = _round_number(x.get("roundNumber") or challenge.get("roundNumber") or 1)
                    attempts[r] = max(attempts.get(r, 0.0), _pct(x.get("percentage")))
            result = evaluate_rounds(attempts, policy)
            return jsonify({"success": True, "challengeId": cid, "roundScores": attempts, **result})
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception("Challenge qualification lookup failed")
            return jsonify({"error": "Unable to load challenge qualification."}), 500

    @app.post("/api/admin/challenges/<challenge_id>/qualification-policy")
    def configure_qualification_policy(challenge_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            store = db(); uid = detail["uid"]
            if role(store, detail) != "admin":
                return jsonify({"error": "Admin access required."}), 403
            cid = _key(challenge_id)
            ref = store.collection("academicChallenges").document(cid)
            snap = ref.get()
            if not snap.exists:
                return jsonify({"error": "Challenge not found."}), 404
            body = request.get_json(silent=True) or {}
            # Qualification policy is a published fairness rule. It may be set before
            # publication, but cannot be changed after the challenge is published or
            # after an attempt exists. This prevents mid-competition rule changes.
            if str((snap.to_dict() or {}).get("status") or "").lower() == "published":
                return jsonify({"error": "Qualification policy is locked after publication."}), 409
            if any(store.collection("challengeAttempts").where("challengeId", "==", cid).limit(1).stream()):
                return jsonify({"error": "Qualification policy is locked after challenge attempts begin."}), 409
            try:
                qround = int(body.get("qualificationRound", DEFAULT_QUALIFICATION_ROUND))
                threshold = float(body.get("advancementPercentage", DEFAULT_ADVANCEMENT_PERCENTAGE))
            except (TypeError, ValueError):
                return jsonify({"error": "Invalid qualification policy."}), 400
            if qround not in ALLOWED_QUALIFICATION_ROUNDS:
                return jsonify({"error": "qualificationRound must be 5 or 7."}), 400
            if threshold < 0 or threshold > 100:
                return jsonify({"error": "advancementPercentage must be between 0 and 100."}), 400
            now = datetime.now(timezone.utc)
            ref.update({
                "qualificationRound": qround,
                "advancementPercentage": round(threshold, 2),
                "qualificationPolicyUpdatedBy": uid,
                "qualificationPolicyUpdatedAt": now,
                "updatedAt": now,
            })
            try:
                from firebase_admin import firestore
                store.collection("adminAuditLogs").add({
                    "action": "challenge_qualification_policy_updated",
                    "challengeId": cid,
                    "qualificationRound": qround,
                    "advancementPercentage": round(threshold, 2),
                    "actorUid": uid,
                    "createdAt": now,
                })
            except Exception:
                app.logger.exception("Qualification policy audit write failed")
                return jsonify({"error": "Policy updated but audit logging failed."}), 500
            return jsonify({"success": True, "challengeId": cid, "qualificationRound": qround, "advancementPercentage": round(threshold, 2)})
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception("Qualification policy update failed")
            return jsonify({"error": "Unable to update qualification policy."}), 500
