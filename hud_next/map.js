const MAPI={"wheelchair": "<path d=\"M250.53,199.59l-24,8a8,8,0,0,1-9.69-4L187.05,144H104a8,8,0,0,1-8-8V106.34A56,56,0,0,0,112,216c25.91,0,50.09-18.05,56.25-42a8,8,0,1,1,15.5,4c-8.06,31.3-38.23,54-71.75,54A72,72,0,0,1,96,89.81v-19a28,28,0,1,1,16,0V88h56a8,8,0,0,1,0,16H112v24h80a8,8,0,0,1,7.15,4.42l28.9,57.8,17.42-5.81a8,8,0,0,1,5.06,15.18Z\"/>"};
/* Home map: an original octilinear transit map drawn as real track (ballast, sleepers, two rails).
   Lines are Subway's own: A Antigravity, C Claude, O OpenCode, P pi, L Local, M Mail, T Track shuttle.
   Sharp 45° bends only; square signs. Frame 1440x900, scaled uniformly; E marks ends that run off-screen. */
(function(){
const E="edge";
const LINES=[
 {id:"T",c:"#8A9094",pts:[[380,640],[1060,640]],term:[1,1]},
 {id:"O",c:"#12924A",pts:[[E,330],[700,330],[740,290],[1160,290],[1220,350],[E,350]]},
 {id:"P",c:"#8E4A9E",pts:[[E,520],[440,520],[500,460],[980,460],[1040,520],[E,520]],express:true},
 {id:"A",c:"#2C86B0",pts:[[600,210],[600,560],[540,620],[540,740]],term:[1,1]},
 {id:"M",c:"#E2382B",pts:[[624,210],[624,570],[564,630],[564,740]],term:[0,0]},
 {id:"C",c:"#F08A24",pts:[[840,190],[840,400],[920,480],[920,740]],term:[1,1]},
 {id:"L",c:"#F7C51E",ink:"#0F2F3A",pts:[[864,190],[864,390],[944,470],[944,740]],term:[0,0]}];
const COLOR=Object.fromEntries(LINES.map(l=>[l.id,l]));
/* interchanges: a bar across the lines that stop there */
const PILLS=[[588,330,636,330],[588,460,636,460],[828,290,876,290],[528,640,576,640],[908,640,956,640],
  [588,210,636,210],[828,190,876,190],[528,740,576,740],[908,740,956,740]];
/* local stops: a tick across one line */
const TICKS=[["P",760,460,"v"],["O",470,330,"v"],["O",1000,290,"v"],["A",600,400,"h"],["C",920,560,"h"],["T",740,640,"v"],["P",360,520,"v"]];
/* labels: x, y, anchor, name, routes, signs */
const LABELS=[
 [650,206,"start","Threads Junction",["A","M"],["acc"]],
 [890,186,"start","Fastmail Plaza",["C","L"],["acc"]],
 [650,322,"start","Ledger St",["A","M","O"],[]],
 [890,282,"start","Lease Av",["C","L","O"],["acc","warn"]],
 [650,452,"start","Principal Sq",["A","M","P"],["acc"]],
 [470,632,"end","Landing",["A","M","T"],["warn"]],
 [970,632,"start","Workers Yard",["C","L","T"],["acc"]],
 [470,746,"end","Worktree Av",["A","M"],[]],
 [970,746,"start","Isolate Yard",["C","L"],[]],
 [760,488,"middle","Signoff St",["P"],[]],
 [470,358,"middle","Claim St",["O"],[]],
 [1000,276,"middle","Quota Reset",["O"],[]],
 [586,404,"end","Recall",["A"],[]],
 [934,564,"start","Review Av",["C"],["acc"]],
 [740,668,"middle","Rewind",["T"],[]],
 [372,640,"end","Ask Room",["T"],[]],
 [1068,640,"start","Compact",["T"],[]]];
const NS="http://www.w3.org/2000/svg";
let svg=null,trains=[],raf=0,last=0;
const reduce=matchMedia("(prefers-reduced-motion: reduce)").matches;
const isNight=()=>{const t=document.documentElement.dataset.theme;return t==="dark"||(t!=="light"&&matchMedia("(prefers-color-scheme: dark)").matches);};
const MP=()=>isNight()?{ground:"#0B0E11",ink:"#E8EEF2",station:"#14181C",bed:"#2A3036",plate:"#14181C"}:{ground:"#F3F4EE",ink:"#0F2F3A",station:"#fff",bed:"#E7E9E0",plate:"#F3F4EE"};
const M=()=>window.HudMotion;const still=()=>reduce||!!(M()&&M().fpsCap()===0);
function el(tag,attrs,parent){const e=document.createElementNS(NS,tag);for(const k in attrs)e.setAttribute(k,attrs[k]);parent&&parent.append(e);return e;}

/* offset an octilinear polyline by d (left normal), mitred */
function offset(pts,d){const out=[];for(let i=0;i<pts.length;i++){
  const n=(a,b)=>{const dx=b[0]-a[0],dy=b[1]-a[1],L=Math.hypot(dx,dy)||1;return[-dy/L,dx/L];};
  const n1=i>0?n(pts[i-1],pts[i]):null,n2=i<pts.length-1?n(pts[i],pts[i+1]):null;
  if(!n1||!n2){const m=n1||n2;out.push([pts[i][0]+m[0]*d,pts[i][1]+m[1]*d]);continue;}
  let mx=n1[0]+n2[0],my=n1[1]+n2[1];const ml=Math.hypot(mx,my)||1;mx/=ml;my/=ml;const k=d/(mx*n1[0]+my*n1[1]);out.push([pts[i][0]+mx*k,pts[i][1]+my*k]);}
  return out;}
const dstr=p=>"M"+p.map(q=>q[0].toFixed(1)+" "+q[1].toFixed(1)).join("L");

let markDrawn=false;const MARK_SVG={};
function draw(){const P=MP();if(svg)svg.remove();cancelAnimationFrame(raf);raf=0;trains=[];
  const W=innerWidth,H=innerHeight,hb=document.querySelector(".hero")?.getBoundingClientRect().bottom||H*.2,top=hb+28,bot=H-(W<=1180?150:96);
  const s=Math.max(.45,Math.min(W/1440,(bot-top)/590)),ox=(W-1440*s)/2,oy=top-170*s,k=Math.max(.72,Math.min(1.15,s));
  const T=(x,y)=>[ox+x*s,oy+y*s];
  svg=el("svg",{class:"map",viewBox:`0 0 ${W} ${H}`,width:W,height:H,"aria-hidden":"true"});
  document.body.prepend(svg);
  const gBed=el("g",{},svg),gTrack=el("g",{},svg),gTrain=el("g",{},svg),gSta=el("g",{},svg),gLab=el("g",{},svg);
  const paths={};
  for(const l of LINES){
    const pts=l.pts.map((p,i)=>{if(p[0]!==E)return T(p[0],p[1]);const y=T(0,p[1])[1];return[i===0?-60:W+60,y];});
    paths[l.id]=pts;
    el("path",{d:dstr(pts),fill:"none",stroke:P.bed,"stroke-width":20*k,"stroke-linejoin":"miter","stroke-linecap":"butt"},gBed);
    const g=el("g",{},gTrack);
    /* sleepers: a dashed wide stroke gives ties perpendicular to the track, bends included */
    el("path",{d:dstr(pts),fill:"none",stroke:l.c,"stroke-width":13*k,"stroke-dasharray":`${2.6*k} ${4.4*k}`,"stroke-linejoin":"miter",opacity:l.id==="T"?.55:.85},g);
    for(const d of[-3.4*k,3.4*k])el("path",{d:dstr(offset(pts,d)),fill:"none",stroke:l.c,"stroke-width":1.7*k,"stroke-linejoin":"miter"},g);
    if(l.express)el("path",{d:dstr(pts),fill:"none",stroke:l.c,"stroke-width":.9*k,"stroke-dasharray":`${14*k} ${10*k}`,opacity:.6},g);
    l.screen=pts;}
  /* interchange bars: ink casing, white core, square ends */
  for(const[x1,y1,x2,y2]of PILLS){const a=T(x1,y1),b=T(x2,y2);
    el("path",{d:dstr([a,b]),stroke:P.ink,"stroke-width":15*k,"stroke-linecap":"square"},gSta);
    el("path",{d:dstr([a,b]),stroke:P.station,"stroke-width":9.5*k,"stroke-linecap":"square"},gSta);}
  for(const[id,x,y,o]of TICKS){const[a,b]=o==="v"?[T(x,y-9),T(x,y+9)]:[T(x-9,y),T(x+9,y)];
    el("path",{d:dstr([a,b]),stroke:P.ink,"stroke-width":6*k,"stroke-linecap":"butt"},gSta);
    el("path",{d:dstr([a,b]),stroke:P.station,"stroke-width":3*k,"stroke-linecap":"butt"},gSta);}
  /* termini: square box, colour frame, ink core */
  for(const l of LINES){if(!l.term)continue;l.term.forEach((on,i)=>{if(!on)return;const p=l.pts[i?l.pts.length-1:0];
    const grp=LINES.filter(m=>m.term&&m.pts[i?m.pts.length-1:0][1]===p[1]&&Math.abs(m.pts[i?m.pts.length-1:0][0]-p[0])<=24);
    if(grp[0]!==l)return;const xs=grp.flatMap(m=>[m.pts[i?m.pts.length-1:0][0]]);const[cx,cy]=T((Math.min(...xs)+Math.max(...xs))/2,p[1]+(i?16:-16));
    const w=(Math.max(...xs)-Math.min(...xs))*s+22*k,h=14*k;
    el("rect",{x:cx-w/2,y:cy-h/2,width:w,height:h,fill:P.station,stroke:P.ink,"stroke-width":2*k},gSta);
    grp.forEach(m=>{const[mx]=T(m.pts[i?m.pts.length-1:0][0],0);el("rect",{x:mx-3*k,y:cy-3*k,width:6*k,height:6*k,fill:m.c},gSta);});});}
  /* labels with square route bullets and signs (svg is in the DOM, so text can be measured) */
  const fs=13*k,b=15*k,cy=-fs*.36,FONT="Instrument Sans, system-ui, sans-serif";
  for(const[x,y,anc,name,routes,signs]of LABELS){const[px,py]=T(x,y);const g=el("g",{transform:`translate(${px.toFixed(1)} ${py.toFixed(1)})`},gLab);
    const plate=el("rect",{fill:P.plate},g);
    const t=el("text",{y:0,"font-size":fs,"font-weight":"600","font-family":FONT,fill:P.ink,"letter-spacing":"-.01em"},g);t.textContent=name;
    const tw=t.getComputedTextLength();
    const full=tw+6*k+routes.length*(b+2.5*k)+signs.length*(b+4.5*k)-2.5*k;
    const dx=anc==="end"?-full:anc==="middle"?-full/2:0;t.setAttribute("x",dx);
    Object.entries({x:dx-4*k,y:cy-b/2-3*k,width:full+8*k,height:b+6*k}).forEach(([k2,v])=>plate.setAttribute(k2,v));
    let cx=dx+tw+6*k;
    for(const r of routes){const L=COLOR[r];
      if(L.express){const d=b*.74;el("rect",{x:cx+b/2-d/2,y:cy-d/2,width:d,height:d,fill:L.c,transform:`rotate(45 ${cx+b/2} ${cy})`},g);}
      else el("rect",{x:cx,y:cy-b/2,width:b,height:b,fill:L.c},g);
      const lt=el("text",{x:cx+b/2,y:cy+b*.33,"font-size":10*k,"font-weight":"700","text-anchor":"middle","font-family":FONT,fill:L.ink||"#fff"},g);lt.textContent=r;cx+=b+2.5*k;}
    for(const sg of signs){cx+=2*k;const g2=el("g",{transform:`translate(${cx} ${cy-b/2})`},g);
      if(sg==="acc"){el("rect",{width:b,height:b,fill:"#2C86B0"},g2);const ic=el("g",{transform:`translate(${b*.12} ${b*.12}) scale(${b*.76/256})`,fill:P.station},g2);ic.innerHTML=MAPI.wheelchair;}
      else{const ic=el("g",{transform:`scale(${b/256})`},g2);ic.innerHTML=`<polygon points="128,20 244,228 12,228" fill="#F7C51E" stroke="#0F2F3A" stroke-width="18" stroke-linejoin="miter"/><rect x="118" y="92" width="20" height="78" fill="#0F2F3A"/><rect x="118" y="186" width="20" height="20" fill="#0F2F3A"/>`;}
      cx+=b+2.5*k;}}
  /* wordmark in the open block between Ledger St and Principal Sq: the logo is the S of "subway" */
  {const[mx,my]=T(728,392),wf=44*s;
    const g=el("g",{class:"map-mark",transform:`translate(${mx.toFixed(1)} ${my.toFixed(1)})`},gLab);
    const t=el("text",{y:0,"font-size":wf,"font-weight":"600","font-family":FONT,fill:P.ink,"letter-spacing":"-.025em"},g);t.textContent="ubway";
    const Sh=wf*.92,sc=Sh/312,Sw=252*sc,gap=wf*.06,tw=t.getComputedTextLength(),full=Sw+gap+tw,x0=-full/2;
    const S=el("g",{transform:`translate(${(x0+6*sc).toFixed(2)} ${(-Sh+wf*.12+6*sc).toFixed(2)}) scale(${sc.toFixed(4)})`},g);
    t.setAttribute("x",x0+Sw+gap);t.setAttribute("y",0);
    el("rect",{x:x0-10*s,y:-Sh+wf*.12-10*s,width:full+20*s,height:Sh+wf*.18+18*s,fill:P.plate},g).parentNode.prepend(g.lastChild);
    const anim=!markDrawn&&!reduce;if(anim){markDrawn=true;t.style.cssText="opacity:0;animation:mark-fade .5s ease .7s forwards";}
    const src=(document.getElementById("brandMark")?.getAttribute("src"))||"logo/S1s.svg";
    const put=txt=>{const sv=new DOMParser().parseFromString(txt,"image/svg+xml").documentElement;
      [...sv.children].forEach((n,i)=>{const m=S.appendChild(document.importNode(n,true));
        if(anim)m.style.cssText=`opacity:0;transform-box:fill-box;transform-origin:center;animation:${m.tagName==="circle"?"mark-pop .45s cubic-bezier(.3,1.6,.5,1)":"mark-lay .6s cubic-bezier(.2,.8,.2,1)"} ${(.1+i*.07).toFixed(2)}s forwards`;});};
    if(MARK_SVG[src])put(MARK_SVG[src]);else fetch(src).then(r=>r.text()).then(x=>{MARK_SVG[src]=x;put(x);}).catch(()=>{});}
  /* trains: two-car sets in line colour with a window band */
  for(const[id,dir,off]of[["P",1,0],["O",-1,.4],["C",1,.2],["A",-1,.7]]){
    const path=el("path",{d:dstr(paths[id]),fill:"none",stroke:"none"},gTrain);const len=path.getTotalLength();
    const cars=[0,1].map(()=>{const g=el("g",{},gTrain);el("rect",{x:-12*k,y:-4.6*k,width:24*k,height:9.2*k,fill:COLOR[id].c,stroke:P.ink,"stroke-width":1.2*k},g);
      el("rect",{x:-9*k,y:-1.4*k,width:18*k,height:2.8*k,fill:P.station,opacity:.85},g);return g;});
    trains.push({path,len,pos:len*off,dir,cars,k});}
  if(still()){place();return;}raf=requestAnimationFrame(tick);}
function place(){for(const t of trains)t.cars.forEach((c,i)=>{const at=t.pos-i*27*t.k*t.dir;const L=((at%t.len)+t.len)%t.len;
  const p=t.path.getPointAtLength(L),q=t.path.getPointAtLength(Math.min(t.len,L+1)),r=t.path.getPointAtLength(Math.max(0,L-1));
  const a=Math.atan2(q.y-r.y,q.x-r.x)*180/Math.PI;c.setAttribute("transform",`translate(${p.x} ${p.y}) rotate(${a})`);});}
function tick(now){const cap=M()?M().fpsCap():60;if(!cap){raf=0;last=0;return;}if(last&&now-last<1000/cap-1){raf=requestAnimationFrame(tick);return;}const dt=Math.min(.05,(now-(last||now))/1000);last=now;for(const t of trains)t.pos+=t.dir*48*dt;place();raf=requestAnimationFrame(tick);}
function mount(){const r=(location.hash.slice(1)||"home").split("/")[0];
  if(r!=="home"){if(svg){svg.remove();svg=null;}cancelAnimationFrame(raf);raf=0;last=0;return;}draw();}
let rt=0;addEventListener("resize",()=>{clearTimeout(rt);rt=setTimeout(()=>{if(svg)draw();},120);});
const resume=()=>{if(!svg)return;if(still()){cancelAnimationFrame(raf);raf=0;last=0;place();}else if(!raf){last=0;raf=requestAnimationFrame(tick);}};
document.addEventListener("visibilitychange",resume);if(window.HudMotion)window.HudMotion.onChange(resume);
document.addEventListener("page:render",mount);
if(document.fonts&&document.fonts.ready)document.fonts.ready.then(mount);else mount();

new MutationObserver(()=>{if(svg)draw();}).observe(document.documentElement,{attributes:true,attributeFilter:["data-theme"]});
matchMedia("(prefers-color-scheme: dark)").addEventListener("change",()=>{if(svg)draw();});
})();
