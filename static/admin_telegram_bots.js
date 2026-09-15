/* V31.84: Admin panel wiring for the Moderator and Publisher Telegram bots.
   Mirrors dashboard_integrations.js's Academic bot pattern, parameterized so
   both bots share one implementation. Additive file - does not modify the
   existing Academic bot wiring. */
(function(){
  function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  async function authHeaders(){
    const u = window.BMTWaitForAuth ? await window.BMTWaitForAuth(10000) : ((window.auth && window.auth.currentUser) || (window.BMTAuthReady ? await window.BMTAuthReady : null));
    if(!u) throw new Error('Please sign in again.');
    return {Authorization:`Bearer ${await u.getIdToken(true)}`};
  }

  function wireBot(prefix, statusUrl, setWebhookUrl){
    async function loadStatus(){
      const box=document.getElementById(prefix+'AdminStatus'); if(!box)return;
      box.innerHTML='<div class="spinner"></div> Checking bot…';
      try{
        const r=await fetch(statusUrl,{headers:await authHeaders()});
        const d=await r.json().catch(()=>({})); if(!r.ok)throw Error(d.error||'Could not load bot status.');
        box.innerHTML=`<div class="item-row"><strong>${d.configured?'🟢 Bot configured':'🟠 Bot not configured'}</strong><div class="subtitle">${esc(d.botName||d.username||'No bot identity available')} · Webhook: ${d.webhookConfigured?'configured':'not configured'}</div></div>`;
        const url=document.getElementById(prefix+'WebhookUrl'); if(url&&d.webhookUrl)url.value=d.webhookUrl;
      }catch(e){box.innerHTML=`<div class="error-text">${esc(e.message)}</div>`;}
    }
    async function setWebhook(){
      const status=document.getElementById(prefix+'AdminActionStatus'); if(status)status.textContent='Setting webhook…';
      try{
        const h=await authHeaders(); h['Content-Type']='application/json';
        const url=document.getElementById(prefix+'WebhookUrl')?.value.trim();
        const r=await fetch(setWebhookUrl,{method:'POST',headers:h,body:JSON.stringify({url})});
        const d=await r.json().catch(()=>({})); if(!r.ok)throw Error(d.error||'Could not set webhook.');
        if(status)status.textContent='✅ Webhook configured successfully.';
        loadStatus();
      }catch(e){if(status)status.textContent='❌ '+e.message;}
    }
    document.getElementById(prefix+'RefreshBtn')?.addEventListener('click',loadStatus);
    document.getElementById(prefix+'SetWebhookBtn')?.addEventListener('click',setWebhook);
    if(document.getElementById(prefix+'AdminStatus')) loadStatus();
  }

  function init(){
    wireBot('telegramModerator','/api/admin/telegram/moderator/status','/api/admin/telegram/moderator/set-webhook');
    wireBot('telegramPublisher','/api/admin/telegram/publisher/status','/api/admin/telegram/publisher/set-webhook');
  }
  window.BMTTelegramBotsAdmin={init};
  function initWhenAuthReady(){ if(window.auth?.currentUser) init(); else window.addEventListener('BMTAuthReady',init,{once:true}); }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',initWhenAuthReady);else initWhenAuthReady();
})();
