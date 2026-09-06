from pathlib import Path

root = Path(__file__).resolve().parents[1]
scanner = (root / 'smart_quiz_scanner_routes.py').read_text()
app = (root / 'app.py').read_text()
req = (root / 'requirements.txt').read_text()
student = (root / 'static/student.js').read_text()
html = (root / 'templates/student.html').read_text()

checks = {
    'PyMuPDF dependency': 'PyMuPDF==' in req,
    'Scanner transaction': '@firestore.transactional' in scanner,
    'Transaction reads scan': 'current = tx.get(scan_ref)' in scanner,
    'Transaction uses current questions': 'tx_questions = current_data.get("questions")' in scanner,
    'Library routes registered': 'register_library_routes(app' in app,
    'Existing bookmark export': 'window.toggleBookmark = toggleBookmark' in student,
    'Auth export': 'window.auth = auth' in student,
    'Reader UI': 'id="readerModal"' in html,
    'Scanner route': '@app.post("/api/scanner/scan")' in scanner,
}
failed = [name for name, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(('PASS' if ok else 'FAIL') + ': ' + name)
if failed:
    raise SystemExit(1)
print('V31.71 release gate: PASS')
