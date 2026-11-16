/* Bottom-left clock: mirrors the account trigger (bottom-right). Home only. */
(function(){
const el=document.createElement("div");el.className="clk";
el.innerHTML=`<time class="clk-t"></time><span class="clk-d"></span>`;
const t=el.querySelector(".clk-t"),d=el.querySelector(".clk-d");
function paint(){const now=new Date();t.dateTime=now.toISOString();
  t.textContent=now.toLocaleTimeString([], {hour:"numeric",minute:"2-digit"});
  d.textContent=now.toLocaleDateString([], {weekday:"short",day:"numeric",month:"short"});
  el.title=now.toLocaleString([], {dateStyle:"full",timeStyle:"short"});}
function loop(){paint();setTimeout(loop,60000-Date.now()%60000+20);}loop();
document.addEventListener("visibilitychange",()=>{if(!document.hidden)paint();});
function mount(){const home=(location.hash.slice(1)||"home").split("/")[0]==="home";
  if(home&&!el.isConnected){paint();document.body.append(el);}else if(!home)el.remove();}
document.addEventListener("page:render",mount);mount();
})();
