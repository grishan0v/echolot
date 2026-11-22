// Dig deeper from the page. Every step is a form and works without any of
// this. With script on: the request dialog of a report sends what is ticked
// to the screen before the agent starts, that screen copies the request, and
// one press of Start is one press.
(function () {
  var E = window.echolot;

  // The dialog: the ticked checks go with the form, a field each.
  var ask = E.$('ask-start-form');
  if (ask && E.ticked) {
    ask.addEventListener('submit', function () {
      E.all('input[data-dig]', ask).forEach(function (el) { el.parentNode.removeChild(el); });
      E.ticked().forEach(function (c) {
        var el = document.createElement('input');
        el.type = 'hidden'; el.name = 'pick.' + c.dataset.id; el.value = '1'; el.setAttribute('data-dig', '');
        ask.appendChild(el);
      });
    });
  }

  // The screen before it starts: the request to copy, the way out.
  var copy = E.$('dig-copy'), text = E.$('dig-request'), said = E.$('dig-copied');
  if (copy && text) {
    var word = copy.textContent;
    function say(words) { copy.textContent = words; if (said) said.textContent = words; }
    function byHand() {
      var fold = text.closest('details'); if (fold) fold.open = true;
      var range = document.createRange(); range.selectNodeContents(text);
      var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
      var ok = false;
      try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
      say(ok ? 'Copied' : 'Selected: copy it now');
    }
    copy.addEventListener('click', function () {
      try { navigator.clipboard.writeText(text.textContent).then(function () { say('Copied'); }, byHand); }
      catch (err) { byHand(); }
      setTimeout(function () { copy.textContent = word; }, 2500);
    });
  }

  // One press is one press: a second click while the first is on its way
  // would be answered "already digging" in place of the page of the run.
  E.all('#dig-start-form, #ask-start-form').forEach(function (form) {
    var sent = false;
    form.addEventListener('submit', function (ev) { if (sent) ev.preventDefault(); sent = true; });
    window.addEventListener('pageshow', function () { sent = false; });
  });
})();
