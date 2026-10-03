"""The Reflect Report: report.json for the agent, report.md for the human.

Same split as the Marker Report. The json is the stable, complete thing —
`/echolot-reflect` reads it; the markdown is what a person opens first:
signals on top, the facts they rest on below.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from .. import table
from .facts import Facts
from .model import Session
from .signals import Signal

_MARK = {"warn": "⚠", "info": "ℹ", "ok": "✓", "skip": "·"}


def build(session: Session, facts: Facts, signals: list[Signal]) -> dict[str, Any]:
    by_sev: dict[str, int] = {"warn": 0, "info": 0, "ok": 0, "skip": 0}
    for s in signals:
        by_sev[s.severity] = by_sev.get(s.severity, 0) + 1
    return {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {
            "agent": session.agent,
            "agent_version": session.agent_version,
            "model": session.model,
            "session": session.id,
            "cwd": session.cwd,
            "git_branch": session.git_branch,
            "files": session.sources,
            # What this source can show, and what the reader wants said about
            # it. A consumer that ignores both will read an absent finding as
            # an absent problem.
            "carries": session.carries,
            "notes": session.notes,
        },
        "context": {
            "started": session.started,
            "ended": session.ended,
            "duration_s": facts.cost.get("duration_s"),
            "config": facts.config,
            # Whether this session was writing the tool rather than running
            # it. Carried into the JSON because the reader that acts on the
            # report needs the same reason the page gives a human.
            "building": facts.building,
        },
        "summary": {
            "signals": by_sev,
            "warn_ids": [s.id for s in signals if s.severity == "warn"],
            "info_ids": [s.id for s in signals if s.severity == "info"],
            "skipped_ids": [s.id for s in signals if s.severity == "skip"],
            "echolot_calls": len(facts.echolot_calls),
            "hunts": len(facts.hunts),
            "asks": len(session.asks),
        },
        "signals": [s.to_dict() for s in signals],
        "entry": facts.entry,
        "timeline": facts.milestones,
        "echolot_calls": [asdict(c) for c in facts.echolot_calls],
        "questions": [asdict(a) for a in session.asks],
        "hunts": facts.hunts,
        "conclusion": facts.conclusion,
        "instrumentation": facts.instrumentation,
        "cost": {**facts.cost, "top_outputs": facts.top_outputs, "gaps": facts.gaps},
        "runs_recorded": facts.runs,
        "notes": session.notes,
    }


def to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- markdown

def to_markdown(report: dict[str, Any]) -> str:
    """The report as a page: a section per function below, in this order."""
    out: list[str] = []
    for section in _SECTIONS:
        section(report, out)
    return "\n".join(out)


def _head(report: dict[str, Any], out: list[str]) -> None:
    src, ctx = report["source"], report["context"]
    out.append("# Reflect Report")
    out.append("")
    head = [f"Session `{src['session']}`", src["agent"]]
    if src.get("agent_version"):
        head[-1] += f" {src['agent_version']}"
    if src.get("model"):
        head.append(f"model `{src['model']}`")
    out.append(" · ".join(head))
    span = _span(ctx.get("started"), ctx.get("ended"), ctx.get("duration_s"))
    if span:
        out.append(span)
    if src.get("cwd"):
        out.append(f"cwd `{src['cwd']}`" + (f" · branch `{src['git_branch']}`"
                                            if src.get("git_branch") else ""))
    out.append(_config_line(ctx.get("config") or {}))
    # Said here rather than left to the Not-checked section. Sixteen checks
    # held back is the loudest number in the tally below, and a reader who
    # does not know why reads it as a report that failed to do its job.
    if (report.get("context") or {}).get("building"):
        out.append("_This session was building echolot rather than using it — "
                   "the project is the tool's own checkout and nothing was "
                   "collected or hunted. The hunting-protocol checks are held "
                   "back below: a session spent writing detectors breaks every "
                   "one of them by definition._")


def _config_line(cfg: dict[str, Any]) -> str:
    if not cfg.get("present"):
        return ("Config: _none found in this directory — protocol checks that "
                "need it were skipped_")
    bits = [f"`{cfg.get('path')}`"]
    if cfg.get("scenario"):
        bits.append(f"scenario `{cfg['scenario']}`")
    if cfg.get("max_rounds") is not None:
        bits.append(f"max_rounds {cfg['max_rounds']}")
    if cfg.get("instrumentation_allowed"):
        bits.append("allowed " + ", ".join(f"`{a}`" for a in cfg["instrumentation_allowed"]))
    return "Config: " + " · ".join(bits)


def _tally(report: dict[str, Any], out: list[str]) -> None:
    s = report["summary"]
    out.append("")
    sig = s["signals"]
    tally = (f"**{sig.get('warn', 0)} warn · {sig.get('info', 0)} info · "
             f"{sig.get('ok', 0)} ok")
    if sig.get('skip'):
        tally += f" · {sig['skip']} not checked"
    out.append(tally + f"** — {s['echolot_calls']} echolot call(s), "
               f"{s['hunts']} subagent run(s), {s['asks']} question(s) to the human")
    out.append("")

    # What the source could not show, before anything it did. Read in the
    # other order, a short report looks like a clean one.
    for note in (report.get("source") or {}).get("notes") or []:
        out.append(f"> {note}")
    if (report.get("source") or {}).get("notes"):
        out.append("")


def _signals(report: dict[str, Any], out: list[str]) -> None:
    """Warn and info in full."""
    loud = [x for x in report["signals"] if x["severity"] in ("warn", "info")]
    if not loud:
        return
    out.append("## Signals")
    out.append("")
    for x in loud:
        out.append(f"### {_MARK[x['severity']]} {x['title']}")
        out.append(f"_{x['why']}_")
        out.append("")
        if x["rows"]:
            out.append(_table(x["rows"]))
            out.append("")
        if x.get("hint"):
            out.append(f"> {x['hint']}")
            out.append("")
        out.append(f"<sub>signal `{x['id']}`</sub>")
        out.append("")


def _passed(report: dict[str, Any], out: list[str]) -> None:
    """Ok as a checklist."""
    quiet = [x for x in report["signals"] if x["severity"] == "ok"]
    if not quiet:
        return
    out.append("## Protocol checks passed")
    out.append("")
    for x in quiet:
        out.append(f"- ✓ **{x['title']}** — {x['why']}")
    out.append("")


def _not_checked(report: dict[str, Any], out: list[str]) -> None:
    skipped = [x for x in report["signals"] if x["severity"] == "skip"]
    if not skipped:
        return
    out.append("## Not checked")
    out.append("")
    # Grouped by why, since the reasons differ: a source with echolot's
    # calls alone, a brief Codex keeps encrypted, a session that was
    # building the tool. One sentence over all of them said the first
    # reason of every check, the brief's included.
    by_why: dict[str, list[str]] = {}
    for x in skipped:
        by_why.setdefault(x.get("why") or "", []).append(x["id"])
    for why, ids in by_why.items():
        if why:
            out.append(f"_{why}_")
            out.append("")
        out.append(", ".join(f"`{i}`" for i in ids))
        out.append("")


def _entry(report: dict[str, Any], out: list[str]) -> None:
    e = report.get("entry") or {}
    out.append("## Entry")
    out.append("")
    if e.get("seconds_to_first_call") is not None:
        out.append(f"First echolot call after **{_dur(e['seconds_to_first_call'])}**; "
                   f"{e.get('slash_before_first_call', 0)} slash command(s) before it; "
                   f"{e.get('interruptions', 0)} interruption(s).")
    if e.get("skills_loaded"):
        out.append("Skills loaded: " + ", ".join(f"`{k}`" for k in e["skills_loaded"]))
    if e.get("slash_commands"):
        out.append("")
        out.append(_table([{"time": _t(x["ts"]), "command": x["command"],
                            "args": x.get("args") or ""} for x in e["slash_commands"]]))
    if e.get("user_prompts"):
        out.append("")
        out.append("User prompts (main context, truncated):")
        out.append("")
        for p in e["user_prompts"]:
            out.append(f"- `{_t(p['ts'])}` {_oneline(p['text'], 200)}")
    out.append("")


def _timeline(report: dict[str, Any], out: list[str]) -> None:
    if not report.get("timeline"):
        return
    out.append("## Timeline")
    out.append("")
    out.append(_table([{"time": _t(m["ts"]), "agent": m["agent"],
                        "milestone": m["label"], "detail": _oneline(m["detail"], 80)}
                       for m in report["timeline"]]))
    out.append("")


def _echolot_calls(report: dict[str, Any], out: list[str]) -> None:
    calls = report.get("echolot_calls") or []
    out.append("## echolot calls")
    out.append("")
    if not calls:
        out.append("_none_")
        out.append("")
        return
    out.append(_table([_call_row(c) for c in calls]))
    by_sub: dict[str, int] = {}
    for c in calls:
        by_sub[c["sub"]] = by_sub.get(c["sub"], 0) + 1
    out.append("")
    out.append("By subcommand: " + ", ".join(f"{k} {v}" for k, v in sorted(by_sub.items())))
    out.append("")


def _call_row(c: dict[str, Any]) -> dict[str, Any]:
    dur = c.get("duration_s")
    if dur is None:
        dur_cell: Any = "—"
    elif c.get("shared", 1) > 1:
        dur_cell = f"≤{dur}"    # the line's time, not this call's
    else:
        dur_cell = dur
    return {
        "time": _t(c["ts"]), "agent": c["agent"],
        "command": f"echolot {c['sub']} {c['argv']}"[:90],
        "exit": "—" if c.get("exit") is None else c["exit"],
        "s": dur_cell,
        "out": c.get("output_chars", 0),
        "note": ", ".join(_call_notes(c)),
    }


def _call_notes(c: dict[str, Any]) -> list[str]:
    """What the table's note cell says about one call, in its order."""
    note = []
    if c.get("traceback"):
        note.append("traceback")
    if c.get("is_help"):
        note.append("help")
    if c.get("config"):
        note.append(f"-c {c['config'][-40:]}")
    facts = (c.get("recorded") or {}).get("facts") or {}
    if "fired" in facts:
        note.append(f"fired {len(facts['fired'])}")
    if facts.get("failed"):
        note.append(f"doctor failed {len(facts['failed'])}")
    if not c.get("ran", True):
        note.append("shell skipped it")
    elif c.get("shared", 1) > 1:
        note.append(f"{c['shared']} calls in one line")
    return note


def _questions(report: dict[str, Any], out: list[str]) -> None:
    qs = report.get("questions") or []
    if not qs:
        return
    out.append("## Questions to the human")
    out.append("")
    out.append(_table([{
        "time": _t(q["ts"]), "question": _oneline(q["question"], 90),
        "options": len(q.get("options") or []),
        "recommended": _oneline(q.get("recommended") or "—", 40),
        "chosen": _oneline(q.get("chosen") or "—", 40),
        "answered after": _dur(q.get("answered_after_s")),
    } for q in qs]))
    chosen_rec = sum(1 for q in qs if q.get("recommended") and q.get("chosen")
                     and q["chosen"] == q["recommended"])
    with_rec = sum(1 for q in qs if q.get("recommended") and q.get("chosen"))
    if with_rec:
        out.append("")
        out.append(f"Recommended option taken {chosen_rec} of {with_rec} time(s).")
    out.append("")


def _conclusion(report: dict[str, Any], out: list[str]) -> None:
    concl = report.get("conclusion") or {}
    if not concl.get("text"):
        return
    out.append("## What the main context concluded")
    out.append("")
    out.append("_its last message — for a session without a hunt, the whole result_")
    out.append("")
    for line in concl["text"][:1800].splitlines():
        out.append(f"> {line}")
    out.append("")


def _hunts(report: dict[str, Any], out: list[str]) -> None:
    for h in report.get("hunts") or []:
        _hunt(h, out)


def _hunt(h: dict[str, Any], out: list[str]) -> None:
    out.append(f"## Subagent `{h.get('type') or '?'}` — {h.get('description') or h['id']}")
    out.append("")
    u = h.get("usage") or {}
    facts = [
        f"duration **{_dur(h.get('duration_s'))}**",
        f"rounds **{h['rounds']}**" + (f" of {h['max_rounds']}" if h.get("max_rounds") else " (max_rounds not set, default 3)"),
        f"re-records {h['re_records']}",
        f"analyze calls {h['analyze_calls']}",
        "tools " + ", ".join(f"{k} {v}" for k, v in sorted(h["tools"].items(), key=lambda kv: -kv[1])),
        f"tokens out {u.get('output', 0):,} · in {u.get('input', 0):,} · cache read {u.get('cache_read', 0):,}",
        f"thinking blocks {h.get('thinking_blocks', 0)}",
    ]
    mix = _mix_line(h.get("window") or {})
    if mix:
        facts.append(mix)
    for line in facts:
        out.append(f"- {line}")
    pm = h.get("prompt_mentions") or {}
    out.append("- prompt ({} chars) mentions: {}".format(
        h.get("prompt_chars", 0),
        ", ".join(f"{k} {'✓' if v else '✗'}" for k, v in pm.items())))
    cf = h.get("conclusion_fields") or {}
    out.append("- conclusion fields: " + ", ".join(
        f"{k} {'✓' if v else '✗'}" for k, v in cf.items()) +
        (f" · confidence: {h['confidence']}" if h.get("confidence") else ""))
    if not h.get("has_transcript"):
        out.append("- _no separate transcript found for this subagent; "
                   "tool counts and tokens are unavailable_")
    if h.get("final_text"):
        out.append("")
        out.append("Returned upward:")
        out.append("")
        for line in h["final_text"][:1800].splitlines():
            out.append(f"> {line}")
    out.append("")


def _instrumentation(report: dict[str, Any], out: list[str]) -> None:
    inst = report.get("instrumentation") or {}
    if not inst.get("files"):
        return
    out.append("## Temporary instrumentation")
    out.append("")
    out.append(_table([{"file": f, "added": v["added"], "removed": v["removed"],
                        "shell edits": v.get("shell", 0)}
                       for f, v in inst["files"].items()]))
    out.append("")
    verdict = inst.get("cleanup_grep_clean")
    found = ("found nothing" if verdict is True else
             "still found the prefix" if verdict is False else "result unclear")
    out.append(f"Prefix `{inst.get('prefix')}` · grep for it after the last edit: "
               f"{inst.get('cleanup_grep_after_last_edit', 0)}"
               + (f" ({found})" if inst.get('cleanup_grep_after_last_edit') else "")
               + f" · grep calls in total: {inst.get('grep_calls_total', 0)}")
    if inst.get("shell_edits"):
        out.append(f"{inst['shell_edits']} edit(s) went through the shell (python, sed) — "
                   f"no add/remove direction to balance; the grep verdict stands for them.")
    tree = inst.get("tree") or {}
    if tree.get("checked"):
        left = tree.get("files") or []
        out.append("The tree when this report was made: "
                   + (f"{len(left)} file(s) still carry the prefix — "
                      + ", ".join(f"`{p}`" for p in left[:6]) if left
                      else "no file carries the prefix."))
    out.append("")


def _cost(report: dict[str, Any], out: list[str]) -> None:
    c = report.get("cost") or {}
    out.append("## Cost")
    out.append("")
    um, us = c.get("usage_main") or {}, c.get("usage_subagents") or {}
    out.append(f"- wall time **{_dur(c.get('duration_s'))}**; user turns {c.get('user_turns', 0)}; "
               f"questions {c.get('asks', 0)}")
    out.append(f"- main context: out {um.get('output', 0):,} · in {um.get('input', 0):,} · "
               f"cache read {um.get('cache_read', 0):,} · {um.get('messages', 0)} responses · "
               f"thinking blocks {c.get('thinking_blocks_main', 0)}")
    if us.get("messages"):
        out.append(f"- subagents: out {us.get('output', 0):,} · in {us.get('input', 0):,} · "
                   f"cache read {us.get('cache_read', 0):,} · {us.get('messages', 0)} responses")
    if c.get("tools_main"):
        out.append("- tools, main: " + ", ".join(f"{k} {v}" for k, v in c["tools_main"].items()))
    if c.get("tools_subagents"):
        out.append("- tools, subagents: " + ", ".join(f"{k} {v}" for k, v in c["tools_subagents"].items()))
    out.append(f"- tool output in total: {c.get('tool_output_chars', 0):,} chars")
    mix = _mix_line(c.get("window_main") or {})
    if mix:
        out.append(f"- main {mix}")
    if c.get("top_outputs"):
        out.append("")
        out.append("Largest tool outputs:")
        out.append("")
        out.append(_table([{"chars": o["chars"], "tool": o["tool"], "agent": o["agent"],
                            "what": _oneline(o["what"], 80)} for o in c["top_outputs"]]))
    if c.get("gaps"):
        out.append("")
        out.append("Longest silences:")
        out.append("")
        out.append(_table([{"agent": g["agent"], "seconds": g["seconds"],
                            "after": _oneline(g["after"], 80)} for g in c["gaps"]]))
    out.append("")


def _recorder(report: dict[str, Any], out: list[str]) -> None:
    runs = report.get("runs_recorded") or []
    out.append("## Recorder (`.echolot/log/runs.jsonl`)")
    out.append("")
    if runs:
        out.append(_table([{
            "time": _t(r.get("ts", "")), "cmd": r.get("cmd"), "exit": r.get("exit"),
            "ms": r.get("ms"), "config": ((r.get("config") or {}).get("sha") or "—"),
            "facts": _oneline(json.dumps(r.get("facts") or {}, ensure_ascii=False), 70),
            "error": "yes" if r.get("error") else "",
        } for r in runs]))
    else:
        out.append("_no recorded runs inside this session's window — the recorder "
                   "was not there yet, or the session ran from another directory_")
    out.append("")


def _notes(report: dict[str, Any], out: list[str]) -> None:
    if not report.get("notes"):
        return
    out.append("## Reader notes")
    out.append("")
    for n in report["notes"]:
        out.append(f"- {n}")
    out.append("")


def _sources(report: dict[str, Any], out: list[str]) -> None:
    out.append("<sub>sources: " + ", ".join(f"`{f}`" for f in report["source"].get("files", []))
               + "</sub>")


_SECTIONS = (_head, _tally, _signals, _passed, _not_checked, _entry, _timeline,
             _echolot_calls, _questions, _conclusion, _hunts, _instrumentation,
             _cost, _recorder, _notes, _sources)


# ------------------------------------------------------------------ helpers

def _table(rows: list[dict[str, Any]]) -> str:
    # A reflect row carries a command line, and a command line carries pipes.
    # This used to render them raw, which broke the table exactly where the
    # evidence was.
    return table.render(rows, cell=_fmt)


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v).replace("|", "\\|").replace("\n", " ")


def _oneline(text: Any, limit: int) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[:limit] + "…"


def _t(ts: str | None) -> str:
    return ts[11:19] if ts and len(ts) >= 19 else (ts or "")


def _mix_line(window: dict[str, Any]) -> str | None:
    """`window fed by: source reading 21 calls · 59k chars (57%) · echolot …`

    Chars of tool output per activity — exact; tokens are roughly a quarter
    of that, said once at the end rather than pretended per bucket.
    """
    by = window.get("by_activity") or {}
    if not by:
        return None
    parts = []
    for name, v in sorted(by.items(), key=lambda kv: -kv[1]["chars"]):
        parts.append(f"{name} {v['calls']} calls · {v['chars'] / 1000:.0f}k chars ({v['share']}%)")
    total = window.get("total_chars", 0)
    return ("window fed by: " + " · ".join(parts)
            + f" — {total / 1000:.0f}k chars ≈ {total / 4000:.0f}k tokens of tool output")


def _dur(seconds: Any) -> str:
    if seconds is None:
        return "—"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


def _span(started: str | None, ended: str | None, duration_s: Any) -> str:
    if not started:
        return ""
    day = started[:10]
    line = f"{day} {_t(started)} → {_t(ended)} UTC"
    if duration_s is not None:
        line += f" ({_dur(duration_s)})"
    return line
