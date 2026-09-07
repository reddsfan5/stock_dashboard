(() => {
  'use strict';
  const dialog=document.getElementById('atlasLightbox'), picture=document.getElementById('lightboxImage'), body=dialog.querySelector('.lightbox-body'), zoom=document.getElementById('imageZoom');
  let trigger;
  document.querySelectorAll('[data-image]').forEach(button=>button.addEventListener('click',()=>{
    trigger=button;picture.src=button.dataset.image;picture.alt=button.querySelector('img').alt;
    document.getElementById('lightboxTitle').textContent=button.dataset.caption;
    body.classList.remove('zoomed');zoom.textContent='原始尺寸';zoom.setAttribute('aria-pressed','false');
    document.body.classList.add('atlas-image-open');dialog.showModal();document.getElementById('imageClose').focus();
  }));
  document.getElementById('imageClose').onclick=()=>dialog.close();
  dialog.addEventListener('close',()=>{document.body.classList.remove('atlas-image-open');trigger?.focus({preventScroll:true});});
  dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}});
  zoom.onclick=()=>{const active=body.classList.toggle('zoomed');zoom.setAttribute('aria-pressed',String(active));zoom.textContent=active?'适应窗口':'原始尺寸';};
  const search=document.getElementById('stockSearch'), layer=document.getElementById('stockLayer'), stocks=[...document.querySelectorAll('.atlas-stock')];
  function filter(){let count=0;const term=search.value.trim().toLowerCase();stocks.forEach(card=>{card.hidden=!(card.dataset.search.toLowerCase().includes(term)&&(!layer.value||card.dataset.layer===layer.value));if(!card.hidden)count++;});document.getElementById('stockCount').textContent=count+' 家 / 本篇 '+stocks.length+' 家';document.getElementById('stockEmpty').hidden=!!count;}
  search.addEventListener('input',filter);layer.addEventListener('change',filter);document.getElementById('resetStocks').onclick=()=>{search.value='';layer.value='';filter();search.focus();};filter();
  document.getElementById('sectorSelect').onchange=e=>{location.href='/sector_atlas_'+encodeURIComponent(e.target.value)+'.html';};
  const links=[...document.querySelectorAll('.atlas-toc a')];
  const observer=new IntersectionObserver(entries=>{const entry=entries.filter(x=>x.isIntersecting).sort((a,b)=>a.boundingClientRect.top-b.boundingClientRect.top)[0];if(!entry)return;links.forEach(a=>{if(a.hash==='#'+entry.target.id)a.setAttribute('aria-current','location');else a.removeAttribute('aria-current');});},{rootMargin:'-120px 0px -55% 0px',threshold:0});
  document.querySelectorAll('.atlas-content>section').forEach(section=>observer.observe(section));
})();
