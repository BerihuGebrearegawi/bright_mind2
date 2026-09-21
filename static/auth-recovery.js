/* BMT V31.00 — shared Firebase password recovery UI */
(function(){
  const esc=s=>String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function openRecovery(){
    let modal=document.getElementById('bmtPasswordRecovery');
    if(!modal){
      modal=document.createElement('div'); modal.id='bmtPasswordRecovery'; modal.className='modal-overlay';
      modal.innerHTML=`<div class="modal-content" style="max-width:460px"><div style="display:flex;justify-content:space-between;align-items:center"><h3 style="margin:0">🔐 Forgot Password?</h3><button type="button" class="btn btn-neutral" data-close>✕</button></div><p class="subtitle">Enter your account email. We will send a secure password-reset email if an account exists.</p><form id="bmtRecoveryForm" class="form-vertical"><label>Email</label><input id="bmtRecoveryEmail" type="email" autocomplete="email" required placeholder="you@example.com"><button class="btn btn-primary" type="submit">Send reset email</button><div id="bmtRecoveryStatus" class="subtitle" aria-live="polite"></div></form></div>`;
      document.body.appendChild(modal);
      modal.querySelector('[data-close]').onclick=()=>modal.remove();
      modal.addEventListener('click',e=>{if(e.target===modal)modal.remove()});
      modal.querySelector('#bmtRecoveryForm').onsubmit=async e=>{
        e.preventDefault(); const email=modal.querySelector('#bmtRecoveryEmail').value.trim(); const status=modal.querySelector('#bmtRecoveryStatus'); const btn=modal.querySelector('button[type=submit]');
        btn.disabled=true; btn.textContent='Sending…'; status.textContent='';
        try{
          const r=await fetch('/api/auth/forgot-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email})});
          const d=await r.json().catch(()=>({}));
          if(!r.ok) throw new Error(d.error||'Password recovery is temporarily unavailable.');
          status.textContent=d.message||'If the account exists, a reset email has been sent.'; status.style.color='var(--success,#16a34a)';
        }catch(err){status.textContent=err.message;status.style.color='var(--danger,#dc2626)';}
        finally{btn.disabled=false;btn.textContent='Send reset email';}
      };
    }
    const source=document.activeElement;
    const email=source?.dataset?.recoveryEmail||document.querySelector('input[type=email]')?.value||'';
    modal.querySelector('#bmtRecoveryEmail').value=email; modal.style.display='flex'; modal.querySelector('#bmtRecoveryEmail').focus();
  }
  function attach(){
    document.querySelectorAll('[data-forgot-password]').forEach(btn=>{if(!btn.dataset.recoveryBound){btn.dataset.recoveryBound='1';btn.addEventListener('click',openRecovery)}});
  }
  window.BMTPasswordRecovery={open:openRecovery,attach};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',attach);else attach();
})();
