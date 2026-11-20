/* Home clouds: flat painted cumulus in the Mononoke sky palette (deep #296A7F, sky #2692A6, shade #74A5A9,
   lit white). Placed at random on each load, drifting slowly; behind the map, Home only. */
(function(){
/* Colours are CSS variables so a theme switch repaints the clouds without a rebuild. */
if(!document.getElementById("cl-theme")){const st=document.createElement("style");st.id="cl-theme";
  const D="--cl-shade:#26343B;--cl-base:#1E3138;--cl-lit:#222D35;--cl-mid:#1A242B";
  st.textContent=`.clouds{--cl-shade:#9EC3C6;--cl-base:#74A5A9;--cl-lit:#FFFFFF;--cl-mid:#D6E7E7}
:root[data-theme="dark"] .clouds{${D}}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]) .clouds{${D}}}`;document.head.append(st);}
const NS="http://www.w3.org/2000/svg";
const R=(a,b)=>a+Math.random()*(b-a);
const box=document.createElement("div");box.className="clouds";box.setAttribute("aria-hidden","true");
let built=false;

/* One cloud: a flat base, a row of low puffs and 1-3 towers. Painted in three flat layers:
   shade (whole silhouette), lit (same puffs shrunk and lifted), and a few mid-tone pockets. */
function cloud(w){const h=w*.5,base=h*.92,puffs=[];
  const n=Math.round(R(5,8));for(let i=0;i<n;i++){const t=(i+.5)/n;const r=w*R(.09,.14)*(1-Math.abs(t-.5)*.7);
    puffs.push([w*(.08+t*.84),base-r*R(.35,.6),r]);}
  const towers=Math.round(R(1,3));for(let i=0;i<towers;i++){const cx=w*R(.3,.7),r=w*R(.13,.2);
    puffs.push([cx,base-r*R(1.1,1.7),r]);puffs.push([cx+r*R(-.6,.6),base-r*R(1.8,2.3),r*R(.55,.75)]);}
  const svg=document.createElementNS(NS,"svg");svg.setAttribute("viewBox",`0 ${-h*.15} ${w} ${h*1.15}`);svg.setAttribute("width",w);svg.setAttribute("height",h*1.15);
  const id="cb"+Math.random().toString(36).slice(2,8);
  svg.innerHTML=`<defs><clipPath id="${id}"><rect x="0" y="${-h}" width="${w}" height="${base+h}"/></clipPath></defs>
   <g clip-path="url(#${id})">
    <g style="fill:var(--cl-shade)">${puffs.map(([x,y,r])=>`<circle cx="${x}" cy="${y}" r="${r}"/>`).join("")}</g>
    <rect x="0" y="${base-h*.06}" width="${w}" height="${h*.06}" style="fill:var(--cl-base)" opacity=".55"/>
    <g style="fill:var(--cl-lit)">${puffs.map(([x,y,r])=>`<circle cx="${x-r*.08}" cy="${y-r*.2}" r="${r*.86}"/>`).join("")}</g>
    <g style="fill:var(--cl-mid)">${puffs.filter(()=>Math.random()<.45).map(([x,y,r])=>`<circle cx="${x+r*.35}" cy="${y+r*.25}" r="${r*.42}"/>`).join("")}</g>
   </g>`;
  return svg;}

function build(){box.replaceChildren();const W=innerWidth,H=innerHeight;const count=W<640?3:W<1180?5:7;const placed=[];
  for(let i=0,tries=0;i<count&&tries<60;tries++){const w=Math.round(R(160,W<640?240:380));const x=R(-.08,.92)*W,y=R(.02,.62)*H;
    if(placed.some(p=>Math.abs(p.x-x)<(p.w+w)*.55&&Math.abs(p.y-y)<(p.w+w)*.22))continue;placed.push({x,y,w});i++;
    const el=document.createElement("div");el.className="cloud";el.append(cloud(w));
    const far=w<240;el.style.cssText=`left:${(x/W*100).toFixed(2)}%;top:${(y/H*100).toFixed(2)}%;opacity:${far?.7:.95};--dx:${Math.round(R(40,110)*(Math.random()<.5?-1:1))}px;animation-duration:${Math.round(R(50,110))}s;animation-delay:${-Math.round(R(0,50))}s;animation-direction:alternate;animation-timing-function:ease-in-out`;
    box.append(el);}
  built=true;}

function mount(){const home=(location.hash.slice(1)||"home").split("/")[0]==="home";
  if(!home){box.remove();return;}
  if(!built)build();
  if(box.parentNode!==document.body||box!==document.body.firstElementChild)document.body.prepend(box);}
/* the map prepends its svg on render; put the clouds back underneath it */
new MutationObserver(()=>{if(box.isConnected&&box!==document.body.firstElementChild)document.body.prepend(box);}).observe(document.body,{childList:true});
document.addEventListener("page:render",mount);mount();
})();
