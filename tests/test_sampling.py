#!/usr/bin/env python3
"""Callstack sampling: what `collect` asks the device for, and what the report says.

The sampler is the one part of a recording that slows the app on purpose, so
it is off unless `runner.sampling` asks for it, and every report says whether
one ran. What it says comes from the trace, and a sampler comes back empty in
three ways that each have their own cause; each is held here to its own
sentence.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from google.protobuf import text_format
from perfetto.protos.perfetto.trace import perfetto_trace_pb2 as pb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402

from echolot import fixture, runner, selftest  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import analyze_trace  # noqa: E402


def parsed(text: str) -> pb.TraceConfig:
    """The config as perfetto on the device will read it: protobuf text."""
    return text_format.Parse(text, pb.TraceConfig())


# --- what the device is asked for ------------------------------------------------

def test_the_config_parses_with_the_sampler_and_without() -> None:
    """A config that does not parse on the device fails every capture, silently."""
    for hz in (None, 100):
        cfg = parsed(runner.trace_config("com.example.app", 12000,
                                         runner.DEFAULT_CATEGORIES, 131072,
                                         sampling_hz=hz))
        names = [ds.config.name for ds in cfg.data_sources]
        check(f"{hz}: the sampler is there exactly when asked for",
              ("linux.perf" in names) == bool(hz), names)


def test_the_sampler_is_the_one_measured_on_a_device() -> None:
    cfg = parsed(runner.trace_config("com.example.app", 12000,
                                     runner.DEFAULT_CATEGORIES, 65536,
                                     sampling_hz=100))
    perf = next(ds.config for ds in cfg.data_sources if ds.config.name == "linux.perf")
    stacks = perf.perf_event_config.callstack_sampling
    check("at the rate asked", perf.perf_event_config.timebase.frequency == 100,
          str(perf))
    check("no process filter: it loses a cold start",
          not stacks.HasField("scope"), str(stacks))
    check("user frames only", stacks.kernel_frames is False, str(stacks))
    check("into a buffer of its own, the main one's size",
          perf.target_buffer == 1 and [b.size_kb for b in cfg.buffers] == [65536, 65536],
          str(cfg.buffers))
    others = [ds.config for ds in cfg.data_sources if ds.config.name != "linux.perf"]
    check("while everything else stays in the first",
          all(c.target_buffer == 0 for c in others), [c.name for c in others])


@pytest.mark.parametrize("value, hz", [
    (None, None), (False, None), (True, runner.SAMPLING_HZ), (150, 150), (99.9, 99),
], ids=["empty", "false", "true", "rate", "fraction"])
def test_what_sampling_accepts(value, hz) -> None:
    said: list[str] = []
    check(f"{value!r} → {hz}", runner.sampling({"sampling": value}, said.append) == hz)
    check("and nothing to warn about", not said, said)


@pytest.mark.parametrize("value", ["250Hz", 0, -5, [100]],
                         ids=["unit", "zero", "negative", "list"])
def test_what_sampling_refuses(value) -> None:
    with pytest.raises(runner.RunnerError) as e:
        runner.sampling({"sampling": value})
    check("the refusal says how to write it",
          "number of Hz" in str(e.value) and "`false`" in str(e.value), str(e.value))


def test_a_rate_above_the_advice_is_recorded_and_warned_about() -> None:
    said: list[str] = []
    check("recorded as asked", runner.sampling({"sampling": 1000}, said.append) == 1000)
    check("with the reason it may not come back whole",
          len(said) == 1 and "without a stack" in said[0], said)


# --- what the report says ------------------------------------------------------

def test_a_sampler_that_never_ran_is_said(tmp_path) -> None:
    """Asked for, and the device had no sampler to run: said, never left to be found."""
    trace = tmp_path / "asked.perfetto-trace"
    trace.write_bytes(fixture.build(sampling="asked"))
    s = analyze_trace(trace, Config(selftest.FIXTURE_CONFIG))["environment"]["sampling"]
    check("asked at 100 Hz, and nothing arrived",
          s == {"hz": 100, "started": False, "samples": 0, "with_stack": 0}, s)
    lines = report_mod._sampling_lines(s)
    check("the report says the sampler did not run, and that nothing was slowed",
          len(lines) == 1 and "did not run" in lines[0] and "not slowed" in lines[0],
          lines)


def one(samples: int, with_stack: int, **more) -> dict:
    return {"hz": 100, "started": True, "samples": samples, "with_stack": with_stack,
            **more}


def test_each_way_of_coming_back_has_its_own_sentence() -> None:
    check("nothing sampled, nothing said", report_mod._sampling_lines(None) == [])

    line = report_mod._sampling_lines(one(30, 28))
    check("a sampled round is one line, with its share of stacks and its cost",
          len(line) == 1 and not line[0].startswith(">")
          and "30 samples" in line[0] and "93% with a stack" in line[0]
          and "same rate" in line[0], line)

    unwound = report_mod._sampling_lines(one(30, 0))
    check("none with a stack: the manifest is named, and so is a start the sampler lost",
          unwound[0].startswith("> ⚠️") and "profileable" in unwound[0]
          and "lost the process as it started" in unwound[0], unwound)

    missed = report_mod._sampling_lines(one(0, 0))
    check("no samples of ours: the filter that misses a starting process is named",
          missed[0].startswith("> ⚠️") and "zygote" in missed[0], missed)


def test_a_share_is_not_rounded_to_all_or_nothing() -> None:
    check("999 of 1000 is not 100%",
          "99% with a stack" in report_mod._sampling_lines(one(1000, 999))[0])
    check("1 of 1000 is not 0%",
          "1% with a stack" in report_mod._sampling_lines(one(1000, 1))[0])
    check("all of them is said so",
          "all with a stack" in report_mod._sampling_lines(one(1000, 1000))[0])


def test_repeats_merge_to_medians_and_a_count() -> None:
    merged = report_mod._merge_sampling([one(30, 28), one(40, 36), one(50, 50)])
    check("medians of the repeats, how many ran a sampler, and how many got stacks",
          merged == {"hz": 100, "started": True, "samples": 40, "with_stack": 36,
                     "runs": "3/3", "runs_with_stack": "3/3"}, merged)
    check("a set nothing sampled merges to nothing",
          report_mod._merge_sampling([None, None]) is None)

    half = report_mod._merge_sampling([one(30, 28), None])
    check("half a set sampled is counted as half", half["runs"] == "1/2", half)
    line = report_mod._sampling_lines(half)[0]
    check("and the report says the repeats were not recorded alike",
          "in 1 of 2 repeats" in line and "not recorded alike" in line, line)

    rates = report_mod._merge_sampling([one(30, 28), {**one(30, 28), "hz": 250}])
    check("two rates in one set leave no single rate to name",
          rates["hz"] is None, rates)


def test_a_repeat_without_stacks_is_not_hidden_by_the_median() -> None:
    """What a device did: one cold start of six lost every stack, the rest had all.

    The build was profileable. A median over the six reads "all with a stack",
    and the one repeat with none disappears from the line.
    """
    merged = report_mod._merge_sampling(
        [one(2335, 2335), one(2306, 2306), one(2276, 0), one(2331, 2331),
         one(2347, 2347), one(2156, 2156)])
    check("five of the six got stacks", merged["runs_with_stack"] == "5/6", merged)
    line = report_mod._sampling_lines(merged)[0]
    check("the line says so, and gives no share a median would have made up",
          "in 5 of 6 repeats" in line and "%" not in line
          and "all with a stack" not in line, line)
    none = report_mod._merge_sampling([one(30, 0), one(40, 0)])
    check("no repeat with a stack is the warning",
          report_mod._sampling_lines(none)[0].startswith("> ⚠️"), none)
