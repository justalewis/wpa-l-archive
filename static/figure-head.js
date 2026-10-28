/* Red-figure and black-figure follow the reader's system setting. This
   applies a saved override before first paint, so a reader who chose the
   other figure does not watch the page repaint into it. Loaded without
   defer in <head>, from this origin, so the CSP needs no 'unsafe-inline'
   (Pinakes inlines the same lines). */
(function () {
  try {
    var f = localStorage.getItem('wpa-figure');
    if (f === 'light' || f === 'dark') document.documentElement.setAttribute('data-theme', f);
  } catch (e) {}
})();
