/* At a glance: a bento of live summaries, opened from the button above the profile chip.
   Real data only: /api/status (limits, workers), /api/landing, /api/chats, /api/diagnostics.
   No hover effects: cards are plain surfaces with the prototype's hairline ring. */
(function(){
const reduce=matchMedia("(prefers-reduced-motion: reduce)").matches;
const PKEY="subway-glance-provider";

const css=`
.bento{--gap:12px;display:grid;grid-template-columns:1fr;gap:var(--gap);margin-top:28px}
.bc{position:relative;display:flex;flex-direction:column;gap:14px;min-height:190px;padding:18px;border-radius:var(--radius);background:var(--surface);
  color:var(--ink);text-decoration:none;box-shadow:0 0 0 1px var(--hair),0 1px 2px color-mix(in srgb,var(--ink) 3%,transparent);min-width:0}
a.bc:focus-visible,.bc a:focus-visible,.bc button:focus-visible{outline:2px solid var(--sky);outline-offset:3px}
.bc-top{display:flex;align-items:center;justify-content:space-between;gap:10px;min-width:0}
.bc-lab{font-size:12px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
.bc-meta{font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bc-body{flex:1;display:flex;flex-direction:column;justify-content:center;gap:10px;min-height:0}
.bc-foot{display:flex;flex-direction:column;gap:3px}
.bc-title{font-size:17px;font-weight:500;letter-spacing:-.01em}
.bc-desc{font-size:13px;line-height:1.45;color:var(--muted)}
.bc-foot a{color:inherit}
.bc-empty{font-size:13.5px;color:var(--muted)}
.bc-big{font-family:"Newsreader",Georgia,serif;font-size:44px;line-height:1;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.bc-big small{font-family:"Instrument Sans",sans-serif;font-size:14px;color:var(--muted);letter-spacing:0;margin-left:6px}
.bc-seg{display:flex;gap:2px;padding:3px;border-radius:10px;background:var(--cloud);overflow-x:auto;scrollbar-width:none;max-width:100%}
.bc-seg::-webkit-scrollbar{display:none}
.bc-seg button{all:unset;cursor:pointer;padding:5px 10px;border-radius:7px;font-size:12.5px;color:var(--muted);white-space:nowrap}
.bc-seg button[aria-pressed="true"]{background:var(--surface);color:var(--ink);box-shadow:0 0 0 1px var(--hair)}
.bar{display:grid;grid-template-columns:minmax(64px,auto) 1fr 44px;align-items:center;gap:10px;font-size:13px;color:var(--muted);font-variant-numeric:tabular-nums}
.bar > span:first-child{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:150px}
.bar i{display:block;height:8px;border-radius:99px;background:color-mix(in srgb,var(--ink) 14%,transparent);overflow:hidden}
.bar i b{display:block;height:100%;width:0;border-radius:inherit;background:var(--sky);transition:width .6s cubic-bezier(.2,.8,.2,1)}
.bar i b.hot{background:#E07B24}
.bar span:last-child{text-align:right;color:var(--ink)}
.bar-note{font-size:12px;color:var(--muted);margin-top:-4px}
.gauge{display:grid;place-items:center;position:relative}
.gauge svg{width:min(100%,220px);height:auto;overflow:visible}
.gauge .gv{position:absolute;bottom:2px;text-align:center}
.gauge .gv b{display:block;font-family:"Newsreader",Georgia,serif;font-weight:500;font-size:34px;line-height:1;font-variant-numeric:tabular-nums}
.gauge .gv span{font-size:12px;color:var(--muted)}
.wk{display:flex;flex-direction:column;gap:2px}
.wk-row{display:grid;grid-template-columns:10px minmax(0,1fr) auto;align-items:center;gap:10px;padding:9px 2px;border-top:1px solid var(--hair);font-size:14px}
.wk-row:first-child{border-top:0}
.wk-dot{width:8px;height:8px;border-radius:50%;background:var(--sky)}
.wk-dot.wait{background:#C99A26}.wk-dot.fail{background:#C0392B}.wk-dot.done{background:#B9C2BC}
.wk-name{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.wk-name em{font-style:normal;color:var(--muted);margin-left:6px;font-size:12px}
.chip{font-size:11px;font-weight:600;letter-spacing:.04em;padding:3px 8px;border-radius:99px;background:#EEF4F1;color:var(--sky-ink);white-space:nowrap}
.chip.wait{background:#FBF1DD;color:#8A5A0E}.chip.fail{background:#FBE5E2;color:#A3271B}.chip.done{background:#EEF1EA;color:var(--muted)}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.stats div{display:flex;flex-direction:column;gap:2px;min-width:0}
.stats b{font-family:"Newsreader",Georgia,serif;font-weight:500;font-size:26px;line-height:1.1;font-variant-numeric:tabular-nums}
.stats b small{font-family:"Instrument Sans",sans-serif;font-size:13px;color:var(--muted);margin-left:2px}
.stats span{font-size:12px;color:var(--muted)}
.spark{width:100%;height:42px}
.ch-list{display:flex;flex-direction:column;gap:6px;font-size:13.5px}
.ch-list div{display:flex;gap:8px;min-width:0}
.ch-list span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ch-list em{font-style:normal;color:var(--muted);font-size:12px;flex-shrink:0}
@media (min-width:640px){.bento{grid-template-columns:repeat(2,1fr)}.bc.w,.bc.l{grid-column:span 2}}
@media (min-width:1024px){.bento{grid-template-columns:repeat(3,1fr);grid-auto-rows:minmax(176px,auto);margin-top:0}
  .bc.w,.bc.l{grid-column:auto}.bc.lim{grid-column:span 2}.bc{min-height:176px;padding:16px}.wk-row:nth-child(n+4){display:none}}
.gl-btn{position:fixed;right:24px;bottom:calc(24px + var(--h) + 10px);z-index:59;display:inline-flex;align-items:center;gap:8px;height:var(--h);
  padding:0 16px 0 13px;border:0;border-radius:var(--radius);background:color-mix(in srgb,var(--surface) 82%,transparent);backdrop-filter:blur(12px) saturate(1.2);-webkit-backdrop-filter:blur(12px) saturate(1.2);
  box-shadow:0 0 0 1px var(--hair),0 6px 18px -10px color-mix(in srgb,var(--ink) 25%,transparent);color:var(--ink);font:500 14px "Instrument Sans",sans-serif;cursor:pointer;transition:background .15s}
.gl-btn:hover{background:var(--surface)}.gl-btn:focus-visible{outline:2px solid var(--sky);outline-offset:3px}
.gl-btn svg{color:var(--sky-ink)}.gl-btn[aria-expanded="true"]{background:var(--sky-chip-hi)}
.gl-layer{position:fixed;inset:0;z-index:58;background:color-mix(in srgb,var(--ground) 32%,transparent);backdrop-filter:blur(5px);-webkit-backdrop-filter:blur(5px);opacity:0;transition:opacity .2s}
.gl-layer.open{opacity:1}
.gl-panel{position:fixed;right:24px;bottom:calc(24px + var(--h) + 10px + var(--h) + 12px);width:min(1100px,calc(100vw - 48px));
  max-height:calc(100vh - 2 * var(--h) - 90px);overflow:auto;padding:4px;transform-origin:100% 100%;transform:translateY(8px) scale(.98);transition:transform .25s cubic-bezier(.2,.8,.2,1)}
.gl-layer.open .gl-panel{transform:none}
@media (max-width:639px){.gl-btn{right:16px}.gl-panel{right:16px;width:calc(100vw - 32px)}}
@media (prefers-reduced-motion:reduce){.gl-layer,.gl-panel,.bar i b{transition:none}}`;
const st=document.createElement("style");st.textContent=css;document.head.append(st);

/* ---- data ---- */
const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
const get=p=>fetch(p,{cache:"no-store"}).then(r=>r.ok?r.json():Promise.reject(new Error(r.status)));
const D={status:null,landing:null,chats:null,diag:null,loaded:false};
async function load(onPart){
  const onSettled=(p,assign)=>p.then((val)=>{assign(val);D.loaded=true;if(typeof onPart==="function")onPart();}).catch(()=>{});
  const [s,l,c,g]=await Promise.allSettled([
    onSettled(get("/api/status?view=next"),v=>{D.status=v;}),
    onSettled(get("/api/landing"),v=>{D.landing=v;}),
    onSettled(get("/api/chats?limit=50"),v=>{D.chats=v;}),
    onSettled(get("/api/diagnostics"),v=>{D.diag=v;})]);
  D.loaded=true;}

const fmtB=n=>{n=Number(n||0);return n>=1e9?(n/1e9).toFixed(1)+" GB":n>=1e6?Math.round(n/1e6)+" MB":Math.round(n/1e3)+" KB";};
function untilTxt(ts){const s=Number(ts||0)-Date.now()/1000;if(!ts||s<=0)return "";
  const d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);
  return d?`${d}d ${h}h`:h?`${h}h ${m}m`:`${m}m`;}
const usedPct=w=>Math.round(w.used_pct!=null?Number(w.used_pct):100-Number(w.left_pct??100));

/* Providers that report at least one limit window. */
function limitProviders(){
  return ((D.status?.quotas?.providers)||[]).map(p=>({id:p.id,name:p.name||p.id,plan:p.plan||"",state:p.state,note:p.note||"",
    wins:(p.pools||[]).flatMap(pool=>(pool.windows||[]).filter(w=>!w.disabled).map(w=>({...w,pool:pool.name,pools:(p.pools||[]).length})))}))
    .filter(p=>p.wins.length);}
let pick=(()=>{try{return localStorage.getItem(PKEY)||"";}catch(_){return "";}})();

/* ---- pieces ---- */
function gauge(p){const r=80,c=Math.PI*r,v=Math.max(0,Math.min(1,p));
  return `<svg viewBox="0 0 200 112" aria-hidden="true"><path d="M20 100 A80 80 0 0 1 180 100" fill="none" style="stroke:color-mix(in srgb,var(--ink) 14%,transparent)" stroke-width="14" stroke-linecap="round"/>
  <path class="g-arc" d="M20 100 A80 80 0 0 1 180 100" fill="none" stroke="${v>.8?"#E07B24":"var(--sky)"}" stroke-width="14" stroke-linecap="round" stroke-dasharray="${c}" stroke-dashoffset="${c}" data-to="${c*(1-v)}"/></svg>`;}
function spark(a){if(!a||a.length<2)return "";const w=200,h=42,mx=Math.max(...a),mn=Math.min(...a),x=i=>i/(a.length-1)*w,y=v=>h-4-(v-mn)/(mx-mn||1)*(h-8);
  const d=a.map((v,i)=>`${i?"L":"M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join("");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><path d="${d} L${w} ${h} L0 ${h}Z" fill="rgba(38,146,166,.10)"/><path d="${d}" fill="none" stroke="var(--sky)" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;}
const bar=(lab,p,hot)=>`<div class="bar"><span title="${esc(lab)}">${esc(lab)}</span><i><b class="${hot?"hot":""}" data-w="${p}"></b></i><span>${p}%</span></div>`;
const STATE={running:["Running",""],launching:["Starting",""],pending_approval:["Waiting on you","wait"],failed:["Failed","fail"],completed:["Done","done"],stopped:["Stopped","done"],rejected:["Rejected","done"]};

function limitsCard(){
  const ps=limitProviders();
  if(!ps.length)return `<div class="bc lim"><div class="bc-top"><span class="bc-lab">Limits</span></div><div class="bc-body"><p class="bc-empty">${D.loaded?"No provider is reporting limits right now.":"Loading limits…"}</p></div>
    <div class="bc-foot"><span class="bc-title">Usage</span><span class="bc-desc"><a href="#usage">Open Usage</a> for every provider.</span></div></div>`;
  const p=ps.find(x=>x.id===pick)||ps[0];
  const soon=p.wins.map(w=>w.resets_at).filter(Boolean).sort((a,b)=>a-b)[0];
  const rows=p.wins.map(w=>{const u=usedPct(w);const lab=p.wins.length>1&&w.pools>1?`${w.pool} · ${w.label}`:w.label;
    return bar(lab,u,u>=80)+(w.note?`<div class="bar-note">${esc(w.note)}</div>`:"");}).join("");
  return `<div class="bc lim"><div class="bc-top"><span class="bc-lab">Limits</span><span class="bc-meta">${soon?`resets in ${untilTxt(soon)}`:esc(p.plan)}</span></div>
    <div class="bc-seg" role="group" aria-label="Provider">${ps.map(x=>`<button type="button" data-prov="${esc(x.id)}" aria-pressed="${x.id===p.id}">${esc(x.name)}</button>`).join("")}</div>
    <div class="bc-body">${rows}</div>
    <div class="bc-foot"><span class="bc-title">${esc(p.name)}${p.plan?` <span class="bc-desc">· ${esc(p.plan)}</span>`:""}</span><span class="bc-desc">Used share of each window. <a href="#usage">Open Usage</a></span></div></div>`;}

function tightestCard(){
  let best=null;
  for(const p of limitProviders())for(const w of p.wins){const left=100-usedPct(w);if(!best||left<best.left)best={left,p,w};}
  if(!best)return `<a class="bc" href="#usage"><div class="bc-top"><span class="bc-lab">Tightest limit</span></div><div class="bc-body"><p class="bc-empty">${D.loaded?"Nothing to report.":"Loading…"}</p></div></a>`;
  return `<a class="bc" href="#usage"><div class="bc-top"><span class="bc-lab">Tightest limit</span><span class="bc-meta">${best.w.resets_at?`resets in ${untilTxt(best.w.resets_at)}`:""}</span></div>
    <div class="bc-body gauge">${gauge(1-best.left/100)}<div class="gv"><b>${best.left}%</b><span>left</span></div></div>
    <div class="bc-foot"><span class="bc-title">${esc(best.p.name)}</span><span class="bc-desc">${esc(best.w.label)}${best.w.pools>1?` · ${esc(best.w.pool)}`:""} is the closest to running out.</span></div></a>`;}

function workersCard(){
  const ws=(D.status?.workers)||[];
  const running=ws.filter(w=>w.status==="running"||w.status==="launching").length,waiting=ws.filter(w=>w.status==="pending_approval").length;
  const order={pending_approval:0,running:1,launching:1,failed:2};
  const top=ws.slice().sort((a,b)=>(order[a.status]??3)-(order[b.status]??3)).slice(0,5);
  return `<a class="bc l" href="#workers"><div class="bc-top"><span class="bc-lab">Workers</span><span class="bc-meta">${running} running · ${waiting} waiting</span></div>
    <div class="bc-body" style="justify-content:flex-start">${top.length?`<div class="wk">${top.map(w=>{const [lab,cls]=STATE[w.status]||[w.status||"Unknown","done"];
      const via=[w.provider,w.model].filter(Boolean).join(" · ");
      return `<div class="wk-row"><span class="wk-dot ${cls}"></span><span class="wk-name">${esc(w.role||w.id)}${via?`<em>${esc(via)}</em>`:""}</span><span class="chip ${cls}">${esc(lab)}</span></div>`;}).join("")}</div>`
      :`<p class="bc-empty">${D.loaded?"No workers right now.":"Loading…"}</p>`}</div>
    <div class="bc-foot"><span class="bc-title">The fleet</span><span class="bc-desc">What is running, and what needs you before it can start.</span></div></a>`;}

function landingCard(){
  const l=D.landing||{};const q=Number(l.queue||0),c=Number(l.conflicts||0);
  return `<a class="bc w" href="#landing"><div class="bc-top"><span class="bc-lab">Landing</span><span class="bc-meta">${c?`${c} overlap${c===1?"":"s"} to resolve`:"no overlaps"}</span></div>
    <div class="bc-body"><div class="bc-big">${D.landing?q:"–"}<small>branch${q===1?"":"es"} waiting to land</small></div></div>
    <div class="bc-foot"><span class="bc-title">${q?"Ready to merge":"Nothing waiting"}</span><span class="bc-desc">Land mode: ${esc(l.land_mode||"unknown")}.</span></div></a>`;}

function chatsCard(){
  const cs=((D.chats?.chats)||[]).filter(c=>c.open&&!c.worker&&!c.subagent);
  return `<a class="bc" href="#threads"><div class="bc-top"><span class="bc-lab">Chats</span><span class="bc-meta">${esc(D.chats?.total_all??"")} in all</span></div>
    <div class="bc-body">${cs.length?`<div class="ch-list">${cs.slice(0,3).map(c=>`<div><span>${esc(c.tab_name||c.title||c.id)}</span><em>${esc(c.source||"")}</em></div>`).join("")}</div>`:`<p class="bc-empty">${D.loaded?"No chats open.":"Loading…"}</p>`}</div>
    <div class="bc-foot"><span class="bc-title">${cs.length} open</span><span class="bc-desc">Chats open across your agents.</span></div></a>`;}

function healthCard(){
  const g=D.diag;
  if(!g)return `<a class="bc" href="#usage/diagnostics"><div class="bc-top"><span class="bc-lab">HUD health</span></div><div class="bc-body"><p class="bc-empty">${D.loaded?"Diagnostics unavailable.":"Loading…"}</p></div></a>`;
  const trend=(g.history||[]).slice(-24).map(h=>Number(h.cpu||0));
  return `<a class="bc" href="#usage/diagnostics"><div class="bc-top"><span class="bc-lab">HUD health</span><span class="bc-meta">${g.healthy?"healthy":"needs a look"}</span></div>
    <div class="bc-body"><div class="stats"><div><b>${Math.round(g.cpu_pct||0)}<small>%</small></b><span>CPU</span></div><div><b>${esc(fmtB(g.rss_bytes).split(" ")[0])}<small>${esc(fmtB(g.rss_bytes).split(" ")[1])}</small></b><span>memory</span></div><div><b>${g.process_count||0}</b><span>processes</span></div></div>${spark(trend)}</div>
    <div class="bc-foot"><span class="bc-title">Machine</span><span class="bc-desc">CPU over the last samples.</span></div></a>`;}

const cards=()=>[limitsCard(),tightestCard(),workersCard(),landingCard(),chatsCard(),healthCard()].join("");

function fill(){requestAnimationFrame(()=>{if(!grid)return;grid.querySelectorAll(".bar b").forEach(b=>{b.style.width=b.dataset.w+"%";});
  grid.querySelectorAll(".g-arc").forEach(a=>{if(reduce){a.style.strokeDashoffset=a.dataset.to;return;}
    a.animate([{strokeDashoffset:a.getAttribute("stroke-dashoffset")},{strokeDashoffset:a.dataset.to}],{duration:900,easing:"cubic-bezier(.2,.8,.2,1)",fill:"forwards"});});});}
function paint(){if(!grid)return;const f=document.activeElement?.dataset?.prov;grid.innerHTML=cards();fill();
  if(f)grid.querySelector(`[data-prov="${CSS.escape(f)}"]`)?.focus({preventScroll:true});}

/* ---- panel ---- */
let btn=null,layer=null,grid=null,timer=0,onPush=null;
const SQ='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/></svg>';
function close(focusBtn){if(!layer)return;const l=layer;layer=null;grid=null;if(onPush){window.removeEventListener("subway:push",onPush);onPush=null;}clearInterval(timer);timer=0;
  document.removeEventListener("keydown",onKey,true);
  btn?.setAttribute("aria-expanded","false");l.classList.remove("open");setTimeout(()=>l.remove(),reduce?0:200);if(focusBtn)btn?.focus();}
function onKey(e){if(e.key==="Escape"){e.stopPropagation();close(true);}}
function open(){if(layer)return;
  layer=document.createElement("div");layer.className="gl-layer";
  layer.innerHTML=`<div class="gl-panel" role="dialog" aria-label="At a glance"></div>`;
  grid=document.createElement("section");grid.className="bento";grid.innerHTML=cards();
  layer.firstElementChild.append(grid);document.body.append(layer);
  layer.addEventListener("click",e=>{if(e.target===layer)close(true);});
  grid.addEventListener("click",e=>{
    const b=e.target.closest("[data-prov]");
    if(b){pick=b.dataset.prov;try{localStorage.setItem(PKEY,pick);}catch(_){}paint();return;}
    if(e.target.closest("a"))close(false);});
  document.addEventListener("keydown",onKey,true);
  btn.setAttribute("aria-expanded","true");
  requestAnimationFrame(()=>{layer?.classList.add("open");fill();});
  load(paint).then(paint);
  onPush=()=>{if(layer&&!document.hidden)load(paint).then(paint);};
  window.addEventListener("subway:push",onPush);
  grid.querySelector(".bc,.bc-seg button")?.focus?.({preventScroll:true});}
function mount(){close(false);const r=(location.hash.slice(1)||"home").split("/")[0];
  if(r!=="home"){btn?.remove();btn=null;return;}
  if(btn)return;
  btn=document.createElement("button");btn.type="button";btn.className="gl-btn";btn.setAttribute("aria-expanded","false");btn.setAttribute("aria-haspopup","dialog");
  btn.innerHTML=SQ+"<span>At a glance</span>";btn.addEventListener("click",()=>layer?close(true):open());
  document.body.append(btn);}
document.addEventListener("page:render",mount);mount();
})();
