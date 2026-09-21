/* V31.86: Admin panel wiring for the Question Bank and the regional
   Academic Challenge & Scholarship Manager (Mathematics & Aptitude
   competitions, grade-level, region-level, up to scholarship awards).
   Additive file - reuses existing server endpoints in
   learning_challenge_routes.py, awards_routes.py, award_ledger_routes.py. */
(function(){
  function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  async function authHeaders(json){
    const u = window.BMTWaitForAuth ? await window.BMTWaitForAuth(10000) : ((window.auth && window.auth.currentUser) || (window.BMTAuthReady ? await window.BMTAuthReady : null));
    const t = u ? await u.getIdToken(true) : '';
    const h = {Authorization:`Bearer ${t}`};
    if(json) h['Content-Type']='application/json';
    return h;
  }
  function toIso(localValue){ return localValue ? new Date(localValue).toISOString() : null; }
  function selected(id){ return Array.from(document.getElementById(id)?.selectedOptions||[]).map(o=>o.value); }

  // ---------- Region dropdown (populated from server's region list) ----------
  async function loadRegions(){
    const sel=document.getElementById('chRegion'); if(!sel)return;
    try{
      const r=await fetch('/api/student/region',{headers:await authHeaders()});
      const d=await r.json().catch(()=>({}));
      (d.regions||[]).forEach(reg=>{ const o=document.createElement('option'); o.value=reg; o.textContent=reg; sel.appendChild(o); });
    }catch(_){}
  }

  // ---------- Question Bank ----------
  async function addQuestion(){
    const status=document.getElementById('qbStatus');
    const body={
      question: document.getElementById('qbQuestion').value.trim(),
      options: {A:document.getElementById('qbOptA').value.trim(), B:document.getElementById('qbOptB').value.trim(), C:document.getElementById('qbOptC').value.trim(), D:document.getElementById('qbOptD').value.trim()},
      correctAnswer: document.getElementById('qbCorrect').value,
      subject: document.getElementById('qbSubject').value,
      grade: document.getElementById('qbGrade').value.trim(),
      learningModes: selected('qbLearningMode'),
      audiences: selected('qbAudience'),
    };
    if(status) status.textContent='Saving…';
    try{
      const r=await fetch('/api/question-bank',{method:'POST',headers:await authHeaders(true),body:JSON.stringify(body)});
      const d=await r.json().catch(()=>({})); if(!r.ok) throw Error(d.error||'Could not add question.');
      if(status) status.textContent=`✅ Added as draft (id: ${d.questionId}). Approve it below to use it in a challenge.`;
      document.getElementById('qbQuestion').value='';
      ['qbOptA','qbOptB','qbOptC','qbOptD'].forEach(id=>document.getElementById(id).value='');
      loadPendingQuestions();
    }catch(e){ if(status) status.textContent='❌ '+e.message; }
  }

  async function loadPendingQuestions(){
    const box=document.getElementById('qbPendingList'); if(!box)return;
    box.innerHTML='<div class="spinner"></div> Loading…';
    try{
      const r=await fetch('/api/question-bank?status=draft',{headers:await authHeaders()});
      const d=await r.json().catch(()=>({})); if(!r.ok) throw Error(d.error||'Could not load questions.');
      const rows=d.questions||[];
      box.innerHTML=rows.length?rows.map(q=>`<div class="item-row"><div class="item-content"><strong>${esc(q.id)}</strong> — ${esc(q.question)}<div class="subtitle">${esc(q.subject||'')} · Grade ${esc(q.grade||'-')} · Mode: ${esc((q.learningModes||[]).join(', '))} · Audience: ${esc((q.audiences||[]).join(', '))} · Correct: ${esc(q.correctAnswer||'')}</div></div><div class="item-actions"><button class="btn btn-success" data-approve-q="${esc(q.id)}">✅ Approve</button></div></div>`).join(''):'<p class="subtitle">No draft questions pending.</p>';
      box.querySelectorAll('[data-approve-q]').forEach(b=>b.addEventListener('click',async()=>{
        try{ const r=await fetch(`/api/question-bank/${b.dataset.approveQ}/approve`,{method:'POST',headers:await authHeaders()}); const d=await r.json().catch(()=>({})); if(!r.ok)throw Error(d.error||'Could not approve.'); loadPendingQuestions(); }catch(e){ alert(e.message); }
      }));
    }catch(e){ box.innerHTML=`<p class="error-text">${esc(e.message)}</p>`; }
  }

  // ---------- Challenge Manager ----------
  async function createChallenge(){
    const status=document.getElementById('chCreateStatus');
    const qIds=document.getElementById('chQuestionIds').value.split(',').map(s=>s.trim()).filter(Boolean);
    const body={
      title: document.getElementById('chTitle').value.trim(),
      subject: document.getElementById('chSubject').value,
      grade: document.getElementById('chGrade').value.trim(),
      region: document.getElementById('chRegion').value,
      zone: document.getElementById('chZone').value.trim(),
      woreda: document.getElementById('chWoreda').value.trim(),
      roundNumber: Number(document.getElementById('chRound').value||1),
      qualificationCount: Number(document.getElementById('chQualCount').value||0),
      durationMinutes: Number(document.getElementById('chDuration').value||20),
      season: document.getElementById('chSeason').value.trim(),
      isScholarshipChallenge: document.getElementById('chIsScholarship').checked,
      startsAt: toIso(document.getElementById('chStart').value),
      endsAt: toIso(document.getElementById('chEnd').value),
      questionIds: qIds,
      learningModes: selected('chLearningMode'),
      audiences: selected('chAudience'),
    };
    if(status) status.textContent='Saving…';
    try{
      const r=await fetch('/api/teacher/challenges',{method:'POST',headers:await authHeaders(true),body:JSON.stringify(body)});
      const d=await r.json().catch(()=>({})); if(!r.ok) throw Error(d.error||'Could not create challenge.');
      if(status) status.textContent=`✅ Created as draft (id: ${d.challengeId}). Publish it below when ready.`;
      loadChallenges();
    }catch(e){ if(status) status.textContent='❌ '+e.message; }
  }

  async function loadChallenges(){
    const box=document.getElementById('chList'); if(!box)return;
    box.innerHTML='<div class="spinner"></div> Loading…';
    try{
      const r=await fetch('/api/teacher/challenges',{headers:await authHeaders()});
      const d=await r.json().catch(()=>({})); if(!r.ok) throw Error(d.error||'Could not load challenges.');
      const rows=d.challenges||[];
      box.innerHTML=rows.length?rows.map(c=>`
        <div class="card" style="margin-bottom:10px">
          <strong>${esc(c.title)}</strong> ${c.isScholarshipChallenge?'🎓':''}
          <div class="subtitle">${esc(c.subject||'')} · Grade ${esc(c.grade||'-')} · Mode: ${esc((c.learningModes||[]).join(', '))} · Audience: ${esc((c.audiences||[]).join(', '))} · Region: ${esc(c.region||'ALL')}${c.zone?' · Zone: '+esc(c.zone):''}${c.woreda?' · Woreda: '+esc(c.woreda):''} · Round ${esc(c.roundNumber)} · ${esc(c.questionCount)} questions · Status: <strong>${esc(c.status)}</strong> · Entry fee: <strong>${c.entryFee>0?esc(c.entryFee)+' '+esc(c.entryCurrency||'ETB'):'Free'}</strong></div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px">
            <button class="btn btn-outline" data-set-fee="${esc(c.id)}" data-current-fee="${esc(c.entryFee||0)}" data-current-currency="${esc(c.entryCurrency||'ETB')}">💰 Set Entry Fee</button>
            ${c.status==='draft'?`<button class="btn btn-success" data-publish="${esc(c.id)}">📢 Publish</button>`:''}
            ${c.status==='published'?`<button class="btn btn-outline" data-close="${esc(c.id)}">⏹ Close</button>`:''}
            ${c.status==='closed'||c.status==='published'?`<button class="btn btn-outline" data-leaderboard="${esc(c.id)}">📊 Leaderboard</button>`:''}
            <button class="btn btn-outline" data-participants="${esc(c.id)}">👥 Participants</button>
            <button class="btn btn-outline" data-analytics="${esc(c.id)}">📈 Analytics</button>
            ${c.status==='closed'?`<button class="btn btn-outline" data-policy="${esc(c.id)}">🏅 Award Policy</button><button class="btn btn-primary" data-finalize="${esc(c.id)}">💰 Finalize Awards</button><button class="btn btn-primary" data-winners="${esc(c.id)}">📣 Publish Winners</button><button class="btn btn-outline" data-certificates="${esc(c.id)}">🎓 Certificates</button>`:''}
          </div>
          <div id="chDetail-${esc(c.id)}" style="margin-top:8px"></div>
        </div>`).join(''):'<p class="subtitle">No challenges yet. Create one above.</p>';
      wireChallengeActions(box);
    }catch(e){ box.innerHTML=`<p class="error-text">${esc(e.message)}</p>`; }
  }

  function wireChallengeActions(box){
    box.querySelectorAll('[data-set-fee]').forEach(b=>b.addEventListener('click',async()=>{
      const id=b.dataset.setFee;
      const currentFee=b.dataset.currentFee||'0', currentCurrency=b.dataset.currentCurrency||'ETB';
      const feeInput=prompt(`Entry fee amount (0 = free) for this challenge:`, currentFee);
      if(feeInput===null) return;
      const fee=Number(feeInput);
      if(!Number.isFinite(fee)||fee<0){ alert('Enter a non-negative number.'); return; }
      const currencyInput=prompt('Currency code (e.g. ETB):', currentCurrency) || currentCurrency;
      try{
        const r=await fetch(`/api/admin/challenges/${id}/entry-fee`,{method:'POST',headers:await authHeaders(true),body:JSON.stringify({entryFee:fee, entryCurrency:currencyInput})});
        const d=await r.json().catch(()=>({})); if(!r.ok) throw Error(d.error||'Could not set entry fee.');
        loadChallenges();
      }catch(e){ alert(e.message); }
    }));
    box.querySelectorAll('[data-publish]').forEach(b=>b.addEventListener('click',async()=>{
      try{ const r=await fetch(`/api/teacher/challenges/${b.dataset.publish}/publish`,{method:'POST',headers:await authHeaders()}); const d=await r.json().catch(()=>({})); if(!r.ok)throw Error(d.error||'Could not publish.'); loadChallenges(); }catch(e){ alert(e.message); }
    }));
    box.querySelectorAll('[data-close]').forEach(b=>b.addEventListener('click',async()=>{
      if(!confirm('Close this challenge? Students will no longer be able to submit attempts.')) return;
      try{ const r=await fetch(`/api/admin/challenges/${b.dataset.close}/close`,{method:'POST',headers:await authHeaders()}); const d=await r.json().catch(()=>({})); if(!r.ok)throw Error(d.error||'Could not close.'); loadChallenges(); }catch(e){ alert(e.message); }
    }));
    box.querySelectorAll('[data-leaderboard]').forEach(b=>b.addEventListener('click',async()=>{
      const id=b.dataset.leaderboard; const out=document.getElementById('chDetail-'+id);
      out.innerHTML='<div class="spinner"></div>';
      try{ const r=await fetch(`/api/challenges/${id}/leaderboard`,{headers:await authHeaders()}); const d=await r.json().catch(()=>({})); if(!r.ok)throw Error(d.error||'Could not load leaderboard.');
        const rows=d.leaderboard||d.rows||[];
        out.innerHTML=rows.length?('<table style="width:100%;font-size:13px"><tr><th>#</th><th>Score</th></tr>'+rows.map((x,i)=>`<tr><td>${i+1}</td><td>${esc(x.percentage!=null?x.percentage+'%':x.score)}</td></tr>`).join('')+'</table>'):'<p class="subtitle">No submissions yet.</p>';
      }catch(e){ out.innerHTML=`<p class="error-text">${esc(e.message)}</p>`; }
    }));
    box.querySelectorAll('[data-participants]').forEach(b=>b.addEventListener('click',async()=>{
      const id=b.dataset.participants; const out=document.getElementById('chDetail-'+id);
      out.innerHTML='<div class="spinner"></div>';
      try{ const r=await fetch(`/api/admin/challenges/${id}/participants`,{headers:await authHeaders()}); const d=await r.json().catch(()=>({})); if(!r.ok)throw Error(d.error||'Could not load participants.');
        const rows=d.participants||[]; const by=d.byAudience||{};
        const summary=Object.keys(by).map(k=>`${esc(k)}: ${esc(by[k])}`).join(' · ');
        out.innerHTML=`<div class="subtitle"><strong>${esc(d.participantCount||0)} participants</strong> — ${summary}</div>`+
          (rows.length?('<table style="width:100%;font-size:13px;margin-top:6px"><tr><th>Name</th><th>Audience</th><th>Status</th><th>%</th></tr>'+rows.map(p=>`<tr><td>${esc(p.displayName)}</td><td>${esc((p.audiences||[]).join(', '))}</td><td>${esc(p.attemptStatus)}${p.result?' ('+esc(p.result)+')':''}</td><td>${esc(p.percentage)}</td></tr>`).join('')+'</table>'):'<p class="subtitle">No participants yet.</p>');
      }catch(e){ out.innerHTML=`<p class="error-text">${esc(e.message)}</p>`; }
    }));
    box.querySelectorAll('[data-analytics]').forEach(b=>b.addEventListener('click',async()=>{
      const id=b.dataset.analytics; const out=document.getElementById('chDetail-'+id);
      out.innerHTML='<div class="spinner"></div>';
      try{ const r=await fetch(`/api/admin/challenges/${id}/analytics`,{headers:await authHeaders()}); const d=await r.json().catch(()=>({})); if(!r.ok)throw Error(d.error||'Could not load analytics.');
        const by=d.byAudience||{}; const summary=Object.keys(by).map(k=>`${esc(k)}: ${esc(by[k])}`).join(' · ');
        out.innerHTML=`<div class="subtitle">Started: <strong>${esc(d.startedCount)}</strong> · Submitted: <strong>${esc(d.submittedCount)}</strong> (${esc(d.completionRate)}%) · Avg: <strong>${esc(d.averagePercentage)}%</strong> · Pass rate: <strong>${esc(d.passRate)}%</strong><br>${summary}<br>Certificates issued: <strong>${esc(d.certificatesIssued)}</strong> (revoked: ${esc(d.certificatesRevoked)})</div>`;
      }catch(e){ out.innerHTML=`<p class="error-text">${esc(e.message)}</p>`; }
    }));
    box.querySelectorAll('[data-certificates]').forEach(b=>b.addEventListener('click',async()=>{
      const id=b.dataset.certificates; const out=document.getElementById('chDetail-'+id);
      out.innerHTML='<div class="spinner"></div>';
      try{ const r=await fetch(`/api/admin/challenges/${id}/certificates`,{headers:await authHeaders()}); const d=await r.json().catch(()=>({})); if(!r.ok)throw Error(d.error||'Could not load certificates.');
        const rows=d.certificates||[];
        out.innerHTML=rows.length?('<table style="width:100%;font-size:13px"><tr><th>Rank</th><th>Name</th><th>Certificate ID</th><th>Status</th></tr>'+rows.map(c2=>`<tr><td>${esc(c2.rank)}</td><td>${esc(c2.displayName)}</td><td>${esc(c2.certificateId)}</td><td>${esc(c2.status)}</td></tr>`).join('')+'</table>'):'<p class="subtitle">No certificates issued yet.</p>';
      }catch(e){ out.innerHTML=`<p class="error-text">${esc(e.message)}</p>`; }
    }));
    box.querySelectorAll('[data-policy]').forEach(b=>b.addEventListener('click',()=>{
      const id=b.dataset.policy; const out=document.getElementById('chDetail-'+id);
      out.innerHTML=`<div style="display:grid;gap:6px;max-width:320px">
        <label style="font-size:12px;font-weight:700">Cash awards (top N)</label><input type="number" min="0" value="3" id="pol-count-${id}">
        <label style="font-size:12px;font-weight:700">Prize pool (total, ETB)</label><input type="number" min="0" value="0" id="pol-pool-${id}">
        <label style="font-size:12px;font-weight:700">Scholarship awards (top N)</label><input type="number" min="0" value="0" id="pol-schol-${id}">
        <label style="font-size:12px;font-weight:700">Scholarship label</label><input type="text" placeholder="e.g. Full BMT Scholarship" id="pol-label-${id}">
        <label style="font-size:12px;font-weight:700"><input type="checkbox" id="pol-cert-${id}" checked> Issue certificates</label>
        <button class="btn btn-primary" id="pol-save-${id}" style="width:fit-content">💾 Save Policy</button>
        <div class="subtitle" id="pol-status-${id}"></div>
      </div>`;
      document.getElementById(`pol-save-${id}`).addEventListener('click',async()=>{
        const st=document.getElementById(`pol-status-${id}`); st.textContent='Saving…';
        try{
          const body={awardCount:Number(document.getElementById(`pol-count-${id}`).value||0), prizePoolShare:Number(document.getElementById(`pol-pool-${id}`).value||0), scholarshipCount:Number(document.getElementById(`pol-schol-${id}`).value||0), scholarshipLabel:document.getElementById(`pol-label-${id}`).value.trim(), certificateEnabled:document.getElementById(`pol-cert-${id}`).checked};
          const r=await fetch(`/api/admin/challenges/${id}/award-policy`,{method:'POST',headers:await authHeaders(true),body:JSON.stringify(body)});
          const d=await r.json().catch(()=>({})); if(!r.ok)throw Error(d.error||'Could not save policy.');
          st.textContent='✅ Saved.';
        }catch(e){ st.textContent='❌ '+e.message; }
      });
    }));
    box.querySelectorAll('[data-finalize]').forEach(b=>b.addEventListener('click',async()=>{
      if(!confirm('Finalize awards for this challenge? This locks in the winners and prize amounts.')) return;
      try{ const r=await fetch(`/api/admin/challenges/${b.dataset.finalize}/finalize-awards`,{method:'POST',headers:await authHeaders()}); const d=await r.json().catch(()=>({})); if(!r.ok)throw Error(d.error||'Could not finalize.'); alert('Awards finalized.'); }catch(e){ alert(e.message); }
    }));
    box.querySelectorAll('[data-winners]').forEach(b=>b.addEventListener('click',async()=>{
      try{ const r=await fetch(`/api/admin/challenges/${b.dataset.winners}/publish-winners`,{method:'POST',headers:await authHeaders()}); const d=await r.json().catch(()=>({})); if(!r.ok)throw Error(d.error||'Could not publish winners.'); alert('Winners published.'); }catch(e){ alert(e.message); }
    }));
  }

  function init(){
    if(!document.getElementById('challengeManagerSection')) return;
    loadRegions();
    document.getElementById('qbAddBtn')?.addEventListener('click',addQuestion);
    document.getElementById('qbRefreshBtn')?.addEventListener('click',loadPendingQuestions);
    document.getElementById('chCreateBtn')?.addEventListener('click',createChallenge);
    document.getElementById('chRefreshBtn')?.addEventListener('click',loadChallenges);
    loadPendingQuestions();
    loadChallenges();
  }
  window.BMTChallengeManagerAdmin={init};
  function initWhenAuthReady(){ if(window.auth?.currentUser) init(); else window.addEventListener('BMTAuthReady',init,{once:true}); }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',initWhenAuthReady);else initWhenAuthReady();
})();
