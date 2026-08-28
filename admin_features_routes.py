"""BMT admin feature APIs.
Server-authoritative writes for settings, moderation, notifications and live stream.
"""
import os
import requests
from datetime import datetime, timezone
from flask import request, jsonify


def register_admin_feature_routes(app, require_admin, firebase_admin_factory, require_user=None):
    def db():
        fb = firebase_admin_factory()
        if not fb:
            raise RuntimeError("Firebase server credentials are not configured.")
        from firebase_admin import firestore
        return firestore.client()

    def clean(v, n=1000):
        return str(v or '').strip()[:n]

    def iso(v):
        return v.isoformat() if hasattr(v, 'isoformat') else None

    @app.get('/api/admin/settings/organization')
    def get_org_settings():
        ok, detail = require_admin()
        if not ok: return detail
        try:
            snap = db().collection('settings').document('organization').get()
            return jsonify({'success': True, 'organization': snap.to_dict() if snap.exists else {}})
        except Exception as exc:
            app.logger.exception('Organization settings read failed')
            return jsonify({'error': 'Could not load organization settings.'}), 500

    @app.post('/api/admin/settings/organization/logo')
    def save_org_logo():
        ok, detail = require_admin()
        if not ok: return detail
        file = request.files.get('file')
        if not file or not file.filename:
            return jsonify({'error': 'Logo image is required.'}), 400
        if (file.content_type or '').lower() not in {'image/jpeg','image/png','image/gif','image/webp'}:
            return jsonify({'error': 'Unsupported logo image type.'}), 415
        data = file.read(15 * 1024 * 1024 + 1)
        if len(data) > 15 * 1024 * 1024:
            return jsonify({'error': 'Logo is too large. Maximum size is 15 MB.'}), 413
        cloud_name = os.getenv('CLOUDINARY_CLOUD_NAME', '').strip()
        api_key = os.getenv('CLOUDINARY_API_KEY', '').strip()
        api_secret = os.getenv('CLOUDINARY_API_SECRET', '').strip()
        upload_preset = os.getenv('CLOUDINARY_UPLOAD_PRESET', '').strip()
        if not cloud_name:
            return jsonify({'error': 'Cloudinary cloud name is not configured on the server.'}), 503
        try:
            import hashlib
            import time
            timestamp = int(time.time())
            form = {'timestamp': timestamp}
            if api_key and api_secret:
                signature = hashlib.sha1((f'timestamp={timestamp}' + api_secret).encode('utf-8')).hexdigest()
                form.update({'api_key': api_key, 'signature': signature})
            elif upload_preset:
                form = {'upload_preset': upload_preset}
            else:
                return jsonify({'error': 'Cloudinary API credentials or upload preset are not configured on the server.'}), 503
            r = requests.post(
                f'https://api.cloudinary.com/v1_1/{cloud_name}/image/upload',
                data=form,
                files={'file': (file.filename, data, file.content_type)},
                timeout=60,
            )
            payload = r.json() if r.content else {}
            if not r.ok or not payload.get('secure_url'):
                safe_error = payload.get('error', {}).get('message', 'Cloudinary upload failed.') if isinstance(payload.get('error'), dict) else 'Cloudinary upload failed.'
                app.logger.warning('Admin logo Cloudinary failure: status=%s message=%s', r.status_code, safe_error)
                return jsonify({'error': safe_error}), 502
            url = payload['secure_url']
            database = db()
            database.collection('settings').document('organization').set({
                'logoUrl': url,
                'logoProvider': 'cloudinary',
                'logoPublicId': payload.get('public_id', ''),
                'updatedAt': datetime.now(timezone.utc),
                'updatedBy': detail.get('uid')
            }, merge=True)
            return jsonify({'success': True, 'logoUrl': url, 'provider': 'cloudinary'})
        except Exception:
            app.logger.exception('Admin logo upload failed')
            return jsonify({'error': 'Logo upload failed.'}), 502

    @app.delete('/api/admin/settings/organization/logo')
    def remove_org_logo():
        ok, detail = require_admin()
        if not ok: return detail
        try:
            database = db()
            database.collection('settings').document('organization').set({
                'logoUrl': '', 'updatedAt': datetime.now(timezone.utc), 'updatedBy': detail.get('uid')
            }, merge=True)
            return jsonify({'success': True})
        except Exception:
            return jsonify({'error': 'Could not remove organization logo.'}), 500

    @app.post('/api/admin/storage/cloudinary')
    def admin_cloudinary_upload():
        """Server-authoritative Cloudinary upload for admin media.

        The browser must never be able to turn the public/unsigned Cloudinary
        preset into an unrestricted upload endpoint. Authorization is checked
        by Firebase Admin before the file is forwarded to Cloudinary.
        """
        ok, detail = require_admin()
        if not ok:
            return detail

        file = request.files.get('file')
        if not file or not file.filename:
            return jsonify({'error': 'Media file is required.'}), 400

        requested_type = clean(request.form.get('resourceType'), 20).lower() or 'auto'
        allowed = {
            'image': {'types': {'image/jpeg', 'image/png', 'image/gif', 'image/webp'}, 'max': 15 * 1024 * 1024},
            'video': {'types': {'video/mp4', 'video/webm', 'video/ogg'}, 'max': 100 * 1024 * 1024},
            'raw': {'types': {'application/pdf', 'application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}, 'max': 100 * 1024 * 1024},
            'auto': {'types': set(), 'max': 100 * 1024 * 1024},
        }
        rule = allowed.get(requested_type)
        if rule is None:
            return jsonify({'error': 'Unsupported Cloudinary resource type.'}), 400
        content_type = (file.content_type or '').lower().strip()
        if rule['types'] and content_type not in rule['types']:
            return jsonify({'error': 'File type is not allowed for this upload.'}), 415

        data = file.read(rule['max'] + 1)
        if len(data) > rule['max']:
            return jsonify({'error': f"File is too large. Maximum is {rule['max'] // (1024 * 1024)} MB."}), 413

        cloud_name = os.getenv('CLOUDINARY_CLOUD_NAME', '').strip()
        api_key = os.getenv('CLOUDINARY_API_KEY', '').strip()
        api_secret = os.getenv('CLOUDINARY_API_SECRET', '').strip()
        upload_preset = os.getenv('CLOUDINARY_UPLOAD_PRESET', '').strip()
        if not cloud_name:
            return jsonify({'error': 'Cloudinary cloud name is not configured on the server.'}), 503

        try:
            import hashlib
            import time
            timestamp = int(time.time())
            upload_type = requested_type if requested_type != 'auto' else 'auto'
            form = {'timestamp': timestamp}
            if api_key and api_secret:
                # Cloudinary signed uploads use a SHA-1 signature over the
                # upload parameters plus the API secret.
                signature_base = f'timestamp={timestamp}'
                signature = hashlib.sha1((signature_base + api_secret).encode('utf-8')).hexdigest()
                form.update({'api_key': api_key, 'signature': signature})
            elif upload_preset:
                # Fallback for installations that only have an unsigned preset.
                # The endpoint is still admin-protected, so the preset is no
                # longer directly exposed as an unrestricted admin upload path.
                form = {'upload_preset': upload_preset}
            else:
                return jsonify({'error': 'Cloudinary API credentials or upload preset are not configured on the server.'}), 503

            endpoint = f'https://api.cloudinary.com/v1_1/{cloud_name}/{upload_type}/upload'
            response = requests.post(endpoint, data=form, files={'file': (file.filename, data, content_type)}, timeout=120)
            payload = response.json() if response.content else {}
            if not response.ok or not payload.get('secure_url'):
                app.logger.warning('Admin Cloudinary upload failed: status=%s response=%s', response.status_code, str(payload)[:500])
                return jsonify({'error': payload.get('error', {}).get('message', 'Cloudinary upload failed.')}), 502
            return jsonify({
                'success': True,
                'url': payload['secure_url'],
                'provider': 'cloudinary',
                'fileId': payload.get('public_id', ''),
                'resourceType': payload.get('resource_type', upload_type),
                'bytes': payload.get('bytes', len(data)),
                'format': payload.get('format', ''),
            }), 201
        except Exception:
            app.logger.exception('Admin Cloudinary upload exception')
            return jsonify({'error': 'Cloudinary upload failed.'}), 502

    @app.post('/api/admin/catalog/books')
    def create_admin_book():
        """Create a book metadata record after server-side admin authorization."""
        ok, detail = require_admin()
        if not ok:
            return detail
        body = request.get_json(silent=True) or {}
        title = clean(body.get('title'), 160)
        class_name = clean(body.get('className'), 40)
        provider = clean(body.get('storageProvider', 'gcs'), 30).lower()
        if not title or not class_name:
            return jsonify({'error': 'Book title and class are required.'}), 400
        if provider not in {'gcs', 'google_drive', 'cloudinary'}:
            return jsonify({'error': 'Unsupported book storage provider.'}), 400
        allowed_classes = {str(i) for i in range(1, 13)} | {'KG', 'Nursery', 'LKG', 'UKG'}
        if class_name not in allowed_classes:
            return jsonify({'error': 'Invalid class selection.'}), 400
        fields = {}
        for key in ('link','fileUrl','previewUrl','downloadUrl','sourceUrl','storagePublicId','fileId','storagePath','storageBucket'):
            fields[key] = clean(body.get(key), 1200 if key.endswith('Url') or key in {'link','fileUrl','sourceUrl'} else 500)
        if provider == 'google_drive' and not fields['fileId']:
            return jsonify({'error': 'Google Drive file ID is required.'}), 400
        if provider != 'google_drive' and not (fields['storagePath'] or fields['fileId'] or fields['fileUrl']):
            return jsonify({'error': 'Uploaded book storage reference is missing.'}), 400
        if provider == 'cloudinary':
            from urllib.parse import urlparse
            candidate = fields['fileUrl'] or fields['link'] or fields['downloadUrl']
            try:
                parsed = urlparse(candidate)
                if parsed.scheme != 'https' or parsed.hostname not in {'res.cloudinary.com', 'cloudinary.com'}:
                    return jsonify({'error': 'Cloudinary books must use a secure Cloudinary URL.'}), 400
            except Exception:
                return jsonify({'error': 'Invalid Cloudinary book URL.'}), 400
        try:
            now = datetime.now(timezone.utc)
            data = {
                'title': title, 'className': class_name, 'storageProvider': provider,
                **fields, 'createdAt': now, 'createdBy': detail.get('uid')
            }
            ref = db().collection('books').document()
            ref.set(data)
            return jsonify({'success': True, 'id': ref.id, 'book': {'id': ref.id, **data}}), 201
        except Exception:
            app.logger.exception('Admin book metadata save failed')
            return jsonify({'error': 'Could not save book metadata.'}), 500

    @app.post('/api/admin/catalog/videos')
    def create_admin_video():
        """Create a video metadata record after server-side admin authorization."""
        ok, detail = require_admin()
        if not ok:
            return detail
        body = request.get_json(silent=True) or {}
        title = clean(body.get('title'), 160)
        class_name = clean(body.get('className'), 40)
        url = clean(body.get('url'), 1500)
        provider = clean(body.get('provider', 'cloudinary'), 30).lower()
        if not title or not class_name or not url:
            return jsonify({'error': 'Video title, class and URL are required.'}), 400
        if provider not in {'cloudinary', 'youtube'}:
            return jsonify({'error': 'Unsupported video provider.'}), 400
        if not url.lower().startswith(('https://','http://')):
            return jsonify({'error': 'Video URL must be an HTTP(S) URL.'}), 400
        if provider == 'youtube':
            import re
            if not re.match(r'^https?://(www\.)?(youtube\.com/(watch\?v=|live/|shorts/)|youtu\.be/)', url, re.I):
                return jsonify({'error': 'Invalid YouTube URL.'}), 400
        try:
            now = datetime.now(timezone.utc)
            data = {
                'title': title, 'className': class_name, 'url': url,
                'provider': provider, 'isPaid': bool(body.get('isPaid', False)),
                'views': 0, 'createdAt': now, 'createdBy': detail.get('uid')
            }
            ref = db().collection('videos').document()
            ref.set(data)
            return jsonify({'success': True, 'id': ref.id, 'video': {'id': ref.id, **data}}), 201
        except Exception:
            app.logger.exception('Admin video metadata save failed')
            return jsonify({'error': 'Could not save video metadata.'}), 500


    @app.post('/api/admin/catalog/quizzes')
    def create_admin_quiz():
        """Create quiz content through the server-authoritative admin boundary."""
        ok, detail = require_admin()
        if not ok:
            return detail
        body = request.get_json(silent=True) or {}
        class_name = clean(body.get('className'), 40)
        title = clean(body.get('title') or 'Quiz', 160)
        question = clean(body.get('question'), 4000)
        image_url = clean(body.get('imageUrl'), 1500)
        options_raw = body.get('options') if isinstance(body.get('options'), dict) else {}
        options = {k: clean(options_raw.get(k), 1000) for k in ('A','B','C','D')}
        correct = clean(body.get('correctAnswer'), 1).upper()
        allowed_classes = {str(i) for i in range(1, 13)} | {'KG', 'Nursery', 'LKG', 'UKG'}
        if class_name not in allowed_classes:
            return jsonify({'error': 'Invalid class selection.'}), 400
        if not question or correct not in {'A','B','C','D'}:
            return jsonify({'error': 'Quiz question and a valid correct answer are required.'}), 400
        if not options.get(correct):
            return jsonify({'error': 'The correct answer option cannot be empty.'}), 400
        if image_url:
            from urllib.parse import urlparse
            try:
                parsed = urlparse(image_url)
                if parsed.scheme != 'https' or parsed.hostname not in {'res.cloudinary.com', 'cloudinary.com'}:
                    return jsonify({'error': 'Quiz images must use a secure Cloudinary URL.'}), 400
            except Exception:
                return jsonify({'error': 'Invalid quiz image URL.'}), 400
        try:
            now = datetime.now(timezone.utc)
            data = {
                'className': class_name,
                'title': title,
                'question': question,
                'imageUrl': image_url,
                'options': options,
                'correctAnswer': correct,
                'createdAt': now,
                'createdBy': detail.get('uid')
            }
            ref = db().collection('quizzes').document()
            ref.set(data)
            return jsonify({'success': True, 'id': ref.id, 'quiz': {'id': ref.id, **data}}), 201
        except Exception:
            app.logger.exception('Admin quiz save failed')
            return jsonify({'error': 'Could not save quiz.'}), 500

    @app.get('/api/admin/catalog/quizzes')
    def list_admin_quizzes():
        """Return quiz metadata to authenticated admins without client Firestore reads."""
        ok, detail = require_admin()
        if not ok:
            return detail
        try:
            rows = []
            for snap in db().collection('quizzes').stream():
                data = snap.to_dict() or {}
                rows.append({'id': snap.id, **data})
            rows.sort(key=lambda x: str(x.get('createdAt') or ''), reverse=True)
            return jsonify({'success': True, 'quizzes': rows}), 200
        except Exception:
            app.logger.exception('Admin quiz listing failed')
            return jsonify({'error': 'Unable to load quizzes.'}), 500

    @app.get('/api/settings/prices')
    def public_prices():
        try:
            snap = db().collection('settings').document('prices').get()
            defaults = {'currency': os.getenv('PAYMENT_CURRENCY','ETB') or 'ETB','monthly':9.99,'yearly':79.99,'video':2.99,'exam':4.99,'quiz':1.99,'live':5.99}
            if snap.exists: defaults.update(snap.to_dict() or {})
            defaults['currency']=str(defaults.get('currency') or 'ETB').upper()
            return jsonify({'success':True,'prices':defaults})
        except Exception:
            # Public pricing remains available from safe defaults if Firebase is temporarily unavailable.
            return jsonify({'success':True,'prices':{'currency':os.getenv('PAYMENT_CURRENCY','ETB') or 'ETB','monthly':9.99,'yearly':79.99,'video':2.99,'exam':4.99,'quiz':1.99,'live':5.99},'source':'defaults'})

    @app.get('/api/admin/settings/prices')
    def get_prices():
        ok, detail = require_admin()
        if not ok: return detail
        try:
            snap = db().collection('settings').document('prices').get()
            defaults = {'currency': os.getenv('PAYMENT_CURRENCY', 'ETB') or 'ETB',
                        'monthly': 9.99, 'yearly': 79.99, 'video': 2.99, 'exam': 4.99, 'quiz': 1.99, 'live': 5.99}
            if snap.exists: defaults.update(snap.to_dict() or {})
            defaults['currency'] = str(defaults.get('currency') or 'ETB').upper()
            return jsonify({'success': True, 'prices': defaults})
        except Exception:
            return jsonify({'error': 'Could not load service prices.'}), 500

    @app.put('/api/admin/settings/prices')
    def put_prices():
        ok, detail = require_admin()
        if not ok: return detail
        body = request.get_json(silent=True) or {}
        try:
            currency = clean(body.get('currency', 'ETB'), 3).upper()
            if currency not in {'ETB','USD'}: raise ValueError('Currency must be ETB or USD.')
            values = {k: float(body[k]) for k in ('monthly','yearly','video','exam','quiz','live')}
            if any(v < 0 for v in values.values()): raise ValueError('Prices cannot be negative.')
            values.update({'currency': currency, 'updatedAt': datetime.now(timezone.utc), 'updatedBy': detail.get('uid')})
            db().collection('settings').document('prices').set(values, merge=True)
            return jsonify({'success': True, 'prices': values})
        except (ValueError, TypeError, KeyError):
            return jsonify({'error': 'Enter valid non-negative prices and choose ETB or USD.'}), 400
        except Exception:
            return jsonify({'error': 'Could not save service prices.'}), 500

    @app.get('/api/admin/teacher-requests')
    def admin_teacher_requests():
        ok, detail = require_admin()
        if not ok: return detail
        try:
            rows=[]
            for snap in db().collection('teacherRequests').where('status','==','pending').stream():
                x=snap.to_dict() or {}; x['id']=snap.id
                rows.append(x)
            rows.sort(key=lambda x: str(x.get('createdAt') or ''), reverse=True)
            return jsonify({'success': True, 'requests': rows})
        except Exception:
            return jsonify({'error': 'Could not load teacher requests.'}), 500

    @app.post('/api/admin/ai-copilot')
    def admin_ai_copilot():
        """Admin-side AI copilot using the existing Gemini configuration."""
        ok, detail = require_admin()
        if not ok:
            return detail
        body = request.get_json(silent=True) or {}
        task = clean(body.get('task'), 7000)
        context = clean(body.get('context'), 12000)
        mode = clean(body.get('mode'), 40).lower() or 'general'
        if not task:
            return jsonify({'error': 'Admin AI task is required.'}), 400
        try:
            database = db(); uid = str(detail.get('uid') or 'admin')
            from ai_tutor_service import ask_gemini, check_and_record_quota
            allowed, remaining = check_and_record_quota(lambda: database, uid, premium=True)
            if not allowed:
                return jsonify({'error': 'Daily Admin AI limit reached.', 'remaining': 0}), 429
            mode_guidance = {
                'announcement': 'Draft a clear, professional educational announcement for BMT. Keep it concise and actionable.',
                'moderation': 'Analyze the supplied chat moderation context. Identify risks, recommend fair actions, and draft a moderator response. Do not expose private information.',
                'analytics': 'Analyze the supplied platform context and give practical operational insights, risks, and priorities. Do not invent missing metrics.',
                'content': 'Help the administrator plan or improve educational content, metadata, categorization, or publishing workflow.',
                'general': 'Act as a secure educational platform operations copilot for the administrator.'
            }.get(mode, 'Act as a secure educational platform operations copilot for the administrator.')
            prompt = (
                'You are the Bright Mind Tutor Admin Copilot. ' + mode_guidance +
                ' Use only information supplied in the task/context. Clearly label suggestions. '
                'Never claim to have executed an external action. Never reveal secrets, API keys, passwords, '
                'service-account credentials, or private student data.\n\nTASK:\n' + task +
                ('\n\nCONTEXT:\n' + context if context else '')
            )
            answer, model = ask_gemini(prompt, [], 'Admin', 'Platform Administration', [])
            return jsonify({'answer': answer, 'model': model, 'remainingToday': remaining})
        except RuntimeError as exc:
            return jsonify({'error': str(exc)}), 503
        except Exception:
            app.logger.exception('Admin AI copilot failure')
            return jsonify({'error': 'Admin AI Copilot is temporarily unavailable.'}), 502

    @app.get('/api/admin/chat/messages')
    def admin_chat_messages():
        ok, detail = require_admin()
        if not ok: return detail
        try:
            rows=[]
            for snap in db().collection('chats').limit(200).stream():
                x=snap.to_dict() or {}; x['id']=snap.id
                for k in ('createdAt','updatedAt'):
                    x[k]=iso(x.get(k))
                rows.append(x)
            rows.sort(key=lambda x:x.get('createdAt') or '', reverse=True)
            return jsonify({'success': True, 'messages': rows})
        except Exception:
            return jsonify({'error':'Could not load chat moderation.'}), 500

    @app.post('/api/admin/chat/reply')
    def admin_chat_reply():
        ok, detail = require_admin()
        if not ok: return detail
        body=request.get_json(silent=True) or {}
        target_id=clean(body.get('messageId'),150); text=clean(body.get('message'),3000)
        media_url=clean(body.get('mediaUrl'),2000); media_type=clean(body.get('mediaType'),20).lower()
        if not target_id or (not text and not media_url): return jsonify({'error':'messageId and message or media are required.'}),400
        if media_url and media_type not in {'image','audio','video','file'}: return jsonify({'error':'Unsupported media type.'}),400
        if media_url and not (media_url.startswith('https://res.cloudinary.com/') or media_url.startswith('https://storage.googleapis.com/')): return jsonify({'error':'Media URL must come from an approved BMT storage provider.'}),400
        try:
            database=db(); target=database.collection('chats').document(target_id).get()
            if not target.exists: return jsonify({'error':'Original message not found.'}),404
            original=target.to_dict() or {}; now=datetime.now(timezone.utc)
            ref=database.collection('chats').document()
            ref.set({'senderUid':detail.get('uid'),'senderName':'👑 Admin','userName':'👑 Admin',
                     'isAdminReply':True,'className':original.get('className',''),'messageText':text,'message':text,
                     'replyTo':target_id,'replyToText':clean(original.get('messageText') or original.get('message'),300),
                     'createdAt':now,'isRead':False})
            return jsonify({'success':True,'id':ref.id})
        except Exception:
            app.logger.exception('Admin chat reply failed')
            return jsonify({'error':'Could not send admin reply.'}),500

    @app.post('/api/admin/chat/reaction')
    def admin_chat_reaction():
        ok, detail = require_admin()
        if not ok: return detail
        body=request.get_json(silent=True) or {}; message_id=clean(body.get('messageId'),150); reaction=clean(body.get('reaction'),20)
        allowed={'like','dislike','heart','laugh','wow','sad','fire'}
        if reaction not in allowed: return jsonify({'error':'Unsupported reaction.'}),400
        try:
            from firebase_admin import firestore
            ref=db().collection('chats').document(message_id); snap=ref.get()
            if not snap.exists: return jsonify({'error':'Message not found.'}),404
            ref.update({f'reactions.{reaction}': firestore.Increment(1)})
            return jsonify({'success':True})
        except Exception:
            return jsonify({'error':'Could not save reaction.'}),500

    @app.post('/api/chat/community/media')
    def community_chat_media_upload():
        """Authenticated community media upload. URL is generated server-side."""
        if require_user is None:
            return jsonify({'error':'Community chat is unavailable.'}),503
        ok, detail = require_user()
        if not ok: return detail
        file = request.files.get('file')
        if not file or not file.filename:
            return jsonify({'error':'Media file is required.'}),400
        content_type=(file.content_type or '').lower().strip()
        allowed={
            'image/jpeg','image/png','image/gif','image/webp',
            'audio/mpeg','audio/mp4','audio/ogg','audio/webm','audio/wav',
            'video/mp4','video/webm','video/quicktime',
        }
        if content_type not in allowed:
            return jsonify({'error':'Only image, audio and video files are allowed.'}),415
        max_bytes = 10*1024*1024 if content_type.startswith('image/') or content_type.startswith('audio/') else 50*1024*1024
        raw=file.read(max_bytes+1)
        if len(raw)>max_bytes:
            return jsonify({'error':f'Media is too large. Maximum is {max_bytes//(1024*1024)} MB.'}),413
        cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME','').strip()
        api_key=os.getenv('CLOUDINARY_API_KEY','').strip()
        api_secret=os.getenv('CLOUDINARY_API_SECRET','').strip()
        preset=os.getenv('CLOUDINARY_UPLOAD_PRESET','').strip()
        if not cloud_name or not ((api_key and api_secret) or preset):
            return jsonify({'error':'Community media storage is not configured on the server.'}),503
        resource='image' if content_type.startswith('image/') else ('video' if content_type.startswith('video/') else 'video')
        try:
            import hashlib, time
            timestamp=int(time.time())
            form={'timestamp':timestamp}
            if api_key and api_secret:
                signature=hashlib.sha1((f'timestamp={timestamp}' + api_secret).encode()).hexdigest()
                form.update({'api_key':api_key,'signature':signature})
            else:
                form={'upload_preset':preset}
            r=requests.post(f'https://api.cloudinary.com/v1_1/{cloud_name}/{resource}/upload',data=form,files={'file':(file.filename,raw,content_type)},timeout=60)
            payload=r.json() if r.content else {}
            if not r.ok or not payload.get('secure_url'):
                app.logger.warning('Community media upload failed: %s', r.status_code)
                return jsonify({'error':'Media upload failed.'}),502
            return jsonify({'success':True,'url':payload['secure_url'],'mediaType':'image' if resource=='image' else ('video' if content_type.startswith('video/') else 'audio'),'provider':'cloudinary','fileId':payload.get('public_id','')}),201
        except Exception:
            app.logger.exception('Community media upload failed')
            return jsonify({'error':'Media upload failed.'}),502

    @app.get('/api/chat/community')
    def community_chat_list():
        """Authenticated community chat feed. Read through Flask so the browser
        does not depend on Firestore client rules for the shared community room."""
        if require_user is None:
            return jsonify({'error':'Community chat is unavailable.'}),503
        ok, detail = require_user()
        if not ok: return detail
        try:
            rows=[]
            for snap in db().collection('chats').where('chatRoom','==','community').limit(300).stream():
                item=snap.to_dict() or {}
                item['id']=snap.id
                item['createdAt']=iso(item.get('createdAt'))
                item['reactions']=item.get('reactions') or {}
                rows.append(item)
            rows.sort(key=lambda x:x.get('createdAt') or '')
            return jsonify({'success':True,'messages':rows})
        except Exception:
            app.logger.exception('Community chat load failed')
            return jsonify({'error':'Could not load community chat.'}),500

    @app.post('/api/chat/community/send')
    def community_chat_send():
        if require_user is None:
            return jsonify({'error':'Community chat is unavailable.'}),503
        ok, detail = require_user()
        if not ok: return detail
        body=request.get_json(silent=True) or {}
        text=clean(body.get('message'),3000)
        media_url=clean(body.get('mediaUrl'),2000)
        media_type=clean(body.get('mediaType'),20).lower()
        profile_image=clean(body.get('profileImage'),2000)
        if not text and not media_url:
            return jsonify({'error':'Message or media is required.'}),400
        if media_url and media_type not in {'image','audio','video','file'}:
            return jsonify({'error':'Unsupported media type.'}),400
        if media_url and not (media_url.startswith('https://res.cloudinary.com/') or media_url.startswith('https://storage.googleapis.com/')):
            return jsonify({'error':'Media URL must come from an approved BMT storage provider.'}),400
        if media_url and not (media_url.startswith('https://res.cloudinary.com/') or media_url.startswith('https://storage.googleapis.com/')):
            return jsonify({'error':'Media URL must come from an approved BMT storage provider.'}),400
        if media_url and not (media_url.startswith('https://res.cloudinary.com/') or media_url.startswith('https://storage.googleapis.com/')):
            return jsonify({'error':'Media URL must come from an approved BMT storage provider.'}),400
        try:
            database=db(); uid=detail.get('uid')
            user_snap=database.collection('users').document(uid).get()
            user=user_snap.to_dict() if user_snap.exists else {}
            is_admin=detail.get('admin') is True or detail.get('role') == 'admin' or user.get('isAdmin') is True or user.get('role') == 'admin'
            is_teacher=approved_teacher = bool(database.collection('teachers').document(uid).get().exists and (database.collection('teachers').document(uid).get().to_dict() or {}).get('approved') is True)
            if not is_admin and not is_teacher:
                # Ordinary authenticated users are students unless an explicit teacher/admin role is proven server-side.
                role='student'
            else:
                role='admin' if is_admin else 'teacher'
            display_name=clean(user.get('name') or user.get('displayName') or detail.get('name') or detail.get('email') or 'User',120)
            if role=='admin': display_name='👑 Admin'
            elif role=='teacher': display_name='👨‍🏫 '+display_name
            from firebase_admin import firestore
            now=datetime.now(timezone.utc)
            ref=database.collection('chats').document()
            ref.set({
                'senderUid':uid,'senderName':display_name,'userName':display_name,'role':role,
                'isAdminReply':role=='admin','isTeacherReply':role=='teacher','chatRoom':'community',
                'className':'community','messageText':text,'message':text,'mediaUrl':media_url,
                'mediaType':media_type,'profileImage':profile_image,'replyTo':None,
                'replyToText':'','createdAt':now,'isRead':False,'reactions':{}
            })
            return jsonify({'success':True,'id':ref.id})
        except Exception:
            app.logger.exception('Community chat send failed')
            return jsonify({'error':'Could not send community message.'}),500

    @app.post('/api/chat/reaction')
    def chat_reaction():
        if require_user is None:
            return jsonify({'error':'Chat reactions are unavailable.'}),503
        ok, detail = require_user()
        if not ok: return detail
        body=request.get_json(silent=True) or {}; message_id=clean(body.get('messageId'),150); reaction=clean(body.get('reaction'),20)
        allowed={'like','dislike','heart','laugh','wow','sad','fire'}
        if reaction not in allowed: return jsonify({'error':'Unsupported reaction.'}),400
        try:
            from firebase_admin import firestore
            database=db(); ref=database.collection('chats').document(message_id); snap=ref.get()
            if not snap.exists: return jsonify({'error':'Message not found.'}),404
            data=snap.to_dict() or {}; uid=detail.get('uid')
            user_snap=database.collection('users').document(uid).get()
            user_data=user_snap.to_dict() if user_snap.exists else {}
            user_class=user_data.get('class')
            if data.get('chatRoom') != 'community' and data.get('className') != user_class and data.get('senderUid') != uid:
                return jsonify({'error':'You are not allowed to react to this message.'}),403
            # One active reaction of each type per user/message. A second tap toggles it off.
            vote_ref=ref.collection('reactionVotes').document(uid)
            vote_snap=vote_ref.get()
            previous=(vote_snap.to_dict() or {}).get('reaction') if vote_snap.exists else None
            if previous == reaction:
                ref.update({f'reactions.{reaction}': firestore.Increment(-1)})
                vote_ref.delete()
                return jsonify({'success':True,'active':False,'reaction':reaction})
            updates={}
            if previous in allowed:
                updates[f'reactions.{previous}']=firestore.Increment(-1)
            updates[f'reactions.{reaction}']=firestore.Increment(1)
            ref.update(updates)
            vote_ref.set({'reaction':reaction,'updatedAt':datetime.now(timezone.utc)})
            return jsonify({'success':True,'active':True,'reaction':reaction})
        except Exception:
            return jsonify({'error':'Could not save reaction.'}),500

    @app.post('/api/chat/community/reply')
    def community_chat_reply():
        if require_user is None:
            return jsonify({'error':'Community chat is unavailable.'}),503
        ok, detail = require_user()
        if not ok: return detail
        body=request.get_json(silent=True) or {}
        target_id=clean(body.get('messageId'),150); text=clean(body.get('message'),3000)
        media_url=clean(body.get('mediaUrl'),2000); media_type=clean(body.get('mediaType'),20).lower()
        if not target_id or (not text and not media_url): return jsonify({'error':'messageId and message or media are required.'}),400
        if media_url and media_type not in {'image','audio','video','file'}: return jsonify({'error':'Unsupported media type.'}),400
        if media_url and not (media_url.startswith('https://res.cloudinary.com/') or media_url.startswith('https://storage.googleapis.com/')): return jsonify({'error':'Media URL must come from an approved BMT storage provider.'}),400
        try:
            database=db(); target=database.collection('chats').document(target_id).get()
            if not target.exists: return jsonify({'error':'Original message not found.'}),404
            original=target.to_dict() or {}
            if original.get('chatRoom') != 'community':
                return jsonify({'error':'This message is not in the community chat.'}),403
            uid=detail.get('uid')
            user=database.collection('users').document(uid).get().to_dict() or {}
            teacher_snap=database.collection('teachers').document(uid).get()
            teacher=teacher_snap.to_dict() if teacher_snap.exists else {}
            is_admin = detail.get('admin') is True or detail.get('role') == 'admin'
            approved_teacher = bool(teacher.get('approved') is True and (teacher.get('uid') in (None, '', uid)))
            if is_admin:
                role='admin'
            elif approved_teacher:
                role='teacher'
            else:
                role='student'
            from firebase_admin import firestore
            ref=database.collection('chats').document(); now=datetime.now(timezone.utc)
            name=detail.get('name') or user.get('name') or user.get('displayName') or 'User'
            if is_admin: name='👑 Admin'
            elif role == 'teacher': name='👨‍🏫 '+str(name)
            ref.set({'senderUid':uid,'senderName':name,'userName':name,'role':role,
                     'isAdminReply':is_admin,'isTeacherReply':role=='teacher','chatRoom':'community',
                     'className':'community','messageText':text,'message':text,
                     'replyTo':target_id,'replyToText':clean(original.get('messageText') or original.get('message'),300),
                     'createdAt':now,'isRead':False,'reactions':{}})
            return jsonify({'success':True,'id':ref.id})
        except Exception:
            app.logger.exception('Community chat reply failed')
            return jsonify({'error':'Could not send community reply.'}),500

    @app.post('/api/chat/report')
    def report_chat_message():
        if require_user is None:
            return jsonify({'error':'Chat reports are unavailable.'}),503
        ok, detail = require_user()
        if not ok: return detail
        body=request.get_json(silent=True) or {}
        message_id=clean(body.get('messageId'),150); reason=clean(body.get('reason'),240) or 'Reported by user.'
        if not message_id: return jsonify({'error':'messageId is required.'}),400
        try:
            database=db(); target=database.collection('chats').document(message_id).get()
            if not target.exists or (target.to_dict() or {}).get('chatRoom') != 'community':
                return jsonify({'error':'Community message not found.'}),404
            uid=detail.get('uid')
            report_ref=database.collection('chatReports').document()
            report_ref.set({'messageId':message_id,'reporterUid':uid,'reason':reason,'status':'open','createdAt':datetime.now(timezone.utc)})
            return jsonify({'success':True,'reportId':report_ref.id}),201
        except Exception:
            app.logger.exception('Community chat report failed')
            return jsonify({'error':'Could not submit report.'}),500

    @app.get('/api/admin/chat/reports')
    def admin_chat_reports():
        ok, detail = require_admin()
        if not ok: return detail
        try:
            rows=[]
            for snap in db().collection('chatReports').where('status','==','open').limit(200).stream():
                x=snap.to_dict() or {}; x['id']=snap.id; x['createdAt']=iso(x.get('createdAt')); rows.append(x)
            rows.sort(key=lambda x:x.get('createdAt') or '', reverse=True)
            return jsonify({'success':True,'reports':rows})
        except Exception:
            return jsonify({'error':'Could not load chat reports.'}),500

    @app.post('/api/admin/chat/reports/<report_id>/resolve')
    def resolve_chat_report(report_id):
        ok, detail = require_admin()
        if not ok: return detail
        try:
            ref=db().collection('chatReports').document(clean(report_id,150)); snap=ref.get()
            if not snap.exists: return jsonify({'error':'Report not found.'}),404
            ref.update({'status':'resolved','resolvedAt':datetime.now(timezone.utc),'resolvedBy':detail.get('uid')})
            return jsonify({'success':True})
        except Exception:
            return jsonify({'error':'Could not resolve report.'}),500

    @app.delete('/api/admin/chat/messages/<message_id>')
    def admin_chat_delete(message_id):
        ok, detail=require_admin()
        if not ok:return detail
        try:
            db().collection('chats').document(clean(message_id,150)).delete()
            return jsonify({'success':True})
        except Exception:
            return jsonify({'error':'Could not delete message.'}),500

    @app.get('/api/admin/fcm-tokens')
    def admin_fcm_tokens():
        ok, detail=require_admin()
        if not ok:return detail
        try:
            tokens=[]
            for snap in db().collection('fcmTokens').limit(500).stream():
                token=(snap.to_dict() or {}).get('token')
                if token: tokens.append(token)
            return jsonify({'success':True,'tokens':tokens,'count':len(tokens)})
        except Exception:
            return jsonify({'error':'Could not load notification devices.'}),500

    @app.get('/api/admin/live-stream')
    def admin_live_stream_get():
        ok, detail=require_admin()
        if not ok:return detail
        try:
            snap=db().collection('settings').document('liveStream').get()
            return jsonify({'success':True,'stream':snap.to_dict() if snap.exists else {}})
        except Exception:return jsonify({'error':'Could not load live stream status.'}),500

    @app.put('/api/admin/live-stream')
    def admin_live_stream_put():
        ok, detail=require_admin()
        if not ok:return detail
        body=request.get_json(silent=True) or {}; url=clean(body.get('url'),1000); title=clean(body.get('title'),160)
        active=bool(body.get('isActive',True)); live_type=clean(body.get('type','Free'),30)
        if active:
            low=url.lower()
            if 't.me/' in low or 'telegram.me/' in low:
                return jsonify({'error':'Telegram bot links cannot be marked as a live stream. Use a YouTube Live or supported video stream URL.'}),400
            if not (low.startswith('https://') or low.startswith('http://')):
                return jsonify({'error':'A valid HTTPS/HTTP live URL is required.'}),400
        try:
            data={'title':title,'url':url,'type':live_type,'isActive':active,'isMuted':bool(body.get('isMuted',False)),
                  'updatedAt':datetime.now(timezone.utc),'updatedBy':detail.get('uid')}
            db().collection('settings').document('liveStream').set(data,merge=True)
            return jsonify({'success':True,'stream':data})
        except Exception:return jsonify({'error':'Could not save live stream settings.'}),500
