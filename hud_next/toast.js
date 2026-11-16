/* Toasts, top right: a vanilla port of rare-ui "toast" (swamimalode07/rare-ui, MIT), which is built on Sonner.
   Kept: status tiles with drawn glyphs, a stacked pile that fans out on hover, pause on hover and when the tab is
   hidden, swipe right to dismiss, a corner close button, an action button, and toast.promise swapping in place.
   Changed for Subway: the card uses the uniform 12px radius (no squircle) and the tile colours are our line colours.
   API: toast(title, {description, action:{label,onClick}, duration, id}) plus .success .error .warning .info
   .loading .promise(task, {loading, success, error}) and .dismiss(id). */
(function(){
const DURATION=4000,GAP=10,PEEK=14,VISIBLE=3,EXIT=200;
const GLYPH={
  success:'<path pathLength="1" d="M6 12.5l4 4 8-9"/>',
  error:'<path pathLength="1" d="M7.5 7.5l9 9"/><path pathLength="1" d="M16.5 7.5l-9 9"/>',
  warning:'<path pathLength="1" d="M12 6v7"/><circle cx="12" cy="18" r="1.6" fill="currentColor" stroke="none"/>',
  info:'<circle cx="12" cy="6.25" r="1.6" fill="currentColor" stroke="none"/><path pathLength="1" d="M12 11v7"/>',
  loading:'<circle cx="12" cy="12" r="8" stroke-opacity=".25"/><path d="M12 4a8 8 0 0 1 8 8"/>'};
const reduce=matchMedia("(prefers-reduced-motion: reduce)").matches;
const host=document.createElement("section");host.className="tst";host.setAttribute("aria-label","Notifications");
const list=document.createElement("ol");list.setAttribute("aria-live","polite");list.setAttribute("aria-relevant","additions text");host.append(list);
document.body.append(host);
let items=[],seq=0,hovered=false;
const esc=v=>String(v).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"})[c]);

function iconHTML(type,arriving){
  return `<span class="tst-tile ${type}${arriving?" arrive":""}" aria-hidden="true"><span class="tst-glyph"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${type==="loading"?2.75:3}" stroke-linecap="round" stroke-linejoin="round"${type==="loading"?' class="spin"':""}>${GLYPH[type]}</svg></span></span>`;}
function paint(t,arriving){
  t.card.innerHTML=`<div class="tst-body">${t.type!=="default"?iconHTML(t.type,arriving):""}
    <div class="tst-txt${t.type==="default"?" pad":""}"><p class="tst-title">${esc(t.title)}</p>${t.description?`<p class="tst-desc">${esc(t.description)}</p>`:""}</div>
    ${t.action?`<button type="button" class="tst-act">${esc(t.action.label)}</button>`:""}</div>
    ${Array.isArray(t.actions)&&t.actions.length?`<div class="tst-acts">${t.actions.map((a,idx)=>`<button type="button" class="tst-act ${a.cls||"secondary"}" data-tst-act="${idx}">${esc(a.label)}</button>`).join("")}</div>`:""}`;
  const a=t.card.querySelector(".tst-act");if(a)a.onclick=()=>{try{t.action.onClick();}finally{dismiss(t.id);}};
  t.card.querySelectorAll("[data-tst-act]").forEach(btn=>{btn.onclick=()=>{const idx=Number(btn.dataset.tstAct);const act=t.actions&&t.actions[idx];if(!act)return;try{act.onClick();}finally{if(!act.keep)dismiss(t.id);}};});}
function layout(){let y=0;const live=items.filter(t=>!t.leaving);const frontH=live[0]?live[0].h:0;
  live.forEach((t,i)=>{const s=t.li.style;t.li.dataset.front=String(i===0);t.li.dataset.expanded=String(hovered);s.zIndex=String(1000-i);
    if(hovered){s.transform=`translate3d(${t.dx||0}px,${y}px,0)`;s.height=t.h+"px";y+=t.h+GAP;}
    else{s.transform=`translate3d(${t.dx||0}px,${i*PEEK}px,0) scale(${1-i*.05})`;s.height=(i===0?t.h:frontH)+"px";}
    s.opacity=i<VISIBLE?"1":"0";s.pointerEvents=i<VISIBLE?"":"none";});
  host.style.height=(hovered?y:frontH+Math.min(live.length-1,VISIBLE-1)*PEEK)+"px";}
function measure(t){t.h=t.card.offsetHeight;}
function startTimer(t){clearTimeout(t.timer);if(t.duration===Infinity||hovered||document.hidden)return;t.started=Date.now();t.timer=setTimeout(()=>dismiss(t.id),t.remaining);}
function pauseTimer(t){if(t.timer){clearTimeout(t.timer);t.timer=0;t.remaining-=Date.now()-t.started;}}
function show(type,title,opts={}){
  const ex=opts.id!=null&&items.find(t=>t.id===opts.id&&!t.leaving);
  if(ex){const prev=ex.type;Object.assign(ex,{type,title,description:opts.description,action:opts.action,actions:opts.actions,
      duration:opts.duration??(type==="loading"?Infinity:DURATION)});ex.remaining=ex.duration;
    paint(ex,false);if(prev!==type&&!reduce){const tile=ex.card.querySelector(".tst-tile");tile&&tile.classList.add("bump");}
    measure(ex);layout();startTimer(ex);return ex.id;}
  const id=opts.id??`t${++seq}`;
  const li=document.createElement("li");li.className="tst-item";li.setAttribute("role",type==="error"?"alert":"status");
  const card=document.createElement("div");card.className="tst-card";li.append(card);
  if(opts.dismissible!==false){const x=document.createElement("button");x.type="button";x.className="tst-x";x.setAttribute("aria-label","Dismiss");
    x.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" aria-hidden="true"><path d="M7 7l10 10M17 7L7 17"/></svg>';
    x.onclick=()=>dismiss(id);li.append(x);}
  const t={id,li,card,type,title,description:opts.description,action:opts.action,actions:opts.actions,dx:0,leaving:false,
    duration:opts.duration??(type==="loading"?Infinity:DURATION)};t.remaining=t.duration;
  paint(t,true);items.unshift(t);list.prepend(li);
  li.style.transform="translate3d(0,-110%,0)";li.style.opacity="0";measure(t);
  requestAnimationFrame(()=>requestAnimationFrame(()=>{li.classList.add("in");layout();}));
  swipe(t);startTimer(t);
  while(items.filter(x=>!x.leaving).length>12)dismiss(items.filter(x=>!x.leaving).pop().id);
  return id;}
function dismiss(id){const list_=id==null?items.slice():items.filter(t=>t.id===id);
  for(const t of list_){if(t.leaving)continue;t.leaving=true;clearTimeout(t.timer);
    t.li.style.transition=reduce?"none":`opacity ${EXIT}ms ease, transform ${EXIT}ms ease`;
    t.li.style.opacity="0";t.li.style.transform=`translate3d(${t.dx>0?t.dx+60:0}px,${t.dx>0?0:-12}px,0) scale(.96)`;
    setTimeout(()=>{t.li.remove();items=items.filter(x=>x!==t);if(!items.length){hovered=false;}layout();},reduce?0:EXIT);}
  layout();}
function swipe(t){let x0=null,start=0;
  t.li.addEventListener("pointerdown",e=>{if(e.button!==0||e.target.closest("button"))return;x0=e.clientX;start=e.timeStamp;t.li.setPointerCapture(e.pointerId);t.li.classList.add("drag");});
  t.li.addEventListener("pointermove",e=>{if(x0==null)return;t.dx=Math.max(0,e.clientX-x0);layout();});
  const end=e=>{if(x0==null)return;const v=t.dx/Math.max(1,e.timeStamp-start);x0=null;t.li.classList.remove("drag");
    if(t.dx>45||v>.11)dismiss(t.id);else{t.dx=0;layout();}};
  t.li.addEventListener("pointerup",end);t.li.addEventListener("pointercancel",end);}
host.addEventListener("pointerenter",()=>{hovered=true;items.forEach(pauseTimer);layout();});
host.addEventListener("pointerleave",()=>{hovered=false;items.forEach(startTimer);layout();});
host.addEventListener("focusin",()=>{hovered=true;items.forEach(pauseTimer);layout();});
host.addEventListener("focusout",e=>{if(!host.contains(e.relatedTarget)){hovered=false;items.forEach(startTimer);layout();}});
document.addEventListener("visibilitychange",()=>items.forEach(document.hidden?pauseTimer:startTimer));
addEventListener("keydown",e=>{if(e.altKey&&e.key.toLowerCase()==="t"){const b=host.querySelector(".tst-x,.tst-act");b&&b.focus();}});
const v=type=>(title,opts)=>show(type,title,opts);
function promise(task,m,opts={}){const id=show("loading",m.loading,{...opts,duration:Infinity});
  const p=typeof task==="function"?task():task;const msg=(x,arg)=>typeof x==="function"?x(arg):x;
  p.then(d=>show("success",msg(m.success,d),{...opts,id,duration:opts.duration})).catch(err=>show("error",msg(m.error,err),{...opts,id,duration:opts.duration}));
  return p;}
window.toast=Object.assign(v("default"),{success:v("success"),error:v("error"),warning:v("warning"),info:v("info"),loading:v("loading"),promise,dismiss});
})();
