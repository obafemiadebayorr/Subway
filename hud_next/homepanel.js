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
  radial-gradient(120% 90% at 15% 10%,#E9E4FB 0,transparent 55%),radial-gradient(90% 80% at 90% 90%,#F6E7C9 0,transparent 60%),var(--hair);
  animation:hp-in 220ms cubic-bezier(.23,1,.32,1)}
.hp[hidden]{display:none}
@keyframes hp-in{from{opacity:0}to{opacity:1}}
.hp-slot{position:absolute;right:16px;top:12px;font-size:11px;letter-spacing:.04em;color:color-mix(in srgb,var(--ink) 45%,transparent);pointer-events:none}
.hp-panel{position:absolute;left:12px;right:12px;top:72px;bottom:calc(24px + var(--h) + 16px + var(--hp-ch,200px) + 12px);display:flex;flex-direction:column;
  background:var(--surface);border-radius:16px;box-shadow:0 1px 2px rgba(17,17,17,.06),0 24px 64px -24px rgba(17,17,17,.28);overflow:hidden;
  animation:hp-up 260ms cubic-bezier(.23,1,.32,1)}
@keyframes hp-up{from{transform:translateY(12px) scale(.985);opacity:0}to{transform:none;opacity:1}}
/* tab bar: browser-style tabs; the selected tab joins the white body with curved feet */
.hp-tbar{display:flex;align-items:flex-end;gap:4px;background:var(--cloud);padding:8px 6px 0;flex-shrink:0}
.hp-tabs{flex:1;min-width:0;display:flex;align-items:flex-end;padding:0 10px;overflow-x:auto;overflow-y:hidden;scrollbar-width:none}
.hp-tabs::-webkit-scrollbar{display:none}
.hp-tab{position:relative;display:flex;align-items:center;gap:8px;flex:0 0 150px;height:38px;padding:0 14px;border:0;border-radius:10px 10px 0 0;
  background:transparent;font:inherit;font-size:13px;color:var(--muted);cursor:pointer;text-align:left;transition:background 150ms,color 150ms}
.hp-tab svg{width:16px;height:16px;flex-shrink:0}
.hp-tab span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hp-tab:hover{color:var(--ink);background:color-mix(in srgb,var(--surface) 55%,transparent)}
.hp-tab[aria-selected="true"]{background:var(--surface);color:var(--ink);font-weight:500;z-index:1}
.hp-tab[aria-selected="true"]::before,.hp-tab[aria-selected="true"]::after{content:"";position:absolute;bottom:0;width:10px;height:10px;pointer-events:none}
.hp-tab[aria-selected="true"]::before{left:-10px;background:radial-gradient(circle at 0 0,transparent 9.5px,var(--surface) 10px)}
.hp-tab[aria-selected="true"]::after{right:-10px;background:radial-gradient(circle at 100% 0,transparent 9.5px,var(--surface) 10px)}
.hp-tab.live svg{color:var(--sky-ink)}
.hp-tab .hp-pin{font-size:11px;color:var(--sky-ink);opacity:.8}
.hp-tab .hp-badge{font-size:10px;font-weight:600;color:var(--ochre);border:1px solid var(--ochre);border-radius:6px;padding:0 4px;line-height:16px}
.hp-link{font-size:.7rem;padding:0 .35rem;border-radius:999px;border:1px solid currentColor;opacity:.75;margin-left:.25rem;cursor:pointer;white-space:nowrap}.hp-link:hover{opacity:1}
.hp-tact{display:grid;place-items:center;flex-shrink:0;width:34px;height:34px;margin-bottom:4px;border:0;border-radius:10px;background:transparent;color:var(--muted);cursor:pointer}
.hp-tact:hover{background:color-mix(in srgb,var(--surface) 70%,transparent);color:var(--ink)}
.hp-tab:focus-visible,.hp-tact:focus-visible,.hp-prov:focus-visible,.hp-ic:focus-visible,.hp-scope:focus-visible,.hp-menu button:focus-visible{outline:2px solid var(--ochre);outline-offset:-2px}
.hp-tabs-empty{font-size:13px;color:var(--muted);padding:0 6px 12px}
.hp-body{position:relative;flex:1;min-height:0}
.hp-frame{position:absolute;inset:0;width:100%;height:100%;border:0;background:var(--bg,var(--ground))}
.hp-frame[hidden]{display:none}
.hp-chat{position:absolute;inset:0;overflow-y:auto;padding:20px 16px 24px;display:flex;flex-direction:column;gap:14px}
.hp-chat[hidden]{display:none}
/* the selected chat: transit strip, an expandable sheet, then the transcript (flex column) */
.hp-thread{position:absolute;inset:0;display:flex;flex-direction:column;min-height:0}
.hp-thread[hidden]{display:none}
.hp-thread>.hp-chat{position:relative;inset:auto;flex:1;min-height:0}
.hp-transit{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:8px 14px;background:var(--cloud);border-bottom:1px solid var(--hair);font-size:12px}
.hp-transit[hidden]{display:none}
.hp-tchip{border:1px solid var(--hair);background:var(--surface);border-radius:999px;padding:3px 10px;font:inherit;font-size:12px;color:var(--ink);cursor:pointer}
.hp-tchip:hover{background:var(--ground)}
.hp-tchip[aria-pressed="true"]{background:var(--sky-chip-hi);color:var(--sky-ink);border-color:transparent}
.hp-tchip:focus-visible{outline:2px solid var(--ochre);outline-offset:1px}
.hp-sheet{padding:12px 16px;background:var(--ground);border-bottom:1px solid var(--hair);display:flex;flex-direction:column;gap:10px;max-height:46%;overflow:auto}
.hp-sheet[hidden]{display:none}
.hp-sheet-grid{display:flex;flex-direction:column;gap:8px}
.hp-sheet-row{display:grid;grid-template-columns:120px minmax(0,1fr);gap:10px;align-items:center;font-size:12px}
.hp-sheet-k{color:var(--muted)}
.hp-sheet-v{display:flex;align-items:center;gap:8px;flex-wrap:wrap;min-width:0;overflow-wrap:anywhere}
.hp-sheet select,.hp-sheet input{font:inherit;font-size:12px;color:var(--ink);background:var(--surface);border:1px solid var(--hair);border-radius:8px;padding:4px 8px;max-width:100%}
.hp-sheet input[type="number"]{width:120px}
.hp-sheet-list{display:flex;flex-direction:column;gap:6px;width:100%}
.hp-sheet-item{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;align-items:center;padding:6px 8px;border:1px solid var(--hair);border-radius:8px;background:var(--surface);min-width:0}
.hp-sheet-item b{font-weight:500}
.hp-sheet-item span{color:var(--muted);white-space:nowrap}
.hp-sheet-empty{color:var(--muted);font-size:12px}
.hp-media{display:flex;flex-wrap:wrap;gap:8px}
.hp-media img{display:block;max-width:180px;max-height:140px;border-radius:8px;border:1px solid var(--hair);object-fit:cover}
.hp-media-file{display:inline-flex;align-items:center;gap:6px;max-width:100%;padding:6px 10px;border:1px solid var(--hair);border-radius:8px;background:var(--surface);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
@media (max-width:639px){.hp-sheet-row{grid-template-columns:1fr}.hp-sheet{max-height:52%}}
.hp-msg.user.pending{opacity:.85}.hp-newchat{display:flex;flex-direction:column;gap:6px;align-items:center;justify-content:center;text-align:center;min-height:40%;padding:48px 16px;color:var(--muted);font-size:14px}.hp-newchat b{color:var(--ink);font-size:18px;font-weight:600}
.md p{margin:0 0 10px}.md p:last-child{margin-bottom:0}
.md .md-h{font-weight:600;margin:16px 0 6px;letter-spacing:-.01em}.md .md-h1{font-size:17px}.md .md-h2{font-size:15.5px}.md .md-h3{font-size:14px}
.md .md-h:first-child{margin-top:0}
.md ul,.md ol{margin:0 0 10px;padding-left:20px}.md li{margin:3px 0}.md li.d1{margin-left:18px}.md li.d2{margin-left:36px}.md li.d3{margin-left:54px}
.md li::marker{color:var(--muted)}
.md code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.88em;background:var(--cloud);border:1px solid var(--hair);border-radius:5px;padding:1px 5px}
.md pre{margin:0 0 10px;background:var(--cloud);border:1px solid var(--hair);border-radius:10px;padding:10px 12px;overflow-x:auto;white-space:pre}
.md pre code{background:none;border:0;padding:0;font-size:12.5px}
.md strong{font-weight:600}.md a{color:var(--sky-ink)}
.md blockquote{margin:0 0 10px;padding:2px 12px;border-left:3px solid var(--hair);color:var(--muted)}
.md hr{border:0;height:1px;background:var(--hair);margin:14px 0}
.md .md-tw{overflow-x:auto;margin:0 0 10px}.md table{border-collapse:collapse;font-size:13px}.md th,.md td{border-bottom:1px solid var(--hair);padding:6px 10px;text-align:left;white-space:nowrap}.md th{font-weight:600}
.hp-msg.assistant{white-space:normal}
.hp-steps{align-self:flex-start;list-style:none;margin:0;padding:10px 14px;border:1px solid var(--hair);border-radius:12px;background:var(--cloud);display:flex;flex-direction:column;gap:8px;min-width:min(320px,100%);max-width:min(560px,100%);font-size:13px}
.hp-steps li{display:grid;grid-template-columns:16px 1fr;column-gap:10px;align-items:center}
.hp-steps .hp-si{display:grid;place-items:center;width:16px;height:16px}
.hp-steps .hp-sl{color:var(--ink)}.hp-steps .st-pending .hp-sl{color:var(--muted)}.hp-steps .st-error .hp-sl{color:#B42318}
.hp-si-dot{width:6px;height:6px;border-radius:50%;background:var(--hair)}
.hp-si-spin{width:14px;height:14px;animation:hp-spin .8s linear infinite}.hp-si-spin circle{stroke:var(--hair)}.hp-si-spin path{stroke:var(--sky-ink)}
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
.cmp-apps-go:hover{color:var(--ink);background:color-mix(in srgb,var(--ink) 5%,transparent)}
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
.hp-msg{content-visibility:auto;contain-intrinsic-size:auto 96px;position:relative}.hp-msg:nth-last-child(-n+10){content-visibility:visible}.hp-mmenu{position:absolute;top:4px;right:4px;opacity:0;border:0;background:transparent;color:var(--muted);cursor:pointer;border-radius:8px;padding:2px 6px;font:inherit}.hp-msg:hover .hp-mmenu,.hp-mmenu:focus-visible{opacity:1}.hp-undo{border:0;background:none;color:var(--accent);cursor:pointer;font:inherit;padding:0}@media (hover:none){.hp-mmenu{opacity:.6}}.hp-chat-note{margin:auto;font-size:13px;color:var(--muted);text-align:center}.hp-skel{display:flex;flex-direction:column;gap:14px}.hp-skel i{display:block;height:44px;border-radius:12px;background:color-mix(in srgb,var(--ink) 7%,transparent);animation:hp-skel 1.1s ease-in-out infinite alternate}.hp-skel i:nth-child(odd){width:60%;align-self:flex-end;height:28px}@keyframes hp-skel{to{opacity:.45}}@media (prefers-reduced-motion:reduce){.hp-skel i{animation:none}}.hp-older{align-self:center;border:1px solid var(--line,color-mix(in srgb,var(--ink) 14%,transparent));background:transparent;border-radius:999px;padding:6px 14px;font:inherit;font-size:12px;color:var(--muted);cursor:pointer}.hp-older:disabled{opacity:.6;cursor:default}.hp-empty{margin:auto;display:flex;flex-direction:column;gap:12px;width:min(360px,100%);padding:24px 16px}.hp-empty-h{font-size:13px;font-weight:600;color:var(--ink);margin:0}.hp-empty-list{display:flex;flex-direction:column;gap:2px}.hp-empty-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:9px 10px;border:0;border-radius:8px;background:none;text-align:left;font:inherit;font-size:13px;color:var(--ink);cursor:pointer}.hp-empty-row:hover,.hp-empty-row:focus-visible{background:var(--ground)}.hp-empty-t{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.hp-empty-m{flex-shrink:0;font-size:11px;color:var(--muted)}.hp-empty-new{align-self:flex-start;padding:8px 14px;border:0;border-radius:8px;background:var(--ink);color:#fff;font:inherit;font-size:13px;font-weight:600;cursor:pointer}
.hp-seg{display:flex;align-items:center;gap:10px;margin:14px 0;font-size:12px;color:var(--muted);text-align:center}.hp-seg::before,.hp-seg::after{content:"";flex:1;height:1px;background:currentColor;opacity:.3}
@media (min-width:768px){.hp-chat{padding:28px max(24px,calc((100% - 820px)/2)) 32px}}
.hp-tab.page{padding-right:6px}
.hp-tx,.hp-tmore{display:grid;place-items:center;margin-left:auto;width:22px;height:22px;border:0;border-radius:6px;background:transparent;color:inherit;cursor:pointer;flex-shrink:0;opacity:0}
.hp-tab:hover .hp-tx,.hp-tab:focus-within .hp-tx,.hp-tab[aria-selected="true"] .hp-tx,
.hp-tab:hover .hp-tmore,.hp-tab:focus-within .hp-tmore,.hp-tab[aria-selected="true"] .hp-tmore{opacity:1}
.hp-tx:hover,.hp-tmore:hover{background:var(--hair)}
.hp-tx svg,.hp-tmore svg{width:12px;height:12px}
.hp-strip{display:flex;align-items:center;justify-content:flex-start;gap:6px;padding:10px;overflow-x:auto;scrollbar-width:none;flex-shrink:0}
.hp-strip::-webkit-scrollbar{display:none}
.hp-scope{display:flex;align-items:center;gap:6px;flex-shrink:0;height:28px;padding:0 10px;border:1px solid var(--hair);border-radius:8px;background:var(--sky-chip);font:inherit;font-size:12px;font-weight:500;color:var(--sky-ink);cursor:pointer}
.hp-scope:hover{background:var(--sky-chip-hi)}
.hp-prov{display:flex;align-items:center;gap:6px;flex-shrink:0;height:28px;padding:0 10px;border:1px solid var(--hair);border-radius:8px;
  background:var(--surface);font:inherit;font-size:12px;font-weight:500;color:var(--ink);cursor:pointer;transition:background 150ms,border-color 150ms}
.hp-prov:hover{background:var(--cloud)}
.hp-prov em{font-style:normal;font-weight:400;color:var(--muted)}
.hp-prov[aria-pressed="true"]{background:var(--ink);border-color:var(--ink);color:var(--surface)}.hp-prov[aria-pressed="true"] *{color:inherit}
.hp-prov[aria-pressed="true"] em{color:var(--muted)}
.hp-menu,.hp-hover{position:fixed;z-index:60;background:var(--surface);border:1px solid var(--hair);border-radius:12px;box-shadow:0 12px 40px -16px rgba(17,17,17,.35);padding:6px;min-width:220px;color:var(--ink)}
.hp-menu[hidden],.hp-hover[hidden]{display:none}
.hp-menu button,.hp-menu .hp-mi{display:flex;align-items:center;gap:8px;width:100%;border:0;background:transparent;font:inherit;font-size:13px;text-align:left;padding:8px 10px;border-radius:8px;color:inherit;cursor:pointer}
.hp-menu button:hover,.hp-menu .hp-mi:hover{background:var(--sky-chip);color:var(--sky-ink)}
.hp-menu .danger{color:#B42318}.hp-menu .danger:hover{background:#FEECEC;color:#B42318}
.hp-sep{height:1px;background:var(--hair);margin:4px 2px}
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
let starting={id:"",until:0,timer:0};let chats=[],chatsAt=0,picked=null,pprov=(()=>{try{return localStorage.getItem("hp-prov")||"claude";}catch(_){return"claude";}})();
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
/* Titles often start with an injected relay line ("[SUBWAY: …] Branch: …", "<task-notification>…"); show the human part. */
const cleanTitle=t=>{let x=String(t||"").trim();for(let i=0;i<3;i++)x=x.replace(/^\[(?:SUBWAY|subway)[^\]]*\]\s*/,"").replace(/^<[^>]{1,40}>\s*/,"").replace(/^Branch:\s*\S+\s*/,"").trim();return x;};
function chatTitle(c){const meta=c.meta||{};const al=c.aliases&&c.aliases[0]&&"@"+String(c.aliases[0]).replace(/^@/,"");return cleanTitle(meta.title)||cleanTitle(c.title)||al||cleanTitle(c.tab_name)||"Untitled";}
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
const SC=window.SubwayChat;let chatEl=null;const msgMemo=new Map();let winN=20;
const SKEL='<div class="hp-skel" aria-busy="true" aria-label="Loading chat"><i></i><i></i><i></i><i></i></div>';
const PC_KEY="hp-chatcache";
function pcLoad(id){try{const m=JSON.parse(localStorage.getItem(PC_KEY)||"{}");return m[id]||null;}catch(_){return null;}}
function pcSave(id,d){try{const m=JSON.parse(localStorage.getItem(PC_KEY)||"{}");m[id]={d:{...d,turns:(d.turns||[]).slice(-50),live:null},at:Date.now()};const ks=Object.keys(m).sort((a,b)=>m[b].at-m[a].at);for(const k of ks.slice(8))delete m[k];const keep=ks.slice(0,8);let s=JSON.stringify(m);while(s.length>400000&&keep.length>1){delete m[keep.pop()];s=JSON.stringify(m);}localStorage.setItem(PC_KEY,s);}catch(_){}}
function lastPick(p){try{return(JSON.parse(localStorage.getItem("hp-last")||"{}")||{})[p]||null;}catch(_){return null;}}
function setLastPick(p,id){try{const m=JSON.parse(localStorage.getItem("hp-last")||"{}")||{};m[p]=id;localStorage.setItem("hp-last",JSON.stringify(m));}catch(_){}}
function msgHtml(m){const k=m.role+"\u0001"+(m.tid||"")+"\u0001"+m.text;let h=msgMemo.get(k);if(h==null){const tid=m.tid||"",menu=tid?`<button type="button" class="hp-mmenu" data-hp-mmenu="${esc(tid)}" aria-label="Message actions">⋯</button>`:"";h=m.role==="user"?`<div class="hp-msg user" data-tid="${esc(tid)}">${esc(m.text)}${menu}</div>`:`<div class="hp-msg assistant md" data-tid="${esc(tid)}">${md(m.text)}${menu}</div>`;if(msgMemo.size>600)msgMemo.clear();msgMemo.set(k,h);}return h;}
/* Context meter for the picked chat: used / compacts-at, plus Compact when the provider supports it. */
const ctxNote={};
function ctxBarHtml(x,note){const st=`<p class="hp-ctx-status" role="status" aria-live="polite">${esc(note||"")}</p>`;if(!x)return note?st:"";
  const hasMeter=x.used!=null&&x.at>0;if(!hasMeter&&!x.can_compact)return note?st:"";const pct=Math.max(0,x.pct||0),name=SRC[x.provider]||x.provider||"This chat";
  const title=x.can_compact?`Context used · compacts at ${fmtK(x.at)}`:`${name} compacts on its own at the threshold`;
  const meter=hasMeter?`<div class="hp-ctx" title="${esc(title)}" role="meter" aria-label="Context used" aria-valuemin="0" aria-valuemax="${x.at}" aria-valuenow="${Math.min(x.used,x.at)}" aria-valuetext="${Math.round(pct)}% of compact threshold"><span class="hp-ctx-bar${pct>=100?" over":pct>=80?" warn":""}"><i style="width:${Math.min(100,pct)}%"></i></span><span class="hp-ctx-num">${fmtK(x.used)} / ${fmtK(x.at)}</span></div>`:"";
  return `<div class="hp-ctxbar">${meter}${x.can_compact?'<button type="button" class="hp-compact" data-hp-compact>Compact</button>':""}</div>${st}`;}
function fmtK(n){return n>=1000?Math.round(n/1000)+"K":String(n);}
/* Transit strip + expandable thread sheet: the selected chat's route, allocation,
   workers, media and per-thread settings sit in a flex column above the transcript. */
let activeSheet="";
let peersCache=null,peersLoading=false,vmsCache=null,vmsLoading=false,sandboxMode="",sandboxLoading=false;
const threadSettingsCache=new Map(),threadSettingsLoading=new Set();
let threadEl=null,transitEl=null,sheetEl=null;

function targetMap(){try{const m=JSON.parse(localStorage.getItem("hp-target")||"{}");return m&&typeof m==="object"?m:{};}catch(_){return{};}}
function targetFor(id){return targetMap()[id||""]||"this";}
function setTargetFor(id,v){try{const m=targetMap();m[id||""]=v||"this";localStorage.setItem("hp-target",JSON.stringify(m));}catch(_){}}
function targetLabel(v){
  if(!v||v==="this")return "This station";
  if(v.indexOf("peer:")===0){const p=(peersCache||[]).find(x=>String(x.peer_id||"")===v.slice(5));return p?String(p.name||p.alias||p.device_id||"Peer station"):"Peer station";}
  if(v.indexOf("vm:")===0)return "VM "+v.slice(3).split(":").pop();
  return String(v);
}
async function loadPeers(){if(peersCache||peersLoading)return;peersLoading=true;
  try{const r=await fetch("/api/peers",{cache:"no-store"});const d=await r.json();peersCache=r.ok&&Array.isArray(d.peers)?d.peers:[];}catch(_){peersCache=[];}
  finally{peersLoading=false;}renderTransit();}
async function loadVms(){if(vmsCache||vmsLoading)return;vmsLoading=true;
  try{const r=await fetch("/api/vms",{cache:"no-store"});const d=await r.json();vmsCache=r.ok&&Array.isArray(d.vms)?d.vms:[];}catch(_){vmsCache=[];}
  finally{vmsLoading=false;}renderTransit();}
async function loadSandboxMode(){if(sandboxMode||sandboxLoading)return;sandboxLoading=true;
  try{const d=await(await fetch("/api/settings",{cache:"no-store"})).json();const v=(d.values||{})["sandbox.state_mode"];sandboxMode=v==="dry_run"?"dry_run":"live";}catch(_){sandboxMode="live";}
  finally{sandboxLoading=false;}renderTransit();}
async function loadThreadSettings(id){if(!id||threadSettingsCache.has(id)||threadSettingsLoading.has(id))return;threadSettingsLoading.add(id);
  try{const d=await(await fetch("/api/thread_settings?id="+encodeURIComponent(id),{cache:"no-store"})).json();threadSettingsCache.set(id,d&&typeof d==="object"?d:{});}catch(_){threadSettingsCache.set(id,{});}
  finally{threadSettingsLoading.delete(id);}renderTransit();}

/* Turn media is provider-shaped: accept bare paths, objects with url/src/path, or data URLs. */
function mediaOf(turns){
  const out=[];
  const add=v=>{
    if(!v)return;
    if(typeof v==="string"){const s=v.trim();if(s)out.push({src:s,name:s.split(/[\\/]/).pop()||s});return;}
    if(typeof v==="object"){const s=String(v.url||v.src||v.href||v.path||"").trim();if(s)out.push({src:s,name:String(v.name||v.filename||v.title||s.split(/[\\/]/).pop()||s)});}
  };
  for(const t of turns||[]){
    if(!t||typeof t!=="object")continue;
    for(const k of ["attachments","images","media","files"])if(Array.isArray(t[k]))t[k].forEach(add);
    if(t.image)add(t.image);
    if(t.image_url)add(t.image_url);
  }
  const seen=new Set();return out.filter(m=>{if(seen.has(m.src))return false;seen.add(m.src);return true;});
}
function mediaHtml(turns){
  const media=mediaOf(turns);
  if(!media.length)return '<span class="hp-sheet-empty">No media in this thread yet.</span>';
  return `<div class="hp-media">${media.map(m=>{
    const direct=/^data:image\//i.test(m.src)||/^https?:\/\//i.test(m.src);
    return direct?`<img src="${esc(m.src)}" alt="${esc(m.name)}" loading="lazy">`:`<span class="hp-media-file" title="${esc(m.src)}">${esc(m.name)}</span>`;}).join("")}</div>`;
}
function sheetSelect(key,label,value,choices){
  return `<label class="hp-sheet-row"><span class="hp-sheet-k">${esc(label)}</span><span class="hp-sheet-v"><select data-hp-tset="${esc(key)}">${choices.map(([v,t])=>`<option value="${esc(v)}"${String(v)===String(value==null?"":value)?" selected":""}>${esc(t)}</option>`).join("")}</select></span></label>`;
}
function allocationHtml(ch){
  const id=ch.id||picked,cur=targetFor(id);
  const all=peersCache||[],own=all.filter(p=>String(p.relation||"")==="own"),usePeers=own.length?own:all;
  const opts=['<option value="this"'+ (cur==="this"?" selected":"") +'>This station</option>'];
  for(const p of usePeers){const pid=String(p.peer_id||"");if(!pid)continue;const v="peer:"+pid;
    opts.push(`<option value="${esc(v)}"${cur===v?" selected":""}>${esc(String(p.name||p.alias||p.device_id||"Peer"))}</option>`);}
  for(const vm of vmsCache||[]){if(!vm||!vm.name)continue;const v="vm:"+String(vm.backend||"vm")+":"+String(vm.name);
    opts.push(`<option value="${esc(v)}"${cur===v?" selected":""}>${esc("VM "+vm.name)}</option>`);}
  const state=!peersCache?"Loading peers…":(usePeers.length?`${usePeers.length} peer station${usePeers.length>1?"s":""}`:"No linked stations");
  const vmState=!vmsCache?"Loading VMs…":((vmsCache||[]).length?`${(vmsCache||[]).length} VM${(vmsCache||[]).length>1?"s":""}`:"No VMs");
  const live=sandboxMode==="dry_run"?"dry_run":"live";
  return `<label class="hp-sheet-row"><span class="hp-sheet-k">Station / VM</span><span class="hp-sheet-v"><select data-hp-target>${opts.join("")}</select><span class="hp-sheet-empty">${esc(state)} · ${esc(vmState)}</span></span></label>`+
    `<div class="hp-sheet-row"><span class="hp-sheet-k">Principal</span><span class="hp-sheet-v">${esc((ch.meta&&ch.meta.principal_id)||"none")}</span></div>`+
    `<div class="hp-sheet-row"><span class="hp-sheet-k">Paid by</span><span class="hp-sheet-v">${esc(ch.paid_by||ch.source||"unknown")}</span></div>`+
    `<div class="hp-sheet-row"><span class="hp-sheet-k">Sandbox mode</span><span class="hp-sheet-v">`+
    `<button type="button" class="hp-tchip" data-hp-sandbox="live" aria-pressed="${String(live==="live")}">live</button>`+
    `<button type="button" class="hp-tchip" data-hp-sandbox="dry_run" aria-pressed="${String(live==="dry_run")}">dry_run</button></span></div>`;
}
function workersHtml(ch){
  const list=Array.isArray(ch.workers)?ch.workers:[];
  if(!list.length)return '<span class="hp-sheet-empty">No workers on this thread yet.</span>';
  return `<div class="hp-sheet-list">${list.map(w=>{
    const who=[w.provider,w.model].filter(Boolean).join(" · ")||"no model";
    const tok=Number(w.tokens||0);
    return `<div class="hp-sheet-item"><span><b>${esc(w.role||"Worker")}</b> · ${esc(who)}</span><span>${esc(w.status||"unknown")} · ${esc(fmtK(tok))} tok</span></div>`;}).join("")}</div>`;
}
function settingsHtml(ch){
  const id=ch.id||picked,ts=threadSettingsCache.get(id)||{},eff=ts.effective||{},ov=ch.thread_settings||{};
  const get=(k,d)=>eff[k]!=null?eff[k]:(ov[k]!=null?ov[k]:d);
  const modes=[["","Use global"],["ask","Ask"],["auto_safe","Auto-safe"],["auto_approve","Auto-approve"]];
  const comp=[["","Use global"],["suggest","Suggest"],["auto","Auto"],["never","Never"]];
  const budget=get("budget_tokens","");
  return sheetSelect("permission_mode","Permissions",get("permission_mode",""),modes)+
    sheetSelect("terminal_mode","Terminal",get("terminal_mode",""),modes)+
    `<label class="hp-sheet-row"><span class="hp-sheet-k">Budget tokens</span><span class="hp-sheet-v"><input type="number" min="0" step="1000" placeholder="global" value="${esc(budget)}" data-hp-tset="budget_tokens"></span></label>`+
    sheetSelect("compaction","Compaction",get("compaction",""),comp);
}
function sheetHtml(kind,ch){
  if(kind==="allocation")return allocationHtml(ch);
  if(kind==="workers")return workersHtml(ch);
  if(kind==="media"){const v=chatView(ch.id||picked);return mediaHtml((v&&v.d&&v.d.turns)||[]);}
  if(kind==="settings")return settingsHtml(ch);
  return "";
}
function renderTransit(){
  if(!transitEl||!sheetEl)return;
  const show=sel.kind==="chat"&&!!picked;
  transitEl.hidden=!show;
  if(!show){sheetEl.hidden=true;sheetEl.innerHTML="";return;}
  const ch=chats.find(x=>x.id===picked)||{id:picked,source:pprov};
  const id=ch.id||picked,workers=Array.isArray(ch.workers)?ch.workers:[];
  const route=ch.project?String(ch.project).split(/[\\/]/).pop():"Local";
  const lifecycle=ch.lifecycle||(ch.open?"live":"ready");
  const mode=sandboxMode||"live";
  transitEl.innerHTML=`<span>${esc(route)} · ${esc(lifecycle)}</span>`+
    `<button type="button" class="hp-tchip" data-hp-sheet="allocation" aria-pressed="${String(activeSheet==="allocation")}">Allocation · ${esc(targetLabel(targetFor(id)))} · ${esc(mode)}</button>`+
    `<button type="button" class="hp-tchip" data-hp-sheet="workers" aria-pressed="${String(activeSheet==="workers")}">Workers (${workers.length})</button>`+
    `<button type="button" class="hp-tchip" data-hp-sheet="media" aria-pressed="${String(activeSheet==="media")}">Media</button>`+
    `<button type="button" class="hp-tchip" data-hp-sheet="settings" aria-pressed="${String(activeSheet==="settings")}">Settings</button>`+
    `<button type="button" class="hp-tchip" data-hp-atlas>Atlas ↗</button>`;
  if(!activeSheet){sheetEl.hidden=true;sheetEl.innerHTML="";return;}
  sheetEl.hidden=false;
  sheetEl.innerHTML=`<div class="hp-sheet-grid">${sheetHtml(activeSheet,ch)}</div>`;
  if(activeSheet==="allocation"){loadPeers();loadVms();loadSandboxMode();}
  else if(activeSheet==="settings")loadThreadSettings(id);
}
async function saveSandboxMode(v){
  const mode=v==="dry_run"?"dry_run":"live",id=picked;
  if(!id)return;
  sandboxMode=mode;renderTransit();
  try{
    const r=await fetch("/api/settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({"sandbox.state_mode":mode,thread:id})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(d.error||`HTTP ${r.status}`);
    window.toast?.(`Sandbox mode: ${mode}`);
  }catch(err){window.toast?.error?.(String(err.message||err));}
}
async function saveThreadSetting(id,key,raw){
  if(!id)return;
  let value=raw;
  if(key==="budget_tokens"){const s=String(raw==null?"":raw).trim();value=s?Number(s):"";if(value!==""&&!Number.isFinite(value)){window.toast?.error?.("Budget must be a number");return;}}
  try{
    const r=await fetch("/api/thread_settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id,[key]:value})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(d.error||`HTTP ${r.status}`);
    threadSettingsCache.set(id,d&&typeof d==="object"?d:{});
    renderTransit();window.toast?.("Thread settings saved");
  }catch(err){window.toast?.error?.(String(err.message||err));}
}
function onSheetClick(e){
  const sb=e.target.closest("[data-hp-sandbox]");
  if(sb){e.stopPropagation();saveSandboxMode(sb.dataset.hpSandbox);}
}
function onSheetChange(e){
  const ts=e.target.closest("[data-hp-tset]");
  if(ts){e.stopPropagation();saveThreadSetting(picked,ts.dataset.hpTset,ts.value);return;}
  const tg=e.target.closest("[data-hp-target]");
  if(tg){e.stopPropagation();setTargetFor(picked,tg.value);renderTransit();}
}
function ensureThread(body){
  if(!chatEl){chatEl=document.createElement("div");chatEl.className="hp-chat";chatEl.setAttribute("aria-live","polite");}
  if(!threadEl||!threadEl.isConnected){
    threadEl=document.createElement("div");threadEl.className="hp-thread";
    transitEl=document.createElement("div");transitEl.className="hp-transit";
    sheetEl=document.createElement("div");sheetEl.className="hp-sheet";sheetEl.hidden=true;
    sheetEl.addEventListener("click",onSheetClick);
    sheetEl.addEventListener("change",onSheetChange);
    threadEl.append(transitEl,sheetEl,chatEl);
    body.prepend(threadEl);
  }else if(chatEl.parentNode!==threadEl)threadEl.append(chatEl);
}

/* One data layer (chatcore.js): open() reads the chat and its context in parallel and keeps one live stream on it.
   A saved copy paints at once while the read is in flight. */
function fetchChat(id,turns){const s=SC.get(id);if(!s.d){const seed=pcLoad(id);if(seed&&seed.d)SC.seed(id,seed.d);}return SC.open(id,pprov,{turns:turns||winN});}
function chatView(id){const s=SC.get(id);if(s.d)return{d:s.d,ctx:s.context,live:s.live};if(s.error)return{err:s.error};return null;}
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
const CP_WAVE=`<span class="hp-cp-wave" aria-hidden="true"></span>`;
function cpWho(id){const c=(chats||[]).find(x=>x.id===id||(x.aliases||[]).some(a=>String(a).replace(/^@/,"")===String(id).replace(/^@/,"")));const prov=c&&(c.source||c.provider)||"";const name=prov?prov[0].toUpperCase()+prov.slice(1):(String(id).slice(0,8)||"?");return name;}
function cpTime(sec){const d=new Date(sec*1000);return d.toLocaleString([], {weekday:"short",month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"});}
function cpHtml(cp){const t=cpTime(cp.at);if(cp.kind==="compact")return `<div class="hp-cp hp-cp-compact" role="separator" aria-label="Context compacted at ${esc(t)}">${CP_WAVE}<span class="hp-cp-label"><span class="hp-cp-prov">Compacted</span><span class="hp-cp-time">${esc(t)}</span></span>${CP_WAVE}</div>`;const lbl=`<span class="hp-cp-prov">${esc(cpWho(cp.from))}</span><span class="hp-cp-arrow">→</span><span class="hp-cp-prov">${esc(cpWho(cp.to))}</span><span class="hp-cp-time">${esc(t)}</span>`;return `<div class="hp-cp" role="separator" aria-label="${esc(`Principal swapped from ${cpWho(cp.from)} to ${cpWho(cp.to)} at ${t}`)}" title="${esc(cp.reason||"Principal swapped")}">${CP_WAVE}<span class="hp-cp-label">${lbl}</span>${CP_WAVE}</div>`;}
function renderChat(){const body=hp.querySelector(".hp-body");ensureThread(body);
  chatEl.hidden=sel.kind!=="chat"&&sel.kind!=="new";threadEl.hidden=chatEl.hidden;if(sel.kind!=="chat")return;
  renderTransit();
  if(!picked){const rec=visibleChats().slice(0,6);chatEl.innerHTML=`<div class="hp-empty"><p class="hp-empty-h">${rec.length?"Pick up where you left off":"No chats yet"}</p>${rec.length?`<div class="hp-empty-list">${rec.map(c=>`<button type="button" class="hp-empty-row" data-hp-pick="${esc(c.id)}"><span class="hp-empty-t">${esc(chatTitle(c))}</span><span class="hp-empty-m">${esc(c.source||"")}</span></button>`).join("")}</div>`:""}<button type="button" class="hp-empty-new" data-hp-emptynew>New chat</button></div>`;return;}
  const id=picked;bindChat(id);const c=chatView(id),p=pend.get(id);
  if(!c){chatEl.innerHTML=SKEL;return;}
  const kickStart=()=>{if(starting.timer)return;starting.timer=setTimeout(()=>{starting.timer=0;if(picked===id&&sel.kind==="chat")SC.refresh(id);},1500);};
  if(c.err){
    /* A chat started a moment ago may not have a transcript yet: keep "Starting…" until SSE updates arrive. */
    if(starting.id===id&&Date.now()<starting.until){chatEl.innerHTML=`<p class="hp-chat-note">Starting the session…</p>`;kickStart();return;}
    chatEl.innerHTML=`<p class="hp-chat-note">Couldn't load this chat: ${esc(c.err)}</p>`;return;}
  if(c.d&&c.d.starting){chatEl.innerHTML=`<p class="hp-chat-note">Starting the session…</p>`;kickStart();return;}
  if(starting.timer){clearTimeout(starting.timer);starting.timer=0;}
  const all=c.d.turns||[];const t=all.slice(-winN);const near=chatEl.scrollHeight-chatEl.scrollTop-chatEl.clientHeight<80;const fromBottom=chatEl.scrollHeight-chatEl.scrollTop;
  const cps=(c.d.checkpoints||[]).slice();for(const ts of (c.ctx&&c.ctx.compactions)||[])cps.push({at:ts,kind:"compact"});cps.sort((a,b)=>a.at-b.at);const firstTs=(t.find(m=>m.ts)||{}).ts||0;
  let html=(c.d.total?c.d.total>t.length:all.length>=winN)?'<button type="button" class="hp-older" data-hp-older>Load older messages</button>':"",lastProv="";const conv=c.d.conversation;for(const m of t){if(conv&&m.provider&&lastProv&&m.provider!==lastProv)html+=`<div class="hp-seg">Continued on ${esc(SRC[m.provider]||m.provider)}</div>`;if(m.provider)lastProv=m.provider;while(cps.length&&m.ts&&cps[0].at<=m.ts){const cp=cps.shift();if(!firstTs||cp.at>=firstTs)html+=cpHtml(cp);}
    html+=msgHtml(m);}
  for(const cp of cps)if(!firstTs||cp.at>=firstTs)html+=cpHtml(cp);
  if(p&&!p.folded&&!t.slice(-3).some(m=>m.role==="user"&&m.text.trim()===p.text.trim()))html+=`<div class="hp-msg user pending">${esc(p.text)}</div>`;
  const live=c.live,lt=live&&live.running&&live.text?String(live.text).trim():"";
  if(lt&&!all.slice(-3).some(m=>m.role==="assistant"&&String(m.text||"").trim().startsWith(lt.slice(0,80))))html+=`<div class="hp-msg assistant md hp-live">${md(live.text)}</div>`;
  if(p)html+=stepsHtml(p);
  if(c.d.reverted>0)html+=`<div class="hp-seg">Reverted ${c.d.reverted} message${c.d.reverted>1?"s":""}${c.d.cut_sent?"":` · <button type="button" class="hp-undo" data-hp-undo>Undo</button>`}</div>`;
  chatEl.innerHTML=ctxBarHtml(c.ctx,ctxNote[picked])+(html||`<p class="hp-chat-note">No messages in this chat yet.</p>`);
  {const ob=chatEl.querySelector("[data-hp-older]");if(ob&&winN<100&&typeof IntersectionObserver!=="undefined"){const io=new IntersectionObserver(es=>{/* only when the reader has scrolled up: never on first paint at the bottom */if(es.some(x=>x.isIntersecting)&&chatEl.scrollHeight-chatEl.scrollTop-chatEl.clientHeight>200){io.disconnect();if(!ob.disabled&&ob.isConnected)ob.click();}},{root:chatEl,rootMargin:"300px 0px 0px 0px"});io.observe(ob);}}
  const pin=near||p||chatEl.dataset.hpFor!==id;chatEl.dataset.hpFor=id;
  const place=()=>{chatEl.scrollTop=pin?chatEl.scrollHeight:chatEl.scrollHeight-fromBottom;};place();
  /* culled rows resolve their real height a frame later; place again so the view doesn't bounce */
  requestAnimationFrame(()=>{place();requestAnimationFrame(place);});}
function repaint(id){if(picked===id&&sel.kind==="chat"&&!hp.hidden)renderChat();}
/* Delivery steps follow the chat's state from chatcore. "Replied" waits for the run to stop,
   or, where a provider reports no run state, for the transcript to go quiet. */
const PEND_GIVEUP_MS=240000,PEND_QUIET_MS=2500;
function stepPend(id,s){const p=pend.get(id);if(!p||p.done||p.steps[0].status!=="done")return;
  const waitIdx=p.steps.length>3?Math.max(1,p.steps.length-2):1,replyIdx=p.steps.length>3?p.steps.length-1:2;
  const t=s.turns||[],L=s.live||{},want=p.text.trim();
  const i=t.map(m=>m.role==="user"&&String(m.text||"").trim()===want).lastIndexOf(true);
  const mine=i>=0||(L.user&&want.startsWith(String(L.user).trim().slice(0,60)));
  if(mine&&!p.seen){p.seen=true;p.steps[waitIdx].detail.push(`message is in the chat · ${secs(p)}s`);}
  (L.activity||[]).forEach(a=>{if(a&&!p.steps[waitIdx].detail.includes(a))p.steps[waitIdx].detail.push(a);});
  const la=lastA(t),answered=(i>=0&&t.slice(i+1).some(m=>m.role==="assistant"))||(la&&la!==p.baseLast&&(s.total||0)>p.baseTotal);
  const replying=answered||(L.running&&L.text&&String(L.text).trim());
  if(replying&&p.steps[waitIdx].status!=="done"){p.steps[waitIdx].status="done";p.steps[waitIdx].label="Agent is replying";p.steps[replyIdx].status="running";}
  clearTimeout(p.quiet);
  if(answered&&!L.running)p.quiet=setTimeout(()=>{if(p.done)return;clearTimeout(p.timer);p.steps[waitIdx].label="Agent replied";p.steps[replyIdx].status="done";p.done=true;repaint(id);
    setTimeout(()=>{p.folded=`Replied in ${secs(p)}s`;repaint(id);},1200);},PEND_QUIET_MS);}
/* A send to a chat that isn't the open tab keeps its own subscription until the reply lands. */
function followPend(id){if(id===subId)return;const p=pend.get(id);if(!p||p.done||p.unsub)return;
  p.unsub=SC.subscribe(id,s=>{stepPend(id,s);repaint(id);if(p.done&&p.unsub){p.unsub();p.unsub=null;if(id!==subId)SC.close(id);}});fetchChat(id);}
document.addEventListener("composer:sending",e=>{const {chat:id,text,label}=e.detail||{};if(!id)return;const prev=pend.get(id);if(prev){clearTimeout(prev.timer);clearTimeout(prev.quiet);if(prev.unsub)prev.unsub();}
  const s=SC.get(id),t=s.turns||[];
  pend.set(id,{text:String(text||""),at:Date.now(),baseLast:lastA(t),baseTotal:s.total||0,steps:[
    {label:`Sending to ${label}`,status:"running",detail:[]},{label:"Waiting for the reply",status:"pending",detail:[]},{label:"Reply in the panel",status:"pending",detail:[]}]});repaint(id);});
document.addEventListener("composer:sent",async e=>{const {chat:id,method,provider:nextProv,handoff_stats:hs}=e.detail||{};
  if(method==="new"&&id){starting={id,until:Date.now()+60000,timer:0};picked=id;setLastPick(pprov,id);winN=20;chatOptOut=null;sel={kind:"chat"};chatsAt=0;
    chatEl.innerHTML=`<p class="hp-chat-note">Starting the session…</p>`;await loadChats(true).catch(()=>{});renderTabs();showBody();return;}
  const p=pend.get(id);if(!p)return;
  const srcProv=nextProv||pprov;
  if(hs&&Array.isArray(hs.stages)&&hs.stages.length){
    const modeTxt=hs.full?"Full conversation":`k=${hs.k} (Turns ${hs.slice_range?`${hs.slice_range[0]}..${hs.slice_range[1]}`:"tail"} of ${hs.total_turns||"?"})`;
    const tokTxt=`${hs.raw_tokens||0} → ${hs.normalized_tokens||0} tok (-${hs.reduction_pct||0}%)`;
    p.steps=[
      ...hs.stages.map(st=>({label:st.label||st.id,status:st.status==="error"?"error":"done",detail:st.detail?[st.detail]:[]})),
      {label:`Waiting for ${SRC[srcProv]||srcProv}`,status:"running",detail:[modeTxt,tokTxt,"watching live"]},
      {label:"Reply in the panel",status:"pending",detail:[]}
    ];
  }else{
    p.steps[0].status="done";p.steps[0].label="Delivered";p.steps[0].detail=[`via ${method||"live delivery"} · ${secs(p)}s`];
    p.steps[1].status="running";p.steps[1].detail=["watching live"];
  }
  const waitIdx=Math.max(1,p.steps.length-2);
  p.timer=setTimeout(()=>{if(p.done)return;clearTimeout(p.quiet);p.steps[waitIdx].status="error";p.steps[waitIdx].label="No reply after 4 minutes";p.steps[waitIdx].detail.push("the chat may be idle or waiting on a permission");p.done=true;repaint(id);},PEND_GIVEUP_MS);
  if(id===subId)SC.open(id,srcProv,{turns:winN});else followPend(id);
  stepPend(id,SC.get(id));repaint(id);});
document.addEventListener("composer:send-failed",e=>{const {chat:id,error}=e.detail||{};const p=pend.get(id);if(!p)return;clearTimeout(p.timer);clearTimeout(p.quiet);
  p.steps[0].status="error";p.steps[0].label="Not delivered";p.steps[0].detail=[String(error||"unknown error")];p.done=true;repaint(id);});
/* The composer sends to the selected chat tab while one is showing. */
let chatOptOut=null;
function syncTarget(){const c=C();if(!c?.setChat||sel.kind==="new")return;
  const open=!hp.hidden&&sel.kind==="chat"&&picked&&picked!==chatOptOut;
  const ch=open?(chats.find(x=>x.id===picked)||{id:picked,source:pprov,srcLabel:SRC[pprov]||pprov,label:"this chat"}):null;
  c.setChat(ch?{id:ch.id,source:ch.source,srcLabel:SRC[ch.source]||ch.source,label:(ch.aliases&&ch.aliases[0]&&"@"+String(ch.aliases[0]).replace(/^@/,""))||ch.tab_name||ch.title||ch.label||"this chat"}:null);}
document.addEventListener("composer:chat-cleared",()=>{chatOptOut=picked;});
/* Principal swap: an explicit "Make principal" menu click makes a chat the principal of every live work thread.
   POST /api/composer/principal moves the thread's workers (reports follow), open permissions and leases,
   and briefs the new chat. */
let ini=(()=>{try{return JSON.parse(localStorage.getItem("hp-initiator")||"null");}catch(_){return null;}})();
const chatLabel=ch=>(ch.aliases&&ch.aliases[0]&&"@"+String(ch.aliases[0]).replace(/^@/,""))||cleanTitle(ch.tab_name)||cleanTitle(ch.title)||ch.id.slice(0,8);
async function liveRoots(){const r=await fetch("/api/status?view=next",{cache:"no-store"});const d=await r.json();
  const live=(d.workers||[]).filter(w=>!/^(completed|failed|stopped|rejected|cancelled|error)$/.test(String(w.status||"")));
  return[...new Set(live.map(w=>w.thread_root).filter(Boolean))];}
/* The open chat: one subscription and one live stream (chatcore), dropped when the panel hides or the tab changes. */
let subId="",unsub=null;const savedD=new WeakSet();
function onChatState(id,s){if(s.d&&!savedD.has(s.d)){savedD.add(s.d);pcSave(id,s.d);}
  const cp=s.conversation&&s.conversation.current_provider;if(cp)C()?.followProvider?.(id,cp);stepPend(id,s);repaint(id);}
function bindChat(id){id=id||"";if(id===subId)return;if(unsub){unsub();unsub=null;}const old=subId;subId=id;
  if(old){const p=pend.get(old);if(p&&!p.done)followPend(old);else SC.close(old);}
  if(id){unsub=SC.subscribe(id,s=>onChatState(id,s));fetchChat(id);}}
function showBody(){const body=hp.querySelector(".hp-body");renderChat();syncTarget();if(sel.kind!=="chat"||!picked)bindChat("");
  for(const p of pages){const on=sel.kind==="page"&&sel.r===p.r;
    if(on&&!p.frame){p.frame=document.createElement("iframe");p.frame.className="hp-frame";p.frame.title=pageLabel(p.r);p.frame.src=frameSrc(p.full);body.append(p.frame);}
    if(p.frame)p.frame.hidden=!on;}}
function renderTabs(){let list=visibleChats();
  if(sel.kind==="chat"&&picked&&!list.some(c=>c.id===picked)){const k=chats.find(c=>c.id===picked&&c.source===pprov);if(k)list.unshift(k);else if(starting.id===picked&&Date.now()<starting.until){list.unshift({id:picked,source:pprov,title:"Starting…",project:""});}}
  const ptabs=pages.map(p=>{const on=sel.kind==="page"&&sel.r===p.r;return`<div class="hp-tab page" role="tab" tabindex="0" data-hp-page="${esc(p.r)}" aria-selected="${String(on)}">${pageIcon(p.r)}<span>${esc(pageLabel(p.r))}</span><button type="button" class="hp-tx" data-hp-close="${esc(p.r)}" aria-label="Close ${esc(pageLabel(p.r))}">${XS}</button></div>`;}).join("");
  const scope=getScope(pprov);const cap=coverage[pprov];const scopeLabel=scope.mode==="project"?"This project":scope.mode==="folder"?(scope.folder||"Folder"):scope.mode==="open"?"Open windows":"All chats";
  const head=`<button type="button" class="hp-scope" data-hp-scope title="Load scope: ${esc(scopeLabel)}">Load: ${esc(scopeLabel)}</button>`;
  if(!list.length){tabsEl.innerHTML=ptabs+head+(sel.kind==="new"?`<button type="button" class="hp-tab" role="tab" aria-selected="true" data-hp-newtab><span>New ${esc(SRC[pprov]||pprov)} chat</span></button>`:"")+`<span class="hp-tabs-empty">${chatsAt?`No ${esc(SRC[pprov]||pprov)} chats.`:"Loading chats…"}</span>`;tabsEl.querySelector('[aria-selected="true"]')?.scrollIntoView({block:"nearest",inline:"nearest"});return;}
  if(sel.kind!=="new"&&!list.some(c=>c.id===picked)){const lp=lastPick(pprov);picked=(lp&&list.some(c=>c.id===lp))?lp:list[0].id;winN=20;if(!hp.hidden)queueMicrotask(showBody);}
  tabsEl.innerHTML=ptabs+head+(sel.kind==="new"?`<button type="button" class="hp-tab" role="tab" aria-selected="true" data-hp-newtab><span>New ${esc(SRC[pprov]||pprov)} chat</span></button>`:"")+list.map(c=>{const m=chatMeta(c),n=chatTitle(c),on=sel.kind==="chat"&&c.id===picked;
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

/* A folder scope holds the chat's project label, which some providers set to a bare name (Antigravity: the workspace name).
   New chats need a real path, so resolve it from a chat in that scope; with none, the "in folder" option is not offered. */
const isAbsDir=p=>/^(\/|[A-Za-z]:[\\/]|\\\\)/.test(p);
function chatDir(c){const raw=c.project_dir||(Array.isArray(c._project_path)?c._project_path[0]:c._project_path)||c.cwd||c.project||"";
  let p=String(raw||"");if(p.startsWith("file://")){try{p=decodeURIComponent(p.slice(7));}catch(_){p=p.slice(7);}}return isAbsDir(p)?p:"";}
function scopeDir(sc){if(sc.mode!=="folder"||!sc.folder)return hudProject||"";if(isAbsDir(sc.folder))return sc.folder;
  const c=chats.find(x=>x.source===pprov&&String(x.project||"")===sc.folder&&chatDir(x));return c?chatDir(c):"";}
function openPicker(anchor){hideHover();const closed=closedFor(pprov);const archived=chats.filter(c=>c.source===pprov&&chatMeta(c).archived);
  const label=SRC[pprov]||pprov;
  const sc=getScope(pprov),dir=scopeDir(sc),base=dir.split(/[\\/]/).filter(Boolean).pop()||"";
  let html=(dir?`<button type="button" data-hp-newchat data-dir="${esc(dir)}" title="${esc(dir)}">New ${esc(label)} chat in ${esc(base)}</button>`:"")+`<button type="button" data-hp-newchat data-dir="">New ${esc(label)} chat · no folder</button><div class="hp-sep"></div><div style="padding:6px 10px;font-size:12px;color:var(--muted)">Closed / hidden (${esc(pprov)})</div>`;
  if(closed.length) closed.slice().reverse().forEach(x=>{html+=`<button type="button" data-hp-unclose="${esc(x.id)}">${esc(x.title||x.id)}</button>`;}); else html+=`<div style="padding:6px 10px;font-size:13px">No closed tabs.</div>`;
  html+=`<div class="hp-sep"></div><button type="button" data-hp-toggle-archived">${showArchived?"Hide archived":"Show archived"}</button>`;
  if(showArchived&&archived.length){html+=`<div class="hp-sep"></div><div style="padding:6px 10px;font-size:12px;color:var(--muted)">Archived</div>`;archived.slice(0,20).forEach(c=>{html+=`<button type="button" data-hp-unarchive="${esc(c.id)}">${esc(chatTitle(c))}</button>`;});}
  menuEl.innerHTML=html;menuEl.hidden=false;const r=anchor.getBoundingClientRect();menuEl.style.left=Math.min(r.left,innerWidth-260)+"px";menuEl.style.top=Math.min(r.bottom+6,innerHeight-320)+"px";}

function openTabMenu(c,x,y){hideHover();const m=chatMeta(c),pinned=!!m.pinned,unread=(m.tags||[]).includes("unread");
  menuEl.innerHTML=`<button type="button" data-act="rename">Rename</button><button type="button" data-act="pin">${pinned?"Unpin":"Pin"}</button><button type="button" data-act="unread">${unread?"Mark read":"Mark unread"}</button><button type="button" data-act="settle">${m.settled?"Unsettle":"Settle"}</button><button type="button" data-act="snooze">Snooze 1h</button><button type="button" data-act="initiator">Make principal</button><div class="hp-sep"></div><button type="button" data-act="copy-id">Copy chat ID</button><button type="button" data-act="copy-path">Copy path</button><div class="hp-sep"></div><button type="button" data-act="open-tab">Open in new tab</button><div class="hp-sep"></div><button type="button" data-act="archive">Archive</button><button type="button" data-act="delete">Remove from Subway</button><button type="button" class="danger" data-act="delete-permanent">Delete from ${esc(SRC[c.source]||c.source||"provider")}</button>`;
  menuEl.hidden=false;menuEl.style.left=Math.min(x,innerWidth-240)+"px";menuEl.style.top=Math.min(y,innerHeight-360)+"px";
  menuEl.onclick=async ev=>{const b=ev.target.closest("button");if(!b)return;const act=b.dataset.act;hideMenu();
    try{
      if(act==="rename"){const t=prompt("Rename chat",chatTitle(c)); if(t!=null){await postAction(c.id,"rename",t); await loadChats(true); renderStrip(); renderTabs(); showBody();}}
      else if(act==="pin"){await postAction(c.id,"pin",pinned?"off":"on"); await loadChats(true); renderStrip(); renderTabs(); showBody();}
      else if(act==="unread"){await postAction(c.id,"tag",unread?{remove:"unread"}:{add:"unread"}); await loadChats(true); renderStrip(); renderTabs(); showBody();}
      else if(act==="settle"){await postAction(c.id,"settle",m.settled?"off":"on"); await loadChats(true); renderStrip(); renderTabs(); showBody();}
      else if(act==="snooze"){await postAction(c.id,"snooze",3600); await loadChats(true); renderStrip(); renderTabs(); showBody();}
      else if(act==="initiator"){await handOver(c);}
      else if(act==="copy-id"){await navigator.clipboard?.writeText(String(c.id||"")); window.toast?.("Copied chat ID");}
      else if(act==="copy-path"){await navigator.clipboard?.writeText(String(c.path||c.project||c.project_dir||c.id||"")); window.toast?.("Copied path");}
      else if(act==="open-tab"){openInNewTab(c);}
      else if(act==="archive"){await postAction(c.id,"archive","on"); await loadChats(true); renderStrip(); renderTabs(); showBody();}
      else if(act==="delete"){const src=SRC[c.source]||c.source||"provider"; if(confirm(`Remove from Subway? The chat stays in ${src} and can be recalled.`)){await postAction(c.id,"delete","on"); await postAction(c.id,"archive","on"); await loadChats(true); renderStrip(); renderTabs(); showBody();}}
      else if(act==="delete-permanent"){const src=SRC[c.source]||c.source||"provider"; if(confirm(`Permanently delete this chat from ${src} AND Subway? This deletes the session on disk and cannot be undone.`)){await postAction(c.id,"delete_permanent","on"); await loadChats(true); renderStrip(); renderTabs(); showBody();}}
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
  const b=e.target.closest("[data-hp-tab]");if(!b)return;picked=b.dataset.hpTab;setLastPick(pprov,picked);winN=20;chatOptOut=null;sel={kind:"chat"};renderTabs();showBody();});
hp.addEventListener("click",async e=>{const sh=e.target.closest("[data-hp-sheet]");if(sh){e.stopPropagation();const k=sh.dataset.hpSheet;activeSheet=activeSheet===k?"":k;renderTransit();return;}const at=e.target.closest("[data-hp-atlas]");if(at){e.stopPropagation();if(picked)openPage("threads/"+encodeURIComponent(picked));return;}const mm=e.target.closest("[data-hp-mmenu]");if(mm){e.stopPropagation();hideHover();const tid=mm.dataset.hpMmenu;const cur=chats.find(x=>x.id===picked),prov=cur&&cur.source||pprov;let h=`<button type="button" data-hp-cut="${esc(tid)}">Revert to here</button><button type="button" data-hp-fork="${esc(tid)}" data-prov="${esc(prov)}">Fork here</button>`;for(const s of srcs())if(s.id!==prov)h+=`<button type="button" data-hp-fork="${esc(tid)}" data-prov="${esc(s.id)}">Fork to ${esc(s.label)}</button>`;menuEl.innerHTML=h;menuEl.hidden=false;const r=mm.getBoundingClientRect();menuEl.style.left=Math.min(r.left,innerWidth-260)+"px";menuEl.style.top=Math.min(r.bottom+6,innerHeight-300)+"px";return;}const cb=e.target.closest("[data-hp-compact]");if(cb){e.stopPropagation();const btn=cb,cid=picked;const say=m=>{ctxNote[cid]=m;const st=chatEl.querySelector(".hp-ctx-status");if(st)st.textContent=m;else chatEl.insertAdjacentHTML("afterbegin",`<p class="hp-ctx-status" role="status" aria-live="polite">${esc(m)}</p>`);};btn.disabled=true;btn.textContent="Compacting…";say("Compacting…");try{const res=await SC.compact(cid),d=res.data||{};if(res.ok)say(`Compaction requested (${d.command||d.method||""}) · the agent will summarise and continue`);else say(res.error);}catch(err){say(String(err.message||err));}finally{btn.disabled=false;btn.textContent="Compact";}return;}const ob=e.target.closest("[data-hp-older]");if(ob){e.stopPropagation();ob.disabled=true;ob.textContent="Loading…";winN+=20;fetchChat(picked,winN);return;}const p=e.target.closest("[data-hp-pick]");if(p){e.stopPropagation();picked=p.dataset.hpPick;setLastPick(pprov,picked);winN=20;chatOptOut=null;sel={kind:"chat"};renderTabs();showBody();return;}const undo=e.target.closest("[data-hp-undo]");if(undo){e.stopPropagation();try{const id=picked,res=await SC.uncut(id);if(!res.ok){chatEl.insertAdjacentHTML("beforeend",`<p class="hp-chat-note">${esc(res.error)}</p>`);return;}SC.refresh(id);}catch(err){chatEl.insertAdjacentHTML("beforeend",`<p class="hp-chat-note">${esc(String(err.message||err))}</p>`);}return;}if(e.target.closest("[data-hp-emptynew]")){e.stopPropagation();hp.querySelector("[data-hp-new]")?.click();}});
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
menuEl.addEventListener("click",async e=>{const b=e.target.closest('button'); if(!b) return; if(b.dataset.hpCut!=null||b.dataset.hpFork!=null){hideMenu();const fork=b.dataset.hpFork!=null,tid=fork?b.dataset.hpFork:b.dataset.hpCut,id=picked;
  const note=t=>chatEl.insertAdjacentHTML("beforeend",`<p class="hp-chat-note">${esc(t)}</p>`);
  if(!fork&&!confirm("Messages after this point are hidden. Code changes stay; use Landing to roll back files."))return;
  let text="";if(fork){const v=prompt("First message for the fork (optional):","Continue from here.");if(v===null)return;text=v.trim()||"Continue from here.";note("Forking… starting a new session");}
  const res=fork?await SC.fork(id,tid,b.dataset.prov||"",text):await SC.cut(id,tid);if(!res.ok){note(res.error);return;}const d=res.data||{};
  if(fork){note(`Forked to ${SRC[d.provider]||d.provider} · ${d.method==="transplant"?"full history":"summary"}`);
    if(d.chat_id){if(d.provider&&d.provider!==pprov){pprov=d.provider;try{localStorage.setItem("hp-prov",pprov);}catch(_){}}
      starting={id:d.chat_id,until:Date.now()+60000,timer:0};picked=d.chat_id;setLastPick(pprov,picked);winN=20;chatOptOut=null;sel={kind:"chat"};chatsAt=0;loadChats(true).then(()=>{renderStrip();renderTabs();showBody();},()=>{});}return;}
  SC.refresh(id);return;}
if(b.dataset.hpNewchat!=null){hideMenu(); const label=SRC[pprov]||pprov; const cmp=C(); if(cmp&&!cmp.isOpen)cmp.open?.(); const dir=b.dataset.dir||""; cmp?.newChat?.({source:pprov,label,project_dir:dir});
  /* Show the fresh chat in the panel too, so the click visibly does something. */
  picked=null; sel={kind:"new"}; hp.querySelectorAll("[data-hp-pick]").forEach(t=>t.setAttribute("aria-selected","false")); renderTabs();
  chatEl.innerHTML=`<div class="hp-newchat"><b>New ${esc(label)} chat</b><span>Write the first message below. It starts a fresh ${esc(label)} session ${dir?`in ${esc(dir)}`:"with no project folder"} and opens here.</span></div>`;
  requestAnimationFrame(()=>document.querySelector(".cmp textarea")?.focus()); return;} if(b.dataset.hpScopeSet){setScope(pprov,JSON.parse(b.dataset.hpScopeSet)); hideMenu(); return;} if(b.dataset.hpUnclose){unclose(b.dataset.hpUnclose,pprov); hideMenu(); return;} if(b.dataset.hpToggleArchived!=null){showArchived=!showArchived; hideMenu(); renderStrip(); renderTabs(); return;} if(b.dataset.hpUnarchive){try{await postAction(b.dataset.hpUnarchive,"archive","off"); chatsAt=0; await loadChats(); renderStrip(); renderTabs();}catch(err){window.toast?.error?.(String(err.message||err));} hideMenu(); return;}});

document.addEventListener("click",e=>{if(hp.hidden)return;const a=e.target.closest('a[href^="#"]');if(!a||a.closest(".cmp"))return;
  const f=a.getAttribute("href").slice(1);if(!f)return;e.preventDefault();e.stopPropagation();openPage(f);},true);
addEventListener("hashchange",()=>{if(hp.hidden)return;const f=location.hash.slice(1)||"home";if(f.split("/")[0]==="home")return;
  history.replaceState(null,"","#home");dispatchEvent(new HashChangeEvent("hashchange"));openPage(f);});
hp.querySelector("[data-hp-new]").addEventListener("click",e=>{e.stopPropagation(); openPicker(e.currentTarget);});
stripEl.addEventListener("click",e=>{const b=e.target.closest("[data-hp-prov]");if(!b||b.dataset.hpProv===pprov)return;
  pprov=b.dataset.hpProv;try{localStorage.setItem("hp-prov",pprov);}catch(_){}picked=null;sel={kind:"chat"};renderStrip();renderTabs();});

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
  if(show===!hp.hidden)return;hp.hidden=!show;document.body.classList.toggle("hp-on",show);if(!show){C()?.setChat(null);bindChat("");}
  if(show){fitComposer();loadNeeds();renderNav();Promise.all([loadHomePrefs(),loadCoverage(),loadHudProject(),loadChats()]).then(()=>{loadLinks().then(()=>{if(!hp.hidden)renderTabs();});renderStrip();renderTabs();showBody();});}}
addEventListener("focus",()=>{if(!hp.hidden&&Date.now()-chatsAt>5000)loadChats(true).then(()=>{renderStrip();renderTabs();showBody();});});
let pushAt=0;
addEventListener("subway:push",ev=>{if(hp.hidden)return;const types=ev.detail&&ev.detail.types||ev.detail&&ev.detail.type?[].concat(ev.detail.types||ev.detail.type||[]):[];
  if(!types.some(t=>t==="message"||t==="status"))return;const now=Date.now();if(now-pushAt<400)return;pushAt=now;
  loadChats(true).then(()=>{renderStrip();renderTabs();showBody();});});
addEventListener("subway:event",ev=>{if(hp.hidden)return;const n=String((ev.detail&&ev.detail.name)||"");
  if(n!=="message"&&n!=="status"&&n!=="connect")return;
  const id=picked,c=id?chatView(id):null;
  const isStarting=(starting.id&&Date.now()<starting.until)||(c&&c.d&&c.d.starting);
  if(!isStarting)return;
  chatsAt=0;loadChats(true).then(()=>{renderStrip();renderTabs();if(picked===id&&sel.kind==="chat")SC.refresh(id);}).catch(()=>{});});
const cmp=document.querySelector(".cmp");
if(cmp&&window.ResizeObserver)new ResizeObserver(()=>{if(!cmp.hidden)document.body.style.setProperty("--hp-ch",cmp.offsetHeight+"px");}).observe(cmp);
if(cmp)new MutationObserver(sync).observe(cmp,{attributes:true,attributeFilter:["hidden"]});
addEventListener("hashchange",sync);
document.addEventListener("visibilitychange",()=>SC.setVisible(!document.hidden));SC.setVisible(!document.hidden);
})();
