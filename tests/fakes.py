"""Lightweight test doubles for the BMT Telegram bot test suite.

Real firebase_admin is not installed in this environment (and should not be
required just to run tests). FakeFirestore below implements just enough of
the Firestore client surface that telegram_*_routes.py actually calls
(collection/document/where/limit/stream/get/set/update) to let us write real
behavioural tests instead of the string-matching "regression scripts" in
scripts/*_test.py.
"""
import itertools
import json

_id_counter = itertools.count(1)


def _match(value, op, target):
    if op == "==":
        return value == target
    if op == "in":
        return value in (target or [])
    if op == ">=":
        return value is not None and value >= target
    if op == "<=":
        return value is not None and value <= target
    if op == ">":
        return value is not None and value > target
    if op == "<":
        return value is not None and value < target
    raise NotImplementedError(f"Unsupported Firestore operator in fake: {op}")


class FakeDocSnapshot:
    def __init__(self, doc_id, data, exists, ref):
        self.id = doc_id
        self._data = dict(data) if data else {}
        self.exists = exists
        self.reference = ref

    def to_dict(self):
        return dict(self._data)


class FakeDocRef:
    def __init__(self, store, collection_name, doc_id):
        self._store = store
        self._collection_name = collection_name
        self.id = doc_id

    def get(self, transaction=None):
        # `transaction` is accepted (and ignored) to match the real
        # `DocumentReference.get(transaction=...)` signature that route
        # modules call inside `@firestore.transactional` functions. The fake
        # store isn't actually concurrent, so a transactional read is just
        # a normal read.
        coll = self._store.setdefault(self._collection_name, {})
        data = coll.get(self.id)
        return FakeDocSnapshot(self.id, data, data is not None, self)

    def set(self, data, merge=False):
        coll = self._store.setdefault(self._collection_name, {})
        resolved = {k: _resolve_value(v) for k, v in data.items()}
        if merge and self.id in coll:
            coll[self.id].update(resolved)
        else:
            coll[self.id] = resolved

    def update(self, data):
        coll = self._store.setdefault(self._collection_name, {})
        doc = coll.setdefault(self.id, {})
        for key, value in data.items():
            # Firestore dotted field paths ("reactions.like") address a
            # nested map field without overwriting its siblings - route code
            # relies on this to bump one reaction count without clobbering
            # the others, so the fake has to nest instead of doing a flat
            # dict.update().
            parts = key.split(".")
            target = doc
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            leaf = parts[-1]
            if isinstance(value, FakeIncrement):
                target[leaf] = (target.get(leaf) or 0) + value.amount
            else:
                target[leaf] = _resolve_value(value)

    def delete(self):
        coll = self._store.setdefault(self._collection_name, {})
        coll.pop(self.id, None)

    def collection(self, name):
        # Subcollections are modeled as their own flat namespace keyed by the
        # full path (parentCollection/parentId/name). This is unique per
        # parent document, which is all the fake needs: real Firestore
        # subcollections are similarly independent of documents with the
        # same ID under a different parent.
        return FakeQuery(self._store, f"{self._collection_name}/{self.id}/{name}")


class FakeQuery:
    def __init__(self, store, collection_name, filters=None, limit_n=None, order_by_field=None, order_desc=False):
        self._store = store
        self._collection_name = collection_name
        self._filters = filters or []
        self._limit_n = limit_n
        self._order_by_field = order_by_field
        self._order_desc = order_desc

    def where(self, field, op, value):
        return FakeQuery(self._store, self._collection_name, self._filters + [(field, op, value)],
                          self._limit_n, self._order_by_field, self._order_desc)

    def limit(self, n):
        return FakeQuery(self._store, self._collection_name, self._filters, n,
                          self._order_by_field, self._order_desc)

    def order_by(self, field, direction="ASCENDING"):
        return FakeQuery(self._store, self._collection_name, self._filters, self._limit_n,
                          field, str(direction).upper().startswith("DESC"))

    def document(self, doc_id=None):
        if doc_id is None:
            doc_id = f"auto_{next(_id_counter)}"
        return FakeDocRef(self._store, self._collection_name, doc_id)

    def stream(self):
        coll = self._store.get(self._collection_name, {})
        results = []
        for doc_id, data in coll.items():
            if all(_match(data.get(f), op, v) for f, op, v in self._filters):
                ref = FakeDocRef(self._store, self._collection_name, doc_id)
                results.append(FakeDocSnapshot(doc_id, data, True, ref))
        if self._order_by_field is not None:
            # Missing/None values sort first regardless of direction, same
            # spirit as Firestore's NULL-first ordering, so tests don't have
            # to special-case documents that never set the ordered field.
            def sort_key(snap):
                value = snap.to_dict().get(self._order_by_field)
                return (value is None, value if value is not None else 0)
            results.sort(key=sort_key, reverse=self._order_desc)
        if self._limit_n is not None:
            results = results[: self._limit_n]
        return iter(results)


class FakeIncrement:
    """Stand-in for firestore.Increment(n) - a sentinel the real SDK resolves
    server-side into an atomic += on the target field. The fake resolves it
    immediately in FakeDocRef.update() instead, which is equivalent for
    single-threaded tests."""

    def __init__(self, amount):
        self.amount = amount


class FakeServerTimestamp:
    """Stand-in for firestore.SERVER_TIMESTAMP - a sentinel the real SDK
    resolves server-side to the write's commit time. The fake resolves it
    eagerly (to "now") wherever it's written, since there's no real
    server round-trip to defer to - equivalent for single-threaded tests
    that only care that *some* timestamp landed, not its exact value."""


SERVER_TIMESTAMP = FakeServerTimestamp()


def _resolve_value(value):
    if isinstance(value, FakeServerTimestamp):
        import datetime as _dt
        return _dt.datetime.now(_dt.timezone.utc)
    return value


class FakeAlreadyExists(Exception):
    """Mirrors google.api_core.exceptions.AlreadyExists closely enough for
    route code that catches a create-on-existing-doc conflict."""


class FakeTransaction:
    """Stand-in for a Firestore Transaction. The fake store has no real
    concurrency, so every operation applies immediately against the shared
    dict instead of buffering until commit() - that's a faithful enough model
    for single-threaded tests, which only care about the net effect of a
    transaction, not about isolation from concurrent writers."""

    def __init__(self, store):
        self._store = store

    def get(self, ref):
        return ref.get()

    def create(self, ref, data):
        coll = self._store.setdefault(ref._collection_name, {})
        if ref.id in coll:
            raise FakeAlreadyExists(f"Document already exists: {ref._collection_name}/{ref.id}")
        coll[ref.id] = dict(data)

    def set(self, ref, data, merge=False):
        ref.set(data, merge=merge)

    def update(self, ref, data):
        ref.update(data)

    def commit(self):
        return []


def fake_transactional(func):
    """Stand-in for the `@firestore.transactional` decorator. Real Firestore
    retries the wrapped function on contention; the fake has no contention to
    retry, so this just calls straight through with the transaction as the
    first argument, matching how route modules invoke `wrapped(tx)`."""
    return func


class FakeBatch:
    """Stand-in for a Firestore WriteBatch. Same immediate-apply model as
    FakeTransaction: no isolation, operations land on commit() in the order
    they were queued, which is all a single-threaded test needs."""

    def __init__(self, store):
        self._store = store
        self._ops = []

    def set(self, ref, data, merge=False):
        self._ops.append(lambda: ref.set(data, merge=merge))
        return self

    def update(self, ref, data):
        self._ops.append(lambda: ref.update(data))
        return self

    def delete(self, ref):
        self._ops.append(lambda: ref.delete())
        return self

    def commit(self):
        for op in self._ops:
            op()
        self._ops = []
        return []


class FakeFirestore:
    """Stand-in for firestore.client(). Supports exactly what the telegram
    route modules use, backed by a plain dict so tests can seed/inspect state
    directly instead of mocking every call."""

    def __init__(self, seed=None):
        self._store = {k: dict(v) for k, v in (seed or {}).items()}

    def collection(self, name):
        return FakeQuery(self._store, name)

    def transaction(self):
        return FakeTransaction(self._store)

    def batch(self):
        return FakeBatch(self._store)

    def seed(self, collection_name, doc_id, data):
        self._store.setdefault(collection_name, {})[doc_id] = dict(data)

    def dump(self, collection_name):
        return dict(self._store.get(collection_name, {}))

    def count(self, collection_name):
        return len(self._store.get(collection_name, {}))


class FakeTelegramResponse:
    def __init__(self, ok=True, result=None):
        self.ok = ok
        self._payload = {"ok": ok, "result": result}
        if result is None:
            self._payload["result"] = {}

    def json(self):
        return self._payload


class FakeTelegramAPI:
    """Replaces requests.post for calls to api.telegram.org. Records every
    call so tests can assert on exactly what was sent (chat_id, text,
    reply_markup, etc.) instead of just "no exception was raised"."""

    _DEFAULT_RESULTS = {
        "deleteMessage": True,
        "restrictChatMember": True,
        "banChatMember": True,
        "answerCallbackQuery": True,
    }

    def __init__(self):
        self.calls = []

    def post(self, url, json=None, timeout=None, **kwargs):
        method = url.rsplit("/", 1)[-1]
        self.calls.append({"method": method, "payload": json or {}})
        if method in self._DEFAULT_RESULTS:
            result = self._DEFAULT_RESULTS[method]
        else:
            result = {"message_id": len(self.calls)}
        return FakeTelegramResponse(True, result)

    def calls_for(self, method):
        return [c["payload"] for c in self.calls if c["method"] == method]


class FakeHttpResponse:
    """Generic stand-in for requests.Response, covering the surface admin
    route code actually reads: .ok, .status_code, .content (truthiness),
    .json(), .headers, and .text."""

    def __init__(self, status_code=200, json_data=None, content=b"{}", headers=None, text=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 400
        self._json = {} if json_data is None else json_data
        self.content = content
        self.headers = headers or {}
        self.text = text if text is not None else str(self._json)

    def json(self):
        return self._json


class FakeCloudinaryAPI:
    """Replaces requests.post for calls to api.cloudinary.com. Records every
    call (url/data/files) so tests can assert on what was uploaded, and
    returns a scripted success/failure response - no real network call is
    ever made."""

    def __init__(self, secure_url="https://res.cloudinary.com/demo/image/upload/v1/logo.png",
                 public_id="demo_public_id", extra=None, fail=False, status_code=None):
        self.calls = []
        self.secure_url = secure_url
        self.public_id = public_id
        self.extra = extra or {}
        self.fail = fail
        self.status_code = status_code if status_code is not None else (502 if fail else 200)

    def post(self, url, data=None, files=None, timeout=None, **kwargs):
        self.calls.append({"url": url, "data": data, "files": files})
        if self.fail:
            payload = {"error": {"message": "Simulated Cloudinary failure"}}
        else:
            payload = {"secure_url": self.secure_url, "public_id": self.public_id,
                       "resource_type": "image", "bytes": 123, "format": "png", **self.extra}
        return FakeHttpResponse(status_code=self.status_code, json_data=payload)


class FakeGeminiAPI:
    """Replaces requests.post for calls to generativelanguage.googleapis.com.
    Records every call (url/headers/json) so tests can assert on the prompt
    and inline image parts that were sent, and returns a scripted response
    shaped like the real Gemini generateContent API - a list of candidates
    each carrying text parts, where smart_quiz_scanner_routes.py expects the
    text of the (only) part to be a JSON object string.

    By default returns one well-formed question so happy-path tests don't
    each have to build the candidates envelope by hand; pass `questions=`
    (a list of raw dicts, pre-normalization) to control extraction content,
    `raw_text=` to return arbitrary/malformed text instead, or `ok=False` /
    `status_code=` to simulate the upstream rejecting the request."""

    def __init__(self, questions=None, raw_text=None, ok=True, status_code=None):
        self.calls = []
        self.ok = ok
        self.status_code = status_code if status_code is not None else (200 if ok else 503)
        if raw_text is not None:
            self._text = raw_text
        else:
            default_questions = questions if questions is not None else [{
                "question": "2 + 2 = ?",
                "options": ["3", "4", "5", "6"],
                "correctAnswer": "B",
                "pageNumber": 1,
                "sourceRef": "client-supplied-should-be-overwritten",
                "learningObjective": "Addition",
            }]
            self._text = json.dumps({"questions": default_questions})

    def post(self, url, headers=None, json=None, timeout=None, **kwargs):
        self.calls.append({"url": url, "headers": headers, "json": json})
        response_payload = {
            "candidates": [{"content": {"parts": [{"text": self._text}]}}]
        } if self.ok else {"error": {"message": "Simulated Gemini failure"}}
        return FakeHttpResponse(status_code=self.status_code, json_data=response_payload)


class FakeChapaAPI:
    """Replaces requests.request (app.py's Chapa helper uses
    requests.request, not requests.post, so it needs its own patch target)
    for calls to api.chapa.co. Scripts both the /transaction/initialize and
    /transaction/verify/<ref> shapes app.py actually reads via
    _extract_chapa_data (payload["data"] or the payload itself), and
    records every call so tests can assert on what was sent (tx_ref,
    amount, callback_url, ...)."""

    def __init__(self, ok=True, status_code=None, checkout_url="https://checkout.chapa.co/abc123",
                 verify_status="success", verify_amount=None, verify_currency="ETB",
                 extra_data=None, message="Chapa request failed."):
        self.calls = []
        self.ok = ok
        self.status_code = status_code if status_code is not None else (200 if ok else 400)
        self.checkout_url = checkout_url
        self.verify_status = verify_status
        self.verify_amount = verify_amount
        self.verify_currency = verify_currency
        self.extra_data = extra_data or {}
        self.message = message

    def request(self, method, url, headers=None, json=None, timeout=None, **kwargs):
        self.calls.append({"method": method, "url": url, "headers": headers, "json": json})
        if not self.ok:
            payload = {"message": self.message}
        elif "/transaction/initialize" in url:
            payload = {"status": "success", "data": {"checkout_url": self.checkout_url, **self.extra_data}}
        elif "/transaction/verify/" in url:
            data = dict(self.extra_data)
            data["status"] = self.verify_status
            if self.verify_currency is not None:
                data["currency"] = self.verify_currency
            if self.verify_amount is not None:
                data["amount"] = self.verify_amount
            payload = {"status": "success", "data": data}
        else:
            payload = {"status": "success", "data": {}}
        return FakeHttpResponse(status_code=self.status_code, json_data=payload)


class FakeTelegramBotAdminAPI:
    """Replaces both requests.get and requests.post for the admin Telegram
    bot control-panel routes in app.py (getMe/getWebhookInfo via GET,
    setWebhook via POST) - distinct from FakeTelegramAPI above, which only
    covers the moderator/publisher bots' own POST-only outbound calls
    (sendMessage, deleteMessage, ...)."""

    def __init__(self, bot_ok=True, bot_name="BMT Bot", username="bmt_bot",
                 webhook_url="", pending_updates=0, set_webhook_ok=True,
                 set_webhook_description="Bad Request: bad webhook", raise_connection_error=False):
        self.calls = []
        self.bot_ok = bot_ok
        self.bot_name = bot_name
        self.username = username
        self.webhook_url = webhook_url
        self.pending_updates = pending_updates
        self.set_webhook_ok = set_webhook_ok
        self.set_webhook_description = set_webhook_description
        self.raise_connection_error = raise_connection_error

    def get(self, url, timeout=None, **kwargs):
        if self.raise_connection_error:
            import requests
            raise requests.exceptions.ConnectionError("simulated network failure")
        self.calls.append({"method": "GET", "url": url})
        if url.endswith("/getMe"):
            payload = {"ok": self.bot_ok, "result": {"first_name": self.bot_name, "username": self.username}}
        elif url.endswith("/getWebhookInfo"):
            payload = {"ok": True, "result": {"url": self.webhook_url, "pending_update_count": self.pending_updates}}
        else:
            payload = {"ok": False}
        return FakeHttpResponse(status_code=200, json_data=payload)

    def post(self, url, json=None, timeout=None, **kwargs):
        if self.raise_connection_error:
            import requests
            raise requests.exceptions.ConnectionError("simulated network failure")
        self.calls.append({"method": "POST", "url": url, "json": json})
        if self.set_webhook_ok:
            payload = {"ok": True, "result": True}
            status = 200
        else:
            payload = {"ok": False, "description": self.set_webhook_description}
            status = 400
        resp = FakeHttpResponse(status_code=status, json_data=payload)
        resp.text = self.set_webhook_description if not self.set_webhook_ok else "OK"
        return resp


class FakeFirebaseUser:
    """Stand-in for the UserRecord object firebase_admin.auth returns."""

    def __init__(self, uid, phone_number=None, display_name=None):
        self.uid = uid
        self.phone_number = phone_number
        self.display_name = display_name


class FakeFirebaseAuth:
    """Stand-in for the firebase_admin.auth module, covering only the
    surface app.py's phone+PIN auth routes actually use: looking up/
    creating users by phone number, and minting custom tokens. There is no
    real Firebase Identity Platform involved - tests seed users via
    create_user() (or seed_user() directly) and then assert on what the
    route did with the fake user/token."""

    class UserNotFoundError(Exception):
        pass

    def __init__(self):
        self._by_phone = {}
        self._by_uid = {}
        self._next_id = 1
        self.custom_tokens = []  # [(uid, claims_dict), ...] in call order
        self._id_tokens = {}  # token string -> decoded claims dict, for verify_id_token
        self.revoked_uids = set()

    def script_id_token(self, token, uid, **extra_claims):
        """Registers a bearer token string that verify_id_token() will
        accept and decode to {"uid": uid, **extra_claims} - needed for
        tests that exercise the real _require_user_bearer/
        _require_admin_bearer instead of monkeypatching them (the normal
        pattern used everywhere else in this test suite)."""
        self._id_tokens[token] = {"uid": uid, **extra_claims}

    def revoke_refresh_tokens(self, uid):
        self.revoked_uids.add(uid)

    def seed_user(self, uid, phone_number=None, display_name=None):
        user = FakeFirebaseUser(uid, phone_number=phone_number, display_name=display_name)
        if phone_number:
            self._by_phone[phone_number] = user
        self._by_uid[uid] = user
        return user

    def get_user_by_phone_number(self, phone_number):
        user = self._by_phone.get(phone_number)
        if user is None:
            raise self.UserNotFoundError(f"No user record found for phone number: {phone_number}")
        return user

    def create_user(self, phone_number=None, display_name=None, uid=None, **kwargs):
        new_uid = uid or f"fake-uid-{self._next_id}"
        self._next_id += 1
        return self.seed_user(new_uid, phone_number=phone_number, display_name=display_name)

    def create_custom_token(self, uid, developer_claims=None):
        claims = dict(developer_claims or {})
        self.custom_tokens.append((uid, claims))
        return f"fake-custom-token:{uid}".encode("utf-8")

    def verify_id_token(self, token, check_revoked=False):
        # Only reached if a test exercises _require_user_bearer/
        # _require_admin_bearer for real instead of monkeypatching them
        # (the normal pattern used throughout this test suite) - use
        # script_id_token() above to register an acceptable token first.
        if token not in self._id_tokens:
            raise ValueError("FakeFirebaseAuth.verify_id_token is not scripted for this test")
        claims = self._id_tokens[token]
        if check_revoked and claims.get("uid") in self.revoked_uids:
            raise ValueError("Token has been revoked.")
        return claims


class FakeFCMMessage:
    def __init__(self, notification=None, token=None):
        self.notification = notification
        self.token = token


class FakeFCMNotification:
    def __init__(self, title=None, body=None):
        self.title = title
        self.body = body


class FakeFCMSendResponse:
    def __init__(self, success, exception=None):
        self.success = success
        self.exception = exception


class FakeFCMBatchResponse:
    def __init__(self, responses):
        self.responses = responses
        self.success_count = sum(1 for r in responses if r.success)
        self.failure_count = sum(1 for r in responses if not r.success)


class FakeMessaging:
    """Stand-in for firebase_admin.messaging, covering only what app.py's
    /api/notifications/send and the optional announcement push use:
    Message/Notification constructors and send_each(). Script per-token
    outcomes via `outcomes` - a list matched positionally to the tokens in
    each send_each() call, where True means delivered and an Exception
    instance means that token failed with that error (so tests can verify
    invalid-token cleanup and error-code aggregation)."""

    def __init__(self, outcomes=None):
        self.outcomes = outcomes
        self.calls = []

    def Message(self, notification=None, token=None):
        return FakeFCMMessage(notification, token)

    def Notification(self, title=None, body=None):
        return FakeFCMNotification(title, body)

    def send_each(self, messages):
        self.calls.append(messages)
        responses = []
        for i, _m in enumerate(messages):
            outcome = self.outcomes[i] if self.outcomes and i < len(self.outcomes) else True
            responses.append(FakeFCMSendResponse(True) if outcome is True
                              else FakeFCMSendResponse(False, exception=outcome))
        return FakeFCMBatchResponse(responses)
