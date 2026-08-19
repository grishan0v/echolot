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

_MARK = {"warn": "⚠", "info": "ℹ", "ok": "✓"}


def build(session: Session, facts: Facts, signals: list[Signal]) -> dict[str, Any]:
    by_sev: dict[str, int] = {"warn": 0, "info": 0, "ok": 0}
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
        },
        "context": {
            "started": session.started,
            "ended": session.ended,
            "duration_s": facts.cost.get("duration_s"),
            "config": facts.config,
        },
        "summary": {
            "signals": by_sev,
            "warn_ids": [s.id for s in signals if s.severity == "warn"],
            "info_ids": [s.id for s in signals if s.severity == "info"],
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
        "instrumentation": facts.instrumentation,
        "cost": {**facts.cost, "top_outputs": facts.top_outputs, "gaps": facts.gaps},
        "runs_recorded": facts.runs,
        "notes": session.notes,
    }


def to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- markdown

def to_markdown(report: dict[str, Any]) -> str:
    out: list[str] = []
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
    cfg = ctx.get("config") or {}
    if cfg.get("present"):
        bits = [f"`{cfg.get('path')}`"]
        if cfg.get("scenario"):
            bits.append(f"scenario `{cfg['scenario']}`")
        if cfg.get("max_rounds") is not None:
            bits.append(f"max_rounds {cfg['max_rounds']}")
        if cfg.get("instrumentation_allowed"):
            bits.append("allowed " + ", ".join(f"`{a}`" for a in cfg["instrumentation_allowed"]))
        out.append("Config: " + " · ".join(bits))
    else:
        out.append("Config: _none found in this directory — protocol checks that "
                   "need it were skipped_")
    s = report["summary"]
    out.append("")
    out.append(f"**{s['signals'].get('warn', 0)} warn · {s['signals'].get('info', 0)} info · "
               f"{s['signals'].get('ok', 0)} ok** — {s['echolot_calls']} echolot call(s), "
               f"{s['hunts']} subagent run(s), {s['asks']} question(s) to the human")
    out.append("")

    # ---- signals: warn and info in full, ok as a checklist
    loud = [x for x in report["signals"] if x["severity"] in ("warn", "info")]
    quiet = [x for x in report["signals"] if x["severity"] == "ok"]
    if loud:
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
    if quiet:
        out.append("## Protocol checks passed")
        out.append("")
        for x in quiet:
            out.append(f"- ✓ **{x['title']}** — {x['why']}")
        out.append("")

    # ---- entry
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

    # ---- timeline
    if report.get("timeline"):
        out.append("## Timeline")
        out.append("")
        out.append(_table([{"time": _t(m["ts"]), "agent": m["agent"],
                            "milestone": m["label"], "detail": _oneline(m["detail"], 80)}
                           for m in report["timeline"]]))
        out.append("")

    # ---- echolot calls
    calls = report.get("echolot_calls") or []
    out.append("## echolot calls")
    out.append("")
    if calls:
        rows = []
        for c in calls:
            note = []
            if c.get("traceback"):
                note.append("traceback")
            if c.get("is_help"):
                note.append("help")
            if c.get("config"):
                note.append(f"-c {c['config'][-40:]}")
            if c.get("recorded"):
                r = c["recorded"]
                facts = r.get("facts") or {}
                if "fired" in facts:
                    note.append(f"fired {len(facts['fired'])}")
                if facts.get("failed"):
                    note.append(f"doctor failed {len(facts['failed'])}")
            if not c.get("ran", True):
                note.append("shell skipped it")
            elif c.get("shared", 1) > 1:
                note.append(f"{c['shared']} calls in one line")
            dur = c.get("duration_s")
            if dur is None:
                dur_cell: Any = "—"
            elif c.get("shared", 1) > 1:
                dur_cell = f"≤{dur}"    # the line's time, not this call's
            else:
                dur_cell = dur
            rows.append({
                "time": _t(c["ts"]), "agent": c["agent"],
                "command": f"echolot {c['sub']} {c['argv']}"[:90],
                "exit": "—" if c.get("exit") is None else c["exit"],
                "s": dur_cell,
                "out": c.get("output_chars", 0),
                "note": ", ".join(note),
            })
        out.append(_table(rows))
        by_sub: dict[str, int] = {}
        for c in calls:
            by_sub[c["sub"]] = by_sub.get(c["sub"], 0) + 1
        out.append("")
        out.append("By subcommand: " + ", ".join(f"{k} {v}" for k, v in sorted(by_sub.items())))
    else:
        out.append("_none_")
    out.append("")

    # ---- questions
    qs = report.get("questions") or []
    if qs:
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

    # ---- hunts
    for h in report.get("hunts") or []:
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

    # ---- instrumentation
    inst = report.get("instrumentation") or {}
    if inst.get("files"):
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
        out.append("")

    # ---- cost
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

    # ---- recorder
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

    if report.get("notes"):
        out.append("## Reader notes")
        out.append("")
        for n in report["notes"]:
            out.append(f"- {n}")
        out.append("")

    out.append("<sub>sources: " + ", ".join(f"`{f}`" for f in src.get("files", [])) + "</sub>")
    return "\n".join(out)


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
    return (f"window fed by: " + " · ".join(parts)
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
