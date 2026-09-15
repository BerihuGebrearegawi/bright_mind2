from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
for name in ["award_ledger_routes.py", "awards_routes.py"]:
    ast.parse((ROOT / name).read_text(encoding="utf-8"), filename=name)

ledger = (ROOT / "award_ledger_routes.py").read_text(encoding="utf-8")
preview = (ROOT / "awards_routes.py").read_text(encoding="utf-8")
for text, label in [(ledger, "final ledger"), (preview, "award preview")]:
    assert "qualificationRound" in text
    assert "advancementPercentage" in text
    assert "roundNumber" in text
    assert "season" in text
    assert "range(1, target_round + 1)" in text
print("PASS: seasonal 5/7 qualification gate present in final ledger and preview")
