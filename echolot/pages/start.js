// The start page: a button that copies the command beside it. Where the
// clipboard is refused, the command is selected instead, ready for a copy by hand.
(function () {
  var E = window.echolot;
  function select(el) {
    var range = document.createRange(); range.selectNodeContents(el);
    var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
  }
  E.all('[data-copy]').forEach(function (btn) {
    var src = E.$(btn.getAttribute('data-copy')), said = E.$('next-said'), word = btn.textContent, back = null;
    function say(text, shown) {
      btn.textContent = shown; if (said) said.textContent = text;
      clearTimeout(back); back = setTimeout(function () { btn.textContent = word; }, 2000);
    }
    function byHand() {
      select(src);
      var done = false;
      try { done = document.execCommand('copy'); } catch (err) { done = false; }
      if (done) say('Copied', 'Copied'); else say('Selected: copy it now', 'Selected');
    }
    btn.addEventListener('click', function () {
      if (!src) return;
      var text = src.textContent;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () { say('Copied', 'Copied'); }, byHand);
      } else byHand();
    });
  });
})();

// The button that checks the app. It is a form and works without any of
// this; with script on, the button and the line beside it follow the choice
// of a scenario and the number of launches, and the list of a run is redrawn
// in place instead of the whole page being loaded again.
(function () {
  var E = window.echolot, form = E.$('check-form'), go = E.$('check-go'), hint = E.$('check-hint');
  // A sentence of the page's, with a name written in backticks as code.
  function say(el, text) {
    el.textContent = '';
    String(text).split('`').forEach(function (part, i) {
      var node = i % 2 ? document.createElement('code') : document.createTextNode('');
      node.textContent = part; el.appendChild(node);
    });
  }
  if (form && go && hint) {
    var offers = {};
    try { offers = JSON.parse(form.getAttribute('data-offers') || '{}'); } catch (err) { offers = {}; }
    var count = E.$('launches'), anew = E.$('check-anew');
    function picked() {
      var one = E.all('input[name="what"]').filter(function (el) { return el.type !== 'radio' || el.checked; })[0];
      return one ? offers[one.value] : null;
    }
    function show() {
      var offer = picked(); if (!offer) return;
      var n = count && /^[0-9]+$/.test(count.value) ? String(parseInt(count.value, 10)) : '';
      go.textContent = offer.label;
      go.disabled = !!offer.why;
      say(hint, offer.why || (offer.hints && offer.hints[n]) || offer.hint);
      // What a press runs says the number a press would send.
      if (n && offer.hints && offer.hints[n]) {
        E.all('pre[data-n]').forEach(function (pre) { pre.textContent = pre.textContent.replace(/ -n [0-9]+/, ' -n ' + n); });
      }
      if (anew) anew.hidden = !offer.anew;
    }
    E.all('input[name="what"]').forEach(function (el) { el.addEventListener('change', show); });
    if (count) count.addEventListener('input', show);
    // One press is one press: a second click while the first is on its way
    // would be answered "a check is already running" in place of the run.
    var sent = false;
    form.addEventListener('submit', function (ev) { if (sent) ev.preventDefault(); sent = true; });
    window.addEventListener('pageshow', function () { sent = false; });
    // A browser may bring the page back with another choice than it was drawn with.
    show();
  }

  var run = E.$('run'), url = run && run.getAttribute('data-poll'), rows = E.$('run-rows'), said = E.$('run-said');
  if (!url || !rows || !window.fetch || !window.DOMParser) return;
  var last = null;
  function later() { setTimeout(ask, 1000); }
  function ask() {
    fetch(url, { cache: 'no-store', credentials: 'same-origin' }).then(function (reply) {
      if (!reply.ok) throw new Error('refused');
      return reply.text();
    }).then(function (text) {
      var now = new DOMParser().parseFromString(text, 'text/html').getElementById('run-now');
      // It ended, one way or another: the page that says how is this one, drawn anew.
      if (!now || now.getAttribute('data-state') !== 'running') { location.replace(location.pathname); return; }
      var list = now.querySelector('ol');
      if (list && text !== last) {
        var fresh = document.importNode(list, true);
        rows.textContent = '';
        while (fresh.firstChild) rows.appendChild(fresh.firstChild);
        last = text;
      }
      var words = now.getAttribute('data-said') || '';
      if (said && said.textContent !== words) said.textContent = words;
      later();
    }).catch(later);
  }
  later();
})();
