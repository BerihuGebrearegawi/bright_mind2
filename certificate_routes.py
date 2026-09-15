"""V31.43 server-authoritative certificate issuance and verification.

Certificates are issued only from finalized award-ledger records. The browser
cannot create, edit, or revoke certificate records directly.
"""
from datetime import datetime, timezone
import hashlib
import secrets
import os
import hmac
from flask import jsonify, request


def _key(v):
    return ''.join(ch for ch in str(v or '') if ch.isalnum() or ch in '._:-')[:150]


def _certificate_id(award_id):
    return 'BMT-CERT-' + hashlib.sha256((award_id + ':' + secrets.token_hex(16)).encode()).hexdigest()[:24].upper()


def _verification_signature(certificate_id):
    secret = os.environ.get("BMT_CERT_VERIFY_SECRET")
    if not secret:
        raise RuntimeError('Certificate verification secret is not configured.')
    return hmac.new(secret.encode('utf-8'), certificate_id.encode('utf-8'), hashlib.sha256).hexdigest()[:32]


def register_certificate_routes(app, require_user, firebase_admin_factory):
    def db():
        fb = firebase_admin_factory()
        if not fb:
            raise RuntimeError('Firebase server credentials are not configured.')
        from firebase_admin import firestore
        return firestore.client(), firestore

    @app.post('/api/me/awards/<award_id>/certificate')
    def issue_certificate(award_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            store, _ = db()
            aid = _key(award_id)
            ref = store.collection('awardLedger').document(aid)
            snap = ref.get()
            if not snap.exists:
                return jsonify({'error': 'Award not found.'}), 404
            award = snap.to_dict() or {}
            if award.get('userId') != detail['uid']:
                return jsonify({'error': 'Access denied.'}), 403
            if award.get('awardsFinalizedAt') is None:
                return jsonify({'error': 'Awards have not been finalized yet.'}), 409
            if award.get('certificateEligible') is not True:
                return jsonify({'error': 'This award is not certificate-eligible.'}), 409

            cert_ref = ref.collection('certificate').document('record')
            from firebase_admin import firestore
            tx = store.transaction()

            @firestore.transactional
            def _issue(transaction):
                latest_award = ref.get(transaction=transaction)
                if not latest_award.exists:
                    raise LookupError('Award not found.')
                latest = latest_award.to_dict() or {}
                if latest.get('userId') != detail['uid']:
                    raise PermissionError('Access denied.')
                if latest.get('awardsFinalizedAt') is None:
                    raise RuntimeError('Awards have not been finalized yet.')
                if latest.get('certificateEligible') is not True:
                    raise RuntimeError('This award is not certificate-eligible.')
                existing_snap = cert_ref.get(transaction=transaction)
                if existing_snap.exists:
                    return existing_snap.to_dict() or {}, False
                cid = _certificate_id(aid)
                now = datetime.now(timezone.utc)
                record = {
                    'certificateId': cid,
                    'awardId': aid,
                    'userId': detail['uid'],
                    'challengeId': _key(latest.get('challengeId')),
                    'rank': int(latest.get('rank', 0) or 0),
                    'score': float(latest.get('score', 0) or 0),
                    'percentage': float(latest.get('percentage', 0) or 0),
                    'issuedAt': now,
                    'status': 'issued',
                    'verificationVersion': 2,
                    'verificationSignature': _verification_signature(cid),
                }
                transaction.create(cert_ref, record)
                transaction.update(ref, {'certificateStatus': 'issued', 'certificateId': cid, 'certificateIssuedAt': now})
                return record, True

            record, created = _issue(tx)
            return jsonify({'success': True, 'certificate': _public_certificate(record)}), 201 if created else 200
        except RuntimeError as exc:
            return jsonify({'error': str(exc)}), 503
        except Exception:
            app.logger.exception('Certificate issuance failed')
            return jsonify({'error': 'Unable to issue certificate.'}), 500

    @app.get('/api/certificates/<certificate_id>/verify')
    def verify_certificate(certificate_id):
        try:
            store, _ = db()
            cid = str(certificate_id or '')[:100]
            if not cid.startswith('BMT-CERT-'):
                return jsonify({'valid': False, 'status': 'invalid'}), 400
            # Certificate IDs are not document IDs; query the indexed field.
            docs = store.collection_group('certificate').where('certificateId', '==', cid).limit(1).stream()
            for snap in docs:
                record = snap.to_dict() or {}
                status = str(record.get('status') or 'invalid')
                if status != 'issued':
                    return jsonify({'valid': False, 'status': status}), 200
                expected = _verification_signature(cid)
                stored = record.get('verificationSignature')
                if not stored or not hmac.compare_digest(str(stored), expected):
                    return jsonify({'valid': False, 'status': 'invalid_signature'}), 200
                return jsonify({'valid': True, 'status': 'issued', 'certificate': _public_certificate(record, public=True)}), 200
            return jsonify({'valid': False, 'status': 'not_found'}), 404
        except RuntimeError as exc:
            return jsonify({'error': str(exc)}), 503
        except Exception:
            app.logger.exception('Certificate verification failed')
            return jsonify({'error': 'Unable to verify certificate.'}), 500


    @app.post('/api/admin/certificates/<certificate_id>/revoke')
    def revoke_certificate(certificate_id):
        ok, detail = require_user()
        if not ok:
            return detail
        try:
            store, _ = db()
            us = store.collection('users').document(detail['uid']).get()
            u = us.to_dict() or {} if us.exists else {}
            role = str(u.get('accountType') or u.get('role') or detail.get('role') or '').lower()
            if role != 'admin':
                return jsonify({'error': 'Admin access required.'}), 403
            cid = str(certificate_id or '')[:100]
            docs = store.collection_group('certificate').where('certificateId', '==', cid).limit(1).stream()
            found = None
            for snap in docs:
                found = snap
                break
            if not found:
                return jsonify({'error': 'Certificate not found.'}), 404
            cert_ref = found.reference
            cert = found.to_dict() or {}
            if cert.get('status') == 'revoked':
                return jsonify({'success': True, 'status': 'revoked', 'certificateId': cid, 'alreadyRevoked': True}), 200
            now = datetime.now(timezone.utc)
            cert_ref.update({'status': 'revoked', 'revokedAt': now, 'revokedBy': detail['uid']})
            aid = _key(cert.get('awardId'))
            if aid:
                store.collection('awardLedger').document(aid).update({'certificateStatus': 'revoked', 'certificateRevokedAt': now})
            # Existing audit collection is server-only; never trust client audit payloads.
            audit_id = hashlib.sha256(('certificate-revoke:' + cid + ':' + detail['uid'] + ':' + now.isoformat()).encode()).hexdigest()[:40]
            store.collection('adminAuditLogs').document(audit_id).set({
                'action': 'certificate_revoked', 'certificateId': cid, 'awardId': aid,
                'adminUid': detail['uid'], 'createdAt': now, 'source': 'certificate_routes_v31_44'
            })
            return jsonify({'success': True, 'status': 'revoked', 'certificateId': cid}), 200
        except RuntimeError as exc:
            return jsonify({'error': str(exc)}), 503
        except Exception:
            app.logger.exception('Certificate revocation failed')
            return jsonify({'error': 'Unable to revoke certificate.'}), 500


def _public_certificate(record, public=False):
    data = {
        'certificateId': record.get('certificateId'),
        'challengeId': record.get('challengeId'),
        'rank': record.get('rank'),
        'issuedAt': record.get('issuedAt'),
        'status': record.get('status'),
        'verificationVersion': record.get('verificationVersion', 1),
    }
    data['verificationUrlPath'] = '/api/certificates/' + str(record.get('certificateId') or '') + '/verify'
    if not public:
        data.update({'awardId': record.get('awardId'), 'score': record.get('score'), 'percentage': record.get('percentage')})
    return data
