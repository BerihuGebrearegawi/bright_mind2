"""V31.41 deterministic Academic Challenge awards.

Additive module: does not alter A-owned UI or payment verification. Award
eligibility is derived server-side from published challenge rules and submitted
challenge attempts. No client-supplied rank, score, prize, or award decision is trusted.
"""
from datetime import datetime, timezone
from flask import jsonify, request

MAX_AWARDS = 100
DEFAULT_PRIZE_SHARE = 0.70


def _key(v):
    import re
    return re.sub(r"[^A-Za-z0-9._:-]", "", str(v or ""))[:150]


def _num(v, default=0.0):
    try:
        x = float(v)
        return x if x >= 0 else default
    except (TypeError, ValueError):
        return default


def register_award_routes(app, require_user, firebase_admin_factory):
    def db():
        fb = firebase_admin_factory()
        if not fb:
            raise RuntimeError("Firebase server credentials are not configured.")
        from firebase_admin import firestore
        return firestore.client()

    def identity_role(store, detail):
        uid = detail["uid"]
        snap = store.collection("users").document(uid).get()
        user = snap.to_dict() or {} if snap.exists else {}
        return str(user.get("accountType") or user.get("role") or detail.get("role") or "").lower()

    @app.get("/api/challenges/<challenge_id>/awards")
    def challenge_awards(challenge_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            store = db(); uid = detail["uid"]; role = identity_role(store, detail)
            if role not in {"student", "teacher", "admin"}:
                return jsonify({"error": "Authenticated access required."}), 403
            if role == "teacher":
                ts = store.collection("teachers").document(uid).get()
                if not ts.exists or not (ts.to_dict() or {}).get("approved"):
                    return jsonify({"error": "Approved teacher access required."}), 403

            cid = _key(challenge_id)
            cs = store.collection("academicChallenges").document(cid).get()
            if not cs.exists:
                return jsonify({"error": "Challenge not found."}), 404
            c = cs.to_dict() or {}
            award_count = max(0, min(MAX_AWARDS, int(c.get("awardCount", c.get("qualificationCount", 0)) or 0)))
            certificate_enabled = bool(c.get("certificateEnabled", True))
            prize_share = min(1.0, max(0.0, _num(c.get("prizePoolShare"), DEFAULT_PRIZE_SHARE)))
            currency = str(c.get("prizeCurrency") or "ETB")[:10]

            verified_users = set()
            if _num(c.get("entryFee")) > 0:
                for snap in store.collection("challengeEntries").where("challengeId", "==", cid).where("status", "==", "verified").limit(5000).stream():
                    e = snap.to_dict() or {}
                    eu = _key(e.get("userId"))
                    if eu:
                        verified_users.add(eu)
            rows_by_user = {}
            def _epoch(v):
                if v is None: return float("inf")
                if hasattr(v, "timestamp"):
                    try: return float(v.timestamp())
                    except Exception: pass
                try: return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
                except Exception: return float("inf")
            for snap in store.collection("challengeAttempts").where("challengeId", "==", cid).where("status", "==", "submitted").limit(5000).stream():
                x = snap.to_dict() or {}
                attempt_user = _key(x.get("userId"))
                if not attempt_user or (_num(c.get("entryFee")) > 0 and attempt_user not in verified_users):
                    continue
                row = {"userId": attempt_user, "score": _num(x.get("score")), "percentage": _num(x.get("percentage")), "submittedAt": x.get("submittedAt")}
                prior = rows_by_user.get(attempt_user)
                if prior is None or (row["percentage"], row["score"], -_epoch(row.get("submittedAt"))) > (prior["percentage"], prior["score"], -_epoch(prior.get("submittedAt"))):
                    rows_by_user[attempt_user] = row
            rows = list(rows_by_user.values())

            # Mirror finalization qualification: for a seasonal multi-round
            # challenge, a student must meet the published threshold in every
            # round through the current target round.
            target_round = max(1, min(7, int(c.get("qualificationRound", c.get("roundNumber", 1)) or 1)))
            threshold = max(0.0, min(100.0, _num(c.get("advancementPercentage", 70.0))))
            season = str(c.get("season") or "").strip()
            if season:
                season_challenges = []
                for ss in store.collection("academicChallenges").where("season", "==", season).limit(200).stream():
                    sc = ss.to_dict() or {}
                    if sc.get("status") == "published":
                        season_challenges.append((ss.id, max(1, min(7, int(sc.get("roundNumber", 1) or 1)))))
                season_ids = {sid for sid, _ in season_challenges}
                best_by_user_round = {}
                for snap in store.collection("challengeAttempts").where("status", "==", "submitted").limit(5000).stream():
                    x = snap.to_dict() or {}; cid2 = _key(x.get("challengeId")); uid = _key(x.get("userId"))
                    if cid2 not in season_ids or not uid:
                        continue
                    rn = next((r for sid, r in season_challenges if sid == cid2), 1)
                    key = (uid, rn); candidate = {"percentage": _num(x.get("percentage")), "score": _num(x.get("score")), "submittedAt": x.get("submittedAt")}
                    prior = best_by_user_round.get(key)
                    if prior is None or (candidate["percentage"], candidate["score"], -_epoch(candidate.get("submittedAt"))) > (prior["percentage"], prior["score"], -_epoch(prior.get("submittedAt"))):
                        best_by_user_round[key] = candidate
                rows = [r for r in rows if all((r["userId"], rn) in best_by_user_round and best_by_user_round[(r["userId"], rn)]["percentage"] >= threshold for rn in range(1, target_round + 1))]

            rows.sort(key=lambda x: (-x["percentage"], -x["score"], _epoch(x.get("submittedAt")), x["userId"]))

            # Only the server-derived leaderboard position can determine awards.
            for i, row in enumerate(rows, 1):
                row["rank"] = i
                row["qualified"] = bool(award_count and i <= award_count)
                row["certificateEligible"] = bool(certificate_enabled and row["qualified"])

            # Prize pool is informational until verified challenge-entry revenue exists.
            # This intentionally does not infer money from generic payments.
            entries = []
            for snap in store.collection("challengeEntries").where("challengeId", "==", cid).where("status", "==", "verified").limit(5000).stream():
                e = snap.to_dict() or {}
                entries.append(_num(e.get("amount")))
            verified_revenue = round(sum(entries), 2)
            prize_pool = round(verified_revenue * prize_share, 2)

            # Equal-share allocation is only produced for currently qualified rows;
            # an admin can publish a different deterministic rule later.
            qualified = [r for r in rows if r["qualified"]]
            per_award = round(prize_pool / len(qualified), 2) if qualified and prize_pool > 0 else 0.0
            remainder = round(prize_pool - (per_award * len(qualified)), 2) if qualified else 0.0
            for i, r in enumerate(qualified, 1):
                # Keep preview allocation identical to the finalized award ledger.
                r["prizeAmount"] = round(per_award + (remainder if i == 1 else 0.0), 2)
                r["prizeCurrency"] = currency

            if role == "student":
                mine = next((r for r in rows if r.get("userId") == uid), None)
                result = None if mine is None else {k: v for k, v in mine.items() if k not in {"userId"}}
                return jsonify({"challengeId": cid, "award": result, "certificateEnabled": certificate_enabled, "prizeCurrency": currency})

            for r in rows:
                r.pop("userId", None)
            return jsonify({
                "challengeId": cid,
                "awardCount": award_count,
                "certificateEnabled": certificate_enabled,
                "verifiedEntryRevenue": verified_revenue,
                "prizePoolShare": prize_share,
                "prizePool": prize_pool,
                "prizeCurrency": currency,
                "awards": rows[:MAX_AWARDS],
                "note": "Prize figures use only verified challengeEntries. Generic account payments are never treated as challenge revenue."
            })
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception("Challenge award lookup failed")
            return jsonify({"error": "Unable to load challenge awards."}), 500

    @app.post("/api/admin/challenges/<challenge_id>/award-policy")
    def configure_award_policy(challenge_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            store = db(); uid = detail["uid"]
            role = identity_role(store, detail)
            if role != "admin":
                return jsonify({"error": "Admin access required."}), 403
            ref = store.collection("academicChallenges").document(_key(challenge_id)); snap = ref.get()
            if not snap.exists:
                return jsonify({"error": "Challenge not found."}), 404
            body = request.get_json(silent=True) or {}
            award_count = max(0, min(MAX_AWARDS, int(body.get("awardCount", 0) or 0)))
            share = _num(body.get("prizePoolShare"), DEFAULT_PRIZE_SHARE)
            if share > 1:
                return jsonify({"error": "prizePoolShare must be between 0 and 1."}), 400
            certificate_enabled = bool(body.get("certificateEnabled", True))
            currency = str(body.get("prizeCurrency") or "ETB").strip().upper()[:10]
            now = datetime.now(timezone.utc)
            ref.update({"awardCount": award_count, "prizePoolShare": share, "certificateEnabled": certificate_enabled, "prizeCurrency": currency, "awardPolicyUpdatedBy": uid, "awardPolicyUpdatedAt": now, "updatedAt": now})
            return jsonify({"success": True, "challengeId": ref.id, "awardCount": award_count, "prizePoolShare": share, "certificateEnabled": certificate_enabled, "prizeCurrency": currency})
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid award policy."}), 400
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception("Award policy update failed")
            return jsonify({"error": "Unable to update award policy."}), 500
