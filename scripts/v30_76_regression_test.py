from pathlib import Path
root=Path(__file__).resolve().parents[1]
app=(root/"admin_control_routes.py").read_text()
html=(root/"templates/admin.html").read_text()
js=(root/"static/admin.js").read_text()
manifest=(root/"V30.76_RELEASE_MANIFEST.json").read_text()
checks=[
("/api/admin/analytics/overview" in app,"admin analytics endpoint"),
("require_admin()" in app and "storageProviders" in app,"admin-only provider analytics"),
("anActive7d" in html and "anVideosWatched" in html,"analytics dashboard UI"),
("loadAdminAnalytics" in js and "adminRequest('/api/admin/analytics/overview')" in js,"analytics client integration"),
("V30.76" in manifest,"release manifest version"),
]
failed=0
for ok,name in checks:
 print(("PASS: " if ok else "FAIL: ")+name)
 failed += not ok
raise SystemExit(1 if failed else 0)
