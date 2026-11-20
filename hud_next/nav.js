const ICONS={"home": "<svg width=\"18\" height=\"18\" viewBox=\"0 0 256 256\" fill=\"currentColor\" style=\"flex-shrink:0\"><path d=\"M222.14,105.85l-80-80a20,20,0,0,0-28.28,0l-80,80A19.86,19.86,0,0,0,28,120v96a12,12,0,0,0,12,12h64a12,12,0,0,0,12-12V164h24v52a12,12,0,0,0,12,12h64a12,12,0,0,0,12-12V120A19.86,19.86,0,0,0,222.14,105.85ZM204,204H164V152a12,12,0,0,0-12-12H104a12,12,0,0,0-12,12v52H52V121.65l76-76,76,76Z\"/></svg>", "threads": "<svg width=\"18\" height=\"18\" viewBox=\"0 0 256 256\" fill=\"currentColor\" style=\"flex-shrink:0\"><path d=\"M236.34,187.09A84,84,0,0,0,172.29,68.9,84,84,0,0,0,19.66,139.09l-6.84,23.26a20,20,0,0,0,24.83,24.83l23.26-6.84a83.94,83.94,0,0,0,22.76,6.74,84.06,84.06,0,0,0,111.42,41.26l23.26,6.84a20,20,0,0,0,24.83-24.83ZM62,155.5a11.88,11.88,0,0,0-3.39.49l-20.72,6.09L44,141.35a12,12,0,0,0-.93-9A60,60,0,1,1,67.7,156.92,12,12,0,0,0,62,155.5Zm150.89,24.8a12,12,0,0,0-.93,9l6.09,20.73L197.36,204a12,12,0,0,0-9.06.93A60,60,0,0,1,111,186.63a83.93,83.93,0,0,0,68.55-91.37,60,60,0,0,1,33.38,85Z\"/></svg>", "devices": "<svg width=\"18\" height=\"18\" viewBox=\"0 0 256 256\" fill=\"currentColor\" style=\"flex-shrink:0\"><path d=\"M224,72H212V64a28,28,0,0,0-28-28H40A28,28,0,0,0,12,64v88a28,28,0,0,0,28,28h96v12a28,28,0,0,0,28,28h60a28,28,0,0,0,28-28V100A28,28,0,0,0,224,72ZM40,156a4,4,0,0,1-4-4V64a4,4,0,0,1,4-4H184a4,4,0,0,1,4,4v8H164a28,28,0,0,0-28,28v56Zm188,36a4,4,0,0,1-4,4H164a4,4,0,0,1-4-4V100a4,4,0,0,1,4-4h60a4,4,0,0,1,4,4ZM124,208a12,12,0,0,1-12,12H88a12,12,0,0,1,0-24h24A12,12,0,0,1,124,208Zm88-84a12,12,0,0,1-12,12H188a12,12,0,0,1,0-24h12A12,12,0,0,1,212,124Z\"/></svg>", "workers": "<svg width=\"18\" height=\"18\" viewBox=\"0 0 256 256\" fill=\"currentColor\" style=\"flex-shrink:0\"><path d=\"M72,104a16,16,0,1,1,16,16A16,16,0,0,1,72,104Zm96,16a16,16,0,1,0-16-16A16,16,0,0,0,168,120Zm68-40V192a36,36,0,0,1-36,36H56a36,36,0,0,1-36-36V80A36,36,0,0,1,56,44h60V16a12,12,0,0,1,24,0V44h60A36,36,0,0,1,236,80Zm-24,0a12,12,0,0,0-12-12H56A12,12,0,0,0,44,80V192a12,12,0,0,0,12,12H200a12,12,0,0,0,12-12Zm-12,82a30,30,0,0,1-30,30H86a30,30,0,0,1,0-60h84A30,30,0,0,1,200,162Zm-80-6v12h16V156ZM86,168H96V156H86a6,6,0,0,0,0,12Zm90-6a6,6,0,0,0-6-6H160v12h10A6,6,0,0,0,176,162Z\"/></svg>"};

const ITEMS=[["home","Home","Overview of every line"],["threads","Threads","Chats across Claude, Gemini, OpenCode and pi"],["devices","Devices","Machines on your network"],["workers","Workers","Agents running for you"]];
const SPLIT=8,HOVER_SPLIT=2,DRAG_SLOP=4,PROJECTION=0.16;
const row=document.getElementById("row"),page=document.getElementById("page");
const EXTRA={settings:["Settings","Settings","Workers, permissions, providers, mail and git"],usage:["Usage","Usage","Limits, tokens, speed and diagnostics"],landing:["Landing","Landing","Branches waiting to land, and overlaps to resolve"],help:["Help","Help","Shortcuts, guides and what every page does"],collab:["Collab","Collab","People on this station and coupled sessions"],terminal:["Terminal","Terminal","Shared terminals and what they are running"]};
const routeOf=()=>(location.hash.slice(1)||"home").split("/")[0];
let active=ITEMS.findIndex(i=>i[0]===routeOf()),hover=null,preview=null,drag=null;
if(active<0&&!EXTRA[routeOf()])active=0;
const segs=ITEMS.map(([id,name],i)=>{
  const s=document.createElement("div");s.className="mseg";
  s.innerHTML=`<a href="#${id}" draggable="false"><span style="position:relative;display:flex;align-items:center;gap:8px">${ICONS[id]}${name}</span></a>`;
  const a=s.querySelector("a");
  a.addEventListener("click",e=>{e.preventDefault();if(drag&&drag.swallow)return;select(i);});
  a.addEventListener("focus",()=>{if(a.matches(":focus-visible")){preview=i;layout(true);}});
  s.addEventListener("pointerenter",e=>{if(e.pointerType==="mouse"&&!(drag&&drag.scrubbing)){hover=i;layout();}});
  s.addEventListener("pointerleave",e=>{if(e.pointerType==="mouse"&&hover===i){hover=null;layout();}});
  row.appendChild(s);return s;});
const bump=(f,k)=>Math.max(0,1-Math.abs(f-k)),clamp=(v,a,b)=>Math.min(b,Math.max(a,v));
function offsets(f){const n=ITEMS.length,xs=[0];let t=0;
  for(let j=0;j<n-1;j++){let g=SPLIT*Math.max(bump(f,j),bump(f,j+1));
    if(hover!==null&&hover!==Math.round(f)&&(j===hover-1||j===hover))g=Math.max(g,HOVER_SPLIT);
    t+=g;xs.push(t);}
  return xs.map(x=>x-t/2);}

const ROOT=2*Math.PI/(0.32*1.2),K=ROOT*ROOT,C=2*(1-0.15)*ROOT;  /* Motion spring: bounce .15, visualDuration .32 */
const pos=segs.map(()=>({x:0,v:0,t:0}));let raf=0,last=0;
function paint(){const xs=pos.map(p=>p.x);
  segs.forEach((s,i)=>{s.style.transform=`translate3d(${xs[i].toFixed(2)}px,0,0)`;});}
function tick(now){const dt=Math.min(0.032,(now-(last||now))/1000)||0.016;last=now;let moving=false;
  for(const p of pos){const a=-K*(p.x-p.t)-C*p.v;p.v+=a*dt;p.x+=p.v*dt;
    if(Math.abs(p.v)>0.01||Math.abs(p.x-p.t)>0.01)moving=true;else{p.x=p.t;p.v=0;}}
  paint();raf=moving?requestAnimationFrame(tick):0;if(!raf)last=0;}
const reduce=matchMedia("(prefers-reduced-motion: reduce)").matches;
function layout(instant){let f=drag&&drag.scrubbing?drag.focus:(preview??active);if(f<0)f=-10;const xs=offsets(f),hi=Math.round(f);
  segs.forEach((s,i)=>{pos[i].t=xs[i];if(instant||reduce){pos[i].x=xs[i];pos[i].v=0;}
    s.classList.toggle("pinned",i===active);s.classList.toggle("hi",i===hi);s.classList.toggle("current",i===active);
    s.querySelector("a").toggleAttribute("aria-current",i===active);});
  if(instant||reduce){paint();return;}
  if(!raf)raf=requestAnimationFrame(tick);}
function select(i){active=clamp(i,0,ITEMS.length-1);preview=null;history.pushState(null,"","#"+ITEMS[active][0]);layout();render();}
window.addEventListener("hashchange",()=>{const r=routeOf();active=ITEMS.findIndex(i=>i[0]===r);if(active<0&&!EXTRA[r]){active=0;}preview=null;layout();render();});
function centers(){return segs.map(s=>{const a=s.querySelector("a").getBoundingClientRect();return a.left+a.width/2;});}
function toFocus(x,c){if(x<=c[0])return 0;for(let i=0;i<c.length-1;i++)if(x<=c[i+1])return i+(x-c[i])/(c[i+1]-c[i]);return c.length-1;}
row.addEventListener("pointerdown",e=>{if(e.button!==0)return;drag={id:e.pointerId,x0:e.clientX,c:centers(),scrubbing:false,focus:active,samples:[{x:e.clientX,t:e.timeStamp}]};});
row.addEventListener("pointermove",e=>{if(!drag||drag.id!==e.pointerId)return;
  if(!drag.scrubbing){if(Math.abs(e.clientX-drag.x0)<DRAG_SLOP)return;drag.scrubbing=true;row.setPointerCapture(e.pointerId);hover=null;}
  drag.samples.push({x:e.clientX,t:e.timeStamp});if(drag.samples.length>12)drag.samples.shift();
  drag.focus=toFocus(e.clientX,drag.c);layout();});
function end(e,commit){if(!drag||drag.id!==e.pointerId)return;const d=drag;
  if(!d.scrubbing){drag=null;return;}
  const s=d.samples.filter(p=>e.timeStamp-p.t<90),v=s.length>1?(s[s.length-1].x-s[0].x)/Math.max(1,(s[s.length-1].t-s[0].t))*1000:0;
  d.swallow=true;setTimeout(()=>{drag=null;},0);
  if(commit)select(Math.round(toFocus(e.clientX+v*PROJECTION,d.c)));else layout();}
row.addEventListener("pointerup",e=>end(e,true));row.addEventListener("pointercancel",e=>end(e,false));
row.addEventListener("keydown",e=>{const m={ArrowLeft:-1,ArrowRight:1}[e.key];const from=segs.findIndex(s=>s.querySelector("a")===document.activeElement);
  if(e.key==="Home"||e.key==="End"){e.preventDefault();const n=e.key==="Home"?0:ITEMS.length-1;segs[n].querySelector("a").focus();preview=n;layout(true);return;}
  if(!m||from<0)return;e.preventDefault();const n=clamp(from+m,0,ITEMS.length-1);preview=n;segs[n].querySelector("a").focus();layout(true);});
row.addEventListener("focusout",e=>{if(!row.contains(e.relatedTarget)){preview=null;layout(true);}});
/* Threads page: local chat list + linked stations (fetched after local paint, never blocks). */
(function styles(){const s=document.createElement("style");s.textContent=`
.th-wrap{margin-top:18px;display:flex;flex-direction:column;gap:14px;max-width:860px}
.th-card{background:var(--surface);border:1px solid var(--hair);border-radius:var(--radius,14px);padding:22px 20px}
.th-empty-t{margin:0;font-weight:600;font-size:15px;color:var(--ink)}
.th-empty-d{margin:6px 0 0;font-size:13px;color:var(--muted)}
.th-list{display:flex;flex-direction:column;background:var(--surface);border:1px solid var(--hair);border-radius:var(--radius,14px);overflow:hidden}
.th-row{display:flex;align-items:center;gap:10px;padding:10px 14px;border-bottom:1px solid var(--hair);cursor:default}
.th-row:last-child{border-bottom:0}
.th-title{flex:1;min-width:0;font-size:14px;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.th-alias{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:12px;color:var(--muted)}
.th-pill{flex-shrink:0;font-size:11px;font-weight:600;letter-spacing:.03em;color:var(--sky-ink,#1F6E86);background:color-mix(in srgb,var(--accent) 12%,transparent);border-radius:999px;padding:2px 9px}
.th-h{margin:6px 2px 0;font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.th-note{padding:10px 14px;font-size:13px;color:var(--muted)}
`;document.head.appendChild(s);})();
function thEl(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e;}
function thRow(c,station){const row=thEl("div","th-row");
  const title=c.title||c.alias||c.id||"Untitled";
  row.appendChild(thEl("span","th-title",String(title)));
  if(c.alias)row.appendChild(thEl("span","th-alias",station?`@${c.alias}@${station}`:`@${c.alias}`));
  if(c.source)row.appendChild(thEl("span","th-pill",String(c.source)));
  return row;}
function mountThreads(){const wrap=thEl("div","th-wrap");page.appendChild(wrap);
  const localBox=thEl("div");wrap.appendChild(localBox);
  const peersBox=thEl("div");wrap.appendChild(peersBox);
  fetch("/api/chats?limit=2000",{cache:"no-store"}).then(r=>r.ok?r.json():[]).then(d=>{
    const list=(Array.isArray(d)?d:(d.threads||d.chats||[])).filter(c=>c&&!c.worker&&!c.subagent);
    list.sort((a,b)=>(Number(b.updated)||0)-(Number(a.updated)||0));
    if(!list.length){const card=thEl("div","th-card");card.appendChild(thEl("p","th-empty-t","No chats on this station yet."));card.appendChild(thEl("p","th-empty-d","Start a chat in Claude Code, OpenCode, Antigravity or pi and it appears here."));localBox.appendChild(card);return;}
    const box=thEl("div","th-list");for(const c of list)box.appendChild(thRow(c,null));localBox.appendChild(box);
  }).catch(()=>{const card=thEl("div","th-card");card.appendChild(thEl("p","th-empty-t","No chats on this station yet."));card.appendChild(thEl("p","th-empty-d","Start a chat in Claude Code, OpenCode, Antigravity or pi and it appears here."));localBox.appendChild(card);})
  .finally(()=>{fetch("/api/peers/chats?limit=20",{cache:"no-store"}).then(r=>{if(!r.ok)throw 0;return r.json();}).then(d=>{
    const stations=(d&&Array.isArray(d.stations))?d.stations:[];if(!stations.length)return;
    for(const st of stations){if(!st)continue;
      peersBox.appendChild(thEl("h2","th-h","On "+String(st.station||"linked station")));
      const box=thEl("div","th-list");
      if(st.offline){box.appendChild(thEl("div","th-note","Offline"));}
      else if(st.denied){box.appendChild(thEl("div","th-note","Not shared with this station"));}
      else{const chats=Array.isArray(st.chats)?st.chats:[];for(const c of chats)box.appendChild(thRow(c||{},st.station));if(!chats.length)box.appendChild(thEl("div","th-note","No chats shared."));}
      peersBox.appendChild(box);}
  }).catch(()=>{});});}
const ask=document.getElementById('ask'),askIn=document.getElementById('askInput'),askSend=document.getElementById('askSend');
askIn.addEventListener('input',()=>{askSend.disabled=!askIn.value.trim();});
ask.addEventListener('submit',e=>{e.preventDefault();if(!askIn.value.trim())return;askIn.value='';askSend.disabled=true;});
function render(){const r=routeOf(),[id,name,sub]=active>=0?ITEMS[active]:[r,EXTRA[r][0],EXTRA[r][2]];ask.hidden=!['home','collab'].includes(id);
  /* One title per page (no eyebrow repeating it); wide pages sit right under the brand. */
  document.body.classList.toggle("on-wide",!["home","settings","help"].includes(id));
  page.innerHTML=id==="home"||id==="settings"?"":`<h1 class="page-h">${name}</h1><p class="page-sub">${sub}</p>`;document.title=name+" · Subway";if(id==="threads")mountThreads();document.dispatchEvent(new CustomEvent("page:render",{detail:r}));}
layout(true);render();
(function(){var n=1+Math.floor(Math.random()*4),src="logo/S"+n+"s.svg";var m=document.getElementById("brandMark");if(m)m.src=src;var l=document.querySelector('link[rel="icon"]')||document.head.appendChild(Object.assign(document.createElement("link"),{rel:"icon",type:"image/svg+xml"}));l.href=src;})();
