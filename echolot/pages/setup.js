// Setting a project up. The form works without any of this. With script on,
// a command or the lines to add are copied at a press, and one press of
// Set it up is one press: a second click while the first is on its way would
// be answered "already under way" in place of the page that shows it.
(function () {
  var E = window.echolot;
  function select(el) {
    var range = document.createRange(); range.selectNodeContents(el);
    var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
  }
  E.all('[data-setup-copy]').forEach(function (btn) {
    var src = E.$(btn.getAttribute('data-setup-copy')), said = btn.nextElementSibling, word = btn.textContent, back = null;
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
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(src.textContent).then(function () { say('Copied', 'Copied'); }, byHand);
      } else byHand();
    });
  });

  var form = E.$('setup-form'), go = E.$('setup-go');
  if (!form || !go) return;
  var sent = false, word = go.textContent;
  form.addEventListener('submit', function (ev) {
    if (sent) { ev.preventDefault(); return; }
    sent = true; go.textContent = 'Setting up…';
  });
  window.addEventListener('pageshow', function () { sent = false; go.textContent = word; });
})();
