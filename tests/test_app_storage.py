"""Real behavioural tests for app.py's document storage routes.

/api/storage/document-url is the interesting one: _can_access_gcs_book()
re-authorizes on every call before minting a signed URL (a bearer
credential), so possessing a storage path must never be sufficient on its
own - access has to trace back to an actual Firestore book record the
caller is entitled to (their class, or a book they themselves uploaded),
or admin.
"""
import pytest


def require_user_as(app_module, monkeypatch, uid="user-1", **extra):
    detail = {"uid": uid, **extra}
    monkeypatch.setattr(app_module, "_require_user_bearer", lambda: (True, detail))
    return detail


def require_user_denied(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "_require_user_bearer",
                         lambda: (False, ({"error": "Sign in required."}, 401)))


def _client(app_module):
    return app_module.app.test_client()


class TestUploadImageProxyDeprecated:
    def test_always_returns_410(self, app_module):
        r = _client(app_module).post("/api/storage/image")
        assert r.status_code == 410
        assert r.get_json()["provider"] == "cloudinary"


class TestUploadDocumentProxy:
    def test_signed_out_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_denied(app_module, monkeypatch)
        r = _client(app_module).post("/api/storage/document")
        assert r.status_code == 401

    def test_plain_student_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="student-1")
        r = _client(app_module).post("/api/storage/document")
        assert r.status_code == 403

    def test_unapproved_teacher_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="teacher-1")
        fake_db.seed("teachers", "teacher-1", {"approved": False})
        r = _client(app_module).post("/api/storage/document")
        assert r.status_code == 403

    def test_missing_file_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="admin-1", admin=True)
        r = _client(app_module).post("/api/storage/document", data={}, content_type="multipart/form-data")
        assert r.status_code == 400

    def test_unsupported_content_type_is_rejected(self, fake_db, app_module, monkeypatch):
        import io
        require_user_as(app_module, monkeypatch, uid="admin-1", admin=True)
        bad = (io.BytesIO(b"hi"), "book.exe", "application/x-msdownload")
        r = _client(app_module).post("/api/storage/document", data={"file": bad}, content_type="multipart/form-data")
        assert r.status_code == 415

    def test_approved_teacher_can_upload(self, fake_db, app_module, monkeypatch):
        import io
        require_user_as(app_module, monkeypatch, uid="teacher-1")
        fake_db.seed("teachers", "teacher-1", {"approved": True})
        import cloudinary_storage
        monkeypatch.setattr(cloudinary_storage, "upload_document",
                             lambda raw, filename, content_type, uid, folder=None: {"path": "bmt/books/x.pdf", "provider": "gcs"})
        monkeypatch.setattr(cloudinary_storage, "signed_url", lambda path, **kw: f"https://signed/{path}")
        pdf = (io.BytesIO(b"%PDF-1.4 fake"), "book.pdf", "application/pdf")
        r = _client(app_module).post("/api/storage/document", data={"file": pdf}, content_type="multipart/form-data")
        assert r.status_code == 201
        body = r.get_json()
        assert body["url"] == "https://signed/bmt/books/x.pdf"


class TestDocumentAccessUrl:
    def test_signed_out_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_denied(app_module, monkeypatch)
        r = _client(app_module).post("/api/storage/document-url", json={"storagePath": "bmt/books/x.pdf"})
        assert r.status_code == 401

    def test_missing_path_is_rejected(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch)
        r = _client(app_module).post("/api/storage/document-url", json={})
        assert r.status_code == 400

    def test_admin_can_access_any_path(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="admin-1", admin=True)
        import cloudinary_storage
        monkeypatch.setattr(cloudinary_storage, "signed_url", lambda path, **kw: f"https://signed/{path}")
        r = _client(app_module).post("/api/storage/document-url", json={"storagePath": "bmt/books/anything.pdf"})
        assert r.status_code == 200
        assert r.get_json()["url"] == "https://signed/bmt/books/anything.pdf"

    def test_path_outside_books_prefix_is_denied_even_for_non_admin(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        r = _client(app_module).post("/api/storage/document-url", json={"storagePath": "bmt/private/secret.pdf"})
        assert r.status_code == 403

    def test_path_with_no_matching_book_record_is_denied(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        r = _client(app_module).post("/api/storage/document-url", json={"storagePath": "bmt/books/unknown.pdf"})
        assert r.status_code == 403

    def test_non_gcs_book_record_is_denied(self, fake_db, app_module, monkeypatch):
        """A path that happens to match a book's storagePath field but where
        the book isn't actually stored via GCS/Cloudinary must not grant
        access - it isn't the record this path is supposed to unlock."""
        require_user_as(app_module, monkeypatch, uid="user-1")
        fake_db.seed("books", "book1", {"storagePath": "bmt/books/x.pdf", "storageProvider": "cloudinary-image", "className": "7"})
        fake_db.seed("users", "user-1", {"class": "7"})
        r = _client(app_module).post("/api/storage/document-url", json={"storagePath": "bmt/books/x.pdf"})
        assert r.status_code == 403

    def test_student_in_matching_class_is_authorized(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        fake_db.seed("books", "book1", {"storagePath": "bmt/books/x.pdf", "storageProvider": "gcs", "className": "7"})
        fake_db.seed("users", "user-1", {"class": "7"})
        import cloudinary_storage
        monkeypatch.setattr(cloudinary_storage, "signed_url", lambda path, **kw: f"https://signed/{path}")
        r = _client(app_module).post("/api/storage/document-url", json={"storagePath": "bmt/books/x.pdf"})
        assert r.status_code == 200

    def test_student_in_different_class_is_denied(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        fake_db.seed("books", "book1", {"storagePath": "bmt/books/x.pdf", "storageProvider": "gcs", "className": "7"})
        fake_db.seed("users", "user-1", {"class": "8"})
        r = _client(app_module).post("/api/storage/document-url", json={"storagePath": "bmt/books/x.pdf"})
        assert r.status_code == 403

    def test_uploading_teacher_is_authorized_regardless_of_class(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="teacher-1")
        fake_db.seed("books", "book1", {"storagePath": "bmt/books/x.pdf", "storageProvider": "gcs",
                                         "className": "9", "teacherUid": "teacher-1"})
        fake_db.seed("users", "teacher-1", {"class": ""})
        import cloudinary_storage
        monkeypatch.setattr(cloudinary_storage, "signed_url", lambda path, **kw: f"https://signed/{path}")
        r = _client(app_module).post("/api/storage/document-url", json={"storagePath": "bmt/books/x.pdf"})
        assert r.status_code == 200

    def test_fileid_field_is_also_accepted(self, fake_db, app_module, monkeypatch):
        require_user_as(app_module, monkeypatch, uid="user-1")
        fake_db.seed("books", "book1", {"fileId": "bmt/books/x.pdf", "storageProvider": "gcs", "className": "7"})
        fake_db.seed("users", "user-1", {"class": "7"})
        import cloudinary_storage
        monkeypatch.setattr(cloudinary_storage, "signed_url", lambda path, **kw: f"https://signed/{path}")
        r = _client(app_module).post("/api/storage/document-url", json={"fileId": "bmt/books/x.pdf"})
        assert r.status_code == 200
