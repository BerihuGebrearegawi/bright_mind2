/* Bright Mind Tutor V30.63 - password visibility control */
(function(){
  'use strict';
  const ICON_SHOW='<span class="eye-icon" aria-hidden="true">👁️</span>';
  const ICON_HIDE='<span class="eye-icon" aria-hidden="true">🙈</span>';
  function enhance(input){
    if(!input || input.dataset.passwordVisibilityEnhanced==='true') return;
    input.dataset.passwordVisibilityEnhanced='true';
    const wrap=document.createElement('div');
    wrap.className='password-field-wrap';
    input.parentNode.insertBefore(wrap,input);
    wrap.appendChild(input);
    const btn=document.createElement('button');
    btn.type='button';
    btn.className='password-visibility-toggle';
    btn.setAttribute('aria-label','Show password');
    btn.setAttribute('aria-pressed','false');
    btn.innerHTML=ICON_SHOW;
    btn.addEventListener('click',function(){
      const visible=input.type==='text';
      input.type=visible?'password':'text';
      btn.innerHTML=visible?ICON_SHOW:ICON_HIDE;
      btn.setAttribute('aria-label',visible?'Show password':'Hide password');
      btn.setAttribute('aria-pressed',String(!visible));
      input.focus({preventScroll:true});
    });
    wrap.appendChild(btn);
  }
  function scan(root=document){ root.querySelectorAll('input[type="password"]').forEach(enhance); }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',()=>scan()); else scan();
  const observer=new MutationObserver(mutations=>{
    for(const m of mutations){
      for(const node of m.addedNodes){ if(node.nodeType===1){ if(node.matches?.('input[type="password"]')) enhance(node); scan(node); } }
    }
  });
  observer.observe(document.documentElement,{childList:true,subtree:true});
})();
