(function(){
  const $=id=>document.getElementById(id);
  async function token(){ const u=window.BMTWaitForAuth ? await window.BMTWaitForAuth(10000) : (window.auth?.currentUser || (window.BMTAuthReady ? await window.BMTAuthReady : null)); return u?u.getIdToken(true):''; }
  async function api(url,opts={}){ const t=await token(); opts.headers={...(opts.headers||{}),Authorization:`Bearer ${t}`,'Content-Type':'application/json'}; const r=await fetch(url,opts); const d=await r.json().catch(()=>({})); if(!r.ok) throw new Error(d.error||`HTTP ${r.status}`); return d; }
  async function load(){ try{ const d=await api('/api/admin/telegram/targets'); $('tgTargetList').innerHTML=(d.targets||[]).map(x=>`<label style="display:block"><input type="checkbox" value="${x.chatId}"> ${x.name} (${x.kind})</label>`).join('')||'No targets configured.'; }catch(e){$('tgStatus').textContent=e.message;} }
  async function smartApply(){
    const grade=$('tgSmartGrade').value.trim(); const purpose=$('tgSmartPurpose').value;
    try{
      const d=await api(`/api/admin/telegram/smart-targets?grade=${encodeURIComponent(grade)}&purpose=${encodeURIComponent(purpose)}`);
      const chatIds=new Set((d.targets||[]).map(x=>String(x.chatId)));
      document.querySelectorAll('#tgTargetList input[type=checkbox]').forEach(cb=>{ cb.checked=chatIds.has(cb.value); });
      $('tgStatus').textContent=`✨ Auto-selected ${chatIds.size} target(s) for ${purpose}${grade?' · Grade '+grade:''}.`;
    }catch(e){ $('tgStatus').textContent='❌ '+e.message; }
  }
  async function publish(){
    const targets=[...document.querySelectorAll('#tgTargetList input:checked')].map(x=>x.value);
    const type=$('tgType').value;
    let mediaUrl=$('tgMediaUrl').value;
    const file=$('tgMediaFile')?.files?.[0];
    try{
      if(file){
        $('tgStatus').textContent='Uploading media…';
        const resourceType = type==='photo' ? 'image' : type==='video' ? 'video' : type==='document' ? 'raw' : 'auto';
        const result = await (await import('./storage-service.js')).uploadAdminMedia(file, resourceType);
        if(!result.success) throw new Error(result.error || 'Media upload failed.');
        mediaUrl = result.url;
        if($('tgMediaUrl')) $('tgMediaUrl').value = mediaUrl;
      }
      const body={type,targetChatIds:targets,text:$('tgText').value,caption:$('tgCaption').value,mediaUrl};
      const d=await api('/api/admin/telegram/publish',{method:'POST',body:JSON.stringify(body)});
      $('tgStatus').textContent=`Published: ${d.results.filter(x=>x.ok).length}/${targets.length}`;
      if(file) $('tgMediaFile').value='';
    }catch(e){$('tgStatus').textContent=e.message;}
  }
  function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  async function loadAnalytics(){
    const box=$('tgAnalyticsSummary'); if(!box)return; box.innerHTML='<div class="spinner"></div> Loading…';
    try{ const d=await api('/api/admin/telegram/analytics'); box.innerHTML=`<div class="item-row"><div>📨 Posts: <strong>${d.posts}</strong></div><div>✅ Sent: <strong>${d.postsSent}</strong></div><div>❌ Failed: <strong>${d.postsFailed}</strong></div><div>🛡️ Moderation actions: <strong>${d.moderationActions}</strong> (${d.moderationDeleted} deleted)</div></div>`; }
    catch(e){ box.innerHTML=`<p class="error-text">${esc(e.message)}</p>`; }
  }
  async function loadModerationLogs(){
    const box=$('tgModerationLogList'); if(!box)return; box.innerHTML='<div class="spinner"></div> Loading…';
    try{ const d=await api('/api/admin/telegram/moderation-logs'); const rows=d.logs||[];
      box.innerHTML=rows.length?rows.map(x=>`<div class="item-row"><div class="item-content">Chat ${esc(x.chatId)} · User ${esc(x.userId)} · <strong>${esc(x.action)}</strong>${x.reason?' — '+esc(x.reason):''}<div class="subtitle">${esc(x.createdAt||'')}</div></div></div>`).join(''):'<p class="subtitle">No moderation activity yet.</p>';
    }catch(e){ box.innerHTML=`<p class="error-text">${esc(e.message)}</p>`; }
  }
  async function loadMediaLibrary(){
    const box=$('tgMediaLibraryList'); if(!box)return; box.innerHTML='<div class="spinner"></div> Loading…';
    try{ const d=await api('/api/admin/telegram/media-library'); const rows=d.media||[];
      box.innerHTML=rows.length?rows.map(x=>`<div class="item-row"><div class="item-content">${esc(x.type)} — ${esc(x.caption||'(no caption)')}<div class="subtitle">${esc(x.createdAt||'')}</div></div><div class="item-actions"><button class="btn btn-outline" data-reuse-media="${esc(x.mediaUrl)}">↺ Reuse</button></div></div>`).join(''):'<p class="subtitle">No media published yet.</p>';
      box.querySelectorAll('[data-reuse-media]').forEach(b=>b.addEventListener('click',()=>{ if($('tgMediaUrl')) $('tgMediaUrl').value=b.dataset.reuseMedia; }));
    }catch(e){ box.innerHTML=`<p class="error-text">${esc(e.message)}</p>`; }
  }
  async function loadContentCalendar(){
    const box=$('tgContentCalendar'); if(!box)return; box.innerHTML='<div class="spinner"></div> Loading…';
    try{ const d=await api('/api/admin/telegram/calendar'); const days=d.days||[];
      box.innerHTML=days.length?days.map(day=>`<div style="margin-bottom:8px"><strong>${esc(day.date)}</strong>${day.items.map(it=>`<div class="item-row"><div class="item-content">${esc(it.time.slice(11,16))} — ${esc(it.type)}: ${esc(it.preview)}<div class="subtitle">${esc(it.status)} · ${it.targetCount} target(s)</div></div></div>`).join('')}</div>`).join(''):'<p class="subtitle">Nothing scheduled yet.</p>';
    }catch(e){ box.innerHTML=`<p class="error-text">${esc(e.message)}</p>`; }
  }
  window.BMTTelegramAdmin={init(){ if($('telegramPlatformSection')){ $('tgRefresh').onclick=load; $('tgPublish').onclick=publish; $('tgSmartApplyBtn')?.addEventListener('click',smartApply); load(); } if($('telegramAnalyticsSection')){ $('tgAnalyticsRefreshBtn').onclick=()=>{loadAnalytics();loadModerationLogs();loadMediaLibrary();loadContentCalendar();}; loadAnalytics(); loadModerationLogs(); loadMediaLibrary(); loadContentCalendar(); } }};
  document.addEventListener('DOMContentLoaded',()=>{const run=()=>window.BMTTelegramAdmin.init();if(window.auth?.currentUser)run();else window.addEventListener('BMTAuthReady',run,{once:true});});
})();
