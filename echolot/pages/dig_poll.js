// The agent at work, on its investigation's page. The page loads itself again
// where script is off; with script on it asks this server every two seconds
// what the agent is doing, says it in place, and draws the page anew when a
// round came in, the investigation closed, or the agent ended.
(function () {
  var E = window.echolot, box = E.$('dig'), url = box && box.getAttribute('data-poll');
  if (!url || !window.fetch || !window.DOMParser) return;
  var phase = E.$('dig-phase'), said = E.$('dig-said'), quiet = E.$('dig-quiet'), quietText = E.$('dig-quiet-t');
  var rev = box.getAttribute('data-rev');
  function later() { setTimeout(look, 2000); }
  function look() {
    fetch(url, { cache: 'no-store', credentials: 'same-origin' }).then(function (reply) {
      if (!reply.ok) throw new Error('refused');
      return reply.text();
    }).then(function (text) {
      var now = new DOMParser().parseFromString(text, 'text/html').getElementById('dig-now');
      if (!now || now.getAttribute('data-state') !== 'running' || now.getAttribute('data-rev') !== rev) {
        location.replace(location.pathname); return;
      }
      var words = now.getAttribute('data-said') || '';
      if (phase && phase.textContent !== words) phase.textContent = words;
      if (said && said.textContent !== words) said.textContent = words;
      var q = now.getAttribute('data-quiet') || '';
      if (quiet && quietText) { quiet.hidden = !q; if (quietText.textContent !== q) quietText.textContent = q; }
      if (now.getAttribute('data-stopping')) E.all('#dig button[type="submit"]').forEach(function (b) { b.disabled = true; });
      later();
    }).catch(later);
  }
  later();
})();
