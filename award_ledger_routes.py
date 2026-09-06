"""V31.42 additive award ledger and certificate issuance metadata.

Server-authoritative, idempotent award finalization. This module never accepts a
client-supplied score, rank, prize amount, or certificate decision.
"""
from datetime import datetime, timezone
import hashlib
import re
from decimal import Decimal, InvalidOperation
from flask import jsonify


def _submission_epoch(value):
    """Return a stable sortable timestamp for Firestore Timestamp/ISO values."""
    if value is None:
        return float("inf")
    if hasattr(value, "timestamp"):
        try:
            return float(value.timestamp())
        except Exception:
            pass
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return float("inf")

MAX_AWARDS = 100


def _key(v):
    return re.sub(r"[^A-Za-z0-9._:-]", "", str(v or ""))[:150]


def _num(v):
    try:
        x = float(v)
        return x if x >= 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _money(v):
    try:
        x = Decimal(str(v))
        if not x.is_finite() or x < 0:
            return None
        return x.quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _stamp_id(challenge_id, user_id):
    raw = f"{challenge_id}:{user_id}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:40]


def register_award_ledger_routes(app, require_user, firebase_admin_factory):
    def db():
        fb = firebase_admin_factory()
        if not fb:
            raise RuntimeError("Firebase server credentials are not configured.")
        from firebase_admin import firestore
        return firestore.client(), firestore

    def role(store, detail):
        snap = store.collection("users").document(detail["uid"]).get()
        user = snap.to_dict() or {} if snap.exists else {}
        return str(user.get("accountType") or user.get("role") or detail.get("role") or "").lower()

    @app.post("/api/admin/challenges/<challenge_id>/close")
    def close_challenge(challenge_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            store, _ = db()
            if role(store, detail) != "admin":
                return jsonify({"error": "Admin access required."}), 403
            cid = _key(challenge_id)
            ref = store.collection("academicChallenges").document(cid)
            snap = ref.get()
            if not snap.exists:
                return jsonify({"error": "Challenge not found."}), 404
            c = snap.to_dict() or {}
            if c.get("status") == "finalized":
                return jsonify({"error": "Finalized challenges cannot be closed again."}), 409
            if c.get("status") == "closed":
                return jsonify({"success": True, "challengeId": cid, "status": "closed", "alreadyClosed": True}), 200
            if c.get("status") not in ("published", "active"):
                return jsonify({"error": "Only PUBLISHED or ACTIVE challenges can be closed."}), 409
            # Do not manually close a live challenge while participants still
            # have time remaining; that would create open attempts that can
            # never be submitted and would block deterministic finalization.
            now = datetime.now(timezone.utc)
            end_at = c.get("endsAt")
            if end_at is not None:
                if not hasattr(end_at, "timestamp"):
                    try:
                        end_at = datetime.fromisoformat(str(end_at).replace("Z", "+00:00"))
                    except ValueError:
                        return jsonify({"error": "Invalid challenge end time."}), 500
                if getattr(end_at, "tzinfo", None) is None or end_at.utcoffset() is None:
                    return jsonify({"error": "Challenge end time must be timezone-aware."}), 500
                if now < end_at:
                    open_attempt = next(store.collection("challengeAttempts").where("challengeId", "==", cid).where("status", "==", "started").limit(1).stream(), None)
                    if open_attempt is not None:
                        return jsonify({"error": "Challenge has open attempts; wait until they are submitted or expired."}), 409
            ref.update({"status": "closed", "closedAt": now, "closedBy": detail["uid"], "updatedAt": now})
            return jsonify({"success": True, "challengeId": cid, "status": "closed"}), 200
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception("Challenge close failed")
            return jsonify({"error": "Unable to close challenge."}), 500

    @app.post("/api/admin/challenges/<challenge_id>/finalize-awards")
    def finalize_awards(challenge_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            store, firestore = db()
            if role(store, detail) != "admin":
                return jsonify({"error": "Admin access required."}), 403
            cid = _key(challenge_id)
            cref = store.collection("academicChallenges").document(cid)
            cs = cref.get()
            if not cs.exists:
                return jsonify({"error": "Challenge not found."}), 404
            c = cs.to_dict() or {}
            end_at = c.get("endsAt")
            if end_at is not None:
                if not hasattr(end_at, "timestamp"):
                    try:
                        end_at = datetime.fromisoformat(str(end_at).replace("Z", "+00:00"))
                    except ValueError:
                        return jsonify({"error": "Invalid challenge end time."}), 500
                if getattr(end_at, "tzinfo", None) is None or end_at.utcoffset() is None:
                    return jsonify({"error": "Challenge end time must be timezone-aware."}), 500
                if datetime.now(timezone.utc) < end_at:
                    return jsonify({"error": "Challenge is still open; awards cannot be finalized yet."}), 409
            award_count = max(0, min(MAX_AWARDS, int(c.get("awardCount", 0) or 0)))
            if award_count == 0:
                return jsonify({"error": "No award slots are configured."}), 400
            if not bool(c.get("published", c.get("status") == "published")):
                return jsonify({"error": "Only a published challenge can be finalized."}), 409

            # Do not finalize while any participant still has an open attempt.
            # Otherwise a late submission could be omitted from the final ranking.
            open_attempt = next(store.collection("challengeAttempts").where("challengeId", "==", cid).where("status", "==", "started").limit(1).stream(), None)
            if open_attempt is not None:
                return jsonify({"error": "There are still open challenge attempts; finalize after all attempts are submitted or expired."}), 409

            verified_users = set()
            for snap in store.collection("challengeEntries").where("challengeId", "==", cid).where("status", "==", "verified").limit(5000).stream():
                row = snap.to_dict() or {}
                user_id = _key(row.get("userId"))
                if user_id:
                    verified_users.add(user_id)

            # Collapse legacy duplicate attempts to one best result per student.
            # Ranking is deterministic: percentage, score, earliest submission,
            # then user ID as a stable final tie-break.
            by_user = {}
            for snap in store.collection("challengeAttempts").where("challengeId", "==", cid).where("status", "==", "submitted").limit(5000).stream():
                x = snap.to_dict() or {}
                uid = _key(x.get("userId"))
                if not uid:
                    continue
                if _num(c.get("entryFee")) > 0 and uid not in verified_users:
                    continue
                row = {"userId": uid, "score": _num(x.get("score")), "percentage": _num(x.get("percentage")), "submittedAt": x.get("submittedAt")}
                prior = by_user.get(uid)
                if prior is None or (row["percentage"], row["score"], -_submission_epoch(row.get("submittedAt"))) > (prior["percentage"], prior["score"], -_submission_epoch(prior.get("submittedAt"))):
                    by_user[uid] = row
            attempts = list(by_user.values())

            # Final awards must honor the published 5/7-round qualification policy.
            # A season is a sequence of challenge documents sharing the same season;
            # each round is represented by its challenge.roundNumber. For each user,
            # use the best submitted result in each round and require every round
            # through the target round to meet the published threshold.
            target_round = max(1, min(7, int(c.get("qualificationRound", c.get("roundNumber", 1)) or 1)))
            threshold = max(0.0, min(100.0, _num(c.get("advancementPercentage", 70.0))))
            season = str(c.get("season") or "").strip()
            round_scores = {}
            if season:
                season_challenges = []
                for ss in store.collection("academicChallenges").where("season", "==", season).limit(200).stream():
                    sc = ss.to_dict() or {}
                    if sc.get("status") == "published":
                        season_challenges.append((ss.id, max(1, min(7, int(sc.get("roundNumber", 1) or 1)))))
                season_ids = {sid for sid, _ in season_challenges}
                if season_ids:
                    for snap in store.collection("challengeAttempts").where("status", "==", "submitted").limit(5000).stream():
                        x = snap.to_dict() or {}
                        if _key(x.get("challengeId")) not in season_ids:
                            continue
                        uid = _key(x.get("userId"))
                        if not uid or (_num(c.get("entryFee")) > 0 and uid not in verified_users):
                            continue
                        cid2 = _key(x.get("challengeId")); rn = next((r for sid, r in season_challenges if sid == cid2), 1)
                        row = {"percentage": _num(x.get("percentage")), "score": _num(x.get("score")), "submittedAt": x.get("submittedAt")}
                        prior = round_scores.setdefault(uid, {}).get(rn)
                        if prior is None or (row["percentage"], row["score"], -_submission_epoch(row.get("submittedAt"))) > (prior["percentage"], prior["score"], -_submission_epoch(prior.get("submittedAt"))):
                            round_scores.setdefault(uid, {})[rn] = row

            qualified_attempts = []
            for row in attempts:
                uid = row["userId"]
                if season:
                    scores = round_scores.get(uid, {})
                    # A seasonal finalist must have a valid submitted result for
                    # every required round through the configured target round.
                    # Never treat a missing season record as implicitly qualified.
                    if any(r not in scores or scores[r]["percentage"] < threshold for r in range(1, target_round + 1)):
                        continue
                qualified_attempts.append(row)

            qualified_attempts.sort(key=lambda x: (-x["percentage"], -x["score"], _submission_epoch(x.get("submittedAt")), x["userId"]))
            winners = qualified_attempts[:award_count]

            verified_revenue = 0.0
            for snap in store.collection("challengeEntries").where("challengeId", "==", cid).where("status", "==", "verified").limit(5000).stream():
                verified_revenue += _num((snap.to_dict() or {}).get("amount"))
            share = max(0.0, min(1.0, _num(c.get("prizePoolShare"))))
            pool = round(verified_revenue * share, 2)
            # Allocate every cent deterministically. This prevents silent loss of
            # fractional cents when the pool is not evenly divisible.
            # Work in integer cents so the distributed total is exactly the
            # computed pool. Any remainder cent is assigned deterministically
            # from rank 1 onward; never create a negative remainder through
            # decimal rounding.
            pool_cents = int((Decimal(str(pool)) * Decimal("100")).quantize(Decimal("1"))) if winners and pool > 0 else 0
            base_cents, remainder_cents = divmod(pool_cents, len(winners)) if winners else (0, 0)
            currency = str(c.get("prizeCurrency") or "ETB")[:10]
            cert_enabled = bool(c.get("certificateEnabled", True))
            now = datetime.now(timezone.utc)

            # Idempotency must be atomic: two admins may finalize concurrently.
            # Compute the candidate ledger outside the transaction, then atomically
            # re-check the challenge and create/update the deterministic award rows.
            transaction = store.transaction()
            award_refs = [store.collection("awardLedger").document(_stamp_id(cid, w["userId"])) for w in winners]
            challenge_snapshot = transaction.get(cref)
            existing_award_snaps = [transaction.get(ref) for ref in award_refs]
            if challenge_snapshot.exists and (challenge_snapshot.to_dict() or {}).get("awardsFinalizedAt"):
                return jsonify({"success": True, "challengeId": cid, "createdAwards": 0, "alreadyFinalized": True}), 200
            latest_challenge = challenge_snapshot.to_dict() or {} if challenge_snapshot.exists else {}
            if latest_challenge.get("status") != "closed":
                return jsonify({"error": "Challenge must be CLOSED before awards are finalized."}), 409

            created = 0
            for rank, (winner, ref, existing) in enumerate(zip(winners, award_refs, existing_award_snaps), 1):
                if existing.exists:
                    continue
                transaction.set(ref, {
                    "challengeId": cid,
                    "userId": winner["userId"],
                    "rank": rank,
                    "score": winner["score"],
                    "percentage": winner["percentage"],
                    "prizeAmount": float(Decimal(base_cents + (1 if rank <= remainder_cents else 0)) / Decimal("100")),
                    "prizeCurrency": currency,
                    "certificateEligible": cert_enabled,
                    "certificateStatus": "eligible" if cert_enabled else "disabled",
                    "awardsFinalizedAt": now,
                    "source": "server_finalized_challenge",
                    "finalizedAt": now,
                })
                created += 1
            transaction.update(cref, {"status": "finalized", "awardsFinalizedAt": now, "awardsFinalizedBy": detail["uid"], "updatedAt": now})
            transaction.commit()
            return jsonify({"success": True, "challengeId": cid, "createdAwards": created, "awardCount": len(winners), "prizePool": pool, "prizeCurrency": currency, "certificateEnabled": cert_enabled})
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid challenge award configuration."}), 400
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception("Award finalization failed")
            return jsonify({"error": "Unable to finalize awards."}), 500

    @app.post("/api/admin/challenges/<challenge_id>/publish-winners")
    def publish_winners(challenge_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            store, firestore = db()
            if role(store, detail) != "admin":
                return jsonify({"error": "Admin access required."}), 403
            cid = _key(challenge_id)
            cref = store.collection("academicChallenges").document(cid)
            cs = cref.get()
            if not cs.exists:
                return jsonify({"error": "Challenge not found."}), 404
            challenge = cs.to_dict() or {}
            if challenge.get("status") != "finalized" or not challenge.get("awardsFinalizedAt"):
                return jsonify({"error": "Challenge and awards must be FINALIZED before winners can be published."}), 409
            if challenge.get("winnersPublishedAt"):
                return jsonify({"success": True, "challengeId": cid, "alreadyPublished": True}), 200

            award_snaps = store.collection("awardLedger").where("challengeId", "==", cid).limit(MAX_AWARDS).stream()
            awards = []
            for snap in award_snaps:
                a = snap.to_dict() or {}
                uid = _key(a.get("userId"))
                if not uid:
                    continue
                user_snap = store.collection("users").document(uid).get()
                user = user_snap.to_dict() or {} if user_snap.exists else {}
                # Public publication intentionally exposes only a display name and
                # competition result; no email, phone, UID, payment data, or audit data.
                display_name = str(user.get("displayName") or user.get("name") or "Participant")[:100]
                awards.append({
                    "rank": int(a.get("rank", 0) or 0),
                    "displayName": display_name,
                    "score": _num(a.get("score")),
                    "percentage": _num(a.get("percentage")),
                    "prizeAmount": _num(a.get("prizeAmount")),
                    "prizeCurrency": str(a.get("prizeCurrency") or challenge.get("prizeCurrency") or "ETB")[:10],
                    "certificateEligible": bool(a.get("certificateEligible")),
                })
            awards.sort(key=lambda x: (x["rank"], x["displayName"]))
            now = datetime.now(timezone.utc)
            transaction = store.transaction()
            snap = transaction.get(cref)
            latest = snap.to_dict() or {} if snap.exists else {}
            if latest.get("winnersPublishedAt"):
                return jsonify({"success": True, "challengeId": cid, "alreadyPublished": True}), 200
            transaction.update(cref, {
                "winnersPublishedAt": now,
                "winnersPublishedBy": detail["uid"],
                "winnerPublicationVersion": 1,
                "updatedAt": now,
            })
            transaction.commit()

            audit_id = hashlib.sha256(("winner-publish:" + cid + ":" + detail["uid"] + ":" + now.isoformat()).encode()).hexdigest()[:40]
            store.collection("adminAuditLogs").document(audit_id).set({
                "action": "winners_published", "challengeId": cid,
                "adminUid": detail["uid"], "createdAt": now,
                "source": "award_ledger_routes_v31_60",
            })
            return jsonify({"success": True, "challengeId": cid, "published": True, "winnerCount": len(awards)}), 200
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception("Winner publication failed")
            return jsonify({"error": "Unable to publish winners."}), 500

    @app.get("/api/challenges/<challenge_id>/winners")
    def public_winners(challenge_id):
        try:
            store, _ = db()
            cid = _key(challenge_id)
            cs = store.collection("academicChallenges").document(cid).get()
            if not cs.exists:
                return jsonify({"error": "Challenge not found."}), 404
            challenge = cs.to_dict() or {}
            if not challenge.get("winnersPublishedAt"):
                return jsonify({"error": "Winner results are not publicly published."}), 404
            rows = []
            for snap in store.collection("awardLedger").where("challengeId", "==", cid).limit(MAX_AWARDS).stream():
                a = snap.to_dict() or {}
                uid = _key(a.get("userId"))
                if not uid:
                    continue
                us = store.collection("users").document(uid).get()
                user = us.to_dict() or {} if us.exists else {}
                rows.append({
                    "rank": int(a.get("rank", 0) or 0),
                    "displayName": str(user.get("displayName") or user.get("name") or "Participant")[:100],
                    "score": _num(a.get("score")),
                    "percentage": _num(a.get("percentage")),
                    "prizeAmount": _num(a.get("prizeAmount")),
                    "prizeCurrency": str(a.get("prizeCurrency") or challenge.get("prizeCurrency") or "ETB")[:10],
                    "certificateEligible": bool(a.get("certificateEligible")),
                })
            rows.sort(key=lambda x: (x["rank"], x["displayName"]))
            return jsonify({
                "success": True, "challengeId": cid,
                "challengeTitle": str(challenge.get("title") or "Academic Challenge")[:200],
                "publishedAt": challenge.get("winnersPublishedAt"),
                "winners": rows[:MAX_AWARDS],
            }), 200
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception("Public winner lookup failed")
            return jsonify({"error": "Unable to load published winners."}), 500

    @app.get("/api/me/awards")
    def my_awards():
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            store, _ = db()
            uid = detail["uid"]
            rows = []
            for snap in store.collection("awardLedger").where("userId", "==", uid).limit(100).stream():
                x = snap.to_dict() or {}
                # Student-facing history is a deliberate allow-list. Never expose
                # admin UIDs, payment metadata, internal timestamps, or audit fields.
                rows.append({
                    "awardId": snap.id,
                    "challengeId": _key(x.get("challengeId")),
                    "rank": int(x.get("rank", 0) or 0),
                    "score": _num(x.get("score")),
                    "percentage": _num(x.get("percentage")),
                    "prizeAmount": _num(x.get("prizeAmount")),
                    "prizeCurrency": str(x.get("prizeCurrency") or "ETB")[:10],
                    "certificateEligible": bool(x.get("certificateEligible")),
                    "certificateStatus": str(x.get("certificateStatus") or "not_issued")[:30],
                    "certificateId": str(x.get("certificateId") or "")[:100] or None,
                    "certificateIssuedAt": x.get("certificateIssuedAt"),
                    "awardsFinalizedAt": x.get("awardsFinalizedAt"),
                })
            rows.sort(key=lambda x: str(x.get("awardsFinalizedAt") or ""), reverse=True)
            return jsonify({"success": True, "awards": rows})
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception("Award ledger lookup failed")
            return jsonify({"error": "Unable to load awards."}), 500
