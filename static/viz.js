/* One tooltip for every chart: any element with data-tip (and optionally
   data-tip-title) shows .viz-tip near the pointer or the focused mark. The
   charts are drawn on the server and read without this; it adds the
   hover layer. Positions are set through the CSSOM, which the CSP allows
   (only style attributes in markup are restricted). */
(function () {
  var tip = null;
  function ensure() {
    if (!tip) {
      tip = document.createElement('div');
      tip.className = 'viz-tip';
      tip.setAttribute('role', 'status');
      tip.hidden = true;
      document.body.appendChild(tip);
    }
    return tip;
  }
  function show(el, x, y) {
    var t = ensure();
    t.textContent = '';
    var title = el.getAttribute('data-tip-title');
    if (title) {
      var h = document.createElement('div');
      h.className = 'tip-title';
      h.textContent = title;
      t.appendChild(h);
    }
    var body = document.createElement('div');
    body.className = 'tip-num';
    body.textContent = el.getAttribute('data-tip');
    t.appendChild(body);
    t.hidden = false;
    var pad = 14, w = t.offsetWidth, h2 = t.offsetHeight;
    var left = Math.min(x + pad, window.scrollX + document.documentElement.clientWidth - w - 8);
    var top = y - h2 - pad < window.scrollY ? y + pad : y - h2 - pad;
    t.style.left = left + 'px';
    t.style.top = top + 'px';
  }
  function hide() { if (tip) tip.hidden = true; }
  document.addEventListener('mouseover', function (e) {
    var el = e.target.closest && e.target.closest('[data-tip]');
    if (el) show(el, e.pageX, e.pageY); else hide();
  });
  document.addEventListener('focusin', function (e) {
    var el = e.target.closest && e.target.closest('[data-tip]');
    if (!el) return hide();
    var r = el.getBoundingClientRect();
    show(el, r.left + window.scrollX + r.width / 2, r.top + window.scrollY);
  });
  document.addEventListener('mouseleave', hide);
})();
