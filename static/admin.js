import { initializeApp } from "https://www.gstatic.com/firebasejs/10.7.1/firebase-app.js";
import { 
    getFirestore, collection, addDoc, getDocs, query, where, orderBy, 
    onSnapshot, doc, updateDoc, setDoc, deleteDoc, serverTimestamp, getDoc 
} from "https://www.gstatic.com/firebasejs/10.7.1/firebase-firestore.js";
import { 
    getAuth, onAuthStateChanged, signInWithEmailAndPassword, 
    signOut, getIdToken, getIdTokenResult 
} from "https://www.gstatic.com/firebasejs/10.7.1/firebase-auth.js";
import { uploadFile, uploadAdminMedia } from './storage-service.js';

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

let isAdminAudioMuted = false;
let _logoFile = null;

// ==========================================================
// 🔐 AUTH
// ==========================================================
document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("adminCommunitySendBtn")?.addEventListener("click", sendAdminCommunityMessage);
    const loginScreen = document.getElementById("loginScreen");
    const adminPanel = document.getElementById("adminPanel");
    const loginError = document.getElementById("loginError");
    const emailLoginBtn = document.getElementById("emailLoginBtn");
    const logoutBtn = document.getElementById("logoutBtn");
    const loginEmail = document.getElementById("loginEmail");
    const loginPassword = document.getElementById("loginPassword");

    onAuthStateChanged(auth, async (user) => {
        if (!user) {
            loginScreen.style.display = "block";
            adminPanel.style.display = "none";
            return;
        }
        try {
            const tokenResult = await getIdTokenResult(user, true);
            const claims = tokenResult.claims || {};
            const isAdmin = claims.admin === true || claims.role === "admin";
            if (!isAdmin) {
                await signOut(auth);
                loginError.textContent = "⛔ Access denied. Only authorized admins can sign in.";
                loginScreen.style.display = "block";
                adminPanel.style.display = "none";
                return;
            }
            loginScreen.style.display = "none";
            adminPanel.style.display = "block";
            loadAdminBooks();
            loadAdminVideos();
            loadAdminQuizzes();
            loadAdminChats();
            loadAdminPayments();
            loadAdminReports();
            loadOrganizationLogo();
            loadTeacherRequests();
            loadServicePrices();
            loadAdminControlCenter();
            loadAdminConfigStatus();
        } catch (error) {
            console.error("Admin claim verification failed:", error);
            await signOut(auth);
            loginError.textContent = "⛔ Could not verify administrator authorization.";
            loginScreen.style.display = "block";
            adminPanel.style.display = "none";
        }
    });

    if (emailLoginBtn) {
        emailLoginBtn.addEventListener("click", async () => {
            loginError.textContent = "";
            const email = loginEmail.value.trim();
            const password = loginPassword.value;
            if (!email || !password) {
                loginError.textContent = "Please enter email and password.";
                return;
            }
            try {
                showLoadingOverlay('Signing in...');
                await signInWithEmailAndPassword(auth, email, password);
                hideLoadingOverlay();
            } catch (error) {
                hideLoadingOverlay();
                console.error("Login error:", error);
                let msg = "Login failed. ";
                if (error.code === 'auth/user-not-found') msg += "User not found.";
                else if (error.code === 'auth/wrong-password') msg += "Wrong password.";
                else if (error.code === 'auth/too-many-requests') msg += "Too many attempts. Try again later.";
                else msg += error.message;
                loginError.textContent = msg;
            }
        });
    }

    if (logoutBtn) {
        logoutBtn.addEventListener("click", () => {
            signOut(auth);
        });
    }

    document.querySelectorAll('[data-admin-preview]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const role = btn.dataset.adminPreview;
            const status = document.getElementById('adminPreviewStatus');
            try {
                btn.disabled = true;
                if (status) status.textContent = 'Creating secure preview…';
                const user = auth.currentUser;
                if (!user) throw new Error('Admin session expired.');
                const token = await user.getIdToken(true);
                const r = await fetch('/api/admin/preview-token', {
                    method: 'POST',
                    headers: {'Content-Type':'application/json','Authorization':`Bearer ${token}`},
                    body: JSON.stringify({role})
                });
                const d = await r.json().catch(() => ({}));
                if (!r.ok) throw new Error(d.error || `Preview request failed (${r.status})`);
                const path = role === 'student' ? '/student' : role === 'teacher' ? '/teacher' : '/parent';
                window.location.href = `${path}?preview=1&role=${encodeURIComponent(role)}&token=${encodeURIComponent(d.token)}`;
            } catch (e) {
                console.error('Admin preview failed:', e);
                if (status) status.textContent = `Preview failed: ${e.message}`;
                btn.disabled = false;
            }
        });
    });

    const adminAIBtn = document.getElementById('adminAIBtn');
    if (adminAIBtn) adminAIBtn.addEventListener('click', runAdminAICopilot);

    const pushForm = document.getElementById('pushForm');
    if (pushForm) pushForm.addEventListener('submit', sendPushNotification);

    const uploadBookBtn = document.getElementById("uploadBookBtn");
    if (uploadBookBtn) {
        uploadBookBtn.addEventListener("click", uploadBook);
    }
});

// ==========================================================
// 📚 1. BOOKS — ምስ Cloudinary
// ==========================================================
function toggleBookSource() {
    const provider = document.getElementById('bookStorageProvider')?.value || 'gcs';
    const fileGroup = document.getElementById('bookFileGroup');
    const driveGroup = document.getElementById('bookDriveGroup');
    if (fileGroup) fileGroup.style.display = provider === 'cloudinary' ? '' : 'none';
    if (driveGroup) driveGroup.style.display = provider === 'google_drive' ? '' : 'none';
}

function parseGoogleDriveFileUrl(rawUrl) {
    const value = String(rawUrl || '').trim();
    if (!value) throw new Error('Please enter a Google Drive file link.');
    let url;
    try { url = new URL(value); } catch (_) { throw new Error('Please enter a valid Google Drive URL.'); }
    const host = url.hostname.toLowerCase();
    if (host !== 'drive.google.com' && host !== 'docs.google.com') {
        throw new Error('Only Google Drive/Docs file links are supported for this storage option.');
    }

    let fileId = '';
    const pathMatch = url.pathname.match(/\/file\/d\/([^/]+)/i);
    const docsMatch = url.pathname.match(/\/document\/d\/([^/]+)/i);
    if (pathMatch) fileId = pathMatch[1];
    else if (docsMatch) fileId = docsMatch[1];
    else if (url.searchParams.get('id')) fileId = url.searchParams.get('id');
    else {
        const ucMatch = url.pathname.match(/\/uc(?:\.html)?/i);
        if (ucMatch && url.searchParams.get('id')) fileId = url.searchParams.get('id');
    }

    if (!fileId || !/^[A-Za-z0-9_-]{10,}$/.test(fileId)) {
        throw new Error('Could not find a valid Google Drive file ID in the link. Use a file link such as https://drive.google.com/file/d/FILE_ID/view');
    }

    return {
        fileId,
        sourceUrl: value,
        previewUrl: `https://drive.google.com/file/d/${encodeURIComponent(fileId)}/preview`,
        downloadUrl: `https://drive.google.com/uc?export=download&id=${encodeURIComponent(fileId)}`
    };
}

async function uploadBook() {
    const titleInput = document.getElementById("bookTitle");
    const fileInput = document.getElementById("bookFile");
    const classSelect = document.getElementById("bookClass");
    const providerSelect = document.getElementById('bookStorageProvider');
    const driveInput = document.getElementById('bookDriveUrl');

    if (!titleInput || !classSelect) return;

    const title = titleInput.value.trim();
    const className = classSelect.value;
    const provider = providerSelect?.value || 'gcs';

    if (!title) {
        showToast("Please enter the book title.", "error");
        return;
    }

    try {
        showLoadingOverlay(provider === 'google_drive' ? 'Saving Google Drive book...' : 'Uploading book to Google Cloud Storage...');

        let payload = {
            title,
            className,
            storageProvider: provider,
            createdAt: serverTimestamp()
        };

        if (provider === 'google_drive') {
            const drive = parseGoogleDriveFileUrl(driveInput?.value);
            payload = {
                ...payload,
                link: drive.previewUrl,
                fileUrl: drive.downloadUrl,
                previewUrl: drive.previewUrl,
                downloadUrl: drive.downloadUrl,
                sourceUrl: drive.sourceUrl,
                storagePublicId: drive.fileId,
                fileId: drive.fileId
            };
        } else {
            const file = fileInput?.files?.[0];
            if (!file) throw new Error('Please select a PDF/document file.');
            const result = provider === 'cloudinary'
                ? await uploadAdminMedia(file, 'raw')
                : await uploadFile(file, 'documents');
            if (!result.success) {
                throw new Error(result.error || (provider === 'cloudinary'
                    ? 'Cloudinary book upload failed.'
                    : 'Google Cloud Storage upload failed.'));
            }
            payload = {
                ...payload,
                link: result.url,
                fileUrl: result.url,
                previewUrl: result.url,
                downloadUrl: result.url,
                storagePublicId: result.fileId || result.publicId || "",
                storagePath: result.storagePath || result.path || result.fileId || '',
                storageBucket: result.bucket || '',
            };
        }

        await adminRequest('/api/admin/catalog/books', 'POST', payload);

        hideLoadingOverlay();
        showToast(provider === 'google_drive' ? "Google Drive book saved successfully!" : "Book uploaded successfully!", "success");
        titleInput.value = "";
        if (fileInput) fileInput.value = "";
        if (driveInput) driveInput.value = "";
        if (providerSelect) providerSelect.value = 'cloudinary';
        toggleBookSource();
        loadAdminBooks();
    } catch (error) {
        hideLoadingOverlay();
        console.error("Book save error:", error);
        showToast("Book save failed: " + error.message, "error");
    }
}

async function loadAdminBooks() {
    const container = document.getElementById("adminBooksList");
    if (!container) return;

    try {
        const querySnapshot = await getDocs(collection(db, "books"));
        container.innerHTML = "";

        if (querySnapshot.empty) {
            container.innerHTML = "No books uploaded yet.";
            return;
        }

        querySnapshot.forEach((docSnap) => {
            const book = docSnap.data();
            container.innerHTML += `
                <div class="item-row">
                    <div class="item-content">📚 <b>${book.title}</b> (Class: ${book.className}) <span class="badge">${book.storageProvider || "gcs"}</span></div>
                    <div class="item-actions">
                        <a href="${book.link || book.fileUrl}" target="_blank" class="btn btn-outline" style="padding:4px 12px; font-size:12px;">Open</a>
                        <button class="btn btn-danger" onclick="deleteItem('books', '${docSnap.id}')" style="padding:4px 12px; font-size:12px;">Delete</button>
                    </div>
                </div>
            `;
        });
    } catch (error) {
        console.error("Error loading admin books:", error);
        container.innerHTML = "Error loading books.";
    }
}

// ==========================================================
// 🎥 2. VIDEOS — ምስ Cloudinary
// ==========================================================
window.uploadBook = uploadBook;
window.toggleBookSource = toggleBookSource;

window.uploadVideo = async function() {
    const titleInput = document.getElementById("videoTitle");
    const fileInput = document.getElementById("videoFile");
    const classSelect = document.getElementById("videoClass");
    const isPaidCheckbox = document.getElementById("isPaidVideo");
    const youtubeInput = document.getElementById("videoYoutubeUrl");

    if (!titleInput || !fileInput || !classSelect) return;

    const title = titleInput.value.trim();
    const file = fileInput.files[0];
    const youtubeUrl = youtubeInput?.value.trim() || "";
    const className = classSelect.value;
    const isPaid = isPaidCheckbox ? isPaidCheckbox.checked : false;

    if (!title || (!file && !youtubeUrl)) {
        showToast("Enter a title and choose a video file or paste a YouTube URL.", "error");
        return;
    }
    if (file && youtubeUrl) {
        showToast("Use either a video file or a YouTube URL, not both.", "error");
        return;
    }

    try {
        showLoadingOverlay('Uploading video...');
        
        let videoUrl = youtubeUrl;
        let provider = youtubeUrl ? "youtube" : "cloudinary";
        if (file) {
            const result = await uploadAdminMedia(file, 'video');
            if (!result.success) {
                throw new Error(result.error || "Cloudinary upload failed. Check the unsigned upload preset and cloud name.");
            }
            videoUrl = result.url;
        }
        if (youtubeUrl && !/^https?:\/\/(www\.)?(youtube\.com\/(watch\?v=|live\/|shorts\/)|youtu\.be\/)/i.test(youtubeUrl)) {
            throw new Error("Please enter a valid YouTube watch, live, shorts, or youtu.be URL.");
        }

        await adminRequest('/api/admin/catalog/videos', 'POST', {
            title, className, url: videoUrl, provider, isPaid
        });

        hideLoadingOverlay();
        showToast("Video uploaded successfully!", "success");
        titleInput.value = "";
        fileInput.value = "";
        if (youtubeInput) youtubeInput.value = "";
        if (isPaidCheckbox) isPaidCheckbox.checked = false;
        loadAdminVideos();
        loadAdminReports();
    } catch (error) {
        hideLoadingOverlay();
        console.error("Video upload error:", error);
        showToast("Video upload failed: " + error.message, "error");
    }
};

async function loadAdminVideos() {
    const container = document.getElementById("adminVideosList");
    if (!container) return;

    try {
        const querySnapshot = await getDocs(collection(db, "videos"));
        container.innerHTML = "";

        if (querySnapshot.empty) {
            container.innerHTML = "No videos uploaded yet.";
            return;
        }

        querySnapshot.forEach((docSnap) => {
            const video = docSnap.data();
            const badge = video.isPaid ? `<span class="badge badge-paid">Paid</span>` : `<span class="badge badge-free">Free</span>`;
            container.innerHTML += `
                <div class="item-row">
                    <div class="item-content">🎥 <b>${video.title}</b> (${video.className}) ${badge} 👁️${video.views || 0}</div>
                    <div class="item-actions">
                        <a href="${video.url}" target="_blank" class="btn btn-outline" style="padding:4px 12px; font-size:12px;">Watch</a>
                        <button class="btn btn-danger" onclick="deleteItem('videos', '${docSnap.id}')" style="padding:4px 12px; font-size:12px;">Delete</button>
                    </div>
                </div>
            `;
        });
    } catch (error) {
        console.error("Error loading admin videos:", error);
        container.innerHTML = "Error loading videos.";
    }
}

// ==========================================================
// 📡 3. LIVE STREAM
// ==========================================================
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

window.startLiveStream = async function() {
    const title=document.getElementById('liveTitle')?.value.trim()||''; const raw=document.getElementById('liveLink')?.value.trim()||''; const link=toEmbedUrl(raw); const type=document.getElementById('liveType')?.value||'Free';
    if(!link)return showToast('Please enter a stream link.','error');
    if(/t\.me\/|telegram\.me\//i.test(raw))return showToast('Telegram bot links cannot be used as live stream links. Use YouTube Live or another supported stream URL.','error');
    try{showLoadingOverlay('Starting live stream...');await adminRequest('/api/admin/live-stream','PUT',{title,url:link,type,isActive:true,isMuted:isAdminAudioMuted});showToast('Live stream started.','success');watchLiveStatus();}catch(e){showToast('Failed to start live stream: '+e.message,'error');}finally{hideLoadingOverlay();}
};

window.stopLiveStream = async function(){try{showLoadingOverlay('Stopping live stream...');await adminRequest('/api/admin/live-stream','PUT',{isActive:false,url:'',title:''});showToast('Live stream stopped.','success');watchLiveStatus();}catch(e){showToast('Failed to stop live stream: '+e.message,'error');}finally{hideLoadingOverlay();}};

window.toggleAdminAudioMute = async function(){isAdminAudioMuted=!isAdminAudioMuted;try{await adminRequest('/api/admin/live-stream','PUT',{isActive:true,isMuted:isAdminAudioMuted});showToast(isAdminAudioMuted?'Audio muted':'Audio unmuted','info');}catch(e){isAdminAudioMuted=!isAdminAudioMuted;showToast(e.message,'error');}};

// ==========================================================
// ✍️ 4. QUIZZES
// ==========================================================
window.uploadQuiz = async function() {
    const classSelect = document.getElementById("quizClass");
    const titleInput = document.getElementById("quizTitle");
    const questionInput = document.getElementById("quizQuestion");
    const imageInput = document.getElementById("quizImageFile");

    const optA = document.getElementById("optA");
    const optB = document.getElementById("optB");
    const optC = document.getElementById("optC");
    const optD = document.getElementById("optD");
    const correctOpt = document.getElementById("correctOpt");

    if (!classSelect || !questionInput || !correctOpt) return;

    const className = classSelect.value;
    const title = titleInput ? titleInput.value.trim() : "Quiz";
    const question = questionInput.value.trim();
    const correctAnswer = correctOpt.value.trim().toUpperCase();
    const imageFile = imageInput && imageInput.files[0] ? imageInput.files[0] : null;

    if (!question || !correctAnswer) {
        showToast("Please enter question and correct answer.", "error");
        return;
    }

    try {
        showLoadingOverlay('Saving quiz...');
        let imageUrl = "";
        if (imageFile) {
            // ✅ Image → Cloudinary
            const result = await uploadAdminMedia(imageFile, 'image');
            if (result.success) {
                imageUrl = result.url;
            } else {
                throw new Error(result.error);
            }
        }

        await adminRequest('/api/admin/catalog/quizzes', 'POST', {
            className,
            title,
            question,
            imageUrl,
            options: {
                A: optA ? optA.value.trim() : "",
                B: optB ? optB.value.trim() : "",
                C: optC ? optC.value.trim() : "",
                D: optD ? optD.value.trim() : ""
            },
            correctAnswer
        });

        hideLoadingOverlay();
        showToast("Quiz saved successfully!", "success");
        questionInput.value = "";
        if (titleInput) titleInput.value = "";
        if (optA) optA.value = "";
        if (optB) optB.value = "";
        if (optC) optC.value = "";
        if (optD) optD.value = "";
        if (correctOpt) correctOpt.value = "";
        if (imageInput) imageInput.value = "";
        loadAdminQuizzes();
    } catch (error) {
        hideLoadingOverlay();
        console.error("Quiz upload error:", error);
        showToast("Failed to save quiz: " + error.message, "error");
    }
};

async function loadAdminQuizzes() {
    const container = document.getElementById("adminQuizzesList");
    if (!container) return;

    try {
        const payload = await adminRequest('/api/admin/catalog/quizzes');
        const quizzes = Array.isArray(payload.quizzes) ? payload.quizzes : [];
        container.innerHTML = "";

        if (!quizzes.length) {
            container.innerHTML = "No quizzes created yet.";
            return;
        }

        quizzes.forEach((quiz) => {
            container.innerHTML += `
                <div class="item-row">
                    <div class="item-content">✍️ <b>${escapeHtml(quiz.title || "Quiz")}</b> (${escapeHtml(quiz.className || "")})</div>
                    <div class="item-actions">
                        <button class="btn btn-danger" onclick="deleteItem('quizzes', '${escapeAttr(quiz.id)}')" style="padding:4px 12px; font-size:12px;">Delete</button>
                    </div>
                </div>
            `;
        });
    } catch (error) {
        console.error("Error loading quizzes:", error);
        container.innerHTML = "Error loading quizzes.";
    }
}

// ==========================================================
// 💳 5. PAYMENTS
// ==========================================================
async function loadAdminPayments() {
    const container = document.getElementById("adminPaymentsList");
    if (!container) return;

    try {
        const q = query(collection(db, "payments"), orderBy("timestamp", "desc"));
        onSnapshot(q, (snapshot) => {
            container.innerHTML = "";

            if (snapshot.empty) {
                container.innerHTML = "No payments submitted yet.";
                return;
            }

            snapshot.forEach((docSnap) => {
                const p = docSnap.data();
                const status = p.status || "Pending";
                const isAutoVerified = p.autoVerified || false;
                const plan = p.plan || 'monthly';
                const amount = p.amount || 0;
                
                let statusBadge = "";
                if (status === "Approved") {
                    statusBadge = `<span class="badge badge-approved">✅ Approved ${isAutoVerified ? '🤖' : '👤'}</span>`;
                } else if (status === "Rejected") {
                    statusBadge = `<span class="badge badge-rejected">❌ Rejected</span>`;
                } else {
                    statusBadge = `<span class="badge badge-pending">⏳ Pending ${isAutoVerified ? '🤖' : '👤'}</span>`;
                }

                container.innerHTML += `
                    <div class="item-row">
                        <div class="item-content">
                            💳 <b>${p.studentName || "Unknown"}</b> (${p.className || "-"})
                            <div style="font-size:0.85em; color:var(--text-muted);">
                                Plan: ${plan} | Amount: $${amount} | TX: <code>${p.transactionId || ""}</code>
                                ${isAutoVerified ? '🤖 Auto-verified' : '👤 Manual'}
                            </div>
                            ${statusBadge}
                        </div>
                        <div class="item-actions">
                            ${status === 'Pending' ? `
                                <button class="btn btn-success" style="padding:6px 10px; font-size:12px;" onclick="approvePayment('${docSnap.id}')">Approve</button>
                                <button class="btn btn-danger" onclick="rejectPayment('${docSnap.id}')" style="padding:6px 10px; font-size:12px;">Reject</button>
                            ` : `
                                <span style="font-size:0.8em; color:var(--text-muted);">${status}</span>
                            `}
                        </div>
                    </div>
                `;
            });
        });
    } catch (error) {
        console.error("Error loading payments:", error);
        container.innerHTML = "Error loading payments.";
    }
}

async function adminRequest(path, method = "GET", body = undefined) {
    const user = auth.currentUser;
    if (!user) throw new Error("Admin session expired. Please sign in again.");
    const token = await user.getIdToken(true);
    const options = { method, headers: { "Authorization": `Bearer ${token}` } };
    if (body !== undefined) { options.headers["Content-Type"] = "application/json"; options.body = JSON.stringify(body); }
    const response = await fetch(path, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
}

async function adminApi(path, body) { return adminRequest(path, "POST", body); }

window.approvePayment = async function(paymentId) {
    try {
        showLoadingOverlay('Approving payment and unlocking account...');
        await adminApi('/api/payments/approve', { paymentId });
        hideLoadingOverlay();
        showToast("Payment approved and account unlocked instantly!", "success");
        loadAdminReports();
    } catch (error) {
        hideLoadingOverlay();
        console.error("Approve payment error:", error);
        showToast("Could not approve payment: " + error.message, "error");
    }
};

window.rejectPayment = async function(paymentId) {
    try {
        showLoadingOverlay('Rejecting payment...');
        await adminApi('/api/payments/reject', { paymentId });
        hideLoadingOverlay();
        showToast("Payment rejected.", "info");
        loadAdminReports();
    } catch (error) {
        hideLoadingOverlay();
        console.error("Reject payment error:", error);
        showToast("Could not reject payment: " + error.message, "error");
    }
};

// ==========================================================
// 💬 6. CHAT
// ==========================================================
async function loadAdminChats() {
    const container=document.getElementById('adminChatList'); if(!container)return;
    try {
        const data=await adminRequest('/api/admin/chat/messages'); const rows=data.messages||[];
        container.innerHTML=rows.length?rows.map(chat=>{
            const sender=escapeHtml(chat.userName||chat.senderName||'User'); const text=escapeHtml(chat.messageText||chat.message||'');
            const reactions=chat.reactions||{};
            return `<div class="item-row" style="align-items:flex-start"><div class="item-content"><div><b>${sender}</b> <span class="subtitle">${escapeHtml(chat.className||'General')}</span></div>${chat.replyTo?`<div class="subtitle" style="margin:4px 0">↩️ Reply to: ${escapeHtml(chat.replyToText||'message')}</div>`:''}<div style="margin-top:5px">${text}</div><div style="display:flex;gap:4px;flex-wrap:wrap;margin-top:8px"><button class="btn btn-neutral" data-chat-reply="${chat.id}">↩️ Reply</button><button class="btn btn-neutral" data-chat-react="${chat.id}:heart">❤️ ${reactions.heart||0}</button><button class="btn btn-neutral" data-chat-react="${chat.id}:like">👍 ${reactions.like||0}</button><button class="btn btn-neutral" data-chat-react="${chat.id}:dislike">👎 ${reactions.dislike||0}</button><button class="btn btn-neutral" data-chat-react="${chat.id}:fire">🔥 ${reactions.fire||0}</button></div></div><div class="item-actions"><button class="btn btn-danger" data-chat-delete="${chat.id}">Delete</button></div></div>`;
        }).join(''):'<p class="subtitle">No chat messages found.</p>';
        container.querySelectorAll('[data-chat-reply]').forEach(b=>b.onclick=async()=>{const msg=prompt('Reply to this message:');if(!msg?.trim())return;try{await adminRequest('/api/admin/chat/reply','POST',{messageId:b.dataset.chatReply,message:msg.trim()});showToast('Reply sent.','success');loadAdminChats();}catch(e){showToast(e.message,'error')}});
        container.querySelectorAll('[data-chat-react]').forEach(b=>b.onclick=async()=>{const [id,reaction]=b.dataset.chatReact.split(':');try{await adminRequest('/api/admin/chat/reaction','POST',{messageId:id,reaction});loadAdminChats();}catch(e){showToast(e.message,'error')}});
        container.querySelectorAll('[data-chat-delete]').forEach(b=>b.onclick=async()=>{if(!confirm('Delete this message?'))return;try{await adminRequest('/api/admin/chat/messages/'+encodeURIComponent(b.dataset.chatDelete),'DELETE');loadAdminChats();}catch(e){showToast(e.message,'error')}});
    } catch(e){container.innerHTML=`<p class="error-text">${escapeHtml(e.message)}</p>`;}
}

// ==========================================================
// 👨‍🏫 7. TEACHER REQUESTS
// ==========================================================
document.getElementById('parentLinkForm')?.addEventListener('submit', async e => {
    e.preventDefault();
    const btn=document.getElementById('parentLinkBtn'), status=document.getElementById('parentLinkStatus');
    const parentEmail=document.getElementById('parentLinkParentEmail').value.trim();
    const studentEmail=document.getElementById('parentLinkStudentEmail').value.trim();
    if(!parentEmail || !studentEmail){ status.textContent='❌ Both email addresses are required.'; return; }
    btn.disabled=true; status.textContent='Linking accounts…';
    try {
        const data=await adminApi('/api/admin/parent-links',{parentEmail,studentEmail});
        status.textContent=`✅ Parent linked successfully. Link ID: ${data.linkId}`;
        e.target.reset();
        showToast('Parent linked to student.','success');
    } catch(err) { status.textContent='❌ '+err.message; showToast(err.message,'error'); }
    finally { btn.disabled=false; }
});

async function loadTeacherRequests() {
    const container=document.getElementById('teacherRequestsList'); if(!container)return;
    try { const data=await adminRequest('/api/admin/teacher-requests'); const rows=data.requests||[]; container.innerHTML=rows.length?rows.map(x=>`<div class="item-row" style="align-items:flex-start"><div class="item-content"><strong>${escapeHtml(x.name||'Teacher')}</strong> (${escapeHtml(x.email||'')})<div class="subtitle">Subjects: ${escapeHtml((x.subjects||[]).join(', '))} | Grades: ${escapeHtml((x.classes||[]).join(', '))}</div><div class="subtitle">Education: ${escapeHtml(x.educationLevel||'Not provided')}${x.institution?' • '+escapeHtml(x.institution):''} | Experience: ${escapeHtml(x.experienceYears||'Not provided')} years</div>${x.experience?`<div style="margin-top:5px">${escapeHtml(x.experience)}</div>`:''}${x.certifications?`<div class="subtitle">Qualifications: ${escapeHtml(x.certifications)}</div>`:''}${x.bio?`<div class="subtitle">Bio: ${escapeHtml(x.bio)}</div>`:''}</div><div class="item-actions"><button class="btn btn-success" onclick="approveTeacher('${x.id}')">✅ Approve</button><button class="btn btn-danger" onclick="rejectTeacher('${x.id}')">❌ Reject</button></div></div>`).join(''):'<p class="subtitle">No pending teacher requests.</p>'; } catch(e){container.innerHTML=`<p class="error-text">${escapeHtml(e.message)}</p>`;}
}

async function approveTeacher(requestId) {
    try {
        showLoadingOverlay('Approving teacher...');
        await adminApi('/api/admin/teachers/approve', { requestId });
        hideLoadingOverlay();
        showToast('✅ Teacher approved securely!', 'success');
        loadTeacherRequests();
    } catch (error) {
        hideLoadingOverlay();
        console.error('Approve teacher error:', error);
        showToast('Failed to approve teacher: ' + error.message, 'error');
    }
}

async function rejectTeacher(requestId) {
    try {
        const reason = window.prompt('Optional rejection reason:') || '';
        showLoadingOverlay('Rejecting teacher...');
        await adminApi('/api/admin/teachers/reject', { requestId, reason });
        hideLoadingOverlay();
        showToast('Teacher rejected securely.', 'info');
        loadTeacherRequests();
    } catch (error) {
        hideLoadingOverlay();
        console.error('Reject teacher error:', error);
        showToast('Failed to reject teacher: ' + error.message, 'error');
    }
}

async function runAdminAICopilot() {
    const task = document.getElementById('adminAITask')?.value.trim();
    const context = document.getElementById('adminAIContext')?.value.trim();
    const mode = document.getElementById('adminAIMode')?.value || 'general';
    const button = document.getElementById('adminAIBtn');
    const status = document.getElementById('adminAIStatus');
    const result = document.getElementById('adminAIResult');
    if (!task) { showToast('Write an AI task first.', 'error'); return; }
    button.disabled = true; button.textContent = 'Thinking…'; status.textContent = 'Using BMT Gemini…'; result.style.display = 'block'; result.textContent = '🤖 AI is working…';
    try {
        const data = await adminRequest('/api/admin/ai-copilot', 'POST', {task, context, mode});
        result.textContent = data.answer || 'No answer returned.';
        status.textContent = Number.isFinite(data.remainingToday) ? `AI requests remaining today: ${data.remainingToday}` : 'AI ready';
    } catch (e) {
        result.textContent = e.message || 'Admin AI is unavailable.';
        status.textContent = 'AI request failed';
        showToast(e.message || 'Admin AI error', 'error');
    } finally {
        button.disabled = false; button.textContent = '🤖 Ask BMT AI';
    }
}

// ==========================================================
// 🗑️ 8. DELETE
// ==========================================================
window.deleteItem = async function(collectionName, docId) {
    if (!confirm("Are you sure you want to delete this item?")) return;

    try {
        showLoadingOverlay('Deleting...');
        await deleteDoc(doc(db, collectionName, docId));
        hideLoadingOverlay();
        showToast("Item deleted successfully!", "success");
        if (collectionName === 'books') loadAdminBooks();
        if (collectionName === 'videos') { loadAdminVideos(); loadAdminReports(); }
        if (collectionName === 'quizzes') loadAdminQuizzes();
        if (collectionName === 'chats') loadAdminChats();
    } catch (error) {
        hideLoadingOverlay();
        console.error("Delete error:", error);
        showToast("Failed to delete: " + error.message, "error");
    }
};

// ==========================================================
// 🔴 9. LIVE STATUS
// ==========================================================
async function watchLiveStatus(){
    const box=document.getElementById('liveStatusBox'); if(!box)return;
    try{const data=await adminRequest('/api/admin/live-stream');const d=data.stream||{};if(d.isActive&&d.url){box.style.background='rgba(46,204,113,.12)';box.style.border='2px solid var(--success)';box.textContent=`🔴 LIVE is active — Students can watch now. (${d.title||'Live'})`;}else{box.style.background='rgba(0,0,0,.05)';box.style.border='1px solid var(--border)';box.textContent='⚪ No live stream active. Enter a valid stream link and click Start Live.';}}catch(e){box.textContent='⚠️ '+e.message;}}

// ==========================================================
// 📊 10. ADMIN REPORTS
// ==========================================================
async function loadAdminReports() {
    await loadStudentCount();
    await loadPaymentSummary();
    await loadVideoStats();
}

async function loadStudentCount() {
    try {
        const snap = await getDocs(collection(db, "users"));
        const students = snap.docs.filter(d => !d.data().isAdmin);
        const el = document.getElementById('studentCount');
        if (el) {
            el.textContent = students.length;
        }
    } catch (error) {
        console.error('Student count error:', error);
    }
}

async function loadPaymentSummary() {
    try {
        const snap = await getDocs(collection(db, "payments"));
        const payments = snap.docs.map(d => d.data());
        const totalPayments = payments.length;
        const approvedPayments = payments.filter(p => p.status === 'Approved').length;
        const pendingPayments = payments.filter(p => p.status === 'Pending').length;
        const rejectedPayments = payments.filter(p => p.status === 'Rejected').length;
        
        const mappings = {
            'totalPayments': totalPayments,
            'approvedPayments': approvedPayments,
            'pendingPayments': pendingPayments,
            'rejectedPayments': rejectedPayments
        };
        
        Object.entries(mappings).forEach(([id, value]) => {
            const el = document.getElementById(id);
            if (el) el.textContent = value;
        });
    } catch (error) {
        console.error('Payment summary error:', error);
    }
}

async function loadVideoStats() {
    try {
        const snap = await getDocs(collection(db, "videos"));
        const videos = snap.docs.map(d => d.data());
        const totalViews = videos.reduce((sum, v) => sum + (v.views || 0), 0);
        
        const totalVideosEl = document.getElementById('totalVideos');
        if (totalVideosEl) totalVideosEl.textContent = videos.length;
        
        const totalViewsEl = document.getElementById('totalViews');
        if (totalViewsEl) totalViewsEl.textContent = totalViews;
        
        const topVideoEl = document.getElementById('topVideo');
        if (topVideoEl) {
            if (videos.length > 0) {
                const topVideo = videos.reduce((a, b) => (a.views || 0) > (b.views || 0) ? a : b);
                topVideoEl.textContent = `${topVideo.title} (${topVideo.views || 0} views)`;
            } else {
                topVideoEl.textContent = 'No videos yet';
            }
        }
    } catch (error) {
        console.error('Video stats error:', error);
    }
}

// ==========================================================
// 🏢 11. ORGANIZATION LOGO
// ==========================================================
window.openLogoModal = function() {
    const modal = document.getElementById('logoModal');
    if (modal) modal.classList.add('active');
    loadCurrentLogo();
};

window.closeLogoModal = function() {
    const modal = document.getElementById('logoModal');
    if (modal) modal.classList.remove('active');
    _logoFile = null;
};

async function loadCurrentLogo() {
    try {
        const data = await adminRequest('/api/admin/settings/organization');
        const logoUrl = data.organization?.logoUrl || '';
        const preview = document.getElementById('logoPreview');
        const currentDisplay = document.getElementById('currentLogoDisplay');
        if (logoUrl) {
            if (preview) { preview.src = logoUrl; preview.classList.add('has-image'); }
            if (currentDisplay) currentDisplay.src = logoUrl;
        }
    } catch (error) { console.error('Load logo error:', error); }
}

async function loadOrganizationLogo() { return loadCurrentLogo(); }

window.saveOrganizationLogo = async function() {
    const file = _logoFile;
    if (!file) { showToast('Please select a logo image.', 'error'); return; }
    showLoadingOverlay('Uploading logo...');
    try {
        const user = auth.currentUser;
        if (!user) throw new Error('Admin session expired.');
        const token = await user.getIdToken(true);
        const form = new FormData(); form.append('file', file);
        const response = await fetch('/api/admin/settings/organization/logo', { method:'POST', headers:{Authorization:`Bearer ${token}`}, body:form });
        const data = await response.json().catch(()=>({}));
        if (!response.ok) throw new Error(data.error || 'Logo upload failed.');
        const logoUrl = data.logoUrl;
        document.querySelectorAll('.org-logo, #navLogo, #currentLogoDisplay').forEach(img => { if (img) img.src = logoUrl; });
        _logoFile = null; closeLogoModal(); showToast('Logo updated successfully!', 'success');
    } catch (error) { console.error('Logo upload error:', error); showToast('Failed to upload logo: ' + error.message, 'error'); }
    finally { hideLoadingOverlay(); }
};

window.removeOrganizationLogo = async function() {
    if (!confirm('Are you sure you want to remove the organization logo?')) return;
    try {
        showLoadingOverlay('Removing logo...');
        await adminRequest('/api/admin/settings/organization/logo', 'DELETE');
        document.querySelectorAll('.org-logo, #navLogo, #currentLogoDisplay').forEach(img => { if (img) img.src = '/static/logo.png'; });
        showToast('Logo removed successfully.', 'info');
    } catch (error) { showToast('Failed to remove logo: ' + error.message, 'error'); }
    finally { hideLoadingOverlay(); }
};

// ==========================================================
// 💰 12. SERVICE PRICES
// ==========================================================
async function loadServicePrices() {
    try {
        const data = await adminRequest('/api/admin/settings/prices');
        const p = data.prices || {};
        ['monthly','yearly','video','exam','quiz','live'].forEach(k => { const el=document.getElementById('price'+k[0].toUpperCase()+k.slice(1)); if(el) el.value=Number(p[k] ?? 0); });
        const currency=document.getElementById('priceCurrency'); if(currency) currency.value=p.currency||'ETB';
        updatePriceLabels(p.currency||'ETB');
    } catch (error) { showToast('Failed to load prices: '+error.message, 'error'); }
}

function updatePriceLabels(currency) {
    document.querySelectorAll('[data-price-currency]').forEach(el => el.textContent = currency);
}

window.saveServicePrices = async function() {
    const body={currency:document.getElementById('priceCurrency')?.value||'ETB'};
    for(const k of ['monthly','yearly','video','exam','quiz','live']) { const el=document.getElementById('price'+k[0].toUpperCase()+k.slice(1)); const v=Number(el?.value); if(!Number.isFinite(v)||v<0){showToast('Please enter valid non-negative prices.','error');return;} body[k]=v; }
    try {
        showLoadingOverlay('Saving prices...');
        const data=await adminRequest('/api/admin/settings/prices','PUT',body);
        const p=data.prices||body; updatePriceLabels(p.currency);
        const status=document.getElementById('priceStatus'); if(status){status.style.display='block';status.style.background='var(--success-light)';status.innerHTML=`<strong>✅ Prices Updated (${p.currency})</strong><div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px;font-size:.9em;">${['monthly','yearly','video','exam','quiz','live'].map(k=>`<div>${k}: ${Number(p[k]).toFixed(2)} ${p.currency}</div>`).join('')}</div>`;}
        showToast('Prices saved successfully.','success');
    } catch(e){showToast('Failed to save prices: '+e.message,'error');}
    finally{hideLoadingOverlay();}
};



async function createInAppAnnouncement(event) {
    event.preventDefault();
    const status=document.getElementById('announcementStatus');
    try {
        const idToken=await getIdToken(auth.currentUser, true);
        const response=await fetch('/api/admin/announcements',{method:'POST',headers:{'Content-Type':'application/json','Authorization':`Bearer ${idToken}`},body:JSON.stringify({title:document.getElementById('announcementTitle').value.trim(),message:document.getElementById('announcementMessage').value.trim(),kind:'announcement',targetUid:'all'})});
        const data=await response.json().catch(()=>({}));
        if(!response.ok) throw new Error(data.error||'Announcement failed');
        status.textContent=`✅ Published. ${data.push?.sent||0} browser push notifications sent.`;
        event.target.reset();
        document.getElementById('announcementTitle').value='';
        showToast('Announcement published.', 'success');
    } catch(e) { status.textContent='❌ '+e.message; showToast(e.message,'error'); }
}

document.addEventListener('DOMContentLoaded',()=>{
    const f=document.getElementById('announcementForm');
    if(f) f.addEventListener('submit',createInAppAnnouncement);
});

async function sendPushNotification(event) {
    event.preventDefault(); const status=document.getElementById('pushStatus');
    try {
        const title=document.getElementById('pushTitle').value.trim(); const message=document.getElementById('pushMessage').value.trim();
        if(!message)throw new Error('Message is required.');
        const tokenData=await adminRequest('/api/admin/fcm-tokens'); if(!tokenData.tokens?.length)throw new Error('No students have enabled notifications yet.');
        const data=await adminRequest('/api/notifications/send','POST',{tokens:tokenData.tokens,title,message});
        status.textContent=`✅ Sent: ${data.sent}; failed: ${data.failed}`; showToast('Notification sent.','success');
    } catch(e){status.textContent='❌ '+e.message;showToast(e.message,'error');}
}

// ==========================================================
// 🔔 13. TOAST & LOADING HELPERS
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

// ==========================================================
// 🚀 14. INIT
// ==========================================================
document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("adminCommunitySendBtn")?.addEventListener("click", sendAdminCommunityMessage);
    watchLiveStatus();
});

// Make functions globally available
window.uploadVideo = window.uploadVideo;
window.uploadQuiz = window.uploadQuiz;
window.startLiveStream = window.startLiveStream;
window.stopLiveStream = window.stopLiveStream;
window.toggleAdminAudioMute = window.toggleAdminAudioMute;
window.approvePayment = window.approvePayment;
window.rejectPayment = window.rejectPayment;
window.deleteItem = window.deleteItem;
window.openLogoModal = window.openLogoModal;
window.closeLogoModal = window.closeLogoModal;
window.previewLogo = window.previewLogo;
window.saveOrganizationLogo = window.saveOrganizationLogo;
window.removeOrganizationLogo = window.removeOrganizationLogo;
window.approveTeacher = approveTeacher;
window.rejectTeacher = rejectTeacher;
window.saveServicePrices = window.saveServicePrices;
window.loadServicePrices = loadServicePrices;
// ==========================================================
// 📚 NATIONAL EXAM ARCHIVE ADMIN
// ==========================================================
window.toggleArchiveStream = function() {
    const grade = document.getElementById('archiveGrade')?.value;
    const group = document.getElementById('archiveStreamGroup');
    if (group) group.style.display = grade === '12' ? 'block' : 'none';
};

async function archiveAdminApi(method, path, body) {
    const user = auth.currentUser;
    if (!user) throw new Error('Admin session expired.');
    const token = await user.getIdToken(true);
    const response = await fetch(path, {
        method,
        headers: {'Content-Type': 'application/json', 'Authorization': `Bearer ${token}`},
        body: body === undefined ? undefined : JSON.stringify(body)
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
}

window.saveExamArchive = async function() {
    const provider = document.getElementById('archiveProvider').value;
    const archiveFile = document.getElementById('archiveFile')?.files?.[0];
    const body = {
        title: document.getElementById('archiveTitle').value.trim(),
        grade: document.getElementById('archiveGrade').value,
        stream: document.getElementById('archiveStream').value,
        subject: document.getElementById('archiveSubject').value.trim(),
        year: Number(document.getElementById('archiveYear').value),
        storageProvider: provider,
        fileId: document.getElementById('archiveFileId').value.trim(),
        previewUrl: document.getElementById('archivePreviewUrl').value.trim(),
        downloadUrl: document.getElementById('archiveDownloadUrl').value.trim(),
        isPremium: document.getElementById('archivePremium').checked,
        priceLabel: document.getElementById('archivePriceLabel')?.value.trim() || '',
        currency: document.getElementById('archiveCurrency')?.value || 'ETB'
    };
    try {
        showLoadingOverlay(provider === 'gcs' && archiveFile ? 'Uploading exam PDF to Google Cloud Storage...' : 'Saving exam archive...');
        if (provider === 'gcs' && archiveFile) {
            if (archiveFile.type !== 'application/pdf') throw new Error('Exam archive file must be a PDF.');
            const result = await uploadFile(archiveFile, 'documents');
            if (!result.success) throw new Error(result.error || 'Google Cloud Storage PDF upload failed.');
            body.fileId = result.fileId || body.fileId;
            body.storagePath = result.path || result.storagePath || result.fileId || '';
            body.previewUrl = result.url || body.previewUrl;
            body.downloadUrl = result.url || body.downloadUrl;
        } else if (provider === 'google_drive' && body.fileId) {
            const drive = parseGoogleDriveFileUrl(body.fileId);
            body.fileId = drive.fileId;
            if (!body.previewUrl) body.previewUrl = drive.previewUrl;
            if (!body.downloadUrl) body.downloadUrl = drive.downloadUrl;
        }
        await archiveAdminApi('POST', '/api/admin/exam-archive', body);
        hideLoadingOverlay();
        showToast('Exam archive item added successfully.', 'success');
        document.getElementById('examArchiveForm').reset();
        document.getElementById('archivePremium').checked = true;
        if (document.getElementById('archivePriceLabel')) document.getElementById('archivePriceLabel').value = '';
        if (document.getElementById('archiveCurrency')) document.getElementById('archiveCurrency').value = 'ETB';
        toggleArchiveStream();
        await loadAdminExamArchive();
    } catch (error) {
        hideLoadingOverlay();
        showToast('Could not save archive: ' + error.message, 'error');
    }
};

window.deleteExamArchive = async function(id) {
    if (!confirm('Delete this exam archive item?')) return;
    try {
        await archiveAdminApi('DELETE', `/api/admin/exam-archive/${encodeURIComponent(id)}`);
        showToast('Archive item deleted.', 'success');
        await loadAdminExamArchive();
    } catch (error) { showToast(error.message, 'error'); }
};

async function loadAdminExamArchive() {
    const box = document.getElementById('adminExamArchiveList');
    if (!box) return;
    try {
        const token = await auth.currentUser.getIdToken(true);
        const response = await fetch('/api/exam-archive', {headers: {'Authorization': `Bearer ${token}`} });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Could not load archive.');
        box.innerHTML = data.items.length ? data.items.map(item => `
            <div class="item-row">
              <div class="item-content"><b>${escapeHtml(item.title || '')}</b>
                <div style="font-size:.85em;color:var(--text-muted);">Grade ${item.grade}${item.stream ? ' • '+escapeHtml(item.stream) : ''} • ${escapeHtml(item.subject || '')} • ${item.year} • ${item.storageProvider}</div>
                <span class="badge ${item.isPremium ? 'badge-pending' : 'badge-approved'}">${item.isPremium ? '💳 Paid'+(item.priceLabel ? ' · '+escapeHtml(item.priceLabel)+' '+escapeHtml(item.currency||'ETB') : '') : '🆓 Free'}</span>
              </div>
              <div class="item-actions"><button class="btn btn-danger" style="padding:6px 10px" onclick="deleteExamArchive('${item.id}')">Delete</button></div>
            </div>`).join('') : '<p class="subtitle">No archive items yet.</p>';
    } catch (error) { box.innerHTML = `<p class="error-text">${escapeHtml(error.message)}</p>`; }
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}

const _oldAdminInit = window.loadAdminReports;
window.addEventListener('load', () => { toggleArchiveStream(); setTimeout(loadAdminExamArchive, 700); });

// ==========================================================
// 🛡️ V26 ADMIN CONTROL CENTER 2.0
// ==========================================================
async function controlCenterApi() {
    const token = await getIdToken(auth.currentUser, true);
    const response = await fetch('/api/admin/control-center', {
        headers: { 'Authorization': `Bearer ${token}` }
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
}

function ccSet(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value ?? '—';
}

function ccEsc(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}

async function loadAdminConfigStatus() {
    try {
        const data = await adminRequest('/api/admin/config-status');
        const checks = data.checks || {};
        const missing = Object.entries(checks).filter(([,v]) => !v).map(([k]) => k);
        const box = document.getElementById('adminConfigStatus');
        if (box) {
            box.textContent = missing.length ? `⚠️ Missing/unready: ${missing.join(', ')}` : '✅ Firebase and production integrations are configured.';
            box.className = missing.length ? 'subtitle error-text' : 'subtitle';
        }
    } catch (e) {
        const box = document.getElementById('adminConfigStatus');
        if (box) box.textContent = `⚠️ Configuration check failed: ${e.message}`;
    }
}

async function loadAdminAnalytics() {
    const err = document.getElementById('adminAnalyticsError');
    try {
        if (err) { err.style.display='none'; err.textContent=''; }
        const data = await adminRequest('/api/admin/analytics/overview');
        const u=data.users||{}, l=data.learning||{}, c=data.content||{}, sp=data.storageProviders||{};
        ccSet('anActive7d', u.activeStudents7d);
        ccSet('anActive30d', u.activeStudents30d);
        ccSet('anTeachers30d', u.activeTeachers30d);
        ccSet('anVideosWatched', l.videosWatched);
        ccSet('anQuizSubmissions', l.quizSubmissions);
        ccSet('anStudentsProgress', l.studentsWithProgress);
        ccSet('anCourses', c.courses);
        ccSet('anLessons', c.lessons);
        ccSet('anQuizzes', c.quizzes);
        ccSet('anArchives', c.examArchives);
        ccSet('anYoutube', sp.youtube);
        ccSet('anCloudinary', sp.cloudinary);
        ccSet('anDrive', sp.google_drive);
    } catch (error) {
        console.error('Admin analytics error:', error);
        if (err) { err.style.display='block'; err.textContent='Could not load analytics: '+error.message; }
    }
}

async function loadAdminControlCenter() {
    const activity = document.getElementById('ccRecentActivity');
    const audit = document.getElementById('ccAuditLog');
    if (!activity || !audit) return;
    try {
        const data = await controlCenterApi();
        const c = data.counts || {};
        ccSet('ccStudents', c.students);
        ccSet('ccTeachers', c.teachers);
        ccSet('ccCourses', c.courses);
        ccSet('ccExams', c.exams);
        ccSet('ccLive', c.liveClasses);
        ccSet('ccPayments', c.payments);
        ccSet('ccSupport', c.supportTickets);
        ccSet('ccAiUsage', c.aiUsage);

        const p = data.payments || {};
        ccSet('ccPendingPayments', p.pending);
        ccSet('ccApprovedPayments', p.approved);
        ccSet('ccRejectedPayments', p.rejected);

        const t = data.teacherRequests || {};
        ccSet('ccPendingTeachers', t.pending);
        ccSet('ccApprovedTeachers', t.approved);
        ccSet('ccRejectedTeachers', t.rejected);

        const s = data.support || {};
        ccSet('ccOpenSupport', s.open);
        ccSet('ccUnassignedSupport', s.unassigned);
        ccSet('ccClosedSupport', s.closed);

        const rows = data.activity || [];
        activity.innerHTML = rows.length ? rows.map(x => `
            <div class="item-row">
                <div class="item-content">
                    <b>${ccEsc(x.title)}</b>
                    <span style="color:var(--text-muted);font-size:12px;"> · ${ccEsc(x.collection)}${x.status ? ` · ${ccEsc(x.status)}` : ''}</span>
                </div>
                <div style="font-size:12px;color:var(--text-muted);">${ccEsc(x.updatedAt || '')}</div>
            </div>
        `).join('') : '<div class="subtitle">No recent activity found.</div>';

        const logs = data.audit || [];
        audit.innerHTML = logs.length ? logs.map(x => `
            <div class="item-row">
                <div class="item-content">🛡️ <b>${ccEsc(x.action)}</b> <span style="color:var(--text-muted);">${ccEsc(x.target || '')}</span></div>
                <div style="font-size:12px;color:var(--text-muted);">${ccEsc(x.adminEmail || x.adminUid || '')}<br>${ccEsc(x.createdAt || '')}</div>
            </div>
        `).join('') : '<div class="subtitle">No audit events recorded yet.</div>';

        ccSet('ccGeneratedAt', data.generatedAt ? `Last refreshed: ${data.generatedAt}` : '');
        loadAdminAnalytics();
    } catch (error) {
        console.error('Admin Control Center error:', error);
        activity.innerHTML = `<div class="subtitle" style="color:var(--danger);">Could not load control center: ${ccEsc(error.message)}</div>`;
        audit.innerHTML = '';
    }
}

window.scrollToAdminSection = function(id) {
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
};

window.loadAdminControlCenter = loadAdminControlCenter;

document.addEventListener('DOMContentLoaded', () => {
    const refreshAnalytics = document.getElementById('refreshAnalyticsBtn');
    if (refreshAnalytics) refreshAnalytics.addEventListener('click', async () => {
        refreshAnalytics.disabled = true;
        try { await loadAdminAnalytics(); } finally { refreshAnalytics.disabled = false; }
    });
    const refresh = document.getElementById('refreshControlCenterBtn');
    if (refresh) refresh.addEventListener('click', async () => {
        refresh.disabled = true;
        try { await loadAdminControlCenter(); }
        finally { refresh.disabled = false; }
    });
});

async function sendAdminCommunityMessage(){
 const text=document.getElementById('adminCommunityMessage')?.value.trim()||''; const file=document.getElementById('adminCommunityMedia')?.files?.[0]||null; const status=document.getElementById('adminCommunitySendStatus');
 if(!text&&!file){if(status)status.textContent='Write a message or attach media.';return;}
 try{const user=auth.currentUser;if(!user)throw Error('Admin session expired.');const token=await user.getIdToken(true);let mediaUrl='',mediaType='';
  if(file){const fd=new FormData();fd.append('file',file);const mr=await fetch('/api/chat/community/media',{method:'POST',headers:{Authorization:`Bearer ${token}`},body:fd});const md=await mr.json().catch(()=>({}));if(!mr.ok||!md.success)throw Error(md.error||'Media upload failed.');mediaUrl=md.url;mediaType=md.mediaType;}
  const r=await fetch('/api/chat/community/send',{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${token}`},body:JSON.stringify({message:text,mediaUrl,mediaType})});const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.error||'Could not send community message.');
  document.getElementById('adminCommunityMessage').value='';document.getElementById('adminCommunityMedia').value='';if(status)status.textContent='✅ Sent.';loadAdminChats();
 }catch(e){if(status)status.textContent='❌ '+e.message;}
}
