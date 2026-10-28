/* Behaviour shared by every page. Everything here is an enhancement: the
   pages read and work without it. */
(function () {
  /* Red-figure and black-figure. The button reports the figure it would
     switch you TO, and the choice is remembered; figure-head.js applies it
     before first paint on the next page. Ported from Pinakes' base shell. */
  var b = document.getElementById('figure-toggle');
  if (b) {
    var current = function () {
      var set = document.documentElement.getAttribute('data-theme');
      if (set === 'light' || set === 'dark') return set;
      return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    };
    var label = function () { b.textContent = current() === 'dark' ? 'Red-figure' : 'Black-figure'; };
    label();
    b.hidden = false;
    b.addEventListener('click', function () {
      var next = current() === 'dark' ? 'light' : 'dark';
      var el = document.documentElement;
      el.classList.add('figure-switching');
      el.setAttribute('data-theme', next);
      try { localStorage.setItem('wpa-figure', next); } catch (e) {}
      label();
      requestAnimationFrame(function () {
        requestAnimationFrame(function () { el.classList.remove('figure-switching'); });
      });
    });
  }

  /* The retro skin. Any .skin-toggle button switches it; the choice is
     remembered and applied before first paint by figure-head.js. */
  var skinButtons = document.querySelectorAll('.skin-toggle');
  var skinLabel = function () {
    var on = document.documentElement.getAttribute('data-skin') === 'listserv';
    skinButtons.forEach(function (sb) { sb.textContent = on ? 'Modern view' : 'Retro view'; });
  };
  skinButtons.forEach(function (sb) {
    sb.hidden = false;
    sb.addEventListener('click', function () {
      var el = document.documentElement;
      var on = el.getAttribute('data-skin') === 'listserv';
      if (on) el.removeAttribute('data-skin'); else el.setAttribute('data-skin', 'listserv');
      try { localStorage.setItem('wpa-skin', on ? 'modern' : 'listserv'); } catch (e) {}
      skinLabel();
    });
  });
  skinLabel();

  /* Copy buttons: <button class="copy-btn" data-copy="#id">. */
  document.querySelectorAll('.copy-btn[data-copy]').forEach(function (btn) {
    btn.hidden = false;
    btn.addEventListener('click', function () {
      var src = document.querySelector(btn.getAttribute('data-copy'));
      if (!src || !navigator.clipboard) return;
      navigator.clipboard.writeText(src.textContent.trim()).then(function () {
        var was = btn.textContent;
        btn.textContent = 'Copied';
        setTimeout(function () { btn.textContent = was; }, 1500);
      });
    });
  });
})();
