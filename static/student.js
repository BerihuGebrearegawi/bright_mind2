import { initializeApp } from "https://www.gstatic.com/firebasejs/10.7.1/firebase-app.js";
import { 
    getFirestore, collection, addDoc, getDocs, query, where, 
    onSnapshot, doc, getDoc, updateDoc, setDoc, serverTimestamp, increment 
} from "https://www.gstatic.com/firebasejs/10.7.1/firebase-firestore.js";
import { 
    getAuth, onAuthStateChanged, signInWithEmailAndPassword, 
    signOut, createUserWithEmailAndPassword, deleteUser, getIdTokenResult, signInWithCustomToken, RecaptchaVerifier, signInWithPhoneNumber,
    setPersistence, inMemoryPersistence
} from "https://www.gstatic.com/firebasejs/10.7.1/firebase-auth.js";

const firebaseConfig = {
    apiKey: "AIzaSyAyeZpwu9-FECjC5Qp-lI0OUAblKusxkeI",
    authDomain: "bright-mind-tutor-app.firebaseapp.com",
    projectId: "bright-mind-tutor-app",
    storageBucket: "bright-mind-tutor-app.firebasestorage.app",
    messagingSenderId: "782512714975",
    appId: "1:782512714975:web:719e3b7a09ac8c7f9d256a",
    measurementId: "G-TWYNFN7MT6"
};

const app = initializeApp(firebaseConfig);
const db = getFirestore(app);
const auth = getAuth(app);
window.auth = auth;
let messaging = null;

let unsubscribeChat = null;
let unsubscribeUserAccess = null;
let replyToMessageId = null;
let typingTimeout = null;
let quizTimer = null;
let _profileFile = null;
const BMT_PREVIEW_PARAMS = new URLSearchParams(location.search);
const BMT_PREVIEW_ROLE = BMT_PREVIEW_PARAMS.get('preview') === '1' && BMT_PREVIEW_PARAMS.get('role') === 'student' ? 'student' : '';
const BMT_PREVIEW_TOKEN = BMT_PREVIEW_PARAMS.get('token') || '';
const BMT_PREVIEW_UID = BMT_PREVIEW_PARAMS.get('uid') || '';
// True only once this tab is actually signed in as the previewed account, so
// write-guards below don't accidentally block a real logged-in student.
window.BMT_ADMIN_PREVIEW = false;
let _bmtPreviewSignInAttempted = false;
let isAuthenticated = false;
let currentUser = null;
let currentUserData = null;
let BMT_PRICES = { currency: 'ETB', monthly: 9.99, yearly: 79.99, video: 2.99, exam: 4.99, quiz: 1.99, live: 5.99 };
let selectedPlan = { name: 'monthly', amount: BMT_PRICES.monthly };

let _bookmarks = {};
async function loadBookmarks(){
    const box=document.getElementById('savedItemsContainer'); if(!box||!auth.currentUser)return;
    box.style.display='block'; box.innerHTML='<div class="spinner"></div> Loading saved items…';
    try{
        const snap=await getDoc(doc(db,'bookmarks',auth.currentUser.uid));
        _bookmarks=(snap.exists()?(snap.data().items||{}):{});
        const items=Object.values(_bookmarks);
        box.innerHTML=items.length?items.map(x=>{
            const isReaderSpot=x.type==='book_position';
            const parts=isReaderSpot?String(x.id||'').split(':'):[];
            const resumeBtn=isReaderSpot&&parts.length===3?`<button class="btn btn-outline" type="button" data-resume-reader="${escapeHtml(parts[0])}" data-resume-book="${escapeHtml(parts[1])}" data-resume-sub="${escapeHtml(parts[2])}" data-resume-title="${escapeHtml((x.title||'').split(' — ')[0]||x.title||'')}">📖 Resume reading</button>`:'';
            const isGcsBookmark=x.storageProvider==='gcs'&&x.storagePath;
            const openBtn=isGcsBookmark
                ?`<button class="btn btn-outline" type="button" data-bookmark-gcs-open="${escapeHtml(x.storagePath)}">Open</button>`
                :(x.url?`<a class="btn btn-outline" target="_blank" rel="noopener" href="${escapeHtml(x.url)}">Open</a>`:'');
            return `<div class="item-row" style="margin-bottom:6px"><div class="item-content"><strong>${escapeHtml(x.title||'Saved item')}</strong><div class="subtitle">${escapeHtml(isReaderSpot?'Reading position':(x.type||'content'))} • ${escapeHtml(x.className||'')}</div></div><div class="item-actions">${resumeBtn}${openBtn}<button class="btn btn-danger" type="button" data-remove-bookmark="${escapeHtml(x.id)}">Remove</button></div></div>`;
        }).join(''):'<p class="subtitle">No saved items yet. Use 🔖 Save on a book or video.</p>';
        box.querySelectorAll('[data-remove-bookmark]').forEach(b=>b.onclick=()=>toggleBookmark(b.dataset.removeBookmark));
        box.querySelectorAll('[data-resume-reader]').forEach(b=>b.onclick=async()=>{ const {resumeReader,resumeBook,resumeSub,resumeTitle}=b.dataset; if(typeof window.openReader!=='function')return; await window.openReader(resumeReader,resumeBook,resumeTitle||'Reader'); if(typeof window.readerJumpTo==='function')window.readerJumpTo(resumeSub); });
        box.querySelectorAll('[data-bookmark-gcs-open]').forEach(button=>{
            button.addEventListener('click', async ()=>{
                try{
                    const token=await auth.currentUser.getIdToken(true);
                    const path=button.dataset.bookmarkGcsOpen;
                    const response=await fetch('/api/storage/document-url',{method:'POST',headers:{'Content-Type':'application/json','Authorization':`Bearer ${token}`},body:JSON.stringify({storagePath:path})});
                    const data=await response.json().catch(()=>({}));
                    if(!response.ok||!data.url) throw new Error(data.error||'Could not open document.');
                    window.open(data.url,'_blank','noopener');
                }catch(error){console.error(error);alert(error.message||'Could not open document.');}
            });
        });
    }catch(e){console.error('Bookmarks:',e);box.innerHTML='<p class="error-text">Unable to load saved items.</p>';}
}
async function toggleBookmark(id,title='',type='',url='',className='',storagePath=''){
    const user=auth.currentUser;if(!user||!id)return;
    try{
        const ref=doc(db,'bookmarks',user.uid),snap=await getDoc(ref); const items=snap.exists()?(snap.data().items||{}):{};
        if(items[id]) delete items[id]; else items[id]={id,title,type,url:storagePath?'':url,className,storagePath,storageProvider:storagePath?'gcs':'',savedAt:new Date().toISOString()};
        await setDoc(ref,{items,updatedAt:serverTimestamp()},{merge:true}); _bookmarks=items;
        document.querySelectorAll(`[data-bookmark-id="${CSS.escape(id)}"]`).forEach(btn=>btn.textContent=items[id]?'🔖 Saved':'🔖 Save');
        if(document.getElementById('savedItemsContainer')?.style.display!=='none') loadBookmarks();
        showToast(items[id]?'Saved to My Library':'Removed from My Library','success');
    }catch(e){console.error(e);showToast('Could not update saved item.','error');}
}
function bindBookmarkButtons(){
    document.querySelectorAll('[data-bookmark-id]').forEach(btn=>{
        if(btn.dataset.bound)return; btn.dataset.bound='1';
        const id=btn.dataset.bookmarkId; btn.textContent=_bookmarks[id]?'🔖 Saved':'🔖 Save';
        btn.onclick=()=>toggleBookmark(id,btn.dataset.bookmarkTitle||'',btn.dataset.bookmarkType||'',btn.dataset.bookmarkUrl||'',btn.dataset.bookmarkClass||'',btn.dataset.bookmarkStoragePath||'');
    });
}
function setupContentSearch(){
    const input=document.getElementById('contentSearch'); if(!input||input.dataset.ready)return; input.dataset.ready='1';
    input.addEventListener('input',()=>{const q=input.value.trim().toLowerCase();document.querySelectorAll('#booksContainer .item-row,#studentVideosContainer .video-card,#libraryList .item-row,#examArchiveList .item-row').forEach(el=>{el.style.display=!q||el.textContent.toLowerCase().includes(q)?'':'none';});});
}
const CLASSES = ['KG', '1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12'];
window.BMT_PRICES = BMT_PRICES;
async function loadBmtPrices(){ try{const r=await fetch('/api/settings/prices');const d=await r.json();if(d.success&&d.prices){BMT_PRICES={...BMT_PRICES,...d.prices};window.BMT_PRICES=BMT_PRICES;renderPaymentPrices();}}catch(e){console.warn('Pricing load failed; using defaults.',e);} }
async function loadOrgLogo(){ try{const r=await fetch('/api/settings/organization');const d=await r.json();if(d.success&&d.logoUrl){document.querySelectorAll('#navLogo, .org-logo').forEach(img=>{if(img)img.src=d.logoUrl;});}}catch(e){console.warn('Organization logo load failed; using default.',e);} }
function renderPaymentPrices(){const c=BMT_PRICES.currency||'ETB';const m=document.querySelector('[data-plan-monthly]'),y=document.querySelector('[data-plan-yearly]');if(m)m.textContent=`${BMT_PRICES.monthly.toFixed(2)} ${c}`;if(y)y.textContent=`${BMT_PRICES.yearly.toFixed(2)} ${c}`;const a=document.getElementById('selectedPlanAmount');const cu=document.getElementById('selectedPlanCurrency');if(a)a.textContent=Number(selectedPlan.amount).toFixed(2);if(cu)cu.textContent=c;}


window.loginStudent = loginStudent;
document.getElementById('refreshParentLinkCode')?.addEventListener('click',loadStudentParentLinkCode);
document.getElementById('regenerateParentLinkCode')?.addEventListener('click',regenerateStudentParentLinkCode);
document.getElementById('copyParentLinkCode')?.addEventListener('click',async()=>{const v=document.getElementById('parentLinkCodeDisplay')?.textContent||'';if(v&&v!=='—'){await navigator.clipboard?.writeText(v);document.getElementById('parentLinkCodeStatus').textContent='✅ Code copied.';}});
document.getElementById('studentPrivateChatSend')?.addEventListener('click',sendStudentPrivateMessage);

// ==========================================================
// 🔐 ROLE-AWARE AUTH
// Admin access is determined by Firebase custom claims, not by a
// hard-coded email address in client code. Backend/Firestore rules remain
// the authoritative security boundary.
// ==========================================================

// Applied AFTER the real student's real data has loaded through the normal
// path below. This does not fabricate any content — it only adds a banner
// and locks down controls that would write data, so what the admin sees is
// the genuine dashboard, not a mock-up.
function applyStudentPreviewSafeguards(){
    const old=document.querySelector('.bmt-preview-banner'); if(old) old.remove();
    const banner=document.createElement('div');
    banner.className='bmt-preview-banner';
    banner.innerHTML='<strong>ADMIN PREVIEW</strong> — Real Student Dashboard · Read-only view <span>Actions that change data are disabled</span> <a href="/admin">Return to Admin</a>';
    document.body.prepend(banner);

    const navButtons=document.querySelectorAll('.student-dashboard-nav button');
    navButtons.forEach(btn=>btn.classList.add('bmt-preview-safe'));
    const search=document.getElementById('contentSearch');
    if(search) search.disabled=false;

    // Keep the whole dashboard visible for inspection, but block operations that
    // would create, edit, delete, submit, pay, upload, message, or change account data.
    document.querySelectorAll('#mainContent form, #mainContent input[type=file]').forEach(el=>{
        el.dataset.previewDisabled='1';
        el.querySelectorAll('button,input,select,textarea').forEach(x=>x.disabled=true);
    });
    document.querySelectorAll('#mainContent button').forEach(btn=>{
        if(btn.classList.contains('bmt-preview-safe')) return;
        btn.disabled=true;
    });
    // Restore harmless section navigation after the blanket safety pass.
    navButtons.forEach(btn=>btn.disabled=false);
    const darkToggle=document.getElementById('darkModeToggle'); if(darkToggle) darkToggle.disabled=false;
}


// ==========================================================
// 🔐 AUTH STATE
// ==========================================================
function handlePaymentReturn() {
    const params = new URLSearchParams(location.search);
    if (params.get('payment') !== 'return') return;
    // Clean the tx_ref/payment params so a page refresh doesn't re-trigger this.
    history.replaceState({}, '', location.pathname);
    showToast('⏳ Confirming your payment… this usually takes a few seconds.', 'info');
    // startUserAccessListener's onSnapshot already shows a success toast and
    // unlocks content the moment the backend approves the payment. This is
    // just a safety-net re-check in case that snapshot lands a moment late,
    // and gives the user something to see immediately after returning from
    // Chapa checkout instead of a silent dashboard.
    let attempts = 0;
    const recheck = setInterval(async () => {
        attempts += 1;
        await checkPaymentStatus();
        if (attempts >= 4) clearInterval(recheck);
    }, 2000);
}

onAuthStateChanged(auth, async (user) => {
    if (BMT_PREVIEW_ROLE) {
        if (!user) {
            if (_bmtPreviewSignInAttempted) return; // avoid retry-looping on a real failure
            _bmtPreviewSignInAttempted = true;
            try {
                if (!BMT_PREVIEW_UID) throw new Error('Preview link is missing its target account.');
                await setPersistence(auth, inMemoryPersistence); // never remembered past this tab
                const r = await fetch(`/api/admin/preview-signin?role=student&uid=${encodeURIComponent(BMT_PREVIEW_UID)}&token=${encodeURIComponent(BMT_PREVIEW_TOKEN)}`);
                const d = await r.json().catch(() => ({}));
                if (!r.ok || !d.token) throw new Error(d.error || 'Preview link is invalid or expired.');
                await signInWithCustomToken(auth, d.token); // triggers this handler again, now with a real user
            } catch (e) {
                showToast(e.message, 'error');
            }
            return;
        }
        window.BMT_ADMIN_PREVIEW = true;
    }
    if (user) {
        try {
            const tokenResult = await getIdTokenResult(user, true);
            const claims = tokenResult.claims || {};
            if (claims.admin === true || claims.role === "admin") {
                await signOut(auth);
                showToast("This is an Admin account. Please use the Admin Panel instead.", "error");
                document.getElementById('authSection').style.display = 'block';
                document.getElementById('mainContent').style.display = 'none';
                return;
            }
        } catch (error) {
            console.error("Role verification failed:", error);
            await signOut(auth);
            showToast("Could not verify your account role. Please try again.", "error");
            return;
        }
    }
    if (user) {
        isAuthenticated = true;
        currentUser = user;
        window.dispatchEvent(new Event('BMTAuthReady'));
        document.getElementById('authSection').style.display = 'none';
        document.getElementById('mainContent').style.display = 'block';
        const bmtTop = document.getElementById('bmtLandingTop');
        if (bmtTop) bmtTop.style.display = 'none';
        await loadBmtPrices();
        loadOrgLogo();
        await loadUserDataAndAutoFill();
        await loadUserProfile();
        await loadStudentLearningMode();
        loadStudentAttendance();
        loadStudentMarkList();
        loadCompetitionDashboard();
        await loadStudentParentLinkCode();
        await loadStudentPrivateContacts();
        await loadStudentDevelopment();
        await loadPaymentHistory();
        await checkPaymentStatus();
        startUserAccessListener(user.uid);
        handlePaymentReturn();
        renderDownloadedVideos();
        renderPlaylist();
        const classSelect = document.getElementById('classSelect');
        if (classSelect) {
            const selectedClass = classSelect.value || 'KG';
            await loadStudentContent(selectedClass);
            await loadStudentVideos(selectedClass);
            await loadStudentExams(selectedClass);
            await window.loadExamHistory?.();
            setTimeout(() => window.loadExamArchive?.(), 300);
            setupRealtimeChat(selectedClass);
        }
        if (BMT_PREVIEW_ROLE) applyStudentPreviewSafeguards();
    } else {
        isAuthenticated = false;
        currentUser = null;
        currentUserData = null;
        location.href = '/auth?role=student&next=' + encodeURIComponent(location.pathname);
    }
});

// ==========================================================
// 📝 REGISTRATION
// ==========================================================
function withTimeout(promise, ms=15000, message='Request timed out. Please check your internet connection and try again.') {
    return Promise.race([promise, new Promise((_, reject) => setTimeout(() => reject(new Error(message)), ms))]);
}

async function registerStudent() {
    const name = document.getElementById('regName')?.value.trim();
    const email = document.getElementById('regEmail')?.value.trim();
    const password = document.getElementById('regPassword')?.value;
    const className = document.getElementById('regClass')?.value;
    const confirmPassword = document.getElementById('regConfirmPassword')?.value;

    if (!name || !email || !password || !className) {
        const status = document.getElementById('registerStatus'); if (status) status.textContent = 'Please fill in all required fields.';
        showToast("Please fill in all fields.", "error");
        return;
    }
    if (password.length < 6) {
        showToast("Password must be at least 6 characters.", "error");
        return;
    }
    if (password !== confirmPassword) {
        showToast("Passwords do not match.", "error");
        return;
    }

    let createdAuthUser = null;
    try {
        const status = document.getElementById('registerStatus');
        const btn = document.getElementById('registerBtn');
        if (status) { status.textContent = 'Creating your account…'; status.style.color = ''; }
        if (btn) { btn.disabled = true; btn.textContent = 'Creating account…'; }
        showLoadingOverlay('Creating account...');
        const userCredential = await withTimeout(createUserWithEmailAndPassword(auth, email, password), 15000, 'Account creation timed out. Please check your connection and try again.');
        const user = userCredential.user;
        createdAuthUser = user;

        await withTimeout(setDoc(doc(db, "users", user.uid), {
            uid: user.uid,
            name: name,
            email: email,
            class: className,
            registeredAt: serverTimestamp(),
            progress: { videos: {}, quizzes: {} },
            payments: [],
            isAdmin: false,
            profileImage: "",
            bio: "",
            freeTrial: {
                isActive: true,
                startedAt: serverTimestamp(),
                expiresAt: new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString(),
                daysRemaining: 7,
                usedDays: 0
            },
            isPaid: false,
            accountType: 'student'
        }), 15000, 'Profile creation timed out. Please check Firebase access and try again.');

        hideLoadingOverlay();
        if (status) { status.textContent = '✅ Registration successful. Your account is ready.'; status.style.color = 'var(--success, #16a34a)'; }
        if (btn) btn.disabled = false;
        showToast("Registration successful! Welcome!", "success");
    } catch (error) {
        // If Auth creation succeeded but the profile write failed, remove the
        // orphaned Auth account so the user can retry registration cleanly.
        if (createdAuthUser) {
            try { await deleteUser(createdAuthUser); } catch (cleanupError) {
                console.warn('Could not clean up incomplete registration:', cleanupError);
            }
        }
        hideLoadingOverlay();
        const btn = document.getElementById('registerBtn'); if (btn) { btn.disabled = false; btn.textContent = 'Create Student Account'; }
        console.error("Registration error:", error);
        let msg = "Registration failed. ";
        if (error.code === 'auth/email-already-in-use') msg += "Email already in use.";
        else if (error.code === 'auth/weak-password') msg += "Password too weak.";
        else if (error.code === 'auth/invalid-email') msg += "Please enter a valid email address.";
        else if (error.code === 'auth/network-request-failed') msg += "Network connection failed. Check your internet and try again.";
        else if (error.code === 'permission-denied' || error.code === 'firestore/permission-denied') msg += "Firebase denied profile creation. Check Firestore rules/deployment.";
        else msg += error.message;
        showToast(msg, "error");
    }
}

// The page uses inline form submit handlers. Because this file is an ES module,
// module-scoped functions are not automatically available on window. Export
// these two auth handlers explicitly so the Login/Register buttons work.
window.registerStudent = registerStudent;
window.logoutUser = logoutUser;

// Bind registration from the ES module itself. This avoids relying on inline
// HTML handlers and guarantees the button works even when stricter CSP is used.
const studentRegisterForm = document.getElementById('studentRegisterForm');
if (studentRegisterForm) {
    studentRegisterForm.addEventListener('submit', (event) => {
        event.preventDefault();
        registerStudent();
    });
}

window.loadBookmarks = loadBookmarks;
window.toggleBookmark = toggleBookmark;
window.getBookmarks = () => _bookmarks;
window.sendChatMessageWithReply = sendChatMessageWithReply;
window.cancelReply = cancelReply;

// ==========================================================
// 🔐 LOGIN / LOGOUT
// ==========================================================
async function loginStudentUnified(identity, secret, status, btn) {
    const value = String(identity || '').trim();
    const pass = String(secret || '');
    if (!value || !pass) { if(status) status.textContent='❌ Enter your email/phone and password/PIN.'; return; }
    const looksPhone = /^[+0-9][0-9\s().-]{7,}$/.test(value) && !value.includes('@');
    if (btn) { btn.disabled=true; btn.textContent='Signing in…'; }
    if (status) status.textContent='Checking your account…';
    try {
        if (looksPhone) {
            if (!/^[0-9]{6,8}$/.test(pass)) throw new Error('Phone sign-in requires a 6–8 digit PIN.');
            const r=await fetch('/api/auth/phone-pin/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:value,pin:pass,role:'student'})});
            const d=await r.json().catch(()=>({})); if(!r.ok) throw new Error(d.error||'Phone sign-in failed.');
            await signInWithCustomToken(auth,d.token);
        } else {
            await withTimeout(signInWithEmailAndPassword(auth,value,pass),15000,'Sign-in timed out. Please check your connection and try again.');
        }
        if(status) status.textContent='✅ Signed in. Loading your dashboard…';
        showToast('Welcome back!','success');
    } catch(e) { if(status) status.textContent='❌ '+(e?.message||'Sign-in failed.'); showToast(e?.message||'Sign-in failed.','error'); }
    finally { if(btn){btn.disabled=false;btn.textContent='Sign In';} }
}

const studentUnifiedLoginForm=document.getElementById('studentUnifiedLoginForm');
if(studentUnifiedLoginForm && !studentUnifiedLoginForm.dataset.bmtUnifiedAuthBound){
    studentUnifiedLoginForm.dataset.bmtUnifiedAuthBound='1';
    studentUnifiedLoginForm.addEventListener('submit',e=>{e.preventDefault();loginStudentUnified(document.getElementById('studentLoginIdentity')?.value,document.getElementById('studentLoginSecret')?.value,document.getElementById('studentUnifiedLoginStatus'),document.getElementById('studentUnifiedLoginBtn'));});
}

async function loadStudentParentLinkCode(){
 const box=document.getElementById('parentLinkCodeDisplay'); if(!box||!auth.currentUser)return;
 try{const token=await auth.currentUser.getIdToken(true);const r=await fetch('/api/student/parent-link',{headers:{Authorization:`Bearer ${token}`}});const d=await r.json();if(!r.ok)throw Error(d.error||'Unable to load parent code.');box.textContent=d.code||'—';}catch(e){box.textContent='—';document.getElementById('parentLinkCodeStatus').textContent='❌ '+e.message;}
}
async function regenerateStudentParentLinkCode(){
 try{const token=await auth.currentUser.getIdToken(true);const r=await fetch('/api/student/parent-link/regenerate',{method:'POST',headers:{Authorization:`Bearer ${token}`}});const d=await r.json();if(!r.ok)throw Error(d.error||'Unable to create a new code.');document.getElementById('parentLinkCodeDisplay').textContent=d.code;document.getElementById('parentLinkCodeStatus').textContent='✅ New parent link code created.';}catch(e){document.getElementById('parentLinkCodeStatus').textContent='❌ '+e.message;}
}
async function loadStudentPrivateContacts(){
 const box=document.getElementById('studentPrivateContacts');if(!box||!auth.currentUser)return;
 try{const token=await auth.currentUser.getIdToken(true);const r=await fetch('/api/student/contacts',{headers:{Authorization:`Bearer ${token}`}});const d=await r.json();if(!r.ok)throw Error(d.error||'Unable to load contacts.');const rows=d.contacts||[];box.innerHTML=rows.length?rows.map(c=>`<button type="button" class="card" data-private-contact="${escapeHtml(c.uid)}" style="text-align:left;border:1px solid var(--bmt-line);cursor:pointer"><strong>${c.role==='parent'?'👨‍👩‍👧':'👨‍🏫'} ${escapeHtml(c.name)}</strong><div class="subtitle">${escapeHtml(c.role)}</div></button>`).join(''):'<p class="subtitle">No verified private contacts yet. Connect a parent or wait for a teacher relationship to appear.</p>';box.querySelectorAll('[data-private-contact]').forEach(b=>b.onclick=()=>openStudentPrivateChat(b.dataset.privateContact,b.querySelector('strong')?.textContent||'Private chat'));}catch(e){box.innerHTML=`<p class="error-text">${escapeHtml(e.message)}</p>`;}
}
async function openStudentPrivateChat(uid,name){window._studentPrivateUid=uid;document.getElementById('studentPrivateChat').style.display='block';document.getElementById('studentPrivateChatTitle').textContent=name;await loadStudentPrivateMessages();}
async function loadStudentPrivateMessages(){const uid=window._studentPrivateUid;if(!uid||!auth.currentUser)return;try{const token=await auth.currentUser.getIdToken(true);const r=await fetch('/api/messages/'+encodeURIComponent(uid),{headers:{Authorization:`Bearer ${token}`}});const d=await r.json();if(!r.ok)throw Error(d.error||'Unable to load messages.');const box=document.getElementById('studentPrivateChatMessages');box.innerHTML=(d.messages||[]).map(m=>`<div class="item-row"><strong>${escapeHtml(m.senderName||'User')}</strong><div>${escapeHtml(m.message||'')}</div><div class="subtitle">${m.createdAt?new Date(m.createdAt).toLocaleString():''}</div></div>`).join('')||'<p class="subtitle">No messages yet.</p>';box.scrollTop=box.scrollHeight;}catch(e){document.getElementById('studentPrivateChatMessages').innerHTML=`<p class="error-text">${escapeHtml(e.message)}</p>`;}}
async function sendStudentPrivateMessage(){const uid=window._studentPrivateUid,input=document.getElementById('studentPrivateChatInput');if(!uid||!input||!input.value.trim())return;try{const token=await auth.currentUser.getIdToken(true);const r=await fetch('/api/messages/'+encodeURIComponent(uid),{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${token}`},body:JSON.stringify({message:input.value.trim()})});const d=await r.json();if(!r.ok)throw Error(d.error||'Unable to send message.');input.value='';await loadStudentPrivateMessages();}catch(e){showToast(e.message,'error');}}

async function loginStudent() {
    const email = document.getElementById('loginEmail')?.value.trim();
    const password = document.getElementById('loginPassword')?.value;
    if (!email || !password) {
        showToast("Please enter email and password.", "error");
        return;
    }
    try {
        showLoadingOverlay('Signing in...');
        await withTimeout(signInWithEmailAndPassword(auth, email, password), 15000, 'Sign-in timed out. Please check your connection and try again.');
        hideLoadingOverlay();
        showToast("Welcome back!", "success");
    } catch (error) {
        hideLoadingOverlay();
        console.error("Login error:", error);
        showToast("Login failed. Please check your credentials.", "error");
    }
}

async function phonePinLoginStudent() {
    const phone = document.getElementById('phoneLoginNumber')?.value.trim();
    const pin = document.getElementById('phoneLoginPin')?.value.trim();
    const otp = document.getElementById('phoneLoginOtp')?.checked;
    const status = document.getElementById('phoneLoginStatus');
    const btn = document.getElementById('phoneLoginBtn');
    if (!phone || !/^[0-9]{6,8}$/.test(pin)) { if(status) status.textContent='❌ Enter your phone number and 6–8 digit PIN.'; return; }
    if(btn){btn.disabled=true;btn.textContent='Signing in…';}
    try {
        const r=await fetch('/api/auth/phone-pin/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone,pin,role:'student'})});
        const d=await r.json().catch(()=>({}));
        if(!r.ok) throw new Error(d.error||'Phone sign-in failed.');
        await signInWithCustomToken(auth,d.token);
        if(status) status.textContent='✅ Signed in with phone + PIN.';
        if(otp) await startOptionalPhoneOtp(phone,status);
        showToast('Welcome back!','success');
    } catch(e){ if(status) status.textContent='❌ '+e.message; showToast(e.message,'error'); }
    finally{if(btn){btn.disabled=false;btn.textContent='Sign In';}}
}

async function registerStudentPhonePin() {
    const name=document.getElementById('phoneRegName')?.value.trim();
    const phone=document.getElementById('phoneRegNumber')?.value.trim();
    const pin=document.getElementById('phoneRegPin')?.value.trim();
    const confirm=document.getElementById('phoneRegPinConfirm')?.value.trim();
    const className=document.getElementById('regClass')?.value;
    const status=document.getElementById('phoneRegisterStatus'); const btn=document.getElementById('phoneRegisterBtn');
    if(!name||!phone||!/^[0-9]{6,8}$/.test(pin)||pin!==confirm||!className){if(status)status.textContent='❌ Complete all fields and make sure the PINs match.';return;}
    if(btn){btn.disabled=true;btn.textContent='Creating…';}
    try{const r=await fetch('/api/auth/phone-pin/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({role:'student',name,phone,pin,className})});const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.error||'Phone registration failed.');if(status)status.textContent='✅ Account created. You can now sign in with phone + PIN.';showToast('Student account created.','success');document.getElementById('phoneLoginNumber').value=phone;switchTab('login');document.querySelectorAll('[data-login-method]').forEach(x=>x.classList.remove('active'));document.querySelector('[data-login-method="phone"]')?.classList.add('active');document.getElementById('emailLoginBox').style.display='none';document.getElementById('phoneLoginBox').style.display='block';}
    catch(e){if(status)status.textContent='❌ '+e.message;showToast(e.message,'error');}finally{if(btn){btn.disabled=false;btn.textContent='Create Student Account';}}
}

async function startOptionalPhoneOtp(phone,status){
    try{
        if(!window.bmtRecaptcha){window.bmtRecaptcha=new RecaptchaVerifier(auth,'phoneOtpBtn',{size:'invisible'});}
        const normalized=phone.startsWith('+251')?phone:phone.startsWith('0')?'+251'+phone.slice(1):'+251'+phone;
        const confirmation=await signInWithPhoneNumber(auth,normalized,window.bmtRecaptcha);
        const code=window.prompt('Enter the 6-digit SMS code sent to your phone:');
        if(!code) return;
        await confirmation.confirm(code.trim());
        const token=await auth.currentUser.getIdToken(true);
        await fetch('/api/auth/phone-pin/mark-verified',{method:'POST',headers:{Authorization:`Bearer ${token}`}});
        if(status)status.textContent='✅ Phone number verified by SMS.';
    }catch(e){if(status)status.textContent='⚠️ PIN login succeeded, but SMS verification was not completed.';console.warn('Optional OTP:',e);}
}

async function forgotPinStudent(){
    // V31.108 fix: phone+PIN accounts had no way to recover a forgotten PIN
    // (the shared "Forgot Password?" modal only emails a reset link, which
    // does nothing for an account with no email/password). Reuses Firebase
    // Phone Auth OTP -- signInWithPhoneNumber signs into the *same* uid for
    // a phone that already has an account, so a successful OTP confirmation
    // proves ownership and yields a token /api/auth/phone-pin/reset accepts.
    // Builds its own invisible reCAPTCHA container instead of depending on
    // a fixed page element, so it works from any dashboard's login screen.
    const phone=window.prompt('Enter the phone number on your account:');
    if(!phone) return;
    let container=document.getElementById('bmtPinResetRecaptcha');
    if(!container){container=document.createElement('div');container.id='bmtPinResetRecaptcha';container.style.display='none';document.body.appendChild(container);}
    try{
        const verifier=new RecaptchaVerifier(auth,'bmtPinResetRecaptcha',{size:'invisible'});
        const normalized=phone.startsWith('+251')?phone:phone.startsWith('0')?'+251'+phone.slice(1):'+251'+phone;
        const confirmation=await signInWithPhoneNumber(auth,normalized,verifier);
        const code=window.prompt('Enter the 6-digit SMS code sent to your phone:');
        if(!code) return;
        await confirmation.confirm(code.trim());
        const newPin=window.prompt('Enter your new 6–8 digit PIN:');
        if(!newPin||!/^[0-9]{6,8}$/.test(newPin)){showToast('PIN must be 6–8 digits.','error');return;}
        const token=await auth.currentUser.getIdToken(true);
        const r=await fetch('/api/auth/phone-pin/reset',{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${token}`},body:JSON.stringify({pin:newPin})});
        const d=await r.json().catch(()=>({}));
        if(!r.ok) throw new Error(d.error||'Could not reset PIN.');
        showToast('PIN reset. Sign in with your new PIN.','success');
    }catch(e){showToast(e.message||'Could not reset PIN.','error');}
}

window.phonePinLoginStudent=phonePinLoginStudent;
window.registerStudentPhonePin=registerStudentPhonePin;
window.forgotPinStudent=forgotPinStudent;

async function logoutUser() {
    try {
        await signOut(auth);
        showToast("Logged out successfully.", "info");
    } catch (error) {
        console.error("Logout error:", error);
        showToast("Failed to logout.", "error");
    }
}

// ==========================================================
// 📚 CLASS SELECTOR
// ==========================================================
function renderClassChips(selectedClass = 'KG') {
    const container = document.getElementById('classChips');
    if (!container) return;
    container.innerHTML = '';
    CLASSES.forEach(cls => {
        const chip = document.createElement('div');
        chip.className = `class-chip ${cls === selectedClass ? 'active' : ''}`;
        chip.textContent = `Grade ${cls}`;
        chip.dataset.value = cls;
        chip.onclick = () => selectClass(cls);
        container.appendChild(chip);
    });
}

function selectClass(className) {
    document.querySelectorAll('.class-chip').forEach(chip => {
        chip.classList.toggle('active', chip.dataset.value === className);
    });
    const select = document.getElementById('classSelect');
    if (select) {
        select.value = className;
        const event = new Event('change');
        select.dispatchEvent(event);
    }
    localStorage.setItem('selectedClass', className);
    loadStudentContent(className);
    loadStudentVideos(className);
    loadStudentExams(className);
    setupRealtimeChat(className);
}

// ==========================================================
// 👤 USER DATA AUTO-FILL
// ==========================================================
async function loadUserDataAndAutoFill() {
    const user = auth.currentUser;
    if (!user) return;
    try {
        const docSnap = await getDoc(doc(db, "users", user.uid));
        let userData = {};
        if (docSnap.exists()) {
            userData = docSnap.data();
            currentUserData = userData;
        } else {
            userData = {
                name: user.displayName || user.email || "Student",
                email: user.email || "",
                class: "KG",
                bio: "",
                profileImage: ""
            };
            currentUserData = userData;
        }
        const nameInput = document.getElementById('chatUserName');
        if (nameInput) {
            nameInput.value = userData.name || user.displayName || user.email || "Student";
        }
        if (userData.class) {
            renderClassChips(userData.class);
            const select = document.getElementById('classSelect');
            if (select) select.value = userData.class;
        }
        const greetingName = document.getElementById('greetingName');
        if (greetingName) {
            greetingName.textContent = userData.name || user.displayName || "Student";
        }
        const navAvatar = document.getElementById('navAvatar');
        if (navAvatar) {
            if (userData.profileImage) {
                navAvatar.src = userData.profileImage;
            } else {
                const name = userData.name || user.displayName || "User";
                navAvatar.src = `https://ui-avatars.com/api/?name=${encodeURIComponent(name)}&background=6C63FF&color=fff&size=64`;
            }
        }
    } catch (error) {
        console.error('Load user data error:', error);
    }
}

// ==========================================================
// 👤 PROFILE
// ==========================================================
function openProfileModal() {
    const modal = document.getElementById('profileModal');
    if (modal) modal.classList.add('active');
    loadCurrentProfile();
}

function closeProfileModal() {
    const modal = document.getElementById('profileModal');
    if (modal) modal.classList.remove('active');
    _profileFile = null;
}

async function loadCurrentProfile() {
    const user = auth.currentUser;
    if (!user) return;
    try {
        const docSnap = await getDoc(doc(db, "users", user.uid));
        if (docSnap.exists()) {
            const data = docSnap.data();
            const name = data.name || user.displayName || user.email;
            const preview = document.getElementById('profilePreview');
            if (preview) {
                if (data.profileImage) {
                    preview.src = data.profileImage;
                    preview.classList.add('has-image');
                } else {
                    preview.src = `https://ui-avatars.com/api/?name=${encodeURIComponent(name)}&background=6C63FF&color=fff&size=128`;
                    preview.classList.remove('has-image');
                }
            }
            document.getElementById('modalName').value = data.name || '';
            document.getElementById('modalBio').value = data.bio || '';
        }
    } catch (error) {
        console.error('Load profile error:', error);
    }
}

function previewProfilePicture(file) {
    if (!file) return;
    const reader = new FileReader();
    const preview = document.getElementById('profilePreview');
    if (!preview) return;
    reader.onload = (e) => {
        preview.src = e.target.result;
        preview.classList.add('has-image');
        _profileFile = file;
    };
    reader.readAsDataURL(file);
}

function compressImage(file, maxWidth, maxHeight) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.readAsDataURL(file);
        reader.onload = (e) => {
            const img = new Image();
            img.src = e.target.result;
            img.onload = () => {
                const canvas = document.createElement('canvas');
                let width = img.width;
                let height = img.height;
                if (width > maxWidth) {
                    height = (maxWidth / width) * height;
                    width = maxWidth;
                }
                if (height > maxHeight) {
                    width = (maxHeight / height) * width;
                    height = maxHeight;
                }
                canvas.width = width;
                canvas.height = height;
                const ctx = canvas.getContext('2d');
                ctx.imageSmoothingEnabled = true;
                ctx.imageSmoothingQuality = 'high';
                ctx.drawImage(img, 0, 0, width, height);
                canvas.toBlob((blob) => {
                    resolve(blob);
                }, 'image/jpeg', 0.85);
            };
            img.onerror = reject;
        };
        reader.onerror = reject;
    });
}

// ==========================================================
// 💾 SAVE PROFILE — ምስ Cloudinary/Google Drive
// ==========================================================
async function saveProfile() {
    const user = auth.currentUser;
    if (!user) {
        showToast('Please sign in first.', 'error');
        return;
    }
    const name = document.getElementById('modalName')?.value.trim();
    const bio = document.getElementById('modalBio')?.value.trim();
    const file = _profileFile || null;
    showLoadingOverlay('Saving profile...');
    try {
        const updates = {};
        if (name) updates.name = name;
        if (bio) updates.bio = bio;
        if (file) {
            // ✅ Upload to Google Drive (images) or Cloudinary
            const { uploadFile } = await import('./storage-service.js');
            const result = await uploadFile(file, 'images');
            if (result.success) {
                updates.profileImage = result.url;
                _profileFile = null;
            } else {
                throw new Error(result.error);
            }
        }
        await updateDoc(doc(db, "users", user.uid), updates);
        hideLoadingOverlay();
        showToast('✅ Profile updated successfully!', 'success');
        closeProfileModal();
        loadUserProfile();
        loadUserDataAndAutoFill();
    } catch (error) {
        hideLoadingOverlay();
        console.error('Save profile error:', error);
        showToast('Failed to save profile: ' + error.message, 'error');
    }
}

async function deleteProfilePicture() {
    if (!confirm('Are you sure you want to remove your profile picture?')) return;
    const user = auth.currentUser;
    if (!user) return;
    try {
        await updateDoc(doc(db, "users", user.uid), {
            profileImage: null
        });
        showToast('Profile picture removed.', 'info');
        loadUserProfile();
        loadUserDataAndAutoFill();
        const preview = document.getElementById('profilePreview');
        if (preview) {
            const name = document.getElementById('modalName')?.value || user.email;
            preview.src = `https://ui-avatars.com/api/?name=${encodeURIComponent(name)}&background=6C63FF&color=fff&size=128`;
            preview.classList.remove('has-image');
        }
    } catch (error) {
        console.error('Delete profile picture error:', error);
        showToast('Failed to remove profile picture.', 'error');
    }
}

async function loadUserProfile() {
    const user = auth.currentUser;
    if (!user) return;
    try {
        const docSnap = await getDoc(doc(db, "users", user.uid));
        if (docSnap.exists()) {
            const data = docSnap.data();
            const name = data.name || user.displayName || user.email;
            const profileImg = document.getElementById('profileImage');
            if (profileImg) {
                if (data.profileImage) {
                    profileImg.src = data.profileImage;
                } else {
                    profileImg.src = `https://ui-avatars.com/api/?name=${encodeURIComponent(name)}&background=6C63FF&color=fff&size=128`;
                }
            }
            const navAvatar = document.getElementById('navAvatar');
            if (navAvatar) {
                if (data.profileImage) {
                    navAvatar.src = data.profileImage;
                } else {
                    navAvatar.src = `https://ui-avatars.com/api/?name=${encodeURIComponent(name)}&background=6C63FF&color=fff&size=64`;
                }
            }
            document.getElementById('profileName').textContent = name;
            document.getElementById('profileEmail').textContent = data.email || user.email;
            document.getElementById('profileClass').textContent = data.class || "Not set";
            document.getElementById('profileBio').textContent = data.bio || "No bio yet";
            const progress = data.progress || { videos: {}, quizzes: {} };
            const totalVideos = Object.keys(progress.videos || {}).length;
            const watchedVideos = Object.values(progress.videos || {}).filter(v => v.watched).length;
            const totalQuizzes = Object.keys(progress.quizzes || {}).length;
            document.getElementById('progressStats').innerHTML = `
                <div class="stat-item">📹 Videos: ${watchedVideos}/${totalVideos}</div>
                <div class="stat-item">✍️ Quizzes: ${totalQuizzes} completed</div>
            `;
        }
    } catch (error) {
        console.error("Profile load error:", error);
    }
}

// ==========================================================
// 📚 CONTENT LOADING
// ==========================================================
async function loadStudentContent(selectedClass) {
    const booksContainer = document.getElementById('booksContainer');
    const quizContainer = document.getElementById('quizContainer');
    if (booksContainer) booksContainer.innerHTML = '<div class="spinner"></div> Loading books...';
    if (quizContainer) quizContainer.innerHTML = '<div class="spinner"></div> Loading quizzes...';
    try {
        if (booksContainer) {
            // V31.108 security fix: this used to query the `books`
            // collection directly with the client Firestore SDK, which
            // required a public/authenticated-only Firestore rule that a
            // student could use to read every grade's (and every targeted
            // Distance/Scholarship) book directly, bypassing the grade and
            // Learning Mode/Audience checks server-side. Now goes through
            // the same server-authorized /api/library path the Digital
            // Library search panel already uses. Rendering, bookmark and
            // GCS open/download logic below is unchanged.
            const libToken = await auth.currentUser.getIdToken(true);
            const libResponse = await fetch(`/api/library?className=${encodeURIComponent(selectedClass)}`, {
                headers: { 'Authorization': `Bearer ${libToken}` }
            });
            const libPayload = await libResponse.json().catch(() => ({}));
            if (!libResponse.ok) throw new Error(libPayload.error || 'Unable to load library.');
            const libraryItems = Array.isArray(libPayload.items) ? libPayload.items : [];
            booksContainer.innerHTML = "";
            if (!libraryItems.length) {
                booksContainer.innerHTML = "No books found for this class.";
            } else {
                libraryItems.forEach(data => {
                    const safeTitle = String(data.title || 'Book').replace(/[<>"'&]/g, '');
                    const previewUrl = data.previewUrl || data.url || '';
                    const downloadUrl = data.downloadUrl || data.url || '';
                    const isDrive = data.storageProvider === 'google_drive';
                    const isGcs = data.storageProvider === 'gcs' && (data.storagePath || data.fileId);
                    const gcsPath = encodeURIComponent(data.storagePath || data.fileId || '');
                    const coverUrl = String(data.coverUrl || '').trim();
                    const sourceLabel = isGcs ? 'Cloud Storage' : isDrive ? 'Drive' : '';
                    const safeCoverUrl = coverUrl.replace(/"/g,'&quot;');
                    const cover = coverUrl
                        ? `<img class="library-cover-image" src="${safeCoverUrl}" alt="" loading="lazy">`
                        : `<div class="library-cover-fallback" aria-hidden="true">📚</div>`;
                    booksContainer.innerHTML += `<article class="library-card">
                        <div class="library-cover">${cover}<div class="library-cover-title">${safeTitle}</div>${sourceLabel ? `<span class="library-cover-badge">${sourceLabel}</span>` : ''}</div>
                        <div class="library-card-body">
                            <strong class="library-card-name">${safeTitle}</strong>
                            <div class="library-card-actions">
                                ${isGcs ? `<button type="button" class="btn btn-outline" data-gcs-open="${gcsPath}">Open</button><button type="button" class="btn btn-primary" data-gcs-download="${gcsPath}">Download</button>` : `${previewUrl ? `<a href="${previewUrl}" target="_blank" rel="noopener" class="btn btn-outline">${isDrive ? 'Preview' : 'Open'}</a>` : ''}${downloadUrl ? `<a href="${downloadUrl}" target="_blank" rel="noopener" class="btn btn-primary">Download</a>` : ''}`}
                                <button class="library-star-btn" type="button" data-bookmark-id="${data.id}" data-bookmark-title="${safeTitle}" data-bookmark-type="book" data-bookmark-url="${String(previewUrl).replace(/"/g,'&quot;')}" data-bookmark-storage-path="${isGcs ? String(data.storagePath || data.fileId || '').replace(/"/g,'&quot;') : ''}" data-bookmark-class="${String(selectedClass).replace(/"/g,'&quot;')}" aria-label="Save book">🔖</button>
                            </div>
                        </div>
                    </article>`;
                });
            }
            booksContainer.querySelectorAll('[data-gcs-open],[data-gcs-download]').forEach(button => {
                button.addEventListener('click', async () => {
                    try {
                        const token = await auth.currentUser.getIdToken(true);
                        const path = decodeURIComponent(button.dataset.gcsOpen || button.dataset.gcsDownload || '');
                        const response = await fetch('/api/storage/document-url', {method:'POST', headers:{'Content-Type':'application/json','Authorization':`Bearer ${token}`}, body:JSON.stringify({storagePath:path})});
                        const data = await response.json().catch(()=>({}));
                        if (!response.ok || !data.url) throw new Error(data.error || 'Could not open document.');
                        window.open(data.url, '_blank', 'noopener');
                    } catch (error) { console.error(error); alert(error.message || 'Could not open document.'); }
                });
            });
        }
        if (quizContainer) {
            try {
                const token = await auth.currentUser?.getIdToken(true);
                if (!token) throw new Error('Authentication required.');
                const response = await fetch(`${window.BMT_API_BASE || ''}/api/quizzes?className=${encodeURIComponent(selectedClass)}`, {
                    headers: { 'Authorization': `Bearer ${token}` }
                });
                const payload = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(payload.error || 'Unable to load quizzes.');
                const quizzes = Array.isArray(payload.quizzes) ? payload.quizzes : [];
                quizContainer.innerHTML = "";
                if (!quizzes.length) {
                    quizContainer.innerHTML = "No quizzes found for this class.";
                } else {
                    quizzes.forEach((quiz, index) => {
                        const quizId = String(quiz.id || '');
                        const options = quiz.options && typeof quiz.options === 'object' ? quiz.options : {};
                        let optionsHtml = '';
                        Object.entries(options).forEach(([key, value]) => {
                            optionsHtml += `
                                <label style="display:block; margin:8px 0; cursor:pointer;">
                                    <input type="radio" name="quiz_${quizId}" value="${escapeHtml(key)}" style="margin-right:8px;">
                                    <strong>${escapeHtml(key)}.</strong> ${escapeHtml(value)}
                                </label>
                            `;
                        });
                        const imageHtml = quiz.imageUrl ? `<img src="${escapeHtml(quiz.imageUrl)}" alt="Quiz illustration" style="max-width:100%; max-height:200px; border-radius:var(--radius-sm); margin-top:8px;">` : "";
                        quizContainer.innerHTML += `
                            <div class="quiz-card" data-quiz="${escapeHtml(quizId)}" style="background:var(--surface); padding:16px; border-radius:var(--radius-md); border:1px solid var(--border); margin-bottom:16px;">
                                <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
                                    <h3 style="margin:0; color:var(--primary);">${index + 1}. ${escapeHtml(quiz.title || "Quiz")}</h3>
                                    <span id="timer_${escapeHtml(quizId)}" style="font-weight:bold; color:var(--warning);">⏱️ 0:00</span>
                                </div>
                                <div class="quiz-question" data-quiz="${escapeHtml(quizId)}" style="margin-top:10px;">
                                    <p style="font-weight:500; white-space:pre-wrap;">${escapeHtml(quiz.question || '')}</p>
                                    ${imageHtml}
                                    <div style="margin:12px 0;">${optionsHtml}</div>
                                    <div id="result_${escapeHtml(quizId)}" style="font-weight:bold; margin-top:8px; min-height:24px;"></div>
                                </div>
                                <button class="btn btn-primary" onclick="submitQuiz('${escapeHtml(quizId)}')" style="margin-top:10px; width:100%;">Submit Quiz</button>
                                <div id="quizResult_${escapeHtml(quizId)}"></div>
                            </div>
                        `;
                        startQuizTimer(quizId, 1);
                    });
                }
            } catch (error) {
                console.error('Quiz loading error:', error);
                quizContainer.innerHTML = 'Unable to load quizzes.';
            }
        }
    } catch (error) {
        console.error('Error loading student content:', error);
    }
    setupContentSearch(); bindBookmarkButtons();
}

// ==========================================================
// 🎥 VIDEOS
// ==========================================================
async function loadStudentVideos(selectedClass) {
    const studentVideosContainer = document.getElementById('studentVideosContainer');
    if (!studentVideosContainer) return;
    studentVideosContainer.innerHTML = '<div class="spinner"></div> Loading videos...';
    const user = auth.currentUser;
    if (!user) {
        studentVideosContainer.innerHTML = 'Please sign in to view videos.';
        return;
    }
    try {
        const token = await user.getIdToken(true);
        const response = await fetch(`${window.BMT_API_BASE || ''}/api/videos?className=${encodeURIComponent(selectedClass)}`, {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.error || 'Unable to load videos.');
        const videos = Array.isArray(payload.videos) ? payload.videos : [];
        const hasFreeTrial = payload.freeTrialActive === true;
        const isPaid = payload.premium === true;
        if (!videos.length) {
            studentVideosContainer.innerHTML = "No videos found for this class.";
            return;
        }
        studentVideosContainer.innerHTML = '';
        if (hasFreeTrial && !isPaid) {
            studentVideosContainer.innerHTML += `
                <div style="background:var(--success-light); padding:12px; border-radius:var(--radius-md); margin-bottom:16px; border:2px solid var(--success);">
                    🎉 <strong>Free Trial Active!</strong> You can access eligible trial content.
                </div>
            `;
        } else if (!isPaid) {
            studentVideosContainer.innerHTML += `
                <div style="background:var(--warning-light); padding:12px; border-radius:var(--radius-md); margin-bottom:16px; border:2px solid var(--warning);">
                    🔒 <strong>Premium content is locked.</strong> Subscribe to access paid content.
                </div>
            `;
        }
        videos.forEach((video) => {
            const videoId = String(video.id || '');
            const canAccess = video.accessible === true && !!video.url;
            const accessHtml = video.isPaid ? `<span class="badge badge-paid">🔒 Paid</span>` : `<span class="badge badge-free">🔓 Free</span>`;
            let bodyHtml;
            if (!canAccess) {
                bodyHtml = `
                    <div style="background:var(--danger-light); border:1px dashed var(--danger); border-radius:var(--radius-md); padding:20px; text-align:center;">
                        <div style="font-size:2em;">🔒</div>
                        <p style="font-weight:600; margin:10px 0;">Subscribe or use an eligible free trial to access this content.</p>
                        <button onclick="showToast('Please subscribe to access this content.', 'info')" class="btn btn-primary">💳 Subscribe</button>
                    </div>
                `;
            } else {
                const url = String(video.url || '');
                const title = escapeHtml(video.title || 'BMT video');
                bodyHtml = `
                    ${video.provider==='youtube' || /youtube\.com|youtu\.be/i.test(url) ? `<iframe id="video_${escapeHtml(videoId)}" width="100%" height="280" src="${escapeHtml(toEmbedUrl(url))}" title="${title}" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen style="border-radius:var(--radius-sm);background:#000;"></iframe>` : `<video id="video_${escapeHtml(videoId)}" controls style="width:100%; max-height:280px; border-radius:var(--radius-sm); background:#000;" preload="metadata"><source src="${escapeHtml(url)}" type="video/mp4"><p>Your browser doesn't support video playback.</p></video>`}
                    <div class="video-progress-bar"><div id="progress_${escapeHtml(videoId)}" class="progress-fill" style="width:0%;"></div></div>
                    <div style="margin-top:10px; display:flex; gap:8px; flex-wrap:wrap;">
                        <button class="btn btn-primary" style="flex:1;" onclick="downloadVideoLocally('${escapeHtml(url)}', '${String(video.title||'').replace(/'/g,"\\'")}')">📥 Save Offline</button>
                        <button class="btn btn-accent" style="flex:1;" onclick="addToPlaylist('${escapeHtml(videoId)}', '${String(video.title||'').replace(/'/g,"\\'")}')">➕ Add to Playlist</button>
                        <button class="btn btn-outline" style="flex:1;" data-bookmark-id="${escapeHtml(videoId)}" data-bookmark-title="${title}" data-bookmark-type="video" data-bookmark-url="${escapeHtml(url)}" data-bookmark-class="${escapeHtml(selectedClass)}">🔖 Save</button>
                    </div>
                `;
            }
            studentVideosContainer.innerHTML += `
                <div class="video-card" data-video-id="${escapeHtml(videoId)}" style="background:var(--surface); border:1px solid var(--border); border-radius:var(--radius-md); padding:16px; margin-bottom:16px;">
                    <div style="display:flex;justify-content:space-between;gap:8px;align-items:center;flex-wrap:wrap;"><h3 style="margin:0;">${escapeHtml(video.title || 'Video')}</h3>${accessHtml}</div>
                    ${video.description ? `<p class="subtitle">${escapeHtml(video.description)}</p>` : ''}
                    ${bodyHtml}
                </div>
            `;
        });
        bindBookmarkButtons();
        setupVideoProgressTracking();
    } catch (error) {
        console.error('Video loading error:', error);
        studentVideosContainer.innerHTML = 'Unable to load videos.';
    }
}

// ==========================================================
// 🎥 VIDEO PROGRESS
// ==========================================================
function setupVideoProgress(videoElement, videoId) {
    const savedProgress = localStorage.getItem(`video_progress_${videoId}`);
    if (savedProgress) {
        const progress = parseFloat(savedProgress);
        if (progress > 0 && progress < 95) {
            videoElement.currentTime = (progress / 100) * videoElement.duration;
        }
    }
    videoElement.addEventListener('timeupdate', () => {
        if (videoElement.duration > 0) {
            const progress = (videoElement.currentTime / videoElement.duration) * 100;
            localStorage.setItem(`video_progress_${videoId}`, progress.toString());
            updateVideoProgress(videoId, progress);
            const progressBar = document.getElementById(`progress_${videoId}`);
            if (progressBar) {
                progressBar.style.width = `${Math.min(progress, 100)}%`;
            }
        }
    });
    videoElement.addEventListener('ended', () => {
        localStorage.setItem(`video_progress_${videoId}`, '100');
        updateVideoProgress(videoId, 100, true);
        showToast('✅ Video completed!', 'success');
    });
    videoElement.addEventListener('play', () => {
        incrementVideoViews(videoId);
    });
    videoElement.addEventListener('error', (e) => {
        console.error('Video error:', e);
    });
}

async function updateVideoProgress(videoId, progress, watched = false) {
    const user = auth.currentUser;
    if (!user) return;
    try {
        const userRef = doc(db, "users", user.uid);
        const userSnap = await getDoc(userRef);
        const data = userSnap.exists() ? userSnap.data() : {};
        const currentProgress = data.progress || { videos: {}, quizzes: {} };
        if (!currentProgress.videos) currentProgress.videos = {};
        currentProgress.videos[videoId] = {
            progress: progress,
            watched: watched || progress >= 95,
            watchedAt: watched || progress >= 95 ? serverTimestamp() : null
        };
        await updateDoc(userRef, { progress: currentProgress });
    } catch (error) {
        console.error("Progress update error:", error);
    }
}

async function incrementVideoViews(videoId) {
    try {
        const videoRef = doc(db, "videos", videoId);
        await updateDoc(videoRef, { views: increment(1) });
    } catch (error) {
        console.error("View count error:", error);
    }
}

// ==========================================================
// ✍️ QUIZ FUNCTIONS
// ==========================================================
function startQuizTimer(quizId, totalQuestions) {
    const timerDisplay = document.getElementById(`timer_${quizId}`);
    if (!timerDisplay) return;
    let timeLeft = totalQuestions * 30;
    timerDisplay.textContent = `⏱️ ${formatTime(timeLeft)}`;
    if (quizTimer) clearInterval(quizTimer);
    quizTimer = setInterval(() => {
        timeLeft--;
        timerDisplay.textContent = `⏱️ ${formatTime(timeLeft)}`;
        if (timeLeft <= 0) {
            clearInterval(quizTimer);
            timerDisplay.textContent = '⏰ Time is up!';
            submitQuiz(quizId);
        }
    }, 1000);
}

function formatTime(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs.toString().padStart(2, '0')}`;
}

async function submitQuiz(quizId) {
    const questionDiv = document.querySelector(`.quiz-question[data-quiz="${CSS.escape(quizId)}"]`);
    if (!questionDiv || questionDiv.dataset.submitted === 'true') return;
    const selected = questionDiv.querySelector('input[type="radio"]:checked');
    const resultSpan = document.getElementById(`result_${quizId}`);
    if (!resultSpan) return;
    if (!selected) {
        resultSpan.style.color = 'var(--warning)';
        resultSpan.textContent = '⚠️ Please select an answer.';
        return;
    }
    const user = auth.currentUser;
    if (!user) {
        resultSpan.textContent = 'Please sign in first.';
        return;
    }
    try {
        questionDiv.dataset.submitted = 'true';
        const token = await user.getIdToken(true);
        const response = await fetch(`${window.BMT_API_BASE || ''}/api/quiz-results`, {
            method: 'POST',
            headers: {'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json'},
            body: JSON.stringify({quizId, selectedAnswer: selected.value})
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || 'Unable to submit quiz.');
        const correct = Number(data.score) === 1;
        resultSpan.style.color = correct ? 'var(--success)' : 'var(--danger)';
        resultSpan.textContent = correct ? '✅ Correct!' : '❌ Incorrect.';
        updateQuizScore(quizId, Number(data.score) || 0, Number(data.total) || 1);
        const resultContainer = document.getElementById(`quizResult_${quizId}`);
        if (resultContainer) {
            resultContainer.innerHTML = `<div style="background:var(--primary-light); padding:15px; border-radius:var(--radius-md); text-align:center; margin-top:15px;"><p style="font-weight:bold; margin-bottom:4px;">📊 Result</p><p style="font-size:1.2em;">Score: ${Number(data.score)||0}/${Number(data.total)||1}</p></div>`;
        }
        questionDiv.querySelectorAll('input[type="radio"]').forEach(input => input.disabled = true);
    } catch (error) {
        delete questionDiv.dataset.submitted;
        resultSpan.style.color = 'var(--danger)';
        resultSpan.textContent = escapeHtml(error.message || 'Quiz submission failed.');
    }
    if (quizTimer) clearInterval(quizTimer);
}

async function updateQuizScore(quizId, score, total) {
    const user = auth.currentUser;
    if (!user) return;
    try {
        const userRef = doc(db, "users", user.uid);
        const userSnap = await getDoc(userRef);
        const data = userSnap.exists() ? userSnap.data() : {};
        const currentProgress = data.progress || { videos: {}, quizzes: {} };
        if (!currentProgress.quizzes) currentProgress.quizzes = {};
        currentProgress.quizzes[quizId] = {
            score: score,
            total: total,
            completedAt: serverTimestamp()
        };
        await updateDoc(userRef, { progress: currentProgress });
    } catch (error) {
        console.error("Quiz score update error:", error);
    }
}

async function sendQuizResultToAdmin(quizId, selectedAnswer) {
    const user = auth.currentUser;
    if (!user) {
        showToast('Please sign in to send results.', 'error');
        return;
    }
    try {
        const token = await user.getIdToken();
        const response = await fetch(`${window.BMT_API_BASE || ''}/api/quiz-results`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${token}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ quizId, selectedAnswer })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || 'Failed to submit result.');
        showToast(`📤 Result sent to admin! Score: ${data.score}/${data.total}`, 'success');
    } catch (error) {
        console.error('Send result error:', error);
        showToast(error.message || 'Failed to send results.', 'error');
    }
}

// ==========================================================
// 📋 PLAYLIST
// ==========================================================
let playlist = JSON.parse(localStorage.getItem('playlist')) || [];

function addToPlaylist(videoId, videoTitle) {
    if (playlist.some(v => v.id === videoId)) {
        showToast('Video already in playlist!', 'info');
        return;
    }
    playlist.push({ id: videoId, title: videoTitle, addedAt: new Date().toISOString() });
    localStorage.setItem('playlist', JSON.stringify(playlist));
    renderPlaylist();
    showToast(`✅ "${videoTitle}" added to playlist!`, 'success');
}

function removeFromPlaylist(videoId) {
    playlist = playlist.filter(v => v.id !== videoId);
    localStorage.setItem('playlist', JSON.stringify(playlist));
    renderPlaylist();
    showToast('Removed from playlist', 'info');
}

function renderPlaylist() {
    const container = document.getElementById('playlistContainer');
    if (!container) return;
    if (playlist.length === 0) {
        container.innerHTML = 'No videos in playlist yet.';
        return;
    }
    container.innerHTML = playlist.map((v, i) => `
        <div class="item-row" style="padding:8px 12px;">
            <div class="item-content">${i + 1}. ${v.title}</div>
            <div class="item-actions">
                <button onclick="playVideoFromPlaylist('${v.id}')" class="btn btn-primary" style="padding:4px 10px; font-size:12px;">▶️ Play</button>
                <button onclick="removeFromPlaylist('${v.id}')" class="btn btn-danger" style="padding:4px 8px; font-size:12px;">✕</button>
            </div>
        </div>
    `).join('');
}

function playVideoFromPlaylist(videoId) {
    const videoElement = document.getElementById(`video_${videoId}`);
    if (videoElement) {
        videoElement.scrollIntoView({ behavior: 'smooth' });
        videoElement.play();
    } else {
        showToast('Video not found on this page. Please select the correct class.', 'error');
    }
}

// ==========================================================
// 📥 OFFLINE VIDEOS
// ==========================================================
window.downloadVideoLocally = function(videoUrl, videoTitle) {
    let downloadedVideos = JSON.parse(localStorage.getItem('downloadedVideos')) || [];
    if (!downloadedVideos.some(v => v.url === videoUrl)) {
        downloadedVideos.push({ title: videoTitle, url: videoUrl });
        localStorage.setItem('downloadedVideos', JSON.stringify(downloadedVideos));
        showToast(`✅ "${videoTitle}" saved offline!`, 'success');
        renderDownloadedVideos();
    } else {
        showToast("Video already downloaded.", 'info');
    }
};

function renderDownloadedVideos() {
    const downloadedContainer = document.getElementById('downloadedVideosContainer');
    if (!downloadedContainer) return;
    let downloadedVideos = JSON.parse(localStorage.getItem('downloadedVideos')) || [];
    downloadedContainer.innerHTML = "";
    if (downloadedVideos.length === 0) {
        downloadedContainer.innerHTML = "No downloaded videos yet.";
        return;
    }
    downloadedVideos.forEach((video) => {
        downloadedContainer.innerHTML += `
            <div class="item-row" style="margin-bottom:4px; padding:10px 14px;">
                <div class="item-content">📥 ${video.title}</div>
                <div class="item-actions">
                    <a href="${video.url}" target="_blank" class="btn btn-primary" style="padding:4px 12px; font-size:12px;">Open</a>
                    <button onclick="removeDownloadedVideo('${video.url}')" class="btn btn-danger" style="padding:4px 8px; font-size:12px;">Remove</button>
                </div>
            </div>
        `;
    });
}

function removeDownloadedVideo(videoUrl) {
    let downloadedVideos = JSON.parse(localStorage.getItem('downloadedVideos')) || [];
    downloadedVideos = downloadedVideos.filter(v => v.url !== videoUrl);
    localStorage.setItem('downloadedVideos', JSON.stringify(downloadedVideos));
    renderDownloadedVideos();
}

// ==========================================================
// 💳 PAYMENT
// ==========================================================
function selectPlan(name, amount) {
    selectedPlan = { name, amount };
    document.getElementById('selectedPlanDisplay').style.display = 'block';
    document.getElementById('selectedPlanName').textContent = name.charAt(0).toUpperCase() + name.slice(1);
    document.getElementById('selectedPlanAmount').textContent = amount;
    const currency=BMT_PRICES.currency||'ETB'; const a=document.getElementById('selectedPlanAmount'); const c=document.getElementById('selectedPlanCurrency'); if(a)a.textContent=Number(amount).toFixed(2); if(c)c.textContent=currency; showToast(`✅ ${name} plan selected: ${Number(amount).toFixed(2)} ${currency}`, 'success');
}

function resetPaymentForm() {
    document.getElementById('paymentName').value = '';
    document.getElementById('txId').value = '';
    document.getElementById('selectedPlanDisplay').style.display = 'none';
    document.getElementById('paymentStatus').style.display = 'none';
    selectedPlan = { name: 'monthly', amount: 9.99 };
}

async function studentApi(path, body) {
    const user = auth.currentUser;
    if (!user) throw new Error("Please sign in first.");
    const token = await user.getIdToken();
    const response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}` },
        body: JSON.stringify(body)
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
}

window.startChapaPayment = async function() {
    const status = document.getElementById('chapaStatus');
    if (!status) return;
    status.textContent = 'Starting secure checkout…';
    try {
        const result = await studentApi('/api/payments/chapa/initialize', {
            plan: selectedPlan.name
        });
        if (!result.checkoutUrl) throw new Error('No checkout URL was returned.');
        status.textContent = 'Redirecting to secure Chapa checkout…';
        window.location.assign(result.checkoutUrl);
    } catch (error) {
        console.error('Chapa payment error:', error);
        status.textContent = error?.message || 'Unable to start secure checkout.';
        showToast('❌ Chapa payment could not start: ' + (error?.message || 'Unknown error'), 'error');
    }
};

window.submitStudentPayment = async function() {
    const nameInput = document.getElementById('paymentName');
    const classSelect = document.getElementById('paymentClass');
    const txIdInput = document.getElementById('txId');
    const name = nameInput?.value.trim();
    const className = classSelect?.value;
    const transactionId = txIdInput?.value.trim();
    const provider = (document.getElementById('paymentProvider')?.value || 'manual').toLowerCase();

    if (!name) {
        showToast('Please enter your full name.', 'error');
        nameInput.focus();
        return;
    }
    if (!transactionId) {
        showToast('Please enter transaction ID.', 'error');
        txIdInput.focus();
        return;
    }

    const statusDiv = document.getElementById('paymentStatus');
    statusDiv.style.display = 'block';
    statusDiv.style.background = 'var(--primary-light)';
    statusDiv.innerHTML = `<div class="spinner"></div> <strong>Submitting payment...</strong><p style="font-size:0.9em; margin-top:4px;">Your transaction is being registered securely.</p>`;

    try {
        const result = await studentApi('/api/payments/submit', {
            studentName: name,
            className,
            transactionId,
            provider,
            plan: selectedPlan.name
        });

        statusDiv.style.background = 'var(--warning-light)';
        statusDiv.innerHTML = `
            <div style="font-size:2em;">⏳</div>
            <strong>Payment Submitted!</strong>
            <p style="font-size:0.9em; margin-top:4px;">Transaction <code>${result.paymentId || ''}</code> is waiting for verification.</p>
        `;
        showToast('⏳ Payment submitted successfully.', 'info');
        txIdInput.value = '';
        await checkPaymentStatus();
    } catch (error) {
        console.error('Payment error:', error);
        statusDiv.style.background = 'var(--danger-light)';
        statusDiv.innerHTML = `<div style="font-size:2em;">❌</div><strong>Payment Failed</strong><p style="font-size:0.9em; margin-top:4px;">${error.message}</p>`;
        showToast('❌ Payment failed: ' + error.message, 'error');
    }
};

// NOTE: account unlocking is server-authoritative. The browser can never
// set isPaid/subscription/entitlements by itself. Unlocking happens only
// after a verified gateway webhook or an authenticated admin approval.

function startUserAccessListener(uid) {
    if (unsubscribeUserAccess) unsubscribeUserAccess();
    unsubscribeUserAccess = onSnapshot(doc(db, "users", uid), async (snap) => {
        if (!snap.exists()) return;
        const data = snap.data() || {};
        currentUserData = data;
        const expiresAt = data.subscription?.expiresAt;
        const expired = expiresAt?.toDate ? expiresAt.toDate() <= new Date() : false;
        if (data.isPaid && !expired) {
            // Refresh visible paid content immediately after admin approval.
            const classSelect = document.getElementById('classSelect');
            if (classSelect) {
                const selectedClass = classSelect.value || 'KG';
                await loadStudentContent(selectedClass);
                await loadStudentVideos(selectedClass);
            }
            showToast('✅ Premium access is now active!', 'success');
        }
        await checkPaymentStatus();
    }, (error) => console.error('User access listener error:', error));
}

async function checkPaymentStatus() {
    const user = auth.currentUser;
    if (!user) return;
    try {
        const userRef = doc(db, "users", user.uid);
        const userSnap = await getDoc(userRef);
        if (userSnap.exists()) {
            const data = userSnap.data();
            const isPaid = data.isPaid || false;
            const hasFreeTrial = data.freeTrial?.isActive || false;
            const paymentsQuery = query(
                collection(db, "payments"),
                where("studentName", "==", data.name || user.displayName || user.email),
                where("status", "in", ["Pending", "Submitted", "Verified", "Approved"])
            );
            const paySnap = await getDocs(paymentsQuery);
            const hasPendingPayment = paySnap.docs.some(d => ['Pending', 'Submitted'].includes((d.data() || {}).status));
            const hasVerifiedPayment = paySnap.docs.some(d => ['Verified', 'Approved'].includes((d.data() || {}).status));
            const statusDiv = document.getElementById('paymentStatus');
            if (isPaid) {
                statusDiv.style.display = 'block';
                statusDiv.style.background = 'var(--success-light)';
                statusDiv.innerHTML = `<div style="font-size:1.2em;">✅ <strong>Premium Active</strong></div><p style="font-size:0.9em;">You have full access to all content.</p>`;
            } else if (hasVerifiedPayment) {
                statusDiv.style.display = 'block';
                statusDiv.style.background = 'var(--success-light)';
                statusDiv.innerHTML = `<div style="font-size:1.2em;">🔓 <strong>Payment Verified</strong></div><p style="font-size:0.9em;">Your premium access is being refreshed.</p>`;
            } else if (hasPendingPayment) {
                statusDiv.style.display = 'block';
                statusDiv.style.background = 'var(--warning-light)';
                statusDiv.innerHTML = `<div style="font-size:1.2em;">⏳ <strong>Payment Pending</strong></div><p style="font-size:0.9em;">Your payment is being verified by admin.</p>`;
            } else if (hasFreeTrial) {
                const daysRemaining = data.freeTrial.daysRemaining || 0;
                statusDiv.style.display = 'block';
                statusDiv.style.background = 'var(--primary-light)';
                statusDiv.innerHTML = `<div style="font-size:1.2em;">🎉 <strong>Free Trial Active</strong></div><p style="font-size:0.9em;">${daysRemaining} days remaining.</p>`;
            }
        }
    } catch (error) {
        console.error('Check payment status error:', error);
    }
}

async function loadPaymentHistory() {
    const user = auth.currentUser;
    if (!user) return;
    try {
        const name = user.displayName || user.email;
        const paymentsQuery = query(
            collection(db, "payments"),
            where("studentName", "==", name)
        );
        const snap = await getDocs(paymentsQuery);
        const container = document.getElementById('paymentHistory');
        if (!container) return;
        if (snap.empty) {
            container.innerHTML = 'No payment history yet.';
            return;
        }
        container.innerHTML = snap.docs.map(doc => {
            const p = doc.data();
            return `
                <div class="item-row">
                    <div class="item-content">
                        💳 ${p.className} — ${p.transactionId}
                        <span class="badge ${p.status === 'Approved' ? 'badge-approved' : 'badge-pending'}">${p.status}</span>
                    </div>
                    <div style="font-size:0.8em; color:var(--text-muted);">
                        ${p.timestamp ? new Date(p.timestamp.seconds * 1000).toLocaleDateString() : 'N/A'}
                    </div>
                </div>
            `;
        }).join('');
    } catch (error) {
        console.error('Payment history error:', error);
    }
}

// ==========================================================
// 💬 CHAT
// ==========================================================
function setupRealtimeChat(selectedClass) {
    const container = document.getElementById('chatMessagesContainer');
    if (!container) return;
    container.innerHTML = '<div class="spinner"></div> Loading messages...';
    if (unsubscribeChat) {
        unsubscribeChat();
    }
    setupTypingListener(selectedClass);
    const q = query(collection(db, "chats"), where("chatRoom", "==", "community"));
    unsubscribeChat = onSnapshot(q, (snapshot) => {
        container.innerHTML = "";
        if (snapshot.empty) {
            container.innerHTML = "No messages yet. Be the first!";
            return;
        }
        const toMillis = (v) => (v && typeof v.toMillis === "function" ? v.toMillis() : Number.MAX_SAFE_INTEGER);
        const messages = snapshot.docs
            .map(d => ({ id: d.id, data: d.data() }))
            .sort((a, b) => toMillis(a.data.createdAt) - toMillis(b.data.createdAt));
        messages.forEach(item => {
            const chat = item.data;
            let mediaHtml = "";
            let displayName = chat.userName || chat.senderName || "User";
            let displayMsg = chat.messageText || chat.message || "";
            const avatar = chat.profileImage || 
                `https://ui-avatars.com/api/?name=${encodeURIComponent(displayName)}&background=6C63FF&color=fff&size=64`;
            const isAdmin = chat.isAdminReply || chat.senderName === "👑 Admin";
            const messageStyle = isAdmin ? 
                'border-left: 4px solid #FF6584; background: #FFF0F5;' : 
                'border-left: 4px solid var(--primary); background: var(--primary-light);';
            if (chat.mediaUrl) {
                if (chat.mediaType === "image" || chat.mediaUrl.match(/\.(jpeg|jpg|gif|png)(\?.*)?$/i)) {
                    mediaHtml = `<div style="margin-top:5px;"><img src="${escapeAttr(chat.mediaUrl)}" data-chat-media-open data-media-type="image" alt="Shared image" style="max-width:180px; max-height:180px; border-radius:var(--radius-sm); cursor:zoom-in;"></div>`;
                } else if (chat.mediaType === "audio" || chat.mediaUrl.match(/\.(mp3|wav|ogg)(\?.*)?$/i)) {
                    mediaHtml = `<div style="margin-top:5px;"><audio controls data-chat-media-open data-media-type="audio" data-media-url="${escapeAttr(chat.mediaUrl)}" style="max-width:200px;"></audio></div>`;
                } else if (chat.mediaType === "video" || chat.mediaUrl.match(/\.(mp4|webm)(\?.*)?$/i)) {
                    mediaHtml = `<div style="margin-top:5px;"><video controls data-chat-media-open data-media-type="video" data-media-url="${escapeAttr(chat.mediaUrl)}" src="${escapeAttr(chat.mediaUrl)}" style="max-width:200px; max-height:150px; cursor:zoom-in;"></video></div>`;
                } else {
                    mediaHtml = `<div style="margin-top:5px;"><button type="button" class="bmt-chat-file-open" data-chat-media-open data-media-type="file" data-media-url="${escapeAttr(chat.mediaUrl)}">📎 Open file</button></div>`;
                }
            }
            container.innerHTML += `
                <div class="chat-message bmt-chat-interactive" id="msg_${item.id}" data-chat-msgid="${item.id}" data-chat-sender="${escapeAttr(displayName)}" style="${messageStyle} padding:7px 10px; margin-bottom:5px; border-radius:var(--radius-md);">
                    ${chat.replyTo ? `<div class="chat-quote" style="margin-bottom:5px;padding:5px 8px;border-left:3px solid var(--primary);background:rgba(108,99,255,.08);border-radius:6px;font-size:11px;color:var(--text-muted);"><strong>↩️ Replying to</strong><br>${String(chat.replyToText||'Original message').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}</div>` : ''}
                    ${isAdmin ? `<div style="font-size:10px; color:#FF6584; margin-bottom:2px; font-weight:600;">👑 Admin Reply</div>` : ''}
                    <div class="msg-header" style="display:flex; align-items:center; gap:6px; margin-bottom:2px;">
                        <img src="${avatar}" alt="${displayName}" class="msg-avatar" style="width:22px; height:22px; border-radius:50%; object-fit:cover;">
                        <span class="msg-user" style="font-weight:600; color:var(--primary-dark); font-size:12px;">${displayName}</span>
                        <span class="msg-time" style="font-size:10px; color:var(--text-muted);">${chat.isRead ? '✅' : '⏳'}</span>
                    </div>
                    <div style="margin-top:2px; white-space:pre-wrap; font-size:13px; line-height:1.35; color:var(--text);">${displayMsg}</div>
                    ${mediaHtml}
                    ${chat.mediaType === 'audio' && chat.durationSeconds ? `<div class="subtitle" style="margin-top:2px;font-size:10px;">🎤 ${Math.round(Number(chat.durationSeconds))}s voice message</div>` : ''}
                    <div style="margin-top:4px; display:flex; gap:3px; flex-wrap:wrap;">
                        <button type="button" class="btn chat-reaction-btn" data-chat-react="${item.id}:heart">❤️ ${(chat.reactions||{}).heart||0}</button>
                        <button type="button" class="btn chat-reaction-btn" data-chat-react="${item.id}:like">👍 ${(chat.reactions||{}).like||0}</button>
                        <button type="button" class="btn chat-reaction-btn" data-chat-react="${item.id}:laugh">😂 ${(chat.reactions||{}).laugh||0}</button>
                        <button type="button" class="btn chat-reaction-btn" data-chat-react="${item.id}:wow">😮 ${(chat.reactions||{}).wow||0}</button>
                        <button type="button" class="btn chat-reaction-btn" data-chat-react="${item.id}:fire">🔥 ${(chat.reactions||{}).fire||0}</button>
                        <button type="button" class="btn chat-reaction-btn" data-chat-report="${item.id}" title="Report this message">🚩</button>
                        <button onclick="replyToMessage('${item.id}', '${displayName.replace(/'/g, "\\'")}', '${displayMsg.substring(0, 30).replace(/'/g, "\\'")}')" class="chat-reaction-btn" style="background:none;border:none;color:var(--primary);cursor:pointer;">↩️ Reply</button>
                    </div>
                </div>
            `;
            setTimeout(() => {
                document.querySelectorAll('[data-chat-media-open]').forEach(el => el.onclick = (ev) => {
                    if (el.tagName === 'AUDIO' || el.tagName === 'VIDEO') { ev.stopPropagation(); }
                    const url = el.dataset.mediaUrl || el.currentSrc || el.src || el.closest('[data-chat-msgid]')?.querySelector('[data-chat-media-open]')?.currentSrc || '';
                    if (url) openStudentChatMediaViewer(url, el.dataset.mediaType || 'file', displayName);
                });
                document.querySelectorAll('[data-chat-msgid]').forEach(row => {
                    let pressTimer = null;
                    let pressStart = null;
                    const cancel = () => { if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; } };
                    const start = (ev) => {
                        if (ev.type === 'mousedown' && ev.button !== 0) return;
                        const point = ev.touches ? ev.touches[0] : ev;
                        pressStart = {x: point.clientX, y: point.clientY};
                        pressTimer = setTimeout(() => {
                            pressTimer = null;
                            openStudentChatMenu(row, point.clientX, point.clientY);
                        }, 550);
                    };
                    const move = (ev) => {
                        if (!pressStart) return;
                        const point = ev.touches ? ev.touches[0] : ev;
                        if (Math.hypot(point.clientX - pressStart.x, point.clientY - pressStart.y) > 12) cancel();
                    };
                    row.addEventListener('touchstart', start, {passive:true});
                    row.addEventListener('touchend', cancel); row.addEventListener('touchmove', move, {passive:true});
                    row.addEventListener('mousedown', start); row.addEventListener('mouseup', cancel); row.addEventListener('mouseleave', cancel);
                });
                document.querySelectorAll('[data-chat-report]').forEach(btn => btn.onclick = async () => {
                const reason = prompt('Why are you reporting this message?');
                if (!reason?.trim()) return;
                try {
                    const token = await currentUser.getIdToken(true);
                    const r = await fetch('/api/chat/report', {method:'POST', headers:{'Content-Type':'application/json',Authorization:`Bearer ${token}`}, body:JSON.stringify({messageId:btn.dataset.chatReport, reason:reason.trim()})});
                    const d = await r.json().catch(()=>({})); if(!r.ok) throw Error(d.error||'Report failed');
                    showToast('Report submitted to moderators.','success');
                } catch(e) { showToast(e.message,'error'); }
            });
            document.querySelectorAll('[data-chat-react]').forEach(btn => btn.onclick = async () => {
                const [messageId, reaction] = btn.dataset.chatReact.split(':');
                try {
                    const token = await currentUser.getIdToken(true);
                    const r = await fetch('/api/chat/reaction', {method:'POST', headers:{'Content-Type':'application/json',Authorization:`Bearer ${token}`}, body:JSON.stringify({messageId,reaction})});
                    const d = await r.json().catch(()=>({})); if(!r.ok) throw Error(d.error||'Reaction failed');
                    setupRealtimeChat(selectedClass);
                } catch(e) { showToast(e.message,'error'); }
            });
            if (!window.BMT_ADMIN_PREVIEW && !isAdmin && chat.senderUid !== currentUser?.uid && chat.isRead !== true) {
                updateDoc(doc(db, "chats", item.id), { isRead: true }).catch(() => {});
            }
            }, 1000);
        });
        container.scrollTop = container.scrollHeight;
    }, (error) => {
        console.error("Chat listener error:", error);
        container.innerHTML = `Error loading chat. (${error.code || "error"})`;
    });
}

function studentChatMediaUrl(url) { return String(url || '').trim(); }
function openStudentChatMediaViewer(url, type, title) {
    url = studentChatMediaUrl(url); if (!url) return;
    document.querySelectorAll('.bmt-chat-viewer').forEach(v => v.remove());
    const viewer = document.createElement('div'); viewer.className='bmt-chat-viewer';
    const safeTitle = escapeHtml(title || 'Shared media');
    let content = '';
    if (type === 'image') content = `<img src="${escapeAttr(url)}" alt="${safeTitle}" class="bmt-chat-viewer-media">`;
    else if (type === 'video') content = `<video controls autoplay playsinline src="${escapeAttr(url)}" class="bmt-chat-viewer-media"></video>`;
    else if (type === 'audio') content = `<audio controls autoplay src="${escapeAttr(url)}" class="bmt-chat-viewer-audio"></audio>`;
    else content = `<div class="bmt-chat-viewer-file">📎<strong>Shared file</strong><span>You can open or download this file.</span></div>`;
    viewer.innerHTML=`<div class="bmt-chat-viewer-backdrop" data-viewer-close></div><div class="bmt-chat-viewer-panel" role="dialog" aria-modal="true" aria-label="${safeTitle}"><div class="bmt-chat-viewer-head"><strong>${safeTitle}</strong><button type="button" class="bmt-chat-viewer-close" data-viewer-close aria-label="Close">✕</button></div><div class="bmt-chat-viewer-content">${content}</div><div class="bmt-chat-viewer-actions"><button type="button" data-viewer-download>⬇️ Download</button><button type="button" data-viewer-share>↗️ Share</button><button type="button" data-viewer-open>↗️ Open</button></div></div>`;
    document.body.appendChild(viewer);
    const close=()=>viewer.remove(); viewer.querySelectorAll('[data-viewer-close]').forEach(b=>b.onclick=close);
    viewer.querySelector('[data-viewer-open]').onclick=()=>window.open(url,'_blank','noopener');
    viewer.querySelector('[data-viewer-download]').onclick=async()=>{ try { const r=await fetch(url,{mode:'cors'}); if(!r.ok) throw Error(); const blob=await r.blob(); const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download=(url.split('/').pop()||'bmt-media').split('?')[0]; document.body.appendChild(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(a.href),1000); } catch (_) { window.open(url,'_blank','noopener'); showToast('Download opened in a new tab because the storage provider blocked direct download.','info'); } };
    viewer.querySelector('[data-viewer-share]').onclick=async()=>{ try { if(navigator.share){await navigator.share({title:title||'BMT shared media',url});} else {await navigator.clipboard.writeText(url); showToast('Media link copied.','success');} } catch(e) { if(e?.name!=='AbortError') showToast('Could not share media.','error'); } };
}
function openStudentChatMenu(row,x,y){
    document.querySelectorAll('.bmt-chat-menu').forEach(m=>m.remove());
    const media=row.querySelector('[data-chat-media-open]'); const url=media?.dataset.mediaUrl||media?.currentSrc||media?.src||''; const type=media?.dataset.mediaType||'file';
    const menu=document.createElement('div'); menu.className='bmt-chat-menu'; menu.style.left=Math.max(8,x)+'px'; menu.style.top=Math.max(8,y)+'px';
    menu.innerHTML=`${url?'<button type="button" class="bmt-chat-menu-item" data-menu-open>🖼️ Open</button><button type="button" class="bmt-chat-menu-item" data-menu-download>⬇️ Download</button><button type="button" class="bmt-chat-menu-item" data-menu-share>↗️ Share</button>':''}<button type="button" class="bmt-chat-menu-item" data-menu-reply>↩️ Reply</button>`;
    document.body.appendChild(menu); const close=()=>{menu.remove();document.removeEventListener('click',close);document.removeEventListener('touchstart',close);};
    setTimeout(()=>{document.addEventListener('click',close);document.addEventListener('touchstart',close);},0);
    menu.querySelector('[data-menu-open]')?.addEventListener('click',e=>{e.stopPropagation();close();openStudentChatMediaViewer(url,type,row.dataset.chatSender);});
    menu.querySelector('[data-menu-download]')?.addEventListener('click',e=>{e.stopPropagation();close();menuDownloadStudentMedia(url);});
    menu.querySelector('[data-menu-share]')?.addEventListener('click',async e=>{e.stopPropagation();close();try{if(navigator.share)await navigator.share({title:'BMT shared media',url});else{await navigator.clipboard.writeText(url);showToast('Media link copied.','success');}}catch(err){if(err?.name!=='AbortError')showToast('Could not share media.','error');}});
    menu.querySelector('[data-menu-reply]')?.addEventListener('click',e=>{e.stopPropagation();close();replyToMessage(row.dataset.chatMsgid,row.dataset.chatSender,(row.querySelector('[style*="white-space:pre-wrap"]')?.textContent||'').slice(0,30));});
    requestAnimationFrame(()=>{const r=menu.getBoundingClientRect();if(r.right>innerWidth)menu.style.left=Math.max(8,innerWidth-r.width-8)+'px';if(r.bottom>innerHeight)menu.style.top=Math.max(8,innerHeight-r.height-8)+'px';});
}
async function menuDownloadStudentMedia(url){ if(!url)return; try{const r=await fetch(url,{mode:'cors'});if(!r.ok)throw Error();const blob=await r.blob();const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=(url.split('/').pop()||'bmt-media').split('?')[0];document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}catch(_){window.open(url,'_blank','noopener');} }

async function sendChatMessageWithReply() {
    const classSelect = document.getElementById("classSelect");
    const nameInput = document.getElementById("chatUserName");
    const messageInput = document.getElementById("chatMessageText");
    const mediaFileInput = document.getElementById("chatMediaFile");
    if (!classSelect || !nameInput || !messageInput) return;
    const selectedClass = classSelect.value;
    const senderName = nameInput.value.trim();
    const messageText = messageInput.value.trim();
    const mediaFile = mediaFileInput?.files[0] || null;
    if (!senderName) {
        showToast("Please enter your name.", "error");
        return;
    }
    if (!messageText && !mediaFile) {
        showToast("Please enter a message or attach a file.", "error");
        return;
    }
    try {
        let mediaUrl = "";
        let mediaType = "";
        if (mediaFile) {
            const token = await auth.currentUser.getIdToken(true);
            const fd = new FormData(); fd.append('file', mediaFile);
            const mediaResponse = await fetch('/api/chat/community/media',{method:'POST',headers:{Authorization:`Bearer ${token}`},body:fd});
            const result = await mediaResponse.json().catch(()=>({}));
            if (!mediaResponse.ok || !result.success) throw new Error(result.error || 'Media upload failed.');
            mediaUrl=result.url; mediaType=result.mediaType;
        }
        const user = auth.currentUser;
        let profileImage = '';
        if (user) {
            try {
                const userSnap = await getDoc(doc(db, "users", user.uid));
                if (userSnap.exists()) {
                    profileImage = userSnap.data().profileImage || '';
                }
            } catch (e) {
                console.error('Error getting user info:', e);
            }
        }
        // Replies go through the server so the quoted message and sender role are
        // authoritative. New messages may still use the normal Firestore path.
        if (replyToMessageId) {
            const token = await user.getIdToken(true);
            const replyResponse = await fetch('/api/chat/community/reply', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Authorization: `Bearer ${token}`
                },
                body: JSON.stringify({ messageId: replyToMessageId, message: messageText, mediaUrl, mediaType, profileImage, durationSeconds: window._bmtRecordedDurationSeconds || undefined })
            });
            const replyData = await replyResponse.json().catch(() => ({}));
            if (!replyResponse.ok) throw new Error(replyData.error || 'Could not send reply.');
            replyToMessageId = null;
            document.getElementById('replyIndicator').style.display = 'none';
            document.getElementById('chatMessageText').placeholder = 'Write to students, teachers and admins...';
        } else {
            const token = await user.getIdToken(true);
            const sendResponse = await fetch('/api/chat/community/send', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                body: JSON.stringify({ message: messageText, mediaUrl, mediaType, profileImage, durationSeconds: window._bmtRecordedDurationSeconds || undefined })
            });
            const sendData = await sendResponse.json().catch(() => ({}));
            if (!sendResponse.ok) throw new Error(sendData.error || 'Could not send message.');
        }
        messageInput.value = "";
        if (mediaFileInput) mediaFileInput.value = ""; window._bmtRecordedDurationSeconds = null; const rs=document.getElementById('voiceRecordStatus'); if(rs){rs.style.display='none';rs.textContent='';}
    } catch (error) {
        console.error("Message send error:", error);
        showToast("Failed to send message: " + error.message, "error");
    }
}

function replyToMessage(messageId, userName, messageText) {
    replyToMessageId = messageId;
    const input = document.getElementById('chatMessageText');
    if (input) {
        input.focus();
        input.placeholder = `Replying to ${userName}...`;
        document.getElementById('replyIndicator').style.display = 'block';
        document.getElementById('replyText').textContent = `Replying to: ${userName} — ${messageText.substring(0, 30)}...`;
    }
}

function cancelReply() {
    replyToMessageId = null;
    document.getElementById('replyIndicator').style.display = 'none';
    document.getElementById('chatMessageText').placeholder = 'Write to students, teachers and admins...';
}

async function askCommunityAI() {
    const input = document.getElementById('chatMessageText');
    const panel = document.getElementById('communityAiAnswer');
    const output = document.getElementById('communityAiAnswerText');
    const button = document.getElementById('communityAiBtn');
    const question = input?.value.trim();
    if (!question) { showToast('Write a learning question first.', 'error'); return; }
    if (!currentUser) { showToast('Please sign in first.', 'error'); return; }
    try {
        button.disabled = true; button.textContent = '🤖 Thinking…';
        panel.style.display = 'block'; output.textContent = 'BMT AI is thinking…';
        const token = await currentUser.getIdToken(true);
        const response = await fetch('/api/ai/tutor', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
            body: JSON.stringify({
                message: question,
                grade: currentUserData?.class || '',
                subject: 'General',
                history: []
            })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || 'AI Tutor is unavailable.');
        output.textContent = data.answer || 'No answer was returned.';
    } catch (e) {
        output.textContent = e.message || 'AI Tutor is unavailable.';
        showToast(e.message || 'AI Tutor error', 'error');
    } finally {
        button.disabled = false; button.textContent = '🤖 Ask BMT AI';
    }
}
function closeCommunityAI() {
    const panel = document.getElementById('communityAiAnswer');
    if (panel) panel.style.display = 'none';
}
window.askCommunityAI = askCommunityAI;
window.closeCommunityAI = closeCommunityAI;

function showEmojiPicker() {
    const picker = document.getElementById('emojiPicker');
    if (picker) {
        picker.style.display = picker.style.display === 'none' ? 'flex' : 'none';
    }
}

function insertEmoji(emoji) {
    const input = document.getElementById('chatMessageText');
    if (input) {
        input.value += emoji;
        input.focus();
        document.getElementById('emojiPicker').style.display = 'none';
    }
}

function setTypingIndicator() {
    const classSelect = document.getElementById("classSelect");
    if (!classSelect) return;
    const className = classSelect.value;
    const user = auth.currentUser;
    if (!user) return;
    const typingRef = doc(db, "typing", className);
    setDoc(typingRef, {
        [user.uid]: {
            name: document.getElementById('chatUserName').value.trim() || "Anonymous",
            isTyping: true,
            timestamp: serverTimestamp()
        }
    }, { merge: true });
    clearTimeout(typingTimeout);
    typingTimeout = setTimeout(() => {
        setDoc(typingRef, {
            [user.uid]: {
                isTyping: false
            }
        }, { merge: true });
    }, 2000);
}

function setupTypingListener(className) {
    const typingRef = doc(db, "typing", className);
    onSnapshot(typingRef, (snap) => {
        const typingContainer = document.getElementById('typingIndicators');
        if (!typingContainer) return;
        if (snap.exists()) {
            const data = snap.data();
            const typingUsers = Object.values(data).filter(u => u.isTyping);
            if (typingUsers.length > 0) {
                const names = typingUsers.map(u => u.name).join(', ');
                typingContainer.textContent = `✍️ ${names} is typing...`;
                typingContainer.style.display = 'block';
            } else {
                typingContainer.style.display = 'none';
            }
        } else {
            typingContainer.style.display = 'none';
        }
    });
}

// ==========================================================
// 📡 LIVE STREAM
// ==========================================================
function setupLiveStream() {
    const liveContainer = document.getElementById("liveStreamContainer");
    if (!liveContainer) return;
    const docRef = doc(db, "settings", "liveStream");
    onSnapshot(docRef, (docSnap) => {
        if (docSnap.exists()) {
            const data = docSnap.data();
            const streamUrl = data.url;
            const isActive = data.isActive;
            if (isActive && streamUrl) {
                const title = data.title ? String(data.title) : "Live Class";
                liveContainer.innerHTML = renderLiveStreamBlock(streamUrl, title);
            } else {
                liveContainer.innerHTML = "No live stream active at the moment.";
            }
        } else {
            liveContainer.innerHTML = "No live stream info available.";
        }
    }, (error) => {
        console.error("Live stream load error:", error);
        liveContainer.innerHTML = "Error loading live stream.";
    });
}

// Detects the platform behind a live-class link and renders the right thing:
// YouTube can be embedded directly with a working fullscreen control.
// Telegram, Zoom, Google Meet and anything else generally refuse to be framed
// (they either block it outright or have nothing meaningful to embed), so those
// get a clear "Join" button that opens the class in its own app/tab instead of
// a broken iframe.
function detectLivePlatform(raw) {
    const url = (raw || "").trim();
    if (/youtube\.com|youtu\.be/i.test(url)) return "youtube";
    if (/t\.me|telegram\.(me|org)/i.test(url)) return "telegram";
    if (/zoom\.us/i.test(url)) return "zoom";
    if (/meet\.google\.com/i.test(url)) return "meet";
    if (/facebook\.com/i.test(url)) return "facebook";
    return "other";
}

function renderLiveStreamBlock(streamUrl, title) {
    const platform = detectLivePlatform(streamUrl);
    const safeTitle = escapeHtml(title);
    if (platform === "youtube") {
        const embedUrl = toEmbedUrl(streamUrl);
        const frameId = "liveFrame_" + Math.random().toString(36).slice(2, 8);
        return `
            <div style="font-weight:600; margin-bottom:8px;">LIVE — ${safeTitle}</div>
            <div class="bmt-live-frame-wrap">
                <iframe id="${frameId}" width="100%" height="220" src="${embedUrl}"
                    title="Live Stream" frameborder="0"
                    allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                    allowfullscreen>
                </iframe>
            </div>
            <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">
                <button class="btn btn-primary" type="button" onclick="bmtFullscreenLive('${frameId}')">Full Screen</button>
                <a class="btn btn-outline" href="${embedUrl}" target="_blank" rel="noopener">Open in New Tab</a>
            </div>`;
    }
    const platformLabel = {
        telegram: "Telegram",
        zoom: "Zoom",
        meet: "Google Meet",
        facebook: "Facebook Live"
    }[platform] || "your class platform";
    return `
        <div style="font-weight:600; margin-bottom:6px;">LIVE — ${safeTitle}</div>
        <p class="subtitle" style="margin:0 0 12px;">This class streams on ${platformLabel}. Tap below to join — it will open in the ${platform === 'other' ? 'app or browser' : platformLabel + ' app'} you already have installed.</p>
        <a class="btn btn-primary" href="${streamUrl}" target="_blank" rel="noopener" style="display:inline-flex;align-items:center;gap:8px">
            Join Live Class
        </a>`;
}

function bmtFullscreenLive(frameId) {
    const el = document.getElementById(frameId);
    if (!el) return;
    if (el.requestFullscreen) el.requestFullscreen();
    else if (el.webkitRequestFullscreen) el.webkitRequestFullscreen();
}
window.bmtFullscreenLive = bmtFullscreenLive;

function toEmbedUrl(raw) {
    const url = (raw || "").trim();
    if (!url) return "";
    try {
        if (url.includes("/embed/")) return url;
        let id = "";
        if (url.includes("youtube.com/watch")) {
            id = new URL(url).searchParams.get("v") || "";
        } else if (url.includes("youtu.be/")) {
            id = url.split("youtu.be/")[1].split(/[?&#]/)[0];
        } else if (url.includes("youtube.com/live/")) {
            id = url.split("youtube.com/live/")[1].split(/[?&#]/)[0];
        } else if (url.includes("youtube.com/shorts/")) {
            id = url.split("youtube.com/shorts/")[1].split(/[?&#]/)[0];
        }
        if (id) return `https://www.youtube.com/embed/${id}?autoplay=1&rel=0`;
    } catch (e) {
        console.warn("Link parse issue:", e);
    }
    return url;
}

function escapeHtml(str) {
    return String(str ?? '').replace(/[&<>"']/g, (c) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
}

// V31.108: assignment/development renderers use the shared short escape helper.
// Keep it as an alias of the existing escapeHtml implementation.
function esc(value) {
    return escapeHtml(value);
}

// ==========================================================
// 🌙 DARK MODE
// ==========================================================
function toggleDarkMode() {
    const html = document.documentElement;
    const currentTheme = html.getAttribute('data-theme');
    const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', newTheme);
    localStorage.setItem('theme', newTheme);
    const btn = document.getElementById('darkModeToggle');
    if (btn) {
        btn.textContent = newTheme === 'dark' ? '☀️' : '🌙';
    }
}

// ==========================================================
// 🔔 TOAST & LOADING
// ==========================================================
function showToast(message, type = "info") {
    document.querySelectorAll('.toast').forEach(t => t.remove());
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add('show'));
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 400);
    }, 4000);
}

function showLoadingOverlay(message = 'Loading...') {
    let overlay = document.getElementById('loadingOverlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'loadingOverlay';
        overlay.style.cssText = `
            position: fixed; inset: 0; background: rgba(0,0,0,0.5);
            backdrop-filter: blur(8px); display: flex; flex-direction: column;
            align-items: center; justify-content: center; z-index: 99999;
            animation: fadeIn 0.3s ease;
        `;
        document.body.appendChild(overlay);
    }
    overlay.innerHTML = `
        <div class="spinner spinner-lg" style="border-color: rgba(255,255,255,0.2); border-top-color: white;"></div>
        <p style="color: white; margin-top: 16px; font-weight: 600; font-size: 16px;">${message}</p>
    `;
}

function hideLoadingOverlay() {
    const overlay = document.getElementById('loadingOverlay');
    if (overlay) {
        overlay.style.opacity = '0';
        overlay.style.transition = 'opacity 0.3s ease';
        setTimeout(() => overlay.remove(), 300);
    }
}

function switchTab(tab) {
    document.getElementById('loginTab').style.display = tab === 'login' ? 'block' : 'none';
    document.getElementById('registerTab').style.display = tab === 'register' ? 'block' : 'none';
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelector(`.tab-btn[onclick*="${tab}"]`).classList.add('active');
}



// ==========================================================
// 🧠 ADVANCED EXAM ENGINE
// ==========================================================
let activeExam = null;
let activeExamTimer = null;
let activeExamAutosave = null;

async function loadStudentExams(selectedClass) {
    const container = document.getElementById('examsContainer');
    if (!container || !auth.currentUser) return;
    container.innerHTML = '<div class="spinner"></div> Loading exams...';
    try {
        const token = await auth.currentUser.getIdToken();
        const response = await fetch(`${window.BMT_API_BASE || ''}/api/exams/available`, {
            headers: { Authorization: `Bearer ${token}` }
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || `Unable to load exams (${response.status})`);
        const exams = (data.exams || []).filter(e => !selectedClass || String(e.className) === String(selectedClass));
        if (!exams.length) {
            container.innerHTML = '<p class="subtitle">No exams are available for this class yet.</p>';
            return;
        }
        container.innerHTML = exams.map(e => {
            const count = Number(e.questionCount || 0);
            return `<div class="exam-card" style="border:1px solid var(--border);border-radius:var(--radius-md);padding:16px;margin-bottom:12px;background:var(--surface);">
                <div style="display:flex;justify-content:space-between;gap:10px;align-items:flex-start;flex-wrap:wrap;">
                    <div><h4 style="margin:0 0 5px">${escapeHtml(e.title || 'Exam')}</h4><div class="subtitle">${count} questions • ${Number(e.durationMinutes || 30)} minutes • ${Number(e.totalPoints || count)} points</div></div>
                    <button class="btn btn-primary" onclick="startAdvancedExam('${escapeHtml(e.id)}')">Start Exam</button>
                </div></div>`;
        }).join('');
    } catch (error) {
        console.error('Exam load error:', error);
        container.innerHTML = `<p class="error-text">${escapeHtml(error.message || 'Unable to load exams. Please try again.')}</p>`;
    }
}

const EXAM_LOCAL_KEY = 'bmt_active_exam_v31';
function persistActiveExam(){
    if(!activeExam) return;
    try{ localStorage.setItem(EXAM_LOCAL_KEY, JSON.stringify({examId:activeExam.id,attemptId:activeExam.attemptId,answers:activeExam.answers||{},savedAt:Date.now()})); }catch(e){}
}
function clearPersistedExam(){ try{ localStorage.removeItem(EXAM_LOCAL_KEY); }catch(e){} }
function applySavedAnswers(answers){
    Object.entries(answers||{}).forEach(([i,v])=>{ const el=document.querySelector(`input[name="exam_q_${i}"][value="${CSS.escape(String(v))}"]`); if(el) el.checked=true; });
}
async function resumeActiveExamFromServer(){
    if(!auth.currentUser || activeExam) return false;
    try{
        const token=await auth.currentUser.getIdToken();
        const r=await fetch(`${window.BMT_API_BASE || ''}/api/exams/active`,{headers:{Authorization:`Bearer ${token}`}});
        const d=await r.json().catch(()=>({}));
        if(!r.ok || !d.active) return false;
        activeExam={id:d.examId,title:d.examTitle,className:d.className,durationMinutes:d.durationMinutes,passMark:d.passMark,questions:d.questions||[],attemptId:d.attemptId,startedAt:Date.parse(d.startedAt),deadlineAt:Date.parse(d.deadlineAt),answers:d.answers||{}};
        renderActiveExamRunner();
        return true;
    }catch(e){ console.debug('No active exam to resume',e); return false; }
}
function renderActiveExamRunner(){
    const container=document.getElementById('examsContainer'); if(!container||!activeExam)return;
    const questions=activeExam.questions||[];
    container.innerHTML=`<div class="exam-runner card" style="margin:0;border:2px solid var(--primary);"><div style="display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap;"><div><h3>${escapeHtml(activeExam.title||'Exam')}</h3><div class="subtitle">Your answers are saved automatically.</div></div><strong id="examTimer" class="exam-timer">⏱️ --:--</strong></div><div id="examQuestions" style="margin-top:16px">${questions.map((q,i)=>renderExamQuestion(q,i)).join('')}</div><button class="btn btn-success" style="width:100%;margin-top:16px" onclick="submitAdvancedExam()">Submit Exam</button></div>`;
    applySavedAnswers(activeExam.answers);
    if(activeExamTimer)clearInterval(activeExamTimer);
    const tick=()=>{const left=Math.max(0,Math.ceil((activeExam.deadlineAt-Date.now())/1000));const timer=document.getElementById('examTimer');if(timer)timer.textContent=`⏱️ ${formatTime(left)}`;if(left<=0){clearInterval(activeExamTimer);window.submitAdvancedExam(true);}};
    tick(); activeExamTimer=setInterval(tick,1000);
    if(activeExamAutosave)clearInterval(activeExamAutosave);
    activeExamAutosave=setInterval(saveActiveExamAnswers,10000);
    persistActiveExam();
}

window.startAdvancedExam = async function(examId) {
    if (activeExam) return;
    try {
        const idToken = await auth.currentUser.getIdToken();
        const startResponse = await fetch(`${window.BMT_API_BASE || ''}/api/exams/start`, {method:'POST', headers:{'Content-Type':'application/json','Authorization':`Bearer ${idToken}`}, body:JSON.stringify({examId})});
        const startData = await startResponse.json().catch(() => ({}));
        if (!startResponse.ok) return showToast(startData.error || 'Could not start exam.', 'error');
        activeExam = { id: examId, title: startData.examTitle, className: startData.className, durationMinutes: startData.durationMinutes, passMark: startData.passMark, questions: startData.questions || [], attemptId: startData.attemptId, startedAt: Date.parse(startData.startedAt), deadlineAt: Date.parse(startData.deadlineAt), answers: {} };
        const questions = Array.isArray(activeExam.questions) ? activeExam.questions : [];
        if (!questions.length) { activeExam = null; return showToast('This exam has no questions.', 'error'); }
        renderActiveExamRunner();
        applySavedAnswers(activeExam.answers);
        window.addEventListener('beforeunload', saveActiveExamAnswers);
    } catch (error) {
        console.error('Start exam error:', error);
        showToast(error.message || 'Could not start exam.', 'error');
    }
};

function renderExamQuestion(q, i) {
    const type = String(q.type || 'mcq').toLowerCase();
    const options = q.options || (type === 'true_false' ? {A:'True', B:'False'} : {});
    const keys = type === 'true_false' ? ['A','B'] : ['A','B','C','D'];
    return `<div class="exam-question" style="padding:16px 0;border-bottom:1px solid var(--border)"><div style="font-weight:700;margin-bottom:10px">${i+1}. ${escapeHtml(q.question || '')}</div>${keys.map(k=>options[k] ? `<label class="exam-option"><input type="radio" name="exam_q_${i}" value="${k}"> <span><strong>${k}.</strong> ${escapeHtml(options[k])}</span></label>` : '').join('')}</div>`;
}

async function collectActiveExamAnswers() {
    const answers = {};
    (activeExam?.questions || []).forEach((q,i) => { answers[i] = document.querySelector(`input[name="exam_q_${i}"]:checked`)?.value || ''; });
    if(activeExam) activeExam.answers = answers;
    persistActiveExam();
    return answers;
}

async function saveActiveExamAnswers() {
    if (!activeExam || !auth.currentUser) return;
    try {
        const token = await auth.currentUser.getIdToken();
        await fetch(`${window.BMT_API_BASE || ''}/api/exams/save`, {method:'POST', headers:{'Content-Type':'application/json','Authorization':`Bearer ${token}`}, body:JSON.stringify({examId:activeExam.id, attemptId:activeExam.attemptId, answers:await collectActiveExamAnswers()})});
    } catch (e) { console.debug('Exam autosave unavailable', e); }
}

window.submitAdvancedExam = async function(forceAutoSubmit=false) {
    if (!activeExam || !auth.currentUser) return;
    if (!forceAutoSubmit && !confirm('Submit this exam now? You will not be able to change your answers after submission.')) return;
    if (activeExamTimer) clearInterval(activeExamTimer);
    if (activeExamAutosave) clearInterval(activeExamAutosave);
    window.removeEventListener('beforeunload', saveActiveExamAnswers);
    const questions = activeExam.questions || [];
    const answers = await collectActiveExamAnswers();
    try {
        const idToken = await auth.currentUser.getIdToken();
        const response = await fetch(`${window.BMT_API_BASE || ''}/api/exams/grade`, {method:'POST',headers:{'Content-Type':'application/json','Authorization':`Bearer ${idToken}`},body:JSON.stringify({examId:activeExam.id,attemptId:activeExam.attemptId,answers})});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Could not grade exam.');
        const score = Number(result.score || 0), total = Number(result.totalPoints || 0), percentage = Number(result.percentage || 0);
        const weak = Array.isArray(result.weakTopics) ? result.weakTopics : [];
        const topicStats = result.topicStats || {};
        activeExam = null;
        clearPersistedExam();
        let review = [];
        try {
            const rr = await fetch(`${window.BMT_API_BASE || ''}/api/exams/result/${encodeURIComponent(result.attemptId || '')}`, {headers:{'Authorization':`Bearer ${idToken}`}});
            if (rr.ok) { const rd = await rr.json(); review = Array.isArray(rd.review) ? rd.review : []; result.passMark = rd.passMark; result.status = rd.status; }
        } catch (reviewError) { console.debug('Detailed result unavailable', reviewError); }
        const passMark = Number(result.passMark ?? 50);
        const passed = String(result.status || (percentage >= passMark ? 'PASS' : 'FAIL')).toUpperCase() === 'PASS';
        const topicHtml = Object.entries(topicStats).map(([topic,v]) => { const pct=v.total ? Math.round(v.score/v.total*100) : 0; return `<div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border)"><span>${escapeHtml(topic)}</span><strong>${pct}%</strong></div>`; }).join('');
        const weakHtml = weak.length ? `<div class="card" style="margin-top:14px;text-align:left"><strong>📌 Topics to review</strong><p class="subtitle">${weak.map(escapeHtml).join(' • ')}</p></div>` : '';
        const reviewHtml = review.length ? `<div class="card" style="margin-top:14px;text-align:left"><strong>📋 Answer review</strong>${review.map((q,i)=>`<div style="padding:10px 0;border-bottom:1px solid var(--border)"><div><strong>${i+1}. ${escapeHtml(q.question)}</strong></div><div class="subtitle">Your answer: ${escapeHtml(q.selectedAnswer || 'Not answered')} ${q.isCorrect ? '✅' : '❌'} • Correct: ${escapeHtml(q.correctAnswer || '—')}</div></div>`).join('')}</div>` : '';
        const container = document.getElementById('examsContainer');
        container.innerHTML = `<div class="exam-result" style="text-align:center;padding:22px;background:var(--success-light);border-radius:var(--radius-md)"><div style="font-size:2.5em">${passed ? '🎉' : '📚'}</div><h3>Exam Complete</h3><div style="font-size:2em;font-weight:800">${percentage}%</div><p>You scored ${score} / ${total} points.</p><p><strong>${passed ? 'PASS' : 'FAIL'}</strong> • Pass mark: ${passMark}%</p>${topicHtml ? `<div class="card" style="margin-top:14px;text-align:left"><strong>📊 Topic performance</strong>${topicHtml}</div>` : ''}${weakHtml}${reviewHtml}<button class="btn btn-primary" style="margin-top:14px" onclick="loadStudentExams(document.getElementById('classSelect').value)">Back to Exams</button></div>`;
        showToast(percentage >= 50 ? '🎉 Great work!' : 'Keep practicing — you are improving!', percentage >= 50 ? 'success' : 'info');
    } catch (error) { console.error(error); showToast('Could not save exam result.', 'error'); }
};

window.loadExamHistory = async function() {
    if (!auth.currentUser) return;
    const container = document.getElementById('examHistoryContainer');
    if (!container) return;
    try {
        const token = await auth.currentUser.getIdToken();
        const r = await fetch(`${window.BMT_API_BASE || ''}/api/exams/history`, {headers:{'Authorization':`Bearer ${token}`}});
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || 'Unable to load history');
        container.innerHTML = (data.attempts || []).length ? data.attempts.map(a => `<div class="exam-history-row"><strong>${escapeHtml(a.examTitle)}</strong><span>${a.percentage}% — ${a.score}/${a.totalPoints}</span></div>`).join('') : '<p class="subtitle">No completed exams yet.</p>';
    } catch (e) { container.innerHTML = '<p class="error-text">Unable to load exam history.</p>'; }
};

// ==========================================================
// 🚀 INIT
// ==========================================================
document.addEventListener("DOMContentLoaded", () => {
    const savedTheme = localStorage.getItem('theme') || 'light';
    document.documentElement.setAttribute('data-theme', savedTheme);
    const btn = document.getElementById('darkModeToggle');
    if (btn) {
        btn.textContent = savedTheme === 'dark' ? '☀️' : '🌙';
    }
    const savedClass = localStorage.getItem('selectedClass') || 'KG';
    renderClassChips(savedClass);
    const select = document.getElementById('classSelect');
    if (select) {
        select.value = savedClass;
    }
    setupLiveStream();
});





// ==========================================================
// 🔔 IN-APP NOTIFICATIONS
// ==========================================================
let _notifications = [];
function notificationTime(iso){
    if(!iso) return '';
    const d=new Date(iso), diff=Math.max(0,Date.now()-d.getTime());
    const mins=Math.floor(diff/60000), hrs=Math.floor(mins/60), days=Math.floor(hrs/24);
    if(mins<1)return 'just now'; if(mins<60)return `${mins}m ago`; if(hrs<24)return `${hrs}h ago`; if(days<7)return `${days}d ago`;
    return d.toLocaleDateString();
}
function renderNotifications(){
    const list=document.getElementById('notificationList'),badge=document.getElementById('notificationBadge');
    if(!list)return;
    const unread=_notifications.filter(n=>!n.read).length;
    if(badge){badge.textContent=unread>99?'99+':String(unread);badge.style.display=unread?'block':'none';}
    list.innerHTML=_notifications.length?_notifications.map(n=>`<button type="button" data-notification-id="${escapeHtml(n.id)}" style="display:block;width:100%;text-align:left;border:0;border-bottom:1px solid var(--border-color);background:${n.read?'transparent':'var(--primary-light)'};padding:12px 4px;cursor:pointer;color:inherit"><div style="display:flex;justify-content:space-between;gap:10px"><strong>${escapeHtml(n.title)}</strong><small class="subtitle">${escapeHtml(notificationTime(n.createdAt))}</small></div><div style="margin-top:4px;font-size:.9em">${escapeHtml(n.message)}</div>${n.actionUrl?`<div style="margin-top:6px;font-size:.82em;color:var(--primary)">Open →</div>`:''}</button>`).join(''):'<p class="subtitle">No notifications yet.</p>';
    list.querySelectorAll('[data-notification-id]').forEach(el=>el.onclick=()=>{const n=_notifications.find(x=>x.id===el.dataset.notificationId);markNotificationRead(el.dataset.notificationId);if(n?.actionUrl&&/^\/(?!\/)/.test(n.actionUrl))location.href=n.actionUrl;});
}
window.loadNotifications=async function(){
    const user=auth.currentUser;if(!user)return;
    try{const token=await user.getIdToken();const r=await fetch(`${window.BMT_API_BASE||''}/api/notifications`,{headers:{Authorization:`Bearer ${token}`}});const d=await r.json();if(!r.ok)throw Error(d.error||'Unable to load notifications');_notifications=d.notifications||[];renderNotifications();}catch(e){console.error('Notifications:',e);}
};
window.toggleNotificationPanel=function(){const p=document.getElementById('notificationPanel');if(!p)return;const open=p.style.display!=='none';p.style.display=open?'none':'block';if(!open)loadNotifications();};
window.markNotificationRead=async function(id){
    const n=_notifications.find(x=>x.id===id);if(n)n.read=true;renderNotifications();
    try{const token=await auth.currentUser.getIdToken();await fetch(`${window.BMT_API_BASE||''}/api/notifications/${encodeURIComponent(id)}/read`,{method:'POST',headers:{Authorization:`Bearer ${token}`}});}catch(e){console.error(e);}
};
window.markAllNotificationsRead=async function(){
    try{const token=await auth.currentUser.getIdToken();const r=await fetch(`${window.BMT_API_BASE||''}/api/notifications/read-all`,{method:'POST',headers:{Authorization:`Bearer ${token}`}});if(!r.ok)throw Error('Unable to mark notifications read');_notifications.forEach(n=>n.read=true);renderNotifications();}catch(e){showToast(e.message,'error');}
};

// ==========================================================
// 🔔 PUSH NOTIFICATIONS (FCM)
// ==========================================================
window.enablePushNotifications = async function() {
    if (!messaging || !('Notification' in window)) return showToast('Notifications are not supported in this browser.', 'error');
    const vapidKey = window.BMT_FCM_VAPID_KEY || '';
    if (!vapidKey) return showToast('Notifications are not configured yet. Add the Firebase Web Push certificate key.', 'info');
    try {
        const permission = await Notification.requestPermission();
        if (permission !== 'granted') return showToast('Notification permission was not granted.', 'info');
        const registration = await navigator.serviceWorker.register('/static/firebase-messaging-sw.js');
        const { getMessaging, getToken } = await import("https://www.gstatic.com/firebasejs/10.7.1/firebase-messaging.js");
        if (!messaging) messaging = getMessaging(app);
        const token = await getToken(messaging, { vapidKey, serviceWorkerRegistration: registration });
        if (!token || !auth.currentUser) throw new Error('Could not obtain a notification token.');
        const apiToken=await auth.currentUser.getIdToken(); const r=await fetch(`${window.BMT_API_BASE||''}/api/notifications/register-device`,{method:'POST',headers:{Authorization:`Bearer ${apiToken}`,'Content-Type':'application/json'},body:JSON.stringify({token,platform:'web'})}); if(!r.ok){const d=await r.json().catch(()=>({}));throw Error(d.error||'Could not register notification device.');}
        showToast('🔔 Notifications enabled!', 'success');
    } catch (error) { console.error(error); showToast('Could not enable notifications.', 'error'); }
};
if (messaging) { import("https://www.gstatic.com/firebasejs/10.7.1/firebase-messaging.js").then(({onMessage}) => onMessage(messaging, payload => { showToast(`🔔 ${payload.notification?.title || 'Bright Mind Tutor'}: ${payload.notification?.body || ''}`, 'info'); })).catch(() => {}); }

// ==========================================================
// 🤖 AI TUTOR — server-side API gateway
// ==========================================================
const aiHistory = JSON.parse(localStorage.getItem('bmt_ai_history') || '[]');
function saveAIHistory(){try{localStorage.setItem('bmt_ai_history',JSON.stringify(aiHistory.slice(-16)));}catch(_){}}
window.clearAIChat=function(){aiHistory.length=0;saveAIHistory();const m=document.getElementById('aiTutorMessages');if(m)m.innerHTML='<div class="ai-message assistant"><strong>Bright Mind Tutor:</strong> Chat cleared. What would you like to learn?</div>';};
window.aiQuickPrompt=function(prompt){const i=document.getElementById('aiTutorInput');if(i){i.value=prompt;i.focus();}};
function renderSavedAIHistory(){const m=document.getElementById('aiTutorMessages');if(!m||!aiHistory.length)return;m.innerHTML='';for(const x of aiHistory){const c=x.role==='user'?'user':'assistant';const l=x.role==='user'?'You':'Bright Mind Tutor';m.insertAdjacentHTML('beforeend',`<div class="ai-message ${c}"><strong>${l}:</strong> ${escapeHtml(x.text||'').replace(/\n/g,'<br>')}</div>`);}}
window.openAIHomeworkCoach=function(){const p=document.getElementById('aiHomeworkPanel');if(p)p.style.display='block';document.getElementById('aiHomeworkInput')?.focus();};
window.solveAIHomework=async function(){const user=auth.currentUser,input=document.getElementById('aiHomeworkInput'),out=document.getElementById('aiHomeworkResult');if(!user||!input||!out)return;const q=input.value.trim();if(!q){out.innerHTML='<div class="ai-message assistant error-text">Enter a homework question first.</div>';return;}out.innerHTML='<div class="ai-message assistant"><span class="ai-dots">● ● ●</span> Working through the problem…</div>';try{const token=await user.getIdToken(),profile=currentUserData||{},grade=profile.class||profile.className||document.getElementById('classSelect')?.value||'',subject=document.getElementById('subjectSelect')?.value||'';const r=await fetch(`${window.BMT_API_BASE||''}/api/ai/homework-coach`,{method:'POST',headers:{'Content-Type':'application/json','Authorization':`Bearer ${token}`},body:JSON.stringify({question:q,grade,subject})});const d=await r.json();if(!r.ok)throw Error(d.error||'Homework coach failed');out.innerHTML=`<div class="ai-message assistant"><strong>🧮 Homework Coach</strong><div style="margin-top:8px">${escapeHtml(d.answer||'').replace(/\n/g,'<br>')}</div></div>`;}catch(e){out.innerHTML=`<div class="ai-message assistant error-text">${escapeHtml(e.message)}</div>`;}};
window.solveAIHomeworkImage=async function(){const user=auth.currentUser,image=document.getElementById('aiHomeworkImage')?.files?.[0],note=document.getElementById('aiHomeworkNote')?.value.trim()||'',out=document.getElementById('aiHomeworkResult');if(!user||!out)return;if(!image){out.innerHTML='<div class="ai-message assistant error-text">Choose a clear homework photo first.</div>';return;}if(image.size>8*1024*1024){out.innerHTML='<div class="ai-message assistant error-text">Image is too large. Maximum is 8 MB.</div>';return;}out.innerHTML='<div class="ai-message assistant"><span class="ai-dots">● ● ●</span> Reading your homework photo…</div>';try{const token=await user.getIdToken(),profile=currentUserData||{},grade=profile.class||profile.className||document.getElementById('classSelect')?.value||'',subject=document.getElementById('subjectSelect')?.value||'',form=new FormData();form.append('image',image);form.append('question',note);form.append('grade',grade);form.append('subject',subject);const r=await fetch(`${window.BMT_API_BASE||''}/api/ai/homework-image`,{method:'POST',headers:{'Authorization':`Bearer ${token}`},body:form});const d=await r.json();if(!r.ok)throw Error(d.error||'Homework image processing failed');out.innerHTML=`<div class="ai-message assistant"><strong>📷🧮 Homework Photo Coach</strong><div style="margin-top:8px">${escapeHtml(d.answer||'').replace(/\n/g,'<br>')}</div></div>`;}catch(e){out.innerHTML=`<div class="ai-message assistant error-text">${escapeHtml(e.message)}</div>`;}};
window.openAIStudyPlan=function(){const p=document.getElementById('aiStudyPlanPanel');if(p)p.style.display='block';document.getElementById('aiPlanGoal')?.focus();};
window.createAIStudyPlan=async function(){const user=auth.currentUser,out=document.getElementById('aiStudyPlanResult');if(!user||!out)return;out.innerHTML='<div class="ai-message assistant"><span class="ai-dots">● ● ●</span> Building your plan…</div>';try{const token=await user.getIdToken(),profile=currentUserData||{},grade=profile.class||profile.className||document.getElementById('classSelect')?.value||'',subject=document.getElementById('subjectSelect')?.value||'',goal=document.getElementById('aiPlanGoal')?.value.trim()||'',weakTopics=document.getElementById('aiPlanWeak')?.value.trim()||'',days=Number(document.getElementById('aiPlanDays')?.value||7);const r=await fetch(`${window.BMT_API_BASE||''}/api/ai/study-plan`,{method:'POST',headers:{'Content-Type':'application/json','Authorization':`Bearer ${token}`},body:JSON.stringify({grade,subject,goal,weakTopics,days})});const d=await r.json();if(!r.ok)throw Error(d.error||'Study plan failed');out.innerHTML=`<div class="ai-message assistant"><strong>📅 Your BMT Study Plan</strong><div style="margin-top:8px">${escapeHtml(d.plan||'').replace(/\n/g,'<br>')}</div></div>`;}catch(e){out.innerHTML=`<div class="ai-message assistant error-text">${escapeHtml(e.message)}</div>`;}};

window.generateAIPracticeQuiz=async function(){
 const user=auth.currentUser, status=document.getElementById('aiTutorStatus'), messages=document.getElementById('aiTutorMessages');
 if(!user||!messages)return;
 const topic=document.getElementById('aiTutorInput')?.value.trim() || 'the current topic';
 const profile=currentUserData||{}, grade=profile.class||profile.className||document.getElementById('classSelect')?.value||'', subject=document.getElementById('subjectSelect')?.value||'';
 messages.insertAdjacentHTML('beforeend','<div class="ai-message assistant"><strong>Bright Mind Tutor:</strong> Creating a BMT-grounded practice quiz…</div>');
 try{
  const token=await user.getIdToken();
  const r=await fetch(`${window.BMT_API_BASE||''}/api/ai/practice-quiz`,{method:'POST',headers:{'Content-Type':'application/json','Authorization':`Bearer ${token}`},body:JSON.stringify({topic,grade,subject,count:5})});
  const d=await r.json(); if(!r.ok)throw new Error(d.error||'Quiz generation failed');
  let html='<div class="ai-message assistant"><strong>🎯 BMT Practice Quiz</strong>';
  (d.questions||[]).forEach((q,i)=>{html+=`<div style="margin-top:12px"><strong>${i+1}. ${escapeHtml(q.question)}</strong>`; q.options.forEach((o,j)=>{html+=`<button class="btn btn-neutral ai-quiz-option" type="button" style="display:block;width:100%;text-align:left;margin-top:6px" data-answer="${j}" data-correct="${q.answerIndex}" data-explanation="${escapeHtml(q.explanation||'')}">${String.fromCharCode(65+j)}. ${escapeHtml(o)}</button>`}); html+='</div>'}); html+='</div>'; messages.insertAdjacentHTML('beforeend',html); messages.querySelectorAll('.ai-quiz-option').forEach(btn=>btn.onclick=function(){const all=this.parentElement.querySelectorAll('.ai-quiz-option');all.forEach(x=>x.disabled=true);const ok=this.dataset.answer===this.dataset.correct;this.insertAdjacentHTML('beforeend',ok?' ✅':' ❌');const ex=this.dataset.explanation;if(ex)this.insertAdjacentHTML('afterend',`<div class="subtitle" style="margin-top:5px">${ex}</div>`);}); if(status)status.textContent=`Practice quiz ready. AI messages remaining today: ${d.remainingToday}`; messages.scrollTop=messages.scrollHeight;
 }catch(e){messages.insertAdjacentHTML('beforeend',`<div class="ai-message assistant error-text">${escapeHtml(e.message)}</div>`);}
};

window.askAITutor=async function(){const input=document.getElementById('aiTutorInput'),messages=document.getElementById('aiTutorMessages'),button=document.getElementById('aiTutorSendBtn'),status=document.getElementById('aiTutorStatus'),text=input?.value.trim(),user=auth.currentUser;if(!text||!user)return;messages.insertAdjacentHTML('beforeend',`<div class="ai-message user"><strong>You:</strong> ${escapeHtml(text)}</div>`);aiHistory.push({role:'user',text});input.value='';button.disabled=true;button.textContent='Thinking…';messages.insertAdjacentHTML('beforeend','<div id="aiTyping" class="ai-message assistant"><span class="ai-dots">● ● ●</span></div>');messages.scrollTop=messages.scrollHeight;try{const token=await user.getIdToken(),profile=currentUserData||{},grade=profile.class||profile.className||document.getElementById('classSelect')?.value||'',subject=document.getElementById('subjectSelect')?.value||'';const response=await fetch(`${window.BMT_API_BASE||''}/api/ai/tutor`,{method:'POST',headers:{'Content-Type':'application/json','Authorization':`Bearer ${token}`},body:JSON.stringify({message:text,grade,subject,history:aiHistory.slice(-16).slice(0,-1)})});const data=await response.json();document.getElementById('aiTyping')?.remove();if(!response.ok)throw new Error(data.error||'AI request failed');const sourceNote=(data.sources||[]).length?`<div style="font-size:.78em;color:var(--text-muted);margin-top:6px">📚 BMT sources: ${data.sources.map(s=>escapeHtml(typeof s==='string'?s:(s.title||'Study material'))).join(', ')}</div>`:'';const quotaNote=Number.isFinite(data.remainingToday)?`<div style="font-size:.75em;color:var(--text-muted);margin-top:4px">AI messages remaining today: ${data.remainingToday}</div>`:'';messages.insertAdjacentHTML('beforeend',`<div class="ai-message assistant"><strong>Bright Mind Tutor:</strong> ${escapeHtml(data.answer).replace(/\n/g,'<br>')}${sourceNote}${quotaNote}</div>`);aiHistory.push({role:'assistant',text:data.answer});while(aiHistory.length>16)aiHistory.shift();saveAIHistory();if(status)status.textContent=Number.isFinite(data.remainingToday)?`AI messages remaining today: ${data.remainingToday}`:'AI Tutor is ready.';}catch(error){document.getElementById('aiTyping')?.remove();aiHistory.pop();saveAIHistory();messages.insertAdjacentHTML('beforeend',`<div class="ai-message assistant error-text">${escapeHtml(error.message)}</div>`);}finally{button.disabled=false;button.textContent='Ask AI';messages.scrollTop=messages.scrollHeight;}};
window.addEventListener('DOMContentLoaded',renderSavedAIHistory);

// ==========================================================
// 🎤 VOICE RECORDING (records audio, attaches it to the chat
// media file input so it goes through the normal chat upload
// path) — this button existed in the HTML but had no matching
// function, so it silently did nothing.
// ==========================================================
let _mediaRecorder = null;
let _recordedChunks = [];
let _recordingTimer = null;
let _recordingStartedAt = 0;
const BMT_AUDIO_MAX_SECONDS = 60;

window.startVoiceRecording = async function() {
    const btn = document.getElementById('voiceRecordBtn');
    const status = document.getElementById('voiceRecordStatus');
    if (_mediaRecorder && _mediaRecorder.state === 'recording') {
        _mediaRecorder.stop();
        return;
    }
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        _recordedChunks = [];
        const mime = ['audio/webm;codecs=opus','audio/webm','audio/ogg','audio/mp4'].find(x => window.MediaRecorder?.isTypeSupported?.(x)) || '';
        _mediaRecorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
        _recordingStartedAt = Date.now();
        const updateTimer = () => {
            const elapsed = Math.min(BMT_AUDIO_MAX_SECONDS, Math.floor((Date.now() - _recordingStartedAt) / 1000));
            if (status) { status.style.display='inline'; status.textContent=`⏺️ ${String(Math.floor(elapsed/60)).padStart(2,'0')}:${String(elapsed%60).padStart(2,'0')} / 01:00`; }
            if (elapsed >= BMT_AUDIO_MAX_SECONDS && _mediaRecorder?.state === 'recording') _mediaRecorder.stop();
        };
        _recordingTimer = setInterval(updateTimer, 250);
        updateTimer();
        _mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) _recordedChunks.push(e.data); };
        _mediaRecorder.onstop = () => {
            clearInterval(_recordingTimer); _recordingTimer = null;
            stream.getTracks().forEach(track => track.stop());
            const elapsed = Math.min(BMT_AUDIO_MAX_SECONDS, Math.max(1, Math.ceil((Date.now() - _recordingStartedAt) / 1000)));
            const blob = new Blob(_recordedChunks, { type: _mediaRecorder.mimeType || 'audio/webm' });
            const ext = (blob.type.includes('ogg') ? 'ogg' : blob.type.includes('mp4') ? 'm4a' : 'webm');
            const file = new File([blob], `voice_${Date.now()}.${ext}`, { type: blob.type || 'audio/webm' });
            const dataTransfer = new DataTransfer();
            dataTransfer.items.add(file);
            const fileInput = document.getElementById('chatMediaFile');
            if (fileInput) fileInput.files = dataTransfer.files;
            if (btn) btn.textContent = '🎤';
            if (status) { status.style.display='inline'; status.textContent=`🎧 ${elapsed}s voice message ready`; }
            window._bmtRecordedDurationSeconds = elapsed;
            showToast('Voice message attached. Click Send to deliver it.', 'success');
        };
        _mediaRecorder.start(250);
        if (btn) btn.textContent = '⏹️';
        showToast('Recording started. Maximum 60 seconds.', 'info');
    } catch (err) {
        clearInterval(_recordingTimer); _recordingTimer = null;
        console.error('Voice recording error:', err);
        showToast('Could not access the microphone.', 'error');
    }
};

// ==========================================================
// 📺 MEDIA PREVIEW MODAL — close button
// ==========================================================
window.closeMediaPreview = function() {
    const modal = document.getElementById('mediaPreviewModal');
    if (modal) modal.classList.remove('active');
};
window.showEmojiPicker = showEmojiPicker;
window.insertEmoji = insertEmoji;
window.setTypingIndicator = setTypingIndicator;
window.submitStudentPayment = submitStudentPayment;
window.downloadVideoLocally = downloadVideoLocally;
window.removeDownloadedVideo = removeDownloadedVideo;
window.addToPlaylist = addToPlaylist;
window.removeFromPlaylist = removeFromPlaylist;
window.playVideoFromPlaylist = playVideoFromPlaylist;
window.submitQuiz = submitQuiz;
window.sendQuizResultToAdmin = sendQuizResultToAdmin;
window.openProfileModal = openProfileModal;
window.closeProfileModal = closeProfileModal;
window.previewProfilePicture = previewProfilePicture;
window.saveProfile = saveProfile;
window.deleteProfilePicture = deleteProfilePicture;
window.renderPlaylist = renderPlaylist;
window.renderDownloadedVideos = renderDownloadedVideos;
window.selectPlan = selectPlan;
window.resetPaymentForm = resetPaymentForm;
// ==========================================================
// 🏛️ NATIONAL EXAM ARCHIVE STUDENT UI
// ==========================================================
function archiveEscape(value) {
    return String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}

function archiveUpdateStreamFilter() {
    const grade = document.getElementById('archiveFilterGrade')?.value;
    const stream = document.getElementById('archiveFilterStream');
    if (!stream) return;
    stream.disabled = grade !== '12';
    if (grade !== '12') stream.value = '';
}

window.loadExamArchive = async function() {
    const box = document.getElementById('examArchiveList');
    const status = document.getElementById('examArchiveStatus');
    if (!box || !currentUser) return;
    archiveUpdateStreamFilter();
    const params = new URLSearchParams();
    const grade = document.getElementById('archiveFilterGrade')?.value;
    const stream = document.getElementById('archiveFilterStream')?.value;
    const subject = document.getElementById('archiveFilterSubject')?.value.trim();
    const year = document.getElementById('archiveFilterYear')?.value;
    if (grade) params.set('grade', grade);
    if (grade === '12' && stream) params.set('stream', stream);
    if (subject) params.set('subject', subject);
    if (year) params.set('year', year);
    box.innerHTML = '<div class="spinner"></div> Loading archive...';
    try {
        const token = await currentUser.getIdToken(true);
        const response = await fetch(`/api/exam-archive?${params.toString()}`, {headers: {'Authorization': `Bearer ${token}`} });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Could not load archive.');
        status.textContent = data.premium ? 'Premium access: unlocked.' : 'Some files are locked. Approve a premium payment to unlock them instantly.';
        box.innerHTML = data.items.length ? data.items.map(item => {
            const locked = item.locked;
            const label = `Grade ${item.grade}${item.stream ? ' • '+archiveEscape(item.stream) : ''} • ${archiveEscape(item.subject)} • ${item.year}`;
            return `<div class="item-row" style="align-items:flex-start">
                <div class="item-content"><b>${archiveEscape(item.title)}</b><div style="font-size:.85em;color:var(--text-muted);margin-top:4px">${label}</div></div>
                <div class="item-actions">${locked ? '<span class="badge badge-pending">🔒 Premium</span>' : `<button class="btn btn-primary" style="padding:6px 10px" onclick="openExamArchive('${item.id}')">📖 Open</button>`}</div>
            </div>`;
        }).join('') : '<p class="subtitle">No exams match these filters.</p>';
    } catch (error) {
        box.innerHTML = `<p class="error-text">${archiveEscape(error.message)}</p>`;
        status.textContent = '';
    }
};

window.openExamArchive = async function(id) {
    try {
        const token = await currentUser.getIdToken(true);
        const response = await fetch(`/api/exam-archive/${encodeURIComponent(id)}`, {headers: {'Authorization': `Bearer ${token}`} });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'This archive file is unavailable or locked.');
        const item = data.item;
        if (!item || !item.previewUrl) throw new Error('This archive file is unavailable or locked.');
        const modal = document.getElementById('mediaPreviewModal');
        const content = document.getElementById('mediaPreviewContent');
        if (!modal || !content) throw new Error('Preview window is unavailable.');
        content.innerHTML = `<iframe src="${archiveEscape(item.previewUrl)}" title="${archiveEscape(item.title)}" style="width:100%;height:70vh;border:0;border-radius:10px" allow="autoplay"></iframe>
          ${item.downloadUrl ? `<div style="margin-top:10px;text-align:center"><a class="btn btn-primary" href="${archiveEscape(item.downloadUrl)}" target="_blank" rel="noopener">⬇️ Open download</a></div>` : ''}`;
        modal.style.display = 'flex';
    } catch (error) { showToast(error.message, 'error'); }
};

window.closeMediaPreview = window.closeMediaPreview || function() {
    const modal = document.getElementById('mediaPreviewModal');
    if (modal) modal.style.display = 'none';
    const content = document.getElementById('mediaPreviewContent');
    if (content) content.innerHTML = '';
};

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('archiveFilterGrade')?.addEventListener('change', archiveUpdateStreamFilter);
    document.getElementById('archiveFilterStream')?.addEventListener('change', () => window.loadExamArchive());
});

window.addEventListener('DOMContentLoaded',()=>{
    setTimeout(()=>loadNotifications(),1200);
    setInterval(()=>{ if(auth.currentUser) loadNotifications(); },60000);
});
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&auth.currentUser)loadNotifications();});

// V14 — Live Classes
window.loadLiveClasses=async function(){
 const box=document.getElementById('liveClassList'); const status=document.getElementById('liveClassStatus');
 if(!box||!currentUser)return;
 try{
  const profile=currentUserData||{}; const grade=profile.class||profile.className||document.getElementById('studentGrade')?.value||document.getElementById('grade')?.value||'';
  const token=await currentUser.getIdToken(true);
  const r=await fetch('/api/live/classes'+(grade?`?grade=${encodeURIComponent(grade)}`:''),{headers:{Authorization:`Bearer ${token}`}});
  const data=await r.json(); if(!r.ok)throw Error(data.error||'Could not load live classes.');
  const rows=data.classes||[]; status.textContent=rows.length?'Upcoming classes':'No upcoming live classes.';
  box.innerHTML=rows.length?rows.map(c=>`<div class="item-row" style="align-items:flex-start"><div class="item-content"><b>${archiveEscape(c.title)}</b><div class="subtitle">Grade ${archiveEscape(c.grade)} • ${archiveEscape(c.subject)} • ${new Date(c.startAt).toLocaleString()} • ${c.teacherOnline?'🟢 Teacher online':'⚪ Teacher offline'}</div>${c.description?`<div style="margin-top:5px">${archiveEscape(c.description)}</div>`:''}</div><div class="item-actions"><button class="btn btn-secondary" data-enroll-class="${archiveEscape(c.id)}">✓ Enroll</button><button class="btn btn-primary" data-join-class="${archiveEscape(c.id)}">▶ Join</button><button class="btn" data-qna-class="${archiveEscape(c.id)}">💬 Q&A</button></div></div>`).join(''):'<p class="subtitle">Your teachers have not scheduled a class yet.</p>';
  document.querySelectorAll('[data-enroll-class]').forEach(b=>b.onclick=async()=>{try{const r=await fetch('/api/live/classes/'+encodeURIComponent(b.dataset.enrollClass)+'/enroll',{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${token}`},body:'{}'});const d=await r.json();if(!r.ok)throw Error(d.error||'Enrollment failed');b.textContent='✓ Enrolled';b.disabled=true;showToast('Enrolled in live class.','success')}catch(e){showToast(e.message,'error')}});
  document.querySelectorAll('[data-join-class]').forEach(a=>a.onclick=async()=>{const popup=window.open('about:blank','_blank','noopener');try{const r=await fetch('/api/live/classes/'+encodeURIComponent(a.dataset.joinClass)+'/attendance',{method:'POST',headers:{Authorization:`Bearer ${token}`}});const d=await r.json();if(!r.ok)throw Error(d.error||'Please enroll first');if(!d.meetingUrl)throw Error('This live class does not have a meeting link yet.');if(popup)popup.location.href=d.meetingUrl;else window.location.href=d.meetingUrl;}catch(e){if(popup)popup.close();a.dataset.blocked='1';showToast(e.message,'error')}});
  document.querySelectorAll('[data-qna-class]').forEach(b=>b.onclick=async()=>{const msg=prompt('Write your question:');if(!msg?.trim())return;try{await fetch('/api/live/classes/'+encodeURIComponent(b.dataset.qnaClass)+'/qna',{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${token}`},body:JSON.stringify({message:msg.trim()})});showToast('Question sent to the teacher.','success')}catch(e){showToast('Could not send question.','error')}});
 }catch(e){box.innerHTML=`<p class="error-text">${archiveEscape(e.message)}</p>`;status.textContent=''}
};
window.addEventListener('DOMContentLoaded',()=>setTimeout(()=>loadLiveClasses(),1400));


// V15 — Student Learning Dashboard
window.loadLearningDashboard = async function(){
  const stats=document.getElementById('learningStats'), next=document.getElementById('learningNext'), grade=document.getElementById('progressGrade');
  if(!stats) return; stats.innerHTML='<div class="spinner"></div>';
  try{
    const user = auth.currentUser; if(!user) return;
    const token=await user.getIdToken();
    const r=await fetch(`${window.BMT_API_BASE||''}/api/student/progress`,{headers:{Authorization:`Bearer ${token}`}});
    const d=await r.json(); if(!r.ok) throw new Error(d.error||'Unable to load progress');
    grade.textContent=d.grade?`Grade ${d.grade} • Your learning snapshot`:'Your learning snapshot';
    const st=d.stats||{};
    const cards=[['🎥',st.videosWatched||0,'Videos watched'],['🧠',`${st.quizAverage||0}%`,'Quiz average'],['📝',st.examsTaken||0,'Exams taken'],['🎯',`${st.examAverage||0}%`,'Exam average'],['📚',st.coursesCompleted||0,'Courses completed'],['🤖',st.aiUsedToday||0,'AI uses today']];
    stats.innerHTML=cards.map(x=>`<div class="stat-card"><div style="font-size:1.25em">${x[0]}</div><strong>${x[1]}</strong><span>${x[2]}</span></div>`).join('');
    const classes=d.upcomingLiveClasses||[];
    const recs=d.recommendations||[];
    const weak=d.weakTopics||[];
    const recommendationHtml=recs.length?`<div class="card" style="margin-top:12px;border-color:var(--primary);"><h4 style="margin:0 0 8px;">🎯 What should I study next?</h4>${recs.map(r=>`<div class="list-row" style="align-items:flex-start;gap:10px;"><div style="flex:1;"><strong>${escapeHtml(r.title)}</strong><div class="subtitle">${escapeHtml(r.reason)}</div></div><span class="badge">${escapeHtml(r.priority)}</span><button class="btn btn-secondary" type="button" data-ai-recommendation="${escapeAttr(r.title)}">🤖 Study</button></div>`).join('')}</div>`:'';
    const weakHtml=weak.length?`<div class="card" style="margin-top:12px;"><h4 style="margin:0 0 8px;">⚠️ Topics to strengthen</h4>${weak.map(w=>`<div class="list-row" style="gap:10px;"><span style="flex:1;">${escapeHtml(w.topic)}</span><strong>${w.percentage}%</strong><button class="btn btn-neutral" type="button" data-ai-topic="${escapeAttr(w.topic)}">Ask AI</button></div>`).join('')}</div>`:'';
    const classesHtml=classes.length?`<h4 style="margin:0 0 8px;">📅 Upcoming classes</h4>`+classes.map(c=>`<div class="list-row"><div><strong>${escapeHtml(c.title)}</strong><div class="subtitle">${escapeHtml(c.subject)} • ${new Date(c.startAt).toLocaleString()}</div></div><button class="btn btn-primary" type="button" data-progress-join="${escapeAttr(c.id)}">Join</button></div>`).join(''):'<p class="subtitle">No upcoming live classes for your grade.</p>';
    next.innerHTML=recommendationHtml+weakHtml+`<div style="margin-top:12px;">${classesHtml}</div>`;
    next.querySelectorAll('[data-ai-recommendation]').forEach(btn=>btn.addEventListener('click',()=>openAIFromProgress(btn.dataset.aiRecommendation)));
    next.querySelectorAll('[data-ai-topic]').forEach(btn=>btn.addEventListener('click',()=>openAIFromProgress(btn.dataset.aiTopic)));
    next.querySelectorAll('[data-progress-join]').forEach(btn=>btn.addEventListener('click',async()=>{
      const popup=window.open('about:blank','_blank','noopener');
      try{
        const enroll=await fetch('/api/live/classes/'+encodeURIComponent(btn.dataset.progressJoin)+'/enroll',{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${await user.getIdToken(true)}`},body:'{}'});
        const enrolled=await enroll.json().catch(()=>({}));
        if(!enroll.ok)throw new Error(enrolled.error||'Enrollment failed');
        const attendance=await fetch('/api/live/classes/'+encodeURIComponent(btn.dataset.progressJoin)+'/attendance',{method:'POST',headers:{Authorization:`Bearer ${await user.getIdToken(true)}`}});
        const joined=await attendance.json().catch(()=>({}));
        if(!attendance.ok)throw new Error(joined.error||'Unable to join live class');
        if(!joined.meetingUrl)throw new Error('This live class does not have a meeting link yet.');
        if(popup)popup.location.href=joined.meetingUrl;else window.location.href=joined.meetingUrl;
      }catch(e){if(popup)popup.close();console.error(e);alert(e.message||'Unable to join live class.')}
    }));
  }catch(e){stats.innerHTML='<p class="error-text">Unable to load your progress right now.</p>'; console.error(e)}
};
// V31.108 audit fix: only http(s) links may be placed in an href (blocks javascript:/data:/vbscript:).
function safeWebUrl(u){try{const x=new URL(String(u||''));return (x.protocol==='https:'||x.protocol==='http:')?x.href:'';}catch(e){return '';}}
function escapeAttr(v){return String(v??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]))}

function openAIFromProgress(topic){
  const input=document.getElementById('aiTutorInput');
  const section=document.getElementById('aiTutorSection');
  if(!input||!section)return;
  input.value=`Help me study ${topic}. Explain it for my grade, give one worked example, then give me 3 practice questions without answers.`;
  section.scrollIntoView({behavior:'smooth',block:'start'});
  setTimeout(()=>input.focus(),450);
  if(typeof showToast==='function') showToast(`AI study prompt prepared for ${topic}.`,'success');
}

setTimeout(()=>{ if(window.auth?.currentUser || typeof auth!=='undefined' && auth.currentUser) loadLearningDashboard(); }, 2500);

// V25 — Student Communication & Support Center
async function supportApi(path, options={}){
  const user=auth.currentUser; if(!user) throw Error('Please sign in first.');
  const token=await user.getIdToken();
  const res=await fetch(`${window.BMT_API_BASE||''}${path}`,{...options,headers:{'Content-Type':'application/json',Authorization:`Bearer ${token}`,...(options.headers||{})}});
  const data=await res.json().catch(()=>({})); if(!res.ok) throw Error(data.error||`Request failed (${res.status})`); return data;
}
async function loadStudentSupport(){
  const box=document.getElementById('studentSupportTickets'); if(!box||!auth.currentUser)return;
  try{
    const d=await supportApi('/api/support/tickets'); const rows=d.tickets||[];
    box.innerHTML=rows.length?rows.map(t=>`<div class="item-row" style="align-items:flex-start"><div class="item-content"><b>${escapeHtml(t.subject)}</b><div class="subtitle">${escapeHtml(t.category)} • ${escapeHtml(t.status)} • ${t.updatedAt?new Date(t.updatedAt).toLocaleString():''}</div></div><div class="item-actions"><button class="btn btn-secondary" data-support-open="${escapeAttr(t.id)}">Open</button>${t.status!=='closed'?`<button class="btn btn-danger" data-support-close="${escapeAttr(t.id)}">Close</button>`:''}</div></div>`).join(''):'<p class="subtitle">No support requests yet.</p>';
    document.querySelectorAll('[data-support-open]').forEach(b=>b.onclick=()=>openStudentSupport(b.dataset.supportOpen));
    document.querySelectorAll('[data-support-close]').forEach(b=>b.onclick=async()=>{try{await supportApi('/api/support/tickets/'+encodeURIComponent(b.dataset.supportClose)+'/close',{method:'POST',body:'{}'});showToast('Ticket closed.','success');loadStudentSupport()}catch(e){showToast(e.message,'error')}});
  }catch(e){box.innerHTML=`<p class="error-text">${escapeHtml(e.message)}</p>`}
}
async function openStudentSupport(id){
  try{
    const d=await supportApi('/api/support/tickets/'+encodeURIComponent(id)+'/messages');
    const messages=d.messages||[];
    const text=messages.map(m=>`${m.senderRole==='teacher'?'Teacher':'You'}: ${m.message}`).join('\n\n');
    const reply=prompt((text||'Conversation')+'\n\nWrite a reply:');
    if(!reply?.trim())return;
    await supportApi('/api/support/tickets/'+encodeURIComponent(id)+'/messages',{method:'POST',body:JSON.stringify({message:reply.trim()})});
    showToast('Reply sent.','success'); loadStudentSupport();
  }catch(e){showToast(e.message,'error')}
}
window.addEventListener('DOMContentLoaded',()=>setTimeout(()=>{
  const form=document.getElementById('supportTicketForm');
  if(form)form.addEventListener('submit',async e=>{e.preventDefault();try{await supportApi('/api/support/tickets',{method:'POST',body:JSON.stringify({subject:document.getElementById('supportSubject').value.trim(),category:document.getElementById('supportCategory').value,message:document.getElementById('supportMessage').value.trim(),teacherUid:document.getElementById('supportTeacherUid').value.trim()})});e.target.reset();document.getElementById('supportStatus').textContent='✅ Request sent.';showToast('Support request sent.','success');loadStudentSupport()}catch(err){document.getElementById('supportStatus').textContent='❌ '+err.message}});
  const feedback=document.getElementById('feedbackForm');
  if(feedback)feedback.addEventListener('submit',async e=>{e.preventDefault();try{await supportApi('/api/feedback',{method:'POST',body:JSON.stringify({rating:Number(document.getElementById('feedbackRating').value),message:document.getElementById('feedbackMessage').value.trim(),category:'student_experience'})});e.target.reset();showToast('Thank you for your feedback.','success')}catch(err){showToast(err.message,'error')}});
  loadStudentSupport();
},2200));

async function submitStudentAssignment(id){
 const text=document.getElementById(`assignment-text-${id}`)?.value.trim()||'';
 const link=document.getElementById(`assignment-link-${id}`)?.value.trim()||'';
 const status=document.getElementById(`assignment-status-${id}`);
 if(!text&&!link){if(status)status.textContent='Write an answer or add a submission link.';return;}
 try{
  const token=await auth.currentUser.getIdToken();
  const r=await fetch(`/api/student/assignments/${encodeURIComponent(id)}/submit`,{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${token}`},body:JSON.stringify({text,link})});
  const d=await r.json().catch(()=>({})); if(!r.ok)throw Error(d.error||'Submission failed.');
  if(status)status.textContent='✅ Submitted successfully.';
  await loadStudentAssignments();
 }catch(e){if(status)status.textContent='❌ '+e.message;}
}
async function loadStudentAssignmentSubmission(id){
 try{
  const token=await auth.currentUser.getIdToken();
  const r=await fetch(`/api/student/assignments/${encodeURIComponent(id)}/submission`,{headers:{Authorization:`Bearer ${token}`}});
  const d=await r.json().catch(()=>({})); if(!r.ok)throw Error(d.error||'Unable to load submission.');
  return d.submission||null;
 }catch(e){return null;}
}
async function loadStudentAssignments(){
 const box=document.getElementById('studentAssignmentList'); if(!box)return;
 try{
  const r=await fetch('/api/student/assignments',{headers:{Authorization:`Bearer ${await auth.currentUser.getIdToken()}`}});
  const d=await r.json().catch(()=>({})); if(!r.ok)throw Error(d.error||'Unable to load assignments');
  const rows=d.assignments||[];
  if(!rows.length){box.innerHTML='<p class="subtitle">No assignments for your grade yet.</p>';return;}
  box.innerHTML=rows.map(a=>`<div class="list-row" style="display:block;margin-bottom:12px"><div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start"><div><strong>${esc(a.title)}</strong><div class="subtitle">Grade ${esc(a.className)}${a.dueAt?' • Due '+esc(a.dueAt):''}</div></div><span class="badge badge-free">Assignment</span></div><div style="margin-top:7px;white-space:pre-wrap">${esc(a.description||'')}</div><div style="margin-top:10px"><textarea id="assignment-text-${esc(a.id)}" rows="4" placeholder="Write your answer here..."></textarea><input id="assignment-link-${esc(a.id)}" type="url" placeholder="Optional: Google Drive / document / project link" style="margin-top:7px;width:100%"><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:8px"><button class="btn btn-primary" type="button" onclick="submitStudentAssignment('${esc(a.id)}')">📤 Submit Assignment</button><span id="assignment-status-${esc(a.id)}" class="subtitle"></span></div><div id="assignment-feedback-${esc(a.id)}" class="subtitle" style="margin-top:8px"></div></div></div>`).join('');
  for(const a of rows){
   const sub=await loadStudentAssignmentSubmission(a.id);
   if(!sub)continue;
   const text=document.getElementById(`assignment-text-${a.id}`), link=document.getElementById(`assignment-link-${a.id}`), status=document.getElementById(`assignment-status-${a.id}`), fb=document.getElementById(`assignment-feedback-${a.id}`);
   if(text)text.value=sub.text||''; if(link)link.value=sub.link||'';
   if(status)status.textContent=sub.status==='graded'?`✅ Graded: ${sub.score}/${sub.maxScore} (${sub.percentage}%)`:'🕒 Submitted — awaiting teacher review.';
   if(fb&&sub.status==='graded'&&sub.feedback)fb.innerHTML=`<strong>Teacher feedback:</strong> ${esc(sub.feedback)}`;
   if(sub.status==='graded'){ if(text)text.disabled=true; if(link)link.disabled=true; }
  }
 }catch(e){box.innerHTML=`<p class="error-text">${esc(e.message)}</p>`}
}

window.submitStudentAssignment=submitStudentAssignment;
window.loadStudentAssignments=loadStudentAssignments;

window.resumeActiveExamFromServer = resumeActiveExamFromServer;
window.addEventListener('online', ()=>{ if(activeExam) saveActiveExamAnswers(); else resumeActiveExamFromServer(); });
document.addEventListener('visibilitychange', ()=>{ if(document.visibilityState==='hidden' && activeExam) saveActiveExamAnswers(); });


// ==========================================================
// 🌱 MY DEVELOPMENT CENTER
// ==========================================================
function developmentEsc(v){return typeof esc==='function'?esc(v):String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
async function loadStudentDevelopment(){
 const box=document.getElementById('studentDevelopmentLessons'), progress=document.getElementById('studentDevelopmentProgress'), status=document.getElementById('studentDevelopmentStatus');
 if(!box||!progress||!auth.currentUser)return;
 box.innerHTML='<p class="subtitle">Loading development lessons…</p>';
 try{
  const token=await auth.currentUser.getIdToken(true); const r=await fetch('/api/student/development',{headers:{Authorization:`Bearer ${token}`}}); const d=await r.json(); if(!r.ok)throw Error(d.error||'Unable to load development center.');
  const state=d.state||{}, catalog=d.catalog||[]; const pg=state.progress||{};
  progress.innerHTML=catalog.map(c=>{const x=pg[c.id]||{};return `<div class="card"><div class="subtitle">${developmentEsc(c.title)}</div><div style="font-size:1.5rem;font-weight:800;margin-top:4px">${Number(x.percentage||0)}%</div><div class="subtitle">${Number(x.completed||0)}/${Number(x.total||0)} lessons</div></div>`}).join('');
  box.innerHTML=catalog.map(c=>`<div class="card" style="margin-bottom:10px"><h4 style="margin:0 0 8px">${developmentEsc(c.title)}</h4>${(c.lessons||[]).map(l=>{const done=(state.completedLessons||[]).includes(l.id);return `<details style="border-top:1px solid var(--border-color);padding:8px 0"><summary style="cursor:pointer"><strong>${done?'✓ ':''}${developmentEsc(l.title)}</strong><span class="badge ${done?'badge-free':'badge-pending'}" style="margin-left:8px">${done?'ተጠናቋል':'ትምህርት'}</span></summary><div style="margin-top:8px;display:grid;gap:7px">${l.videoUrl?`<div>🎬 <a href="${developmentEsc(l.videoUrl)}" target="_blank" rel="noopener noreferrer">ቪድዮ ትምህርቱን ይመልከቱ</a></div>`:''}${l.bookUrl?`<div>📗 <a href="${developmentEsc(l.bookUrl)}" target="_blank" rel="noopener noreferrer">${developmentEsc(l.bookName||'የትምህርቱን መጽሐፍ ይክፈቱ')}</a></div>`:''}<div>🎥 <strong>ይመልከቱ፦</strong> ${developmentEsc(l.watch)}</div><div>📖 <strong>ያንብቡ፦</strong> ${developmentEsc(l.read)}</div><div>💭 <strong>ያስቡ፦</strong> ${developmentEsc(l.think)}</div><div>📝 <strong>ይለማመዱ፦</strong> ${developmentEsc(l.practice)}</div><div>❓ <strong>ጥያቄ፦</strong> ${developmentEsc((l.quiz||[]).join(' • '))}</div><div>🏆 <strong>ስኬት፦</strong> ${developmentEsc(l.badge||'የዕድገት ባጅ')}</div>${done?'':'<button class="btn btn-primary" type="button" data-dev-complete="'+developmentEsc(l.id)+'">ትምህርቱን እንደተጠናቀቀ ምልክት ያድርጉ</button>'}</div></details>`}).join('')}</div>`).join('');
  box.querySelectorAll('[data-dev-complete]').forEach(b=>b.onclick=async()=>{try{b.disabled=true;const token=await auth.currentUser.getIdToken(true);const r=await fetch('/api/student/development',{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${token}`},body:JSON.stringify({lessonId:b.dataset.devComplete})});const x=await r.json();if(!r.ok)throw Error(x.error||'Unable to save progress.');status.textContent='✅ Development progress saved.';await loadStudentDevelopment();}catch(e){b.disabled=false;status.textContent='❌ '+e.message;}});
  status.textContent=`${(state.achievements||[]).length} achievement${(state.achievements||[]).length===1?'':'s'} earned. Keep growing at your own pace.`;
 }catch(e){box.innerHTML=`<p class="error-text">${developmentEsc(e.message)}</p>`;status.textContent='';}
}
window.loadStudentDevelopment=loadStudentDevelopment;

async function loadStudentRegion(){
    const sel=document.getElementById('studentRegionSelect'); if(!sel)return;
    try{
        const token=await auth.currentUser.getIdToken(true);
        const r=await fetch('/api/student/region',{headers:{Authorization:`Bearer ${token}`}});
        const d=await r.json(); if(!r.ok) throw Error(d.error||'Unable to load region.');
        sel.innerHTML='<option value="">Not set</option>'+(d.regions||[]).map(x=>`<option value="${developmentEsc(x)}">${developmentEsc(x)}</option>`).join('');
        sel.value=d.region||'';
    }catch(e){ /* silent - non-critical */ }
}
document.getElementById('studentRegionSaveBtn')?.addEventListener('click',async()=>{
    const sel=document.getElementById('studentRegionSelect'); const status=document.getElementById('studentRegionStatus');
    try{
        const token=await auth.currentUser.getIdToken(true);
        const r=await fetch('/api/student/region',{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${token}`},body:JSON.stringify({region:sel.value})});
        const d=await r.json(); if(!r.ok) throw Error(d.error||'Unable to save region.');
        status.textContent='✅ Region saved.';
    }catch(e){ status.textContent='❌ '+e.message; }
});
if(document.getElementById('studentRegionSelect')) loadStudentRegion();

async function loadProfileDetails(){
    const sel=document.getElementById('profileEducationLevel'); if(!sel)return;
    try{
        const token=await auth.currentUser.getIdToken(true);
        const r=await fetch('/api/student/profile-details',{headers:{Authorization:`Bearer ${token}`}});
        const d=await r.json(); if(!r.ok) throw Error(d.error||'Unable to load profile.');
        sel.innerHTML='<option value="">Not set</option>'+(d.educationLevels||[]).map(x=>`<option value="${developmentEsc(x)}">${developmentEsc(x)}</option>`).join('');
        const p=d.profile||{};
        sel.value=p.educationLevel||'';
        document.getElementById('profileAge').value=p.age||'';
        document.getElementById('profileSchoolName').value=p.schoolName||'';
        document.getElementById('profileGuardianName').value=p.guardianName||'';
        document.getElementById('profileGuardianPhone').value=p.guardianPhone||'';
        document.getElementById('profileAddress').value=p.address||'';
        document.getElementById('profileZone').value=p.zone||'';
        document.getElementById('profileWoreda').value=p.woreda||'';
    }catch(e){ /* silent - non-critical */ }
}
document.getElementById('profileDetailsSaveBtn')?.addEventListener('click',async()=>{
    const status=document.getElementById('profileDetailsStatus');
    try{
        const token=await auth.currentUser.getIdToken(true);
        const body={
            age: document.getElementById('profileAge').value||null,
            educationLevel: document.getElementById('profileEducationLevel').value,
            schoolName: document.getElementById('profileSchoolName').value.trim(),
            guardianName: document.getElementById('profileGuardianName').value.trim(),
            guardianPhone: document.getElementById('profileGuardianPhone').value.trim(),
            address: document.getElementById('profileAddress').value.trim(),
            zone: document.getElementById('profileZone').value.trim(),
            woreda: document.getElementById('profileWoreda').value.trim(),
        };
        const r=await fetch('/api/student/profile-details',{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${token}`},body:JSON.stringify(body)});
        const d=await r.json(); if(!r.ok) throw Error(d.error||'Unable to save profile.');
        status.textContent='✅ Profile saved.';
    }catch(e){ status.textContent='❌ '+e.message; }
});
if(document.getElementById('profileEducationLevel')) loadProfileDetails();

// V31.108 Phase B: reveal the Distance Learning tab only for students whose
// learningMode is 'Distance'. Reuses the same /api/student/profile-details
// endpoint loadProfileDetails() already calls - no new network cost pattern.
async function loadStudentLearningMode(){
    const tabBtn=document.getElementById('distanceTabBtn'); if(!tabBtn)return;
    try{
        const token=await auth.currentUser.getIdToken(true);
        const r=await fetch('/api/student/profile-details',{headers:{Authorization:`Bearer ${token}`}});
        const d=await r.json(); if(!r.ok) throw Error(d.error||'Unable to load profile.');
        const isDistance=(d.profile||{}).learningMode==='Distance';
        tabBtn.style.display=isDistance?'':'none';
        const status=document.getElementById('distancePathwayStatus');
        if(status) status.textContent=isDistance?'':'This tab appears automatically once your account is set to Distance learning mode by an admin.';
        if(isDistance){ loadDistanceCourses(); loadDistanceCertificates(); }
    }catch(e){ /* silent - non-critical */ }
}
window.loadStudentLearningMode=loadStudentLearningMode;

// Classroom attendance history for this student. The backend already
// separates Regular vs Distance (see attendance_routes.py) - a Distance
// student gets back mode:'Distance' with an empty record set and a note,
// so this just renders whichever shape comes back rather than deciding
// Regular/Distance again on the client.
async function loadStudentAttendance(){
    const note=document.getElementById('attendanceModeNote'), summaryBox=document.getElementById('attendanceSummary'), listBox=document.getElementById('attendanceHistoryList');
    if(!note||!listBox) return;
    try{
        const token=await auth.currentUser.getIdToken(true);
        const r=await fetch('/api/student/attendance',{headers:{Authorization:`Bearer ${token}`}});
        const d=await r.json(); if(!r.ok) throw Error(d.error||'Unable to load attendance.');
        if(d.mode!=='Regular'){
            note.textContent=d.note||'Classroom attendance does not apply to Distance-mode students.';
            if(summaryBox) summaryBox.innerHTML='';
            listBox.innerHTML='<p class="subtitle">See your course/lesson progress in the Distance Learning tab instead.</p>';
            return;
        }
        const s=d.summary||{};
        note.textContent='Your classroom attendance record.';
        if(summaryBox) summaryBox.innerHTML=`<div class="card"><div class="subtitle">Present</div><div style="font-size:1.4rem;font-weight:700">${s.present??0}</div></div><div class="card"><div class="subtitle">Absent</div><div style="font-size:1.4rem;font-weight:700">${s.absent??0}</div></div><div class="card"><div class="subtitle">Late</div><div style="font-size:1.4rem;font-weight:700">${s.late??0}</div></div><div class="card"><div class="subtitle">Rate</div><div style="font-size:1.4rem;font-weight:700">${s.attendanceRate!=null?s.attendanceRate+'%':'—'}</div></div>`;
        const rows=d.records||[];
        listBox.innerHTML=rows.length?rows.map(rec=>`<div class="item-row"><div class="item-content"><b>${escapeAttr(rec.date||'')}</b>${rec.subject?' • '+escapeAttr(rec.subject):''}</div><span class="badge ${rec.status==='present'?'badge-free':rec.status==='late'?'badge-pending':'badge-rejected'}">${escapeAttr(rec.status||'')}</span></div>`).join(''):'<p class="subtitle">No attendance has been recorded yet.</p>';
    }catch(e){ note.textContent=''; listBox.innerHTML=`<p class="error-text">${escapeAttr(e.message)}</p>`; }
}
window.loadStudentAttendance=loadStudentAttendance;

// Consolidated Assignment/Quiz/Exam mark list - read-only aggregation from
// marklist_routes.py. Does not change how assignments/quizzes/exams are
// graded; it only summarizes the existing graded records.
async function loadStudentMarkList(){
    const summaryBox=document.getElementById('markListSummary'), detailsBox=document.getElementById('markListDetails');
    if(!summaryBox||!detailsBox) return;
    try{
        const token=await auth.currentUser.getIdToken(true);
        const r=await fetch('/api/student/marklist',{headers:{Authorization:`Bearer ${token}`}});
        const d=await r.json(); if(!r.ok) throw Error(d.error||'Unable to load your marks.');
        const s=d.summary||{};
        summaryBox.innerHTML=`<div class="card"><div class="subtitle">Assignments</div><div style="font-size:1.3rem;font-weight:700">${s.assignmentAverage!=null?s.assignmentAverage+'%':'—'}</div></div><div class="card"><div class="subtitle">Quizzes</div><div style="font-size:1.3rem;font-weight:700">${s.quizAverage!=null?s.quizAverage+'%':'—'}</div></div><div class="card"><div class="subtitle">Exams</div><div style="font-size:1.3rem;font-weight:700">${s.examAverage!=null?s.examAverage+'%':'—'}</div></div><div class="card"><div class="subtitle">Overall</div><div style="font-size:1.3rem;font-weight:700">${s.overallAverage!=null?s.overallAverage+'%':'—'}</div></div>`;
        const items=[
            ...(d.assignments||[]).map(a=>({label:a.title,type:'Assignment',pct:a.percentage})),
            ...(d.quizzes||[]).map(q=>({label:q.title,type:'Quiz',pct:q.percentage})),
            ...(d.exams||[]).map(e=>({label:e.title,type:'Exam',pct:e.percentage})),
        ];
        detailsBox.innerHTML=items.length?items.map(i=>`<div class="item-row"><div class="item-content"><b>${escapeAttr(i.label||'')}</b><div class="subtitle">${escapeAttr(i.type)}</div></div><strong>${i.pct!=null?i.pct+'%':'—'}</strong></div>`).join(''):'<p class="subtitle">No graded work yet.</p>';
    }catch(e){ detailsBox.innerHTML=`<p class="error-text">${escapeAttr(e.message)}</p>`; }
}
window.loadStudentMarkList=loadStudentMarkList;

// V31.108 Phase C: real course/lesson consumption for the Distance tab,
// backed by /api/student/courses (targeting_access-filtered) and
// /api/student/lessons/<id>/complete.
async function loadDistanceCourses(){
    const box=document.getElementById('distanceCourseList'); if(!box)return;
    box.textContent='Loading…';
    try{
        const token=await auth.currentUser.getIdToken(true);
        const r=await fetch('/api/student/courses',{headers:{Authorization:`Bearer ${token}`}});
        const d=await r.json(); if(!r.ok) throw Error(d.error||'Unable to load courses.');
        const courses=d.courses||[];
        box.innerHTML=courses.length?courses.map(c=>`
            <div class="list-row" style="align-items:center">
                <div><strong>${escapeHtml(c.title)}</strong><div class="subtitle">${c.subject?escapeHtml(c.subject)+' • ':''}Grade ${escapeHtml(c.className)} • ${c.completedCount}/${c.lessonCount} lessons (${c.percent}%)</div></div>
                <button class="btn btn-outline" type="button" onclick="openDistanceCourse('${escapeAttr(c.id)}','${escapeAttr(c.title)}')">Open</button>
            </div>`).join(''):'<p class="subtitle">No Distance courses are available for your grade yet.</p>';
    }catch(e){ box.innerHTML=`<p class="error-text">${escapeHtml(e.message)}</p>`; }
}
window.loadDistanceCourses=loadDistanceCourses;

async function openDistanceCourse(courseId, title){
    const viewer=document.getElementById('distanceCourseViewer'); if(!viewer)return;
    viewer.style.display='block';
    document.getElementById('distanceCourseViewerTitle').textContent=title||'Course';
    viewer.dataset.courseId=courseId;
    const lessonsBox=document.getElementById('distanceCourseViewerLessons');
    lessonsBox.innerHTML='<div class="spinner"></div> Loading…';
    try{
        const token=await auth.currentUser.getIdToken(true);
        const r=await fetch(`/api/student/courses/${courseId}/lessons`,{headers:{Authorization:`Bearer ${token}`}});
        const d=await r.json(); if(!r.ok) throw Error(d.error||'Unable to load this course.');
        renderDistanceLessons(d.lessons||[], d.units||[], d.nextLessonId||null);
    }catch(e){ lessonsBox.innerHTML=`<p class="error-text">${escapeHtml(e.message)}</p>`; }
    viewer.scrollIntoView({behavior:'smooth', block:'start'});
}
window.openDistanceCourse=openDistanceCourse;

// V31.108 Course/Unit/Lesson Foundation: units are now shown (the API has
// always returned them) and the student gets a "Continue" action. A course
// with no units renders as a flat lesson list, exactly as before.
function renderDistanceLessonRow(l, nextLessonId){
    const isNext=!l.completed && l.id===nextLessonId;
    return `
        <div class="list-row" style="align-items:center">
            <div><strong>${l.completed?'✅ ':''}${isNext?'▶ ':''}${escapeHtml(l.title)}</strong><div class="subtitle">${escapeHtml(l.contentType||'')}${l.description?' • '+escapeHtml(l.description):''}</div></div>
            <div style="display:flex;gap:6px">
                ${l.hasBlocks?`<button class="btn btn-outline" type="button" onclick="BMTOpenBlockLesson('${escapeAttr(l.id)}')">Open</button>`:(safeWebUrl(l.url)?`<a class="btn btn-outline" href="${escapeAttr(safeWebUrl(l.url))}" target="_blank" rel="noopener noreferrer">Open</a>`:`<span class="subtitle">Link unavailable</span>`)}
                ${l.completed?'':`<button class="btn btn-success" type="button" onclick="completeDistanceLesson('${escapeAttr(l.id)}')">Mark complete</button>`}
            </div>
        </div>`;
}

function renderDistanceLessons(lessons, units, nextLessonId){
    const lessonsBox=document.getElementById('distanceCourseViewerLessons');
    units=units||[];
    const total=lessons.length, done=lessons.filter(l=>l.completed).length;
    const pct=total?Math.round(done/total*100):0;
    document.getElementById('distanceCourseViewerProgress').textContent=`${done}/${total} lessons complete (${pct}%)`;
    if(!lessons.length && !units.length){ lessonsBox.innerHTML='<p class="subtitle">No lessons published in this course yet.</p>'; return; }
    const nextLesson=lessons.find(l=>l.id===nextLessonId && !l.completed);
    const continueBox=nextLesson
        ? `<div class="list-row" style="align-items:center;border:1px solid #cfd8dc;border-radius:10px;padding:8px;margin-bottom:8px"><div><strong>▶ Continue learning</strong><div class="subtitle">${escapeHtml(nextLesson.title)}</div></div>${nextLesson.hasBlocks?`<button class="btn btn-primary" type="button" onclick="BMTOpenBlockLesson('${escapeAttr(nextLesson.id)}')">Continue</button>`:(safeWebUrl(nextLesson.url)?`<a class="btn btn-primary" href="${escapeAttr(safeWebUrl(nextLesson.url))}" target="_blank" rel="noopener noreferrer">Continue</a>`:`<span class="subtitle">Link unavailable</span>`)}</div>`
        : (total && done===total ? '<p class="subtitle">🎉 You have completed every lesson in this course.</p>' : '');
    const known=new Set(units.map(u=>u.id));
    const loose=lessons.filter(l=>!known.has(l.unitId));
    let html=continueBox+loose.map(l=>renderDistanceLessonRow(l,nextLessonId)).join('');
    units.forEach(u=>{
        const inUnit=lessons.filter(l=>l.unitId===u.id);
        const uDone=inUnit.filter(l=>l.completed).length;
        html+=`<div style="margin:12px 0 4px"><strong>📘 ${escapeHtml(u.title||'Unit')}</strong><div class="subtitle">${u.description?escapeHtml(u.description)+' • ':''}${uDone}/${inUnit.length} lessons</div></div>`;
        html+=inUnit.length?inUnit.map(l=>renderDistanceLessonRow(l,nextLessonId)).join(''):'<p class="subtitle">No lessons in this unit yet.</p>';
    });
    lessonsBox.innerHTML=html;
}

async function completeDistanceLesson(lessonId){
    try{
        const token=await auth.currentUser.getIdToken(true);
        const r=await fetch(`/api/student/lessons/${lessonId}/complete`,{method:'POST',headers:{Authorization:`Bearer ${token}`}});
        const d=await r.json(); if(!r.ok) throw Error(d.error||'Unable to update your progress.');
        const viewer=document.getElementById('distanceCourseViewer');
        if(viewer && viewer.dataset.courseId) await openDistanceCourse(viewer.dataset.courseId, document.getElementById('distanceCourseViewerTitle').textContent);
        if(d.certificateIssued){ showToast('🎉 Course complete! Your certificate is ready.', 'success'); loadDistanceCertificates(); }
        loadDistanceCourses();
    }catch(e){ showToast(e.message, 'error'); }
}
window.completeDistanceLesson=completeDistanceLesson;

function closeDistanceCourseViewer(){
    const viewer=document.getElementById('distanceCourseViewer'); if(viewer) viewer.style.display='none';
}
window.closeDistanceCourseViewer=closeDistanceCourseViewer;

async function loadDistanceCertificates(){
    const box=document.getElementById('distanceCertificateList'); if(!box)return;
    box.textContent='Loading…';
    try{
        const token=await auth.currentUser.getIdToken(true);
        const r=await fetch('/api/student/certificates',{headers:{Authorization:`Bearer ${token}`}});
        const d=await r.json(); if(!r.ok) throw Error(d.error||'Unable to load certificates.');
        const rows=d.certificates||[];
        box.innerHTML=rows.length?rows.map(c=>`<div class="list-row"><div><strong>🏆 ${escapeHtml(c.courseTitle)}</strong><div class="subtitle">${escapeHtml(c.certificateId)} • ${escapeHtml(c.issuedAt||'')}</div></div></div>`).join(''):'<p class="subtitle">Complete every lesson in a course to earn a certificate.</p>';
    }catch(e){ box.innerHTML=`<p class="error-text">${escapeHtml(e.message)}</p>`; }
}
window.loadDistanceCertificates=loadDistanceCertificates;

// V31.108 Phase E: Standalone Competition Dashboard - the first student-
// facing UI for a fully-built, tested backend (learning_challenge_routes.py)
// that until now had no way for a student to actually see or take a
// challenge. Reuses /api/challenges, /api/challenges/start|save|submit,
// /api/challenges/<id>/leaderboard, /api/challenges/mine (new in this
// phase) and /api/me/awards (already existed, unused by any student page).
let _competitionAttempt = null; // {attemptId, challengeId, deadlineAt, questions}
let _competitionTimerHandle = null;

async function loadCompetitionDashboard(){
    if(!document.getElementById('competitionChallengeList')) return;
    loadCompetitionChallenges();
    loadCompetitionResults();
    loadCompetitionAwards();
}
window.loadCompetitionDashboard=loadCompetitionDashboard;

async function loadCompetitionChallenges(){
    const box=document.getElementById('competitionChallengeList'); if(!box)return;
    box.textContent='Loading…';
    try{
        const token=await auth.currentUser.getIdToken(true);
        const r=await fetch('/api/challenges',{headers:{Authorization:`Bearer ${token}`}});
        const d=await r.json(); if(!r.ok) throw Error(d.error||'Unable to load challenges.');
        const rows=d.challenges||[];
        box.innerHTML=rows.length?rows.map(c=>{
            const feeLine = c.entryFee>0 ? ` • 💳 Entry fee: ${escapeHtml(c.entryFee)} ${escapeHtml(c.entryCurrency||'ETB')}` : ' • Free entry';
            // V31.108 Competition Payment Flow: a paid challenge whose fee
            // this student has not yet had server-verified shows a Pay
            // action instead of Start - the client never decides this
            // itself, it only reflects entryVerified as /api/challenges
            // computed it server-side from the challengeEntries collection.
            const needsPayment = c.entryFee>0 && !c.entryVerified;
            const startOrPayBtn = needsPayment
                ? `<button class="btn btn-primary" type="button" onclick="openCompetitionPayment('${escapeAttr(c.id)}','${escapeAttr(c.title)}',${c.entryFee},'${escapeAttr(c.entryCurrency||'ETB')}')">💳 Pay to Enter</button>`
                : `<button class="btn btn-primary" type="button" onclick="startCompetitionChallenge('${escapeAttr(c.id)}')">▶️ Start</button>`;
            return `
            <div class="list-row" style="align-items:center">
                <div><strong>${escapeHtml(c.title)}</strong><div class="subtitle">${c.subject?escapeHtml(c.subject)+' • ':''}${c.domain?escapeHtml(c.domain)+' • ':''}Grade ${escapeHtml(c.grade||'—')} • ${c.questionCount} questions • ${c.durationMinutes} min${c.isScholarshipChallenge?' • 🎓 Scholarship':''}${c.roundNumber>1?' • Round '+c.roundNumber:''}${feeLine}</div></div>
                <div style="display:flex;gap:6px">
                    <button class="btn btn-outline" type="button" onclick="viewCompetitionLeaderboard('${escapeAttr(c.id)}')">📊 Leaderboard</button>
                    ${startOrPayBtn}
                </div>
            </div>`;
        }).join(''):'<p class="subtitle">No challenges are currently open for your grade/eligibility.</p>';
    }catch(e){ box.innerHTML=`<p class="error-text">${escapeHtml(e.message)}</p>`; }
}
window.loadCompetitionChallenges=loadCompetitionChallenges;

// V31.108 Competition Payment Flow: reuses the existing manual
// /api/payments/submit path (same one subscription payments use) with
// plan:'challenge_entry' - not a new payment method. The server looks up
// the amount from the challenge's own entryFee; the amount shown here is
// for the student's information only.
let _competitionPayment = null; // {challengeId, paymentId}

function openCompetitionPayment(challengeId, title, fee, currency){
    _competitionPayment = {challengeId, paymentId: null};
    const panel=document.getElementById('competitionPaymentView');
    panel.style.display='block';
    panel.innerHTML=`
        <h4 style="margin:0 0 6px">💳 Pay to enter: ${escapeHtml(title)}</h4>
        <p class="subtitle">Amount due: <strong>${escapeHtml(fee)} ${escapeHtml(currency)}</strong>. Pay via your usual BMT payment method, then submit the transaction ID below for verification.</p>
        <div style="display:grid;gap:8px;max-width:360px">
            <select id="compPayProvider"><option value="manual">Bank transfer / Manual</option><option value="telebirr">Telebirr</option><option value="cbe">CBE</option></select>
            <input id="compPayTxId" placeholder="Transaction ID / reference">
            <button class="btn btn-primary" type="button" onclick="submitCompetitionPayment()">Submit for verification</button>
            <button class="btn btn-outline" type="button" onclick="document.getElementById('competitionPaymentView').style.display='none'">Cancel</button>
        </div>
        <div id="compPayStatus" class="subtitle" style="margin-top:8px"></div>`;
    panel.scrollIntoView({behavior:'smooth', block:'start'});
}
window.openCompetitionPayment=openCompetitionPayment;

async function submitCompetitionPayment(){
    if(!_competitionPayment) return;
    const txId=document.getElementById('compPayTxId')?.value.trim();
    const provider=document.getElementById('compPayProvider')?.value||'manual';
    const statusEl=document.getElementById('compPayStatus');
    if(!txId){ if(statusEl) statusEl.textContent='Enter a transaction ID first.'; return; }
    try{
        const token=await auth.currentUser.getIdToken(true);
        const r=await fetch('/api/payments/submit',{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${token}`},body:JSON.stringify({plan:'challenge_entry', challengeId:_competitionPayment.challengeId, provider, transactionId:txId})});
        const d=await r.json(); if(!r.ok) throw Error(d.error||'Could not submit payment.');
        _competitionPayment.paymentId=d.paymentId;
        if(statusEl) statusEl.innerHTML=`⏳ Submitted. Waiting for admin verification (payment ID: <code>${escapeHtml(d.paymentId)}</code>).<br><button class="btn btn-outline" type="button" style="margin-top:6px" onclick="confirmCompetitionEntry()">I've been verified — Confirm my entry</button>`;
    }catch(e){ if(statusEl) statusEl.textContent=e.message; }
}
window.submitCompetitionPayment=submitCompetitionPayment;

async function confirmCompetitionEntry(){
    if(!_competitionPayment || !_competitionPayment.paymentId) return;
    const statusEl=document.getElementById('compPayStatus');
    try{
        const token=await auth.currentUser.getIdToken(true);
        const r=await fetch(`/api/challenges/${_competitionPayment.challengeId}/entry/confirm`,{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${token}`},body:JSON.stringify({paymentId:_competitionPayment.paymentId})});
        const d=await r.json(); if(!r.ok) throw Error(d.error||'Payment has not been verified yet.');
        showToast('✅ Entry confirmed! You can now start the challenge.', 'success');
        document.getElementById('competitionPaymentView').style.display='none';
        loadCompetitionChallenges();
    }catch(e){ if(statusEl) statusEl.textContent='❌ '+e.message; }
}
window.confirmCompetitionEntry=confirmCompetitionEntry;

async function loadCompetitionResults(){
    const box=document.getElementById('competitionResultsList'); if(!box)return;
    box.textContent='Loading…';
    try{
        const token=await auth.currentUser.getIdToken(true);
        const r=await fetch('/api/challenges/mine',{headers:{Authorization:`Bearer ${token}`}});
        const d=await r.json(); if(!r.ok) throw Error(d.error||'Unable to load your results.');
        const rows=d.attempts||[];
        box.innerHTML=rows.length?rows.map(a=>`
            <div class="list-row"><div><strong>${a.status==='PASS'?'✅':'❌'} ${escapeHtml(a.challengeTitle)}</strong><div class="subtitle">${a.score}/${a.totalPoints} (${a.percentage}%) • ${escapeHtml(a.submittedAt||'')}</div></div>
            <button class="btn btn-outline" type="button" onclick="viewCompetitionLeaderboard('${escapeAttr(a.challengeId)}')">📊 Leaderboard</button></div>`).join(''):'<p class="subtitle">You have not submitted any challenge yet.</p>';
    }catch(e){ box.innerHTML=`<p class="error-text">${escapeHtml(e.message)}</p>`; }
}
window.loadCompetitionResults=loadCompetitionResults;

async function loadCompetitionAwards(){
    const box=document.getElementById('competitionAwardsList'); if(!box)return;
    box.textContent='Loading…';
    try{
        const token=await auth.currentUser.getIdToken(true);
        const r=await fetch('/api/me/awards',{headers:{Authorization:`Bearer ${token}`}});
        const d=await r.json(); if(!r.ok) throw Error(d.error||'Unable to load awards.');
        const rows=d.awards||[];
        box.innerHTML=rows.length?rows.map(a=>`
            <div class="list-row">
                <div><strong>🏅 Rank #${a.rank}</strong><div class="subtitle">${a.percentage}% • ${a.prizeAmount?a.prizeAmount+' '+escapeHtml(a.prizeCurrency):'No prize'}${a.certificateEligible?' • 🎓 Certificate eligible':''}</div></div>
                ${a.certificateId?`<span class="badge badge-free">Certificate: ${escapeHtml(a.certificateId)}</span>`:(a.certificateEligible?`<button class="btn btn-success" type="button" onclick="issueCompetitionCertificate('${escapeAttr(a.awardId)}')">🎓 Get Certificate</button>`:'')}
            </div>`).join(''):'<p class="subtitle">Awards appear here once a challenge you competed in has been finalized.</p>';
    }catch(e){ box.innerHTML=`<p class="error-text">${escapeHtml(e.message)}</p>`; }
}
window.loadCompetitionAwards=loadCompetitionAwards;

async function issueCompetitionCertificate(awardId){
    try{
        const token=await auth.currentUser.getIdToken(true);
        const r=await fetch(`/api/me/awards/${awardId}/certificate`,{method:'POST',headers:{Authorization:`Bearer ${token}`}});
        const d=await r.json(); if(!r.ok) throw Error(d.error||'Unable to issue certificate.');
        showToast('🎉 Certificate issued!', 'success');
        loadCompetitionAwards();
    }catch(e){ showToast(e.message, 'error'); }
}
window.issueCompetitionCertificate=issueCompetitionCertificate;

async function startCompetitionChallenge(challengeId){
    try{
        const token=await auth.currentUser.getIdToken(true);
        const r=await fetch('/api/challenges/start',{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${token}`},body:JSON.stringify({challengeId})});
        const d=await r.json(); if(!r.ok) throw Error(d.error||'Unable to start this challenge.');
        _competitionAttempt={attemptId:d.attemptId, challengeId:d.challengeId, deadlineAt:d.deadlineAt, answers:{}};
        document.getElementById('competitionListView').style.display='none';
        document.getElementById('competitionAttemptView').style.display='block';
        document.getElementById('competitionAttemptTitle').textContent=d.title||'Challenge';
        renderCompetitionQuestions(d.questions||[]);
        startCompetitionTimer(d.deadlineAt);
    }catch(e){ showToast(e.message, 'error'); }
}
window.startCompetitionChallenge=startCompetitionChallenge;

function renderCompetitionQuestions(questions){
    const box=document.getElementById('competitionAttemptQuestions');
    box.innerHTML=questions.map((q,i)=>`
        <div class="card" style="margin:0 0 10px;padding:12px">
            <div><strong>${i+1}. ${escapeHtml(q.question)}</strong> <span class="subtitle">(${q.points||1} pt)</span></div>
            <div style="margin-top:6px;display:grid;gap:6px">
                ${Object.entries(q.options||{}).map(([key,text])=>`
                    <label style="display:flex;gap:8px;align-items:center;cursor:pointer">
                        <input type="radio" name="comp_q_${escapeAttr(q.id)}" value="${escapeAttr(key)}" onchange="setCompetitionAnswer('${escapeAttr(q.id)}','${escapeAttr(key)}')">
                        <span>${escapeHtml(key)}. ${escapeHtml(text)}</span>
                    </label>`).join('')}
            </div>
        </div>`).join('');
}

function setCompetitionAnswer(questionId, value){
    if(!_competitionAttempt) return;
    _competitionAttempt.answers[questionId]=value;
}
window.setCompetitionAnswer=setCompetitionAnswer;

function startCompetitionTimer(deadlineIso){
    if(_competitionTimerHandle) clearInterval(_competitionTimerHandle);
    const deadline=new Date(deadlineIso).getTime();
    const tick=()=>{
        const remaining=Math.max(0, Math.floor((deadline-Date.now())/1000));
        const mm=String(Math.floor(remaining/60)).padStart(2,'0'), ss=String(remaining%60).padStart(2,'0');
        const el=document.getElementById('competitionAttemptTimer'); if(el) el.textContent=`⏱ ${mm}:${ss}`;
        if(remaining<=0){ clearInterval(_competitionTimerHandle); showToast('Time is up — submitting automatically.', 'info'); submitCompetitionAttempt(); }
    };
    tick(); _competitionTimerHandle=setInterval(tick, 1000);
}

async function saveCompetitionAttempt(){
    if(!_competitionAttempt) return;
    try{
        const token=await auth.currentUser.getIdToken(true);
        const r=await fetch('/api/challenges/save',{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${token}`},body:JSON.stringify({challengeId:_competitionAttempt.challengeId, attemptId:_competitionAttempt.attemptId, answers:_competitionAttempt.answers})});
        const d=await r.json(); if(!r.ok) throw Error(d.error||'Unable to save.');
        showToast('Progress saved.', 'success');
    }catch(e){ showToast(e.message, 'error'); }
}
window.saveCompetitionAttempt=saveCompetitionAttempt;

async function submitCompetitionAttempt(){
    if(!_competitionAttempt) return;
    try{
        const token=await auth.currentUser.getIdToken(true);
        const r=await fetch('/api/challenges/submit',{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${token}`},body:JSON.stringify({challengeId:_competitionAttempt.challengeId, attemptId:_competitionAttempt.attemptId, answers:_competitionAttempt.answers})});
        const d=await r.json(); if(!r.ok) throw Error(d.error||'Unable to submit.');
        if(_competitionTimerHandle) clearInterval(_competitionTimerHandle);
        showToast(`${d.status==='PASS'?'✅':'❌'} ${d.percentage}% — ${d.status}`, d.status==='PASS'?'success':'info');
        cancelCompetitionAttempt();
        loadCompetitionResults(); loadCompetitionChallenges(); loadCompetitionAwards();
    }catch(e){ showToast(e.message, 'error'); }
}
window.submitCompetitionAttempt=submitCompetitionAttempt;

function cancelCompetitionAttempt(){
    if(_competitionTimerHandle) clearInterval(_competitionTimerHandle);
    _competitionAttempt=null;
    document.getElementById('competitionAttemptView').style.display='none';
    document.getElementById('competitionListView').style.display='block';
}
window.cancelCompetitionAttempt=cancelCompetitionAttempt;

async function viewCompetitionLeaderboard(challengeId){
    const box=document.getElementById('competitionLeaderboardRows');
    const panel=document.getElementById('competitionLeaderboardView');
    panel.style.display='block'; box.textContent='Loading…';
    try{
        const token=await auth.currentUser.getIdToken(true);
        const r=await fetch(`/api/challenges/${challengeId}/leaderboard`,{headers:{Authorization:`Bearer ${token}`}});
        const d=await r.json(); if(!r.ok) throw Error(d.error||'Unable to load leaderboard.');
        const rows=d.leaderboard||[];
        box.innerHTML=rows.length?rows.map((row,i)=>`<div class="list-row" style="${row.isMe?'font-weight:700':''}"><div>#${i+1} ${row.isMe?'⭐ You':'Student'}</div><div>${row.percentage!=null?row.percentage+'%':''}</div></div>`).join(''):'<p class="subtitle">No submissions yet.</p>';
    }catch(e){ box.innerHTML=`<p class="error-text">${escapeHtml(e.message)}</p>`; }
    panel.scrollIntoView({behavior:'smooth', block:'start'});
}
window.viewCompetitionLeaderboard=viewCompetitionLeaderboard;
