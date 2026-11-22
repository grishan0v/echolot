// What is ticked, the bar that counts it, and the request it turns into.
//
// A check can be drawn in two places, a row and its opened panel: boxes with
// one `data-id` move together. The request is put together here, from what the
// page already holds, so it works in a file opened from disk: the head and the
// tail are on the dialog, the rows of each check on its tick box.
(function () {
  var E = window.echolot, $ = E.$, all = E.all;
  var dock = $('dock'), ask = $('ask');
  if (!dock || !ask) return;

  function picks() { return all('.pick'); }
  function ticked() {
    var seen = {}, out = [];
    picks().forEach(function (c) { if (c.checked && !seen[c.dataset.id]) { seen[c.dataset.id] = 1; out.push(c); } });
    return out;
  }
  function refresh() {
    var n = ticked().length, total = +$('dock-all').dataset.n;
    $('dock-t').textContent = n === 0 ? 'Nothing ticked yet' : n + (n === 1 ? ' item' : ' items') + ' selected';
    $('dock-go').disabled = n === 0;
    $('dock-clear').hidden = n === 0;
    $('dock-all').hidden = n >= total || (n > 0 && startTicked());
    E.onPick.forEach(function (f) { f(); });
  }
  function setPick(id, on) { picks().forEach(function (c) { if (c.dataset.id === id) c.checked = on; }); }
  // Where to start: the checks that need attention, or with none of those
  // the two that found the largest thing. One investigation, one question.
  function toStart() {
    var seen = {}, own = picks().filter(function (c) { if (seen[c.dataset.id]) return false; seen[c.dataset.id] = 1; return true; });
    var red = own.filter(function (c) { return c.dataset.level === 'red'; });
    if (red.length) return red;
    return own.sort(function (a, b) { return (+b.dataset.weight || 0) - (+a.dataset.weight || 0); }).slice(0, 2);
  }
  function startTicked() {
    var on = {}; ticked().forEach(function (c) { on[c.dataset.id] = 1; });
    var want = toStart();
    return want.length === ticked().length && want.every(function (c) { return on[c.dataset.id]; });
  }
  document.addEventListener('change', function (ev) {
    var c = ev.target;
    if (!c.classList || !c.classList.contains('pick')) return;
    setPick(c.dataset.id, c.checked);
    refresh();
  });
  // Emptying the bar takes its buttons away, and focus must not go with them.
  $('dock-all').addEventListener('click', function () {
    var want = {}; toStart().forEach(function (c) { want[c.dataset.id] = 1; });
    picks().forEach(function (c) { c.checked = !!want[c.dataset.id]; }); refresh(); $('dock-go').focus();
  });
  $('dock-clear').addEventListener('click', function () { picks().forEach(function (c) { c.checked = false; }); refresh(); $('dock-all').focus(); });

  // The request, block by block, as report_page.request_text puts it together.
  function request() {
    var rows = [], d = ask.dataset, blocks = [d.question];
    ticked().forEach(function (c) { rows = rows.concat(c.dataset.req.split('\n')); });
    if (d.warn) blocks.push(d.warnHead + '\n' + d.warn);
    blocks.push([d.rowsHead].concat(rows).join('\n'));
    blocks.push(d.tail);
    return blocks.join('\n\n');
  }
  E.request = request;
  E.ticked = ticked;
  $('dock-go').addEventListener('click', function () {
    var many = $('ask-many'), n = ticked().length, focus = +ask.dataset.focus || 3;
    if (many) {
      many.hidden = n <= focus;
      many.firstElementChild.textContent = (ask.dataset.manyNote || '').split('{n}').join(String(n));
    }
    var list = $('ask-list'); list.textContent = '';
    ticked().forEach(function (c) {
      var li = document.createElement('li'), s = document.createElement('span');
      var icon = document.querySelector('[data-icon="' + c.dataset.level + '"]');
      s.textContent = c.dataset.title;
      if (icon) li.appendChild(icon.firstElementChild.cloneNode(true));
      li.appendChild(s); list.appendChild(li);
    });
    $('ask-text').textContent = request();
    $('ask-copy').textContent = 'Copy the request';
    $('ask-said').textContent = '';
    ask.showModal();
    ($('ask-start') || $('ask-copy')).focus();
  });
  $('ask-close').addEventListener('click', function () { ask.close(); });
  ask.addEventListener('click', function (ev) { if (ev.target === ask) ask.close(); });
  ask.addEventListener('close', function () { $('dock-go').focus(); });

  // The clipboard first. Where the browser keeps it from a page (some do for a
  // file on disk), the text is selected, and the old command is tried on it.
  $('ask-copy').addEventListener('click', function () {
    var btn = this, pre = $('ask-text'), said = $('ask-said');
    // The button's new words are said aloud too: a changed label is not announced by itself.
    function say(text) { btn.textContent = text; said.textContent = text; }
    function done() { say('Copied'); }
    function select() {
      pre.closest('details').open = true;
      var range = document.createRange(); range.selectNodeContents(pre);
      var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
      var ok = false;
      try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
      if (ok) return done();
      say('Selected: copy it now');
    }
    try { navigator.clipboard.writeText(pre.textContent).then(done, select); } catch (err) { select(); }
  });

  E.refresh = refresh;
  refresh();
})();
