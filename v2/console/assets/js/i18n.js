/* KM_I18N */
(function(){
  var KM_LANG = localStorage.getItem("km_lang") || (navigator.language||"en").slice(0,2);
  if(["th","en","zh"].indexOf(KM_LANG)===-1){ KM_LANG="en"; }
  var KM_I18N = {};
  var LOCALE = { th:"th-TH", en:"en-US", zh:"zh-CN" };

  function t(key, fallback){
    return (KM_I18N && KM_I18N[key]) || fallback || key;
  }

  function applyNav(){
    var map = {
      "#/overview":"nav.overview",
      "#/analytics":"nav.analytics",
      "#/checklist":"nav.checklist",
      "#/tasks":"nav.tasks",
      "#/devices":"nav.devices",
      "#/commands":"nav.commands",
      "#/reports":"nav.reports"
    };
    Object.keys(map).forEach(function(href){
      var a = document.querySelector('a.nav-link[href="'+href+'"]');
      if(a){ a.textContent = t(map[href], a.textContent); }
    });
    var elClockTitle = document.querySelector("#km-clock-title");
    if(elClockTitle){ elClockTitle.textContent = t("clock.title","Clock"); }
  }

  function applyBadges(root){
    root = root || document;
    Array.prototype.forEach.call(root.querySelectorAll(".badge"), function(b){
      var txt = b.textContent.trim().toLowerCase();
      if(/ok/i.test(txt)) b.textContent = t("badge.ok","OK");
      else if(/issue|err|fail/i.test(txt)) b.textContent = t("badge.issue","Issue");
      else if(/watch|attention/i.test(txt)) b.textContent = t("badge.watch","Watch");
    });
  }

  function applyOverview(root){
    root = root || document;
    // headers inside cards
    Array.prototype.forEach.call(root.querySelectorAll(".card h3"), function(h){
      var s = h.textContent.trim().toLowerCase();
      if(s==="live status") h.textContent = t("overview.live_status","Live Status");
      else if(s==="build / deploy") h.textContent = t("overview.build_deploy","Build / Deploy");
      else if(s==="alerts") h.textContent = t("overview.alerts","Alerts");
      else if(s==="summary") h.textContent = t("overview.summary","Summary");
    });
    // table heads
    var ths = root.querySelectorAll("table thead th");
    if(ths && ths.length>=3){
      ths[0].textContent = t("table.project","Project");
      ths[1].textContent = t("table.progress","Progress");
      ths[2].textContent = t("table.status","Status");
    }
  }

  function applyAll(root){
    applyNav();
    applyOverview(root||document);
    applyBadges(root||document);
  }

  function startClock(){
    try{ if(window.KM_CLOCK_TIMER){ clearInterval(window.KM_CLOCK_TIMER); } }catch(e){}
    function pad(n){ return (n<10?'0':'')+n; }
    function fmtDate(d){
      try { return d.toLocaleDateString(LOCALE[KM_LANG]||"en-US",{weekday:"short",day:"2-digit",month:"short",year:"numeric"}); }
      catch(e){ return d.toDateString(); }
    }
    function tick(){
      var el=document.getElementById("km-clock"); if(!el) return;
      var t=el.querySelector(".time"), dt=el.querySelector(".date"), now=new Date();
      try{ t.textContent = now.toLocaleTimeString(LOCALE[KM_LANG]||"en-US",{hour12:false}); }
      catch(e){ t.textContent = pad(now.getHours())+":"+pad(now.getMinutes())+":"+pad(now.getSeconds()); }
      dt.textContent = fmtDate(now);
    }
    tick();
    window.KM_CLOCK_TIMER = setInterval(tick,1000);
  }

  function setActiveBtn(lang){
    var btns=document.querySelectorAll("#km-lang button[data-lang]");
    Array.prototype.forEach.call(btns,function(b){ b.classList.toggle("active", b.getAttribute("data-lang")===lang); });
  }

  function loadLang(lang){
    KM_LANG = lang;
    localStorage.setItem("km_lang", KM_LANG);
    return fetch("assets/i18n/"+KM_LANG+".json?v="+(window.KM_VER||Date.now()),{cache:"no-store"})
      .then(function(r){ if(!r.ok) throw new Error("HTTP "+r.status); return r.json(); })
      .then(function(json){ KM_I18N=json; applyAll(document); startClock(); setActiveBtn(KM_LANG); })
      .catch(function(e){ console.error("i18n load error:", e); });
  }

  // Expose
  window.KM_setLang = function(lang){ return loadLang(lang||"en"); };
  window.KM_getLang = function(){ return KM_LANG; };
  window.KM_applyI18n = applyAll;
  window.KM_startClock = startClock;

  document.addEventListener("DOMContentLoaded", function(){
    // hook buttons
    var box = document.getElementById("km-lang");
    if(box){
      box.addEventListener("click", function(ev){
        var btn = ev.target.closest("button[data-lang]");
        if(!btn) return;
        KM_setLang(btn.getAttribute("data-lang"));
      });
    }
    // initial
    loadLang(KM_LANG);

    // re-apply to tab content whenever it changes
    var content = document.querySelector("#content");
    if(content){
      var mo = new MutationObserver(function(){ applyAll(content); });
      mo.observe(content, {childList:true, subtree:true});
    }
  });
})();
