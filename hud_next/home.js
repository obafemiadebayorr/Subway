const TI={"check": "<svg width=\"20\" height=\"20\" viewBox=\"0 0 256 256\" fill=\"currentColor\" style=\"flex-shrink:0\"><path d=\"M232.49,80.49l-128,128a12,12,0,0,1-17,0l-56-56a12,12,0,1,1,17-17L96,183,215.51,63.51a12,12,0,0,1,17,17Z\"/></svg>", "git-merge": "<svg width=\"20\" height=\"20\" viewBox=\"0 0 256 256\" fill=\"currentColor\" style=\"flex-shrink:0\"><path d=\"M208,108a36.05,36.05,0,0,0-33.38,22.54l-39-5.57a4,4,0,0,1-2.47-1.35L100.55,85.53A36,36,0,1,0,68,89.94v76.12a36,36,0,1,0,24,0V112.44l23,26.8a28,28,0,0,0,17.3,9.49l41.34,5.91A36,36,0,1,0,208,108ZM80,44A12,12,0,1,1,68,56,12,12,0,0,1,80,44Zm0,168a12,12,0,1,1,12-12A12,12,0,0,1,80,212Zm128-56a12,12,0,1,1,12-12A12,12,0,0,1,208,156Z\"/></svg>", "users-three": "<svg width=\"20\" height=\"20\" viewBox=\"0 0 256 256\" fill=\"currentColor\" style=\"flex-shrink:0\"><path d=\"M164.38,181.1a52,52,0,1,0-72.76,0,75.89,75.89,0,0,0-30,28.89,12,12,0,0,0,20.78,12,53,53,0,0,1,91.22,0,12,12,0,1,0,20.78-12A75.89,75.89,0,0,0,164.38,181.1ZM100,144a28,28,0,1,1,28,28A28,28,0,0,1,100,144Zm147.21,9.59a12,12,0,0,1-16.81-2.39c-8.33-11.09-19.85-19.59-29.33-21.64a12,12,0,0,1-1.82-22.91,20,20,0,1,0-24.78-28.3,12,12,0,1,1-21-11.6,44,44,0,1,1,73.28,48.35,92.18,92.18,0,0,1,22.85,21.69A12,12,0,0,1,247.21,153.59Zm-192.28-24c-9.48,2.05-21,10.55-29.33,21.65A12,12,0,0,1,6.41,136.79,92.37,92.37,0,0,1,29.26,115.1a44,44,0,1,1,73.28-48.35,12,12,0,1,1-21,11.6,20,20,0,1,0-24.78,28.3,12,12,0,0,1-1.82,22.91Z\"/></svg>", "terminal-window": "<svg width=\"20\" height=\"20\" viewBox=\"0 0 256 256\" fill=\"currentColor\" style=\"flex-shrink:0\"><path d=\"M72.5,150.63,100.79,128,72.5,105.37a12,12,0,1,1,15-18.74l40,32a12,12,0,0,1,0,18.74l-40,32a12,12,0,0,1-15-18.74ZM144,172h32a12,12,0,0,0,0-24H144a12,12,0,0,0,0,24ZM236,56V200a20,20,0,0,1-20,20H40a20,20,0,0,1-20-20V56A20,20,0,0,1,40,36H216A20,20,0,0,1,236,56Zm-24,4H44V196H212Z\"/></svg>", "arrow-right": "<svg width=\"20\" height=\"20\" viewBox=\"0 0 256 256\" fill=\"currentColor\" style=\"flex-shrink:0\"><path d=\"M224.49,136.49l-72,72a12,12,0,0,1-17-17L187,140H40a12,12,0,0,1,0-24H187L135.51,64.48a12,12,0,0,1,17-17l72,72A12,12,0,0,1,224.49,136.49Z\"/></svg>"};
/* Home: four quick tiles, two at each edge, vertically centred. Demo data until the port. */
(function(){
/* Dots and lines come from live data: no dot unless something needs you right now. */
const TILES=[
 {side:"l",id:"approvals",title:"Approvals",href:"#workers",icon:"check",c:"#C9C226",sub:"Checking…"},
 {side:"l",id:"files",title:"Files",href:"#landing",icon:"git-merge",c:"#F08A24",sub:"Checking…"},
 {side:"r",id:"team",title:"Collab",href:"#collab/sessions",icon:"users-three",c:"#8E4A9E",sub:"Checking…"},
 {side:"r",id:"terminal",title:"Terminal",href:"#terminal",icon:"terminal-window",c:"#12924A",sub:"Checking…"}];
const pl=(n,w)=>`${n} ${w}${n===1?"":"s"}`;
async function live(){const get=u=>fetch(u,{cache:"no-store"}).then(r=>r.ok?r.json():{}).catch(()=>({}));
  const T=Object.fromEntries(TILES.map(t=>[t.id,t])),len=x=>Array.isArray(x)?x.length:x&&typeof x==="object"?Object.keys(x).length:Number(x)||0;
  const H={run:0,wait:0,session:""},herop=()=>{const el=document.querySelector(".hero-sub");if(!el)return;const parts=[];if(H.run)parts.push(`${pl(H.run,"worker")} running`);if(H.wait)parts.push(`<a href="#workers">${pl(H.wait,"waiting for approval")}</a>`);if(H.session)parts.push(`<a href="#collab/sessions" class="hero-live" style="white-space:nowrap"><span aria-hidden="true" style="display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--ochre);box-shadow:0 0 0 3px color-mix(in srgb,var(--ochre) 20%,transparent);margin-right:6px;vertical-align:1px"></span>Coupled: ${esc(H.session)}</a>`);el.innerHTML=parts.length?parts.join(" · "):"All quiet.";};
  const upd=()=>{if(document.querySelector(".tiles"))paint();herop();};
  /* Each fetch updates only its own badge as soon as it lands: no Promise.all blocking. */
  const fl={ov:0,lk:0};
  const files=()=>Object.assign(T.files,{needs:fl.ov>0,sub:[fl.ov?`${pl(fl.ov,"overlap")} to resolve`:"No overlaps",fl.lk?`${pl(fl.lk,"file")} leased`:""].filter(Boolean).join(" · ")+"."});
  get("/api/status?view=next").then(st=>{
    const ws=st.workers||[];
    H.run=ws.filter(w=>w.status==="running").length;
    H.wait=ws.filter(w=>w.status==="pending_approval").length;
    Object.assign(T.approvals,{needs:H.wait>0,sub:H.wait?`${pl(H.wait,"worker")} ${H.wait===1?"is":"are"} waiting on you.`:"Nothing waiting on you."});
    fl.lk=len(st.locks);files();upd();});
  get("/api/landing").then(ld=>{
    fl.ov=len(ld.conflicts);files();upd();});
  get("/api/coordination/all").then(co=>{
    const others=(co.groups||[]).flatMap(g=>g.stations||[]).filter(x=>x.relation&&x.relation!=="self").length;
    Object.assign(T.team,{needs:false,sub:others?`${pl(others,"linked station")} on track ${co.track||"main"}.`:"No linked stations online."});upd();});
  get("/api/collab/session").then(cs=>{const se=cs&&cs.session;H.session=se&&se.name?String(se.name):"";upd();});
  get("/api/terminals").then(te=>{
    const tn=len(te.terminals);
    Object.assign(T.terminal,{needs:false,sub:tn?`${pl(tn,"terminal")} open.`:"Nothing running."});upd();});
}
const esc=s=>s.replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"})[c]);
const tile=t=>`<a class="tile${t.needs?" needs":""}" href="${t.href}" data-tile="${t.id}">
  <span class="tile-ic" style="color:${t.c}">${TI[t.icon]}</span>
  <span class="tile-txt"><span class="tile-title">${esc(t.title)}</span><span class="tile-sub">${esc(t.sub)}</span></span>
  <span class="tile-go" aria-hidden="true">${TI["arrow-right"]}</span></a>`;
const NAME="Oba";
function greeting(){const hr=new Date().getHours();return hr<5?"Good night":hr<12?"Good morning":hr<17?"Good afternoon":"Good evening";}
function hero(){const el=document.createElement("section");el.className="hero";
  el.innerHTML=`<h1 class="hero-h"><span class="hero-hi">${greeting()}, ${NAME}.</span><br>What should the <em>fleet do?</em></h1>
  <p class="hero-sub">Checking…</p>`;return el;}
function mount(){document.querySelector(".tiles")?.remove();
  const r=(location.hash.slice(1)||"home").split("/")[0];if(r!=="home")return;
  document.getElementById("page").append(hero());
  /* urgent tiles first inside each column */
  const el=document.createElement("nav");el.className="tiles";el.setAttribute("aria-label","Quick links");
  document.getElementById("page").append(el);paint();live();}
function paint(){const el=document.querySelector(".tiles");if(!el)return;
  const col=s=>TILES.filter(t=>t.side===s).sort((a,b)=>(b.needs|0)-(a.needs|0)).map(tile).join("");
  el.innerHTML=`<div class="tiles-col l">${col("l")}</div><div class="tiles-col r">${col("r")}</div>`;}
document.addEventListener("page:render",mount);mount();
})();
