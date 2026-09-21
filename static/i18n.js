/* BMT language switcher — English / አማርኛ / ትግርኛ
 *
 * How it works (deliberately simple):
 *   - The HTML keeps its normal English text. No per-element markup is needed.
 *   - Below, DICT maps an English string to [Amharic, Tigrinya].
 *   - When the user picks a language, every text node / placeholder / aria-label /
 *     title whose text matches an English key is swapped. Picking English restores
 *     the original text. The choice is remembered in localStorage ("bmt_lang").
 *   - Text that JavaScript writes later (status messages, button labels) is
 *     translated automatically by a MutationObserver.
 *
 * To translate more pages/dashboards: add   <div data-lang-switcher></div>   where the
 * selector should appear, include this script, and add their strings with
 *   BMT_I18N.add({ 'English text': ['አማርኛ', 'ትግርኛ'] });
 * For strings with variables use {name}:  BMT_I18N.t('Signing in as {email}', {email: x})
 * Anything not in DICT simply stays in English.
 */
(function () {
  'use strict';

  var LANGS = [
    { code: 'en', label: 'English' },
    { code: 'am', label: 'አማርኛ' },
    { code: 'ti', label: 'ትግርኛ' }
  ];
  var STORE_KEY = 'bmt_lang';
  var IDX = { am: 0, ti: 1 };
  var ATTRS = ['placeholder', 'aria-label', 'title', 'alt'];
  var SKIP_TAGS = { SCRIPT: 1, STYLE: 1, NOSCRIPT: 1, TEXTAREA: 1 };

  var DICT = Object.create(null);
  var lang = 'en';
  var origText = new WeakMap();     // text node -> English source string
  var appliedText = new WeakMap();  // text node -> the string we last wrote into it
  var origTitle = null;
  var observer = null;

  /* ------------------------------------------------------------------ */
  /* Dictionary: English -> [አማርኛ, ትግርኛ]                               */
  /* ------------------------------------------------------------------ */
  var STRINGS = {
    /* ---- shared ---- */
    'Language': ['ቋንቋ', 'ቋንቋ'],

    /* ---- sign-in page: header, step 1 ---- */
    'Sign in · Bright Mind Tutor': ['ይግቡ · Bright Mind Tutor', 'እተዉ · Bright Mind Tutor'],
    'Sign in to keep learning where you left off': ['ትምህርትዎን ካቆሙበት ለመቀጠል ይግቡ', 'ትምህርትኩም ካብ ዘቋረጽኩምሉ ንምቕጻል እተዉ'],
    'Sign in or create an account': ['ይግቡ ወይም አካውንት ይክፈቱ', 'እተዉ ወይ ኣካውንት ክፈቱ'],
    'Choose one of these three ways to sign in:': ['ለመግባት ከሚከተሉት ሦስት መንገዶች አንዱን ይጠቀሙ፦', 'ንምእታው ካብዞም ሰለስተ መገድታት ሓደ ተጠቐሙ፦'],
    'Email': ['ኢሜይል', 'ኢሜይል'],
    '— Enter your email below, then press Continue.': ['— ኢሜይልዎን ከታች ያስገቡና «ቀጥል»ን ይጫኑ።', '— ኢሜይልኩም ኣብ ታሕቲ የእትዉን «ቀጽል» ጠውቑ።'],
    'Phone number & PIN': ['ስልክ ቁጥርና ፒን', 'ቁጽሪ ስልክን ፒንን'],
    '— Pick your account type below, then enter your phone number and 6–8 digit PIN on that page.': ['— ከታች የአካውንትዎን ዓይነት ይምረጡ፤ በዚያ ገጽ ስልክ ቁጥርዎንና ባለ 6 እስከ 8 አሃዝ ፒንዎን ያስገቡ።', '— ኣብ ታሕቲ ዓይነት ኣካውንትኩም ምረጹ፤ ኣብቲ ገጽ ቁጽሪ ስልክኹምን ካብ 6 ክሳብ 8 ዲጂት ዘለዎ ፒንኩምን የእትዉ።'],
    'Google': ['ጉግል', 'ጉግል'],
    '— Press Continue with Google.': ['— «በጉግል ቀጥል»ን ይጫኑ።', '— «ብጉግል ቀጽል» ጠውቑ።'],
    'Email address': ['ኢሜይል አድራሻ', 'ኣድራሻ ኢሜይል'],
    'Continue': ['ቀጥል', 'ቀጽል'],
    'or by phone number & PIN': ['ወይም በስልክ ቁጥርና በፒን', 'ወይ ብቁጽሪ ስልክን ብፒንን'],
    'Student': ['ተማሪ', 'ተመሃራይ'],
    'Teacher': ['መምህር', 'መምህር'],
    'Parent': ['ወላጅ', 'ወላዲ'],
    'or with Google': ['ወይም በጉግል', 'ወይ ብጉግል'],
    'Continue with Google': ['በጉግል ቀጥል', 'ብጉግል ቀጽል'],
    'New student? You can register with your email or with a phone number & PIN.': ['አዲስ ተማሪ ከሆኑ በኢሜይል ወይም በስልክ ቁጥርና በፒን መመዝገብ ይችላሉ።', 'ሓዲሽ ተመሃራይ እንተኾንኩም ብኢሜይል ወይ ብቁጽሪ ስልክን ፒንን ክትምዝገቡ ትኽእሉ።'],

    /* ---- step 2A: returning user ---- */
    '← Use a different email': ['← ሌላ ኢሜይል ይጠቀሙ', '← ካልእ ኢሜይል ተጠቐሙ'],
    'Welcome back': ['እንኳን ደህና መጡ', 'እንቋዕ ብደሓን ተመለስኩም'],
    'Signing in as {email}': ['እየገቡ ያሉት በ{email} ነው', 'ብ{email} ኢኹም እትእተዉ ዘለኹም'],
    "This account signs in with Google — there's no password to enter here.": ['ይህ አካውንት በጉግል ነው የሚገባው — እዚህ የሚያስገቡት የይለፍ ቃል የለም።', 'እዚ ኣካውንት ብጉግል እዩ ዝኣቱ — ኣብዚ ዘእትዉዎ ቃል ምስጢር የለን።'],
    'Password': ['የይለፍ ቃል', 'ቃል ምስጢር'],
    'Show password': ['የይለፍ ቃል አሳይ', 'ቃል ምስጢር ኣርእይ'],
    'Hide password': ['የይለፍ ቃል ደብቅ', 'ቃል ምስጢር ሕባእ'],
    'Sign in': ['ግባ', 'እቱ'],
    'Forgot your password?': ['የይለፍ ቃልዎን ረስተዋል?', 'ቃል ምስጢርኩም ረሲዕኩም?'],

    /* ---- step 2B: role ---- */
    "Who's signing up?": ['ማን ነው የሚመዘገበው?', 'መን እዩ ዝምዝገብ?'],
    'Choose the account that fits you — each dashboard is built around what that role needs.': ['የሚስማማዎትን የአካውንት ዓይነት ይምረጡ — እያንዳንዱ ዳሽቦርድ ለተጠቃሚው ፍላጎት የተዘጋጀ ነው።', 'ዝኸብረኩም ዓይነት ኣካውንት ምረጹ — ነፍሲ ወከፍ ዳሽቦርድ ንድሌት እቲ ተጠቃሚ ዝተዳለወ እዩ።'],
    'Courses, quizzes, exams and progress': ['ኮርሶች፣ ጥያቄዎች፣ ፈተናዎችና የትምህርት ሂደት', 'ኮርሳት፣ ሕቶታት፣ ፈተናታትን ምዕባለ ትምህርትን'],
    'Create lessons, exams and manage classes': ['ትምህርቶችንና ፈተናዎችን ያዘጋጁ፤ ክፍሎችን ያስተዳድሩ', 'ትምህርትታትን ፈተናታትን ኣዳልዉ፤ ክፍልታት ኣመሓድሩ'],
    "Follow your child's learning and progress": ['የልጅዎን ትምህርትና ውጤት ይከታተሉ', 'ትምህርቲን ውጽኢትን ውላድኩም ተኸታተሉ'],
    'or, for teachers & parents': ['ወይም ለመምህራንና ለወላጆች', 'ወይ ንመምህራንን ወለዲን'],

    /* ---- Google role step ---- */
    '← Use a different method': ['← ሌላ መንገድ ይጠቀሙ', '← ካልእ መገዲ ተጠቐሙ'],
    'Continue as…': ['እንደ ማን መቀጠል ይፈልጋሉ?', 'ከም መን ክትቅጽሉ ትደልዩ?'],
    'Continuing as {email}': ['በ{email} እየቀጠሉ ነው', 'ብ{email} ትቕጽሉ ኣለኹም'],

    /* ---- registration ---- */
    '← Choose a different role': ['← ሌላ የአካውንት ዓይነት ይምረጡ', '← ካልእ ዓይነት ኣካውንት ምረጹ'],
    'Create your account': ['አካውንትዎን ይፍጠሩ', 'ኣካውንትኩም ፍጠሩ'],
    'Create your student account': ['የተማሪ አካውንት ይፍጠሩ', 'ናይ ተመሃራይ ኣካውንት ፍጠሩ'],
    'Create your teacher account': ['የመምህር አካውንት ይፍጠሩ', 'ናይ መምህር ኣካውንት ፍጠሩ'],
    'Create your parent account': ['የወላጅ አካውንት ይፍጠሩ', 'ናይ ወላዲ ኣካውንት ፍጠሩ'],
    'Full name': ['ሙሉ ስም', 'ምሉእ ስም'],
    'Age': ['ዕድሜ', 'ዕድመ'],
    'Grade': ['ክፍል', 'ክፍሊ'],
    'Education level': ['የትምህርት ደረጃ', 'ደረጃ ትምህርቲ'],
    'Not set': ['ያልተሞላ', 'ዘይተመልአ'],
    'Pre-primary': ['ቅድመ አንደኛ ደረጃ', 'ቅድመ ቀዳማይ ደረጃ'],
    'Primary (1-8)': ['አንደኛ ደረጃ (1-8)', 'ቀዳማይ ደረጃ (1-8)'],
    'Secondary (9-10)': ['ሁለተኛ ደረጃ (9-10)', 'ካልኣይ ደረጃ (9-10)'],
    'Preparatory (11-12)': ['መሰናዶ (11-12)', 'መሰናዶ (11-12)'],
    'School name': ['የትምህርት ቤት ስም', 'ስም ቤት ትምህርቲ'],
    'e.g. Kokebe Tsibah School': ['ለምሳሌ፦ ኮከበ ጽባሕ ትምህርት ቤት', 'ንኣብነት፦ ኮከበ ጽባሕ ቤት ትምህርቲ'],
    'Region': ['ክልል', 'ክልል'],
    'Guardian name': ['የአሳዳጊ ስም', 'ስም ኣሳዳጊ'],
    'Guardian phone': ['የአሳዳጊ ስልክ', 'ስልኪ ኣሳዳጊ'],
    'Address (optional)': ['አድራሻ (አማራጭ)', 'ኣድራሻ (ምርጫ)'],
    'Your education level': ['የትምህርት ደረጃዎ', 'ደረጃ ትምህርትኩም'],
    'e.g. BSc in Mathematics': ['ለምሳሌ፦ ቢኤስሲ በሂሳብ', 'ንኣብነት፦ ቢኤስሲ ኣብ ሒሳብ'],
    'Institution': ['ተቋም', 'ትካል'],
    'Where you studied or currently teach': ['የተማሩበት ወይም አሁን የሚያስተምሩበት', 'ዝተማህርኩምሉ ወይ ሕጂ ዘመሃርኩምሉ'],
    'Years of experience': ['የሥራ ልምድ (በዓመት)', 'ናይ ስራሕ ተሞክሮ (ብዓመት)'],
    'Subject': ['የትምህርት ዓይነት', 'ዓይነት ትምህርቲ'],
    'Mathematics': ['ሂሳብ', 'ሒሳብ'],
    'Physics': ['ፊዚክስ', 'ፊዚክስ'],
    'Chemistry': ['ኬሚስትሪ', 'ኬሚስትሪ'],
    'Biology': ['ባዮሎጂ', 'ባዮሎጂ'],
    'English': ['እንግሊዝኛ', 'እንግሊዝኛ'],
    'Amharic': ['አማርኛ', 'ኣምሓርኛ'],
    'History': ['ታሪክ', 'ታሪኽ'],
    'Geography': ['ጂኦግራፊ', 'ጂኦግራፊ'],
    'Economics': ['ኢኮኖሚክስ', 'ኢኮኖሚክስ'],
    'Grade you teach': ['የሚያስተምሩት ክፍል', 'ክፍሊ ዘመሃርኩምዎ'],
    'You can teach more subjects and grades from your dashboard once approved.': ['ከተፈቀደልዎ በኋላ ተጨማሪ የትምህርት ዓይነቶችንና ክፍሎችን ከዳሽቦርድዎ ማከል ይችላሉ።', 'ምስ ጸደቐ ድሕሪኡ ተወሳኺ ዓይነት ትምህርቲን ክፍልታትን ካብ ዳሽቦርድኩም ክትውስኹ ትኽእሉ።'],
    "You'll link your child's account from your dashboard using their student code.": ['የልጅዎን አካውንት ከዳሽቦርድዎ በተማሪው ኮድ ያገናኛሉ።', 'ኣካውንት ውላድኩም ካብ ዳሽቦርድኩም ብኮድ ናይቲ ተመሃራይ ክትእስሩ ኢኹም።'],
    'Confirm password': ['የይለፍ ቃሉን ያረጋግጡ', 'ቃል ምስጢር ኣረጋግጹ'],
    'Create account': ['አካውንት ፍጠር', 'ኣካውንት ፍጠር'],

    /* ---- teacher pending ---- */
    'Application submitted': ['ማመልከቻዎ ተልኳል', 'ማመልከቲኹም ተላኢኹ'],
    "Your teacher account is created and your application is waiting for admin approval. You'll be able to sign in fully once approved.": ['የመምህር አካውንትዎ ተፈጥሯል፤ ማመልከቻዎ የአስተዳዳሪ ማጽደቂያን በመጠባበቅ ላይ ነው። ከጸደቀ በኋላ ሙሉ በሙሉ መግባት ይችላሉ።', 'ኣካውንት መምህርነትኩም ተፈጢሩ፤ ማመልከቲኹም ናይ ኣመሓዳሪ ምጽዳቕ ይጽበ ኣሎ። ምስ ጸደቐ ምሉእ ብምሉእ ክትእትዉ ትኽእሉ።'],
    'Back to home': ['ወደ መነሻ ገጽ ተመለስ', 'ናብ ቀዳማይ ገጽ ተመለስ'],

    /* ---- status / error messages (client + server) ---- */
    'Checking…': ['በመፈተሽ ላይ…', 'ኣብ ምፍታሽ…'],
    'Signing in…': ['በመግባት ላይ…', 'ኣብ ምእታው…'],
    'Opening Google…': ['ጉግልን በመክፈት ላይ…', 'ጉግል ኣብ ምኽፋት…'],
    'Creating account…': ['አካውንት በመፍጠር ላይ…', 'ኣካውንት ኣብ ምፍጣር…'],
    'Passwords do not match.': ['የይለፍ ቃሎቹ አይመሳሰሉም።', 'ቃላት ምስጢር ኣይሰማምዑን።'],
    'Account created — taking you to your dashboard…': ['አካውንት ተፈጥሯል — ወደ ዳሽቦርድዎ በመውሰድ ላይ…', 'ኣካውንት ተፈጢሩ — ናብ ዳሽቦርድኩም ይወስደኩም ኣሎ…'],
    'Could not submit your teacher application.': ['የመምህርነት ማመልከቻዎን ማስገባት አልተቻለም።', 'ማመልከቲ መምህርነትኩም ምእታው ኣይተኻእለን።'],
    'If that email has an account, a reset link is on its way.': ['ኢሜይሉ አካውንት ካለው፣ የይለፍ ቃል መቀየሪያ አገናኝ ተልኳል።', 'እቲ ኢሜይል ኣካውንት እንተሎዎ፣ ናይ ቃል ምስጢር ምቕያር ሊንክ ተላኢኹ ኣሎ።'],
    'Could not send the reset email right now. Please try again shortly.': ['አሁን የመቀየሪያ ኢሜይል መላክ አልተቻለም። እባክዎ ትንሽ ቆይተው እንደገና ይሞክሩ።', 'ኣብዚ እዋን ናይ ምቕያር ኢሜይል ምልኣኽ ኣይተኻእለን። በጃኹም ጸኒሕኩም ደጊምኩም ፈትኑ።'],
    'Incorrect password. Please try again.': ['የይለፍ ቃሉ ትክክል አይደለም። እባክዎ እንደገና ይሞክሩ።', 'ቃል ምስጢር ጌጋ እዩ። በጃኹም ደጊምኩም ፈትኑ።'],
    'Too many attempts. Please wait a moment and try again.': ['ብዙ ጊዜ ሞክረዋል። እባክዎ ትንሽ ቆይተው እንደገና ይሞክሩ።', 'ብዙሕ ግዜ ፈቲንኩም። በጃኹም ቁሩብ ጸኒሕኩም ደጊምኩም ፈትኑ።'],
    'That email already has an account — try signing in instead.': ['ይህ ኢሜይል አስቀድሞ አካውንት አለው — በምትኩ ለመግባት ይሞክሩ።', 'እዚ ኢሜይል ድሮ ኣካውንት ኣለዎ — ኣብ ክንዲኡ ክትኣትዉ ፈትኑ።'],
    'Please choose a password with at least 6 characters.': ['እባክዎ ቢያንስ 6 ቁምፊ ያለው የይለፍ ቃል ይምረጡ።', 'በጃኹም እንተወሓደ 6 ቁምፊ ዘለዎ ቃል ምስጢር ምረጹ።'],
    'Please enter a valid email address.': ['እባክዎ ትክክለኛ ኢሜይል አድራሻ ያስገቡ።', 'በጃኹም ቅኑዕ ኣድራሻ ኢሜይል የእትዉ።'],
    'Enter a valid email address.': ['እባክዎ ትክክለኛ ኢሜይል አድራሻ ያስገቡ።', 'በጃኹም ቅኑዕ ኣድራሻ ኢሜይል የእትዉ።'],
    'Network error. Please check your connection and try again.': ['የኔትወርክ ችግር አለ። እባክዎ ግንኙነትዎን ፈትሸው እንደገና ይሞክሩ።', 'ጸገም ኔትወርክ ኣሎ። በጃኹም ርክብኩም ኣረጋግጹ ደጊምኩም ፈትኑ።'],
    'That email already has a password account. Please go back and sign in with your password instead.': ['ይህ ኢሜይል የይለፍ ቃል ያለው አካውንት አለው። እባክዎ ተመልሰው በይለፍ ቃልዎ ይግቡ።', 'እዚ ኢሜይል ብቃል ምስጢር ዝኣቱ ኣካውንት ኣለዎ። በጃኹም ተመሊስኩም ብቃል ምስጢርኩም እተዉ።'],
    'Something went wrong. Please try again.': ['የሆነ ችግር ተፈጥሯል። እባክዎ እንደገና ይሞክሩ።', 'ገለ ጸገም ተፈጢሩ። በጃኹም ደጊምኩም ፈትኑ።'],
    'Sign-in is not configured on the server.': ['የመግቢያ አገልግሎቱ በአገልጋዩ ላይ አልተዘጋጀም።', 'ኣገልግሎት ምእታው ኣብ ሰርቨር ኣይተዳለወን።'],
    'Could not check that email right now. Please try again.': ['አሁን ኢሜይሉን መፈተሽ አልተቻለም። እባክዎ እንደገና ይሞክሩ።', 'ሕጂ ነቲ ኢሜይል ምፍታሽ ኣይተኻእለን። በጃኹም ደጊምኩም ፈትኑ።'],
    'Could not check that email right now.': ['አሁን ኢሜይሉን መፈተሽ አልተቻለም።', 'ሕጂ ነቲ ኢሜይል ምፍታሽ ኣይተኻእለን።'],

    /* ---- landing page (index.html) ---- */
    'Explore BMT': ['BMTን ይመልከቱ', 'BMT ርኣዩ'],
    'SMART • SIMPLE • CONNECTED': ['ብልህ • ቀላል • የተገናኘ', 'ብልሕ • ቀሊል • ዝተኣሳሰረ'],
    'Learn smarter with': ['ብልህ ትምህርት፦', 'ብልሕ ትምህርቲ፦'],
    'One platform for students, teachers and parents. Learn, teach, assess and track progress from one secure place.': ['ለተማሪዎች፣ ለመምህራንና ለወላጆች አንድ መድረክ። ከአንድ ደህንነቱ የተጠበቀ ቦታ ይማሩ፣ ያስተምሩ፣ ይገምግሙ እንዲሁም ሂደትን ይከታተሉ።', 'ንተመሃሮ፣ መምህራንን ወለዲን ሓደ መድረኽ። ካብ ሓደ ውሑስ ቦታ ተማሃሩ፣ ምህሩ፣ ገምግሙ፣ ምዕባለ ውን ተኸታተሉ።'],
    '🔐 Secure authentication': ['🔐 ደህንነቱ የተጠበቀ መግቢያ', '🔐 ውሑስ ምእታው'],
    '🤖 AI-ready': ['🤖 ለAI ዝግጁ', '🤖 ንAI ድሉው'],
    '📝 Online exams': ['📝 የመስመር ላይ ፈተናዎች', '📝 ናይ መስመር ፈተናታት'],
    'Learning Platform': ['የትምህርት መድረክ', 'መድረኽ ትምህርቲ'],
    'Courses': ['ኮርሶች', 'ኮርሳት'],
    'Personalized learning': ['ለእርስዎ የተስተካከለ ትምህርት', 'ንነፍሲ ወከፍ ዝተዳለወ ትምህርቲ'],
    'Exams': ['ፈተናዎች', 'ፈተናታት'],
    'Instant results': ['ፈጣን ውጤት', 'ቅጽበታዊ ውጽኢት'],
    'AI Tutor': ['AI አስተማሪ', 'AI መምህር'],
    '24/7 support': ['የ24/7 ድጋፍ', '24/7 ሓገዝ'],
    '⚡ Fast learning': ['⚡ ፈጣን ትምህርት', '⚡ ቅልጡፍ ትምህርቲ'],
    '🤖 AI-assisted': ['🤖 በAI የታገዘ', '🤖 ብAI ዝተሓገዘ'],
    '📝 Secure exams': ['📝 ደህንነቱ የተጠበቀ ፈተና', '📝 ውሑስ ፈተና'],
    '👨‍👩‍👧 Family progress': ['👨‍👩‍👧 የቤተሰብ ሂደት', '👨‍👩‍👧 ምዕባለ ስድራቤት'],
    'Student Dashboard': ['የተማሪ ዳሽቦርድ', 'ዳሽቦርድ ተመሃራይ'],
    'Courses, videos, quizzes, exams and progress.': ['ኮርሶች፣ ቪዲዮዎች፣ ጥያቄዎች፣ ፈተናዎችና የትምህርት ሂደት።', 'ኮርሳት፣ ቪዲዮታት፣ ሕቶታት፣ ፈተናታትን ምዕባለን።'],
    'Teacher Dashboard': ['የመምህር ዳሽቦርድ', 'ዳሽቦርድ መምህር'],
    'Create lessons, exams, courses and use AI support.': ['ትምህርቶችን፣ ፈተናዎችንና ኮርሶችን ያዘጋጁ፤ የAI ድጋፍ ይጠቀሙ።', 'ትምህርትታት፣ ፈተናታትን ኮርሳትን ኣዳልዉ፤ ሓገዝ AI ተጠቐሙ።'],
    'Parent Dashboard': ['የወላጅ ዳሽቦርድ', 'ዳሽቦርድ ወላዲ'],
    "Follow your child's learning and assessment progress.": ['የልጅዎን ትምህርትና የግምገማ ሂደት ይከታተሉ።', 'ምዕባለ ትምህርትን ግምገማን ውላድኩም ተኸታተሉ።'],
    '📍 Ethiopia': ['📍 ኢትዮጵያ', '📍 ኢትዮጵያ'],
    'Theme': ['ገጽታ', 'ገጽታ'],
    'Secure admin access': ['ደህንነቱ የተጠበቀ የአስተዳዳሪ መግቢያ', 'ውሑስ መእተዊ ኣመሓዳሪ'],

    /* ---- parent dashboard (parent.html) ---- */
    'A verified parent can pay for a linked student by secure Chapa checkout or manual transaction submission.': ['የተረጋገጠ ወላጅ ለተገናኘ ተማሪ በደህንነቱ በተጠበቀ ቻፓ ክፍያ ወይም በእጅ ግብይት ማስገባት መክፈል ይችላል።', 'ዝተረጋገፀ ወላዲ ንዝተኣሳሰረ ተመሃራይ ብውሑስ ናይ ቻፓ ክፍሊት ወይ ብኢድ ግብይት ብምእታው ክኸፍል ይኽእል።'],
    "Ask your child to share their private BMT Parent Link Code, then enter it here.": ['ልጅዎ የግል የBMT የወላጅ ማገናኛ ኮድ እንዲያካፍልዎት ይጠይቁ፣ ከዚያ እዚህ ያስገቡት።', 'ውላድኩም ውልቃዊ ናይ BMT ኮድ መላገፂ ወላዲ ንኸካፍለኩም ሕተትዎ፣ ድሕሪኡ ኣብዚ የእትውዎ።'],
    'Attendance': ['የመገኘት ሁኔታ', 'ኩነታት ምርካብ'],
    'Average': ['አማካይ', 'ማእከላይ'],
    'BMT payment accounts': ['የBMT ክፍያ አካውንቶች', 'ናይ BMT ክፍሊት ኣካውንትታት'],
    'Bright Mind Tutor — Parent Portal': ['ብራይት ማይንድ ትዩተር — የወላጅ ገጽ', 'ብራይት ማይንድ ትዩተር — ገፅ ወላዲ'],
    'CBE Account': ['የCBE አካውንት', 'ናይ CBE ኣካውንት'],
    'Call Bright Mind Tutor': ['ብራይት ማይንድ ትዩተርን ይደውሉ', 'ንብራይት ማይንድ ትዩተር ደውሉ'],
    'Child Development & Parenting Center': ['የልጅ እድገትና የወላጅነት ማዕከል', 'ማእከል ዕብየት ቆልዑን ወላድነትን'],
    "Child's Growth": ['የልጅ እድገት', 'ዕብየት ውላድ'],
    'Children': ['ልጆች', 'ቆልዑ'],
    'Close menu': ['ዝርዝር ዝጋ', 'ዝርዝር ዕፀዉ'],
    'Confirm PIN': ['ፒን ያረጋግጡ', 'ፒን ኣረጋግፁ'],
    'Connect Child': ['ልጅ ያገናኙ', 'ውላድ ኣተኣሳስሩ'],
    'Connect a child': ['ልጅ ያገናኙ', 'ውላድ ኣተኣሳስሩ'],
    'Connect • Support • Progress': ['ግንኙነት • ድጋፍ • እድገት', 'ርክብ • ደገፍ • ዕብየት'],
    'Create Parent Account': ['የወላጅ አካውንት ይፍጠሩ', 'ኣካውንት ወላዲ ፍጠሩ'],
    'Email Bright Mind Tutor': ['ብራይት ማይንድ ትዩተርን በኢሜይል ያግኙ', 'ንብራይት ማይንድ ትዩተር ብኢሜይል ርኸብዎም'],
    'Email or Phone Number': ['ኢሜይል ወይም ስልክ ቁጥር', 'ኢሜይል ወይ ቁፅሪ ስልኪ'],
    'Email uses your password; phone uses your 6–8 digit PIN.': ['ኢሜይል የይለፍ ቃልዎን ይጠቀማል፤ ስልክ ደግሞ ባለ 6–8 አሃዝ ፒንዎን ይጠቀማል።', 'ኢሜይል ቃል ምስጢርኩም ይጥቀም፤ ስልኪ ግና ካብ 6–8 ዲጂት ዘለዎ ፒንኩም ይጥቀም።'],
    'Enter Parent Link Code': ['የወላጅ ማገናኛ ኮድ ያስገቡ', 'ኮድ መላገፂ ወላዲ ኣእትዉ'],
    'Family & Teacher Messages': ['የቤተሰብና የመምህር መልዕክቶች', 'መልእኽቲ ስድራቤትን መምህርን'],
    'Forgot PIN? (phone accounts)': ['ፒን ረስተዋል? (የስልክ አካውንቶች)', 'ፒን ረሲዕኩም? (ናይ ስልኪ ኣካውንት)'],
    'Forgot Password?': ['የይለፍ ቃል ረስተዋል?', 'ቃል ምስጢር ረሲዕኩም?'],
    'LEARN • PRACTICE • GROW': ['ተማር • ተለማመድ • አድግ', 'ተማሃሩ • ተለማመዱ • ዓብዩ'],
    'Loading notifications…': ['ማሳወቂያዎች በመጫን ላይ…', 'ማሳወቒታት ኣብ ምጽዓን…'],
    'Logout': ['ውጣ', 'ውፃእ'],
    'Manual transaction ID': ['በእጅ የገባ የግብይት መለያ', 'ቁፅሪ ናይ ኢድ ግብይት'],
    'Mark all read': ['ሁሉንም እንደተነበበ ምልክት አድርግ', 'ኩሉ ከም ዝተነበበ ግለፁ'],
    'Messages': ['መልዕክቶች', 'መልእኽትታት'],
    'Monthly': ['በወር', 'ወርሓዊ'],
    'More sections': ['ተጨማሪ ክፍሎች', 'ተወሳኺ ክፍልታት'],
    "My Child's Development": ['የልጄ እድገት', 'ዕብየት ውላደይ'],
    'My Students': ['ተማሪዎቼ', 'ተመሃሮይ'],
    'New parent? Create an account': ['አዲስ ወላጅ ነዎት? አካውንት ይክፈቱ', 'ሓዲሽ ወላዲ ዲኹም? ኣካውንት ክፈቱ'],
    'Notifications': ['ማሳወቂያዎች', 'ማሳወቒታት'],
    'One simple box for email or phone.': ['ለኢሜይል ወይም ስልክ አንድ ቀላል ሳጥን።', 'ንኢሜይል ወይ ስልኪ ሓደ ቀሊል ሳፁን።'],
    'Only verified parent-child links are shown.': ['የተረጋገጡ የወላጅ-ልጅ ግንኙነቶች ብቻ ይታያሉ።', 'እተረጋገፀ ርክብ ወላዲ-ውላድ ጥራይ ይርአ።'],
    'Open menu': ['ዝርዝር ክፈት', 'ዝርዝር ክፈቱ'],
    'Overview': ['አጠቃላይ እይታ', 'ሓፈሻዊ ትርኢት'],
    'PIN (6–8 digits)': ['ፒን (6–8 አሃዝ)', 'ፒን (6–8 ዲጂት)'],
    'Parent Learning': ['የወላጅ ትምህርት', 'ትምህርቲ ወላዲ'],
    'Parent Portal': ['የወላጅ ገጽ', 'ገፅ ወላዲ'],
    'Parent dashboard sections': ['የወላጅ ዳሽቦርድ ክፍሎች', 'ክፍልታት ዳሽቦርድ ወላዲ'],
    'Parent learning companion': ['የወላጅ ትምህርት ጓደኛ', 'ብጻይ ትምህርቲ ወላዲ'],
    'Parent portal': ['የወላጅ ገጽ', 'ገፅ ወላዲ'],
    'Parent sign in': ['የወላጅ መግቢያ', 'መእተዊ ወላዲ'],
    'Password (at least 6 characters)': ['የይለፍ ቃል (ቢያንስ 6 ቁምፊ)', 'ቃል ምስጢር (እንተወሓደ 6 ቁምፊ)'],
    'Password or 6–8 digit PIN': ['የይለፍ ቃል ወይም ባለ 6–8 አሃዝ ፒን', 'ቃል ምስጢር ወይ ካብ 6–8 ዲጂት ዘለዎ ፒን'],
    'Pay Automatically': ['በራስ ሰር ይክፈሉ', 'ብቐጥታ ክፈሉ'],
    'Pay for a Student': ['ለተማሪ ይክፈሉ', 'ንተመሃራይ ክፈሉ'],
    'Payment': ['ክፍያ', 'ክፍሊት'],
    'Personal Growth': ['የግል እድገት', 'ውልቃዊ ዕብየት'],
    'Phone + PIN': ['ስልክ + ፒን', 'ስልኪ + ፒን'],
    'Phone number': ['ስልክ ቁጥር', 'ቁፅሪ ስልኪ'],
    'Private conversations are available only with your verified child and the teachers connected to that child.': ['የግል ውይይት ከተረጋገጠ ልጅዎና ከዚያ ልጅ ጋር ከተገናኙ መምህራን ጋር ብቻ ይገኛል።', 'ውልቃዊ ዝርርብ ምስቲ እተረጋገፀ ውላድኩምን ምስቶም ምስቲ ውላድ ዝተኣሳሰሩ መምህራንን ጥራይ ይርከብ።'],
    'Recent assessments': ['የቅርብ ጊዜ ግምገማዎች', 'ናይ ቀረባ ግዜ ግምገማታት'],
    'Refresh': ['አድስ', 'ኣሓድሱ'],
    'Securely follow attendance, assessments, topic mastery and progress from one simple parent portal.': ['መገኘትን፣ ግምገማዎችን፣ የርዕስ ብቃትንና እድገትን ከአንድ ቀላል የወላጅ ገጽ በደህንነት ይከታተሉ።', 'ኩነታት ምርካብ፣ ግምገማታት፣ ብቕዓት ኣርእስትን ዕብየትን ካብ ሓደ ቀሊል ገፅ ወላዲ ብውሑስ መገዲ ተኸታተሉ።'],
    'Select linked student': ['የተገናኘ ተማሪ ይምረጡ', 'እተኣሳሰረ ተመሃራይ ምረፁ'],
    'Send': ['ላክ', 'ስደዱ'],
    "Short, practical learning for supporting your child's growth at home.": ['ልጅዎን በቤት ውስጥ ለማገዝ አጭርና ተግባራዊ ትምህርት።', 'ኣብ ገዛ ንውላድኩም ንምሕጋዝ ሓፂርን ግብራውን ትምህርቲ።'],
    'Sign in': ['ግባ', 'እቱ'],
    "Stay close to your child's": ['ከልጅዎ ጎን ይሁኑ፦', 'ምስ ውላድኩም ጠቢቖም ኩኑ፦'],
    'Submit Manual Payment': ['በእጅ የገባ ክፍያ ላክ', 'ክፍሊት ብኢድ ኣቕርቡ'],
    'Taking you to sign in…': ['ወደ መግቢያ በመውሰድ ላይ…', 'ናብ መእተዊ ይወስደኩም ኣሎ…'],
    'Toggle theme': ['ገጽታ ቀይር', 'ገፅታ ቀይሩ'],
    'Topic mastery': ['የርዕስ ብቃት', 'ብቕዓት ኣርእስቲ'],
    'Welcome back': ['እንኳን ደህና መጡ', 'እንቋዕ ብደሓን ተመለስኩም'],
    'Write a private message…': ['የግል መልዕክት ይጻፉ…', 'ውልቃዊ መልእኽቲ ፅሓፉ…'],
    'Yearly': ['በዓመት', 'ዓመታዊ'],
    'learning journey.': ['የትምህርት ጉዞ።', 'ጉዕዞ ትምህርቲ።'],
    '← Back': ['← ተመለስ', '← ተመለሱ'],
    '← Home': ['← መነሻ', '← ገፅ መእተዊ'],
    /* ---- student dashboard (student.html) ---- */
    '(for regional Math/Aptitude competitions)': ['(ለክልል የሂሳብ/ችሎታ ውድድሮች)', '(ንክልላዊ ውድድር ሒሳብ/ብቕዓት)'],
    'AI Assistant': ['AI ረዳት', 'AI ሓጋዚ'],
    'AI Homework Coach': ['AI የቤት ስራ አሰልጣኝ', 'AI ኣሰልጣኒ ስራሕ ገዛ'],
    'AI Personal Study Plan': ['AI የግል የጥናት እቅድ', 'AI ውልቃዊ መደብ ትምህርቲ'],
    'AI Translation': ['AI ትርጉም', 'AI ትርጉም'],
    'AI Translator': ['AI ተርጓሚ', 'AI ተርጓሚ'],
    'AI Tutor': ['AI አስተማሪ', 'AI መምህር'],
    'Academic question': ['የትምህርት ጥያቄ', 'ሕቶ ትምህርቲ'],
    'Address': ['አድራሻ', 'ኣድራሻ'],
    'Age': ['ዕድሜ', 'ዕድመ'],
    'All categories': ['ሁሉም ምድቦች', 'ኩሎም ምድብታት'],
    'All grades': ['ሁሉም ክፍሎች', 'ኩሎም ክፍልታት'],
    'Amount:': ['መጠን፦', 'መጠን፦'],
    'Ask AI': ['AIን ጠይቅ', 'ንAI ሕተቱ'],
    'Ask BMT AI': ['BMT AIን ጠይቅ', 'ንBMT AI ሕተቱ'],
    'Ask a teacher, report a problem, or send feedback. Your conversation stays inside Bright Mind Tutor.': ['መምህር ይጠይቁ፣ ችግር ያሳውቁ ወይም አስተያየት ይላኩ። ውይይትዎ በብራይት ማይንድ ትዩተር ውስጥ ብቻ ይቀራል።', 'መምህር ሕተቱ፣ ፀገም ሓብሩ ወይ ርእይቶ ስደዱ። ዝርርብኩም ኣብ ውሽጢ ብራይት ማይንድ ትዩተር ጥራይ ይፀንሕ።'],
    'Ask your question...': ['ጥያቄዎን ይጠይቁ...', 'ሕቶኹም ሕተቱ...'],
    'Assignments': ['ስራዎች', 'ዕዮታት'],
    'Attendance': ['የመገኘት ሁኔታ', 'ኩነታት ምርካብ'],
    'Available Challenges': ['ያሉ ውድድሮች', 'ዘለው ውድድራት'],
    'Available now in your Coursework tab.': ['አሁን በኮርስወርክ ትር ውስጥ ይገኛል።', 'ሕጂ ኣብ ትር ኮርስወርክ ይርከብ።'],
    'Available now in your Exams tab.': ['አሁን በፈተናዎች ትር ውስጥ ይገኛል።', 'ሕጂ ኣብ ትር ፈተናታት ይርከብ።'],
    'Available now in your Library tab.': ['አሁን በቤተመፃህፍት ትር ውስጥ ይገኛል።', 'ሕጂ ኣብ ትር ቤተ-መፃሕፍቲ ይርከብ።'],
    'Available now in your Live tab.': ['አሁን በቀጥታ ስርጭት ትር ውስጥ ይገኛል።', 'ሕጂ ኣብ ትር ቀጥታ ስርጭት ይርከብ።'],
    'Available now in your Videos tab.': ['አሁን በቪዲዮዎች ትር ውስጥ ይገኛል።', 'ሕጂ ኣብ ትር ቪድዮታት ይርከብ።'],
    'Available now.': ['አሁን ይገኛል።', 'ሕጂ ይርከብ።'],
    'Awards & Certificates': ['ሽልማቶችና ሰርተፍኬቶች', 'ሽልማትን ምስክር ወረቐትን'],
    'BMT AI Learning Assistant': ['የBMT AI ትምህርት ረዳት', 'ናይ BMT AI ሓጋዚ ትምህርቲ'],
    'BMT Community Live Chat': ['የBMT ማህበረሰብ ቀጥታ ውይይት', 'ናይ ማሕበረሰብ BMT ቀጥታ ዝርርብ'],
    'BMT payment accounts': ['የBMT ክፍያ አካውንቶች', 'ናይ BMT ክፍሊት ኣካውንትታት'],
    'BMT • STUDENT SPACE': ['BMT • የተማሪ ቦታ', 'BMT • ቦታ ተመሃራይ'],
    'Bank Transfer': ['የባንክ ዝውውር', 'ምትሕልላፍ ባንክ'],
    'Bio': ['የግል መግለጫ', 'ውልቃዊ መግለፂ'],
    'Bio:': ['የግል መግለጫ፦', 'ውልቃዊ መግለፂ፦'],
    'Bookmark this spot': ['ይህን ቦታ ምልክት አድርግ', 'ነዚ ቦታ ግለፁ'],
    'Books': ['መጻሕፍት', 'መፃሕፍቲ'],
    'Bright Mind Tutor — Student Dashboard': ['ብራይት ማይንድ ትዩተር — የተማሪ ዳሽቦርድ', 'ብራይት ማይንድ ትዩተር — ዳሽቦርድ ተመሃራይ'],
    'Call Bright Mind Tutor': ['ብራይት ማይንድ ትዩተርን ይደውሉ', 'ንብራይት ማይንድ ትዩተር ደውሉ'],
    'Cancel': ['ሰርዝ', 'ስረዙ'],
    'Certificates': ['ሰርተፍኬቶች', 'ምስክር ወረቓቕቲ'],
    'Change Photo': ['ፎቶ ቀይር', 'ስእሊ ቀይሩ'],
    'Chapters': ['ምዕራፎች', 'ምዕራፋት'],
    'Choose a secure PIN': ['ደህንነቱ የተጠበቀ ፒን ይምረጡ', 'ውሑስ ፒን ምረፁ'],
    'Class': ['ክፍል', 'ክፍሊ'],
    'Class:': ['ክፍል፦', 'ክፍሊ፦'],
    'Clear': ['አጥፋ', 'ደምስሱ'],
    'Close': ['ዝጋ', 'ዕፀዉ'],
    'Close menu': ['ዝርዝር ዝጋ', 'ዝርዝር ዕፀዉ'],
    'Combined Assignment, Quiz and Exam averages.': ['የስራ፣ ጥያቄና ፈተና አማካይ ውጤት በጥምረት።', 'ማእከላይ ውፅኢት ዕዮ፣ ሕቶን ፈተናን ብሓባር።'],
    'Commercial Bank of Ethiopia': ['የኢትዮጵያ ንግድ ባንክ', 'ናይ ንግዲ ባንክ ኢትዮጵያ'],
    'Community Chat': ['የማህበረሰብ ውይይት', 'ዝርርብ ማሕበረሰብ'],
    'Competition': ['ውድድር', 'ውድድር'],
    'Complete Your Profile': ['መገለጫዎን ያሟሉ', 'መግለፂኹም ምልኡ'],
    'Confirm PIN': ['ፒን ያረጋግጡ', 'ፒን ኣረጋግፁ'],
    'Confirm Password': ['የይለፍ ቃሉን ያረጋግጡ', 'ቃል ምስጢር ኣረጋግፁ'],
    'Copy': ['ቅዳ', 'ቅድሑ'],
    'Course': ['ኮርስ', 'ኮርስ'],
    'Courses, practice, secure exams, AI support and your progress in one focused learning space.': ['ኮርሶች፣ ልምምድ፣ ደህንነቱ የተጠበቀ ፈተናዎች፣ የAI ድጋፍና እድገትዎ በአንድ የተሰበሰበ የመማሪያ ቦታ።', 'ኮርሳት፣ ልምምድ፣ ውሑስ ፈተናታት፣ ደገፍ AI ን ዕብየትኩም ኣብ ሓደ ዝተኣከበ ቦታ ትምህርቲ።'],
    'Courses, videos, quizzes, secure exams, community and AI support in one focused student experience.': ['ኮርሶች፣ ቪዲዮዎች፣ ጥያቄዎች፣ ደህንነቱ የተጠበቀ ፈተናዎች፣ ማህበረሰብና የAI ድጋፍ በአንድ የተማሪ ልምድ።', 'ኮርሳት፣ ቪድዮታት፣ ሕቶታት፣ ውሑስ ፈተናታት፣ ማሕበረሰብን ደገፍ AIን ኣብ ሓደ ተሞክሮ ተመሃራይ።'],
    'Coursework': ['የኮርስ ስራ', 'ስራሕ ኮርስ'],
    'Coursework sections': ['የኮርስ ስራ ክፍሎች', 'ክፍልታት ስራሕ ኮርስ'],
    'Create Plan': ['እቅድ ፍጠር', 'መደብ ፍጠሩ'],
    'Create Student Account': ['የተማሪ አካውንት ይፍጠሩ', 'ኣካውንት ተመሃራይ ፍጠሩ'],
    'Create a password': ['የይለፍ ቃል ይፍጠሩ', 'ቃል ምስጢር ፍጠሩ'],
    'Create one': ['አንዱን ይፍጠሩ', 'ሓደ ፍጠሩ'],
    'Days': ['ቀናት', 'መዓልትታት'],
    'Detect language': ['ቋንቋ ለይ', 'ቋንቋ ለይ'],
    'Digital Library': ['ዲጂታል ቤተመፃህፍት', 'ዲጂታል ቤተ-መፃሕፍቲ'],
    'Digital Reading / PDF': ['ዲጂታል ንባብ / PDF', 'ዲጂታል ንባብ / PDF'],
    'Distance Learning': ['የርቀት ትምህርት', 'ትምህርቲ ርሕቐት'],
    'Distance-specific assignment tracking is not built yet.': ['ለርቀት ትምህርት ልዩ የስራ ክትትል ገና አልተዘጋጀም።', 'ናይ ርሕቐት ትምህርቲ ፍሉይ ክትትል ዕዮ ገና ኣይተዳለወን።'],
    'Downloaded': ['የተውረደ', 'ዝወረደ'],
    'Downloaded Videos': ['የተውረዱ ቪዲዮዎች', 'ዝወረደ ቪድዮታት'],
    'Edit Profile': ['መገለጫ አርትዕ', 'መግለፂ ኣርትዑ'],
    'Education Level': ['የትምህርት ደረጃ', 'ደረጃ ትምህርቲ'],
    'Email or Phone Number': ['ኢሜይል ወይም ስልክ ቁጥር', 'ኢሜይል ወይ ቁፅሪ ስልኪ'],
    'Email:': ['ኢሜይል፦', 'ኢሜይል፦'],
    'Emoji': ['ስሜት ገላጭ', 'ስምዒት ገላፂ'],
    'Enable push': ['ማንቂያ አንቃ', 'መጠንቀቕታ ኣርክቡ'],
    'Enter transaction ID from TeleBirr / Bank': ['ከቴሌብር / ባንክ የግብይት መለያ ያስገቡ', 'ካብ ተለብር / ባንክ ቁፅሪ ግብይት ኣእትዉ'],
    'Enter your full name': ['ሙሉ ስምዎን ያስገቡ', 'ምሉእ ስምኩም ኣእትዉ'],
    'Enter your name': ['ስምዎን ያስገቡ', 'ስምኩም ኣእትዉ'],
    'Exam': ['ፈተና', 'ፈተና'],
    'Example: Solve 2x + 5 = 15 and explain every step.': ['ምሳሌ፦ 2x + 5 = 15ን ፍታ፣ እያንዳንዱን ደረጃ አብራራ።', 'ንኣብነት፦ 2x + 5 = 15 ፍትሑ፣ ነፍሲ ወከፍ ደረጃ ግለፁ።'],
    'Exams': ['ፈተናዎች', 'ፈተናታት'],
    'Exams sections': ['የፈተና ክፍሎች', 'ክፍልታት ፈተናታት'],
    'Explain simply': ['በቀላሉ አብራራ', 'ብቐሊሉ ግለፁ'],
    'Family & Teacher Messages': ['የቤተሰብና የመምህር መልዕክቶች', 'መልእኽቲ ስድራቤትን መምህርን'],
    'Fast & Secure Checkout': ['ፈጣንና ደህንነቱ የተጠበቀ ክፍያ', 'ቅልጡፍን ውሑስን ክፍሊት'],
    'Find Grade 6–12 exams by grade, subject, stream and year. Premium files unlock instantly after payment approval.': ['ከ6-12ኛ ክፍል ፈተናዎችን በክፍል፣ በትምህርት ዓይነት፣ በዘርፍና በዓመት ይፈልጉ። ፕሪሚየም ፋይሎች ክፍያ ከጸደቀ በኋላ ወዲያውኑ ይከፈታሉ።', 'ፈተናታት ካብ ክፍሊ 6–12 ብክፍሊ፣ ብዓይነት ትምህርቲ፣ ብዓይነትን ብዓመትን ድለዩ። ፕሪሚየም ፋይላት ክፍሊት ምስ ጸደቐ ብቕፅበት ይኽፈት።'],
    'Find Learning Content': ['የመማሪያ ይዘት ፈልግ', 'ትሕዝቶ ትምህርቲ ድለዩ'],
    'Find your books, videos, live classes, quizzes and support here.': ['መጻሕፍትዎን፣ ቪዲዮዎችዎን፣ ቀጥታ ትምህርቶችን፣ ጥያቄዎችንና ድጋፍ እዚህ ያግኙ።', 'መፃሕፍትኹም፣ ቪድዮታትኩም፣ ቀጥታ ትምህርትታት፣ ሕቶታትን ደገፍን ኣብዚ ርኸቡ።'],
    'Font style': ['የፎንት ዘይቤ', 'ኣገባብ ፎንት'],
    'Forgot PIN? (phone accounts)': ['ፒን ረስተዋል? (የስልክ አካውንቶች)', 'ፒን ረሲዕኩም? (ናይ ስልኪ ኣካውንት)'],
    'Forgot Password?': ['የይለፍ ቃል ረስተዋል?', 'ቃል ምስጢር ረሲዕኩም?'],
    'Full Name': ['ሙሉ ስም', 'ምሉእ ስም'],
    'General': ['አጠቃላይ', 'ሓፈሻዊ'],
    'Generate BMT Quiz': ['የBMT ጥያቄ ፍጠር', 'ሕቶ BMT ፍጠሩ'],
    'Give an example': ['ምሳሌ ስጥ', 'ኣብነት ሃቡ'],
    'Goal, e.g. prepare for Grade 8 exam': ['ግብ፣ ለምሳሌ ለ8ኛ ክፍል ፈተና መዘጋጀት', 'ሸቶ፣ ንኣብነት ንፈተና ክፍሊ 8 ምድላው'],
    'Grade 12 stream': ['የ12ኛ ክፍል ዘርፍ', 'ዓይነት ክፍሊ 12'],
    'Grade 3–12 textbooks, teacher guides, reference books, psychology and general development resources.': ['ከ3-12ኛ ክፍል መማሪያ መጻሕፍት፣ የመምህር መምሪያ፣ የማጣቀሻ መጻሕፍት፣ ስነ ልቦናና አጠቃላይ እድገት ግብዓቶች።', 'መፃሕፍቲ ትምህርቲ ካብ ክፍሊ 3–12፣ መምርሒ መምህራን፣ መጣቐሲ መፃሕፍቲ፣ ስነ-ኣእምሮን ናይ ሓፈሻዊ ዕብየት ትሕዝቶን።'],
    'Growth': ['እድገት', 'ዕብየት'],
    'Growth sections': ['የእድገት ክፍሎች', 'ክፍልታት ዕብየት'],
    'Guardian/Parent Name': ['የአሳዳጊ/ወላጅ ስም', 'ስም ኣሳዳጊ/ወላዲ'],
    'Guardian/Parent Phone': ['የአሳዳጊ/ወላጅ ስልክ', 'ስልኪ ኣሳዳጊ/ወላዲ'],
    'Hello! Ask me a mathematics, science, or study question.': ['ሰላም! የሂሳብ፣ የሳይንስ ወይም የጥናት ጥያቄ ጠይቁኝ።', 'ሰላም! ሕቶ ሒሳብ፣ ሳይንስ ወይ ትምህርቲ ሕተቱኒ።'],
    'Highlight green': ['በአረንጓዴ አድምቅ', 'ብቀጠልያ ኣብርሁ'],
    'Highlight pink': ['በሮዝ አድምቅ', 'ብሮዝ ኣብርሁ'],
    'Highlight yellow': ['በቢጫ አድምቅ', 'ብብጫ ኣብርሁ'],
    'History': ['ታሪክ', 'ታሪኽ'],
    'Home': ['መነሻ', 'ገፅ መእተዊ'],
    'Homework Coach': ['የቤት ስራ አሰልጣኝ', 'ኣሰልጣኒ ስራሕ ገዛ'],
    'Homework and classwork published by your teachers.': ['በመምህራንዎ የተለቀቁ የቤት ስራና የክፍል ስራ።', 'ብመምህራንኩም ዝወፀ ስራሕ ገዛን ስራሕ ክፍልን።'],
    'How can we improve Bright Mind Tutor?': ['ብራይት ማይንድ ትዩተርን እንዴት ማሻሻል እንችላለን?', 'ብራይት ማይንድ ትዩተር ብኸመይ ከነመሓይሽ ንኽእል?'],
    'Improve': ['አሻሽል', 'ኣመሓይሹ'],
    'LIVE ROOM': ['ቀጥታ ክፍል', 'ክፍሊ ቀጥታ ስርጭት'],
    'Larger text': ['ትልቅ ፅሁፍ', 'ዓቢ ፅሑፍ'],
    'Leaderboard': ['የደረጃ ሰሌዳ', 'ሰሌዳ ደረጃ'],
    'Learn': ['ተማር', 'ተማሃሩ'],
    'Learn at your': ['ተማር በራስዎ', 'ብናትኩም ተማሃሩ'],
    'Learn • Practice • Grow': ['ተማር • ተለማመድ • አድግ', 'ተማሃሩ • ተለማመዱ • ዓብዩ'],
    'Lessons & Recorded Videos': ['ትምህርቶችና የተቀረጹ ቪዲዮዎች', 'ትምህርትታትን ዝተቐረፀ ቪድዮታትን'],
    'Library': ['ቤተመፃህፍት', 'ቤተ-መፃሕፍቲ'],
    'Library sections': ['የቤተመፃህፍት ክፍሎች', 'ክፍልታት ቤተ-መፃሕፍቲ'],
    'Live': ['ቀጥታ', 'ቀጥታ ስርጭት'],
    'Live Classes': ['ቀጥታ ትምህርቶች', 'ቀጥታ ትምህርትታት'],
    'Live Stream': ['ቀጥታ ስርጭት', 'ቀጥታ ስርጭት'],
    'Live class': ['ቀጥታ ትምህርት', 'ቀጥታ ትምህርቲ'],
    'Loading archive...': ['መዝገብ በመጫን ላይ...', 'መዝገብ ኣብ ምጽዓን...'],
    'Loading assignments…': ['ስራዎች በመጫን ላይ…', 'ዕዮታት ኣብ ምጽዓን…'],
    'Loading live classes...': ['ቀጥታ ትምህርቶች በመጫን ላይ...', 'ቀጥታ ትምህርትታት ኣብ ምጽዓን...'],
    'Loading notifications…': ['ማሳወቂያዎች በመጫን ላይ…', 'ማሳወቒታት ኣብ ምጽዓን…'],
    'Loading your learning analytics…': ['የትምህርት ትንተናዎ በመጫን ላይ…', 'ትንተና ትምህርትኹም ኣብ ምጽዓን…'],
    'Loading...': ['በመጫን ላይ...', 'ኣብ ምጽዓን...'],
    'Loading…': ['በመጫን ላይ…', 'ኣብ ምጽዓን…'],
    'Logout': ['ውጣ', 'ውፃእ'],
    'Looking up…': ['በመፈለግ ላይ…', 'ኣብ ምድላይ…'],
    'Manual payment fallback': ['አማራጭ የእጅ ክፍያ', 'ኣማራፂ ክፍሊት ብኢድ'],
    'Mark List': ['የውጤት ዝርዝር', 'ዝርዝር ውፅኢት'],
    'Mark all read': ['ሁሉንም እንደተነበበ ምልክት አድርግ', 'ኩሉ ከም ዝተነበበ ግለፁ'],
    'Mathematics & Aptitude Competition Center': ['የሂሳብና ችሎታ ውድድር ማዕከል', 'ማእከል ውድድር ሒሳብን ብቕዓትን'],
    'Media': ['ሚዲያ', 'ሚድያ'],
    'Media Preview': ['የሚዲያ ቅድመ እይታ', 'ቅድመ ትርኢት ሚድያ'],
    'Messages': ['መልዕክቶች', 'መልእኽትታት'],
    'Monthly': ['በወር', 'ወርሓዊ'],
    'More sections': ['ተጨማሪ ክፍሎች', 'ተወሳኺ ክፍልታት'],
    'My Assignments': ['ስራዎቼ', 'ዕዮታተይ'],
    'My Books': ['መጻሕፍቴ', 'መፃሕፍተይ'],
    'My Courses': ['ኮርሶቼ', 'ኮርሳተይ'],
    'My Development': ['እድገቴ', 'ዕብየተይ'],
    'My Exam History': ['የፈተና ታሪኬ', 'ታሪኽ ፈተናይ'],
    'My Learning Progress': ['የትምህርት እድገቴ', 'ዕብየት ትምህርተይ'],
    'My Mark List': ['የውጤት ዝርዝሬ', 'ዝርዝር ውፅኢተይ'],
    'My Playlist': ['የእኔ ተከታታይ ዝርዝር', 'ዝርዝር ፕለይሊስተይ'],
    'My Profile': ['መገለጫዬ', 'መግለፂየይ'],
    'My Region': ['ክልሌ', 'ክልለይ'],
    'My Results': ['ውጤቶቼ', 'ውፅኢተይ'],
    'My Saved': ['የተቀመጡልኝ', 'ዝኸዘንክዎ'],
    'Name:': ['ስም፦', 'ስም፦'],
    'National Archive': ['ብሔራዊ መዝገብ', 'ሃገራዊ መዝገብ'],
    'National Exam Archive': ['ብሔራዊ የፈተና መዝገብ', 'ሃገራዊ መዝገብ ፈተና'],
    'Natural Sciences': ['የተፈጥሮ ሳይንስ', 'ተፈጥሮኣዊ ስነ-ፍልጠት'],
    'New Code': ['አዲስ ኮድ', 'ሓዲሽ ኮድ'],
    'Next →': ['ቀጣይ →', 'ቀፃሊ →'],
    'No account?': ['አካውንት የለዎትም?', 'ኣካውንት የብልኩምን?'],
    'No downloaded videos yet.': ['እስካሁን የተውረደ ቪዲዮ የለም።', 'ክሳብ ሕጂ ዝወረደ ቪድዮ የለን።'],
    'No results loaded yet.': ['እስካሁን ውጤት አልተጫነም።', 'ክሳብ ሕጂ ውፅኢት ኣይተጻዐነን።'],
    'No videos in playlist yet.': ['እስካሁን በተከታታይ ዝርዝር ውስጥ ቪዲዮ የለም።', 'ክሳብ ሕጂ ኣብ ፕለይሊስት ቪድዮ የለን።'],
    'Not set': ['ያልተሞላ', 'ዘይተመልአ'],
    'Notification settings': ['የማሳወቂያ ቅንብሮች', 'ቅንብር ማሳወቒታት'],
    'Notifications': ['ማሳወቂያዎች', 'ማሳወቒታት'],
    'Open AI Tutor': ['AI አስተማሪ ክፈት', 'AI መምህር ክፈቱ'],
    'Open Coursework': ['የኮርስ ስራ ክፈት', 'ስራሕ ኮርስ ክፈቱ'],
    'Open Exams': ['ፈተናዎች ክፈት', 'ፈተናታት ክፈቱ'],
    'Open Library': ['ቤተመፃህፍት ክፈት', 'ቤተ-መፃሕፍቲ ክፈቱ'],
    'Open Live': ['ቀጥታ ክፈት', 'ቀጥታ ስርጭት ክፈቱ'],
    'Open Videos': ['ቪዲዮዎች ክፈት', 'ቪድዮታት ክፈቱ'],
    'Open menu': ['ዝርዝር ክፈት', 'ዝርዝር ክፈቱ'],
    'Optional note about the photo': ['ስለ ፎቶው አማራጭ ማስታወሻ', 'ኣማራፂ ሓበሬታ ብዛዕባ እቲ ስእሊ'],
    'PIN (6–8 digits)': ['ፒን (6–8 አሃዝ)', 'ፒን (6–8 ዲጂት)'],
    'Parent Connection': ['የወላጅ ግንኙነት', 'ርክብ ወላዲ'],
    'Password (6+ chars)': ['የይለፍ ቃል (6+ ቁምፊ)', 'ቃል ምስጢር (6+ ቁምፊ)'],
    'Password / PIN': ['የይለፍ ቃል / ፒን', 'ቃል ምስጢር / ፒን'],
    'Password or 6–8 digit PIN': ['የይለፍ ቃል ወይም ባለ 6–8 አሃዝ ፒን', 'ቃል ምስጢር ወይ ካብ 6–8 ዲጂት ዘለዎ ፒን'],
    'Paste a homework problem. BMT will explain it step-by-step and give one similar practice question.': ['የቤት ስራ ጥያቄ ይለጥፉ። BMT ደረጃ በደረጃ ያብራራል፣ ተመሳሳይ የልምምድ ጥያቄም ይሰጣል።', 'ሕቶ ስራሕ ገዛ ለጥፉ። BMT ደረጃ ብደረጃ ክገልፆ እዩ፣ ተመሳሳሊ ሕቶ ልምምድ ውን ክህብ እዩ።'],
    'Paste or select text from your library reading here…': ['ከቤተመፃህፍት ንባብዎ ፅሁፍ እዚህ ይለጥፉ ወይም ይምረጡ…', 'ካብ ንባብ ቤተ-መፃሕፍትኹም ፅሑፍ ኣብዚ ለጥፉ ወይ ምረፁ…'],
    'Pay Securely with Chapa': ['በቻፓ በደህንነት ይክፈሉ', 'ብቻፓ ብውሑስ መገዲ ክፈሉ'],
    'Pay securely through Chapa. Your payment is verified by the BMT server before premium access is unlocked.': ['በቻፓ በኩል በደህንነት ይክፈሉ። ፕሪሚየም ተደራሽነት ከመክፈቱ በፊት ክፍያዎ በBMT ሰርቨር ይረጋገጣል።', 'ብቻፓ ብውሑስ መገዲ ክፈሉ። ቅድሚ ፕሪሚየም ተኸፊቱ ክፍሊትኩም ብሰርቨር BMT ይረጋገፅ።'],
    'Payment': ['ክፍያ', 'ክፍሊት'],
    'Payment History': ['የክፍያ ታሪክ', 'ታሪኽ ክፍሊት'],
    'Payment Information': ['የክፍያ መረጃ', 'ሓበሬታ ክፍሊት'],
    'Payment sections': ['የክፍያ ክፍሎች', 'ክፍልታት ክፍሊት'],
    'Personal Growth': ['የግል እድገት', 'ውልቃዊ ዕብየት'],
    'Phone + PIN': ['ስልክ + ፒን', 'ስልኪ + ፒን'],
    'Phone Number': ['ስልክ ቁጥር', 'ቁፅሪ ስልኪ'],
    'Practice': ['ልምምድ', 'ልምምድ'],
    'Practice & Quiz': ['ልምምድና ጥያቄ', 'ልምምድን ሕቶን'],
    'Private messages with your verified parent and teachers connected to your class.': ['ከተረጋገጠ ወላጅዎና ከክፍልዎ ጋር ከተገናኙ መምህራን ጋር የግል መልዕክት።', 'ውልቃዊ መልእኽቲ ምስቲ እተረጋገፀ ወላድኩምን ምስቶም ምስ ክፍልኹም ዝተኣሳሰሩ መምህራንን።'],
    'Profile': ['መገለጫ', 'መግለፂ'],
    'Profile Information': ['የመገለጫ መረጃ', 'ሓበሬታ መግለፂ'],
    'Profile Picture': ['የመገለጫ ፎቶ', 'ስእሊ መግለፂ'],
    'Profile Preview': ['የመገለጫ ቅድመ እይታ', 'ቅድመ ትርኢት መግለፂ'],
    'Psychology & General Development': ['ስነ ልቦናና አጠቃላይ እድገት', 'ስነ-ኣእምሮን ሓፈሻዊ ዕብየትን'],
    'Question Bank → Mathematics/Aptitude/Regional/Scholarship → Results & Ranking → Awards & Certificates.': ['የጥያቄ ባንክ → ሂሳብ/ችሎታ/ክልል/ስኮላርሺፕ → ውጤትና ደረጃ → ሽልማትና ሰርተፍኬት።', 'ባንክ ሕቶ → ሒሳብ/ብቕዓት/ዞባ/ስኮላርሺፕ → ውፅኢትን ደረጃን → ሽልማትን ምስክር ወረቐትን።'],
    'Quiz me': ['ጠይቀኝ', 'ሕተቱኒ'],
    'Quizzes': ['ጥያቄዎች', 'ሕቶታት'],
    'Quizzes: 0 completed': ['ጥያቄዎች፦ 0 ተጠናቅቋል', 'ሕቶታት፦ 0 ተዛዚሙ'],
    'Reader': ['አንባቢ', 'ኣንባቢ'],
    'Reading theme': ['የንባብ ገጽታ', 'ገፅታ ንባብ'],
    'Record up to 60 seconds': ['እስከ 60 ሰከንድ ይቅረጹ', 'ክሳብ 60 ካልኢት ቀርፁ'],
    'Record voice message': ['የድምጽ መልዕክት ይቅረጹ', 'መልእኽቲ ድምፂ ቀርፁ'],
    'Reference Books (መጣቐስቲ)': ['የማጣቀሻ መጻሕፍት (መጣቐስቲ)', 'መፃሕፍቲ መጣቐሲ'],
    'Refresh': ['አድስ', 'ኣሓድሱ'],
    'Refresh Code': ['ኮድ አድስ', 'ኮድ ኣሓድሱ'],
    'Refresh Results': ['ውጤት አድስ', 'ውፅኢት ኣሓድሱ'],
    'Register': ['ተመዝገብ', 'ተመዝገቡ'],
    'Remove Photo': ['ፎቶ አስወግድ', 'ስእሊ ኣወግዱ'],
    'Remove highlight': ['ማድመቅ አስወግድ', 'ምብራህ ኣወግዱ'],
    'Repeat PIN': ['ፒን ድገም', 'ፒን ደግሙ'],
    'Repeat password': ['የይለፍ ቃል ድገም', 'ቃል ምስጢር ደግሙ'],
    'Reset': ['አድስ', 'ዳግም ኣቕንዑ'],
    'Save': ['አስቀምጥ', 'ኣቐምጡ'],
    'Save Changes': ['ለውጦችን አስቀምጥ', 'ለውጢ ኣቐምጡ'],
    'Save Profile': ['መገለጫ አስቀምጥ', 'መግለፂ ኣቐምጡ'],
    'Save Region': ['ክልል አስቀምጥ', 'ክልል ኣቐምጡ'],
    'School Name': ['የትምህርት ቤት ስም', 'ስም ቤት ትምህርቲ'],
    'Search': ['ፈልግ', 'ድለዩ'],
    'Search books, videos, lessons…': ['መጻሕፍት፣ ቪዲዮዎችና ትምህርቶች ይፈልጉ…', 'መፃሕፍቲ፣ ቪድዮታትን ትምህርትታትን ድለዩ…'],
    'Search learning content': ['የመማሪያ ይዘት ፈልግ', 'ትሕዝቶ ትምህርቲ ድለዩ'],
    'Search library': ['ቤተመፃህፍት ፈልግ', 'ቤተ-መፃሕፍቲ ድለዩ'],
    'Search the books, videos and lessons currently available to your class.': ['ለክፍልዎ አሁን ያሉ መጻሕፍት፣ ቪዲዮዎችና ትምህርቶች ይፈልጉ።', 'ሕጂ ንክፍልኹም ዘለው መፃሕፍቲ፣ ቪድዮታትን ትምህርትታትን ድለዩ።'],
    'Select': ['ምረጥ', 'ምረፁ'],
    'Select Your Class': ['ክፍልዎን ይምረጡ', 'ክፍልኹም ምረፁ'],
    'Select any word for a dictionary lookup, or select a sentence to highlight it.': ['የቃላት ማብራሪያ ለማየት ማንኛውንም ቃል ይምረጡ፣ ወይም ለማድመቅ ዓረፍተ ነገር ይምረጡ።', 'ንመፍትሕ ቃል ንዝኾነ ቃል ምረፁ፣ ወይ ንምብራህ ሓደ ሓሳብ ምረፁ።'],
    'Selected Plan:': ['የተመረጠ እቅድ፦', 'ዝተመረፀ መደብ፦'],
    'Send': ['ላክ', 'ስደዱ'],
    'Send Feedback': ['አስተያየት ላክ', 'ርእይቶ ስደዱ'],
    'Send Request': ['ጥያቄ ላክ', 'ሕቶ ስደዱ'],
    'Share this private code with your parent so they can follow your learning journey.': ['የትምህርት ጉዞዎን እንዲከታተሉ ይህን የግል ኮድ ለወላጅዎ ያካፍሉ።', 'ወላድኩም ጉዕዞ ትምህርትኹም ንኽከታተሉ ነዚ ውልቃዊ ኮድ ኣካፍልዎም።'],
    'Sign In': ['ግባ', 'እቱ'],
    'Smaller text': ['ትንሽ ፅሁፍ', 'ንእሽቶ ፅሑፍ'],
    'Social Sciences': ['ማህበራዊ ሳይንስ', 'ማሕበራዊ ስነ-ፍልጠት'],
    'Solve Photo': ['ፎቶ ፍታ', 'ስእሊ ፍትሑ'],
    'Solve Step-by-Step': ['ደረጃ በደረጃ ፍታ', 'ደረጃ ብደረጃ ፍትሑ'],
    'Student Portal': ['የተማሪ ገጽ', 'ገፅ ተመሃራይ'],
    'Student Textbooks': ['የተማሪ መማሪያ መጻሕፍት', 'መፃሕፍቲ ትምህርቲ ተመሃራይ'],
    'Student dashboard sections': ['የተማሪ ዳሽቦርድ ክፍሎች', 'ክፍልታት ዳሽቦርድ ተመሃራይ'],
    'Student learning platform': ['የተማሪ የመማሪያ መድረክ', 'መድረኽ ትምህርቲ ተመሃራይ'],
    'Student space': ['የተማሪ ቦታ', 'ቦታ ተመሃራይ'],
    'Students, teachers and admins • live conversation • replies • reactions • photos • audio • video': ['ተማሪዎች፣ መምህራንና አስተዳዳሪዎች • ቀጥታ ውይይት • ምላሽ • ስሜት • ፎቶ • ድምጽ • ቪዲዮ', 'ተመሃሮ፣ መምህራንን ኣመሓደርትን • ቀጥታ ዝርርብ • ምላሽ • ስምዒት • ስእሊ • ድምፂ • ቪድዮ'],
    'Study Materials': ['የጥናት ግብዓቶች', 'ትሕዝቶ ትምህርቲ'],
    'Study Plan': ['የጥናት እቅድ', 'መደብ ትምህርቲ'],
    'Subject': ['የትምህርት ዓይነት', 'ዓይነት ትምህርቲ'],
    'Subject / topic': ['የትምህርት ዓይነት / ርዕስ', 'ዓይነት ትምህርቲ / ኣርእስቲ'],
    'Submit': ['አስገባ', 'ኣቕርቡ'],
    'Submit Manual Payment': ['በእጅ የገባ ክፍያ ላክ', 'ክፍሊት ብኢድ ኣቕርቡ'],
    'Subscribe': ['ይመዝገቡ', 'ተመዝገቡ'],
    'Subscribe to Premium': ['ለፕሪሚየም ይመዝገቡ', 'ንፕሪሚየም ተመዝገቡ'],
    'Support': ['ድጋፍ', 'ደገፍ'],
    'Support & Feedback': ['ድጋፍና አስተያየት', 'ደገፍን ርእይቶን'],
    'Support & Teacher Q&A': ['ድጋፍና የመምህር ጥያቄ መልስ', 'ደገፍን ሕቶ-መልሲ መምህርን'],
    'Taking you to sign in…': ['ወደ መግቢያ በመውሰድ ላይ…', 'ናብ መእተዊ ይወስደኩም ኣሎ…'],
    'Teacher Guides': ['የመምህር መምሪያ', 'መምርሒ መምህራን'],
    'Teacher UID (optional)': ['የመምህር UID (አማራጭ)', 'UID መምህር (ምርጫ)'],
    'Technical problem': ['ቴክኒካዊ ችግር', 'ቴክኒካዊ ፀገም'],
    'TeleBirr Number': ['የቴሌብር ቁጥር', 'ቁፅሪ ተለብር'],
    'Tell us about yourself...': ['ስለ ራስዎ ይንገሩን...', 'ብዛዕባ ርእስኹም ንገሩና...'],
    'Text size': ['የፅሁፍ መጠን', 'መጠን ፅሑፍ'],
    'Toggle dark mode': ['ጨለማ ገጽታ ቀይር', 'ፀሊም ገፅታ ቀይሩ'],
    'Tools & Features': ['መሳሪያዎችና ገጽታዎች', 'መሳርሒታትን ባህርያትን'],
    'Transaction ID': ['የግብይት መለያ', 'ቁፅሪ ግብይት'],
    'Translate with BMT AI': ['በBMT AI ተርጉም', 'ብBMT AI ተርጉሙ'],
    'Upcoming classes for your grade.': ['ለክፍልዎ የሚመጡ ትምህርቶች።', 'ንክፍልኹም ዝመፅእ ትምህርትታት።'],
    'Upload homework photo': ['የቤት ስራ ፎቶ ስቀል', 'ስእሊ ስራሕ ገዛ ስቀሉ'],
    'Use the same box with either your email + password or phone + PIN.': ['ተመሳሳዩን ሳጥን በኢሜይል + የይለፍ ቃል ወይም በስልክ + ፒን ይጠቀሙ።', 'ተመሳሳሊ ሳፁን ብኢሜይል + ቃል ምስጢር ወይ ብስልኪ + ፒን ተጠቐሙ።'],
    'Videos': ['ቪዲዮዎች', 'ቪድዮታት'],
    'Videos sections': ['የቪዲዮ ክፍሎች', 'ክፍልታት ቪድዮታት'],
    'Videos: 0/0': ['ቪዲዮዎች፦ 0/0', 'ቪድዮታት፦ 0/0'],
    'Voice recordings: maximum 60 seconds per message • audio upload: maximum 5 MB': ['የድምጽ ቅጂ፦ ቢበዛ 60 ሰከንድ በመልዕክት • የድምጽ ስቀላ፦ ቢበዛ 5 ሜባ', 'ቅዳሕ ድምፂ፦ ብዝበዝሐ 60 ካልኢት ንመልእኽቲ • ስቐላ ድምፂ፦ ብዝበዝሐ 5 ሜባ'],
    'Weak topics, e.g. algebra, fractions': ['ደካማ ርዕሶች፣ ለምሳሌ አልጀብራ፣ ክፍልፋይ', 'ድኹማት ኣርእስቲ፣ ንኣብነት ኣልጀብራ፣ ክፋልፋይ'],
    'Welcome,': ['እንኳን ደህና መጡ፣', 'እንቋዕ ብደሓን መፃእኩም፣'],
    'Woreda / Kebele': ['ወረዳ / ቀበሌ', 'ወረዳ / ቀበሌ'],
    'Write a private message…': ['የግል መልዕክት ይጻፉ…', 'ውልቃዊ መልእኽቲ ፅሓፉ…'],
    'Write to students, teachers and admins...': ['ለተማሪዎች፣ መምህራንና አስተዳዳሪዎች ይጻፉ...', 'ንተመሃሮ፣ መምህራንን ኣመሓደርትን ፅሓፉ...'],
    'Write your question or problem...': ['ጥያቄዎን ወይም ችግርዎን ይጻፉ...', 'ሕቶኹም ወይ ፀገምኩም ፅሓፉ...'],
    'Year': ['ዓመት', 'ዓመት'],
    'Yearly': ['በዓመት', 'ዓመታዊ'],
    'Your Bright Mind Tutor — ask, understand, practise.': ['የእርስዎ ብራይት ማይንድ ትዩተር — ጠይቁ፣ ይረዱ፣ ይለማመዱ።', 'ናትኩም ብራይት ማይንድ ትዩተር — ሕተቱ፣ ተረድኡ፣ ተለማመዱ።'],
    'Your Name': ['ስምዎ', 'ስምኩም'],
    'Your free trial has ended or you want to access paid content. Choose a plan and make payment to continue.': ['የነፃ ሙከራዎ አብቅቷል ወይም ክፍያ የሚያስከፍል ይዘት ማየት ይፈልጋሉ። ለመቀጠል እቅድ ይምረጡና ይክፈሉ።', 'ናፃ ፈተነኹም ተወዲኡ ወይ ናይ ክፍሊት ትሕዝቶ ክትርእዩ ትደልዩ ኣለኹም። ንምቕፃል መደብ ምረፁን ክፈሉን።'],
    'Your full name': ['ሙሉ ስምዎ', 'ምሉእ ስምኩም'],
    'Your learning space,': ['የመማሪያ ቦታዎ፣', 'ቦታ ትምህርትኹም፣'],
    'Your recent AI conversation is saved on this device.': ['የቅርብ ጊዜ የAI ውይይትዎ በዚህ መሳሪያ ላይ ተቀምጧል።', 'ናይ ቀረባ ግዜ ዝርርብ AI ኹም ኣብዚ መሳርሒ ተዓቂቡ ኣሎ።'],
    'Zone / Sub-city': ['ዞን / ክፍለ ከተማ', 'ዞባ / ክፍለ-ከተማ'],
    'e.g. Bole, or East Hararghe': ['ለምሳሌ፦ ቦሌ ወይም ምስራቅ ሐረርጌ', 'ንኣብነት፦ ቦሌ ወይ ምብራቕ ሐረርጌ'],
    'e.g. Woreda 07, Kebele 03': ['ለምሳሌ፦ ወረዳ 07፣ ቀበሌ 03', 'ንኣብነት፦ ወረዳ 07፣ ቀበሌ 03'],
    'made brighter.': ['የበራ።', 'ብሩህ ዝገበረ።'],
    'own pace.': ['በራስዎ ፍጥነት።', 'ብናትኩም ፍጥነት።'],
    'per month': ['በወር', 'ኣብ ወርሒ'],
    'per year (save 33%)': ['በዓመት (33% ይቆጥቡ)', 'ኣብ ዓመት (33% ዓቅቡ)'],
    'word': ['ቃል', 'ቃል'],
    '← Home': ['← መነሻ', '← ገፅ መእተዊ'],
    '← Previous': ['← ቀዳሚ', '← ናይ ቀደም'],
    '✕ Back to courses': ['✕ ወደ ኮርሶች ተመለስ', '✕ ናብ ኮርሳት ተመለሱ'],
    '✕ Cancel': ['✕ ሰርዝ', '✕ ስረዙ'],
    '✕ Close': ['✕ ዝጋ', '✕ ዕፀዉ'],
    '⭐ Your Feedback': ['⭐ አስተያየትዎ', '⭐ ርእይቶኹም'],
    'Dark': ['ጨለማ', 'ፀሊም'],
    'Light': ['ብርሃን', 'ብሩህ'],
    'Sans-serif': ['ያለ ድምበር ፊደል', 'ዘይብድምበር ፊደል'],
    'Serif': ['ድምበር ያለው ፊደል', 'ዘለዎ ድምበር ፊደል'],
    'Sepia': ['ሴፒያ', 'ሴፒያ'],
    'KG': ['ኪንደርጋርተን', 'ኪንደርጋርተን'],
    'Tigrinya': ['ትግርኛ', 'ትግርኛ'],
    /* ---- teacher dashboard (teacher.html) ---- */
    '(optional — leave unselected to keep this material visible to everyone, as before)': ['(አማራጭ — ይህ ግብዓት እንደበፊቱ ለሁሉም እንዲታይ ካልመረጡት ይተውት)', '(ምርጫ — ከምቲ ቀደም ንኹሉ ክርአ እንተደሊኹም ዘይምረፅዎ)'],
    '(optional — leave unselected unless restricting this material)': ['(አማራጭ — ይህን ግብዓት መገደብ ካልፈለጉ ይተውት)', '(ምርጫ — ነዚ ትሕዝቶ ክትድርቱ ዘይደለኹም እንተኾይንኩም ዘይምረፅዎ)'],
    'AI Assist': ['AI ድጋፍ', 'ደገፍ AI'],
    'AI Copilot': ['AI ረዳት', 'AI ሓጋዚ'],
    'AI Materials': ['የAI ግብዓቶች', 'ትሕዝቶ AI'],
    'AI Teacher Copilot': ['የመምህር AI ረዳት', 'AI ሓጋዚ መምህር'],
    'AI Tutor Materials': ['የAI አስተማሪ ግብዓቶች', 'ትሕዝቶ AI መምህር'],
    'Access BMT textbooks, teacher guides, reference books and development resources from the same library used by students.': ['ተማሪዎች ከሚጠቀሙት ተመሳሳይ ቤተመፃህፍት የBMT መማሪያ መጻሕፍት፣ የመምህር መምሪያ፣ የማጣቀሻ መጻሕፍትና የእድገት ግብዓቶች ያግኙ።', 'ካብቲ ተመሃሮ ዝጥቀሙሉ ተመሳሳሊ ቤተ-መፃሕፍቲ፣ መፃሕፍቲ ትምህርቲ BMT፣ መምርሒ መምህራን፣ መፃሕፍቲ መጣቐሲን ትሕዝቶ ዕብየትን ርኸቡ።'],
    'Activities (one per line)': ['እንቅስቃሴዎች (በአንድ መስመር አንድ)', 'ንጥፈታት (ኣብ ነፍሲ ወከፍ መስመር ሓደ)'],
    'Add Lesson / Learning Material': ['ትምህርት / የመማሪያ ግብዓት ጨምር', 'ትምህርቲ / ትሕዝቶ ትምህርቲ ወስኹ'],
    'Add to AI Knowledge Base': ['ወደ AI እውቀት ቋት ጨምር', 'ናብ ቋት ፍልጠት AI ወስኹ'],
    'Advanced Exam Builder': ['የላቀ የፈተና መገንቢያ', 'ልዑል መስርሒ ፈተና'],
    'Answer assigned student questions and support requests.': ['የተመደቡ የተማሪ ጥያቄዎችንና የድጋፍ ጥያቄዎችን መልስ።', 'ንዝተመደበ ሕቶታት ተመሃሮን ሕቶ ደገፍን መልሱ።'],
    'Apply to Become a Teacher': ['መምህር ለመሆን ያመልክቱ', 'ንመምህርነት ኣመልክቱ'],
    'Approve & Import': ['አጽድቅና አስገባ', 'ኣፅድቑን ኣእትዉን'],
    'Approved': ['ጸድቋል', 'ፀዲቑ'],
    'Assign Selected to Class': ['የተመረጡትን ለክፍል መድብ', 'ዝተመረፀ ንክፍሊ መድቡ'],
    'Assign to class…': ['ለክፍል መድብ…', 'ንክፍሊ መድቡ…'],
    'Assignment / assessment notes': ['የስራ / የግምገማ ማስታወሻ', 'መዘኻኸሪ ዕዮ / ግምገማ'],
    'Assignment title': ['የስራ ርዕስ', 'ኣርእስቲ ዕዮ'],
    'Attendance Report': ['የመገኘት ሪፖርት', 'ጸብጻብ ኩነታት ምርካብ'],
    'BMT AI Assistant': ['የBMT AI ረዳት', 'ናይ BMT AI ሓጋዚ'],
    'BMT • TEACHER WORKSPACE': ['BMT • የመምህር የስራ ቦታ', 'BMT • ቦታ ስራሕ መምህር'],
    'Become a Bright Mind Tutor teacher': ['የብራይት ማይንድ ትዩተር መምህር ይሁኑ', 'መምህር ብራይት ማይንድ ትዩተር ኩኑ'],
    'Book ID (optional)': ['የመፅሐፍ መለያ (አማራጭ)', 'መለለዪ መፅሓፍ (ምርጫ)'],
    'Bright Mind Tutor — Teacher Portal': ['ብራይት ማይንድ ትዩተር — የመምህር ገጽ', 'ብራይት ማይንድ ትዩተር — ገፅ መምህር'],
    'Bright Mind.': ['ብራይት ማይንድ።', 'ብራይት ማይንድ።'],
    'Build better learning with': ['የተሻለ ትምህርት ይገንቡ በ', 'ዝሓሸ ትምህርቲ ስርሑ ብ'],
    'Certificates / professional qualifications': ['ሰርተፍኬቶች / የሙያ ብቃቶች', 'ምስክር ወረቐት / ሞያዊ ብቕዓት'],
    'Chapter ID (optional)': ['የምዕራፍ መለያ (አማራጭ)', 'መለለዪ ምዕራፍ (ምርጫ)'],
    'Checking account…': ['አካውንት በመፈተሽ ላይ…', 'ኣካውንት ኣብ ምፍታሽ…'],
    'Choose class': ['ክፍል ይምረጡ', 'ክፍሊ ምረፁ'],
    'Class Management': ['የክፍል አስተዳደር', 'ምሕደራ ክፍሊ'],
    'Class description': ['የክፍል መግለጫ', 'መግለፂ ክፍሊ'],
    'Class group / section (optional, e.g. 8A)': ['የክፍል ቡድን / ክፍለ ክፍል (አማራጭ፣ ለምሳሌ 8A)', 'ጉጅለ/ክፍለ-ክፍሊ (ምርጫ፣ ንኣብነት 8A)'],
    'Class title': ['የክፍል ርዕስ', 'ኣርእስቲ ክፍሊ'],
    'Classwork': ['የክፍል ስራ', 'ስራሕ ክፍሊ'],
    'College / University / Institution': ['ኮሌጅ / ዩኒቨርስቲ / ተቋም', 'ኮለጅ / ዩኒቨርስቲ / ትካል'],
    'Combined Assignment + Exam averages per student, for a grade you teach.': ['ለሚያስተምሩት ክፍል፣ በተማሪ የተቀናጀ የስራ + ፈተና አማካይ ውጤት።', 'ንዝምህርዎ ክፍሊ፣ ማእከላይ ውፅኢት ዕዮን ፈተናን ብሓባር ንነፍሲ ወከፍ ተመሃራይ።'],
    'Community': ['ማህበረሰብ', 'ማሕበረሰብ'],
    'Completed': ['ተጠናቋል', 'ተዛዚሙ'],
    'Content URL': ['የይዘት አድራሻ (URL)', 'ኣድራሻ ትሕዝቶ (URL)'],
    'Correct answer…': ['ትክክለኛ መልስ…', 'ቅኑዕ መልሲ…'],
    'Course title': ['የኮርስ ርዕስ', 'ኣርእስቲ ኮርስ'],
    'Create Course': ['ኮርስ ፍጠር', 'ኮርስ ፍጠሩ'],
    'Create Teacher Account': ['የመምህር አካውንት ይፍጠሩ', 'ኣካውንት መምህር ፍጠሩ'],
    'Create better.': ['የተሻለ ይፍጠሩ።', 'ዝሓሸ ፍጠሩ።'],
    'Create the exam visually. The secure answer key is stored separately on the server and is never sent to students.': ['ፈተናውን በእይታ ይፍጠሩ። ደህንነቱ የተጠበቀ የመልስ ቁልፍ ለብቻው በሰርቨር ላይ ይቀመጣል፣ ለተማሪዎችም ፈጽሞ አይላክም።', 'ፈተና ብትርኢት ፍጠሩ። ውሑስ መፍትሕ መልሲ ንበይኑ ኣብ ሰርቨር ይቕመጥ፣ ንተመሃሮ ውን ፈፅሙ ኣይልኣኽን።'],
    'D': ['D', 'D'],
    'Describe your teaching/work experience': ['የማስተማር/የስራ ልምድዎን ይግለጹ', 'ተሞክሮ ትምህርቲ/ስራሕኩም ግለፁ'],
    'Distance': ['የርቀት', 'ርሕቐት'],
    'Draft with AI': ['በAI ረቂቅ ፍጠር', 'ብAI ንድፊ ፍጠሩ'],
    'Duration (minutes)': ['ቆይታ (በደቂቃ)', 'ንውሓት ግዜ (ብደቒቕ)'],
    'Easy': ['ቀላል', 'ቀሊል'],
    'Everything you need, organized in one place': ['የሚያስፈልግዎ ሁሉ በአንድ ቦታ የተደራጀ', 'ኩሉ ዘድልየኩም ኣብ ሓደ ቦታ ተወዲኡ'],
    'Exam Results': ['የፈተና ውጤቶች', 'ውፅኢት ፈተና'],
    'Exam title': ['የፈተና ርዕስ', 'ኣርእስቲ ፈተና'],
    'Example: Prepare a Grade 8 mathematics lesson on linear equations with objectives, worked examples, group activity, differentiation and a 5-question exit ticket.': ['ምሳሌ፦ ስለ መስመራዊ ቀመሮች ግቦች፣ የተፈቱ ምሳሌዎች፣ የቡድን ስራ፣ ልዩነትና 5 ጥያቄ ያለው መውጫ ፈተና ያለው የ8ኛ ክፍል የሂሳብ ትምህርት ያዘጋጁ።', 'ንኣብነት፦ ብዛዕባ ቀጥተኛ ቀመር ሸቶታት፣ ዝተፈትሐ ኣብነታት፣ ስራሕ ጉጅለ፣ ፍልልይን 5 ሕቶ ዘለዎ ፈተና መውፅእን ዘለዎ ትምህርቲ ሒሳብ ንክፍሊ 8 ኣዳልዉ።'],
    'External link': ['ውጫዊ አድራሻ', 'ግዳማዊ ኣድራሻ'],
    'Filter assignments…': ['ስራዎችን ማጣሪያ…', 'ዕዮታት ኣጣሩ…'],
    'Filter by name, phone, email, or grade…': ['በስም፣ በስልክ፣ በኢሜይል ወይም በክፍል ማጣሪያ…', 'ብስም፣ ብስልኪ፣ ብኢሜይል ወይ ብክፍሊ ኣጣሩ…'],
    'Filter by subject': ['በትምህርት ዓይነት ማጣሪያ', 'ብዓይነት ትምህርቲ ኣጣሩ'],
    'Filter courses…': ['ኮርሶችን ማጣሪያ…', 'ኮርሳት ኣጣሩ…'],
    'Filter lesson plans…': ['የትምህርት እቅዶችን ማጣሪያ…', 'መደባት ትምህርቲ ኣጣሩ…'],
    'Filter materials…': ['ግብዓቶችን ማጣሪያ…', 'ትሕዝቶ ኣጣሩ…'],
    'Free Regular': ['ነፃ መደበኛ', 'ናፃ ልሙድ'],
    'Generate with AI': ['በAI ፍጠር', 'ብAI ፍጠሩ'],
    'Give students classwork or homework and track your published assignments.': ['ለተማሪዎች የክፍል ወይም የቤት ስራ ስጡ፣ ያሳተሟቸውን ስራዎች ይከታተሉ።', 'ንተመሃሮ ስራሕ ክፍሊ ወይ ገዛ ሃቡ፣ ዝወፀ ዕዮታትኩም ተኸታተሉ።'],
    'Grade (Question Bank only)': ['ክፍል (የጥያቄ ባንክ ብቻ)', 'ክፍሊ (ባንክ ሕቶ ጥራይ)'],
    'Grade 3–12': ['ከ3-12ኛ ክፍል', 'ካብ ክፍሊ 3–12'],
    'Grades': ['ክፍሎች', 'ክፍልታት'],
    'Grade…': ['ክፍል…', 'ክፍሊ…'],
    'Group Work': ['የቡድን ስራ', 'ስራሕ ጉጅለ'],
    'Hard': ['ከባድ', 'ኸቢድ'],
    'Hold Ctrl/Cmd to select more than one. Mark a course "Distance" so it appears in students\' Distance Learning tab.': ['ከአንድ በላይ ለመምረጥ Ctrl/Cmdን ይያዙ። ኮርስን "የርቀት" ብለው ምልክት ካደረጉ በተማሪዎች የርቀት ትምህርት ትር ውስጥ ይታያል።', 'ልዕሊ ሓደ ንምምራፅ Ctrl/Cmd ሓዙ። ኮርስ "ርሕቐት" ኢልኩም እንተጌርኩምዎ ኣብ ትር ትምህርቲ ርሕቐት ተመሃሮ ክርአ እዩ።'],
    'Import as: Classwork (a specific class)': ['እንደ፦ የክፍል ስራ (ለተወሰነ ክፍል) አስገባ', 'ከም፦ ስራሕ ክፍሊ (ንፍሉይ ክፍሊ) ኣእትዉ'],
    'Import as: Question Bank (reusable, needs approval)': ['እንደ፦ የጥያቄ ባንክ (ደጋግሞ የሚያገለግል፣ ማጽደቅ ይፈልጋል) አስገባ', 'ከም፦ ባንክ ሕቶ (ደጋጊሙ ዝጥቀም፣ ምፅዳቕ ዘድልዮ) ኣእትዉ'],
    'In Progress': ['በሂደት ላይ', 'ኣብ ሂደት'],
    'Instructions for students': ['ለተማሪዎች መመሪያ', 'መምርሒ ንተመሃሮ'],
    'Learning Mode': ['የመማሪያ ዘዴ', 'ኣገባብ ትምህርቲ'],
    'Learning Mode (optional)': ['የመማሪያ ዘዴ (አማራጭ)', 'ኣገባብ ትምህርቲ (ምርጫ)'],
    'Learning Mode / Audience are optional — leave unselected unless this plan is specific to one cohort.': ['የመማሪያ ዘዴ / ተመልካች አማራጭ ናቸው — ይህ እቅድ ለተወሰነ ቡድን ካልሆነ በስተቀር ይተውት።', 'ኣገባብ ትምህርቲ / ተመልከቲ ምርጫ እዮም — እዚ መደብ ንፍሉይ ጉጅለ እንተዘይኮይኑ ዘይምረፅዎ።'],
    'Learning objective (optional)': ['የመማሪያ ግብ (አማራጭ)', 'ሸቶ ትምህርቲ (ምርጫ)'],
    'Learning objectives (one per line)': ['የመማሪያ ግቦች (በአንድ መስመር አንድ)', 'ሸቶታት ትምህርቲ (ኣብ ነፍሲ ወከፍ መስመር ሓደ)'],
    'Leave both unselected to keep this class open to every student in the grade, as before.': ['ይህ ክፍል እንደበፊቱ ለሁሉም ተማሪ ክፍት እንዲሆን ሁለቱንም ሳይመርጡ ይተውት።', 'ከምቲ ቀደም እዚ ክፍሊ ንኹሉ ተመሃራይ ክፉት ንክኸውን ክልቲኡ ዘይምረፅዎ።'],
    'Lesson ID (optional, advanced)': ['የትምህርት መለያ (አማራጭ፣ ልዑል)', 'መለለዪ ትምህርቲ (ምርጫ፣ ልዑል)'],
    'Lesson Plans': ['የትምህርት እቅዶች', 'መደባት ትምህርቲ'],
    'Lesson description': ['የትምህርት መግለጫ', 'መግለፂ ትምህርቲ'],
    'Lesson title': ['የትምህርት ርዕስ', 'ኣርእስቲ ትምህርቲ'],
    'Library collection (optional)': ['የቤተመፃህፍት ስብስብ (አማራጭ)', 'እኩብ ቤተ-መፃሕፍቲ (ምርጫ)'],
    'Load Mark List': ['የውጤት ዝርዝር ጫን', 'ዝርዝር ውፅኢት ጽዓኑ'],
    'Load Roster': ['ዝርዝር ተማሪ ጫን', 'ዝርዝር ተመሃሮ ጽዓኑ'],
    'Material title': ['የግብዓት ርዕስ', 'ኣርእስቲ ትሕዝቶ'],
    'Materials / resources (one per line)': ['ግብዓቶች (በአንድ መስመር አንድ)', 'ትሕዝቶታት (ኣብ ነፍሲ ወከፍ መስመር ሓደ)'],
    'Maximum attempts': ['ከፍተኛ ሙከራ', 'ዝለዓለ ፈተነ'],
    'Medium': ['መካከለኛ', 'ማእከላይ'],
    'Meeting link (Zoom, Google Meet, Telegram, Teams, YouTube, etc.)': ['የስብሰባ አድራሻ (ዙም፣ ጉግል ሚት፣ ቴሌግራም፣ ቲምስ፣ ዩቲዩብ፣ ወዘተ)', 'ኣድራሻ ኣኼባ (ዙም፣ ጉግል ሚት፣ ተለግራም፣ ቲምስ፣ ዩትዩብ፣ ወዘተ)'],
    'Message verified parents connected to students in your teaching classes.': ['ከሚያስተምሩት ክፍል ተማሪዎች ጋር ከተገናኙ የተረጋገጡ ወላጆች ጋር ይገናኙ።', 'ምስቶም ኣብ ክፍልታት ትምህርትኹም ተመሃሮ ዝተኣሳሰሩ እተረጋገፀ ወለዲ ተራኸቡ።'],
    'My drafts & pending': ['ረቂቆቼና በመጠባበቅ ላይ ያሉ', 'ንድፍታተይን ዝፀንሑን'],
    'Name': ['ስም', 'ስም'],
    'No assignment link': ['የስራ አድራሻ የለም', 'ኣድራሻ ዕዮ የለን'],
    'No course link': ['የኮርስ አድራሻ የለም', 'ኣድራሻ ኮርስ የለን'],
    'No questions added yet.': ['እስካሁን ጥያቄ አልተጨመረም።', 'ክሳብ ሕጂ ሕቶ ኣይተወሰኸን።'],
    'One sign-in box: email + password or phone + PIN.': ['አንድ የመግቢያ ሳጥን፦ ኢሜይል + የይለፍ ቃል ወይም ስልክ + ፒን።', 'ሓደ ሳፁን መእተዊ፦ ኢሜይል + ቃል ምስጢር ወይ ስልኪ + ፒን።'],
    'Option A': ['አማራጭ A', 'ኣማራፂ A'],
    'Option B': ['አማራጭ B', 'ኣማራፂ B'],
    'Option C': ['አማራጭ C', 'ኣማራፂ C'],
    'Option D': ['አማራጭ D', 'ኣማራፂ D'],
    'Or paste lesson text here': ['ወይም የትምህርት ፅሁፍ እዚህ ይለጥፉ', 'ወይ ፅሑፍ ትምህርቲ ኣብዚ ለጥፉ'],
    'Order in course (0, 1, 2…)': ['በኮርስ ውስጥ ቅደም ተከተል (0, 1, 2…)', 'ቅደም ተኸተል ኣብ ኮርስ (0, 1, 2…)'],
    'Original Google Drive/Cloudinary URL (optional)': ['ዋና የGoogle Drive/Cloudinary አድራሻ (አማራጭ)', 'ናይ መጀመርታ ኣድራሻ Google Drive/Cloudinary (ምርጫ)'],
    'Page number': ['የገጽ ቁጥር', 'ቁፅሪ ገፅ'],
    'Paid Regular': ['የሚከፈልበት መደበኛ', 'ዝኽፈል ልሙድ'],
    'Parent Communication': ['ከወላጅ ጋር መገናኛ', 'ርክብ ምስ ወላዲ'],
    'Parents': ['ወላጆች', 'ወለዲ'],
    'Pass mark %': ['የማለፊያ ውጤት %', 'መመዘኒ ማለፊ %'],
    'Phone': ['ስልክ', 'ስልኪ'],
    "Plan a grade/subject/unit's lessons — objectives, activities, materials and assessment — and track progress. Course, Lesson and Assignment links are optional.": ['የክፍል/የትምህርት ዓይነት/የክፍለ ትምህርት ትምህርቶችን ያቅዱ — ግቦች፣ እንቅስቃሴዎች፣ ግብዓቶችና ግምገማ — እድገትንም ይከታተሉ። የኮርስ፣ የትምህርትና የስራ አድራሻዎች አማራጭ ናቸው።', 'ትምህርትታት ናይ ክፍሊ/ዓይነት ትምህርቲ/ክፍለ-ትምህርቲ መደቡ — ሸቶታት፣ ንጥፈታት፣ ትሕዝቶን ግምገማን — ዕብየት ውን ተኸታተሉ። ኣድራሻታት ኮርስ፣ ትምህርትን ዕዮን ምርጫ እዮም።'],
    'Plan lessons, publish materials, create secure exams, support students and use AI tools from one professional workspace.': ['ትምህርቶችን ያቅዱ፣ ግብዓቶችን ያሳትሙ፣ ደህንነቱ የተጠበቀ ፈተናዎችን ይፍጠሩ፣ ተማሪዎችን ይደግፉ፣ የAI መሳሪያዎችንም ከአንድ ሙያዊ የስራ ቦታ ይጠቀሙ።', 'ትምህርትታት መደቡ፣ ትሕዝቶ ኣውፅኡ፣ ውሑስ ፈተናታት ፍጠሩ፣ ተመሃሮ ደግፉ፣ መሳርሒታት AI ውን ካብ ሓደ ሞያዊ ቦታ ስራሕ ተጠቐሙ።'],
    'Plan title (optional)': ['የእቅድ ርዕስ (አማራጭ)', 'ኣርእስቲ መደብ (ምርጫ)'],
    'Planned': ['የታቀደ', 'ዝተመደበ'],
    'Points': ['ነጥቦች', 'ነጥብታት'],
    'Preview': ['ቅድመ እይታ', 'ቅድመ ትርኢት'],
    'Psychology & Development': ['ስነ ልቦናና እድገት', 'ስነ-ኣእምሮን ዕብየትን'],
    'Publish Assignment': ['ስራ አሳትም', 'ዕዮ ኣውፅኡ'],
    'Publish Exam': ['ፈተና አሳትም', 'ፈተና ኣውፅኡ'],
    'Publish Lesson': ['ትምህርት አሳትም', 'ትምህርቲ ኣውፅኡ'],
    'Question Bank': ['የጥያቄ ባንክ', 'ባንክ ሕቶ'],
    'Question Builder': ['የጥያቄ መገንቢያ', 'መስርሒ ሕቶ'],
    'Question text': ['የጥያቄ ፅሁፍ', 'ፅሑፍ ሕቶ'],
    'Quiz title prefix (optional)': ['የጥያቄ ርዕስ ቅድመ ቅጥያ (አማራጭ)', 'ቅድመ-ቅፅል ኣርእስቲ ሕቶ (ምርጫ)'],
    'Record': ['ቅዳ', 'ቅድሑ'],
    'Reference Books': ['የማጣቀሻ መጻሕፍት', 'መፃሕፍቲ መጣቐሲ'],
    'Regular': ['መደበኛ', 'ልሙድ'],
    'Regular classroom attendance only — Distance students track progress instead and never appear here.': ['የመደበኛ ክፍል መገኘት ብቻ — የርቀት ተማሪዎች ፈንታ እድገት ይከታተላሉ፣ እዚህ አይታዩም።', 'ኩነታት ምርካብ ልሙድ ክፍሊ ጥራይ — ተመሃሮ ርሕቐት ኣብ ክንድኡ ዕብየት ይከታተሉ፣ ኣብዚ ኣይረኣዩን።'],
    'Results': ['ውጤቶች', 'ውፅኢታት'],
    'Review completed exam attempts for exams you created. Student answer keys remain server-side.': ['ለፈጠሯቸው ፈተናዎች የተጠናቀቁ ሙከራዎችን ይገምግሙ። የተማሪ መልስ ቁልፍ በሰርቨር በኩል ይቀራል።', 'ንዝፈጠርኩሞ ፈተናታት ዝተዛዘመ ፈተነታት ገምግሙ። መፍትሕ መልሲ ተመሃራይ ኣብ ሰርቨር ይፀንሕ።'],
    'Save Attendance': ['የመገኘት ሁኔታ አስቀምጥ', 'ኩነታት ምርካብ ኣቐምጡ'],
    'Save Lesson Plan': ['የትምህርት እቅድ አስቀምጥ', 'መደብ ትምህርቲ ኣቐምጡ'],
    'Save Review': ['ግምገማ አስቀምጥ', 'ግምገማ ኣቐምጡ'],
    'Save as Draft': ['እንደ ረቂቅ አስቀምጥ', 'ከም ንድፊ ኣቐምጡ'],
    'Scan & Extract': ['ቃኝና አውጣ', 'ስካን ግበሩን ኣውፅኡን'],
    'Scanner': ['ስካነር', 'ስካነር'],
    'Schedule Grade 3–12 classes. Students see upcoming classes and your online status.': ['ከ3-12ኛ ክፍል ትምህርቶች ፕሮግራም ያውጡ። ተማሪዎች የሚመጡ ትምህርቶችንና የመስመር ላይ ሁኔታዎን ያያሉ።', 'ትምህርትታት ካብ ክፍሊ 3–12 መደብ ግበሩ። ተመሃሮ ዝመፅእ ትምህርትታትን ኩነታት መስመርኹምን ይርእዩ።'],
    'Schedule Live Class': ['ቀጥታ ትምህርት ፕሮግራም አውጣ', 'ቀጥታ ትምህርቲ መደብ ግበሩ'],
    'Scholarship': ['ስኮላርሺፕ', 'ስኮላርሺፕ'],
    'Search Library': ['ቤተመፃህፍት ፈልግ', 'ቤተ-መፃሕፍቲ ድለዩ'],
    'Send Selected to Telegram': ['የተመረጡትን ወደ ቴሌግራም ላክ', 'ዝተመረፀ ናብ ተለግራም ስደዱ'],
    'Send to Telegram target…': ['ወደ ቴሌግራም ዒላማ ላክ…', 'ናብ ዒላማ ተለግራም ስደዱ…'],
    'Short professional bio': ['አጭር ሙያዊ መግለጫ', 'ሓፂር ሞያዊ መግለፂ'],
    'Sign in to access your approved teacher dashboard.': ['የፀደቀ የመምህር ዳሽቦርድዎን ለማግኘት ይግቡ።', 'ንዝፀደቐ ዳሽቦርድ መምህርነትኩም ንምእታው እተዉ።'],
    'Smart Quiz Scanner': ['ብልህ የጥያቄ ስካነር', 'ብልሕ ስካነር ሕቶ'],
    'Source reference (optional)': ['የምንጭ ማጣቀሻ (አማራጭ)', 'መጣቐሲ ምንጪ (ምርጫ)'],
    'Start time (EAT / Africa/Addis_Ababa)': ['የመጀመሪያ ሰዓት (EAT / Africa/Addis_Ababa)', 'ሰዓት ምጅማር (EAT / Africa/Addis_Ababa)'],
    'Student Submissions': ['የተማሪ ማስገቢያዎች', 'ዝቐረበ ካብ ተመሃሮ'],
    'Student Support Inbox': ['የተማሪ ድጋፍ የገቢ መልዕክት', 'ሳፁን ደገፍ ተመሃሮ'],
    'Students': ['ተማሪዎች', 'ተመሃሮ'],
    'Students in the grades you teach. Contact details are shown server-authorized.': ['በሚያስተምሩት ክፍሎች ውስጥ ያሉ ተማሪዎች። የመገናኛ ዝርዝሮች በሰርቨር ፍቃድ ይታያሉ።', 'ተመሃሮ ኣብቶም ትምህርትዎም ክፍልታት። ዝርዝር ርክብ ብፍቓድ ሰርቨር ይርአ።'],
    'Students, teachers and admins • replies/comments • reactions • photos • audio • video': ['ተማሪዎች፣ መምህራንና አስተዳዳሪዎች • ምላሽ/አስተያየት • ስሜት • ፎቶ • ድምጽ • ቪዲዮ', 'ተመሃሮ፣ መምህራንን ኣመሓደርትን • ምላሽ/ርእይቶ • ስምዒት • ስእሊ • ድምፂ • ቪድዮ'],
    'Subchapter ID (optional)': ['የንዑስ ምዕራፍ መለያ (አማራጭ)', 'መለለዪ ንኡስ ምዕራፍ (ምርጫ)'],
    'Subject (Question Bank only)': ['የትምህርት ዓይነት (የጥያቄ ባንክ ብቻ)', 'ዓይነት ትምህርቲ (ባንክ ሕቶ ጥራይ)'],
    'Subject (e.g. Mathematics)': ['የትምህርት ዓይነት (ለምሳሌ ሂሳብ)', 'ዓይነት ትምህርቲ (ንኣብነት ሒሳብ)'],
    'Subject (optional)': ['የትምህርት ዓይነት (አማራጭ)', 'ዓይነት ትምህርቲ (ምርጫ)'],
    'Subjects': ['የትምህርት ዓይነቶች', 'ዓይነታት ትምህርቲ'],
    'Submit your teaching profile. An administrator must approve it before you can publish content.': ['የማስተማሪያ መገለጫዎን ያስገቡ። ይዘት ከማሳተምዎ በፊት አስተዳዳሪ ማጽደቅ አለበት።', 'መግለፂ ትምህርትኹም ኣቕርቡ። ቅድሚ ትሕዝቶ ምውፃእኩም ኣመሓዳሪ ከፅድቖ ኣለዎ።'],
    'Target Audience': ['ዒላማ ተመልካች', 'ዒላማ ተጠቀምቲ'],
    'Target Audience (optional)': ['ዒላማ ተመልካች (አማራጭ)', 'ዒላማ ተጠቀምቲ (ምርጫ)'],
    'Teach smarter.': ['በብልህነት አስተምሩ።', 'ብብልሓት ምሃሩ።'],
    'Teach • Create • Inspire': ['አስተምር • ፍጠር • አነሳሳ', 'ምሃሩ • ፍጠሩ • ኣበራብሩ'],
    'Teacher Sign In': ['የመምህር መግቢያ', 'መእተዊ መምህር'],
    'Teacher account access': ['የመምህር አካውንት መዳረሻ', 'መእተዊ ኣካውንት መምህር'],
    'Teacher dashboard sections': ['የመምህር ዳሽቦርድ ክፍሎች', 'ክፍልታት ዳሽቦርድ መምህር'],
    'Teacher workspace': ['የመምህር የስራ ቦታ', 'ቦታ ስራሕ መምህር'],
    'Teaching Tools': ['የማስተማሪያ መሳሪያዎች', 'መሳርሒታት ትምህርቲ'],
    'Teaching experience': ['የማስተማር ልምድ', 'ተሞክሮ ትምህርቲ'],
    'Topic (e.g. Fractions)': ['ርዕስ (ለምሳሌ ክፍልፋይ)', 'ኣርእስቲ (ንኣብነት ክፋልፋይ)'],
    'Unit (e.g. Kinematics)': ['ክፍለ ትምህርት (ለምሳሌ ኪነማቲክስ)', 'ክፍለ-ትምህርቲ (ንኣብነት ኪነማቲክስ)'],
    'Upload a question photo or PDF. BMT will extract multiple-choice questions for review. Nothing is imported until you approve the edited questions.': ['የጥያቄ ፎቶ ወይም PDF ይስቀሉ። BMT ለግምገማ የምርጫ ጥያቄዎችን ያወጣል። የተስተካከሉ ጥያቄዎችን እስኪያጸድቁ ምንም አይገባም።', 'ስእሊ ሕቶ ወይ PDF ስቐሉ። BMT ንግምገማ ሕቶታት ብዙሓት ምርጫ ከውፅእ እዩ። ክሳብ ዝተኣረመ ሕቶታት ተፅድቑ ዝኾነ ነገር ኣይኣቱን።'],
    'Use email/password or a phone number with a secure PIN.': ['ኢሜይል/የይለፍ ቃል ወይም ስልክ ቁጥር ከደህንነቱ ከተጠበቀ ፒን ጋር ይጠቀሙ።', 'ኢሜይል/ቃል ምስጢር ወይ ቁፅሪ ስልኪ ምስ ውሑስ ፒን ተጠቐሙ።'],
    'Use the existing Gemini setup to prepare lessons, activities, quizzes, homework and differentiated tasks. No new API key is required.': ['ትምህርቶችን፣ እንቅስቃሴዎችን፣ ጥያቄዎችን፣ የቤት ስራንና የተለያዩ ስራዎችን ለማዘጋጀት ያለውን የጀሚኒ ማዋቀሪያ ይጠቀሙ። አዲስ የAPI ቁልፍ አያስፈልግም።', 'ንምድላው ትምህርትታት፣ ንጥፈታት፣ ሕቶታት፣ ስራሕ ገዛን ፍሉይ ዕዮታትን ነቲ ዘሎ ኣቀማምጣ Gemini ተጠቐሙ። ሓዲሽ መፍትሕ API ኣየድልን።'],
    'View Report': ['ሪፖርት እይ', 'ጸብጻብ ርኣዩ'],
    'Voice recordings: maximum 60 seconds • audio upload: maximum 5 MB': ['የድምጽ ቅጂ፦ ቢበዛ 60 ሰከንድ • የድምጽ ስቀላ፦ ቢበዛ 5 ሜባ', 'ቅዳሕ ድምፂ፦ ብዝበዝሐ 60 ካልኢት • ስቐላ ድምፂ፦ ብዝበዝሐ 5 ሜባ'],
    'What will students learn?': ['ተማሪዎች ምን ይማራሉ?', 'ተመሃሮ እንታይ ክመሃሩ እዮም?'],
    'Work / Assignments': ['ስራ / ስራዎች', 'ስራሕ / ዕዮታት'],
    'Workspace active': ['የስራ ቦታ ንቁ ነው', 'ቦታ ስራሕ ንጡፍ እዩ'],
    'Write a private message to the parent…': ['ለወላጅ የግል መልዕክት ይጻፉ…', 'ውልቃዊ መልእኽቲ ንወላዲ ፅሓፉ…'],
    'YOUR TEACHING CENTER': ['የማስተማሪያ ማዕከልዎ', 'ማእከል ትምህርቲ ንዓኹም'],
    'Your professional workspace for classes, assignments, exams, AI and student support.': ['ለክፍሎች፣ ስራዎች፣ ፈተናዎች፣ AI እና የተማሪ ድጋፍ ሙያዊ የስራ ቦታዎ።', 'ሞያዊ ቦታ ስራሕኩም ንክፍልታት፣ ዕዮታት፣ ፈተናታት፣ AI ን ደገፍ ተመሃሮን።'],
    'e.g. Diploma, BSc, MSc, MA': ['ለምሳሌ፦ ዲፕሎማ፣ ቢኤስሲ፣ ኤምኤስሲ፣ ኤምኤ', 'ንኣብነት፦ ዲፕሎማ፣ ቢኤስሲ፣ ኤምኤስሲ፣ ኤምኤ'],
    '↻ Refresh': ['↻ አድስ', '↻ ኣሓድሱ'],
    '＋ Add Question': ['＋ ጥያቄ ጨምር', '＋ ሕቶ ወስኹ'],
    'end-of-list-marker': null
  };
  delete STRINGS['end-of-list-marker'];
  for (var g = 1; g <= 12; g++) {
    STRINGS['Grade ' + g] = ['ክፍል ' + g, 'ክፍሊ ' + g];
  }

  /* ------------------------------------------------------------------ */
  function norm(s) { return String(s).replace(/\s+/g, ' ').trim(); }

  function add(map) {
    Object.keys(map).forEach(function (k) { DICT[norm(k)] = map[k]; });
  }
  add(STRINGS);

  function lookup(englishSrc) {
    if (lang === 'en') return null;
    var entry = DICT[norm(englishSrc)];
    return entry ? (entry[IDX[lang]] || null) : null;
  }

  // Translate a whole string, keeping its surrounding whitespace.
  function translateKeepingSpace(src) {
    var tr = lookup(src);
    if (!tr) return src;
    var lead = /^\s*/.exec(src)[0];
    var trail = /\s*$/.exec(src)[0];
    return lead + tr + trail;
  }

  // Public: translate an English string (with optional {vars}) for JS-built messages.
  function t(english, vars) {
    var out = lookup(english) || english;
    if (vars) {
      Object.keys(vars).forEach(function (k) {
        out = out.split('{' + k + '}').join(vars[k]);
      });
    }
    return out;
  }

  /* ---- DOM walking ---- */
  function skipped(node) {
    var el = node.nodeType === 3 ? node.parentNode : node;
    if (!el || el.nodeType !== 1) return true;
    if (SKIP_TAGS[el.tagName]) return true;
    return !!(el.closest && el.closest('[data-no-i18n]'));
  }

  function processTextNode(n) {
    if (skipped(n)) return;
    var cur = n.nodeValue, src;
    if (appliedText.has(n) && cur === appliedText.get(n)) src = origText.get(n);
    else src = cur;                       // new text, or JS wrote fresh English text
    if (!/\S/.test(src)) return;
    if (!DICT[norm(src)]) { appliedText.delete(n); return; }
    var out = translateKeepingSpace(src);
    if (out !== cur) n.nodeValue = out;
    origText.set(n, src);
    appliedText.set(n, out);
  }

  function processAttrs(el) {
    if (skipped(el)) return;
    ATTRS.forEach(function (a) {
      if (!el.hasAttribute(a)) return;
      var cur = el.getAttribute(a);
      var rec = el.__bmtA && el.__bmtA[a];
      var src = (rec && cur === rec.applied) ? rec.src : cur;
      if (!DICT[norm(src)]) return;
      var out = translateKeepingSpace(src);
      if (out !== cur) el.setAttribute(a, out);
      (el.__bmtA = el.__bmtA || {})[a] = { src: src, applied: out };
    });
  }

  function applyTo(root) {
    if (!root) return;
    if (root.nodeType === 3) { processTextNode(root); return; }
    if (root.nodeType !== 1) return;
    processAttrs(root);
    var w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    var n;
    while ((n = w.nextNode())) processTextNode(n);
    var els = root.querySelectorAll('[placeholder],[aria-label],[title],[alt]');
    for (var i = 0; i < els.length; i++) processAttrs(els[i]);
  }

  function applyTitle() {
    if (origTitle === null) origTitle = document.title;
    document.title = translateKeepingSpace(origTitle);
  }

  /* ---- observer: translate text that JavaScript writes later ---- */
  function connect() {
    if (!observer || lang === 'en' || !document.body) return;
    observer.observe(document.body, {
      childList: true, characterData: true, subtree: true,
      attributes: true, attributeFilter: ATTRS
    });
  }
  function startObserver() {
    if (observer) return;
    observer = new MutationObserver(function (muts) {
      observer.disconnect();
      muts.forEach(function (m) {
        if (m.type === 'characterData') processTextNode(m.target);
        else if (m.type === 'attributes') processAttrs(m.target);
        else if (m.type === 'childList') {
          for (var i = 0; i < m.addedNodes.length; i++) applyTo(m.addedNodes[i]);
        }
      });
      connect();
    });
  }

  /* ---- fonts: Ethiopic script needs a font that has it ---- */
  var fontReady = false;
  function ensureFont() {
    if (fontReady) return;
    fontReady = true;
    var link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = 'https://fonts.googleapis.com/css2?family=Noto+Sans+Ethiopic:wght@400;600;700&display=swap';
    document.head.appendChild(link);
  }
  function injectCss() {
    var st = document.createElement('style');
    st.textContent =
      'html[lang="am"] body,html[lang="am"] button,html[lang="am"] input,html[lang="am"] select,html[lang="am"] textarea,' +
      'html[lang="ti"] body,html[lang="ti"] button,html[lang="ti"] input,html[lang="ti"] select,html[lang="ti"] textarea' +
      '{font-family:Inter,"Noto Sans Ethiopic","Abyssinica SIL",Nyala,Ebrima,system-ui,sans-serif}' +
      '.bmt-lang-select{appearance:auto;border:1px solid rgba(108,99,255,.28);background:transparent;color:inherit;' +
      'border-radius:999px;padding:7px 10px;font:inherit;font-size:13px;font-weight:600;cursor:pointer;max-width:120px}' +
      '.bmt-lang-select option{color:#191d2e;background:#fff}';
    document.head.appendChild(st);
  }

  /* ---- language selector widgets ---- */
  function syncSwitchers() {
    var sels = document.querySelectorAll('.bmt-lang-select');
    for (var i = 0; i < sels.length; i++) {
      sels[i].value = lang;
      sels[i].setAttribute('aria-label', t('Language'));
    }
  }
  function renderSwitchers() {
    var hosts = document.querySelectorAll('[data-lang-switcher]');
    for (var i = 0; i < hosts.length; i++) {
      var host = hosts[i];
      if (host.querySelector('.bmt-lang-select')) continue;
      host.setAttribute('data-no-i18n', '1');     // language names stay in their own script
      var sel = document.createElement('select');
      sel.className = 'bmt-lang-select';
      LANGS.forEach(function (o) {
        var op = document.createElement('option');
        op.value = o.code; op.textContent = o.label;
        sel.appendChild(op);
      });
      sel.addEventListener('change', function (e) { setLang(e.target.value); });
      host.appendChild(sel);
    }
    syncSwitchers();
  }

  /* ---- public API ---- */
  function validLang(c) { return LANGS.some(function (l) { return l.code === c; }); }
  function getLang() { return lang; }

  function setLang(code, opts) {
    if (!validLang(code)) code = 'en';
    lang = code;
    if (!opts || opts.persist !== false) {
      try { localStorage.setItem(STORE_KEY, code); } catch (_) {}
    }
    document.documentElement.setAttribute('lang', code);
    if (observer) observer.disconnect();
    if (code !== 'en') ensureFont();
    if (document.body) applyTo(document.body);
    applyTitle();
    syncSwitchers();
    if (code !== 'en') { startObserver(); connect(); }
    try { document.dispatchEvent(new CustomEvent('bmt:langchange', { detail: { lang: code } })); } catch (_) {}
  }

  function initialLang() {
    try {
      var q = new URLSearchParams(location.search).get('lang');
      if (q && validLang(q)) return q;
    } catch (_) {}
    try {
      var saved = localStorage.getItem(STORE_KEY);
      if (saved && validLang(saved)) return saved;
    } catch (_) {}
    var nav = (navigator.language || 'en').slice(0, 2).toLowerCase();
    return (nav === 'am' || nav === 'ti') ? nav : 'en';
  }

  function init() {
    injectCss();
    renderSwitchers();
    setLang(initialLang(), { persist: false });
  }

  window.BMT_I18N = { t: t, add: add, setLang: setLang, getLang: getLang, langs: LANGS };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
