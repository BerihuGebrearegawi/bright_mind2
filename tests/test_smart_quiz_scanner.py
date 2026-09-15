"""Real behavioural tests for smart_quiz_scanner_routes.py: admin/approved-
teacher gating, upload validation, the Library-location metadata chain,
Gemini OCR extraction (including its error modes), the review step, and -
the highest-risk area, per this module's own comment about closing a
duplicate-import race - the transactional import into quizzes/questionBank
and its idempotency guarantee.

Real fitz/pypdf/cloudinary/requests are installed in this environment, but
none of these tests make a real network call or parse a real PDF/image:
requests.post is patched to a scripted FakeGeminiAPI, and the PDF page-
extraction helpers (which do their heavy-lifting via lazily-imported fitz/
pypdf) are monkeypatched at the smart_quiz_scanner_routes module level so
tests exercise the route's own branching logic, not third-party libraries.
"""
import io
import json

import pytest

import smart_quiz_scanner_routes as scanner_module
from smart_quiz_scanner_routes import register_smart_quiz_scanner_routes
from tests.conftest import make_app
from tests.fakes import FakeGeminiAPI


def require_user_as(uid, admin=False):
    return lambda: (True, {"uid": uid, "admin": admin})


def require_user_denied():
    return lambda: (False, ({"error": "Sign in required."}, 401))


def _client(require_user, firebase_configured=True):
    factory = (lambda: True) if firebase_configured else (lambda: None)
    require_admin = lambda: (True, {})  # noqa: E731 - unused by this module, kept for signature parity
    app = make_app(register_smart_quiz_scanner_routes, require_user, require_admin, factory)
    return app.test_client()


def _seed_admin(fake_db, uid="admin-1"):
    fake_db.seed("users", uid, {"accountType": "admin"})


def _seed_approved_teacher(fake_db, uid="teacher-1"):
    fake_db.seed("teachers", uid, {"approved": True})


def _seed_unapproved_teacher(fake_db, uid="teacher-2"):
    fake_db.seed("teachers", uid, {"approved": False})


def _png(name="scan.png", content=b"\x89PNGfakebytes"):
    return (io.BytesIO(content), name, "image/png")


def _pdf(name="scan.pdf", content=b"%PDF-fake"):
    return (io.BytesIO(content), name, "application/pdf")


def _default_question(n=1, correct="B"):
    return {
        "question": f"Question {n}",
        "options": {"A": "wrong", "B": "right", "C": "wrong", "D": "wrong"},
        "correctAnswer": correct,
        "pageNumber": 1,
        "sourceRef": f"scanner:seed:page:1",
        "learningObjective": "Objective",
    }


def _seed_scan(fake_db, scan_id="scan1", owner="teacher-1", role="teacher", status="REVIEWED",
               questions=None, **extra):
    doc = {
        "sourceScanId": scan_id,
        "ownerUid": owner,
        "ownerRole": role,
        "fileName": "scan.png",
        "contentType": "image/png",
        "status": status,
        "metadata": {"sourceScanId": scan_id, "pageNumber": 1},
        "storage": {},
        "questions": [_default_question()] if questions is None else questions,
        "questionCount": 1 if questions is None else len(questions),
        "ocrModel": "gemini-test",
    }
    doc.update(extra)
    fake_db.seed("smartQuizScans", scan_id, doc)
    return doc


@pytest.fixture(autouse=True)
def _gemini_env(monkeypatch):
    """Every extraction path checks GEMINI_API_KEY first; default it to a
    non-empty value so tests opt OUT of OCR (by clearing it) rather than
    every happy-path test having to opt in."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")


class TestScanUploadAuthAndRole:
    def test_signed_out_is_rejected(self, fake_db):
        client = _client(require_user_denied())
        r = client.post("/api/scanner/scan", data={"file": _png()}, content_type="multipart/form-data")
        assert r.status_code == 401

    def test_plain_student_is_rejected(self, fake_db):
        client = _client(require_user_as("student-1"))
        r = client.post("/api/scanner/scan", data={"file": _png()}, content_type="multipart/form-data")
        assert r.status_code == 403

    def test_unapproved_teacher_is_rejected(self, fake_db):
        _seed_unapproved_teacher(fake_db, "teacher-2")
        client = _client(require_user_as("teacher-2"))
        r = client.post("/api/scanner/scan", data={"file": _png()}, content_type="multipart/form-data")
        assert r.status_code == 403

    def test_approved_teacher_is_allowed(self, fake_db, monkeypatch):
        _seed_approved_teacher(fake_db, "teacher-1")
        monkeypatch.setattr(scanner_module, "_extract_with_gemini", lambda images, name: ([], "gemini-test"))
        client = _client(require_user_as("teacher-1"))
        r = client.post("/api/scanner/scan", data={"file": _png()}, content_type="multipart/form-data")
        assert r.status_code == 201

    def test_admin_flag_is_allowed_without_a_teacher_doc(self, fake_db, monkeypatch):
        monkeypatch.setattr(scanner_module, "_extract_with_gemini", lambda images, name: ([], "gemini-test"))
        client = _client(require_user_as("admin-1", admin=True))
        r = client.post("/api/scanner/scan", data={"file": _png()}, content_type="multipart/form-data")
        assert r.status_code == 201


class TestScanUploadFileValidation:
    def test_missing_file_is_rejected(self, fake_db):
        client = _client(require_user_as("admin-1", admin=True))
        r = client.post("/api/scanner/scan", data={}, content_type="multipart/form-data")
        assert r.status_code == 400

    def test_unsupported_content_type_is_rejected(self, fake_db):
        client = _client(require_user_as("admin-1", admin=True))
        bad = (io.BytesIO(b"hi"), "scan.txt", "text/plain")
        r = client.post("/api/scanner/scan", data={"file": bad}, content_type="multipart/form-data")
        assert r.status_code == 415

    def test_oversized_file_is_rejected(self, fake_db):
        client = _client(require_user_as("admin-1", admin=True))
        big = (io.BytesIO(b"x" * (scanner_module.MAX_SCAN_BYTES + 10)), "scan.png", "image/png")
        r = client.post("/api/scanner/scan", data={"file": big}, content_type="multipart/form-data")
        assert r.status_code == 413

    def test_empty_file_is_rejected(self, fake_db):
        client = _client(require_user_as("admin-1", admin=True))
        empty = (io.BytesIO(b""), "scan.png", "image/png")
        r = client.post("/api/scanner/scan", data={"file": empty}, content_type="multipart/form-data")
        assert r.status_code == 400


class TestScanUploadLibraryMetadata:
    def _client_admin(self):
        return _client(require_user_as("admin-1", admin=True))

    def test_book_id_without_collection_is_rejected(self, fake_db):
        client = self._client_admin()
        r = client.post("/api/scanner/scan", data={"file": _png(), "bookId": "b1"},
                         content_type="multipart/form-data")
        assert r.status_code == 400

    def test_unknown_collection_is_rejected(self, fake_db):
        client = self._client_admin()
        r = client.post("/api/scanner/scan",
                         data={"file": _png(), "collection": "notARealCollection", "bookId": "b1"},
                         content_type="multipart/form-data")
        assert r.status_code == 400

    def test_chapter_without_book_is_rejected(self, fake_db):
        client = self._client_admin()
        r = client.post("/api/scanner/scan",
                         data={"file": _png(), "collection": "books", "chapterId": "c1"},
                         content_type="multipart/form-data")
        assert r.status_code == 400

    def test_subchapter_without_chapter_is_rejected(self, fake_db):
        client = self._client_admin()
        r = client.post("/api/scanner/scan",
                         data={"file": _png(), "collection": "books", "bookId": "b1", "subchapterId": "s1"},
                         content_type="multipart/form-data")
        assert r.status_code == 400

    def test_nonexistent_book_is_rejected(self, fake_db):
        client = self._client_admin()
        r = client.post("/api/scanner/scan",
                         data={"file": _png(), "collection": "books", "bookId": "missing"},
                         content_type="multipart/form-data")
        assert r.status_code == 404

    def test_nonexistent_chapter_is_rejected(self, fake_db):
        fake_db.seed("books", "b1", {"title": "Book"})
        client = self._client_admin()
        r = client.post("/api/scanner/scan",
                         data={"file": _png(), "collection": "books", "bookId": "b1", "chapterId": "missing"},
                         content_type="multipart/form-data")
        assert r.status_code == 404

    def test_nonexistent_subchapter_is_rejected(self, fake_db):
        fake_db.seed("books", "b1", {"title": "Book"})
        fake_db.collection("books").document("b1").collection("chapters").document("c1").set({"title": "Ch1"})
        client = self._client_admin()
        r = client.post("/api/scanner/scan",
                         data={"file": _png(), "collection": "books", "bookId": "b1",
                               "chapterId": "c1", "subchapterId": "missing"},
                         content_type="multipart/form-data")
        assert r.status_code == 404

    def test_full_valid_library_chain_is_accepted(self, fake_db, monkeypatch):
        fake_db.seed("books", "b1", {"title": "Book"})
        fake_db.collection("books").document("b1").collection("chapters").document("c1").set({"title": "Ch1"})
        (fake_db.collection("books").document("b1").collection("chapters").document("c1")
         .collection("subchapters").document("s1").set({"title": "Sub1"}))
        monkeypatch.setattr(scanner_module, "_extract_with_gemini", lambda images, name: ([], "gemini-test"))
        client = self._client_admin()
        r = client.post("/api/scanner/scan",
                         data={"file": _png(), "collection": "books", "bookId": "b1",
                               "chapterId": "c1", "subchapterId": "s1"},
                         content_type="multipart/form-data")
        assert r.status_code == 201


class TestScanExtractionImagePath:
    def test_missing_gemini_key_returns_503(self, fake_db, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        client = _client(require_user_as("admin-1", admin=True))
        r = client.post("/api/scanner/scan", data={"file": _png()}, content_type="multipart/form-data")
        assert r.status_code == 503

    def test_gemini_rejecting_the_request_returns_503(self, fake_db, monkeypatch):
        import requests as requests_module
        fake = FakeGeminiAPI(ok=False)
        monkeypatch.setattr(requests_module, "post", fake.post)
        client = _client(require_user_as("admin-1", admin=True))
        r = client.post("/api/scanner/scan", data={"file": _png()}, content_type="multipart/form-data")
        assert r.status_code == 503

    def test_non_json_gemini_response_returns_500(self, fake_db, monkeypatch):
        import requests as requests_module
        fake = FakeGeminiAPI(raw_text="not json at all")
        monkeypatch.setattr(requests_module, "post", fake.post)
        client = _client(require_user_as("admin-1", admin=True))
        r = client.post("/api/scanner/scan", data={"file": _png()}, content_type="multipart/form-data")
        assert r.status_code == 500

    def test_questions_not_a_list_returns_503(self, fake_db, monkeypatch):
        import requests as requests_module
        fake = FakeGeminiAPI(raw_text=json.dumps({"questions": "oops"}))
        monkeypatch.setattr(requests_module, "post", fake.post)
        client = _client(require_user_as("admin-1", admin=True))
        r = client.post("/api/scanner/scan", data={"file": _png()}, content_type="multipart/form-data")
        assert r.status_code == 503

    def test_malformed_questions_are_silently_dropped_not_errored(self, fake_db, monkeypatch):
        import requests as requests_module
        fake = FakeGeminiAPI(questions=[
            {"question": "", "options": ["1", "2", "3", "4"], "correctAnswer": "A"},  # empty question text
            {"question": "Only three options?", "options": ["1", "2", "3"], "correctAnswer": "A"},  # <4 options
        ])
        monkeypatch.setattr(requests_module, "post", fake.post)
        client = _client(require_user_as("admin-1", admin=True))
        r = client.post("/api/scanner/scan", data={"file": _png()}, content_type="multipart/form-data")
        assert r.status_code == 201
        assert r.get_json()["questionCount"] == 0

    def test_successful_scan_creates_extracted_doc(self, fake_db, monkeypatch):
        import requests as requests_module
        fake = FakeGeminiAPI(questions=[{
            "question": "2 + 2 = ?", "options": ["3", "4", "5", "6"], "correctAnswer": "B",
            "pageNumber": 1, "sourceRef": "client should never win", "learningObjective": "",
        }])
        monkeypatch.setattr(requests_module, "post", fake.post)
        client = _client(require_user_as("teacher-1"))
        _seed_approved_teacher(fake_db, "teacher-1")
        r = client.post("/api/scanner/scan",
                         data={"file": _png(), "learningObjective": "Arithmetic"},
                         content_type="multipart/form-data")
        assert r.status_code == 201
        body = r.get_json()
        assert body["questionCount"] == 1
        scan_id = body["sourceScanId"]
        stored = fake_db.dump("smartQuizScans")[scan_id]
        assert stored["status"] == "EXTRACTED"
        assert stored["ownerUid"] == "teacher-1"
        assert stored["ownerRole"] == "teacher"
        q = stored["questions"][0]
        # Provenance is server-controlled: a client/model-supplied sourceRef
        # must never survive into the stored record.
        assert q["sourceRef"] == f"scanner:{scan_id}:page:1"
        # learningObjective falls back to the request-level metadata when the
        # model didn't supply one for this specific question.
        assert q["learningObjective"] == "Arithmetic"

    def test_options_list_form_is_mapped_to_abcd(self, fake_db, monkeypatch):
        import requests as requests_module
        fake = FakeGeminiAPI(questions=[{
            "question": "Pick one", "options": ["opt1", "opt2", "opt3", "opt4"], "correctAnswer": "c",
        }])
        monkeypatch.setattr(requests_module, "post", fake.post)
        client = _client(require_user_as("admin-1", admin=True))
        r = client.post("/api/scanner/scan", data={"file": _png()}, content_type="multipart/form-data")
        q = r.get_json()["questions"][0]
        assert q["options"] == {"A": "opt1", "B": "opt2", "C": "opt3", "D": "opt4"}
        assert q["correctAnswer"] == "C"  # lowercase input is upper-cased

    def test_invalid_correct_answer_letter_is_blanked_not_rejected(self, fake_db, monkeypatch):
        import requests as requests_module
        fake = FakeGeminiAPI(questions=[{
            "question": "Pick one", "options": ["1", "2", "3", "4"], "correctAnswer": "Z",
        }])
        monkeypatch.setattr(requests_module, "post", fake.post)
        client = _client(require_user_as("admin-1", admin=True))
        r = client.post("/api/scanner/scan", data={"file": _png()}, content_type="multipart/form-data")
        assert r.get_json()["questions"][0]["correctAnswer"] == ""


class TestScanExtractionPdfPath:
    def test_text_pdf_with_enough_text_skips_image_ocr(self, fake_db, monkeypatch):
        """A text-bearing PDF is fed to Gemini as a text prompt (no page
        images rasterized), so _pdf_pages should never be invoked."""
        monkeypatch.setattr(scanner_module, "_text_pdf",
                             lambda raw: [(1, "Q: what is 1+1? A) 1 B) 2 C) 3 D) 4 answer B" * 3)])
        pdf_pages_called = []
        monkeypatch.setattr(scanner_module, "_pdf_pages", lambda raw: pdf_pages_called.append(1) or [])
        import requests as requests_module
        fake = FakeGeminiAPI(questions=[{"question": "what is 1+1?", "options": ["1", "2", "3", "4"], "correctAnswer": "B"}])
        monkeypatch.setattr(requests_module, "post", fake.post)
        client = _client(require_user_as("admin-1", admin=True))
        r = client.post("/api/scanner/scan", data={"file": _pdf()}, content_type="multipart/form-data")
        assert r.status_code == 201
        assert pdf_pages_called == []

    def test_scanned_pdf_with_no_text_falls_back_to_image_ocr(self, fake_db, monkeypatch):
        monkeypatch.setattr(scanner_module, "_text_pdf", lambda raw: [])
        monkeypatch.setattr(scanner_module, "_pdf_pages", lambda raw: [(1, "image/png", b"fake-page-bytes")])
        monkeypatch.setattr(scanner_module, "_extract_with_gemini",
                             lambda images, name: ([{"question": "Q", "options": {"A": "1", "B": "2", "C": "3", "D": "4"}, "correctAnswer": "A", "pageNumber": 1, "sourceRef": "", "learningObjective": ""}], "gemini-test"))
        client = _client(require_user_as("admin-1", admin=True))
        r = client.post("/api/scanner/scan", data={"file": _pdf()}, content_type="multipart/form-data")
        assert r.status_code == 201
        assert r.get_json()["questionCount"] == 1

    def test_short_text_pdf_is_treated_as_scanned(self, fake_db, monkeypatch):
        """Text pages with <=20 chars each (e.g. OCR noise/page numbers) are
        discarded, so the route must still fall back to image OCR."""
        monkeypatch.setattr(scanner_module, "_text_pdf", lambda raw: [(1, "pg 1")])
        pdf_pages_called = []

        def fake_pdf_pages(raw):
            pdf_pages_called.append(1)
            return [(1, "image/png", b"bytes")]
        monkeypatch.setattr(scanner_module, "_pdf_pages", fake_pdf_pages)
        monkeypatch.setattr(scanner_module, "_extract_with_gemini", lambda images, name: ([], "gemini-test"))
        client = _client(require_user_as("admin-1", admin=True))
        r = client.post("/api/scanner/scan", data={"file": _pdf()}, content_type="multipart/form-data")
        assert r.status_code == 201
        assert pdf_pages_called == [1]


class TestScanGet:
    def test_missing_scan_is_404(self, fake_db):
        client = _client(require_user_as("admin-1", admin=True))
        r = client.get("/api/scanner/scan/nope")
        assert r.status_code == 404

    def test_non_owner_non_admin_is_denied(self, fake_db):
        _seed_scan(fake_db, "s1", owner="teacher-1")
        client = _client(require_user_as("teacher-2"))
        r = client.get("/api/scanner/scan/s1")
        assert r.status_code == 403

    def test_owner_can_view_own_scan(self, fake_db):
        _seed_scan(fake_db, "s1", owner="teacher-1")
        client = _client(require_user_as("teacher-1"))
        r = client.get("/api/scanner/scan/s1")
        assert r.status_code == 200
        assert r.get_json()["scan"]["id"] == "s1"

    def test_admin_can_view_any_scan(self, fake_db):
        _seed_scan(fake_db, "s1", owner="teacher-1")
        client = _client(require_user_as("admin-1", admin=True))
        r = client.get("/api/scanner/scan/s1")
        assert r.status_code == 200

    def test_storage_url_is_refreshed_not_reused(self, fake_db, monkeypatch):
        _seed_scan(fake_db, "s1", owner="admin-1",
                   storage={"provider": "gcs", "path": "bmt/x/y.pdf", "url": "https://stale-expired-link"})
        import cloudinary_storage
        monkeypatch.setattr(cloudinary_storage, "signed_url", lambda path, **kw: f"https://fresh/{path}")
        client = _client(require_user_as("admin-1", admin=True))
        r = client.get("/api/scanner/scan/s1")
        assert r.get_json()["scan"]["storage"]["url"] == "https://fresh/bmt/x/y.pdf"

    def test_scan_without_stored_original_has_no_url_refresh_attempt(self, fake_db, monkeypatch):
        _seed_scan(fake_db, "s1", owner="admin-1", storage={})
        import cloudinary_storage
        called = []
        monkeypatch.setattr(cloudinary_storage, "signed_url", lambda path, **kw: called.append(1) or "x")
        client = _client(require_user_as("admin-1", admin=True))
        r = client.get("/api/scanner/scan/s1")
        assert r.status_code == 200
        assert called == []


class TestScanReview:
    def test_missing_scan_is_404(self, fake_db):
        client = _client(require_user_as("admin-1", admin=True))
        r = client.put("/api/scanner/scan/nope/review", json={"questions": [_default_question()]})
        assert r.status_code == 404

    def test_non_owner_non_admin_is_denied(self, fake_db):
        _seed_scan(fake_db, "s1", owner="teacher-1", status="EXTRACTED")
        client = _client(require_user_as("teacher-2"))
        r = client.put("/api/scanner/scan/s1/review", json={"questions": [_default_question()]})
        assert r.status_code == 403

    def test_imported_scan_is_immutable(self, fake_db):
        _seed_scan(fake_db, "s1", owner="admin-1", status="IMPORTED")
        client = _client(require_user_as("admin-1", admin=True))
        r = client.put("/api/scanner/scan/s1/review", json={"questions": [_default_question()]})
        assert r.status_code == 409

    def test_empty_question_list_is_rejected(self, fake_db):
        _seed_scan(fake_db, "s1", owner="admin-1", status="EXTRACTED")
        client = _client(require_user_as("admin-1", admin=True))
        r = client.put("/api/scanner/scan/s1/review", json={"questions": []})
        assert r.status_code == 400

    def test_too_many_questions_is_rejected(self, fake_db):
        _seed_scan(fake_db, "s1", owner="admin-1", status="EXTRACTED")
        client = _client(require_user_as("admin-1", admin=True))
        many = [_default_question(i) for i in range(scanner_module.MAX_QUESTIONS + 1)]
        r = client.put("/api/scanner/scan/s1/review", json={"questions": many})
        assert r.status_code == 400

    def test_one_bad_question_fails_the_whole_save(self, fake_db):
        """Unlike extraction (which silently drops malformed items), review
        is an explicit human edit - a single incomplete question must fail
        loudly rather than silently vanish from what the teacher submitted."""
        _seed_scan(fake_db, "s1", owner="admin-1", status="EXTRACTED")
        client = _client(require_user_as("admin-1", admin=True))
        bad = [_default_question(1), {"question": "no options", "options": ["1", "2"], "correctAnswer": "A"}]
        r = client.put("/api/scanner/scan/s1/review", json={"questions": bad})
        assert r.status_code == 400
        assert fake_db.dump("smartQuizScans")["s1"]["status"] == "EXTRACTED"  # untouched

    def test_successful_review_marks_reviewed(self, fake_db):
        _seed_scan(fake_db, "s1", owner="admin-1", status="EXTRACTED")
        client = _client(require_user_as("admin-1", admin=True))
        r = client.put("/api/scanner/scan/s1/review", json={"questions": [_default_question(1, correct="a")]})
        assert r.status_code == 200
        body = r.get_json()
        assert body["status"] == "REVIEWED"
        assert body["questions"][0]["correctAnswer"] == "A"
        stored = fake_db.dump("smartQuizScans")["s1"]
        assert stored["status"] == "REVIEWED"
        assert stored["questionCount"] == 1


class TestScanImportGuards:
    def test_missing_scan_is_404(self, fake_db):
        client = _client(require_user_as("admin-1", admin=True))
        r = client.post("/api/scanner/scan/nope/import")
        assert r.status_code == 404

    def test_non_owner_non_admin_is_denied(self, fake_db):
        _seed_scan(fake_db, "s1", owner="teacher-1", status="REVIEWED")
        client = _client(require_user_as("teacher-2"))
        r = client.post("/api/scanner/scan/s1/import")
        assert r.status_code == 403

    def test_owner_teacher_can_import_own_scan(self, fake_db):
        _seed_scan(fake_db, "s1", owner="teacher-1", status="REVIEWED")
        client = _client(require_user_as("teacher-1"))
        r = client.post("/api/scanner/scan/s1/import", json={"className": "7"})
        assert r.status_code == 201

    def test_not_reviewed_yet_is_rejected(self, fake_db):
        _seed_scan(fake_db, "s1", owner="admin-1", status="EXTRACTED")
        client = _client(require_user_as("admin-1", admin=True))
        r = client.post("/api/scanner/scan/s1/import")
        assert r.status_code == 409

    def test_no_questions_available_is_rejected(self, fake_db):
        _seed_scan(fake_db, "s1", owner="admin-1", status="REVIEWED", questions=[])
        client = _client(require_user_as("admin-1", admin=True))
        # className validation happens before the empty-questions check for
        # the classwork destination, so use questionbank (no className
        # requirement) to isolate the guard actually under test.
        r = client.post("/api/scanner/scan/s1/import", json={"destination": "questionbank"})
        assert r.status_code == 409

    def test_missing_correct_answer_is_rejected_with_question_numbers(self, fake_db):
        q_ok = _default_question(1)
        q_bad = _default_question(2)
        q_bad["correctAnswer"] = ""
        _seed_scan(fake_db, "s1", owner="admin-1", status="REVIEWED", questions=[q_ok, q_bad])
        client = _client(require_user_as("admin-1", admin=True))
        r = client.post("/api/scanner/scan/s1/import", json={"className": "7"})
        assert r.status_code == 400
        assert "2" in r.get_json()["error"]

    def test_invalid_class_for_classwork_destination_is_rejected(self, fake_db):
        _seed_scan(fake_db, "s1", owner="admin-1", status="REVIEWED")
        client = _client(require_user_as("admin-1", admin=True))
        r = client.post("/api/scanner/scan/s1/import", json={"destination": "classwork", "className": "99"})
        assert r.status_code == 400

    def test_unknown_destination_defaults_to_classwork(self, fake_db):
        _seed_scan(fake_db, "s1", owner="admin-1", status="REVIEWED")
        client = _client(require_user_as("admin-1", admin=True))
        # "className" omitted AND destination bogus -> falls back to
        # classwork, which then requires a valid className -> 400, proving
        # the fallback actually took the classwork branch (not questionbank,
        # which has no className requirement at all).
        r = client.post("/api/scanner/scan/s1/import", json={"destination": "not-a-real-destination"})
        assert r.status_code == 400

    def test_questionbank_destination_does_not_require_class_name(self, fake_db):
        _seed_scan(fake_db, "s1", owner="admin-1", status="REVIEWED")
        client = _client(require_user_as("admin-1", admin=True))
        r = client.post("/api/scanner/scan/s1/import", json={"destination": "questionbank"})
        assert r.status_code == 201


class TestScanImportClasswork:
    def test_creates_one_quiz_per_question_with_expected_fields(self, fake_db):
        meta = {"sourceScanId": "s1", "pageNumber": 3, "collection": "books",
                 "bookId": "b1", "chapterId": "c1", "subchapterId": "sc1", "learningObjective": "Fallback LO"}
        q1 = _default_question(1)
        q2 = _default_question(2)
        q2["learningObjective"] = ""  # must fall back to scan-level metadata
        _seed_scan(fake_db, "s1", owner="admin-1", status="REVIEWED", questions=[q1, q2], metadata=meta)
        client = _client(require_user_as("admin-1", admin=True))
        r = client.post("/api/scanner/scan/s1/import",
                         json={"destination": "classwork", "className": "7", "titlePrefix": "Chapter 3 Quiz"})
        assert r.status_code == 201
        body = r.get_json()
        assert body["quizCount"] == 2
        assert body["idempotent"] is False
        quizzes = fake_db.dump("quizzes")
        assert len(quizzes) == 2
        by_title = {q["title"]: q for q in quizzes.values()}
        item = by_title["Chapter 3 Quiz — Q1"]
        assert item["className"] == "7"
        assert item["createdBy"] == "admin-1"
        assert item["sourceScanId"] == "s1"
        assert item["collection"] == "books"
        assert item["bookId"] == "b1"
        assert item["chapterId"] == "c1"
        assert item["subchapterId"] == "sc1"
        assert item["imageUrl"] == ""
        # q2 had no per-question learningObjective -> falls back to scan metadata
        item2 = by_title["Chapter 3 Quiz — Q2"]
        assert item2["learningObjective"] == "Fallback LO"

    def test_scan_marked_imported_with_quiz_ids(self, fake_db):
        _seed_scan(fake_db, "s1", owner="admin-1", status="REVIEWED")
        client = _client(require_user_as("admin-1", admin=True))
        r = client.post("/api/scanner/scan/s1/import", json={"className": "7"})
        ids = r.get_json()["quizIds"]
        stored = fake_db.dump("smartQuizScans")["s1"]
        assert stored["status"] == "IMPORTED"
        assert stored["importedQuizIds"] == ids
        assert stored["importDestination"] == "classwork"


class TestScanImportQuestionBank:
    def test_creates_draft_questions_pending_further_review(self, fake_db):
        _seed_scan(fake_db, "s1", owner="admin-1", status="REVIEWED", questions=[_default_question(1)])
        client = _client(require_user_as("admin-1", admin=True))
        r = client.post("/api/scanner/scan/s1/import",
                         json={"destination": "questionbank", "subject": "Math", "grade": "7", "domain": "Algebra"})
        assert r.status_code == 201
        items = list(fake_db.dump("questionBank").values())
        assert len(items) == 1
        item = items[0]
        # Auto-scraped questions still need a human "approved" pass before
        # they can reach Practice/Challenge/classwork/Telegram.
        assert item["status"] == "draft"
        assert item["source"] == "scanner"
        assert item["subject"] == "Math"
        assert item["grade"] == "7"
        assert item["domain"] == "Algebra"
        assert item["difficulty"] == "medium"
        assert item["points"] == 1
        assert item["createdBy"] == "admin-1"


class TestScanImportIdempotency:
    def test_importing_twice_does_not_duplicate_quizzes(self, fake_db):
        _seed_scan(fake_db, "s1", owner="admin-1", status="REVIEWED", questions=[_default_question(1)])
        client = _client(require_user_as("admin-1", admin=True))
        first = client.post("/api/scanner/scan/s1/import", json={"className": "7"})
        second = client.post("/api/scanner/scan/s1/import", json={"className": "7"})
        assert first.status_code == 201
        assert first.get_json()["idempotent"] is False
        assert second.status_code == 200  # not 201 - nothing new was created
        second_body = second.get_json()
        assert second_body["idempotent"] is True
        assert second_body["quizIds"] == first.get_json()["quizIds"]
        assert len(fake_db.dump("quizzes")) == 1

    def test_repeated_import_calls_are_denial_of_service_safe(self, fake_db):
        """Five repeated import calls against the same reviewed scan must
        settle on exactly the same quiz set, not create N copies - this is
        the duplicate-import race the transaction exists to close."""
        _seed_scan(fake_db, "s1", owner="admin-1", status="REVIEWED",
                   questions=[_default_question(1), _default_question(2)])
        client = _client(require_user_as("admin-1", admin=True))
        results = [client.post("/api/scanner/scan/s1/import", json={"className": "7"}) for _ in range(5)]
        assert [r.status_code for r in results] == [201, 200, 200, 200, 200]
        assert len(fake_db.dump("quizzes")) == 2
        ids = {tuple(sorted(r.get_json()["quizIds"])) for r in results}
        assert len(ids) == 1  # every call reports the identical set of quiz ids
