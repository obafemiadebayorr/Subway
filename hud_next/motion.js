/* Low-power and idle control for HUD animations, so old laptops stay cool. */
(()=>{const KEY="hud-low-power";let lastInput=Date.now(),idleNow=false;const subs=new Set();
const rm=matchMedia("(prefers-reduced-motion: reduce)");
function setting(){try{return localStorage.getItem(KEY)||"auto";}catch(_){return "auto";}}
function weak(){const c=navigator.hardwareConcurrency||8,m=navigator.deviceMemory||8;return c<=4||m<=4;}
function lowPower(){const v=setting();return rm.matches||v==="1"||(v==="auto"&&weak());}
function fpsCap(){return document.hidden||lowPower()||idleNow?0:20;}
function emit(){document.documentElement.classList.toggle("low-power",lowPower());subs.forEach(f=>{try{f();}catch(_){}});}
const input=()=>{lastInput=Date.now();if(idleNow){idleNow=false;emit();}};
["pointermove","pointerdown","keydown","wheel","touchstart"].forEach(t=>addEventListener(t,input,{passive:true}));
setInterval(()=>{if(!idleNow&&Date.now()-lastInput>60000){idleNow=true;emit();}},5000);
document.addEventListener("visibilitychange",emit);if(rm.addEventListener)rm.addEventListener("change",emit);
window.HudMotion={lowPower,fpsCap,setting,idle:()=>idleNow,onChange(f){subs.add(f);return()=>subs.delete(f);},set(v){try{localStorage.setItem(KEY,v);}catch(_){}emit();}};
document.documentElement.classList.toggle("low-power",lowPower());})();