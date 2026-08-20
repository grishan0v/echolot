"""`echolot reflect` — the command half, next to the rest of reflect.

Everything below the CLI already lives in this package: the reader that turns
one agent's on-disk transcript into the normalised session, the signals over
it, and the two renderers. Only the argument handling and the across-sessions
summary sat in main.py, which meant a change to reflect touched two files at
opposite ends of the codebase for no reason but history.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .. import recorder, table
from ..config import Config, ConfigError
from . import claude_code
from . import facts as facts_mod
from . import from_log
from . import render as reflect_render
from . import signals as signals_mod

def cmd_reflect(args) -> int:
    """The Marker Report over an agent session instead of a trace.

    Reads the agent's transcript plus the tool's own `runs.jsonl`, compresses
    them into facts and signals, and writes `.echolot/reflect/<session>.md`
    and `.json`. Run it from the application project the agent worked in —
    that is where Claude Code keys the transcript, and where echolot.yml and
    the recorder log live.

    With no transcript to read — any client but Claude Code, or a run from a
    plain shell or from CI — it falls back to the recorder log alone. That
    report is smaller and says so: see `from_log.py`.
    """
    project = Path(args.project or ".").resolve()
    tdir = None
    if not args.from_log:
        tdir = (Path(args.transcripts).expanduser() if args.transcripts
                else claude_code.project_dir(project))
        if tdir is not None and not tdir.is_dir():
            tdir = None
        if tdir is None and args.transcripts:
            print(f"error: no transcripts at {args.transcripts}", file=sys.stderr)
            return 2

    reader, source = ((claude_code, tdir) if tdir is not None
                      else (from_log, project))
    if reader is from_log:
        log = from_log.log_path(project)
        if not log.exists():
            looked = claude_code.PROJECTS_ROOT / claude_code.slug_candidates(project)[0]
            print(f"error: nothing to reflect on for {project}\n"
                  f"  no Claude Code transcripts:  {looked}\n"
                  f"  and no run log:              {log}\n"
                  f"  Run it from the project the agent worked in. The log "
                  f"appears the first time any echolot command runs there.",
                  file=sys.stderr)
            return 2
        if not args.from_log:
            print(f"[i] no agent transcript for this project — reading "
                  f"{recorder.LOG_FILE} instead. Fewer checks; the report "
                  f"lists which.", file=sys.stderr)

    since = _parse_since(args.since) if args.since else None
    refs = reader.list_sessions(source)
    if since is not None:
        refs = [r for r in refs if r.mtime >= since]
    if args.session:
        refs = [r for r in refs if r.id.startswith(args.session)]
        if not refs:
            print(f"error: no session starting with '{args.session}'",
                  file=sys.stderr)
            return 2

    picked = []
    for ref in refs:
        session = (claude_code.read_session(ref.path) if reader is claude_code
                   else from_log.read_session(ref))
        # An explicit id is taken as is; otherwise only sessions that used the
        # tool for real work count — a session that merely ran `reflect` is
        # not worth reflecting on.
        if not args.session and not reader.involves_echolot(session):
            continue
        picked.append((ref, session))
        if not (args.all or args.list or args.session or since is not None):
            break   # --last: the newest one is enough

    if not picked:
        print(f"nothing to reflect on: nothing under {source} used echolot"
              + (f" since {args.since}" if args.since else ""), file=sys.stderr)
        return 1

    if args.list:
        print(f"{'session':10} {'started (UTC)':17} {'dur':>7} {'echolot':>7} "
              f"{'hunt':>4}  first prompt")
        for ref, s in picked:
            subs = reader.echolot_subcommands(s)
            hunts = sum(1 for a in s.subagents if a.type == "perf-hunter")
            first = next((t.text for t in s.turns if t.role == "user" and t.kind == "text"), "")
            dur = s.duration_s()
            print(f"{ref.id[:8]:10} {(s.started or '')[:16].replace('T', ' '):17} "
                  f"{_fmt_dur(dur):>7} {len(subs):>7} {hunts:>4}  "
                  f"{' '.join(first.split())[:60]}")
        return 0

    cfg = None
    cfg_path = Path(args.config)
    if not cfg_path.is_absolute() and project != Path.cwd():
        cfg_path = project / cfg_path
    if cfg_path.exists():
        try:
            cfg = Config.load(cfg_path, args.local)
        except ConfigError as e:
            print(f"config ignored: {e}", file=sys.stderr)
    runs = recorder.read(project / recorder.LOG_FILE)

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = project / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    reports = []
    written = []
    for ref, session in picked:
        facts = facts_mod.gather(session, cfg, runs)
        sigs = signals_mod.run(session, facts, cfg)
        rep = reflect_render.build(session, facts, sigs)
        stem = ref.id[:8]
        (out_dir / f"{stem}.json").write_text(
            reflect_render.to_json(rep), encoding="utf-8")
        (out_dir / f"{stem}.md").write_text(
            reflect_render.to_markdown(rep), encoding="utf-8")
        written += [out_dir / f"{stem}.md", out_dir / f"{stem}.json"]
        reports.append(rep)

    recorder.note(sessions=len(reports),
                  warn=sum(r["summary"]["signals"].get("warn", 0) for r in reports))

    if len(reports) == 1:
        print(reflect_render.to_markdown(reports[0]))
    else:
        summary = _reflect_summary(reports)
        (out_dir / "summary.md").write_text(summary, encoding="utf-8")
        (out_dir / "summary.json").write_text(json.dumps({
            "schema": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sessions": [{
                "session": r["source"]["session"], "started": r["context"]["started"],
                "duration_s": r["context"]["duration_s"], "summary": r["summary"],
            } for r in reports],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        written += [out_dir / "summary.md", out_dir / "summary.json"]
        print(summary)

    print("\n" + "\n".join(f"→ {p}" for p in written), file=sys.stderr)
    return 0


def _parse_since(text: str) -> float:
    """`2h`, `30m`, `3d` → epoch seconds of the cut-off."""
    m = re.fullmatch(r"(\d+)\s*([mhd])", text.strip())
    if not m:
        raise SystemExit(f"error: --since expects e.g. 2h, 30m, 3d; got '{text}'")
    n, unit = int(m.group(1)), m.group(2)
    return time.time() - n * {"m": 60, "h": 3600, "d": 86400}[unit]


def _fmt_dur(seconds) -> str:
    if seconds is None:
        return "—"
    s = int(seconds)
    return f"{s // 60}m" if s < 3600 else f"{s // 3600}h{(s % 3600) // 60:02d}"


def _reflect_summary(reports: list[dict]) -> str:
    """Several sessions on one page: a row each, then how often each signal fires."""
    out = ["# Reflect Summary", "",
           f"{len(reports)} session(s), newest first.", ""]
    rows = []
    freq: dict[str, list[int]] = {}
    for r in reports:
        s = r["summary"]
        hunts = r.get("hunts") or []
        rows.append({
            "session": r["source"]["session"][:8],
            "started": (r["context"].get("started") or "")[:16].replace("T", " "),
            "dur": _fmt_dur(r["context"].get("duration_s")),
            "echolot": s["echolot_calls"],
            "hunts": len(hunts),
            "rounds": ", ".join(str(h["rounds"]) for h in hunts) or "—",
            "confidence": ", ".join(str(h.get("confidence") or "?") for h in hunts) or "—",
            "warn": s["signals"].get("warn", 0),
            "warn ids": ", ".join(s.get("warn_ids") or []),
        })
        for sig in r["signals"]:
            freq.setdefault(f"{sig['severity']} {sig['id']}", []).append(1)
    out.append(table.render(rows))
    out.append("")
    out.append("## Signals by frequency")
    out.append("")
    out.append(table.render([{"signal": k, "sessions": len(v)}
                          for k, v in sorted(freq.items(), key=lambda kv: (-len(kv[1]), kv[0]))]))
    return "\n".join(out)
