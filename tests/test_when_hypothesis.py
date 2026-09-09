#!/usr/bin/env python3
"""Property-based tests for echolot/when.py.

`state.py` writes every timestamp echolot itself produces with
`datetime.now(timezone.utc).isoformat()`; `iso_epoch` reads timestamps back
out of files a person may have hand-edited. The property that matters is the
round trip for the format echolot writes, plus "never raises" for the
garbage a person might have typed instead.

"Garbage" has to be generated as near-misses rather than as free text.
`st.text()` never produces anything `datetime.fromisoformat` accepts, so a
strategy built on it only ever exercises the first line of the `try` — the
parse that fails — and reports a pass without having reached the arithmetic
underneath, which is where the remaining ways to raise live.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from hypothesis import example, given
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot.when import ago, iso_epoch  # noqa: E402

aware_datetime = st.datetimes(
    min_value=datetime(1990, 1, 1),
    max_value=datetime(2100, 1, 1),
    timezones=st.just(timezone.utc),
)

# Stamps that get far enough into iso_epoch to matter: a real ISO-8601 string
# with one thing done to it. Free text is in there too, but on its own it
# would only ever test that fromisoformat rejects free text.
_MUTATIONS = [
    lambda s: s,
    lambda s: s.replace("+00:00", "Z"),
    lambda s: s.replace("+00:00", ""),          # naive: no offset at all
    lambda s: s.replace("T", " "),
    lambda s: s.replace("-", "/"),
    lambda s: s[:-1],
    lambda s: s + "Z",
    lambda s: s.replace(":", ""),
]
iso_shaped = st.one_of(
    st.builds(lambda dt, f: f(dt.isoformat()), aware_datetime,
              st.sampled_from(_MUTATIONS)),
    # The two ends of the calendar, where the string parses and the
    # conversion to epoch seconds is the step that can still fail.
    st.sampled_from([
        "0001-01-01T00:00:00", "9999-12-31T23:59:59",
        "0001-01-01T00:00:00+00:00", "9999-12-31T23:59:59+00:00",
        "0001-01-01T00:00:00Z", "9999-12-31T23:59:59Z",
    ]),
    st.text(min_size=0, max_size=60),
)


@given(aware_datetime)
def test_iso_epoch_round_trips_what_state_py_writes(dt):
    """isoformat() → iso_epoch() is the exact round trip state.py relies on."""
    stamp = dt.isoformat()
    got = iso_epoch(stamp)
    assert got is not None
    assert got == dt.timestamp()


@given(aware_datetime)
def test_iso_epoch_accepts_the_trailing_z_spelling(dt):
    """`Z` is a spelling of `+00:00` that fromisoformat itself does not accept
    on older pythons; iso_epoch translates it before parsing."""
    stamp = dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    assert iso_epoch(stamp) == dt.timestamp()


@given(iso_shaped)
@example("")
@example("not a timestamp")
@example("2024-13-40T99:99:99")
@example("0001-01-01T00:00:00")      # parses; .timestamp() is what fails
@example("9999-12-31T23:59:59")      # and again, at the other end
def test_iso_epoch_never_raises_on_a_stamp_it_did_not_write(text):
    """A hand-edited file gets an absent timestamp, never a crash.

    The two calendar-edge examples are the ones worth having. They parse —
    `fromisoformat` is happy with either — and then raise inside
    `.timestamp()`, because converting a naive datetime at the very start or
    end of the calendar walks off the end of it. `iso_epoch` catches
    `ValueError`, which is what CPython raises for that here; a platform
    where the same conversion raises `OSError` or `OverflowError` instead
    would crash, and this test is where that would surface.
    """
    result = iso_epoch(text)
    assert result is None or isinstance(result, float)


def test_iso_epoch_of_none_is_none():
    assert iso_epoch(None) is None


@given(st.floats(min_value=1, max_value=10_000_000, allow_nan=False))
def test_ago_never_raises_and_always_says_ago(delta_seconds):
    """Every branch ends in the same suffix, for any age this trace could be."""
    epoch = datetime.now(timezone.utc).timestamp() - delta_seconds
    result = ago(epoch)
    assert result.endswith(" ago")


@given(st.floats(min_value=-1_000_000, max_value=1_000_000, allow_nan=False))
@example(0.0)
@example(-0.0)
def test_ago_says_never_for_exactly_zero_and_for_nothing_else(epoch):
    """Documented edge case, not a bug fix: `epoch=0.0` (a real Unix instant,
    1970-01-01) is falsy in Python, so `ago(0.0)` reads "never" rather than
    "Nd ago" like any other epoch this far in the past would. `ago` is only
    ever called on timestamps `state.py` itself wrote or `None`, so a real
    trace never produces exactly 0.0 — but the function does not special-case
    it, and `if not epoch` silently swallows it.

    Both halves are asserted. Checking only the zero case would leave every
    other generated float running the function and dropping the answer — a
    hundred examples an example, none of which can fail.
    """
    if epoch == 0.0:            # -0.0 too: it is falsy and equal to 0.0
        assert ago(epoch) == "never"
    else:
        assert ago(epoch) != "never"
        assert ago(epoch).endswith(" ago")


@given(st.integers(min_value=1, max_value=3600))
def test_ago_orders_recent_before_older(gap_seconds):
    """A more recent timestamp is never reported as further in the past.

    Both are read against "now" a moment apart, so this compares the two
    calls' relative ordering rather than an exact string — the coarsening
    into seconds/minutes/hours/days means two epochs close together can
    legitimately render identically.
    """
    now = datetime.now(timezone.utc).timestamp()
    older = now - gap_seconds - 1
    newer = now - 1
    unit_rank = {"s": 0, "m": 1, "h": 2, "d": 3}

    def rank(text):
        for suffix, r in unit_rank.items():
            if suffix + " ago" in text:
                return r
        return -1

    assert rank(ago(newer)) <= rank(ago(older))
