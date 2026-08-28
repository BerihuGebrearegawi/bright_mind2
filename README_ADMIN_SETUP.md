
## V30.67 Admin Access Fix

The admin page intentionally uses Firebase custom claims. The included `setup_admin.py` and `SETUP_ADMIN_WINDOWS.bat` set `admin=true` and `role=admin` for the Firebase user without changing the password.

### Windows
1. Make sure the Firebase service-account JSON belongs to project `bright-mind-tutor-app`.
2. If `FIREBASE_SERVICE_ACCOUNT_JSON` is already configured, run `SETUP_ADMIN_WINDOWS.bat`. Otherwise the script asks for the JSON file path.
3. Sign out of the admin page, close the tab, reopen `/admin`, and sign in again.

Never put the service-account JSON into the ZIP, JavaScript, HTML, Git, or `render.yaml`.
