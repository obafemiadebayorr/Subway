/* Home composer panel: opening the composer on Home shows a white panel (90% wide, 80% tall) over a wallpaper.
   Top: chats from the provider picked in the panel strip (independent of the composer's provider). Body: blank for now. Bottom: provider strip, composer sits just above it.
   Collab / Terminal / Files / Approvals collapse to icons in a left rail on the wallpaper.
   Wallpaper: set --hp-wallpaper on :root to use an image. */
window.WALLPAPERS={default:"",dusk:"linear-gradient(135deg,#2b1f33,#4a2c4f 55%,#7a4a5a)",ocean:"linear-gradient(135deg,#0f2430,#1d4352 55%,#2f6a73)",forest:"linear-gradient(135deg,#17241c,#24402c 55%,#3d5a3a)",graphite:"linear-gradient(135deg,#1a1a1c,#2a2a2e 55%,#3a3a40)"};
window.applyWallpaper=v=>{const r=document.documentElement.style,W=window.WALLPAPERS;v=String(v||"default");if(v.startsWith("custom:")){r.setProperty("--hp-wallpaper",`url("${v.slice(7).replace(/["\\)]/g,"")}")`);}else if(W[v])r.setProperty("--hp-wallpaper",W[v]);else r.removeProperty("--hp-wallpaper");};
fetch("/api/settings",{cache:"no-store"}).then(r=>r.json()).then(j=>window.applyWallpaper((j.values||{}).hud_wallpaper)).catch(()=>{});
(function(){
const CSS=`
.hp{position:fixed;inset:0;z-index:45;background:var(--hp-wallpaper,linear-gradient(135deg,#1f2433 0%,#2c3446 55%,#3a3550 100%)) center/cover no-repeat,
  radial-gradient(120% 90% at 15% 10%,#E9E4FB 0,transparent 55%),radial-gradient(90% 80% at 90% 90%,#F6E7C9 0,transparent 60%),#EEF0EA;
  animation:hp-in 220ms cubic-bezier(.23,1,.32,1)}
.hp[hidden]{display:none}
@keyframes hp-in{from{opacity:0}to{opacity:1}}
.hp-slot{position:absolute;right:16px;top:12px;font-size:11px;letter-spacing:.04em;color:rgba(17,17,17,.45);pointer-events:none}
.hp-panel{position:absolute;left:12px;right:12px;top:72px;bottom:calc(24px + var(--h) + 16px + var(--hp-ch,200px) + 12px);display:flex;flex-direction:column;
  background:#fff;border-radius:16px;box-shadow:0 1px 2px rgba(17,17,17,.06),0 24px 64px -24px rgba(17,17,17,.28);overflow:hidden;
  animation:hp-up 260ms cubic-bezier(.23,1,.32,1)}
@keyframes hp-up{from{transform:translateY(12px) scale(.985);opacity:0}to{transform:none;opacity:1}}
/* tab bar: browser-style tabs; the selected tab joins the white body with curved feet */
.hp-tbar{display:flex;align-items:flex-end;gap:4px;background:#F1F0EA;padding:8px 6px 0;flex-shrink:0}
.hp-tabs{flex:1;min-width:0;display:flex;align-items:flex-end;padding:0 10px;overflow-x:auto;overflow-y:hidden;scrollbar-width:none}
.hp-tabs::-webkit-scrollbar{display:none}
.hp-tab{position:relative;display:flex;align-items:center;gap:8px;flex:0 0 150px;height:38px;padding:0 14px;border:0;border-radius:10px 10px 0 0;
  background:transparent;font:inherit;font-size:13px;color:var(--muted);cursor:pointer;text-align:left;transition:background 150ms,color 150ms}
.hp-tab svg{width:16px;height:16px;flex-shrink:0}
.hp-tab span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hp-tab:hover{color:var(--ink);background:rgba(255,255,255,.55)}
.hp-tab[aria-selected="true"]{background:#fff;color:var(--ink);font-weight:500;z-index:1}
.hp-tab[aria-selected="true"]::before,.hp-tab[aria-selected="true"]::after{content:"";position:absolute;bottom:0;width:10px;height:10px;pointer-events:none}
.hp-tab[aria-selected="true"]::before{left:-10px;background:radial-gradient(circle at 0 0,transparent 9.5px,#fff 10px)}
.hp-tab[aria-selected="true"]::after{right:-10px;background:radial-gradient(circle at 100% 0,transparent 9.5px,#fff 10px)}
.hp-tab.live svg{color:var(--sky-ink)}
.hp-tab .hp-pin{font-size:11px;color:var(--sky-ink);opacity:.8}
.hp-tab .hp-badge{font-size:10px;font-weight:600;color:var(--ochre);border:1px solid var(--ochre);border-radius:6px;padding:0 4px;line-height:16px}
.hp-link{font-size:.7rem;padding:0 .35rem;border-radius:999px;border:1px solid currentColor;opacity:.75;margin-left:.25rem;cursor:pointer;white-space:nowrap}.hp-link:hover{opacity:1}
.hp-tact{display:grid;place-items:center;flex-shrink:0;width:34px;height:34px;margin-bottom:4px;border:0;border-radius:10px;background:transparent;color:var(--muted);cursor:pointer}
.hp-tact:hover{background:rgba(255,255,255,.7);color:var(--ink)}
.hp-tab:focus-visible,.hp-tact:focus-visible,.hp-prov:focus-visible,.hp-ic:focus-visible,.hp-scope:focus-visible,.hp-menu button:focus-visible{outline:2px solid var(--ochre);outline-offset:-2px}
.hp-tabs-empty{font-size:13px;color:var(--muted);padding:0 6px 12px}
.hp-body{position:relative;flex:1;min-height:0}
.hp-frame{position:absolute;inset:0;width:100%;height:100%;border:0;background:var(--bg,#F3F4EE)}
.hp-frame[hidden]{display:none}
.hp-chat{position:absolute;inset:0;overflow-y:auto;padding:20px 16px 24px;display:flex;flex-direction:column;gap:14px}
.hp-chat[hidden]{display:none}
.hp-msg.user.pending{opacity:.7}
.md p{margin:0 0 10px}.md p:last-child{margin-bottom:0}
.md .md-h{font-weight:600;margin:16px 0 6px;letter-spacing:-.01em}.md .md-h1{font-size:17px}.md .md-h2{font-size:15.5px}.md .md-h3{font-size:14px}
.md .md-h:first-child{margin-top:0}
.md ul,.md ol{margin:0 0 10px;padding-left:20px}.md li{margin:3px 0}.md li.d1{margin-left:18px}.md li.d2{margin-left:36px}.md li.d3{margin-left:54px}
.md li::marker{color:var(--muted)}
.md code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.88em;background:#F3F2EC;border:1px solid #ECEBE4;border-radius:5px;padding:1px 5px}
.md pre{margin:0 0 10px;background:#F7F6F1;border:1px solid #ECEBE4;border-radius:10px;padding:10px 12px;overflow-x:auto;white-space:pre}
.md pre code{background:none;border:0;padding:0;font-size:12.5px}
.md strong{font-weight:600}.md a{color:var(--sky-ink)}
.md blockquote{margin:0 0 10px;padding:2px 12px;border-left:3px solid #E4E2D8;color:var(--muted)}
.md hr{border:0;height:1px;background:#EEF0EA;margin:14px 0}
.md .md-tw{overflow-x:auto;margin:0 0 10px}.md table{border-collapse:collapse;font-size:13px}.md th,.md td{border-bottom:1px solid #EEF0EA;padding:6px 10px;text-align:left;white-space:nowrap}.md th{font-weight:600}
.hp-msg.assistant{white-space:normal}
.hp-steps{align-self:flex-start;list-style:none;margin:0;padding:10px 14px;border:1px solid #EEF0EA;border-radius:12px;background:#FCFCFA;display:flex;flex-direction:column;gap:8px;min-width:min(320px,100%);max-width:min(560px,100%);font-size:13px}
.hp-steps li{display:grid;grid-template-columns:16px 1fr;column-gap:10px;align-items:center}
.hp-steps .hp-si{display:grid;place-items:center;width:16px;height:16px}
.hp-steps .hp-sl{color:var(--ink)}.hp-steps .st-pending .hp-sl{color:var(--muted)}.hp-steps .st-error .hp-sl{color:#B42318}
.hp-si-dot{width:6px;height:6px;border-radius:50%;background:#D5D3CA}
.hp-si-spin{width:14px;height:14px;animation:hp-spin .8s linear infinite}.hp-si-spin circle{stroke:#E4E2D8}.hp-si-spin path{stroke:var(--sky-ink)}
@keyframes hp-spin{to{transform:rotate(360deg)}}
.hp-si-ok{width:14px;height:14px;color:var(--sky-ink)}.hp-si-ok path{stroke-dasharray:16;stroke-dashoffset:16;animation:hp-draw .3s cubic-bezier(.23,1,.32,1) .05s forwards}
@keyframes hp-draw{to{stroke-dashoffset:0}}
.hp-si-err{width:14px;height:14px;color:#B42318}
.hp-slog{grid-column:2;margin-top:4px;font:12px/20px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--muted)}
.hp-slog.fade{-webkit-mask-image:linear-gradient(to bottom,rgba(0,0,0,.25),#000 70%);mask-image:linear-gradient(to bottom,rgba(0,0,0,.25),#000 70%)}
.hp-slog div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;animation:hp-line .24s cubic-bezier(.23,1,.32,1)}
@keyframes hp-line{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.hp-steps.folded{flex-direction:row;align-items:center;gap:8px;color:var(--muted);padding:6px 12px;min-width:0}
.hp-steps.folded svg{width:14px;height:14px;color:var(--sky-ink)}
@media (prefers-reduced-motion:reduce){.hp-si-spin,.hp-slog div{animation:none}.hp-si-ok path{animation:none;stroke-dashoffset:0}}
.cmp-pic{width:16px;height:16px;flex-shrink:0;object-fit:contain;border-radius:4px}
.cmp-apps{flex:1;min-width:0;display:flex;align-items:center;gap:10px}
.cmp-apps[hidden],.cmp-head-ic[hidden],.cmp-head-txt[hidden]{display:none}
.cmp-apps-ics{display:flex;align-items:center;gap:10px;min-width:0;overflow:hidden}
.cmp-apps-ics img{width:18px;height:18px;object-fit:contain;flex-shrink:0}
.cmp-apps-go{display:flex;align-items:center;gap:6px;flex-shrink:0;border:0;background:transparent;font:inherit;font-size:13px;color:var(--muted);cursor:pointer;padding:4px 6px;border-radius:8px}
.cmp-apps-go:hover{color:var(--ink);background:rgba(17,17,17,.05)}
.cmp-apps-go .cmp-ic{display:grid}
@media (max-width:639px){.cmp-apps-go{font-size:0;gap:0}.cmp-apps-go .cmp-ic{font-size:13px}}
.cmp-chatto{display:flex;align-items:center;gap:8px;margin:0 4px 6px;padding:6px 10px;border-radius:10px;background:var(--sky-chip);color:var(--sky-ink);font-size:13px;min-width:0}
.cmp-chatto[hidden]{display:none}
.cmp-chatto b{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.cmp-chatto-l,.cmp-chatto-src{opacity:.7;flex-shrink:0}
.cmp-chatto-x{margin-left:auto;flex-shrink:0;border:0;background:transparent;color:inherit;font:inherit;font-size:12px;text-decoration:underline;cursor:pointer;padding:2px 4px;border-radius:6px}
.cmp-chatto-x:focus-visible{outline:2px solid var(--ochre)}
.hp-msg{max-width:min(720px,88%);font-size:14px;line-height:1.55;white-space:pre-wrap;overflow-wrap:anywhere;color:var(--ink)}
.hp-msg.user{align-self:flex-end;background:var(--sky-chip);color:var(--sky-ink);padding:10px 14px;border-radius:14px 14px 4px 14px}
.hp-msg.assistant{align-self:flex-start;padding:2px 2px}
.hp-chat-note{margin:auto;font-size:13px;color:var(--muted);text-align:center}
.hp-seg{display:flex;align-items:center;gap:10px;margin:14px 0;font-size:12px;color:var(--muted,#888);text-align:center}.hp-seg::before,.hp-seg::after{content:"";flex:1;height:1px;background:currentColor;opacity:.3}
@media (min-width:768px){.hp-chat{padding:28px max(24px,calc((100% - 820px)/2)) 32px}}
.hp-tab.page{padding-right:6px}
.hp-tx,.hp-tmore{display:grid;place-items:center;margin-left:auto;width:22px;height:22px;border:0;border-radius:6px;background:transparent;color:inherit;cursor:pointer;flex-shrink:0;opacity:0}
.hp-tab:hover .hp-tx,.hp-tab:focus-within .hp-tx,.hp-tab[aria-selected="true"] .hp-tx,
.hp-tab:hover .hp-tmore,.hp-tab:focus-within .hp-tmore,.hp-tab[aria-selected="true"] .hp-tmore{opacity:1}
.hp-tx:hover,.hp-tmore:hover{background:#ECEBE4}
.hp-tx svg,.hp-tmore svg{width:12px;height:12px}
.hp-strip{display:flex;align-items:center;justify-content:flex-start;gap:6px;padding:10px;overflow-x:auto;scrollbar-width:none;flex-shrink:0}
.hp-strip::-webkit-scrollbar{display:none}
.hp-scope{display:flex;align-items:center;gap:6px;flex-shrink:0;height:28px;padding:0 10px;border:1px solid var(--hair);border-radius:8px;background:var(--sky-chip);font:inherit;font-size:12px;font-weight:500;color:var(--sky-ink);cursor:pointer}
.hp-scope:hover{background:var(--sky-chip-hi)}
.hp-prov{display:flex;align-items:center;gap:6px;flex-shrink:0;height:28px;padding:0 10px;border:1px solid var(--hair);border-radius:8px;
  background:#fff;font:inherit;font-size:12px;font-weight:500;color:var(--ink);cursor:pointer;transition:background 150ms,border-color 150ms}
.hp-prov:hover{background:#FAFAF7}
.hp-prov em{font-style:normal;font-weight:400;color:var(--muted)}
.hp-prov[aria-pressed="true"]{background:#111;border-color:#111;color:#fff}
.hp-prov[aria-pressed="true"] em{color:#9A9A9A}
.hp-menu,.hp-hover{position:fixed;z-index:60;background:#fff;border:1px solid var(--hair);border-radius:12px;box-shadow:0 12px 40px -16px rgba(17,17,17,.35);padding:6px;min-width:220px;color:var(--ink)}
.hp-menu[hidden],.hp-hover[hidden]{display:none}
.hp-menu button,.hp-menu .hp-mi{display:flex;align-items:center;gap:8px;width:100%;border:0;background:transparent;font:inherit;font-size:13px;text-align:left;padding:8px 10px;border-radius:8px;color:inherit;cursor:pointer}
.hp-menu button:hover,.hp-menu .hp-mi:hover{background:var(--sky-chip);color:var(--sky-ink)}
.hp-menu .danger{color:#B42318}.hp-menu .danger:hover{background:#FEECEC;color:#B42318}
.hp-sep{height:1px;background:#EEF0EA;margin:4px 2px}
.hp-hover{min-width:260px;max-width:340px;padding:10px 12px;font-size:12.5px}
.hp-hover .t{font-size:13px;font-weight:600;margin-bottom:6px;white-space:normal}
.hp-hover .r{display:flex;align-items:center;gap:8px;color:var(--muted);margin-top:4px}
.hp-hover svg{width:14px;height:14px;color:var(--sky-ink);flex-shrink:0}
/* nav mirrored on the right (tablet and up; phones keep the bottom dock) */
.hp-nav{position:absolute;display:none;gap:8px}
.hp-ic.on{background:var(--sky-ink);color:#fff}
/* section icons */
.hp-ic{position:relative;display:grid;place-items:center;width:40px;height:40px;border-radius:var(--radius);background:var(--sky-chip);color:var(--sky-ink);
  border:0;cursor:pointer;text-decoration:none;transition:transform 150ms,background 150ms,color 150ms}
.hp-ic:hover{background:var(--sky-chip-hi);transform:translateY(-1px)}
.hp-ic svg{width:18px;height:18px}
.hp-ic.needs::after{content:"";position:absolute;top:6px;right:6px;width:7px;height:7px;border-radius:50%;background:var(--ochre);box-shadow:0 0 0 2px var(--sky-chip)}
/* rail on the wallpaper (phones: a row above the panel) */
.hp-rail{position:absolute;top:16px;left:50%;transform:translateX(-50%);display:flex;gap:8px}
/* the composer moves into the panel, above the provider strip */
body.hp-on .cmp{width:calc(100vw - 24px);bottom:calc(24px + var(--h) + 16px)}
@media (min-width:768px){
  .hp-panel{left:max(5vw,64px);right:max(5vw,64px);top:3vh;bottom:calc(16px + var(--hp-ch,200px) + 12px)}
  .hp-rail{top:50%;left:max(12px,calc(2.5vw - 20px));transform:translateY(-50%);flex-direction:column}
  body.hp-on .cmp{width:var(--hp-cw,min(960px,calc(90vw - 128px)));bottom:16px}
  .hp-nav{display:flex;top:50%;right:max(12px,calc(2.5vw - 20px));transform:translateY(-50%);flex-direction:column}
  body.hp-on .dock{display:none}
  .hp-tab{flex-basis:200px}.hp-strip{padding:10px 14px}}`;
document.head.append(Object.assign(document.createElement("style"),{id:"hp-css",textContent:CSS}));
if(!document.querySelector('link[data-hp-cp]')){const l=document.createElement("link");l.rel="stylesheet";l.href="checkpoint.css";l.dataset.hpCp="1";document.head.appendChild(l);}

const ICONS=[["approvals","Approvals","#workers","check"],["files","Files","#landing","git-merge"],
  ["collab","Collab","#collab/sessions","users-three"],["terminal","Terminal","#terminal","terminal-window"]];
const icons=()=>ICONS.map(([id,t,href,ic])=>`<a class="hp-ic" href="${href}" data-hp-ic="${id}" title="${t}" aria-label="${t}">${(typeof TI!=="undefined"&&TI[ic])||""}</a>`).join("");
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"})[c]);

const hp=document.createElement("div");hp.className="hp";hp.hidden=true;
hp.innerHTML=`<span class="hp-slot" aria-hidden="true"></span>
<nav class="hp-rail" aria-label="Sections">${icons()}</nav>
<nav class="hp-nav" aria-label="Pages"></nav>
<section class="hp-panel hp-keep" role="region" aria-label="Composer panel">
  <div class="hp-tbar"><div class="hp-tabs" role="tablist" aria-label="Chats"></div><button type="button" class="hp-tact" data-hp-new title="New chat" aria-label="New chat"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg></button></div>
  <div class="hp-body"></div>
  <div class="hp-strip" role="group" aria-label="Providers"></div>
</section>
<div class="hp-menu" id="hp-menu" hidden></div>
<div class="hp-hover" id="hp-hover" hidden></div>`;
document.body.append(hp);
const tabsEl=hp.querySelector(".hp-tabs"),stripEl=hp.querySelector(".hp-strip"),menuEl=hp.querySelector("#hp-menu"),hoverEl=hp.querySelector("#hp-hover");
hoverEl.style.pointerEvents="none";

const SRC={claude:"Claude",pi:"Pi",opencode:"OpenCode",antigravity:"Antigravity",copilot:"Copilot",cursor:"Cursor",devin:"Devin"};
const BUBBLE='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true"><path d="M4 5h16v11H9l-5 4z"/></svg>';
const PIN='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true"><path d="M14 4l6 6-2 1-2 5 3 3-1 1-4-4-4 4-1-1 3-3-5 2-1-2 6-6z"/></svg>';
const FOLDER='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true"><path d="M3 7h6l2 2h10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M3 7V6a2 2 0 0 1 2-2h4l2 2"/></svg>';
const CLOCK='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>';
const DOTS='<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>';
let chats=[],chatsAt=0,picked=null,pprov=(()=>{try{return localStorage.getItem("hp-prov")||"claude";}catch(_){return"claude";}})();
let coverage={},coverageAt=0,hudProject="",hudProjectAt=0,hoverTimer=0,hoverGuard=0,showArchived=false;
const C=()=>window.SubwayComposer;

function jget(k,d){try{const v=JSON.parse(localStorage.getItem(k)||"null");return v==null?d:v;}catch(_){return d;}}
function jset(k,v){try{localStorage.setItem(k,JSON.stringify(v));}catch(_){}}
function closedMap(){const m=jget("hp-closed",{});return m&&typeof m==="object"?m:{};}
function closedFor(p){const a=closedMap()[p];return Array.isArray(a)?a:[];}
function setClosedFor(p,arr){const m=closedMap();m[p]=arr;jset("hp-closed",m);}
function isClosed(c){const rec=closedFor(c.source).find(x=>x&&x.id===c.id);if(!rec)return false;const up=Number(c.updated||0);if(rec.closedAt&&up>Number(rec.closedAt))return false;return true;}
async function saveHomePrefs(patch){try{await fetch("/api/home_prefs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(patch)});}catch(_){}}
function closeTab(c){const a=closedFor(c.source).filter(x=>x.id!==c.id);a.push({id:c.id,closedAt:Date.now()/1000,title:chatTitle(c)});setClosedFor(c.source,a);saveHomePrefs({closed:closedMap()});if(picked===c.id)picked=null;renderStrip();renderTabs();showBody();}
function unclose(id,src){setClosedFor(src||pprov,closedFor(src||pprov).filter(x=>x.id!==id));saveHomePrefs({closed:closedMap()});renderStrip();renderTabs();}
function scopeMap(){const m=jget("hp-scope",{});return m&&typeof m==="object"?m:{};}
function getScope(p){const s=scopeMap()[p];return s&&typeof s==="object"?s:{mode:"all"};}
async function setScope(p,s){const m=scopeMap();m[p]=s;jset("hp-scope",m);saveHomePrefs({scopes:m});chatsAt=0;await loadChats(true);renderStrip();renderTabs();showBody();}
function chatTitle(c){const meta=c.meta||{};return String(meta.title||c.title||(c.aliases&&c.aliases[0]&&"@"+String(c.aliases[0]).replace(/^@/,""))||c.tab_name||"Untitled");}
function chatMeta(c){return c.meta||{};}
async function loadCoverage(){if(Date.now()-coverageAt<60000)return;coverageAt=Date.now();try{coverage=await(await fetch("/api/delivery_coverage",{cache:"no-store"})).json();}catch(_){ }}
async function loadHudProject(){if(Date.now()-hudProjectAt<30000)return;hudProjectAt=Date.now();try{const d=await(await fetch("/api/status?view=next",{cache:"no-store"})).json();hudProject=String((d.settings&&d.settings.project_dir)||(d.project_dir)||"");}catch(_){}}
async function loadChats(force=false){if(!force&&Date.now()-chatsAt<30000)return;chatsAt=Date.now();try{const d=await(await fetch("/api/chats?limit=2000",{cache:"no-store"})).json();chats=(Array.isArray(d)?d:(d.threads||d.chats||[])).filter(c=>!c.worker&&!c.subagent);}catch(_){chats=[];}}
let links=[],linksAt=0;async function loadLinks(){if(Date.now()-linksAt<15000)return;linksAt=Date.now();try{const r=await fetch("/api/conversation/links",{cache:"no-store"});const d=await r.json();links=Array.isArray(d.links)?d.links:[];}catch(_){}}
function linkBadges(id){const out=links.filter(l=>l.from===id);if(!out.length)return"";return out.map(l=>`<span class="hp-link" data-hp-jump="${esc(l.to)}" data-hp-jprov="${esc(l.to_provider)}" title="Same conversation continues on ${esc(SRC[l.to_provider]||l.to_provider)}">↔ ${esc((SRC[l.to_provider]||l.to_provider).slice(0,10))}</span>`).join("");}
function chatInScope(c){const s=getScope(c.source||pprov);if(s.mode==="project"&&hudProject)return String(c.project||"")===hudProject;if(s.mode==="folder"&&s.folder)return String(c.project||"")===String(s.folder);if(s.mode==="open")return !!(c.open||c.live||c.is_open);return true;}
function visibleChats(){let list=chats.filter(c=>c.source===pprov);list=list.filter(c=>!isClosed(c)&&(showArchived?!!chatMeta(c).archived:!chatMeta(c).archived));list=list.filter(chatInScope);list.sort((a,b)=>{const ap=chatMeta(a).pinned?1:0,bp=chatMeta(b).pinned?1:0;if(bp!==ap)return bp-ap;return (b.open|0)-(a.open|0)||(Number(b.updated)||0)-(Number(a.updated)||0);});return list;}
const srcs=()=>{const n={};for(const c of chats){if(isClosed(c)||(!showArchived&&chatMeta(c).archived))continue;if(!chatInScope(c))continue;n[c.source]=(n[c.source]||0)+1;}if(!n[pprov])n[pprov]=0;const ids=[...Object.keys(SRC),...Object.keys(n).filter(k=>!SRC[k])];return ids.filter(k=>n[k]||k===pprov).map(k=>({id:k,label:SRC[k]||k[0].toUpperCase()+k.slice(1),n:n[k]||0}));};
async function postAction(id,action,value){const r=await fetch("/api/thread_action",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id,action,value:value==null?"":value})});const d=await r.json().catch(()=>({}));if(!r.ok||d.ok===false)throw new Error(d.error||d.message||`HTTP ${r.status}`);return d;}
function hideMenu(){menuEl.hidden=true;menuEl.innerHTML="";} function hideHover(){clearTimeout(hoverTimer);clearInterval(hoverGuard);hoverTimer=0;hoverGuard=0;hoverEl.hidden=true;}
function openInNewTab(c){openPage("threads/"+encodeURIComponent(c.id));}
function showHover(ev,c){if(!menuEl.hidden)return;clearTimeout(hoverTimer);clearInterval(hoverGuard);const x=ev.clientX,y=ev.clientY;hoverTimer=setTimeout(()=>{if(!menuEl.hidden||!tabsEl.matches(":hover"))return;const el=document.elementFromPoint?.(x,y);if(el&&!tabsEl.contains(el))return;const t=chatTitle(c),proj=c.project||c.project_dir||"";const when=Number(c.updated||0);const ds=when?new Date(when*1000).toLocaleString():"Unknown";hoverEl.innerHTML=`<div class="t">${esc(t)}</div>${proj?`<div class="r">${FOLDER}<span>${esc(proj)}</span></div>`:""}<div class="r">${CLOCK}<span>Updated ${esc(ds)}</span></div>`;hoverEl.style.left=Math.min(x+12,innerWidth-360)+"px";hoverEl.style.top=Math.min(y+14,innerHeight-140)+"px";hoverEl.hidden=false;hoverGuard=setInterval(()=>{if(!tabsEl.matches(":hover")||!menuEl.hidden)hideHover();},120);},500);}

/* Working mode: links to other pages open as tabs here (an embedded copy of that page) instead of leaving Home. */
const PAGE_T={threads:"Threads",devices:"Devices",workers:"Workers",settings:"Settings",usage:"Usage",landing:"Landing",help:"Help",collab:"Collab",terminal:"Terminal"};
const WIN='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18"/></svg>';
const XS='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
const pageLabel=r=>PAGE_T[r]||r[0].toUpperCase()+r.slice(1);
const pageIcon=r=>{const a=document.querySelector(`.mseg a[href="#${r}"] svg`);return a?a.outerHTML:WIN;};
let pages=[],sel={kind:"chat"};
const frameSrc=f=>location.pathname+"?embed=1#"+f;
function openPage(full){const r=full.split("/")[0];if(!r||r==="home"){sel={kind:"chat"};renderTabs();showBody();return;}let p=pages.find(x=>x.r===r);if(!p){p={r,full};pages.push(p);}else if(full!==p.full){p.full=full;if(p.frame)p.frame.src=frameSrc(full);}sel={kind:"page",r};renderTabs();showBody();}
function closePage(r){const i=pages.findIndex(p=>p.r===r);if(i<0)return;pages[i].frame?.remove();pages.splice(i,1);if(sel.kind==="page"&&sel.r===r){const n=pages[i]||pages[i-1];sel=n?{kind:"page",r:n.r}:{kind:"chat"};}renderTabs();showBody();}
/* The selected chat's recent turns (GET /api/chat_read: user text + each reply's final text), rendered as markdown. */
const chatCache=new Map();let chatEl=null;
function inl(t){return t.split(/(`[^`\n]+`)/).map((p,i)=>i%2?`<code>${p.slice(1,-1)}</code>`:p
  .replace(/\*\*([^*\n]+)\*\*/g,"<strong>$1</strong>").replace(/(^|[^*\w])\*([^*\s][^*\n]*?)\*(?!\w)/g,"$1<em>$2</em>")
  .replace(/\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g,'<a href="$2" target="_blank" rel="noopener">$1</a>')).join("");}
function md(src){const L=esc(src).split("\n"),out=[];let para=[];
  const flush=()=>{if(para.length){out.push(`<p>${para.map(inl).join("<br>")}</p>`);para=[];}};
  for(let i=0;i<L.length;i++){const ln=L[i];
    if(/^\s*```/.test(ln)){flush();const code=[];i++;while(i<L.length&&!/^\s*```/.test(L[i]))code.push(L[i++]);out.push(`<pre><code>${code.join("\n")}</code></pre>`);continue;}
    if(/^\s*\|/.test(ln)&&i+1<L.length&&/^\s*\|?\s*:?-{2,}/.test(L[i+1])){flush();const row=r=>r.trim().replace(/^\||\|$/g,"").split("|").map(c=>inl(c.trim()));
      const head=row(ln);i+=2;const body=[];while(i<L.length&&/^\s*\|/.test(L[i]))body.push(row(L[i++]));i--;
      out.push(`<div class="md-tw"><table><thead><tr>${head.map(c=>`<th>${c}</th>`).join("")}</tr></thead><tbody>${body.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);continue;}
    let m=ln.match(/^(#{1,6})\s+(.*)$/);if(m){flush();out.push(`<p class="md-h md-h${Math.min(m[1].length,3)}">${inl(m[2])}</p>`);continue;}
    if(/^\s*(-{3,}|\*{3,})\s*$/.test(ln)){flush();out.push("<hr>");continue;}
    if(/^\s*([-*+]|\d+[.)])\s+/.test(ln)){flush();const items=[];let ordered=/^\s*\d/.test(ln);
      while(i<L.length&&(m=L[i].match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/))){items.push([Math.min(3,Math.floor(m[1].length/2)),m[3]]);i++;}i--;
      out.push(`<${ordered?"ol":"ul"}>${items.map(([d,t])=>`<li${d?` class="d${d}"`:""}>${inl(t)}</li>`).join("")}</${ordered?"ol":"ul"}>`);continue;}
    if(/^&gt;\s?/.test(ln)){flush();out.push(`<blockquote>${inl(ln.replace(/^&gt;\s?/,""))}</blockquote>`);continue;}
    if(!ln.trim()){flush();continue;}
    para.push(ln);}
  flush();return out.join("");}
/* Feedback after a send, in the spirit of KokonutUI ai-loading: a short step list (deliver → wait → reply),
   the running step shows its latest log lines, and the list folds into one line once the reply lands. */
const pend=new Map();
const SI={pending:'<span class="hp-si-dot"></span>',
  running:'<svg class="hp-si-spin" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6.25" stroke-width="1.5"/><path d="M8 1.75a6.25 6.25 0 0 1 6.25 6.25" stroke-width="1.5" stroke-linecap="round"/></svg>',
  done:'<svg class="hp-si-ok" viewBox="0 0 16 16" fill="none"><path d="M3.5 8.5 6.5 11.5 12.5 4.5" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  error:'<svg class="hp-si-err" viewBox="0 0 16 16" fill="none"><path d="M4.5 4.5l7 7m0-7-7 7" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/></svg>'};
function stepsHtml(p){if(p.folded)return`<div class="hp-steps folded">${SI.done}<span>${esc(p.folded)}</span></div>`;
  return`<ol class="hp-steps" role="status" aria-label="Progress">${p.steps.map(s=>{const lines=(s.detail||[]).slice(-3);
    return`<li class="st-${s.status}"><span class="hp-si">${SI[s.status]}</span><span class="hp-sl">${esc(s.label)}</span>${s.status!=="pending"&&lines.length?`<div class="hp-slog${(s.detail||[]).length>2?" fade":""}">${lines.map(l=>`<div>${esc(l)}</div>`).join("")}</div>`:""}</li>`;}).join("")}</ol>`;}
const lastA=t=>{for(let k=t.length-1;k>=0;k--)if(t[k].role==="assistant")return t[k].text;return"";};
const secs=p=>Math.round((Date.now()-p.at)/1000);
const CP_WAVE=`<span class="hp-cp-wave" aria-hidden="true"><svg preserveAspectRatio="none" viewBox="0 0 120 6"><path d="M0 3 Q3 0 6 3 T12 3 T18 3 T24 3 T30 3 T36 3 T42 3 T48 3 T54 3 T60 3 T66 3 T72 3 T78 3 T84 3 T90 3 T96 3 T102 3 T108 3 T114 3 T120 3" fill="none" stroke="currentColor" stroke-width="1" vector-effect="non-scaling-stroke"/></svg></span>`;
function cpWho(id){const c=(chats||[]).find(x=>x.id===id||(x.aliases||[]).some(a=>String(a).replace(/^@/,"")===String(id).replace(/^@/,"")));const prov=c&&(c.source||c.provider)||"";const name=prov?prov[0].toUpperCase()+prov.slice(1):(String(id).slice(0,8)||"?");return name;}
function cpTime(sec){const d=new Date(sec*1000);return d.toLocaleString([], {weekday:"short",month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"});}
function cpHtml(cp){const t=cpTime(cp.at);const lbl=`<span class="hp-cp-prov">${esc(cpWho(cp.from))}</span> · ${esc(t)} <span class="hp-cp-arrow">→</span> <span class="hp-cp-prov">${esc(cpWho(cp.to))}</span> · ${esc(t)}`;return `<div class="hp-cp" role="separator" aria-label="${esc(`Principal swapped from ${cpWho(cp.from)} to ${cpWho(cp.to)} at ${t}`)}" title="${esc(cp.reason||"Principal swapped")}">${CP_WAVE}<span class="hp-cp-label">${lbl}</span>${CP_WAVE}</div>`;}
function renderChat(){const body=hp.querySelector(".hp-body");if(!chatEl){chatEl=document.createElement("div");chatEl.className="hp-chat";chatEl.setAttribute("aria-live","polite");body.prepend(chatEl);}
  chatEl.hidden=sel.kind!=="chat";if(sel.kind!=="chat")return;
  if(!picked){chatEl.innerHTML=`<p class="hp-chat-note">Pick a chat above.</p>`;return;}
  const id=picked,c=chatCache.get(id),p=pend.get(id);
  if(!c){chatEl.innerHTML=`<p class="hp-chat-note">Loading chat…</p>`;chatCache.set(id,{loading:true});
    fetch(`/api/chat_read?id=${encodeURIComponent(id)}&source=${encodeURIComponent(pprov)}&turns=30`,{cache:"no-store"})
      .then(async r=>{const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.error||`HTTP ${r.status}`);return d;})
      .then(d=>{chatCache.set(id,{d,at:Date.now()});const cp=d&&d.conversation&&d.conversation.current_provider;if(cp)C()?.followProvider?.(id,cp);},e=>chatCache.set(id,{err:e.message||String(e)}))
      .finally(()=>{if(picked===id&&sel.kind==="chat")renderChat();});return;}
  if(c.loading)return;
  if(c.err){chatEl.innerHTML=`<p class="hp-chat-note">Couldn't load this chat: ${esc(c.err)}</p>`;chatCache.delete(id);return;}
  const t=c.d.turns||[];const near=chatEl.scrollHeight-chatEl.scrollTop-chatEl.clientHeight<80;
  const cps=(c.d.checkpoints||[]).slice();const firstTs=(t.find(m=>m.ts)||{}).ts||0;
  let html="",lastProv="";const conv=c.d.conversation;for(const m of t){if(conv&&m.provider&&lastProv&&m.provider!==lastProv)html+=`<div class="hp-seg">Continued on ${esc(SRC[m.provider]||m.provider)}</div>`;if(m.provider)lastProv=m.provider;while(cps.length&&m.ts&&cps[0].at<=m.ts){const cp=cps.shift();if(!firstTs||cp.at>=firstTs)html+=cpHtml(cp);}
    html+=m.role==="user"?`<div class="hp-msg user">${esc(m.text)}</div>`:`<div class="hp-msg assistant md">${md(m.text)}</div>`;}
  for(const cp of cps)if(!firstTs||cp.at>=firstTs)html+=cpHtml(cp);
  if(p&&!p.folded&&!t.slice(-3).some(m=>m.role==="user"&&m.text.trim()===p.text.trim()))html+=`<div class="hp-msg user pending">${esc(p.text)}</div>`;
  if(p)html+=stepsHtml(p);
  chatEl.innerHTML=html||`<p class="hp-chat-note">No messages in this chat yet.</p>`;
  if(near||p)chatEl.scrollTop=chatEl.scrollHeight;}
function repaint(id){if(picked===id&&sel.kind==="chat"&&!hp.hidden)renderChat();}
async function poll(id){const p=pend.get(id);if(!p||p.done)return;
  try{const r=await fetch(`/api/chat_read?id=${encodeURIComponent(id)}&source=${encodeURIComponent(pprov)}&turns=30`,{cache:"no-store"});const d=await r.json();
    if(r.ok){const t=d.turns||[],la=lastA(t),i=t.map(m=>m.role==="user"&&m.text.trim()===p.text.trim()).lastIndexOf(true);
      chatCache.set(id,{d,at:Date.now()});
      if(i>=0&&p.steps[1].detail.length<99&&!p.seen){p.seen=true;p.steps[1].detail.push(`message is in the chat · ${secs(p)}s`);}
      if((i>=0&&t.slice(i+1).some(m=>m.role==="assistant"))||(la&&la!==p.baseLast&&(d.total||0)>p.baseTotal)){
        p.steps[1].status="done";p.steps[1].label="Agent replied";p.steps[2].status="done";p.done=true;repaint(id);
        setTimeout(()=>{p.folded=`Replied in ${secs(p)}s`;repaint(id);},1400);return;}}
    p.steps[1].detail.push(`checked · ${secs(p)}s · no reply yet`);}
  catch(e){p.steps[1].detail.push(`couldn't check: ${e.message||e}`);}
  if(secs(p)>240){p.steps[1].status="error";p.steps[1].label="No reply after 4 minutes";p.steps[1].detail.push("the chat may be idle or waiting on a permission");p.done=true;repaint(id);return;}
  repaint(id);p.timer=setTimeout(()=>poll(id),secs(p)<60?4000:10000);}
document.addEventListener("composer:sending",e=>{const {chat:id,text,label}=e.detail||{};if(!id)return;const prev=pend.get(id);if(prev)clearTimeout(prev.timer);
  const c=chatCache.get(id),t=c&&c.d?c.d.turns||[]:[];
  pend.set(id,{text,at:Date.now(),baseLast:lastA(t),baseTotal:c&&c.d?c.d.total||0:0,steps:[
    {label:`Sending to ${label}`,status:"running",detail:[]},{label:"Waiting for the reply",status:"pending",detail:[]},{label:"Reply in the panel",status:"pending",detail:[]}]});repaint(id);});
document.addEventListener("composer:sent",async e=>{const {chat:id,method,handoff_chat_id,chat_id,provider}=e.detail||{};
  const resolvedNextId=id;chatCache.delete(id);
  const p=pend.get(id);
  if(p&&resolvedNextId!==id){pend.delete(id);pend.set(resolvedNextId,p);}
  if(!p)return;
  p.steps[0].status="done";p.steps[0].label=resolvedNextId!==id?`Handed off to ${provider||"new provider"}`:"Delivered";
  p.steps[0].detail=[`via ${method||"live delivery"} · ${secs(p)}s`];
  p.steps[1].status="running";p.steps[1].detail=["watching live"];repaint(resolvedNextId);
  watch(resolvedNextId);
  p.timer=setTimeout(()=>{if(!streams.has(resolvedNextId))poll(resolvedNextId);},2500);});
document.addEventListener("composer:send-failed",e=>{const {chat:id,error}=e.detail||{};const p=pend.get(id);if(!p)return;
  p.steps[0].status="error";p.steps[0].label="Not delivered";p.steps[0].detail=[String(error||"unknown error")];p.done=true;repaint(id);});
/* The composer sends to the selected chat tab while one is showing. */
let chatOptOut=null;
function syncTarget(){const c=C();if(!c?.setChat)return;
  const ch=!hp.hidden&&sel.kind==="chat"&&picked&&picked!==chatOptOut?chats.find(x=>x.id===picked):null;
  c.setChat(ch?{id:ch.id,source:ch.source,srcLabel:SRC[ch.source]||ch.source,label:(ch.aliases&&ch.aliases[0]&&"@"+String(ch.aliases[0]).replace(/^@/,""))||ch.tab_name||ch.title||"this chat"}:null);}
document.addEventListener("composer:chat-cleared",()=>{chatOptOut=picked;});
/* Principal swap: an explicit "Make principal" menu click makes a chat the principal of every live work thread.
   POST /api/composer/principal moves the thread's workers (reports follow), open permissions and leases,
   and briefs the new chat. */
let ini=(()=>{try{return JSON.parse(localStorage.getItem("hp-initiator")||"null");}catch(_){return null;}})();
const chatLabel=ch=>(ch.aliases&&ch.aliases[0]&&"@"+String(ch.aliases[0]).replace(/^@/,""))||ch.tab_name||ch.title||ch.id.slice(0,8);
async function liveRoots(){const r=await fetch("/api/status?view=next",{cache:"no-store"});const d=await r.json();
  const live=(d.workers||[]).filter(w=>!/^(completed|failed|stopped|rejected|cancelled|error)$/.test(String(w.status||"")));
  return[...new Set(live.map(w=>w.thread_root).filter(Boolean))];}
/* Live chat stream (GET /api/chat_stream, SSE): steps and the reply arrive as the agent writes them. */
const streams=new Map();
function watch(id){if(streams.has(id)||typeof EventSource==="undefined")return;const p=pend.get(id);if(!p)return;
  const es=new EventSource(`/api/chat_stream?id=${encodeURIComponent(id)}&source=${encodeURIComponent(p.source||pprov||"all")}`);streams.set(id,es);
  let quiet=0;const finish=()=>{clearTimeout(quiet);es.close();streams.delete(id);};
  es.addEventListener("chat",ev=>{const p=pend.get(id);if(!p||p.done)return finish();let d;try{d=JSON.parse(ev.data);}catch(_){return;}
    chatCache.set(id,{d,at:Date.now()});const cp=d.conversation&&d.conversation.current_provider;if(cp)C()?.followProvider?.(id,cp);
    const L=d.live||{},t=d.turns||[];
    const mine=L.user&&p.text.trim().startsWith(L.user.trim().slice(0,60));
    if(mine&&!p.seen){p.seen=true;p.steps[1].detail.push(`message is in the chat · ${secs(p)}s`);}
    (L.activity||[]).forEach(a=>{if(a&&!p.steps[1].detail.includes(a))p.steps[1].detail.push(a);});
    const replied=(mine&&L.text&&L.text.trim())||(lastA(t)&&lastA(t)!==p.baseLast&&(d.total||0)>p.baseTotal);
    if(replied){p.steps[1].status="done";p.steps[1].label="Agent is replying";p.steps[2].status="running";
      clearTimeout(quiet);quiet=setTimeout(()=>{p.steps[1].label="Agent replied";p.steps[2].status="done";p.done=true;repaint(id);finish();
        setTimeout(()=>{p.folded=`Replied in ${secs(p)}s`;repaint(id);},1200);},2500);}
    repaint(id);});
  es.addEventListener("error",()=>{if(es.readyState===2){streams.delete(id);const p=pend.get(id);if(p&&!p.done)p.timer=setTimeout(()=>poll(id),1500);}});}
/* Passive live stream: keeps the open chat panel fresh even when not sent from the composer. */
let passiveEs=null,passiveId="";
function closePassive(){if(passiveEs)passiveEs.close();passiveEs=null;passiveId="";}
function syncPassive(){if(hp.hidden||sel.kind!=="chat"||!picked||streams.has(picked)||typeof EventSource==="undefined"){closePassive();return;}
  if(passiveId===picked&&passiveEs)return;
  closePassive();passiveId=picked;
  const id=picked;
  passiveEs=new EventSource(`/api/chat_stream?id=${encodeURIComponent(id)}&source=${encodeURIComponent(pprov||"all")}`);
  passiveEs.addEventListener("chat",ev=>{let d;try{d=JSON.parse(ev.data);}catch(_){return;}
    const prev=chatCache.get(id),prevT=prev&&prev.d?prev.d.turns:null,prevL=prev&&prev.d?prev.d.live:null;
    chatCache.set(id,{d,at:Date.now()});const cp=d.conversation&&d.conversation.current_provider;if(cp)C()?.followProvider?.(id,cp);
    const changed=(d.turns?d.turns.length:0)!==(prevT?prevT.length:0)||JSON.stringify(d.live?.text??null)!==JSON.stringify(prevL?.text??null);
    if(changed&&picked===id&&sel.kind==="chat"&&!hp.hidden)renderChat();});
  passiveEs.addEventListener("error",()=>{if(passiveEs&&passiveEs.readyState===2)closePassive();});}
function showBody(){const body=hp.querySelector(".hp-body");renderChat();syncTarget();syncPassive();
  for(const p of pages){const on=sel.kind==="page"&&sel.r===p.r;
    if(on&&!p.frame){p.frame=document.createElement("iframe");p.frame.className="hp-frame";p.frame.title=pageLabel(p.r);p.frame.src=frameSrc(p.full);body.append(p.frame);}
    if(p.frame)p.frame.hidden=!on;}}
function renderTabs(){const list=visibleChats();
  const ptabs=pages.map(p=>{const on=sel.kind==="page"&&sel.r===p.r;return`<div class="hp-tab page" role="tab" tabindex="0" data-hp-page="${esc(p.r)}" aria-selected="${String(on)}">${pageIcon(p.r)}<span>${esc(pageLabel(p.r))}</span><button type="button" class="hp-tx" data-hp-close="${esc(p.r)}" aria-label="Close ${esc(pageLabel(p.r))}">${XS}</button></div>`;}).join("");
  const scope=getScope(pprov);const cap=coverage[pprov];const scopeLabel=scope.mode==="project"?"This project":scope.mode==="folder"?(scope.folder||"Folder"):scope.mode==="open"?"Open windows":"All chats";
  const head=`<button type="button" class="hp-scope" data-hp-scope title="Load scope: ${esc(scopeLabel)}">Load: ${esc(scopeLabel)}</button>`;
  if(!list.length){tabsEl.innerHTML=ptabs+head+`<span class="hp-tabs-empty">${chatsAt?`No ${esc(SRC[pprov]||pprov)} chats.`:"Loading chats…"}</span>`;tabsEl.querySelector('[aria-selected="true"]')?.scrollIntoView({block:"nearest",inline:"nearest"});return;}
  if(!list.some(c=>c.id===picked)){picked=list[0].id;if(!hp.hidden)queueMicrotask(showBody);}
  tabsEl.innerHTML=ptabs+head+list.map(c=>{const m=chatMeta(c),n=chatTitle(c),on=sel.kind==="chat"&&c.id===picked;
    const badge=cap==="none"?`<span class="hp-badge" title="Can't be principal">!</span>`:cap==="spool"?`<span class="hp-badge" title="Spool delivery">~</span>`:"";
    return`<button type="button" class="hp-tab${c.open?" live":""}" role="tab" data-hp-tab="${esc(c.id)}" aria-selected="${String(on)}" aria-label="${esc(n)}" data-hp-chat="${esc(c.id)}">${BUBBLE}${m.pinned?`<span class="hp-pin" aria-label="Pinned">${PIN}</span>`:""}${badge}${linkBadges(c.id)}<span>${esc(n.slice(0,80))}</span><button type="button" class="hp-tmore" data-hp-more="${esc(c.id)}" title="More actions" aria-label="More actions">${DOTS}</button><button type="button" class="hp-tx" data-hp-closechat="${esc(c.id)}" title="Close tab" aria-label="Close tab">${XS}</button></button>`;}).join("");
  tabsEl.querySelector('[aria-selected="true"]')?.scrollIntoView({block:"nearest",inline:"nearest"});}

function renderStrip(){const list=srcs();if(list.length&&!list.some(p=>p.id===pprov))pprov=list[0].id;
  stripEl.innerHTML=list.map(p=>`<button type="button" class="hp-prov" data-hp-prov="${esc(p.id)}" aria-pressed="${String(p.id===pprov)}" title="${esc(p.label)} · ${p.n} chats">${esc(p.label)}<em>${p.n}</em></button>`).join("");}

function openScopeMenu(anchor){hideHover();const s=getScope(pprov),provChats=chats.filter(c=>c.source===pprov);
  const folders=[...new Set(provChats.map(c=>String(c.project||"").trim()).filter(Boolean))].map(f=>({f,n:provChats.filter(c=>String(c.project||"")===f).length})).sort((a,b)=>b.n-a.n||a.f.localeCompare(b.f));
  const item=(label,val,desc)=>{const on=s.mode===val.mode&&(!val.folder||s.folder===val.folder);return`<button type="button" data-hp-scope-set='${esc(JSON.stringify(val))}'>${on?"✓ ":""}${esc(label)}${desc?` <span style="color:var(--muted)">(${esc(desc)})</span>`:""}</button>`;};
  let html=item("This project",{mode:"project"},hudProject?hudProject.split("/").slice(-2).join("/"):"HUD project")+item("Open windows",{mode:"open"})+item("All chats",{mode:"all"});
  if(folders.length){html+=`<div class="hp-sep"></div>`;folders.slice(0,12).forEach(x=>{html+=item(x.f.split("/").slice(-2).join("/")||x.f,{mode:"folder",folder:x.f},String(x.n));});}
  menuEl.innerHTML=html;menuEl.hidden=false;const r=anchor.getBoundingClientRect();menuEl.style.left=Math.min(r.left,innerWidth-260)+"px";menuEl.style.top=Math.min(r.bottom+6,innerHeight-300)+"px";}

function openPicker(anchor){hideHover();const closed=closedFor(pprov);const archived=chats.filter(c=>c.source===pprov&&chatMeta(c).archived);
  const label=SRC[pprov]||pprov;
  let html=`<button type="button" data-hp-newchat>New ${esc(label)} chat</button><div class="hp-sep"></div><div style="padding:6px 10px;font-size:12px;color:var(--muted)">Closed / hidden (${esc(pprov)})</div>`;
  if(closed.length) closed.slice().reverse().forEach(x=>{html+=`<button type="button" data-hp-unclose="${esc(x.id)}">${esc(x.title||x.id)}</button>`;}); else html+=`<div style="padding:6px 10px;font-size:13px">No closed tabs.</div>`;
  html+=`<div class="hp-sep"></div><button type="button" data-hp-toggle-archived">${showArchived?"Hide archived":"Show archived"}</button>`;
  if(showArchived&&archived.length){html+=`<div class="hp-sep"></div><div style="padding:6px 10px;font-size:12px;color:var(--muted)">Archived</div>`;archived.slice(0,20).forEach(c=>{html+=`<button type="button" data-hp-unarchive="${esc(c.id)}">${esc(chatTitle(c))}</button>`;});}
  menuEl.innerHTML=html;menuEl.hidden=false;const r=anchor.getBoundingClientRect();menuEl.style.left=Math.min(r.left,innerWidth-260)+"px";menuEl.style.top=Math.min(r.bottom+6,innerHeight-320)+"px";}

function openTabMenu(c,x,y){hideHover();const m=chatMeta(c),pinned=!!m.pinned,unread=(m.tags||[]).includes("unread");
  menuEl.innerHTML=`<button type="button" data-act="rename">Rename</button><button type="button" data-act="pin">${pinned?"Unpin":"Pin"}</button><button type="button" data-act="unread">${unread?"Mark read":"Mark unread"}</button><button type="button" data-act="initiator">Make principal</button><div class="hp-sep"></div><button type="button" data-act="copy-id">Copy chat ID</button><button type="button" data-act="copy-path">Copy path</button><div class="hp-sep"></div><button type="button" data-act="open-tab">Open in new tab</button><div class="hp-sep"></div><button type="button" data-act="archive">Archive</button><button type="button" class="danger" data-act="delete">Delete</button>`;
  menuEl.hidden=false;menuEl.style.left=Math.min(x,innerWidth-240)+"px";menuEl.style.top=Math.min(y,innerHeight-360)+"px";
  menuEl.onclick=async ev=>{const b=ev.target.closest("button");if(!b)return;const act=b.dataset.act;hideMenu();
    try{
      if(act==="rename"){const t=prompt("Rename chat",chatTitle(c)); if(t!=null){await postAction(c.id,"rename",t); await loadChats(true); renderStrip(); renderTabs(); showBody();}}
      else if(act==="pin"){await postAction(c.id,"pin",pinned?"off":"on"); await loadChats(true); renderStrip(); renderTabs(); showBody();}
      else if(act==="unread"){await postAction(c.id,"tag",unread?{remove:"unread"}:{add:"unread"}); await loadChats(true); renderStrip(); renderTabs(); showBody();}
      else if(act==="initiator"){await handOver(c);}
      else if(act==="copy-id"){await navigator.clipboard?.writeText(String(c.id||"")); window.toast?.("Copied chat ID");}
      else if(act==="copy-path"){await navigator.clipboard?.writeText(String(c.path||c.project||c.project_dir||c.id||"")); window.toast?.("Copied path");}
      else if(act==="open-tab"){openInNewTab(c);}
      else if(act==="archive"){await postAction(c.id,"archive","on"); await loadChats(true); renderStrip(); renderTabs(); showBody();}
      else if(act==="delete"){const src=SRC[c.source]||c.source||"provider"; if(confirm(`Removes it from Subway. The chat's file in ${src} is not deleted.`)){await postAction(c.id,"delete","on"); await postAction(c.id,"archive","on"); await loadChats(true); renderStrip(); renderTabs(); showBody();}}
    }catch(e){window.toast?.error?.(String(e.message||e));}
  };
}

tabsEl.addEventListener("click",e=>{const j=e.target.closest('[data-hp-jump]'); if(j){e.stopPropagation();e.preventDefault();const jp=j.dataset.hpJprov; if(jp&&jp!==pprov){pprov=jp;try{localStorage.setItem("hp-prov",pprov);}catch(_){}}picked=j.dataset.hpJump;chatOptOut=null;sel={kind:"chat"};renderStrip();renderTabs();showBody();return;}
  const un=e.target.closest('[data-hp-unclose]'); // not in tabs but safety
  const scopeBtn=e.target.closest('[data-hp-scope]'); if(scopeBtn){e.stopPropagation(); openScopeMenu(scopeBtn); return;}
  const more=e.target.closest('[data-hp-more]'); if(more){e.stopPropagation(); const c=chats.find(x=>x.id===more.dataset.hpMore); if(c) openTabMenu(c, e.clientX, e.clientY); return;}
  const xc=e.target.closest('[data-hp-closechat]'); if(xc){e.stopPropagation(); const c=chats.find(x=>x.id===xc.dataset.hpClosechat); if(c) closeTab(c); return;}
  const x=e.target.closest("[data-hp-close]");if(x){e.stopPropagation();closePage(x.dataset.hpClose);return;}
  const pg=e.target.closest("[data-hp-page]");if(pg){sel={kind:"page",r:pg.dataset.hpPage};renderTabs();showBody();return;}
  const b=e.target.closest("[data-hp-tab]");if(!b)return;picked=b.dataset.hpTab;chatOptOut=null;sel={kind:"chat"};renderTabs();showBody();});
tabsEl.addEventListener("contextmenu",e=>{const b=e.target.closest("[data-hp-tab]"); if(!b) return; e.preventDefault(); const c=chats.find(x=>x.id===b.dataset.hpTab); if(c) openTabMenu(c,e.clientX,e.clientY);});
tabsEl.addEventListener("pointerover",e=>{const b=e.target.closest("[data-hp-tab]"); if(!b){hideHover();return;} const c=chats.find(x=>x.id===b.dataset.hpTab); if(c) showHover(e,c);});
tabsEl.addEventListener("pointerout",e=>{if(!e.relatedTarget||!e.relatedTarget.closest?.("[data-hp-tab]"))hideHover();});
tabsEl.addEventListener("mouseleave",hideHover);
tabsEl.addEventListener("pointerleave",hideHover);
tabsEl.addEventListener("scroll",hideHover,{passive:true});
addEventListener("blur",hideHover);
document.addEventListener("mouseleave",hideHover);
tabsEl.addEventListener("keydown",e=>{const pg=e.target.closest("[data-hp-page]");if(pg&&e.target===pg&&(e.key==="Enter"||e.key===" ")){e.preventDefault();pg.click();}
  const b=e.target.closest("[data-hp-tab]"); if(!b) return; if(e.key==="ArrowRight"||e.key==="ArrowLeft"){const tabs=[...tabsEl.querySelectorAll('.hp-tab[role="tab"][data-hp-tab]')];const i=tabs.indexOf(b);const n=tabs[e.key==="ArrowRight"?i+1:i-1]; if(n){e.preventDefault(); n.focus(); n.click();}} if(e.key==="Escape"){hideMenu(); hideHover();}});
document.addEventListener("click",e=>{if(menuEl.hidden)return; if(e.target.closest('.hp-menu')||e.target.closest('[data-hp-more]')||e.target.closest('[data-hp-scope]')||e.target.closest('[data-hp-new]'))return; hideMenu();});
document.addEventListener("keydown",e=>{if(e.key==="Escape"){hideMenu(); hideHover();}});
menuEl.addEventListener("click",async e=>{const b=e.target.closest('button'); if(!b) return; if(b.dataset.hpNewchat!=null){hideMenu(); const label=SRC[pprov]||pprov; C()?.newChat?.({source:pprov,label}); document.querySelector(".cmp textarea")?.focus(); return;} if(b.dataset.hpScopeSet){setScope(pprov,JSON.parse(b.dataset.hpScopeSet)); hideMenu(); return;} if(b.dataset.hpUnclose){unclose(b.dataset.hpUnclose,pprov); hideMenu(); return;} if(b.dataset.hpToggleArchived!=null){showArchived=!showArchived; hideMenu(); renderStrip(); renderTabs(); return;} if(b.dataset.hpUnarchive){try{await postAction(b.dataset.hpUnarchive,"archive","off"); chatsAt=0; await loadChats(); renderStrip(); renderTabs();}catch(err){window.toast?.error?.(String(err.message||err));} hideMenu(); return;}});

document.addEventListener("click",e=>{if(hp.hidden)return;const a=e.target.closest('a[href^="#"]');if(!a||a.closest(".cmp"))return;
  const f=a.getAttribute("href").slice(1);if(!f)return;e.preventDefault();e.stopPropagation();openPage(f);},true);
addEventListener("hashchange",()=>{if(hp.hidden)return;const f=location.hash.slice(1)||"home";if(f.split("/")[0]==="home")return;
  history.replaceState(null,"","#home");dispatchEvent(new HashChangeEvent("hashchange"));openPage(f);});
hp.querySelector("[data-hp-new]").addEventListener("click",e=>{e.stopPropagation(); openPicker(e.currentTarget);});
stripEl.addEventListener("click",e=>{const b=e.target.closest("[data-hp-prov]");if(!b||b.dataset.hpProv===pprov)return;
  pprov=b.dataset.hpProv;try{localStorage.setItem("hp-prov",pprov);}catch(_){}picked=null;renderStrip();renderTabs();});

function renderNav(){const r=(location.hash.slice(1)||"home").split("/")[0];
  hp.querySelector(".hp-nav").innerHTML=[...document.querySelectorAll(".mseg a")].map(a=>{const href=a.getAttribute("href")||"#",t=a.textContent.trim(),on=href.slice(1).split("/")[0]===r;
    return`<a class="hp-ic${on?" on":""}" href="${esc(href)}" title="${esc(t)}" aria-label="${esc(t)}"${on?' aria-current="page"':""} >${a.querySelector("svg")?.outerHTML||esc(t[0]||"")}</a>`;}).join("");}
function fitComposer(){if(innerWidth<768){document.body.style.removeProperty("--hp-cw");return;}
  const cx=innerWidth/2;let l=0,r=innerWidth;
  for(const e of document.body.children){if(e===hp||e.matches(".cmp,.dock,.hp"))continue;const cs=getComputedStyle(e);
    if(cs.position!=="fixed"||cs.display==="none"||cs.visibility==="hidden")continue;
    for(const n of [e,...e.querySelectorAll("*")].slice(0,40)){const b=n.getBoundingClientRect();
      if(!b.width||!b.height||b.top<innerHeight-260||b.width>innerWidth*.45)continue;
      if(b.right<=cx)l=Math.max(l,b.right);else if(b.left>=cx)r=Math.min(r,b.left);}}
  const w=Math.max(320,Math.min(960,innerWidth*.9-128,2*Math.min(cx-l,r-cx)-32));
  document.body.style.setProperty("--hp-cw",w+"px");}
addEventListener("resize",()=>{if(!hp.hidden)fitComposer();});
async function loadNeeds(){const get=u=>fetch(u,{cache:"no-store"}).then(r=>r.ok?r.json():{}).catch(()=>({}));
  const [st,ld]=await Promise.all([get("/api/status?view=next"),get("/api/landing")]);
  const n={approvals:(st.workers||[]).filter(w=>w.status==="pending_approval").length,files:Array.isArray(ld.conflicts)?ld.conflicts.length:Number(ld.conflicts)||0};
  for(const a of hp.querySelectorAll("[data-hp-ic]")){const k=n[a.dataset.hpIc]||0,t=a.title;
    a.classList.toggle("needs",k>0);a.setAttribute("aria-label",k?`${t} (${k} need${k===1?"s":""} you)`:t);}}
const onHome=()=>(location.hash.slice(1)||"home").split("/")[0]==="home";
async function handOver(ch,quiet){const to=ch.id;if(ini&&ini.id===to)return;const prev=ini;
  const label=chatLabel(ch),src=ch.source||pprov,cap=(coverage&&coverage[src])||"live";const srcLabel=SRC[src]||src||"Provider";
  if(!quiet){await loadCoverage(); const c2=(coverage&&coverage[src])||cap;
    if(c2==="none"){window.toast?.error?.(`Can't make ${label} the principal: ${srcLabel} chats can't receive reports or permission requests yet.`);return;}
    if(c2==="spool"){window.toast?.warning?.(`Reports reach ${label} on its next Subway call, not instantly.`);}
  }
  ini={id:to,label,source:src};try{localStorage.setItem("hp-initiator",JSON.stringify(ini));}catch(_){}
  C()?.setPrincipal?.({id:ini.label,chat:to});
  let roots=[];try{roots=await liveRoots();}catch(_){}
  if(prev&&prev.id&&!roots.includes(prev.id))roots.push(prev.id);
  const post=root=>fetch("/api/composer/principal",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({thread:root,principal_id:to,note:"Principal switched from the HUD panel"})}).then(async r=>{if(!r.ok)throw new Error((await r.json().catch(()=>({}))).error||`HTTP ${r.status}`);});
  const res=await Promise.allSettled(roots.map(post));const bad=res.find(x=>x.status==="rejected");
  if(quiet)return;
  if(bad)window.toast?.error?.("Principal not switched",{description:String(bad.reason?.message||bad.reason)});
  else if((coverage&&coverage[src])!=="spool")window.toast?.success?.(roots.length?`Principal → ${ini.label}`:"No threads to move",{description:roots.length?`${roots.length} live thread${roots.length>1?"s":""} handed over: workers, permissions and leases now report here.`:"New work from the composer reports here.",
    action:prev?{label:"Undo",onClick:()=>{const p=chats.find(x=>x.id===prev.id)||{id:prev.id,title:prev.label,source:prev.source};handOver(p,true).then(()=>window.toast?.("Principal back to "+prev.label));}}:undefined});}
let prefsLoaded=false;
async function loadHomePrefs(){if(prefsLoaded)return;prefsLoaded=true;try{const d=await(await fetch("/api/home_prefs",{cache:"no-store"})).json();if(d&&typeof d==="object"){if(d.scopes&&typeof d.scopes==="object")jset("hp-scope",{...scopeMap(),...d.scopes});if(d.closed&&typeof d.closed==="object")jset("hp-closed",{...closedMap(),...d.closed});}}catch(_){}}
function sync(){const show=onHome()&&!!C()?.isOpen;
  if(show===!hp.hidden)return;hp.hidden=!show;document.body.classList.toggle("hp-on",show);if(!show){C()?.setChat(null);closePassive();}
  if(show){fitComposer();loadNeeds();renderNav();Promise.all([loadHomePrefs(),loadCoverage(),loadHudProject(),loadChats()]).then(()=>{loadLinks().then(()=>{if(!hp.hidden)renderTabs();});renderStrip();renderTabs();showBody();});}}
addEventListener("focus",()=>{if(!hp.hidden&&Date.now()-chatsAt>5000)loadChats(true).then(()=>{renderStrip();renderTabs();showBody();});});
let pushAt=0;
addEventListener("subway:push",ev=>{if(hp.hidden)return;const types=ev.detail&&ev.detail.types||ev.detail&&ev.detail.type?[].concat(ev.detail.types||ev.detail.type||[]):[];
  if(!types.some(t=>t==="message"||t==="status"))return;const now=Date.now();if(now-pushAt<400)return;pushAt=now;
  loadChats(true).then(()=>{renderStrip();renderTabs();showBody();});});
const cmp=document.querySelector(".cmp");
if(cmp&&window.ResizeObserver)new ResizeObserver(()=>{if(!cmp.hidden)document.body.style.setProperty("--hp-ch",cmp.offsetHeight+"px");}).observe(cmp);
if(cmp)new MutationObserver(sync).observe(cmp,{attributes:true,attributeFilter:["hidden"]});
addEventListener("hashchange",sync);
})();
