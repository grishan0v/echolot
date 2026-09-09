"""`hosts.pick()` — the one interactive prompt in the CLI.

Two kinds of test, and the split is the point.

Most of them replace `input` and cover the decision logic that turns a typed
line into a list of hosts. That logic is where the answers come from, and it
needs no terminal to check.

One of them runs `pick()` on a real pseudo-terminal and presses arrow keys at
it. That one is here because the rest cannot see the thing the prompt was
changed for: whether `readline` was loaded, and so whether the left arrow
edits the line or arrives inside the answer as the escape sequence the key
sends. A test that replaces `input` answers that question by assuming it.
"""

from __future__ import annotations

import io
import os
import select
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import hosts  # noqa: E402
from tests.support import check  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _pick(answer, detected: list[hosts.Host] | None = None):
    """Run `pick()` with `input` replaced. `answer` may be an exception."""
    detected = [hosts.BY_KEY["claude"], hosts.BY_KEY["gemini"]] \
        if detected is None else detected
    stream = io.StringIO()
    fake = mock.Mock(side_effect=answer) if isinstance(answer, type) \
        else mock.Mock(return_value=answer)
    with mock.patch("builtins.input", fake):
        result = hosts.pick(detected, stream=stream)
    return result, stream.getvalue()


# --- the decision logic -----------------------------------------------------

def test_empty_answer_keeps_the_default() -> None:
    detected = [hosts.BY_KEY["claude"], hosts.BY_KEY["gemini"]]
    result, _ = _pick("", detected=detected)
    check("Enter keeps whatever was detected", result == detected)


def test_all_selects_every_host() -> None:
    result, _ = _pick("all", detected=[hosts.BY_KEY["claude"]])
    check("\"all\" ignores what was detected", result == list(hosts.HOSTS))


def test_none_word_clears_the_selection() -> None:
    result, _ = _pick("none")
    check("\"none\" empties the list", result == [])


def test_numbers_select_by_position() -> None:
    result, _ = _pick("1 3")
    check("numbers map onto HOSTS by position, in the order typed",
          result == [hosts.HOSTS[0], hosts.HOSTS[2]])


def test_keys_select_by_name() -> None:
    result, _ = _pick("cursor,copilot")
    check("host keys work the same as numbers, comma or space separated",
          result == [hosts.BY_KEY["cursor"], hosts.BY_KEY["copilot"]])


def test_unknown_tokens_are_ignored_and_reported() -> None:
    result, out = _pick("1 bogus")
    check("a bad token alongside a good one does not drop the good one",
          result == [hosts.HOSTS[0]])
    check("a bad token is reported", "ignored: bogus" in out, out)


def test_a_hopeless_answer_falls_back_to_detected() -> None:
    result, out = _pick("bogus", detected=[hosts.BY_KEY["gemini"]])
    check("nothing usable in the answer keeps whatever was detected",
          result == [hosts.BY_KEY["gemini"]])
    check("the bad token is still reported", "ignored: bogus" in out, out)


@pytest.mark.parametrize("interrupt", [EOFError, KeyboardInterrupt])
def test_leaving_without_choosing_keeps_the_default(interrupt) -> None:
    """Ctrl-D and Ctrl-C, which `input()` reports as two different exceptions.

    Both mean the same thing here, and both have to be caught. #82 removed
    this handler for a while on the belief that the library underneath was
    swallowing them, which was true of one of the two.
    """
    detected = [hosts.BY_KEY["cursor"]]
    result, _ = _pick(interrupt, detected=detected)
    check(f"{interrupt.__name__} keeps whatever was detected", result == detected)


# --- the token vocabulary ---------------------------------------------------

def test_a_superscript_digit_is_an_unknown_word_and_not_a_crash() -> None:
    # `"²".isdigit()` is true and `int("²")` raises, so the obvious pair of
    # calls turns a pasted character into a ValueError rather than an answer.
    check("a superscript digit resolves to nothing", hosts._host_for("²") is None)
    picked, bad = hosts._parse("1,²")
    check("the rest of the line survives it", picked == [hosts.HOSTS[0]])
    check("and it is reported as an unknown word", bad == ["²"])


def test_a_line_of_punctuation_reads_as_no_answer() -> None:
    picked, bad = hosts._parse(",")
    check("nothing is chosen", picked == [])
    check("and there is nothing to complain about", bad == [])


# --- what the prompt does with a terminal in front of it --------------------

def _on_a_terminal(keys: bytes, timeout: float = 15.0) -> str:
    """Run `pick()` on a real pty, send `keys`, return what it chose.

    The handshake waits for the prompt to appear before typing, so the test
    does not depend on how long an interpreter takes to start.
    """
    import pty

    program = (
        "from echolot import hosts\n"
        "chosen = hosts.pick([hosts.BY_KEY['claude']])\n"
        "print('RESULT:' + ','.join(h.key for h in chosen))\n"
    )
    pid, fd = pty.fork()
    if pid == 0:                                    # pragma: no cover — the child
        os.execve(sys.executable, [sys.executable, "-c", program],
                  {**os.environ, "PYTHONPATH": str(ROOT)})

    seen, typed, deadline = "", False, time.monotonic() + timeout
    try:
        while "RESULT:" not in seen and time.monotonic() < deadline:
            if not select.select([fd], [], [], 0.2)[0]:
                continue
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            seen += chunk.decode(errors="replace")
            if not typed and "›" in seen:
                os.write(fd, keys)
                typed = True
    finally:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass
        os.waitpid(pid, 0)
        os.close(fd)
    return seen


@pytest.mark.skipif(sys.platform == "win32", reason="no pty, and no readline")
def test_the_arrow_keys_edit_the_line_instead_of_landing_in_it() -> None:
    """The whole reason the prompt loads `readline`, checked the only way.

    Typed here: "2", left arrow, "1 ". With line editing that is the line
    "1 2" and two hosts come back. Without it the answer still holds a "2"
    and a "1" with the escape sequence for the arrow key wedged between them,
    which is one unreadable word, and `pick()` falls back to what it
    detected. The two outcomes cannot be confused for each other.
    """
    seen = _on_a_terminal(b"2\x1b[D1 \r")
    check("the prompt answered at all", "RESULT:" in seen, seen[-400:])
    line = seen.split("RESULT:")[1].strip().splitlines()[0]
    want = f"{hosts.HOSTS[0].key},{hosts.HOSTS[1].key}"
    check(f"the edited line chose {want}", line == want,
          f"got {line!r} — the arrow key landed in the answer")
