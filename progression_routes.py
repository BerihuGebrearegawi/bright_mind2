"""V31.40 additive Academic Challenge progression/readiness.

This module is intentionally read-only from the student's perspective. It derives
practice mastery and completed challenge history server-side and never accepts
client-supplied scores, ranks, or qualification decisions.
"""
from datetime import datetime, timezone
import re
from flask import jsonify, request

MAX_ROUND = 7
DEFAULT_PRACTICE_MASTERY = 80.0
DEFAULT_MIN_PRACTICE_SESSIONS = 2


def _key(v):
    return re.sub(r"[^A-Za-z0-9._:-]", "", str(v or ""))[:150]


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def register_progression_routes(app, require_user, firebase_admin_factory):
    def db():
        fb = firebase_admin_factory()
        if not fb:
            raise RuntimeError("Firebase server credentials are not configured.")
        from firebase_admin import firestore
        return firestore.client()

    @app.get("/api/challenges/progression")
    def challenge_progression():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            store = db(); uid = detail["uid"]
            user_snap = store.collection("users").document(uid).get()
            user = user_snap.to_dict() or {} if user_snap.exists else {}
            role = str(user.get("accountType") or user.get("role") or detail.get("role") or "").lower()
            if role != "student":
                return jsonify({"error": "Only student accounts can view challenge progression."}), 403

            grade = str(user.get("className") or user.get("class") or user.get("grade") or "").strip()
            practice = []
            for snap in store.collection("practiceSessions").where("userId", "==", uid).limit(200).stream():
                x = snap.to_dict() or {}
                if x.get("status") != "submitted":
                    continue
                practice.append({
                    "bookId": str(x.get("bookId") or ""),
                    "chapterId": str(x.get("chapterId") or ""),
                    "subchapterId": str(x.get("subchapterId") or ""),
                    "percentage": _num(x.get("percentage")),
                    "submittedAt": x.get("submittedAt"),
                })

            chapters = {}
            for x in practice:
                chapter_id = x["chapterId"]
                if not chapter_id:
                    continue
                row = chapters.setdefault(chapter_id, {"bookId": x["bookId"], "attempts": 0, "bestPercentage": 0.0, "averagePercentage": 0.0, "subchapters": {}})
                row["attempts"] += 1
                row["bestPercentage"] = max(row["bestPercentage"], x["percentage"])
                if x["subchapterId"]:
                    sub = row["subchapters"].setdefault(x["subchapterId"], {"attempts": 0, "bestPercentage": 0.0})
                    sub["attempts"] += 1
                    sub["bestPercentage"] = max(sub["bestPercentage"], x["percentage"])

            for row in chapters.values():
                if row["attempts"]:
                    vals = [x["percentage"] for x in practice if x["chapterId"] and x["chapterId"] in chapters and chapters[x["chapterId"]] is row]
                    row["averagePercentage"] = round(sum(vals) / len(vals), 1) if vals else 0.0
                row["mastery"] = bool(row["attempts"] >= DEFAULT_MIN_PRACTICE_SESSIONS and row["bestPercentage"] >= DEFAULT_PRACTICE_MASTERY)
                row["subchapters"] = [{"id": k, **v, "mastery": bool(v["attempts"] >= 1 and v["bestPercentage"] >= DEFAULT_PRACTICE_MASTERY)} for k, v in sorted(row["subchapters"].items())]

            challenges = []
            for snap in store.collection("challengeAttempts").where("userId", "==", uid).limit(200).stream():
                x = snap.to_dict() or {}
                if x.get("status") != "submitted":
                    continue
                cid = str(x.get("challengeId") or "")
                if not cid:
                    continue
                cs = store.collection("academicChallenges").document(_key(cid)).get()
                c = cs.to_dict() or {} if cs.exists else {}
                challenges.append({
                    "challengeId": cid,
                    "roundNumber": max(1, min(MAX_ROUND, int(c.get("roundNumber", 1) or 1))),
                    "season": str(c.get("season") or ""),
                    "percentage": _num(x.get("percentage")),
                    "submittedAt": x.get("submittedAt"),
                })

            best_by_round = {}
            for x in challenges:
                r = x["roundNumber"]
                best_by_round[r] = max(best_by_round.get(r, 0.0), x["percentage"])

            # Readiness is a recommendation, not an automatic qualification decision.
            # Official qualification remains the challenge leaderboard's server-derived rule.
            rounds = []
            previous = None
            for r in range(1, MAX_ROUND + 1):
                if r == 1:
                    ready = True
                    reason = "Round 1 is open to eligible students when a published challenge exists."
                else:
                    previous = best_by_round.get(r - 1, 0.0)
                    ready = previous >= 70.0
                    reason = "Previous-round score meets the readiness threshold." if ready else "Complete the previous round and reach at least 70% to be ready for the next round."
                rounds.append({"roundNumber": r, "ready": ready, "previousRoundBestPercentage": previous, "reason": reason})

            return jsonify({
                "success": True,
                "grade": grade,
                "practiceMasteryThreshold": DEFAULT_PRACTICE_MASTERY,
                "minimumPracticeSessions": DEFAULT_MIN_PRACTICE_SESSIONS,
                "chapters": chapters,
                "challengeBestByRound": best_by_round,
                "rounds": rounds,
                "note": "Readiness is informational; official qualification is determined server-side from the published challenge rules."
            })
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception("Challenge progression lookup failed")
            return jsonify({"error": "Unable to load challenge progression."}), 500
