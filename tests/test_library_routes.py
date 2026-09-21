"""Real behavioural tests for library_routes.py: the reader authorization
rule (admin / owning teacher / matching-grade student), the write-side
ownership check for chapter management, and the manual cascade-delete of
subchapters when a chapter is removed (Firestore does not do this for
subcollections automatically, so the route has to).
"""
import pytest

from library_routes import register_library_routes
from tests.conftest import make_app


def require_user_as(uid, admin=False):
    return lambda: (True, {"uid": uid, "admin": admin})


def require_admin_ok():
    return lambda: (True, {"uid": "admin1", "admin": True})


def require_admin_denied():
    return lambda: (False, ({"error": "Admin access required."}, 403))


def _client(fake_db, require_user, require_admin, firebase_configured=True):
    factory = (lambda: True) if firebase_configured else (lambda: None)
    app = make_app(register_library_routes, require_user, require_admin, factory)
    return app.test_client()


class TestListBooksIsAdminOnly:
    def test_non_admin_is_rejected(self, fake_db):
        client = _client(fake_db, require_user_as("u1"), require_admin_denied())
        r = client.get("/api/library/books")
        assert r.status_code == 403

    def test_unknown_collection_is_rejected(self, fake_db):
        client = _client(fake_db, require_user_as("admin1", admin=True), require_admin_ok())
        r = client.get("/api/library/books?collection=badcollection")
        assert r.status_code == 400

    def test_admin_can_list(self, fake_db):
        fake_db.seed("books", "b1", {"title": "B1", "createdAt": "2024-01-01"})
        client = _client(fake_db, require_user_as("admin1", admin=True), require_admin_ok())
        r = client.get("/api/library/books")
        assert r.status_code == 200


class TestReadAccessByGrade:
    def test_student_in_wrong_grade_is_rejected(self, fake_db):
        fake_db.seed("books", "b1", {"title": "B1", "className": "9"})
        fake_db.seed("users", "student1", {"class": "8"})
        client = _client(fake_db, require_user_as("student1"), require_admin_ok())
        r = client.get("/api/library/books/books/b1/chapters")
        assert r.status_code == 403

    def test_student_in_matching_grade_is_allowed(self, fake_db):
        fake_db.seed("books", "b1", {"title": "B1", "className": "9"})
        fake_db.seed("users", "student1", {"class": "9"})
        client = _client(fake_db, require_user_as("student1"), require_admin_ok())
        r = client.get("/api/library/books/books/b1/chapters")
        assert r.status_code == 200

    def test_book_with_no_grade_set_is_open_to_all(self, fake_db):
        fake_db.seed("books", "b1", {"title": "B1"})
        fake_db.seed("users", "student1", {"class": "9"})
        client = _client(fake_db, require_user_as("student1"), require_admin_ok())
        r = client.get("/api/library/books/books/b1/chapters")
        assert r.status_code == 200

    def test_unknown_book_collection_is_rejected(self, fake_db):
        client = _client(fake_db, require_user_as("u1"), require_admin_ok())
        r = client.get("/api/library/books/badcoll/b1/chapters")
        assert r.status_code == 400

    def test_missing_book_returns_404(self, fake_db):
        client = _client(fake_db, require_user_as("u1"), require_admin_ok())
        r = client.get("/api/library/books/books/nope/chapters")
        assert r.status_code == 404

    def test_approved_teacher_cannot_read_another_teachers_book(self, fake_db):
        """V31.108 audit note: /api/library's list now shows an approved
        teacher every class's books (see test_v31_108_audit_fixes.py), but
        _can_read_book still only allows admins and the owning teacher - so
        an approved teacher who is not the book's owner gets 403 opening a
        book from that same list. Documented here as current behavior, not
        (yet) changed, since whether teachers should be able to read each
        other's uploaded books is a product decision, not a bug fix."""
        fake_db.seed("books", "b1", {"title": "B1", "className": "9", "teacherUid": "owner-teacher"})
        fake_db.seed("teachers", "other-teacher", {"approved": True, "uid": "other-teacher"})
        fake_db.seed("users", "other-teacher", {"role": "teacher"})
        client = _client(fake_db, require_user_as("other-teacher"), require_admin_ok())
        r = client.get("/api/library/books/books/b1/chapters")
        assert r.status_code == 403


class TestChapterManagementOwnership:
    def test_non_owning_teacher_cannot_create_chapter(self, fake_db):
        fake_db.seed("books", "b1", {"title": "B1", "teacherUid": "t2"})
        client = _client(fake_db, require_user_as("t1"), require_admin_ok())
        r = client.post("/api/library/chapters", json={"collection": "books", "bookId": "b1", "title": "Ch1"})
        assert r.status_code == 403

    def test_owning_but_unapproved_teacher_cannot_manage(self, fake_db):
        fake_db.seed("books", "b1", {"title": "B1", "teacherUid": "t1"})
        fake_db.seed("teachers", "t1", {"approved": False})
        client = _client(fake_db, require_user_as("t1"), require_admin_ok())
        r = client.post("/api/library/chapters", json={"collection": "books", "bookId": "b1", "title": "Ch1"})
        assert r.status_code == 403

    def test_owning_approved_teacher_can_create_chapter(self, fake_db):
        fake_db.seed("books", "b1", {"title": "B1", "teacherUid": "t1"})
        fake_db.seed("teachers", "t1", {"approved": True})
        client = _client(fake_db, require_user_as("t1"), require_admin_ok())
        r = client.post("/api/library/chapters", json={"collection": "books", "bookId": "b1", "title": "Ch1", "order": 1})
        assert r.status_code == 201
        assert fake_db.dump("books")["b1"]["hasChapters"] is True

    def test_missing_title_is_rejected(self, fake_db):
        client = _client(fake_db, require_user_as("t1"), require_admin_ok())
        r = client.post("/api/library/chapters", json={"collection": "books", "bookId": "b1", "title": ""})
        assert r.status_code == 400


class TestChapterDeleteCascadesSubchapters:
    def test_deleting_a_chapter_removes_its_subchapters_too(self, fake_db):
        fake_db.seed("books", "b1", {"title": "B1", "teacherUid": "admin1"})
        client = _client(fake_db, require_user_as("admin1", admin=True), require_admin_ok())
        created = client.post("/api/library/chapters", json={"collection": "books", "bookId": "b1", "title": "Ch1"})
        chapter_id = created.get_json()["chapterId"]

        sub_collection_key = f"books/b1/chapters/{chapter_id}/subchapters"
        fake_db.seed(sub_collection_key, "sub1", {"title": "Sub1"})
        assert len(fake_db.dump(sub_collection_key)) == 1

        r = client.delete(f"/api/library/chapters/books/b1/{chapter_id}")
        assert r.status_code == 200
        assert len(fake_db.dump(sub_collection_key)) == 0
        assert chapter_id not in fake_db.dump("books/b1/chapters")
