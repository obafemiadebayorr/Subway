/* ?embed=1 hides the chrome so pages can open as tabs inside the home panel. */
try{if(new URLSearchParams(location.search).has("embed"))document.documentElement.classList.add("embed");}catch(_){}
