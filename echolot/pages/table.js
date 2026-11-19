// The filter above a table, and the panel that shows one of its rows in full.
//
// One <dialog> for the whole page, filled from the <template> of the opened
// row. Beside the table in a wide window it is not modal and the page stays
// usable; in a narrower one it is modal, over a scrim; on a phone it is a
// screen of its own.
(function () {
  var E = window.echolot, $ = E.$, all = E.all, root = document.documentElement;
  var peek = $('peek'), body = $('peek-b'), cur = null;
  if (!peek) return;
  var wide = window.matchMedia('(min-width: 1180px)');
  function ask() { var a = $('ask'); return !!(a && a.open); }
  function rows() { return all('.tr:not([hidden])'); }
  function rowOf(id) { return rows().filter(function (r) { return r.dataset.id === id; })[0] || null; }
  function unmark() {
    all('.tr.on').forEach(function (r) { r.classList.remove('on'); r.querySelector('.rowbtn').setAttribute('aria-expanded', 'false'); });
  }
  function mark(id) {
    var list = rows(), at = -1;
    unmark();
    list.forEach(function (r, i) { if (r.dataset.id === id) { at = i; r.classList.add('on'); r.querySelector('.rowbtn').setAttribute('aria-expanded', 'true'); } });
    $('peek-pos').textContent = at < 0 ? '' : (at + 1) + ' of ' + list.length;
  }
  function show() {
    if (wide.matches) { peek.show(); root.classList.add('has-peek'); }
    else { root.classList.remove('has-peek'); peek.showModal(); }
  }
  // focus: true when the reader opened it, and focus goes to Close; null for
  // a step, where focus stays on the button pressed.
  function open(id, focus) {
    var t = $('peek-' + id); if (!t) return;
    E.hideTip();
    body.textContent = ''; body.appendChild(t.content.cloneNode(true));
    all('.pick', body).forEach(function (c) { var src = $('pick-' + c.dataset.id); if (src) c.checked = src.checked; });
    peek.setAttribute('aria-labelledby', 'peek-title');
    cur = id; body.scrollTop = 0;
    var fresh = !peek.open;
    if (fresh) show();
    mark(id);
    if (focus === true) $('peek-close').focus();
    // Beside the table the panel narrows it, and the rows above grow taller.
    if (fresh && wide.matches) { var row = rowOf(id); if (row) row.scrollIntoView({ block: 'nearest' }); }
  }
  function close() { if (peek.open) peek.close(); }
  peek.addEventListener('close', function () {
    if (peek.open) return;   // closed to change between beside and over, and open again
    var row = cur && rowOf(cur);
    root.classList.remove('has-peek');
    unmark();
    cur = null;
    if (row) { row.querySelector('.rowbtn').focus({ preventScroll: true }); row.scrollIntoView({ block: 'nearest' }); }
  });
  function step(by) {
    var list = rows(), at = -1;
    list.forEach(function (r, i) { if (r.dataset.id === cur) at = i; });
    var next = list[(at + by + list.length) % list.length];
    if (next) { open(next.dataset.id, null); if (wide.matches) next.scrollIntoView({ block: 'nearest' }); }
  }
  // The whole row opens the panel, except its tick box, and except a click
  // that ends a selection of its text.
  document.addEventListener('click', function (ev) {
    var tr = ev.target.closest && ev.target.closest('.tr');
    if (!tr || ev.target.closest('.ck') || ev.target.closest('a')) return;
    if (!ev.target.closest('.rowbtn') && String(window.getSelection())) return;
    open(tr.dataset.id, true);
  });
  $('peek-close').addEventListener('click', close);
  $('peek-prev').addEventListener('click', function () { step(-1); });
  $('peek-next').addEventListener('click', function () { step(1); });
  peek.addEventListener('click', function (ev) { if (ev.target === peek) close(); });
  // A modal dialog closes on Escape by itself; beside the table it is not one.
  document.addEventListener('keydown', function (ev) { if (ev.key === 'Escape' && peek.open && !ask()) { ev.preventDefault(); close(); } });
  // The window crossed the width where the panel changes its kind.
  function rehome() {
    if (!peek.open || ask()) return;
    var id = cur, had = peek.contains(document.activeElement) ? document.activeElement : null;
    peek.close(); show(); mark(id);
    if (had) had.focus();
  }
  if (wide.addEventListener) wide.addEventListener('change', rehome); else wide.addListener(rehome);

  // The filter: a level, or everything. It closes the panel and leaves the ticks.
  all('.view').forEach(function (b) {
    b.disabled = b.dataset.n === '0';
    b.addEventListener('click', function () {
      var bar = b.closest('.views'), want = b.dataset.view;
      // A closing dialog hands focus back to where it came from: here it stays on the filter.
      cur = null; close(); b.focus();
      all('.view', bar).forEach(function (x) { x.setAttribute('aria-pressed', String(x === b)); });
      all('.tr', bar.parentNode).forEach(function (r) { r.hidden = want !== 'all' && r.dataset.group !== want; });
    });
  });
  E.openRow = open; E.closeRow = close;
})();
