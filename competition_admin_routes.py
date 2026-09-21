"""V31.108 Competition Center admin completion.

Additive, admin-only READ endpoints that the Mathematics & Aptitude
Competition Center admin panel was missing:

- GET /api/admin/challenges/<id>/participants  - who has an attempt, broken
  out by target audience (Distance / Free Regular / Paid Regular /
  Scholarship).
- GET /api/admin/challenges/<id>/analytics     - aggregate participation,
  completion, pass-rate and certificate-issuance numbers for one challenge.
- GET /api/admin/challenges/<id>/certificates  - which certificates exist
  for this challenge and their status, so an admin can find a certificate
  to revoke (certificate_routes.py's revoke endpoint requires already
  knowing the exact certificateId, and nothing before this exposed one).

This module reuses the exact collections/fields already written by
learning_challenge_routes.py, awards_routes.py, award_ledger_routes.py and
certificate_routes.py (academicChallenges, challengeAttempts,
challengeEntries, awardLedger, users). No new Firestore collection is
introduced, no existing write path is touched, and nothing here can be
reached by a non-admin: every route re-derives the caller's role from
their own `users` document, the same pattern already used by
award_ledger_routes.py's close/finalize/publish-winners and
certificate_routes.py's revoke endpoint. Restricting these views to admin
only (rather than "approved teacher or admin", which challenge
create/list/publish already allow) is a deliberate choice: participants
and certificates carry more identifying detail per row than the existing
teacher-visible challenge list or leaderboard.
"""
import re
from flask import jsonify

from targeting_access import student_audience_set

MAX_ROWS = 5000
TARGET_AUDIENCES = ["Distance", "Free Regular", "Paid Regular", "Scholarship"]


def _key(v):
    return re.sub(r"[^A-Za-z0-9._:-]", "", str(v or ""))[:150]


def _num(v, default=0.0):
    try:
        x = float(v)
        return x if x >= 0 else default
    except (TypeError, ValueError):
        return default


def register_competition_admin_routes(app, require_user, firebase_admin_factory):
    def db():
        fb = firebase_admin_factory()
        if not fb:
            raise RuntimeError("Firebase server credentials are not configured.")
        from firebase_admin import firestore
        return firestore.client()

    def _is_admin(store, detail):
        snap = store.collection("users").document(detail["uid"]).get()
        user = snap.to_dict() or {} if snap.exists else {}
        role = str(user.get("accountType") or user.get("role") or detail.get("role") or "").lower()
        return role == "admin"

    def _load_challenge(store, challenge_id):
        cid = _key(challenge_id)
        snap = store.collection("academicChallenges").document(cid).get()
        if not snap.exists:
            return cid, None
        c = snap.to_dict() or {}
        c["id"] = cid
        return cid, c

    def _participant_rows(store, cid, challenge):
        """One row per student with an attempt on this challenge. Every field
        is re-derived from server-owned records (challengeAttempts, users,
        challengeEntries) - nothing here is client-suppliable."""
        entry_fee = _num(challenge.get("entryFee"))
        verified_entries = set()
        if entry_fee > 0:
            for snap in (store.collection("challengeEntries")
                         .where("challengeId", "==", cid)
                         .where("status", "==", "verified")
                         .limit(MAX_ROWS).stream()):
                uid = _key((snap.to_dict() or {}).get("userId"))
                if uid:
                    verified_entries.add(uid)
        rows = []
        for snap in store.collection("challengeAttempts").where("challengeId", "==", cid).limit(MAX_ROWS).stream():
            a = snap.to_dict() or {}
            uid = _key(a.get("userId"))
            if not uid:
                continue
            user_snap = store.collection("users").document(uid).get()
            user = user_snap.to_dict() or {} if user_snap.exists else {}
            audiences = sorted(student_audience_set(user) & set(TARGET_AUDIENCES))
            attempt_status = str(a.get("status") or "started")[:20]
            rows.append({
                "userId": uid,
                "displayName": str(user.get("displayName") or user.get("name") or "Participant")[:100],
                "learningMode": str(user.get("learningMode") or "Regular")[:30],
                "audiences": audiences,
                "attemptStatus": attempt_status,
                "result": str(a.get("statusResult"))[:10] if attempt_status == "submitted" and a.get("statusResult") else None,
                "score": _num(a.get("score")),
                "percentage": _num(a.get("percentage")),
                "entryVerified": (entry_fee <= 0) or (uid in verified_entries),
                "startedAt": a.get("startedAt"),
                "submittedAt": a.get("submittedAt"),
            })
        return rows

    def _audience_counts(rows):
        counts = {a: 0 for a in TARGET_AUDIENCES}
        for r in rows:
            for a in r["audiences"]:
                counts[a] = counts.get(a, 0) + 1
        return counts

    @app.get("/api/admin/challenges/<challenge_id>/participants")
    def admin_challenge_participants(challenge_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            store = db()
            if not _is_admin(store, detail):
                return jsonify({"error": "Admin access required."}), 403
            cid, challenge = _load_challenge(store, challenge_id)
            if challenge is None:
                return jsonify({"error": "Challenge not found."}), 404
            rows = _participant_rows(store, cid, challenge)
            rows.sort(key=lambda r: (-r["percentage"], str(r.get("submittedAt") or "")))
            return jsonify({
                "success": True,
                "challengeId": cid,
                "participantCount": len(rows),
                "byAudience": _audience_counts(rows),
                "participants": rows[:MAX_ROWS],
            }), 200
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception("Admin participant listing failed")
            return jsonify({"error": "Unable to load participants."}), 500

    @app.get("/api/admin/challenges/<challenge_id>/analytics")
    def admin_challenge_analytics(challenge_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            store = db()
            if not _is_admin(store, detail):
                return jsonify({"error": "Admin access required."}), 403
            cid, challenge = _load_challenge(store, challenge_id)
            if challenge is None:
                return jsonify({"error": "Challenge not found."}), 404
            rows = _participant_rows(store, cid, challenge)
            started = len(rows)
            submitted_rows = [r for r in rows if r["attemptStatus"] == "submitted"]
            submitted = len(submitted_rows)
            passed = len([r for r in submitted_rows if r["result"] == "PASS"])
            avg_pct = round(sum(r["percentage"] for r in submitted_rows) / submitted, 1) if submitted else 0.0

            award_count = 0
            cert_issued = 0
            cert_revoked = 0
            for snap in store.collection("awardLedger").where("challengeId", "==", cid).limit(MAX_ROWS).stream():
                a = snap.to_dict() or {}
                award_count += 1
                cert_status = str(a.get("certificateStatus") or "")
                if cert_status == "issued":
                    cert_issued += 1
                elif cert_status == "revoked":
                    cert_revoked += 1

            return jsonify({
                "success": True,
                "challengeId": cid,
                "status": challenge.get("status"),
                "isScholarshipChallenge": bool(challenge.get("isScholarshipChallenge")),
                "startedCount": started,
                "submittedCount": submitted,
                "completionRate": round((submitted / started) * 100, 1) if started else 0.0,
                "passCount": passed,
                "passRate": round((passed / submitted) * 100, 1) if submitted else 0.0,
                "averagePercentage": avg_pct,
                "byAudience": _audience_counts(rows),
                "awardCount": award_count,
                "certificatesIssued": cert_issued,
                "certificatesRevoked": cert_revoked,
            }), 200
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception("Admin challenge analytics failed")
            return jsonify({"error": "Unable to load analytics."}), 500

    @app.get("/api/admin/challenges/<challenge_id>/certificates")
    def admin_challenge_certificates(challenge_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            store = db()
            if not _is_admin(store, detail):
                return jsonify({"error": "Admin access required."}), 403
            cid, challenge = _load_challenge(store, challenge_id)
            if challenge is None:
                return jsonify({"error": "Challenge not found."}), 404
            rows = []
            for snap in store.collection("awardLedger").where("challengeId", "==", cid).limit(MAX_ROWS).stream():
                a = snap.to_dict() or {}
                cert_status = str(a.get("certificateStatus") or "not_issued")
                if cert_status == "not_issued":
                    continue
                uid = _key(a.get("userId"))
                user = {}
                if uid:
                    user_snap = store.collection("users").document(uid).get()
                    user = user_snap.to_dict() or {} if user_snap.exists else {}
                rows.append({
                    "awardId": snap.id,
                    "certificateId": a.get("certificateId"),
                    "userId": uid,
                    "displayName": str(user.get("displayName") or user.get("name") or "Participant")[:100],
                    "rank": int(a.get("rank", 0) or 0),
                    "status": cert_status,
                    "issuedAt": a.get("certificateIssuedAt"),
                    "revokedAt": a.get("certificateRevokedAt"),
                })
            rows.sort(key=lambda r: r.get("rank", 0))
            return jsonify({
                "success": True,
                "challengeId": cid,
                "certificateCount": len(rows),
                "certificates": rows[:MAX_ROWS],
            }), 200
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception("Admin certificate listing failed")
            return jsonify({"error": "Unable to load certificates."}), 500
