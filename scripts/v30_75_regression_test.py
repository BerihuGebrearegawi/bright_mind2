from pathlib import Path

root = Path(__file__).resolve().parents[1]
app = (root / 'app.py').read_text()
js = (root / 'static' / 'student.js').read_text()
manifest = (root / 'V30.75_RELEASE_MANIFEST.json').read_text()
checks = [
    ('"version": "30.86"' in app, 'runtime version 30.75'),
    ('data-ai-recommendation' in js and 'openAIFromProgress' in js, 'AI actions on recommendations'),
    ('data-ai-topic' in js, 'AI actions on weak topics'),
    ('aiTutorInput' in js and 'practice questions without answers' in js, 'personalized AI prompt'),
    ('"videos_secondary": "YouTube link/embed"' in manifest, 'YouTube secondary video storage'),
    ('books_secondary' in manifest and 'Google Drive link' in manifest, 'Google Drive secondary book storage'),
]
failed = 0
for ok, name in checks:
    print(('PASS: ' if ok else 'FAIL: ') + name)
    failed += not ok
raise SystemExit(1 if failed else 0)
