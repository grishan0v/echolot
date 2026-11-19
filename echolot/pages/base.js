// What every page shares: two helpers, and one tooltip for every mark that
// carries exact figures. The other scripts of a page hang their own pieces on
// `window.echolot`.
(function () {
  var E = window.echolot = { onPick: [] };
  E.$ = function (id) { return document.getElementById(id); };
  E.all = function (sel, root) { return [].slice.call((root || document).querySelectorAll(sel)); };

  // The same text on hover and on focus. It lives inside the open dialog of
  // the mark it describes: a modal dialog is drawn above everything outside it.
  var tip = document.createElement('div'), shownFor = null;
  tip.id = 'tip'; tip.hidden = true; tip.setAttribute('role', 'status');
  document.body.appendChild(tip);
  function fill(el) {
    if (shownFor === el) return;
    shownFor = el; tip.textContent = '';
    var home = el.closest('dialog[open]') || document.body;
    if (tip.parentNode !== home) home.appendChild(tip);
    el.getAttribute('data-tip').split('\n').forEach(function (line, i) {
      var node = document.createElement(i ? 'div' : 'strong'); node.textContent = line; tip.appendChild(node);
    });
  }
  function place(x, y) {
    tip.hidden = false;
    var r = tip.getBoundingClientRect(), vw = document.documentElement.clientWidth, vh = window.innerHeight;
    var top = y + 18; if (top + r.height > vh - 8) top = y - r.height - 12;
    tip.style.left = Math.min(Math.max(8, x + 14), vw - r.width - 8) + 'px';
    tip.style.top = Math.max(8, top) + 'px';
  }
  function hide() { tip.hidden = true; shownFor = null; }
  function target(ev) { return ev.target && ev.target.closest ? ev.target.closest('[data-tip]') : null; }
  document.addEventListener('pointermove', function (ev) { var el = target(ev); if (!el) return hide(); fill(el); place(ev.clientX, ev.clientY); });
  document.addEventListener('pointerleave', hide);
  document.addEventListener('focusin', function (ev) {
    var el = target(ev); if (!el) return hide();
    fill(el); var r = el.getBoundingClientRect(); place(r.left + Math.min(r.width / 2, 240), r.bottom - 10);
  });
  document.addEventListener('focusout', hide);
  window.addEventListener('scroll', hide, { passive: true, capture: true });
  E.hideTip = hide;
})();
