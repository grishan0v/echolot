// The agent's answer: the command that checks the app again, to copy.
//
// The clipboard first. Where the browser keeps it from a page (some do for a
// file on disk), the text is selected, and the old command is tried on it.
(function () {
  var E = window.echolot, $ = E.$;
  E.all('[data-copy]').forEach(function (btn) {
    var text = $(btn.getAttribute('data-copy')), said = $(btn.getAttribute('data-said'));
    if (!text) return;
    // The button's new words are said aloud too: a changed label is not announced by itself.
    function say(words) { btn.textContent = words; if (said) said.textContent = words; }
    function done() { say('Copied'); }
    function select() {
      var range = document.createRange(); range.selectNodeContents(text);
      var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
      var ok = false;
      try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
      if (ok) return done();
      say('Selected: copy it now');
    }
    btn.addEventListener('click', function () {
      try { navigator.clipboard.writeText(text.textContent).then(done, select); } catch (err) { select(); }
    });
  });
})();
