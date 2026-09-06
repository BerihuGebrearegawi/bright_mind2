import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('scanner', ROOT / 'smart_quiz_scanner_routes.py')
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)

valid = scanner._normalize_question({
    'question': 'What is 2 + 2?',
    'options': ['3','4','5','6'],
    'correctAnswer': 'B',
    'pageNumber': 7,
})
assert valid and valid['options']['B'] == '4' and valid['correctAnswer'] == 'B'

missing_option = scanner._normalize_question({
    'question': 'Q', 'options': {'A':'1','B':'2','C':'','D':'4'}
})
assert missing_option is None

bad_answer = scanner._normalize_question({
    'question': 'Q', 'options': {'A':'1','B':'2','C':'3','D':'4'}, 'correctAnswer':'E'
})
assert bad_answer and bad_answer['correctAnswer'] == ''

assert all(x in scanner.LIBRARY_FIELDS for x in ('collection','bookId','chapterId','subchapterId','learningObjective','pageNumber','sourceRef','sourceScanId'))
assert scanner.MAX_QUESTIONS == 40
print('SMART_QUIZ_SCANNER_UNIT_PASS')
