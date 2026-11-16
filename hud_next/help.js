/* Help page. Content from hud/help.js (updated for the new nav); left rail is a vanilla port of
   rare-ui "proximity-sidebar" (swamimalode07/rare-ui, MIT): dashes that widen as the pointer nears. */
(function(){
const SECTIONS=[
 {title:"Getting started",sub:"How to get up and running with Subway",items:[
  ["Install","subway install sets up MCP and guard hooks for each provider found."],
  ["Open the HUD","subway hud --open starts the dashboard on 127.0.0.1:7890."],
  ["Connect a chat","Any MCP chat sees Subway's tools; after an update reconnect with /mcp."],
  ["Approve a worker","Approve via Home card, subway approve <id>, or give the chat the approval code."]]},
 {title:"Pages",sub:"Where everything lives after the redesign",items:[
  ["Home","What needs you (approvals, alerts, low limits) and what's running now. The composer sits beside the nav here."],
  ["Threads","Chats that used Subway and everything they started: nodes, edges, cost and per-thread settings."],
  ["Devices","Machines on your network running Subway, and which chats live on each."],
  ["Workers","Delegated agents, their live state and logs, and pending permissions."],
  ["Settings, Usage, Landing","In the account menu, bottom right. Usage holds limits, tokens, stats and diagnostics; Landing holds branches waiting to land and overlaps to resolve."],
  ["Collab","Linked stations share chats, files and coordination."],
  ["See my screen","Screen sharing presets (off / snapshot / live); every session needs the sharer's click."],
  ["Connect apps","In the composer bar: configure apps a worker may use."],
  ["Diagnostics","In Usage: live CPU and RAM samples."],
  ["Low-power mode","Settings → Appearance → Display: Auto/On/Off — static map and background for old laptops."]]},
 {title:"Composer",sub:"Options when delegating work",items:[
  ["This station","Where the worker runs."],
  ["Provider","The LLM provider running the agent."],
  ["Model","Model family and tier assigned to the task."],
  ["Effort","How long a model thinks."],
  ["Now/Auto","Run now or schedule for when limits reset."],
  ["Budget ok","The provider has room for this brief."]]},
 {title:"Shortcuts",sub:"Keys that work anywhere in the HUD",items:[
  ["⌘ ,","Open Settings."],["?","Open this page (when you're not typing)."],
  ["← → Home End","Move along the bottom nav once it has focus."],
  ["/","In the composer: commands such as Iterate, Broadcast, Mail, Claim."],
  ["@","In the composer: target a running worker (steer) or a chat (mail)."],
  ["#","In the composer: attach exact lines of a file as a code packet."],
  ["&","Start a line with & to fan out another worker with the same context."],
  ["⌘ ↵","While steering a worker: recall it now instead of stacking."],
  ["Esc","Close a menu, stop a send, then close the composer (the draft is kept)."]]},
 {title:"Key terms",sub:"Words used throughout Subway",items:[
  ["Station","A connected chat window, such as Claude Code, Antigravity or OpenCode."],
  ["Track and ledger","Use a track when several chats work on one goal: decisions broadcast there are what each chat reads on join."],
  ["Lease","A claim on a file so two agents don't edit it at once; taken automatically on first edit."],
  ["Worker","An autonomous agent Subway starts and supervises to do one task."],
  ["Principal","The chat that receives worker wakes, permission requests and leases. Change it with set_principal or the HUD principal picker."],
  ["Budgets","Token limits per provider and per chat. Warns at 80%, blocks at 100%."],
  ["Token cap and wrap-up","Workers wrap up at two thirds of their cap, and never start a call that would pass it."],
  ["Effort","How long a model thinks. Each model offers its own levels; Subway sends the nearest one it supports, or none when the levels are unknown."],
  ["Verifier","An automated PASS, FLAG or FAIL check: proof that a change is mechanically sound, not that it is correct."],
  ["Fast mail","Short messages between chats with states: queued, delivered, read, replied."],
  ["Isolate","Worker edits in its own git worktree; changes come back as a branch."],
  ["Land","Merge the branch, or preserve it for review — setting land_mode."],
  ["Approval code","6 characters shown in the HUD; lets a chat launch a pending worker."],
  ["Sub-workers","A worker may start a few helpers; one more generation at most, scaled by RAM."],
  ["Code packets","code=[\"path:symbol\"] puts exact lines in the brief so workers edit instead of searching."],
  ["Review and doctor","An automatic reviewer pass; a doctor rebriefs a failed worker."],
  ["Watchdog","A worker that makes 12 tool calls without an edit is stopped so you can rebrief it."],
  ["Read budget","Limits how much the principal chat reads before delegating."],
  ["Principal small edits","principal_small_edit_lines: 0 blocks principal edits; N allows edits up to N changed lines."],
  ["MCP relay","mcp_relay: each chat's Subway connection becomes a ~25 MB relay to the HUD instead of a ~100 MB server; off by default."]]},
 {title:"Thread lifecycle",sub:"What state a thread is in right now",items:[
  ["Working","A node in the thread is running now."],["Monitoring","Its workers finished or stopped; kept for review."],
  ["Done","Every node ended and you (or the principal) marked the results used."],["Snoozed","Parked until a set time, such as a limit reset."],
  ["Settled","Acknowledged and put away; still searchable, never deleted."]]},
 {title:"Main MCP tools",sub:"One line each",items:[
  ["read_chat","Read messages, decisions or diffs from another chat."],
  ["search_chat","Search every chat for a phrase or snippet."],
  ["list_chats","List recent lines, active open tabs and connected sessions."],
  ["tag_chat","Assign an alias or tag to a chat session."],
  ["send_note","Send a note to another chat's mailbox."],
  ["check_notes","Check for incoming messages and queued mail."],
  ["reply_note","Send a threaded reply to a fast mail note."],
  ["ask_room","Ask every chat in a project and collect a digest."],
  ["claim_file","Lease a file before editing it."],
  ["release_file","Release an active file lease after edits are done."],
  ["spin_worker","Launch one supervised worker."],
  ["run_concurrent","Run multiple worker tasks in parallel."],
  ["on_worker_done","Handle completion callbacks when a worker finishes."],
  ["instruct_worker","Send guidance or steer a running worker."],
  ["worker_status","Check a worker's state, tokens and log."],
  ["stop_worker","Stop a running worker."],
  ["reject_worker","Reject a pending or unapproved worker."],
  ["approve_worker","Launch a pending worker using its worker ID or code."],
  ["answer_permission","Allow or deny a worker's pending request."],
  ["quota_status","Remaining tokens and reset times per provider."],
  ["broadcast_track","Broadcast decisions or updates to collaborating agents on a track."],
  ["join_track","Catch up on shared track state and decisions."],
  ["set_principal","Set which chat acts as the principal."],
  ["hub_call","Call an MCP tool on a remote hub server."]]},
 {title:"Terminal commands",sub:"The subway binary",items:[
  ["subway hud [port] [--open]","Start the HUD on 127.0.0.1."],
  ["subway workers","List workers, with approval codes for pending ones."],
  ["subway approve <worker_id>","Launch a pending worker."],
  ["subway reject <worker_id>","Reject and remove a pending worker."],
  ["subway stop <worker_id>","Stop a running worker."],
  ["subway watch [--stream]","Wait for, or stream, permission requests and finished workers."],
  ["subway settings set <key> <value>","Update a setting (e.g. mcp_relay true)."],
  ["subway <tool> '<json>'","Call any MCP tool directly."]]},
 {title:"Troubleshooting",sub:"Common issues and fixes",items:[
  ["HUD won't load","Run subway hud; check port 7890 isn't taken."],
  ["Tools missing after an update","Reconnect /mcp in your chat."],
  ["Worker stopped by watchdog or cap","Rebrief with exact code and a higher cap."],
  ["Permission request waiting","Resolve via Home card or Workers → Permissions."],
  ["Branch preserved for manual merge","Landing page: View diff / Merge / Discard."],
  ["Slow or hot old laptop","Turn on low-power mode and mcp_relay."]]}];
const slug=s=>s.toLowerCase().replace(/[^a-z0-9]+/g,"-").replace(/^-|-$/g,"");
const esc=s=>s.replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"})[c]);

/* rail constants, as in the original */
const RADIUS=40,MAX_W=110,IDLE=80,K=320,D=34,M=0.7;
const PRESET={title:{base:40,bump:70,cls:"strong"},subtitle:{base:36,bump:64,cls:"strong"},section:{base:30,bump:56,cls:""},body:{base:24,bump:50,cls:""}};
let rail=null,dashes=[],mouseY=Infinity,inside=false,resetT=0,raf=0,last=0,activeId=null,query="",scrollRaf=0;
const reduce=matchMedia("(prefers-reduced-motion: reduce)").matches;

function buildRail(sections){if(rail)rail.remove();
  rail=document.createElement("nav");rail.className="prox";rail.setAttribute("aria-label","Page sections");
  const col=document.createElement("div");col.className="prox-col";rail.append(col);
  dashes=sections.map(s=>{const b=document.createElement("button");b.type="button";b.className="prox-dash";b.title=s.label;b.setAttribute("aria-label",`Go to ${s.label}`);
    const bar=document.createElement("span");bar.className="prox-bar "+PRESET[s.kind].cls;b.append(bar);col.append(b);
    const p=PRESET[s.kind],x=p.base/MAX_W;const d={id:s.id,kind:s.kind,b,bar,x,v:0,t:x};
    b.addEventListener("click",()=>select(s.id));bar.style.transform=`scaleX(${x})`;return d;});
  const gap=Math.max(3,Math.min(8,(innerHeight*.72-sections.length)/Math.max(1,sections.length)));col.style.gap=gap+"px";
  col.addEventListener("pointermove",e=>{clearTimeout(resetT);inside=true;mouseY=e.clientY;kick();});
  col.addEventListener("pointerleave",()=>{inside=false;mouseY=Infinity;kick();});
  document.body.append(rail);}
function targets(){for(const d of dashes){const p=PRESET[d.kind];const r=d.b.getBoundingClientRect();const dist=Math.abs(mouseY-(r.top+r.height/2));
  const k=Math.max(0,1-dist/RADIUS);d.t=(p.base+p.bump*k)/MAX_W;}}
function step(now){const dt=Math.min(.032,(now-(last||now))/1000)||.016;last=now;targets();let moving=false;
  for(const d of dashes){if(reduce){d.x=d.t;d.v=0;}else{d.v+=((-K*(d.x-d.t)-D*d.v)/M)*dt;d.x+=d.v*dt;}
    if(Math.abs(d.v)>1e-4||Math.abs(d.x-d.t)>1e-4)moving=true;else{d.x=d.t;d.v=0;}d.bar.style.transform=`scaleX(${d.x})`;}
  raf=moving?requestAnimationFrame(step):0;if(!moving)last=0;}
function kick(){if(!raf)raf=requestAnimationFrame(step);}
function pulse(id){const d=dashes.find(x=>x.id===id);if(!d)return;const r=d.b.getBoundingClientRect();mouseY=r.top+r.height/2;kick();clearTimeout(resetT);
  if(!inside)resetT=setTimeout(()=>{mouseY=Infinity;kick();},IDLE);}
function setActive(id){activeId=id;for(const d of dashes){if(d.id===id)d.b.setAttribute("aria-current","location");else d.b.removeAttribute("aria-current");}}
function select(id){const el=document.getElementById(id);if(!el)return;el.scrollIntoView({behavior:reduce?"auto":"smooth",block:"start"});
  history.replaceState(null,"",`#help/${id}`);setActive(id);pulse(id);}
function onScroll(){if(scrollRaf)return;scrollRaf=requestAnimationFrame(()=>{scrollRaf=0;if(!rail)return;const y=innerHeight*.4;let best=null,bd=Infinity;
  for(const d of dashes){const el=document.getElementById(d.id);if(!el)continue;const r=el.getBoundingClientRect();
    const dist=r.top<=y&&r.bottom>=y?0:Math.min(Math.abs(r.top-y),Math.abs(r.bottom-y));if(dist<bd){bd=dist;best=d.id;}}
  if(best&&best!==activeId){setActive(best);if(!inside)pulse(best);}});}

function render(page){const q=query.toLowerCase();
  const secs=SECTIONS.map(s=>({...s,items:s.items.filter(([t,d])=>!q||`${t} ${d} ${s.title}`.toLowerCase().includes(q))})).filter(s=>s.items.length);
  const wrap=document.createElement("div");wrap.className="help";
  wrap.innerHTML=`<label class="help-search"><span class="sr">Search help</span><input type="search" placeholder="Filter sections, terms, tools or commands…" value="${esc(query)}"></label>
   <section class="help-card help-intro" id="help-what"><h2>What is Subway?</h2><p>A local MCP server that lets your AI coding chats — Claude Code, Antigravity, OpenCode, pi and others — share context and hand work to supervised workers. The HUD is its dashboard on 127.0.0.1.</p></section>
   ${secs.map(s=>`<section class="help-card" id="help-${slug(s.title)}"><h2>${esc(s.title)}</h2><p class="help-sub">${esc(s.sub)}</p><dl>${s.items.map(([t,d])=>
     `<div class="help-row" id="help-${slug(s.title)}-${slug(t)}"><dt>${esc(t)}</dt><dd>${esc(d)}</dd></div>`).join("")}</dl></section>`).join("")}
   ${secs.length?"":`<p class="help-empty">No help topics match “${esc(query)}”.</p>`}`;
  page.querySelector(".help")?.remove();page.append(wrap);
  const inp=wrap.querySelector("input");inp.addEventListener("input",()=>{query=inp.value;const pos=inp.selectionStart;render(page);const n=page.querySelector(".help-search input");n.focus();n.setSelectionRange(pos,pos);});
  const rows=[{id:"help-what",label:"What is Subway?",kind:"subtitle"}];
  for(const s of secs){rows.push({id:`help-${slug(s.title)}`,label:s.title,kind:"subtitle"});for(const[t]of s.items)rows.push({id:`help-${slug(s.title)}-${slug(t)}`,label:t,kind:"body"});}
  buildRail(rows);activeId=null;onScroll();}

function mount(){const page=document.getElementById("page");const r=(location.hash.slice(1)||"home").split("/")[0];
  document.body.classList.toggle("on-help",r==="help");
  if(r!=="help"){if(rail){rail.remove();rail=null;dashes=[];}return;}
  render(page);const sub=location.hash.split("/")[1];if(sub)requestAnimationFrame(()=>document.getElementById(sub)?.scrollIntoView({block:"start"}));}
addEventListener("scroll",onScroll,{passive:true});addEventListener("resize",()=>{if(rail)onScroll();});
document.addEventListener("page:render",mount);mount();
})();
