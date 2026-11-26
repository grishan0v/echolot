"""`echolot ui`'s timeline page: a moment of a report, opened in Perfetto UI
with its trace (#132).

The page is a line that says what is opened and how far it got, and Perfetto
UI in a frame under it, filling the window, at an address that zooms to the
moment and pins the thread it happened on (`timeline.fragment`). The page asks the server it came
from for the trace's bytes and posts them into the frame, as the UI asks a
page that embeds it to (`timeline.js`). The trace goes to the frame in this
browser and nowhere else: Perfetto UI runs in the page, and sends a trace
handed to it this way to no server.

A frame and not a new window: a window the page opened would have to be
opened by the click itself, before the trace is read, and the server's
pages are sent with `Cross-Origin-Opener-Policy: same-origin`, under which
the page could not talk to such a window at all. A frame needs neither, and
the address of this page opens the same moment again, reloaded or not.

Its content policy is the served pages' and one thing more: the frame may
be Perfetto UI, and only that (`POLICY`).
"""

from __future__ import annotations

from .. import timeline as timeline_mod
from . import kit
from .kit import e

CSS = ("base.css", "timeline.css")
JS = ("timeline.js",)

# The served pages' policy, and Perfetto UI in a frame. Sent as the header
# too, in place of the one every other page gets (`ui.HEADERS`).
POLICY = f"{kit.SERVED}; frame-src {timeline_mod.UI}"


def render(m: "timeline_mod.Moment", trace_href: str) -> str:
    """The page for one moment. `trace_href` is where this server gives the
    moment's trace."""
    words = timeline_mod.label(m)
    title = f"{m.detector + ': ' if m.detector else ''}{words.replace('`', '')} · Perfetto UI"
    file = timeline_mod.short_name(m.trace) + ".perfetto-trace"
    body = (f'<div class="tl-bar"><b>{kit.prose(words)}</b>'
            f'<span class="tl-said" id="tl-said" role="status">Reading the trace…</span>'
            f'<noscript><span class="tl-said">The trace is handed to Perfetto UI by a script, and script is '
            f'switched off in this browser.</span></noscript></div>'
            f'<iframe class="tl-frame" id="tl-frame" title="Perfetto UI" '
            f'src="{e(timeline_mod.UI)}/#!/?{e(timeline_mod.fragment(m))}" '
            f'data-origin="{e(timeline_mod.UI)}" data-trace="{e(trace_href)}" '
            f'data-title="{e(timeline_mod.short_name(m.trace))}" data-file="{e(file)}"></iframe>')
    return kit.document(title, body, css=CSS, js=JS, policy=POLICY)
