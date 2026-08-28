from pathlib import Path
root=Path(__file__).resolve().parents[1]
app=(root/'app.py').read_text()
student=(root/'static/student.js').read_text()
html=(root/'templates/student.html').read_text()
rules=(root/'firestore.rules').read_text()
sw=(root/'service-worker.js').read_text()
checks=[
('"version": "30.86"' in app,'runtime version 30.75'),
("@app.route('/service-worker.js')" in app,'root service worker route'),
("match /bookmarks/{userId}" in rules and 'isOwnProfile(userId)' in rules,'bookmark ownership rules'),
("let _bookmarks = {};" in student and 'toggleBookmark' in student,'bookmark client logic'),
('id="contentSearch"' in html and 'id="savedItemsContainer"' in html,'student search and saved UI'),
("video.provider==='youtube'" in student,'youtube-aware playback'),
("data-bookmark-id=" in student,'bookmark buttons'),
("const CACHE_NAME='bmt-shell-v30-86'" in sw and "'/student'" in sw,'PWA shell cache'),
]
failed=0
for ok,msg in checks:
 print(('PASS: ' if ok else 'FAIL: ')+msg)
 failed += not ok
raise SystemExit(1 if failed else 0)
