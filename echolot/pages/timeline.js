// The trace handed to Perfetto UI in the frame below, the UI's own way for a
// page that knows a trace (#132): the page asks the server for the trace's
// bytes, sends PING into the frame until the UI answers PONG, which it does
// once it listens, and then posts the bytes, once. The moment to show is in
// the frame's address; the UI applies it to this, the first trace it loads.
(function () {
  var frame = document.getElementById('tl-frame'), said = document.getElementById('tl-said');
  if (!frame || !said || !window.fetch) return;
  var ui = frame.getAttribute('data-origin');
  function say(text) { said.textContent = text; }
  var bytes = fetch(frame.getAttribute('data-trace'), { cache: 'no-store', credentials: 'same-origin' })
    .then(function (reply) {
      if (!reply.ok) throw new Error('the server answered ' + reply.status);
      return reply.arrayBuffer();
    });
  bytes.catch(function (err) { say('The trace could not be read: ' + err.message + '.'); });
  var answered = false, pinging = null;
  window.addEventListener('message', function (ev) {
    if (answered || ev.source !== frame.contentWindow || ev.origin !== ui || ev.data !== 'PONG') return;
    answered = true;
    clearInterval(pinging);
    say('Handing the trace to Perfetto UI…');
    bytes.then(function (buf) {
      frame.contentWindow.postMessage({ perfetto: {
        buffer: buf, title: frame.getAttribute('data-title'), fileName: frame.getAttribute('data-file')
      } }, ui, [buf]);
      say('Opened in Perfetto UI. The trace stays in this browser.');
    }, function () {});
  });
  function ping() {
    if (answered) return;
    try { frame.contentWindow.postMessage('PING', ui); } catch (e) { /* not loaded yet */ }
  }
  frame.addEventListener('load', function () {
    if (pinging === null) pinging = setInterval(ping, 200);
    ping();
  });
})();
