import { initializeApp } from "https://www.gstatic.com/firebasejs/10.7.1/firebase-app.js";
import { getAuth, onAuthStateChanged, signOut, signInWithEmailAndPassword, createUserWithEmailAndPassword, signInWithCustomToken, RecaptchaVerifier, signInWithPhoneNumber, setPersistence, inMemoryPersistence } from "https://www.gstatic.com/firebasejs/10.7.1/firebase-auth.js";
import { getFirestore } from "https://www.gstatic.com/firebasejs/10.7.1/firebase-firestore.js";

const firebaseConfig={apiKey:"AIzaSyAyeZpwu9-FECjC5Qp-lI0OUAblKusxkeI",authDomain:"bright-mind-tutor-app.firebaseapp.com",projectId:"bright-mind-tutor-app",storageBucket:"bright-mind-tutor-app.firebasestorage.app",messagingSenderId:"782512714975",appId:"1:782512714975:web:719e3b7a09ac8c7f9d256a",measurementId:"G-TWYNFN7MT6"};
const app=initializeApp(firebaseConfig),auth=getAuth(app),db=getFirestore(app); window.auth=auth;
// Shared auth bridge for dashboard integrations. Keep the first auth state
// asynchronous so integrations never treat the initial null as a real logout.
window.BMTAuthReady = new Promise(resolve => {
 const unsubscribe = onAuthStateChanged(auth, user => { unsubscribe(); resolve(user); });
});
window.BMTWaitForAuth = function(timeoutMs = 10000) {
 if (auth.currentUser) return Promise.resolve(auth.currentUser);
 return new Promise(resolve => {
  let settled = false;
  const finish = user => {
   if (settled) return;
   settled = true;
   clearTimeout(timer);
   unsubscribe();
   resolve(user || auth.currentUser || null);
  };
  const unsubscribe = onAuthStateChanged(auth, finish);
  const timer = setTimeout(() => finish(auth.currentUser), timeoutMs);
 });
};
let teacher=null; let communityUnsub=null; let communityPollTimer=null; let communitySending=false; let teacherReplyTo=null; let teacherRecordedBlob=null; let teacherRecordedDurationSeconds=null; let teacherRecordingTimer=null; let teacherRecordingStartedAt=0;
const $=id=>document.getElementById(id);
const BMT_PREVIEW_PARAMS = new URLSearchParams(location.search);
const BMT_PREVIEW_ROLE = BMT_PREVIEW_PARAMS.get('preview') === '1' && BMT_PREVIEW_PARAMS.get('role') === 'teacher' ? 'teacher' : '';
const BMT_PREVIEW_TOKEN = BMT_PREVIEW_PARAMS.get('token') || '';
const BMT_PREVIEW_UID = BMT_PREVIEW_PARAMS.get('uid') || '';
window.BMT_ADMIN_PREVIEW = false;
let _bmtPreviewSignInAttempted = false;
const esc=x=>String(x??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
function withTimeout(promise, ms=15000, message='Request timed out. Please check your connection and try again.'){return Promise.race([promise,new Promise((_,reject)=>setTimeout(()=>reject(new Error(message)),ms))]);}
function toast(m,type='info'){const e=document.createElement('div');e.className=`toast toast-${type} show`;e.textContent=m;document.body.appendChild(e);setTimeout(()=>e.remove(),3500)}
async function api(path,options={}){if(!teacher) throw Error('Authentication required.');const token=await teacher.getIdToken();const res=await fetch(path,{...options,headers:{'Content-Type':'application/json',Authorization:`Bearer ${token}`,...(options.headers||{})}});const data=await res.json().catch(()=>({}));if(!res.ok)throw Error(data.error||`Request failed (${res.status})`);return data}
function selectedValues(id){return [...$(id).selectedOptions].map(o=>o.value)}

// Applied AFTER the real teacher's real data has loaded via the normal load()
// path. Adds a banner and locks every control that could write data — it
// never fabricates content, so the admin sees the genuine dashboard.
function applyTeacherPreviewSafeguards(){
 const old=document.querySelector('.bmt-preview-banner'); if(old) old.remove();
 const b=document.createElement('div'); b.className='bmt-preview-banner'; b.innerHTML='<strong>ADMIN PREVIEW</strong> — Real Teacher Dashboard <span>Read-only view</span> <a href="/admin">Return to Admin</a>'; document.body.prepend(b);
 document.querySelectorAll('#teacherPanel form, #teacherPanel input[type=file], #teacherPanel textarea, #teacherPanel select, #teacherPanel input').forEach(el=>el.disabled=true);
 document.querySelectorAll('#teacherPanel button').forEach(btn=>{if(!btn.closest('.bmt-preview-banner'))btn.disabled=true;});
}

async function loadTeacherStudentDirectory(){
 const body=$('teacherStudentDirectoryBody'); if(!body||!teacher)return; const status=$('teacherStudentDirectoryStatus');
 try{const d=await api('/api/teacher/students');const rows=d.students||[];body.innerHTML=rows.length?rows.map(x=>`<tr><td><b>${esc(x.name)}</b></td><td>${esc(x.phone||'—')}</td><td>${esc(x.email||'—')}</td><td>${esc(x.grade||'—')}</td><td>${x.isPaid?'✅ Paid':'⏳ Unpaid'}</td></tr>`).join(''):'<tr><td colspan="5" class="subtitle">No students found in your teaching grades.</td></tr>';if(status)status.textContent=rows.length?`${rows.length} students`:'No students';}catch(e){body.innerHTML=`<tr><td colspan="5" class="error-text">${esc(e.message)}</td></tr>`;}
}
async function load(){
 try{
  const data=await api('/api/teacher/profile');
  if(!data.approved){$('teacherDenied').style.display='block';$('teacherPanel').style.display='none';return;}
  const p=data.profile||{};$('teacherDenied').style.display='none';$('teacherPanel').style.display='block';
  $('teacherIdentity').textContent=`${p.name||teacher.displayName||teacher.email} • ${(p.subjects||[]).join(', ')||'Teacher'}`;
  await loadCourses(); await loadTeacherStudentDirectory(); await loadAiMaterials(); await loadQuestionBank(); setupTeacherCommunityChat(); loadOrgLogo(); loadNotifications();
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
  return `<div class="item-row bmt-chat-interactive" data-chat-msgid="${esc(m.id)}" data-chat-sender="${esc(label)}" style="align-items:flex-start;margin:4px 0"><div class="item-content"><div><b style="font-size:12px">${esc(label)}</b> <span class="subtitle" style="font-size:10px">${m.createdAt?.toDate?m.createdAt.toDate().toLocaleString():(m.createdAt?new Date(m.createdAt).toLocaleString():'')}</span></div>${m.replyTo?`<div class="subtitle" style="margin:3px 0;padding:4px;font-size:11px;border-left:3px solid var(--primary)">↩️ ${esc(m.replyToText||'Original message')}</div>`:''}<div style="margin-top:3px;font-size:13px;line-height:1.35">${esc(m.messageText||m.message||'')}</div>${m.mediaUrl ? (m.mediaType==='image' ? `<img src="${esc(m.mediaUrl)}" data-chat-media-open data-media-type="image" alt="Shared image" style="display:block;max-width:200px;max-height:180px;border-radius:10px;margin-top:6px;object-fit:cover;cursor:zoom-in;">` : m.mediaType==='audio' ? `<audio controls data-chat-media-open data-media-type="audio" data-media-url="${esc(m.mediaUrl)}" src="${esc(m.mediaUrl)}" style="display:block;max-width:240px;margin-top:6px;"></audio>${m.durationSeconds ? `<div class="subtitle" style="margin-top:2px;font-size:10px;">🎤 ${Math.round(Number(m.durationSeconds))}s voice message</div>` : ''}` : m.mediaType==='video' ? `<video controls data-chat-media-open data-media-type="video" data-media-url="${esc(m.mediaUrl)}" src="${esc(m.mediaUrl)}" style="display:block;max-width:240px;max-height:170px;margin-top:6px;border-radius:10px;cursor:zoom-in;"></video>` : `<button type="button" class="bmt-chat-file-open" data-chat-media-open data-media-type="file" data-media-url="${esc(m.mediaUrl)}">📎 Open media</button>`) : ''}<div style="display:flex;gap:3px;flex-wrap:wrap;margin-top:5px"><button class="btn chat-reaction-btn" data-t-reply="${m.id}">↩️</button><button class="btn chat-reaction-btn" data-t-react="${m.id}:heart">❤️ ${reactions.heart||0}</button><button class="btn chat-reaction-btn" data-t-react="${m.id}:like">👍 ${reactions.like||0}</button><button class="btn chat-reaction-btn" data-t-react="${m.id}:dislike">👎 ${reactions.dislike||0}</button><button class="btn chat-reaction-btn" data-t-react="${m.id}:laugh">😂 ${reactions.laugh||0}</button><button class="btn chat-reaction-btn" data-t-react="${m.id}:wow">😮 ${reactions.wow||0}</button><button class="btn chat-reaction-btn" data-t-react="${m.id}:fire">🔥 ${reactions.fire||0}</button><button class="btn chat-reaction-btn" data-t-report="${m.id}" title="Report this message">🚩</button></div></div></div>`;
 }).join(''):'<p class="subtitle">No community messages yet.</p>';
 box.querySelectorAll('[data-t-reply]').forEach(b=>b.onclick=()=>{teacherReplyTo=b.dataset.tReply; const i=$('teacherCommunityMessage'); if(i){i.focus();i.placeholder='Replying to selected message...';}});
 box.querySelectorAll('[data-t-react]').forEach(b=>b.onclick=async()=>{const [messageId,reaction]=b.dataset.tReact.split(':');try{await api('/api/chat/reaction',{method:'POST',body:JSON.stringify({messageId,reaction})});}catch(e){toast(e.message,'error')}});
 box.querySelectorAll('[data-chat-media-open]').forEach(el=>el.onclick=()=>{const url=el.dataset.mediaUrl||el.currentSrc||el.src||'';if(url)openTeacherChatMediaViewer(url,el.dataset.mediaType||'file');});
 box.querySelectorAll('[data-chat-msgid]').forEach(row=>{let timer=null,startPoint=null;const cancel=()=>{if(timer){clearTimeout(timer);timer=null}};const start=ev=>{const p=ev.touches?ev.touches[0]:ev;if(ev.type==='mousedown'&&ev.button!==0)return;startPoint={x:p.clientX,y:p.clientY};timer=setTimeout(()=>{timer=null;openTeacherChatMenu(row,p.clientX,p.clientY)},550)};const move=ev=>{if(!startPoint)return;const p=ev.touches?ev.touches[0]:ev;if(Math.hypot(p.clientX-startPoint.x,p.clientY-startPoint.y)>12)cancel()};row.addEventListener('touchstart',start,{passive:true});row.addEventListener('touchend',cancel);row.addEventListener('touchmove',move,{passive:true});row.addEventListener('mousedown',start);row.addEventListener('mouseup',cancel);row.addEventListener('mouseleave',cancel)});
 box.querySelectorAll('[data-t-report]').forEach(b=>b.onclick=async()=>{const reason=prompt('Why are you reporting this message?');if(!reason?.trim())return;try{await api('/api/chat/report',{method:'POST',body:JSON.stringify({messageId:b.dataset.tReport,reason:reason.trim()})});toast('Report submitted.','success')}catch(e){toast(e.message,'error')}});
}
window.sendTeacherCommunityMessage = sendTeacherCommunityMessage;
function openTeacherChatMediaViewer(url,type){
 document.querySelectorAll('.bmt-chat-viewer').forEach(v=>v.remove());const viewer=document.createElement('div');viewer.className='bmt-chat-viewer';let content=type==='image'?`<img src="${esc(url)}" class="bmt-chat-viewer-media" alt="Shared image">`:type==='video'?`<video controls autoplay playsinline src="${esc(url)}" class="bmt-chat-viewer-media"></video>`:type==='audio'?`<audio controls autoplay src="${esc(url)}" class="bmt-chat-viewer-audio"></audio>`:`<div class="bmt-chat-viewer-file">📎<strong>Shared file</strong><span>You can open or download this file.</span></div>`;viewer.innerHTML=`<div class="bmt-chat-viewer-backdrop" data-viewer-close></div><div class="bmt-chat-viewer-panel" role="dialog" aria-modal="true"><div class="bmt-chat-viewer-head"><strong>Shared media</strong><button type="button" class="bmt-chat-viewer-close" data-viewer-close>✕</button></div><div class="bmt-chat-viewer-content">${content}</div><div class="bmt-chat-viewer-actions"><button type="button" data-viewer-download>⬇️ Download</button><button type="button" data-viewer-share>↗️ Share</button><button type="button" data-viewer-open>↗️ Open</button></div></div>`;document.body.appendChild(viewer);const close=()=>viewer.remove();viewer.querySelectorAll('[data-viewer-close]').forEach(b=>b.onclick=close);viewer.querySelector('[data-viewer-open]').onclick=()=>window.open(url,'_blank','noopener');viewer.querySelector('[data-viewer-download]').onclick=()=>downloadTeacherChatMedia(url);viewer.querySelector('[data-viewer-share]').onclick=async()=>{try{if(navigator.share)await navigator.share({title:'BMT shared media',url});else{await navigator.clipboard.writeText(url);toast('Media link copied.','success')}}catch(e){if(e?.name!=='AbortError')toast('Could not share media.','error')}};
}
async function downloadTeacherChatMedia(url){try{const r=await fetch(url,{mode:'cors'});if(!r.ok)throw Error();const blob=await r.blob();const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=(url.split('/').pop()||'bmt-media').split('?')[0];document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),1000)}catch(_){window.open(url,'_blank','noopener');toast('Download opened in a new tab.','info')}}
function openTeacherChatMenu(row,x,y){document.querySelectorAll('.bmt-chat-menu').forEach(m=>m.remove());const media=row.querySelector('[data-chat-media-open]');const url=media?.dataset.mediaUrl||media?.currentSrc||media?.src||'';const type=media?.dataset.mediaType||'file';const menu=document.createElement('div');menu.className='bmt-chat-menu';menu.style.left=Math.max(8,x)+'px';menu.style.top=Math.max(8,y)+'px';menu.innerHTML=`${url?'<button type="button" class="bmt-chat-menu-item" data-menu-open>🖼️ Open</button><button type="button" class="bmt-chat-menu-item" data-menu-download>⬇️ Download</button><button type="button" class="bmt-chat-menu-item" data-menu-share>↗️ Share</button>':''}<button type="button" class="bmt-chat-menu-item" data-menu-reply>↩️ Reply</button>`;document.body.appendChild(menu);const close=()=>{menu.remove();document.removeEventListener('click',close);document.removeEventListener('touchstart',close)};setTimeout(()=>{document.addEventListener('click',close);document.addEventListener('touchstart',close)},0);menu.querySelector('[data-menu-open]')?.addEventListener('click',e=>{e.stopPropagation();close();openTeacherChatMediaViewer(url,type)});menu.querySelector('[data-menu-download]')?.addEventListener('click',e=>{e.stopPropagation();close();downloadTeacherChatMedia(url)});menu.querySelector('[data-menu-share]')?.addEventListener('click',async e=>{e.stopPropagation();close();try{if(navigator.share)await navigator.share({title:'BMT shared media',url});else{await navigator.clipboard.writeText(url);toast('Media link copied.','success')}}catch(err){if(err?.name!=='AbortError')toast('Could not share media.','error')}});menu.querySelector('[data-menu-reply]')?.addEventListener('click',e=>{e.stopPropagation();close();teacherReplyTo=row.dataset.chatMsgid;const input=$('teacherCommunityMessage');if(input){input.focus();input.placeholder='Replying to selected message...'}});requestAnimationFrame(()=>{const r=menu.getBoundingClientRect();if(r.right>innerWidth)menu.style.left=Math.max(8,innerWidth-r.width-8)+'px';if(r.bottom>innerHeight)menu.style.top=Math.max(8,innerHeight-r.height-8)+'px'})}

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
 communityPollTimer=setInterval(loadTeacherCommunityChat,2000);
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
   await api('/api/chat/community/reply',{method:'POST',body:JSON.stringify({messageId:teacherReplyTo,message:text,mediaUrl,mediaType,durationSeconds:teacherRecordedDurationSeconds||undefined})});
  } else {
   await api('/api/chat/community/send',{method:'POST',body:JSON.stringify({message:text,mediaUrl,mediaType,durationSeconds:teacherRecordedDurationSeconds||undefined})});
  }
  input.value=''; if($('teacherCommunityMedia'))$('teacherCommunityMedia').value=''; teacherRecordedBlob=null; teacherRecordedDurationSeconds=null; teacherReplyTo=null; input.placeholder='Write to students, teachers and admins...';
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
 const btn=$('teacherRecordBtn'); if(!btn||!navigator.mediaDevices?.getUserMedia)return; let recorder=null,chunks=[]; const MAX=60;
 btn.onclick=async()=>{
  if(recorder&&recorder.state==='recording'){recorder.stop();return;}
  try{
   const stream=await navigator.mediaDevices.getUserMedia({audio:true});
   const mime=['audio/webm;codecs=opus','audio/webm','audio/ogg','audio/mp4'].find(x=>window.MediaRecorder?.isTypeSupported?.(x))||'';
   recorder=mime?new MediaRecorder(stream,{mimeType:mime}):new MediaRecorder(stream); chunks=[]; teacherRecordingStartedAt=Date.now();
   teacherRecordingTimer=setInterval(()=>{const sec=Math.min(MAX,Math.floor((Date.now()-teacherRecordingStartedAt)/1000));btn.textContent=`⏹ ${String(Math.floor(sec/60)).padStart(2,'0')}:${String(sec%60).padStart(2,'0')}`;if(sec>=MAX&&recorder?.state==='recording')recorder.stop();},250);
   recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data)};
   recorder.onstop=()=>{clearInterval(teacherRecordingTimer);teacherRecordingTimer=null;stream.getTracks().forEach(t=>t.stop());const sec=Math.min(MAX,Math.max(1,Math.ceil((Date.now()-teacherRecordingStartedAt)/1000)));const blob=new Blob(chunks,{type:recorder.mimeType||'audio/webm'});teacherRecordedBlob=new File([blob],`teacher-voice-${Date.now()}.webm`,{type:blob.type||'audio/webm'});teacherRecordedDurationSeconds=sec;btn.textContent=`🎤 ${sec}s ready`;toast(`Voice message ready (${sec}s). Click Send.`,'success');};
   recorder.start(250);btn.textContent='⏹ 00:00';
  }catch(e){toast('Microphone permission is required for voice recording.','error');}
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
async function phonePinLoginTeacher(){
 const phone=$('teacherPhoneLoginNumber')?.value.trim(),pin=$('teacherPhoneLoginPin')?.value.trim(),status=$('teacherPhoneLoginStatus'),btn=$('teacherPhoneLoginBtn');
 if(!phone||!/^[0-9]{6,8}$/.test(pin)){if(status)status.textContent='❌ Enter phone number and 6–8 digit PIN.';return}
 btn.disabled=true;btn.textContent='Signing in…';
 try{const r=await fetch('/api/auth/phone-pin/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone,pin,role:'teacher'})});const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.error||'Phone sign-in failed.');await signInWithCustomToken(auth,d.token);if($('teacherPhoneOtp')?.checked){try{const rv=new RecaptchaVerifier(auth,'teacherPhoneOtpBtn',{size:'invisible'});const normalized=phone.startsWith('+251')?phone:phone.startsWith('0')?'+251'+phone.slice(1):'+251'+phone;const confirmation=await signInWithPhoneNumber(auth,normalized,rv);const code=window.prompt('Enter the 6-digit SMS code:');if(code) {await confirmation.confirm(code.trim());const t=await auth.currentUser.getIdToken(true);await fetch('/api/auth/phone-pin/mark-verified',{method:'POST',headers:{Authorization:`Bearer ${t}`}});status.textContent='✅ Signed in and phone verified.';}}catch(_){status.textContent='⚠️ PIN login succeeded; SMS verification was not completed.';}}else if(status)status.textContent='✅ Signed in. Checking teacher approval…';toast('Signed in','success')}catch(e){if(status)status.textContent='❌ '+e.message;toast(e.message,'error')}finally{btn.disabled=false;btn.textContent='Sign In'}
}
window.phonePinLoginTeacher=phonePinLoginTeacher;

async function registerTeacherPhonePin(){
 const name=$('teacherPhoneRegName')?.value.trim(),phone=$('teacherPhoneRegNumber')?.value.trim(),pin=$('teacherPhoneRegPin')?.value.trim(),confirm=$('teacherPhoneRegConfirm')?.value.trim(),status=$('teacherPhoneRegStatus'),btn=$('teacherPhoneRegBtn');
 if(!name||!phone||!/^[0-9]{6,8}$/.test(pin)||pin!==confirm){status.textContent='❌ Complete all fields and make sure the PINs match.';return}
 btn.disabled=true;btn.textContent='Creating…';try{const r=await fetch('/api/auth/phone-pin/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({role:'teacher',name,phone,pin})});const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.error||'Phone registration failed.');status.textContent='✅ Account created. Complete your teacher application after signing in.';toast('Teacher account created','success');}catch(e){status.textContent='❌ '+e.message}finally{btn.disabled=false;btn.textContent='Create Teacher Account'}
}
window.registerTeacherPhonePin=registerTeacherPhonePin;

async function loadTeacherParentContacts(){const box=$('teacherParentContacts');if(!box||!auth.currentUser)return;try{const d=await api('/api/teacher/contacts');const rows=d.contacts||[];box.innerHTML=rows.length?rows.map(c=>`<button type="button" class="card" data-teacher-parent="${esc(c.uid)}" style="text-align:left;cursor:pointer"><strong>👨‍👩‍👧 ${esc(c.name)}</strong><div class="subtitle">Parent${c.childUid?' · linked student':''}</div></button>`).join(''):'<p class="subtitle">No connected parent contacts yet.</p>';box.querySelectorAll('[data-teacher-parent]').forEach(b=>b.onclick=()=>openTeacherParentChat(b.dataset.teacherParent,b.querySelector('strong')?.textContent||'Parent'));}catch(e){box.innerHTML=`<p class="error-text">${esc(e.message||'Unable to load parent contacts.')}</p>`;}}
async function openTeacherParentChat(uid,name){window._teacherParentUid=uid;$('teacherParentChat').style.display='block';$('teacherParentChatTitle').textContent=name;await loadTeacherParentMessages();}
async function loadTeacherParentMessages(){const uid=window._teacherParentUid;if(!uid)return;try{const d=await api('/api/messages/'+encodeURIComponent(uid));$('teacherParentChatMessages').innerHTML=(d.messages||[]).map(m=>`<div class="item-row"><strong>${esc(m.senderName||'User')}</strong><div>${esc(m.message||'')}</div><div class="subtitle">${m.createdAt?new Date(m.createdAt).toLocaleString():''}</div></div>`).join('')||'<p class="subtitle">No messages yet.</p>';$('teacherParentChatMessages').scrollTop=$('teacherParentChatMessages').scrollHeight;}catch(e){$('teacherParentChatMessages').innerHTML=`<p class="error-text">${esc(e.message||'Unable to load messages.')}</p>`;}}
async function sendTeacherParentMessage(){const uid=window._teacherParentUid,input=$('teacherParentChatInput');if(!uid||!input.value.trim())return;try{await api('/api/messages/'+encodeURIComponent(uid),{method:'POST',body:JSON.stringify({message:input.value.trim()})});input.value='';await loadTeacherParentMessages();}catch(e){toast(e.message||'Unable to send message','error');}}
$('teacherParentChatSend')?.addEventListener('click',sendTeacherParentMessage);

const teacherLoginForm = $('teacherUnifiedLoginForm');
if (teacherLoginForm) teacherLoginForm.addEventListener('submit', async e => {
  e.preventDefault();
  const identity = $('teacherLoginIdentity')?.value.trim();
  const password = $('teacherLoginSecret')?.value || '';
  const status = $('teacherLoginStatus');
  const btn = $('teacherLoginBtn');
  if (!identity || !password) { if (status) status.textContent='❌ Enter your email/phone and password/PIN.'; return; }
  if (btn) { btn.disabled=true; btn.textContent='Signing in…'; }
  if (status) status.textContent='Checking your account…';
  try {
    const looksPhone = /^[+0-9][0-9\s().-]{7,}$/.test(identity) && !identity.includes('@');
    if (looksPhone) {
      if (!/^[0-9]{6,8}$/.test(password)) throw Error('Phone sign-in requires a 6–8 digit PIN.');
      const r=await fetch('/api/auth/phone-pin/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:identity,pin:password,role:'teacher'})});
      const d=await r.json().catch(()=>({})); if(!r.ok) throw Error(d.error||'Phone sign-in failed.');
      await signInWithCustomToken(auth,d.token);
    } else {
      await withTimeout(signInWithEmailAndPassword(auth, identity, password), 15000, 'Sign-in timed out. Please check your connection and try again.');
    }
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
  if(!u){
   if(_bmtPreviewSignInAttempted) return;
   _bmtPreviewSignInAttempted=true;
   try{
    if(!BMT_PREVIEW_UID) throw Error('Preview link is missing its target account.');
    await setPersistence(auth, inMemoryPersistence);
    const r=await fetch(`/api/admin/preview-signin?role=teacher&uid=${encodeURIComponent(BMT_PREVIEW_UID)}&token=${encodeURIComponent(BMT_PREVIEW_TOKEN)}`);
    const d=await r.json().catch(()=>({}));
    if(!r.ok||!d.token) throw Error(d.error||'Preview link is invalid or expired.');
    await signInWithCustomToken(auth,d.token);
   }catch(e){toast(e.message,'error');}
   return;
  }
  window.BMT_ADMIN_PREVIEW=true;
 }
 teacher=u;window.dispatchEvent(new Event('BMTAuthReady'));if($('teacherHeader')) $('teacherHeader').style.display=u?'flex':'none';if(window.BMTScannerUI){window.BMT_SCANNER_CONFIG={getToken:()=>teacher?.getIdToken(true)};window.BMTScannerUI.init();}if(!u){location.href='/auth?role=teacher&next='+encodeURIComponent(location.pathname);}else{await load();if(BMT_PREVIEW_ROLE) applyTeacherPreviewSafeguards();}
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

// ------------------------- Notifications -------------------------
let _teacherNotifications=[];
function notificationTime(iso){
 if(!iso)return '';
 const d=new Date(iso),diff=Math.max(0,Date.now()-d.getTime());
 const mins=Math.floor(diff/60000),hrs=Math.floor(mins/60),days=Math.floor(hrs/24);
 if(mins<1)return 'just now'; if(mins<60)return `${mins}m ago`; if(hrs<24)return `${hrs}h ago`; if(days<7)return `${days}d ago`;
 return d.toLocaleDateString();
}
function renderNotifications(){
 const list=$('notificationList'),badge=$('notificationBadge'); if(!list)return;
 const unread=_teacherNotifications.filter(n=>!n.read).length;
 if(badge){badge.textContent=unread>99?'99+':String(unread);badge.style.display=unread?'block':'none';}
 list.innerHTML=_teacherNotifications.length?_teacherNotifications.map(n=>`<button type="button" data-notification-id="${esc(n.id)}" style="display:block;width:100%;text-align:left;border:0;border-bottom:1px solid var(--border-color);background:${n.read?'transparent':'var(--primary-light)'};padding:12px 4px;cursor:pointer;color:inherit"><div style="display:flex;justify-content:space-between;gap:10px"><strong>${esc(n.title)}</strong><small class="subtitle">${esc(notificationTime(n.createdAt))}</small></div><div style="margin-top:4px;font-size:.9em">${esc(n.message)}</div></button>`).join(''):'<p class="subtitle">No notifications yet.</p>';
 list.querySelectorAll('[data-notification-id]').forEach(el=>el.onclick=()=>markNotificationRead(el.dataset.notificationId));
}
window.loadNotifications=async function(){
 if(!teacher)return;
 try{const d=await api('/api/notifications');_teacherNotifications=d.notifications||[];renderNotifications();}catch(e){console.error('Notifications:',e);}
};
window.toggleNotificationPanel=function(){const p=$('notificationPanel');if(!p)return;const open=p.style.display!=='none';p.style.display=open?'none':'block';if(!open)loadNotifications();};
window.markNotificationRead=async function(id){
 const n=_teacherNotifications.find(x=>x.id===id);if(n)n.read=true;renderNotifications();
 try{await api(`/api/notifications/${encodeURIComponent(id)}/read`,{method:'POST'});}catch(e){console.error(e);}
};
window.markAllNotificationsRead=async function(){
 try{await api('/api/notifications/read-all',{method:'POST'});_teacherNotifications.forEach(n=>n.read=true);renderNotifications();}catch(e){toast(e.message,'error');}
};

async function loadOrgLogo(){ try{const r=await fetch('/api/settings/organization');const d=await r.json();if(d.success&&d.logoUrl){document.querySelectorAll('#navLogo, .org-logo').forEach(img=>{if(img)img.src=d.logoUrl;});}}catch(e){console.warn('Organization logo load failed; using default.',e);} }
// ------------------------- Question Bank -------------------------
async function loadQuestionBank(){
 const list=$('qbList'); if(!list||!teacher)return;
 list.innerHTML='<div class="spinner"></div> Loading…';
 try{
  const status=$('qbFilterStatus')?.value||'draft';
  const subject=$('qbFilterSubject')?.value.trim()||'';
  const params=new URLSearchParams({status}); if(subject)params.set('subject',subject);
  const data=await api('/api/question-bank?'+params.toString());
  const rows=data.questions||[];
  const isApprovedView=status==='approved';
  $('qbAssignPanel').style.display=isApprovedView&&rows.length?'block':'none';
  if(isApprovedView&&rows.length) loadTelegramTargetsOnce();
  list.innerHTML=rows.length?rows.map(q=>`<div class="list-row">${isApprovedView?`<input type="checkbox" class="qb-select" value="${esc(q.id)}" style="margin-right:8px">`:''}<div><strong>${esc(q.question)}</strong><div class="subtitle">${esc(q.subject||'')} ${q.grade?'· Grade '+esc(q.grade):''} ${q.domain?'· '+esc(q.domain):''} · ${esc(q.difficulty||'medium')}</div></div><div style="display:flex;gap:6px;flex-wrap:wrap">${status==='draft'?`<button class="btn btn-outline" data-qb-approve="${esc(q.id)}">Approve</button>`:''}<button class="btn btn-danger" data-qb-delete="${esc(q.id)}">Delete</button></div></div>`).join(''):'<p class="subtitle">No questions found for this filter.</p>';
  list.querySelectorAll('[data-qb-approve]').forEach(b=>b.onclick=async()=>{try{await api('/api/question-bank/'+encodeURIComponent(b.dataset.qbApprove)+'/approve',{method:'POST'});toast('Question approved','success');loadQuestionBank();}catch(e){toast(e.message,'error');}});
  list.querySelectorAll('[data-qb-delete]').forEach(b=>b.onclick=async()=>{if(!confirm('Delete this question?'))return;try{await api('/api/question-bank/'+encodeURIComponent(b.dataset.qbDelete),{method:'DELETE'});toast('Question deleted','success');loadQuestionBank();}catch(e){toast(e.message,'error');}});
 }catch(e){list.innerHTML=`<p class="error-text">${esc(e.message)}</p>`;}
}
$('qbAssignBtn')?.addEventListener('click',async()=>{
 const ids=[...document.querySelectorAll('.qb-select:checked')].map(c=>c.value);
 const className=$('qbAssignClass').value, assignmentType=$('qbAssignType').value;
 const status=$('qbAssignStatus');
 if(!ids.length){if(status)status.textContent='Select at least one question first.';return;}
 if(!className){if(status)status.textContent='Choose a class.';return;}
 if(status)status.textContent='Assigning…';
 try{
  const data=await api('/api/question-bank/assign',{method:'POST',body:JSON.stringify({questionIds:ids,className,assignmentType})});
  if(status)status.textContent=`✅ Assigned ${data.assignedCount} question(s) to Class ${className}.`;
  toast(`Assigned ${data.assignedCount} question(s)`,'success');
  document.querySelectorAll('.qb-select:checked').forEach(c=>c.checked=false);
 }catch(e){if(status)status.textContent='❌ '+e.message; toast(e.message,'error');}
});
let _qbTelegramTargetsLoaded=false;
async function loadTelegramTargetsOnce(){
 if(_qbTelegramTargetsLoaded)return; _qbTelegramTargetsLoaded=true;
 try{
  const data=await api('/api/telegram/publisher/targets');
  const rows=data.targets||[];
  const sel=$('qbTelegramTarget'); if(!sel)return;
  sel.innerHTML='<option value="">Send to Telegram target…</option>'+rows.map(t=>`<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('');
 }catch(e){console.error('Telegram targets:',e);}
}
$('qbTelegramSendBtn')?.addEventListener('click',async()=>{
 const ids=[...document.querySelectorAll('.qb-select:checked')].map(c=>c.value);
 const targetId=$('qbTelegramTarget').value;
 const status=$('qbTelegramStatus');
 if(!ids.length){if(status)status.textContent='Select at least one question first.';return;}
 if(!targetId){if(status)status.textContent='Choose a Telegram target.';return;}
 if(status)status.textContent='Sending…';
 try{
  const data=await api('/api/telegram/publisher/send-quiz',{method:'POST',body:JSON.stringify({questionIds:ids,targetId})});
  if(status)status.textContent=`✅ Sent ${data.sentCount} question(s) to Telegram.${data.failed?.length?` (${data.failed.length} skipped)`:''}`;
  toast(`Sent ${data.sentCount} question(s) to Telegram`,'success');
  document.querySelectorAll('.qb-select:checked').forEach(c=>c.checked=false);
 }catch(e){if(status)status.textContent='❌ '+e.message; toast(e.message,'error');}
});
$('qbFilterStatus')?.addEventListener('change',loadQuestionBank);
$('qbRefreshBtn')?.addEventListener('click',loadQuestionBank);
$('qbAiDraftBtn')?.addEventListener('click',async()=>{
 const subject=$('qbSubject').value.trim(),grade=$('qbGrade').value,domain=$('qbDomain').value.trim(),difficulty=$('qbDifficulty').value;
 const status=$('qbAiStatus');
 if(!subject&&!domain){if(status)status.textContent='Enter a subject or topic first.';return;}
 if(status)status.textContent='✨ Drafting with AI…';
 try{
  const data=await api('/api/question-bank/ai-draft',{method:'POST',body:JSON.stringify({subject,grade,domain,difficulty})});
  const d=data.draft||{};
  $('qbQuestion').value=d.question||'';
  $('qbOptionA').value=d.options?.A||''; $('qbOptionB').value=d.options?.B||'';
  $('qbOptionC').value=d.options?.C||''; $('qbOptionD').value=d.options?.D||'';
  $('qbCorrectAnswer').value=d.correctAnswer||'';
  if(status)status.textContent='✅ AI draft ready — review and edit before saving.';
 }catch(e){if(status)status.textContent='⚠️ '+e.message;}
});
$('qbForm')?.addEventListener('submit',async e=>{
 e.preventDefault();
 const options={A:$('qbOptionA').value.trim(),B:$('qbOptionB').value.trim(),C:$('qbOptionC').value.trim(),D:$('qbOptionD').value.trim()};
 Object.keys(options).forEach(k=>{if(!options[k])delete options[k];});
 const body={
  question:$('qbQuestion').value.trim(), options,
  correctAnswer:$('qbCorrectAnswer').value,
  subject:$('qbSubject').value.trim(), grade:$('qbGrade').value,
  domain:$('qbDomain').value.trim(), difficulty:$('qbDifficulty').value,
  points:Number($('qbPoints').value||1),
 };
 try{
  await api('/api/question-bank',{method:'POST',body:JSON.stringify(body)});
  $('qbStatus').textContent='✅ Saved as draft — an approved teacher or admin can approve it.';
  toast('Question saved as draft','success');
  e.target.reset(); $('qbDifficulty').value='medium';
  await loadQuestionBank();
 }catch(err){$('qbStatus').textContent='❌ '+err.message; toast(err.message,'error');}
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
  loadTeacherParentContacts();
}};

document.getElementById('refreshAssignmentSubmissions')?.addEventListener('click',()=>{const title=document.getElementById('selectedAssignmentTitle')?.textContent||'';if(currentAssignmentSubmissionId)loadAssignmentSubmissions(currentAssignmentSubmissionId,title);});

document.getElementById('refreshTeacherStudentsBtn')?.addEventListener('click',loadTeacherStudentDirectory);

// ==========================================================
// 🔎 V31.86 — quick client-side filters for long teacher lists.
// Hides/shows already-rendered rows; never touches each section's
// own render/refresh logic or event bindings.
// ==========================================================
function attachListFilter(inputId, listContainerId, rowSelector) {
    const input = document.getElementById(inputId);
    const container = document.getElementById(listContainerId);
    if (!input || !container) return;
    const sel = rowSelector || '.item-row';
    input.addEventListener('input', () => {
        const q = input.value.trim().toLowerCase();
        container.querySelectorAll(sel).forEach(row => {
            row.style.display = (!q || row.textContent.toLowerCase().includes(q)) ? '' : 'none';
        });
    });
}

document.addEventListener('DOMContentLoaded', () => {
    attachListFilter('courseListFilter', 'courseList');
    attachListFilter('assignmentListFilter', 'assignmentList');
    attachListFilter('aiMaterialListFilter', 'aiMaterialList');
    attachListFilter('teacherStudentDirectoryFilter', 'teacherStudentDirectoryBody', 'tr');
});
