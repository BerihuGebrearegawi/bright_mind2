import { initializeApp } from "https://www.gstatic.com/firebasejs/10.7.1/firebase-app.js";
import { getAuth, onAuthStateChanged, signOut, signInWithEmailAndPassword, createUserWithEmailAndPassword } from "https://www.gstatic.com/firebasejs/10.7.1/firebase-auth.js";
import { getFirestore } from "https://www.gstatic.com/firebasejs/10.7.1/firebase-firestore.js";

const firebaseConfig={apiKey:"AIzaSyAyeZpwu9-FECjC5Qp-lI0OUAblKusxkeI",authDomain:"bright-mind-tutor-app.firebaseapp.com",projectId:"bright-mind-tutor-app",storageBucket:"bright-mind-tutor-app.firebasestorage.app",messagingSenderId:"782512714975",appId:"1:782512714975:web:719e3b7a09ac8c7f9d256a",measurementId:"G-TWYNFN7MT6"};
const app=initializeApp(firebaseConfig),auth=getAuth(app),db=getFirestore(app); let teacher=null; let communityUnsub=null; let communityPollTimer=null; let communitySending=false; let teacherReplyTo=null; let teacherRecordedBlob=null;
const $=id=>document.getElementById(id);
const BMT_PREVIEW_PARAMS = new URLSearchParams(location.search);
const BMT_PREVIEW_ROLE = BMT_PREVIEW_PARAMS.get('preview') === '1' && BMT_PREVIEW_PARAMS.get('role') === 'teacher' ? 'teacher' : '';
const BMT_PREVIEW_TOKEN = BMT_PREVIEW_PARAMS.get('token') || '';
const esc=x=>String(x??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
function withTimeout(promise, ms=15000, message='Request timed out. Please check your connection and try again.'){return Promise.race([promise,new Promise((_,reject)=>setTimeout(()=>reject(new Error(message)),ms))]);}
function toast(m,type='info'){const e=document.createElement('div');e.className=`toast toast-${type} show`;e.textContent=m;document.body.appendChild(e);setTimeout(()=>e.remove(),3500)}
async function api(path,options={}){if(!teacher) throw Error('Authentication required.');const token=await teacher.getIdToken();const res=await fetch(path,{...options,headers:{'Content-Type':'application/json',Authorization:`Bearer ${token}`,...(options.headers||{})}});const data=await res.json().catch(()=>({}));if(!res.ok)throw Error(data.error||`Request failed (${res.status})`);return data}
function selectedValues(id){return [...$(id).selectedOptions].map(o=>o.value)}

function renderTeacherPreview(){
 $('teacherDenied').style.display='none'; $('teacherPanel').style.display='block';
 const b=document.createElement('div'); b.className='bmt-preview-banner'; b.innerHTML='<strong>ADMIN PREVIEW</strong> — Teacher Dashboard <span>Read-only demo data</span> <a href="/admin">Return to Admin</a>'; document.body.prepend(b);
 $('teacherIdentity').textContent='Demo Teacher • Mathematics';
 $('teacherStats').innerHTML='<div class="stat-card"><strong>6</strong><span>Courses</span></div><div class="stat-card"><strong>82%</strong><span>Average</span></div><div class="stat-card"><strong>91%</strong><span>Pass Rate</span></div><div class="stat-card"><strong>38</strong><span>Students</span></div>';
 $('courseList').innerHTML='<div class="list-row"><div><strong>Grade 8 Mathematics</strong><div class="subtitle">Linear Equations</div></div><span class="badge badge-free">Published</span></div><div class="list-row"><div><strong>Grade 9 Mathematics</strong><div class="subtitle">Geometry</div></div><span class="badge badge-free">Published</span></div>';
 const summary=$('teacherAnalyticsSummary'); if(summary) summary.innerHTML='<div class="stat-card"><strong>12</strong><span>Submissions</span></div><div class="stat-card"><strong>76%</strong><span>Average</span></div><div class="stat-card"><strong>68%</strong><span>Pass Rate</span></div>';
 const details=$('teacherAnalyticsDetails'); if(details) details.innerHTML='<div class="subtitle">Preview mode shows representative analytics only. No real student data is modified.</div>';
 document.querySelectorAll('#teacherPanel form, #teacherPanel input[type=file], #teacherPanel textarea, #teacherPanel select, #teacherPanel input').forEach(el=>el.disabled=true);
 document.querySelectorAll('#teacherPanel button').forEach(btn=>{if(!btn.closest('.bmt-preview-banner'))btn.disabled=true;});
}

async function load(){
 try{
  const data=await api('/api/teacher/profile');
  if(!data.approved){$('teacherDenied').style.display='block';$('teacherPanel').style.display='none';return;}
  const p=data.profile||{};$('teacherDenied').style.display='none';$('teacherPanel').style.display='block';
  $('teacherIdentity').textContent=`${p.name||teacher.displayName||teacher.email} • ${(p.subjects||[]).join(', ')||'Teacher'}`;
  await loadCourses(); await loadAiMaterials(); setupTeacherCommunityChat();
 }catch(e){console.error(e);toast(e.message,'error')}
}
async function loadCourses(){
 const data=await api('/api/teacher/courses');const courses=data.courses||[];$('teacherStats').innerHTML=`<div class="stat-card"><strong>${courses.length}</strong><span>Courses</span></div>`;
 $('lessonCourse').innerHTML=courses.map(c=>`<option value="${esc(c.id)}">${esc(c.title)} — Grade ${esc(c.className)}</option>`).join('');
 $('courseList').innerHTML=courses.length?courses.map(c=>`<div class="list-row"><div><strong>${esc(c.title)}</strong><div class="subtitle">Grade ${esc(c.className)} • ${esc(c.description||'')}</div></div><span class="badge badge-free">Published</span></div>`).join(''):'<p class="subtitle">No courses yet.</p>';
 $('lessonForm').style.display=courses.length?'block':'none';
}
function renderTeacherCommunity(snapshot){
 const box=$('teacherCommunityMessages'); if(!box)return;
 const rows=snapshot.docs.map(d=>({id:d.id,...d.data()})).sort((a,b)=>(a.createdAt?.toMillis?.()||0)-(b.createdAt?.toMillis?.()||0));
 box.innerHTML=rows.length?rows.map(m=>{
  const reactions=m.reactions||{}; const role=m.role|| (m.isAdminReply?'admin':'student');
  const label=role==='admin'?'👑 Admin':role==='teacher'?'👨‍🏫 Teacher':(m.userName||m.senderName||'Student');
  return `<div class="item-row" style="align-items:flex-start;margin:6px 0"><div class="item-content"><div><b>${esc(label)}</b> <span class="subtitle">${m.createdAt?.toDate?m.createdAt.toDate().toLocaleString():(m.createdAt?new Date(m.createdAt).toLocaleString():'')}</span></div>${m.replyTo?`<div class="subtitle" style="margin:4px 0;padding:5px;border-left:3px solid var(--primary)">↩️ ${esc(m.replyToText||'Original message')}</div>`:''}<div style="margin-top:5px">${esc(m.messageText||m.message||'')}</div><div style="display:flex;gap:4px;flex-wrap:wrap;margin-top:7px"><button class="btn btn-neutral" data-t-reply="${m.id}">↩️ Reply</button><button class="btn btn-neutral" data-t-react="${m.id}:heart">❤️ ${reactions.heart||0}</button><button class="btn btn-neutral" data-t-react="${m.id}:like">👍 ${reactions.like||0}</button><button class="btn btn-neutral" data-t-react="${m.id}:dislike">👎 ${reactions.dislike||0}</button><button class="btn btn-neutral" data-t-react="${m.id}:fire">🔥 ${reactions.fire||0}</button><button class="btn btn-neutral" data-t-report="${m.id}">🚩 Report</button></div></div></div>`;
 }).join(''):'<p class="subtitle">No community messages yet.</p>';
 box.querySelectorAll('[data-t-reply]').forEach(b=>b.onclick=()=>{teacherReplyTo=b.dataset.tReply; const i=$('teacherCommunityMessage'); if(i){i.focus();i.placeholder='Replying to selected message...';}});
 box.querySelectorAll('[data-t-react]').forEach(b=>b.onclick=async()=>{const [messageId,reaction]=b.dataset.tReact.split(':');try{await api('/api/chat/reaction',{method:'POST',body:JSON.stringify({messageId,reaction})});}catch(e){toast(e.message,'error')}});
 box.querySelectorAll('[data-t-report]').forEach(b=>b.onclick=async()=>{const reason=prompt('Why are you reporting this message?');if(!reason?.trim())return;try{await api('/api/chat/report',{method:'POST',body:JSON.stringify({messageId:b.dataset.tReport,reason:reason.trim()})});toast('Report submitted.','success')}catch(e){toast(e.message,'error')}});
}
window.sendTeacherCommunityMessage = sendTeacherCommunityMessage;
async function teacherAskCommunityAI(){
 const input=$('teacherCommunityMessage'); const panel=$('teacherCommunityAiAnswer'); const out=$('teacherCommunityAiText');
 if(!input||!input.value.trim()) return toast('Write a question or draft first.','error');
 try{ panel.style.display='block'; out.textContent='AI is thinking…'; const data=await api('/api/ai/tutor',{method:'POST',body:JSON.stringify({message:input.value.trim(),subject:'Teaching support',grade:'Teacher',history:[]})}); out.textContent=data.answer||'No answer returned.'; }
 catch(e){out.textContent=e.message;toast(e.message,'error');}
}
window.teacherAskCommunityAI=teacherAskCommunityAI;

async function loadTeacherCommunityChat(){
 const box=$('teacherCommunityMessages'); if(!box||!teacher)return;
 try{
  const data=await api('/api/chat/community');
  const rows=(data.messages||[]).map(m=>({id:m.id,...m}));
  renderTeacherCommunity({docs:rows.map(m=>({id:m.id,data:()=>m}))});
 }catch(e){
  box.innerHTML=`<p class="error-text">${esc(e.message||'Could not load community chat.')}</p>`;
 }
}
function setupTeacherCommunityChat(){
 const box=$('teacherCommunityMessages'); if(!box)return;
 if(communityPollTimer)clearInterval(communityPollTimer);
 box.innerHTML='<div class="spinner"></div> Loading community chat…';
 loadTeacherCommunityChat();
 communityPollTimer=setInterval(loadTeacherCommunityChat,5000);
}
async function uploadTeacherCommunityMedia(file){
 const token=await teacher.getIdToken(true); const fd=new FormData(); fd.append('file',file);
 const r=await fetch('/api/chat/community/media',{method:'POST',headers:{Authorization:`Bearer ${token}`},body:fd}); const d=await r.json().catch(()=>({}));
 if(!r.ok||!d.success)throw Error(d.error||'Media upload failed.'); return d;
}
async function sendTeacherCommunityMessage(){
 const input=$('teacherCommunityMessage'); if(!input||communitySending)return;
 const text=input.value.trim(); const file=$('teacherCommunityMedia')?.files?.[0]||teacherRecordedBlob;
 if(!text&&!file)return toast('Write a message or attach media.','error');
 communitySending=true; const btn=$('teacherCommunitySendBtn'); if(btn){btn.disabled=true;btn.textContent='Sending…';}
 try{
  let mediaUrl='',mediaType='';
  if(file){const uploaded=await uploadTeacherCommunityMedia(file);mediaUrl=uploaded.url;mediaType=uploaded.mediaType;}
  if(teacherReplyTo){
   await api('/api/chat/community/reply',{method:'POST',body:JSON.stringify({messageId:teacherReplyTo,message:text,mediaUrl,mediaType})});
  } else {
   await api('/api/chat/community/send',{method:'POST',body:JSON.stringify({message:text,mediaUrl,mediaType})});
  }
  input.value=''; if($('teacherCommunityMedia'))$('teacherCommunityMedia').value=''; teacherRecordedBlob=null; teacherReplyTo=null; input.placeholder='Write to students, teachers and admins...';
  await loadTeacherCommunityChat();
 }catch(e){toast('Could not send message: '+e.message,'error')}
 finally{communitySending=false;if(btn){btn.disabled=false;btn.textContent='Send';}}
}

$('teacherCommunitySendBtn')?.addEventListener('click',sendTeacherCommunityMessage);
$('teacherCommunityAiBtn')?.addEventListener('click',teacherAskCommunityAI);
$('teacherCommunityMessage')?.addEventListener('keydown',e=>{
 if(e.key==='Enter' && !e.shiftKey){e.preventDefault();sendTeacherCommunityMessage();}
});

function setupTeacherRecorder(){
 const btn=$('teacherRecordBtn'); if(!btn||!navigator.mediaDevices?.getUserMedia)return; let recorder=null,chunks=[];
 btn.onclick=async()=>{
  if(recorder&&recorder.state==='recording'){recorder.stop();return;}
  try{const stream=await navigator.mediaDevices.getUserMedia({audio:true}); const mime=['audio/webm;codecs=opus','audio/webm','audio/ogg'].find(x=>window.MediaRecorder?.isTypeSupported?.(x))||''; recorder=mime?new MediaRecorder(stream,{mimeType:mime}):new MediaRecorder(stream); chunks=[]; recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data)}; recorder.onstop=()=>{stream.getTracks().forEach(t=>t.stop());teacherRecordedBlob=new File([new Blob(chunks,{type:'audio/webm'})],'teacher-voice.webm',{type:'audio/webm'});btn.textContent='🎤 Recorded';}; recorder.start();btn.textContent='⏹ Stop';}
  catch(e){toast('Microphone permission is required for voice recording.','error');}
 };
}
setupTeacherRecorder();

$('teacherLogout').onclick=()=>signOut(auth);
$('teacherApplyForm').addEventListener('submit',async e=>{e.preventDefault();try{const data=await api('/api/teacher/apply',{method:'POST',body:JSON.stringify({name:$('applyName').value.trim(),bio:$('applyBio').value.trim(),educationLevel:$('applyEducationLevel').value.trim(),institution:$('applyInstitution').value.trim(),experienceYears:$('applyExperienceYears').value.trim(),experience:$('applyExperience').value.trim(),certifications:$('applyCertifications').value.trim(),subjects:selectedValues('applySubjects'),classes:selectedValues('applyClasses')})});$('applyStatus').textContent='✅ Application submitted. Wait for admin approval.';toast('Application submitted','success');}catch(err){$('applyStatus').textContent='❌ '+err.message;toast(err.message,'error')}});
$('courseForm').addEventListener('submit',async e=>{e.preventDefault();try{await api('/api/teacher/courses',{method:'POST',body:JSON.stringify({title:$('courseTitle').value.trim(),className:$('courseClass').value,description:$('courseDescription').value.trim()})});e.target.reset();await loadCourses();toast('Course created','success')}catch(err){toast(err.message,'error')}});
$('lessonForm').addEventListener('submit',async e=>{e.preventDefault();try{await api('/api/teacher/lessons',{method:'POST',body:JSON.stringify({courseId:$('lessonCourse').value,title:$('lessonTitle').value.trim(),contentType:$('lessonType').value,url:$('lessonUrl').value.trim(),description:$('lessonDescription').value.trim()})});e.target.reset();toast('Lesson published','success')}catch(err){toast(err.message,'error')}});
// V17 — Visual Exam Builder
const examState={questions:[]};
function examQuestionTemplate(q,i){
 const opts=q.options||{A:'',B:'',C:'',D:''};
 return `<div class="card exam-q" data-q="${i}" style="margin:10px 0"><div style="display:flex;justify-content:space-between;gap:8px;align-items:center"><strong>Question ${i+1}</strong><button type="button" class="btn btn-danger" data-remove-q="${i}">Remove</button></div><textarea data-field="question" rows="3" placeholder="Question text">${esc(q.question||'')}</textarea><div style="display:grid;grid-template-columns:1fr 1fr;gap:8px"><select data-field="type"><option value="mcq" ${q.type==='mcq'?'selected':''}>Multiple Choice</option><option value="true_false" ${q.type==='true_false'?'selected':''}>True / False</option></select><input data-field="topic" value="${esc(q.topic||'General')}" placeholder="Topic"></div>${q.type==='true_false'?`<select data-field="answer"><option value="A" ${q.answer==='A'?'selected':''}>True</option><option value="B" ${q.answer==='B'?'selected':''}>False</option></select>`:`<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">${['A','B','C','D'].map(k=>`<input data-opt="${k}" value="${esc(opts[k]||'')}" placeholder="Option ${k}">`).join('')}</div><select data-field="answer"><option value="">Correct answer</option>${['A','B','C','D'].map(k=>`<option value="${k}" ${q.answer===k?'selected':''}>${k}</option>`).join('')}</select>`}<input data-field="points" type="number" min="1" max="100" value="${q.points||1}" placeholder="Points"></div>`;
}
function syncExamState(){
 document.querySelectorAll('.exam-q').forEach(el=>{const i=Number(el.dataset.q),q=examState.questions[i];q.question=el.querySelector('[data-field="question"]').value.trim();q.type=el.querySelector('[data-field="type"]').value;q.topic=el.querySelector('[data-field="topic"]').value.trim()||'General';q.answer=el.querySelector('[data-field="answer"]').value;q.points=Math.max(1,Number(el.querySelector('[data-field="points"]').value)||1);if(q.type==='mcq')q.options=Object.fromEntries(['A','B','C','D'].map(k=>[k,el.querySelector(`[data-opt="${k}"]`).value.trim()]));else q.options={A:'True',B:'False'};});
}
function renderExamBuilder(){
 const box=$('questionBuilder'); if(!box)return; box.innerHTML=examState.questions.length?examState.questions.map(examQuestionTemplate).join(''):'<p class="subtitle">Add your first question.</p>';
 box.querySelectorAll('select[data-field="type"]').forEach(el=>el.onchange=()=>{syncExamState();renderExamBuilder();});
 box.querySelectorAll('[data-remove-q]').forEach(b=>b.onclick=()=>{syncExamState();examState.questions.splice(Number(b.dataset.removeQ),1);renderExamBuilder();renderExamPreview();});
 box.querySelectorAll('input,textarea,select').forEach(el=>el.addEventListener('input',()=>{syncExamState();renderExamPreview();}));
 renderExamPreview();
}
function renderExamPreview(){const box=$('examPreview');if(!box)return;syncExamState();box.innerHTML=examState.questions.length?examState.questions.map((q,i)=>`<div style="margin-bottom:14px"><strong>${i+1}. ${esc(q.question||'Untitled question')}</strong><div class="subtitle">${esc(q.topic)} • ${q.points} point${q.points==1?'':'s'} • ${q.type==='true_false'?'True / False':'MCQ'}</div></div>`).join(''):'No questions added yet.';}
$('addQuestionBtn')?.addEventListener('click',()=>{syncExamState();examState.questions.push({question:'',type:'mcq',topic:'General',options:{A:'',B:'',C:'',D:''},answer:'',points:1});renderExamBuilder();});
$('publishExamBtn')?.addEventListener('click',async()=>{try{syncExamState();const title=$('examTitle').value.trim();if(!title)throw Error('Exam title is required.');if(!examState.questions.length)throw Error('Add at least one question.');if(examState.questions.some(q=>!q.question||!q.answer))throw Error('Every question needs text and a correct answer.');if(examState.questions.some(q=>q.type==='mcq'&&Object.values(q.options).some(v=>!v)))throw Error('Every MCQ needs four options.');const data={title,className:$('examClass').value,durationMinutes:Number($('examDuration').value),passMark:Number($('examPassMark').value),maxAttempts:Number($('examMaxAttempts').value),questions:examState.questions};const r=await api('/api/teacher/exams',{method:'POST',body:JSON.stringify(data)});const published=await api(`/api/teacher/exams/${encodeURIComponent(r.examId)}/publish`,{method:'POST',body:'{}'});$('examStatus').textContent=`✅ Exam published: ${published.examId}`;toast('Exam published','success');examState.questions=[];$('examTitle').value='';renderExamBuilder();}catch(e){$('examStatus').textContent='❌ '+e.message;toast(e.message,'error')}});
async function loadTeacherExamResults(){
 const box=$('teacherExamResults'); if(!box)return;
 try{
  const data=await api('/api/teacher/exams'); const exams=data.exams||[]; const analytics=data.analytics||{};
  const summary=$('teacherAnalyticsSummary');
  if(summary){
   const cards=[
    ['Exams', analytics.totalExams??0],
    ['Submissions', analytics.totalSubmissions??0],
    ['Average', analytics.averagePercentage==null?'—':`${analytics.averagePercentage}%`],
    ['Pass rate', analytics.passRate==null?'—':`${analytics.passRate}%`],
    ['Passed', analytics.passCount??0],
    ['Failed', analytics.failCount??0]
   ];
   summary.innerHTML=cards.map(([label,value])=>`<div class="list-row" style="text-align:center"><strong style="font-size:1.25rem">${esc(value)}</strong><div class="subtitle">${esc(label)}</div></div>`).join('');
  }
  const details=$('teacherAnalyticsDetails');
  if(details){
   const weak=(analytics.topWeakTopics||[]).map(x=>`<span class="badge badge-free" style="margin:3px">${esc(x.topic)} (${esc(x.count)})</span>`).join('');
   const students=(analytics.studentPerformance||[]).slice(0,10).map((x,i)=>`<div class="list-row"><strong>#${i+1} ${esc(x.studentUid||'Student')}</strong><span>${esc(x.averagePercentage)}% · ${esc(x.attempts)} attempt${x.attempts==1?'':'s'}</span></div>`).join('');
   details.innerHTML=`<div style="margin-bottom:12px"><strong>Common weak topics</strong><div style="margin-top:6px">${weak||'<span class="subtitle">No weak-topic data yet.</span>'}</div></div>${students?`<div><strong>Top student averages</strong><div style="display:grid;gap:6px;margin-top:6px">${students}</div></div>`:''}`;
  }
  box.innerHTML=exams.length?exams.map(e=>{
   const attempts=e.attempts||[];
   const avg=e.averagePercentage==null?'—':`${e.averagePercentage}%`;
   const passRate=e.passRate==null?'—':`${e.passRate}%`;
   return `<div class="list-row" style="display:block;margin-bottom:10px"><div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap"><div><strong>${esc(e.title)}</strong><div class="subtitle">Grade ${esc(e.className)} • ${e.questionCount} questions • ${attempts.length} submitted • Pass mark ${esc(e.passMark)}%</div></div><span class="badge badge-free">Avg ${esc(avg)} · Pass ${esc(passRate)}</span></div>${attempts.length?`<div style="margin-top:8px;display:grid;gap:6px">${attempts.map(a=>`<div style="padding:8px;border:1px solid var(--border);border-radius:8px"><strong>${esc(a.studentUid||'Student')}</strong> — ${esc(a.percentage)}% (${esc(a.score)}/${esc(a.totalPoints)}) <span class="badge ${a.passed?'badge-free':'badge-paid'}">${a.passed?'PASS':'FAIL'}</span>${a.weakTopics?.length?`<div class="subtitle">Weak: ${esc(a.weakTopics.join(', '))}</div>`:''}<div class="subtitle">${a.submittedAt?new Date(a.submittedAt).toLocaleString():''}</div></div>`).join('')}</div>`:'<p class="subtitle" style="margin-top:8px">No completed attempts yet.</p>'}</div>`;
  }).join(''):'<p class="subtitle">No exams published yet.</p>';
 }catch(e){
  box.innerHTML=`<p class="error-text">${esc(e.message||'Unable to load exam results.')}</p>`;
  const summary=$('teacherAnalyticsSummary'); if(summary)summary.innerHTML='';
  const details=$('teacherAnalyticsDetails'); if(details)details.innerHTML='';
 }
}

renderExamBuilder();

// V31.08 — dedicated teacher self-registration. The account is created in
// Firebase Auth first, then a minimal role profile is written to Firestore.
// The teacher application remains pending until an admin approves it.
// V31.17 — dedicated teacher sign-in. The previous portal had a signup form
// but no actual sign-in handler, so existing approved teachers could not enter.
const teacherLoginForm = $('teacherLoginForm');
if (teacherLoginForm) teacherLoginForm.addEventListener('submit', async e => {
  e.preventDefault();
  const email = $('teacherLoginEmail')?.value.trim();
  const password = $('teacherLoginPassword')?.value || '';
  const status = $('teacherLoginStatus');
  const btn = $('teacherLoginBtn');
  if (!email || !password) { if (status) status.textContent='❌ Enter your email and password.'; return; }
  if (btn) { btn.disabled=true; btn.textContent='Signing in…'; }
  if (status) status.textContent='Checking your account…';
  try {
    await withTimeout(signInWithEmailAndPassword(auth, email, password), 15000, 'Sign-in timed out. Please check your connection and try again.');
    if (status) status.textContent='✅ Signed in. Loading teacher dashboard…';
  } catch (err) {
    const code=err?.code||'';
    const msg = code==='auth/invalid-credential' || code==='auth/wrong-password' || code==='auth/user-not-found'
      ? '❌ Invalid email or password.'
      : code==='auth/too-many-requests'
      ? '❌ Too many attempts. Please wait and try again.'
      : `❌ ${err?.message||'Unable to sign in.'}`;
    if (status) status.textContent=msg;
    toast(msg,'error');
  } finally { if (btn) { btn.disabled=false; btn.textContent='Sign In'; } }
});

const teacherSignupForm = $('teacherSignupForm');
if (teacherSignupForm) teacherSignupForm.addEventListener('submit', async e => {
  e.preventDefault();
  const name=$('teacherSignupName').value.trim(), email=$('teacherSignupEmail').value.trim(), password=$('teacherSignupPassword').value, confirm=$('teacherSignupConfirm').value;
  const status=$('teacherSignupStatus'), btn=$('teacherSignupBtn');
  if(password.length < 6) { status.textContent='❌ Password must be at least 6 characters.'; return; }
  if(password !== confirm) { status.textContent='❌ Passwords do not match.'; return; }
  btn.disabled=true; status.textContent='Creating account…';
  try {
    const cred=await withTimeout(createUserWithEmailAndPassword(auth,email,password),15000,'Teacher account creation timed out. Please check your connection and try again.');
    try {
      // Do not let a self-registered browser assign privileged roles.
      // The Firestore rules intentionally allow only the safe profile fields.
      await withTimeout(setDoc(doc(db,'users',cred.user.uid),{
        uid:cred.user.uid, name, email, class:'',
        registeredAt:serverTimestamp(), progress:{videos:{},quizzes:{}},
        payments:[], isAdmin:false, profileImage:'', bio:'',
        freeTrial:{isActive:true, startedAt:serverTimestamp(), expiresAt:new Date(Date.now()+7*24*60*60*1000).toISOString(), daysRemaining:7, usedDays:0}, isPaid:false, accountType:'teacher'
      }),15000,'Teacher profile creation timed out. Please check Firebase access and try again.');
    } catch (profileError) {
      try { await cred.user.delete(); } catch (_) {}
      throw profileError;
    }
    status.textContent='✅ Account created. Complete the teacher application below.';
    toast('Teacher account created','success');
  } catch(err) {
    const code=err?.code||'';
    status.textContent=code==='auth/email-already-in-use'?'❌ This email is already registered. Please sign in.':`❌ ${err?.message||'Unable to create account.'}`;
  } finally { btn.disabled=false; }
});

onAuthStateChanged(auth,async u=>{
 if(BMT_PREVIEW_ROLE){
  try{const r=await fetch(`/api/admin/preview-verify?role=teacher&token=${encodeURIComponent(BMT_PREVIEW_TOKEN)}`);if(!r.ok)throw Error('Preview link is invalid or expired.');renderTeacherPreview();}catch(e){toast(e.message,'error');}return;
 }
 teacher=u;if(!u){if($('teacherLogin')) $('teacherLogin').style.display='block'; if($('teacherSignup')) $('teacherSignup').style.display='block'; if($('teacherDenied')) $('teacherDenied').style.display='none'; if($('teacherPanel')) $('teacherPanel').style.display='none';}else{if($('teacherLogin')) $('teacherLogin').style.display='none'; if($('teacherSignup')) $('teacherSignup').style.display='none'; load();}
});

document.getElementById('teacherAIBtn')?.addEventListener('click',async()=>{const task=document.getElementById('teacherAITask')?.value.trim(),out=document.getElementById('teacherAIResult'),status=document.getElementById('teacherAIStatus');if(!task||!out)return;out.innerHTML='<div class="ai-message assistant"><span class="ai-dots">● ● ●</span> Preparing teaching material…</div>';try{const r=await api('/api/teacher/ai-copilot',{method:'POST',body:JSON.stringify({task,grade:document.getElementById('teacherAIGrade')?.value||'',subject:document.getElementById('teacherAISubject')?.value.trim()||''})});out.innerHTML=`<div class="ai-message assistant"><strong>🧑‍🏫 AI Teacher Copilot</strong><div style="margin-top:8px">${esc(r.answer||'').replace(/\n/g,'<br>')}</div></div>`;if(status)status.textContent='AI result ready.';}catch(e){out.innerHTML=`<div class="ai-message assistant error-text">${esc(e.message)}</div>`;if(status)status.textContent='AI request failed.';}});

async function loadAiMaterials(){
 try{
  const data=await api('/api/teacher/ai-materials');
  const list=data.materials||[];
  $('aiMaterialList').innerHTML=list.length?list.map(m=>`<div class="list-row"><div><strong>${esc(m.title)}</strong><div class="subtitle">Grade ${esc(m.grade)} • ${esc(m.subject)} • ${esc(m.sourceType)} • ${m.chunkCount||0} chunks</div></div><button class="btn btn-danger" data-ai-delete="${esc(m.id)}">Delete</button></div>`).join(''):'<p class="subtitle">No AI materials yet.</p>';
  document.querySelectorAll('[data-ai-delete]').forEach(b=>b.onclick=async()=>{if(!confirm('Delete this AI material?'))return;try{await api('/api/teacher/ai-materials/'+encodeURIComponent(b.dataset.aiDelete),{method:'DELETE'});toast('AI material deleted','success');loadAiMaterials()}catch(e){toast(e.message,'error')}});
 }catch(e){console.error(e)}
}
$('aiMaterialForm').addEventListener('submit',async e=>{
 e.preventDefault();
 const f=new FormData();
 f.append('title',$('aiMaterialTitle').value.trim());
 f.append('grade',$('aiMaterialGrade').value);
 f.append('subject',$('aiMaterialSubject').value.trim());
 f.append('sourceUrl',$('aiMaterialUrl').value.trim());
 f.append('text',$('aiMaterialText').value.trim());
 if($('aiMaterialFile').files[0]) f.append('file',$('aiMaterialFile').files[0]);
 try{
  const token=await teacher.getIdToken();
  const r=await fetch('/api/teacher/ai-materials',{method:'POST',headers:{Authorization:`Bearer ${token}`},body:f});
  const data=await r.json().catch(()=>({}));
  if(!r.ok)throw Error(data.error||`Request failed (${r.status})`);
  $('aiMaterialStatus').textContent=`✅ Added ${data.chunks} searchable chunks.`;
  e.target.reset(); await loadAiMaterials(); toast('AI material added','success');
 }catch(err){$('aiMaterialStatus').textContent='❌ '+err.message;toast(err.message,'error')}
});

// V14 — Live Classes & teacher presence
function isoFromLocalInput(value){
  const d=new Date(value); if(Number.isNaN(d.getTime())) throw Error('Choose a valid class date/time.');
  return d.toISOString();
}
async function loadTeacherLiveClasses(){
  try{
    const data=await api('/api/live/classes');
    const mine=(data.classes||[]).filter(x=>x.isOwner);
    const box=$('teacherLiveClasses'); if(!box)return;
    box.innerHTML=mine.length?mine.map(c=>`<div class="list-row"><div><strong>${esc(c.title)}</strong><div class="subtitle">Grade ${esc(c.grade)} • ${esc(c.subject)} • ${new Date(c.startAt).toLocaleString()} • ${esc(c.status)}</div></div><button class="btn btn-danger" data-cancel-class="${esc(c.id)}">Cancel</button></div>`).join(''):'<p class="subtitle">No upcoming classes.</p>';
    document.querySelectorAll('[data-cancel-class]').forEach(b=>b.onclick=async()=>{if(!confirm('Cancel this class?'))return;try{await api('/api/teacher/live/classes/'+encodeURIComponent(b.dataset.cancelClass),{method:'PATCH',body:JSON.stringify({status:'cancelled'})});toast('Class cancelled','success');loadTeacherLiveClasses()}catch(e){toast(e.message,'error')}});
  }catch(e){console.error(e)}
}
const liveForm=$('liveClassForm');
if(liveForm)liveForm.addEventListener('submit',async e=>{
 e.preventDefault();
 try{
  const r=await api('/api/teacher/live/classes',{method:'POST',body:JSON.stringify({title:$('liveTitle').value.trim(),grade:$('liveGrade').value,subject:$('liveSubject').value.trim(),startAt:isoFromLocalInput($('liveStart').value),durationMinutes:Number($('liveDuration').value),classGroupId:$('liveClassGroup').value.trim(),timeZone:'Africa/Addis_Ababa',meetingUrl:$('liveMeetingUrl').value.trim(),description:$('liveDescription').value.trim()})});
  $('liveClassStatus').textContent='✅ Class scheduled.';e.target.reset();$('liveDuration').value=60;toast('Live class scheduled','success');loadTeacherLiveClasses();
 }catch(err){$('liveClassStatus').textContent='❌ '+err.message;toast(err.message,'error')}
});
async function heartbeatTeacher(){try{await api('/api/teacher/presence',{method:'POST',body:'{}'})}catch(e){}}
const oldLoad=load;
load=async function(){await oldLoad(); if($('teacherPanel')?.style.display!=='none'){loadTeacherLiveClasses();heartbeatTeacher();setInterval(heartbeatTeacher,60000)}};

// V25 — Teacher Support Inbox
async function loadTeacherSupport(){
  const box=$('teacherSupportTickets'); if(!box||!teacher)return;
  try{
    const d=await api('/api/teacher/support/tickets'); const rows=d.tickets||[];
    box.innerHTML=rows.length?rows.map(t=>`<div class="item-row" style="align-items:flex-start"><div class="item-content"><b>${esc(t.subject)}</b><div class="subtitle">${esc(t.category)} • ${esc(t.status)} • ${t.updatedAt?new Date(t.updatedAt).toLocaleString():''}</div></div><div class="item-actions"><button class="btn btn-primary" data-teacher-support="${esc(t.id)}">Reply</button></div></div>`).join(''):'<p class="subtitle">No assigned support requests.</p>';
    document.querySelectorAll('[data-teacher-support]').forEach(b=>b.onclick=()=>replyTeacherSupport(b.dataset.teacherSupport));
  }catch(e){box.innerHTML=`<p class="error-text">${esc(e.message)}</p>`}
}
async function replyTeacherSupport(id){
  try{
    const d=await api('/api/support/tickets/'+encodeURIComponent(id)+'/messages');
    const messages=d.messages||[];
    const text=messages.map(m=>`${m.senderRole==='teacher'?'You':'Student'}: ${m.message}`).join('\n\n');
    const reply=prompt((text||'Conversation')+'\n\nWrite your reply:');
    if(!reply?.trim())return;
    await api('/api/support/tickets/'+encodeURIComponent(id)+'/messages',{method:'POST',body:JSON.stringify({message:reply.trim()})});
    toast('Reply sent','success'); loadTeacherSupport();
  }catch(e){toast(e.message,'error')}
}
const previousTeacherLoad=load;
load=async function(){await previousTeacherLoad();if($('teacherPanel')?.style.display!=='none'){loadTeacherSupport();}};

let currentAssignmentSubmissionId=null;
async function loadAssignmentSubmissions(id,title){
 currentAssignmentSubmissionId=id;
 const panel=document.getElementById('assignmentSubmissionsPanel'), list=document.getElementById('assignmentSubmissionsList'), label=document.getElementById('selectedAssignmentTitle');
 if(!panel||!list)return; panel.style.display='block'; if(label)label.textContent=title||''; list.innerHTML='<p class="subtitle">Loading submissions…</p>';
 try{
  const d=await api(`/api/teacher/assignments/${encodeURIComponent(id)}/submissions`); const rows=d.submissions||[];
  list.innerHTML=rows.length?rows.map(s=>`<div class="list-row" style="display:block;margin-bottom:10px"><div style="display:flex;justify-content:space-between;gap:8px"><div><strong>${esc(s.studentName||s.studentEmail||'Student')}</strong><div class="subtitle">${s.submittedAt?new Date(s.submittedAt).toLocaleString():''} • ${esc(s.status||'submitted')}</div></div>${s.status==='graded'?`<span class="badge badge-free">${esc(s.percentage)}%</span>`:''}</div><div style="margin-top:8px;white-space:pre-wrap">${esc(s.text||'')}</div>${s.link?`<div style="margin-top:6px"><a href="${esc(s.link)}" target="_blank" rel="noopener noreferrer">🔗 Open submission link</a></div>`:''}<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px"><input id="score-${esc(s.id)}" type="number" min="0" step="0.01" value="${s.score??''}" placeholder="Score" style="max-width:110px"><input id="max-score-${esc(s.id)}" type="number" min="1" step="0.01" value="${s.maxScore??100}" placeholder="Max" style="max-width:110px"><input id="feedback-${esc(s.id)}" value="${esc(s.feedback||'')}" placeholder="Feedback" style="flex:1;min-width:180px"><button class="btn btn-primary" type="button" data-grade-submission="${esc(s.id)}">${s.status==='graded'?'Update Grade':'Grade'}</button></div><div id="grade-status-${esc(s.id)}" class="subtitle" style="margin-top:5px"></div></div>`).join(''):'<p class="subtitle">No students have submitted this assignment yet.</p>';
  list.querySelectorAll('[data-grade-submission]').forEach(b=>b.onclick=async()=>{
   const id=b.dataset.gradeSubmission, score=document.getElementById(`score-${id}`)?.value, maxScore=document.getElementById(`max-score-${id}`)?.value, feedback=document.getElementById(`feedback-${id}`)?.value.trim()||'', status=document.getElementById(`grade-status-${id}`);
   try{const d=await api(`/api/teacher/submissions/${encodeURIComponent(id)}/grade`,{method:'POST',body:JSON.stringify({score,maxScore,feedback})});if(status)status.textContent=`✅ Graded ${d.percentage}%.`;toast('Grade saved','success');await loadAssignmentSubmissions(currentAssignmentSubmissionId,label?.textContent||'');}catch(e){if(status)status.textContent='❌ '+e.message;}
  });
 }catch(e){list.innerHTML=`<p class="error-text">${esc(e.message)}</p>`}
}

async function loadTeacherAssignments(){
 const list=$('assignmentList'), course=$('assignmentCourse'); if(!list)return;
 try{
  const d=await api('/api/teacher/assignments'); const rows=d.assignments||[];
  list.innerHTML=rows.length?rows.map(a=>`<div class="list-row"><div><strong>${esc(a.title)}</strong><div class="subtitle">Grade ${esc(a.className)}${a.dueAt?' • Due '+esc(a.dueAt):''}</div></div><div class="item-actions"><span class="badge badge-free">Published</span><button class="btn btn-secondary" type="button" data-view-submissions="${esc(a.id)}" data-assignment-title="${esc(a.title)}">📥 Submissions</button></div></div>`).join(''):'<p class="subtitle">No assignments yet.</p>'; list.querySelectorAll('[data-view-submissions]').forEach(b=>b.onclick=()=>{document.querySelectorAll('[data-view-submissions]').forEach(x=>x.classList.remove('active-submission'));b.classList.add('active-submission');loadAssignmentSubmissions(b.dataset.viewSubmissions,b.dataset.assignmentTitle)});
 }catch(e){list.innerHTML=`<p class="error-text">${esc(e.message)}</p>`}
}
$('assignmentForm')?.addEventListener('submit',async e=>{
 e.preventDefault();
 try{const r=await api('/api/teacher/assignments',{method:'POST',body:JSON.stringify({title:$('assignmentTitle').value.trim(),description:$('assignmentDescription').value.trim(),className:$('assignmentGrade').value,courseId:$('assignmentCourse').value,dueAt:$('assignmentDue').value})}); $('assignmentStatus').textContent='✅ Assignment published.'; e.target.reset(); toast('Assignment published','success'); loadTeacherAssignments();}catch(err){$('assignmentStatus').textContent='❌ '+err.message;toast(err.message,'error')}});
const loadBeforeAssignments=load;
load=async function(){await loadBeforeAssignments();if($('teacherPanel')?.style.display!=='none'){
 const cs=await api('/api/teacher/courses').catch(()=>({courses:[]})); $('assignmentCourse').innerHTML=(cs.courses||[]).map(c=>`<option value="${esc(c.id)}">${esc(c.title)} — Grade ${esc(c.className)}</option>`).join(''); loadTeacherAssignments(); loadTeacherExamResults();
}};

document.getElementById('refreshAssignmentSubmissions')?.addEventListener('click',()=>{const title=document.getElementById('selectedAssignmentTitle')?.textContent||'';if(currentAssignmentSubmissionId)loadAssignmentSubmissions(currentAssignmentSubmissionId,title);});
