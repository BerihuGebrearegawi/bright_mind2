/* BMT dashboard integrations: Digital Library + Telegram admin controls. */
(function(){
  function esc(v){return String(v??'').replace(/[&<>\"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[m]));}
  async function authHeaders(){
    const u = window.BMTWaitForAuth ? await window.BMTWaitForAuth(10000) : (window.auth?.currentUser || (window.BMTAuthReady ? await window.BMTAuthReady : null));
    if(!u) throw new Error('Please sign in again.');
    return {Authorization:`Bearer ${await u.getIdToken(true)}`};
  }
  async function loadLibrary(){
    const box=document.getElementById('dashboardLibraryList'); if(!box)return;
    box.innerHTML='<div class="spinner"></div> Loading Digital Library…';
    try{
      const h=await authHeaders();
      const grade=document.getElementById('dashboardLibraryGrade')?.value||'';
      const cat=document.getElementById('dashboardLibraryCategory')?.value||'';
      const subject=document.getElementById('dashboardLibrarySubject')?.value.trim()||'';
      const q=new URLSearchParams({className:grade,category:cat,subject});
      const r=await fetch('/api/library?'+q,{headers:h});
      const d=await r.json().catch(()=>({})); if(!r.ok)throw Error(d.error||'Could not load Digital Library.');
      const rows=d.items||[];
      if(!rows.length){box.innerHTML='<div class="subtitle">No library items matched your search.</div>';return;}
      box.innerHTML=rows.map(x=>`<div class="item-row" style="display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap"><div><strong>${esc(x.title)}</strong><div class="subtitle">${esc(x.categoryLabel||x.category)} · Grade ${esc(x.grade||'All')} · ${esc(x.subject||'General')}</div>${x.description?`<div style="margin-top:4px;font-size:12px">${esc(x.description)}</div>`:''}</div>${x.url?`<a class="btn btn-primary" target="_blank" rel="noopener noreferrer" href="${esc(x.url)}">Open / Read</a>`:'<span class="badge">In-app item</span>'}</div>`).join('');
    }catch(e){box.innerHTML=`<div class="error-text">${esc(e.message)}</div>`;}
  }
  async function loadTelegramStatus(){
    const box=document.getElementById('telegramAdminStatus'); if(!box)return;
    box.innerHTML='<div class="spinner"></div> Checking Telegram bot…';
    try{
      const r=await fetch('/api/admin/telegram/status',{headers:await authHeaders()});
      const d=await r.json().catch(()=>({})); if(!r.ok)throw Error(d.error||'Could not load Telegram status.');
      box.innerHTML=`<div class="item-row"><strong>${d.configured?'🟢 Bot configured':'🟠 Bot not configured'}</strong><div class="subtitle">${esc(d.botName||d.username||'No bot identity available')} · Webhook: ${d.webhookConfigured?'configured':'not configured'}</div></div>`;
      const url=document.getElementById('telegramWebhookUrl'); if(url&&d.webhookUrl)url.value=d.webhookUrl;
    }catch(e){box.innerHTML=`<div class="error-text">${esc(e.message)}</div>`;}
  }
  async function setTelegramWebhook(){
    const status=document.getElementById('telegramAdminActionStatus'); if(status)status.textContent='Setting webhook…';
    try{
      const h=await authHeaders(); h['Content-Type']='application/json';
      const url=document.getElementById('telegramWebhookUrl')?.value.trim();
      const r=await fetch('/api/admin/telegram/set-webhook',{method:'POST',headers:h,body:JSON.stringify({url})});
      const d=await r.json().catch(()=>({})); if(!r.ok)throw Error(d.error||'Could not set Telegram webhook.');
      if(status)status.textContent='✅ Telegram webhook configured successfully.';
      loadTelegramStatus();
    }catch(e){if(status)status.textContent='❌ '+e.message;}
  }
  function init(){
    document.getElementById('dashboardLibrarySearchBtn')?.addEventListener('click',loadLibrary);
    document.getElementById('dashboardLibrarySubject')?.addEventListener('keydown',e=>{if(e.key==='Enter')loadLibrary();});
    document.getElementById('telegramRefreshBtn')?.addEventListener('click',loadTelegramStatus);
    document.getElementById('telegramSetWebhookBtn')?.addEventListener('click',setTelegramWebhook);
    const loadProtected=()=>{
      if(document.getElementById('dashboardLibraryList')) loadLibrary();
      if(document.getElementById('telegramAdminStatus')) loadTelegramStatus();
    };
    if(window.auth?.currentUser) loadProtected();
    else window.addEventListener('BMTAuthReady',loadProtected,{once:true});
  }
  window.BMTDashboardIntegrations={init,loadLibrary,loadTelegramStatus};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
