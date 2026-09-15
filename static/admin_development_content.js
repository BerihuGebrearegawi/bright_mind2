/* V31.85: Admin panel wiring for attaching video/book media to Student and
   Parent Development Center lessons. Additive file. */
(function(){
  function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  async function authHeaders(){
    const u = window.BMTWaitForAuth ? await window.BMTWaitForAuth(10000) : ((window.auth && window.auth.currentUser) || (window.BMTAuthReady ? await window.BMTAuthReady : null));
    const t = u ? await u.getIdToken(true) : '' ;
    return {Authorization:`Bearer ${t}`};
  }
  let ITEMS=[];

  async function loadCatalog(){
    const sel=document.getElementById('devContentLessonSelect'); if(!sel)return;
    const status=document.getElementById('devContentStatus');
    try{
      const r=await fetch('/api/admin/development/catalog',{headers:await authHeaders()});
      const d=await r.json().catch(()=>({})); if(!r.ok)throw Error(d.error||'Could not load lessons.');
      ITEMS=d.items||[];
      let lastGroup='';
      sel.innerHTML=ITEMS.map(x=>{
        const optGroupOpen = x.group!==lastGroup ? (lastGroup?'</optgroup>':'')+`<optgroup label="${esc(x.group)}">` : '';
        lastGroup=x.group;
        return optGroupOpen+`<option value="${esc(x.id)}">${esc(x.title)}${x.videoUrl||x.bookUrl?' ✓':''}</option>`;
      }).join('')+'</optgroup>';
      fillFormFromSelection();
    }catch(e){ if(status) status.textContent='❌ '+e.message; }
  }

  function fillFormFromSelection(){
    const sel=document.getElementById('devContentLessonSelect'); if(!sel)return;
    const item=ITEMS.find(x=>x.id===sel.value);
    document.getElementById('devContentVideoUrl').value = item?.videoUrl || '';
    document.getElementById('devContentBookUrl').value = item?.bookUrl || '';
  }

  async function uploadIfNeeded(fileInputId, urlInputId){
    const fileInput=document.getElementById(fileInputId);
    const file=fileInput?.files?.[0];
    if(!file) return document.getElementById(urlInputId).value.trim();
    const status=document.getElementById('devContentStatus');
    if(status) status.textContent='Uploading '+file.name+'…';
    const h=await authHeaders();
    const form=new FormData(); form.append('file', file);
    const r=await fetch('/api/admin/development/upload',{method:'POST',headers:h,body:form});
    const d=await r.json().catch(()=>({})); if(!r.ok) throw Error(d.error||'Upload failed.');
    return d.url || '';
  }

  async function save(){
    const sel=document.getElementById('devContentLessonSelect');
    const status=document.getElementById('devContentStatus');
    const saveBtn=document.getElementById('devContentSaveBtn');
    const lessonId=sel?.value; if(!lessonId){ if(status) status.textContent='❌ Choose a lesson first.'; return; }
    if(saveBtn) saveBtn.disabled=true;
    try{
      const videoUrl=await uploadIfNeeded('devContentVideoFile','devContentVideoUrl');
      const bookUrl=await uploadIfNeeded('devContentBookFile','devContentBookUrl');
      const bookFile=document.getElementById('devContentBookFile')?.files?.[0];
      const h=await authHeaders(); h['Content-Type']='application/json';
      const r=await fetch('/api/admin/development/media',{method:'POST',headers:h,body:JSON.stringify({lessonId,videoUrl,bookUrl,bookName:bookFile?bookFile.name:undefined})});
      const d=await r.json().catch(()=>({})); if(!r.ok)throw Error(d.error||'Could not save.');
      if(status) status.textContent='✅ Saved. Students/parents will see this the next time they open the lesson.';
      await loadCatalog();
      sel.value=lessonId; fillFormFromSelection();
    }catch(e){ if(status) status.textContent='❌ '+e.message; }
    finally{ if(saveBtn) saveBtn.disabled=false; }
  }

  function init(){
    document.getElementById('devContentRefreshBtn')?.addEventListener('click',loadCatalog);
    document.getElementById('devContentLessonSelect')?.addEventListener('change',fillFormFromSelection);
    document.getElementById('devContentSaveBtn')?.addEventListener('click',save);
    if(document.getElementById('devContentLessonSelect')) loadCatalog();
  }
  window.BMTDevelopmentContentAdmin={init};
  function initWhenAuthReady(){ if(window.auth?.currentUser) init(); else window.addEventListener('BMTAuthReady',init,{once:true}); }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',initWhenAuthReady);else initWhenAuthReady();
})();
