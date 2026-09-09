#!/usr/bin/env python3
"""Where the custom trace_processor came from, as the report states it.

`toolchain.tp_binary` in a `local.yml` had never been exercised on a live run.
The first one showed the binary being picked up correctly and the report
naming the wrong reason for it.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import tp as tp_mod  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import _tp_binary, _tp_binary_source  # noqa: E402


def args(**kw):
    return types.SimpleNamespace(**kw)


def test_the_flag_wins_and_is_named_as_the_flag():
    got = _tp_binary_source(args(tp_binary="/opt/tp"),
                            Config({"toolchain": {"tp_binary": "/from/config"}}))
    assert got == ("/opt/tp", "--tp-binary"), got


def test_a_binary_from_local_yml_is_not_reported_as_a_flag():
    """The mislabel a live run surfaced.

    `local.yml` is normally gitignored, so it is invisible to whoever reads
    the report next. Told that the toolchain came from `--tp-binary`, they
    search a shell history for a flag nobody typed while the answer sits in a
    file beside the config.
    """
    path, source = _tp_binary_source(
        args(tp_binary=None), Config({"toolchain": {"tp_binary": "/from/config"}}))
    assert path == "/from/config", path
    assert source == "toolchain.tp_binary", source


def test_nothing_configured_is_the_pin():
    assert _tp_binary_source(args(tp_binary=None), Config({})) == (None, None)
    assert _tp_binary(args(tp_binary=None), Config({})) is None


def test_the_report_carries_the_source_it_was_given():
    info = tp_mod.toolchain_info("/from/config", "toolchain.tp_binary")
    assert info["source"] == "toolchain.tp_binary", info
    assert info["binary"] == "/from/config", info

    # And an unnamed source still reads as the flag, which is what every
    # caller that does not know meant before this argument existed.
    assert tp_mod.toolchain_info("/opt/tp")["source"] == "--tp-binary"

    # The pinned case names no binary at all.
    pinned = tp_mod.toolchain_info()
    assert pinned["source"] == "pinned", pinned
    assert pinned["binary"] is None, pinned
