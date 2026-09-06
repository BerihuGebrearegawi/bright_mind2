import ast, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
for p in ROOT.rglob("*.py"):
    ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
print("AST_OK")
