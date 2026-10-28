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
