from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def text(rel):
    return (ROOT / rel).read_text(encoding='utf-8', errors='ignore')

def must(rel, terms):
    s = text(rel)
    for term in terms:
        assert term in s, f'{rel}: missing {term}'

must('templates/student.html', [
    'id="digitalLibrarySection"',
    'id="librarySearchBtn"',
    '/api/library?',
])
must('templates/teacher.html', [
    'id="smartQuizScannerSection"',
    'id="teacherDigitalLibrarySection"',
    'smart_quiz_scanner.js',
    'dashboard_integrations.js',
])
must('templates/admin.html', [
    'id="smartQuizScannerSection"',
    'id="adminDigitalLibrarySection"',
    'id="telegramAdminSection"',
    'smart_quiz_scanner.js',
    'dashboard_integrations.js',
])
must('static/dashboard_integrations.js', [
    '/api/library?',
    '/api/admin/telegram/status',
    '/api/admin/telegram/set-webhook',
])
must('app.py', [
    '/api/admin/telegram/status',
    '/api/admin/telegram/set-webhook',
    '/api/library',
])
must('smart_quiz_scanner_routes.py', [
    '/api/scanner/scan',
    '/api/scanner/scan/<scan_id>',
    '/api/scanner/scan/<scan_id>/review',
    '/api/scanner/scan/<scan_id>/import',
])
print('V31.75 dashboard release test: PASS')
