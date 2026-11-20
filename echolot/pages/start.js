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
