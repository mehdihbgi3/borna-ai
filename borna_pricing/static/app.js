(function () {
  "use strict";
  var FA = "۰۱۲۳۴۵۶۷۸۹";
  function toFa(s) { return String(s).replace(/[0-9]/g, function (d) { return FA[+d]; }); }
  function toLatin(s) {
    return String(s).replace(/[۰-۹]/g, function (d) { return FA.indexOf(d); })
      .replace(/[٠-٩]/g, function (d) { return d.charCodeAt(0) - 0x0660; });
  }
  function norm(s) {
    return toLatin(s).toLowerCase().replace(/ي/g, "ی").replace(/ك/g, "ک")
      .replace(/[‌\s]+/g, " ").replace(/[٬,]/g, "").trim();
  }
  function money(n) { return toFa(Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, "٬")); }

  document.querySelectorAll("[data-filter-for]").forEach(function (input) {
    var table = document.getElementById(input.getAttribute("data-filter-for"));
    if (!table || !table.tBodies.length) return;
    var rows = Array.prototype.slice.call(table.tBodies[0].rows);
    var texts = rows.map(function (r) { return norm(r.textContent); });
    var counter = document.querySelector('[data-count-for="' + table.id + '"]');
    input.addEventListener("input", function () {
      var q = norm(input.value);
      var shown = 0;
      rows.forEach(function (r, i) {
        var hit = !q || texts[i].indexOf(q) !== -1;
        r.hidden = !hit;
        if (hit) shown++;
      });
      if (counter) counter.textContent = toFa(shown);
    });
  });

  document.querySelectorAll("[data-toman-preview]").forEach(function (input) {
    var out = document.getElementById(input.getAttribute("data-toman-preview"));
    if (!out) return;
    function update() {
      var v = parseInt(norm(input.value).replace(/\s/g, ""), 10);
      out.textContent = isNaN(v) ? "" : "= " + money(v * 10) + " ریال";
    }
    input.addEventListener("input", update);
    update();
  });

  document.querySelectorAll("[data-requires]").forEach(function (btn) {
    var box = document.getElementById(btn.getAttribute("data-requires"));
    if (!box) return;
    function sync() { btn.disabled = !box.checked; }
    box.addEventListener("change", sync);
    sync();
  });

  document.querySelectorAll("[data-print]").forEach(function (b) {
    b.addEventListener("click", function () { window.print(); });
  });

  document.querySelectorAll(".chart").forEach(function (box) {
    var svg = box.querySelector("svg");
    var dataEl = box.querySelector("script[type='application/json']");
    if (!svg || !dataEl) return;
    var data;
    try { data = JSON.parse(dataEl.textContent); } catch (e) { return; }
    var pts = data.pts || [];
    if (!pts.length) return;
    var hit = svg.querySelector(".hit"), xh = svg.querySelector(".xhair"), dot = svg.querySelector(".xdot");
    var tip = document.createElement("div");
    tip.className = "chart-tip";
    tip.hidden = true;
    var strong = document.createElement("strong"), span = document.createElement("span");
    tip.appendChild(strong); tip.appendChild(span);
    box.appendChild(tip);
    function show(clientX) {
      var r = svg.getBoundingClientRect();
      var scale = data.w / r.width;
      var x = (clientX - r.left) * scale;
      var best = pts[0], bd = Infinity;
      pts.forEach(function (p) { var d = Math.abs(p.x - x); if (d < bd) { bd = d; best = p; } });
      xh.setAttribute("x1", best.x); xh.setAttribute("x2", best.x); xh.setAttribute("visibility", "visible");
      dot.setAttribute("cx", best.x); dot.setAttribute("cy", best.y); dot.setAttribute("visibility", "visible");
      strong.textContent = best.v;
      span.textContent = best.t;
      tip.hidden = false;
      var px = best.x / scale, py = best.y / (data.h / r.height);
      var left = px + 14;
      if (left + tip.offsetWidth > r.width) left = px - tip.offsetWidth - 14;
      tip.style.left = Math.max(0, left) + "px";
      tip.style.top = Math.max(0, py - tip.offsetHeight - 10) + "px";
    }
    function hide() {
      tip.hidden = true;
      xh.setAttribute("visibility", "hidden");
      dot.setAttribute("visibility", "hidden");
    }
    hit.addEventListener("pointermove", function (e) { show(e.clientX); });
    hit.addEventListener("pointerleave", hide);
    var idx = pts.length - 1;
    box.tabIndex = 0;
    box.addEventListener("keydown", function (e) {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      idx = Math.max(0, Math.min(pts.length - 1, idx + (e.key === "ArrowRight" ? 1 : -1)));
      var r = svg.getBoundingClientRect();
      show(r.left + pts[idx].x * (r.width / data.w));
    });
    box.addEventListener("blur", hide);
  });
})();
