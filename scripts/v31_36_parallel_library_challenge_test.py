from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
route = (ROOT / "learning_challenge_routes.py").read_text()
rules = (ROOT / "firestore.rules").read_text()

# B must not duplicate Cloud-B Library routes or direct Library hierarchy collections.
assert '"/api/teacher/library/subchapters"' not in route
assert '"/api/library/structure/<book_id>/<chapter_id>"' not in route
assert 'libraryChapters' not in route
assert 'librarySubchapters' not in route
assert 'match /libraryChapters/{chapterId}' not in rules
assert 'match /librarySubchapters/{subchapterId}' not in rules
# B-owned authoritative systems remain present and browser-denied.
assert 'questionBank' in route and 'academicChallenges' in route
assert 'match /questionBank/{questionId}' in rules
assert 'match /academicChallenges/{challengeId}' in rules
print("V31.65 ownership-boundary regression test: PASS")
