/* BMT Smart Quiz Scanner UI. Authentication is supplied by each dashboard module. */
(function () {
  function esc(x){return String(x??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));}
  function cfg(){return window.BMT_SCANNER_CONFIG||{};}
  function token(){const c=cfg(); if(typeof c.getToken!=='function') throw Error('Scanner authentication is not ready.'); return c.getToken();}
  async function api(path, method, body){
    const t=await token();
    const opts={method:method||'GET',headers:{Authorization:`Bearer ${t}`}};
    if(body!==undefined){opts.headers['Content-Type']='application/json';opts.body=JSON.stringify(body);}
    const r=await fetch(path,opts); const d=await r.json().catch(()=>({}));
    if(!r.ok)throw Error(d.error||`Request failed (${r.status})`); return d;
  }
  function status(msg, ok){const e=document.getElementById('smartScannerStatus'); if(e){e.textContent=msg||'';e.className=ok?'subtitle success-text':'subtitle';}}
  function renderQuestions(questions){
    const box=document.getElementById('smartScannerQuestions'); if(!box)return;
    if(!questions?.length){box.innerHTML='<p class="subtitle">No questions were extracted.</p>';return;}
    box.innerHTML=questions.map((q,i)=>`<div class="card" data-page="${esc(q.pageNumber??'')}" data-question-card="${i}" style="margin:10px 0;padding:12px;border:1px solid var(--border)">
      <div style="display:flex;justify-content:space-between;gap:8px"><strong>Question ${i+1}</strong><span class="subtitle">Page ${esc(q.pageNumber??'—')}</span></div>
      <textarea data-sq="question" data-i="${i}" rows="3" style="width:100%;margin-top:7px">${esc(q.question)}</textarea>
      <div class="subtitle" data-sq-preview="question" data-i="${i}" style="margin-top:4px;padding:6px 8px;background:var(--bg-soft,rgba(0,0,0,.03));border-radius:6px;min-height:1.4em">${esc(q.question)}</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-top:7px">${['A','B','C','D'].map(k=>`<input data-sq="opt${k}" data-i="${i}" value="${esc(q.options?.[k]||'')}" placeholder="Option ${k}">`).join('')}</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-top:7px"><select data-sq="answer" data-i="${i}"><option value="">Correct answer not extracted</option>${['A','B','C','D'].map(k=>`<option value="${k}" ${q.correctAnswer===k?'selected':''}>Correct: ${k}</option>`).join('')}</select><input data-sq="objective" data-i="${i}" value="${esc(q.learningObjective||'')}" placeholder="Learning objective (optional)"></div>
      <input data-sq="sourceRef" data-i="${i}" value="${esc(q.sourceRef||'')}" placeholder="Source reference (optional)" style="width:100%;margin-top:7px">
    </div>`).join('');
    // Live math preview: as the teacher edits the raw LaTeX in the textarea,
    // mirror it into a read-only div so $...$ formulas render via KaTeX
    // (window.bmtRenderMath, loaded from static/math-render.js).
    box.querySelectorAll('textarea[data-sq="question"]').forEach(ta=>{
      const preview=box.querySelector(`[data-sq-preview="question"][data-i="${ta.dataset.i}"]`);
      if(!preview)return;
      ta.addEventListener('input',()=>{preview.textContent=ta.value;window.bmtRenderMath?.(preview);});
    });
    window.bmtRenderMath?.(box);
  }
  function collect(){
    const box=document.getElementById('smartScannerQuestions'); if(!box)return [];
    const count=box.querySelectorAll('[data-sq="question"]').length;
    return Array.from({length:count},(_,i)=>({
      question:box.querySelector(`[data-sq="question"][data-i="${i}"]`)?.value||'',
      options:Object.fromEntries(['A','B','C','D'].map(k=>[k,box.querySelector(`[data-sq="opt${k}"][data-i="${i}"]`)?.value||''])),
      correctAnswer:box.querySelector(`[data-sq="answer"][data-i="${i}"]`)?.value||'',
      learningObjective:box.querySelector(`[data-sq="objective"][data-i="${i}"]`)?.value||'',
      sourceRef:box.querySelector(`[data-sq="sourceRef"][data-i="${i}"]`)?.value||'',
      pageNumber:box.querySelector(`.card[data-page][data-question-card="${i}"]`)?.dataset.page || null
    }));
  }
  async function scan(){
    const file=document.getElementById('smartScannerFile')?.files?.[0];
    if(!file)return status('Choose an image or PDF first.');
    const fd=new FormData(); fd.append('file',file);
    ['collection','bookId','chapterId','subchapterId','learningObjective','pageNumber','sourceRef'].forEach(k=>{const e=document.getElementById('smartScanner_'+k);if(e&&e.value.trim())fd.append(k,e.value.trim());});
    try{
      status('Uploading scan and extracting questions…');
      const t=await token(); const r=await fetch('/api/scanner/scan',{method:'POST',headers:{Authorization:`Bearer ${t}`},body:fd});
      const d=await r.json().catch(()=>({})); if(!r.ok)throw Error(d.error||`Scan failed (${r.status})`);
      document.getElementById('smartScannerId').value=d.sourceScanId||''; renderQuestions(d.questions||[]); status(`Extracted ${d.questionCount||0} question(s). Review them before import.` ,true);
    }catch(e){status(e.message||'Scanner failed.');}
  }
  async function review(){
    const id=document.getElementById('smartScannerId')?.value.trim(); if(!id)return status('Scan the source first.');
    try{status('Saving review…');const d=await api(`/api/scanner/scan/${encodeURIComponent(id)}/review`,'PUT',{questions:collect()});renderQuestions(d.questions||[]);status('Review saved. You can now import after setting all correct answers.',true);}catch(e){status(e.message||'Review failed.');}
  }
  async function importQuestions(){
    const id=document.getElementById('smartScannerId')?.value.trim(); if(!id)return status('Scan the source first.');
    const destination=document.getElementById('smartScanner_destination')?.value||'classwork';
    const payload={titlePrefix:document.getElementById('smartScanner_title')?.value.trim()||'Scanned Quiz', destination};
    if(destination==='questionBank'){
      payload.subject=document.getElementById('smartScanner_qbSubject')?.value.trim()||'';
      payload.grade=document.getElementById('smartScanner_qbGrade')?.value.trim()||'';
    }else{
      const cls=document.getElementById('smartScanner_class')?.value||'';
      if(!cls)return status('Choose a class.');
      payload.className=cls;
    }
    try{
      status('Importing reviewed questions…');
      const d=await api(`/api/scanner/scan/${encodeURIComponent(id)}/import`,'POST',payload);
      status(destination==='questionBank'
        ? `Imported ${d.quizCount||0} question(s) into the Question Bank as drafts — approve them there before use.`
        : `Imported ${d.quizCount||0} question(s) into the existing quiz system.`, true);
    }catch(e){status(e.message||'Import failed.');}
  }
  let initialized=false;
  function init(){
    if(initialized)return; initialized=true;
    document.getElementById('smartScannerScanBtn')?.addEventListener('click',scan);
    document.getElementById('smartScannerReviewBtn')?.addEventListener('click',review);
    document.getElementById('smartScannerImportBtn')?.addEventListener('click',importQuestions);
  }
  window.BMTScannerUI={init};
})();
