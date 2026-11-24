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
from . import claude_code, codex
from . import facts as facts_mod
from . import from_log
from . import render as reflect_render
from . import signals as signals_mod

def cmd_reflect(args) -> int:
    """The Marker Report over an agent session instead of a trace.

    Reads the agent's transcript plus the tool's own `runs.jsonl`, compresses
    them into facts and signals, and writes `.echolot/reflect/<session>.md`
    and `.json`. Run it from the application project the agent worked in —
    that is where Claude Code keys the transcript, where Codex records the
    session's working directory, and where echolot.yml and the recorder log
    live.

    Claude Code's sessions and Codex's are read side by side, newest first:
    `--last` is the newest of either that used echolot (#193).

    With no transcript to read — any other client, or a run from a plain
    shell or from CI — it falls back to the recorder log alone. That report
    is smaller and says so: see `from_log.py`.
    """
    # The project `main` resolved: `--project`, else the one this folder is
    # in. From `app/`, reflect read `app/.echolot/log` and wrote its report
    # under `app/` (#835).
    from ..project import resolve
    project = (getattr(args, "where", None) or resolve(args)).folder
    # (reader, ref), newest first, whichever agent wrote the session.
    found: list = []
    tdir = None
    if not args.from_log:
        tdir = (Path(args.transcripts).expanduser() if args.transcripts
                else claude_code.project_dir(project))
        if tdir is not None and not tdir.is_dir():
            tdir = None
        if tdir is None and args.transcripts:
            print(f"error: no transcripts at {args.transcripts}", file=sys.stderr)
            return 2
        if tdir is not None:
            found += [(claude_code, r) for r in claude_code.list_sessions(tdir)]
        # `--transcripts` names a Claude Code folder, and asks for that alone.
        if not args.transcripts:
            found += [(codex, r) for r in codex.list_sessions(project)]
    found.sort(key=lambda f: f[1].mtime, reverse=True)

    where = "this project's Claude Code and Codex sessions"
    if not found:
        log = from_log.log_path(project)
        if not log.exists():
            looked = claude_code.PROJECTS_ROOT / claude_code.slug_candidates(project)[0]
            print(f"error: nothing to reflect on for {project}\n"
                  f"  no Claude Code transcripts:  {looked}\n"
                  f"  no Codex sessions here:      {codex.sessions_root()}\n"
                  f"  and no run log:              {log}\n"
                  f"  Run it from the project the agent worked in. The log "
                  f"appears the first time any echolot command runs there.",
                  file=sys.stderr)
            return 2
        if not args.from_log:
            print(f"[i] no agent transcript for this project — reading "
                  f"{recorder.LOG_FILE} instead. Fewer checks; the report "
                  f"lists which.", file=sys.stderr)
        where = str(from_log.log_path(project))
        found = [(from_log, r) for r in from_log.list_sessions(project)]
    only_log = all(reader is from_log for reader, _ in found)

    try:
        since = _parse_since(args.since) if args.since else None
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if since is not None:
        found = [(reader, r) for reader, r in found if r.mtime >= since]
    if args.session:
        found = [(reader, r) for reader, r in found if r.id.startswith(args.session)]
        if not found:
            # The log's units are not sessions, so an id from Claude Code
            # cannot match one and never will. "No session starting with X"
            # is true and sends the reader to check the id they copied
            # correctly — the actual answer is that they are asking the wrong
            # source for it.
            if only_log:
                ids = any(r.get("session") for r in recorder.read(from_log.log_path(project)))
                why = ("Its lines that carry a session id are read under that "
                       "id, and none starts with it." if ids else
                       "None of its lines carries a session id: an older "
                       "echolot wrote them, or a host that gives its commands "
                       "none — Claude Code and Codex do, and ECHOLOT_SESSION "
                       "names one for anybody else.")
                print(f"error: '{args.session}' names no session in "
                      f"{recorder.LOG_FILE}.\n"
                      f"  {why} A line without one is cut into sittings "
                      f"wherever the tool was left alone for a while, each "
                      f"named by a hash of its own — so an id from an agent "
                      f"transcript will never match one, however it is "
                      f"spelled.\n"
                      f"  `echolot reflect --from-log --list` names what this "
                      f"log has.",
                      file=sys.stderr)
            else:
                print(f"error: no session starting with '{args.session}'\n"
                      f"  `echolot reflect --list` names the sessions this "
                      f"project has.", file=sys.stderr)
            return 2

    def pick(found):
        picked = []
        for reader, ref in found:
            session = (from_log.read_session(ref) if reader is from_log
                       else reader.read_session(ref.path))
            # An explicit id is taken as is; otherwise only sessions that used
            # the tool for real work count — a session that merely ran
            # `reflect` is not worth reflecting on.
            if not args.session and not reader.involves_echolot(session):
                continue
            picked.append((reader, ref, session))
            if not (args.all or args.list or args.session or since is not None):
                break   # --last: the newest one is enough
        return picked

    picked = pick(found)
    # Transcripts here, and none of them used echolot: an old session that
    # edited the README while the hunt ran from Cursor. The log is the rest
    # of what happened here, and the docs promise the fall-back; it used to
    # come only when there were no transcripts at all.
    if not picked and not args.session and not only_log \
            and from_log.log_path(project).exists():
        logged = [(from_log, r) for r in from_log.list_sessions(project)
                  if since is None or r.mtime >= since]
        picked = pick(logged)
        if picked:
            found = logged
            print(f"[i] no agent session here used echolot — reading "
                  f"{recorder.LOG_FILE} instead. Fewer checks; the report "
                  f"lists which.", file=sys.stderr)
            where, only_log = str(from_log.log_path(project)), True

    if not picked:
        print(f"nothing to reflect on: nothing in {where} used echolot"
              + (f" since {args.since}" if args.since else ""), file=sys.stderr)
        return 1

    # Named by the shortest prefix, eight at least, that no other session here
    # shares — among the ones the picked came from, which are the log's
    # sittings when the transcripts had nothing to say. Codex thread ids are UUIDv7, and their first eight characters
    # are a timestamp that holds for a minute: two threads started within it
    # wrote one report file, and the second replaced the first.
    short = _short_ids([r.id for _, r in found])
    if args.list:
        # A log whose lines carry no session id has no sessions in it, so the
        # column that names its rows must not say "session" — this listing is
        # where somebody comes after being told an id did not match, and
        # telling them the same wrong word again is how the misunderstanding
        # survives being corrected. Lines that carry one are sessions.
        unit = ("sitting" if only_log and not any(getattr(r, "named", False)
                                                   for _, r, _ in picked)
                else "session")
        print(f"{unit:10} {'agent':11} {'started (UTC)':17} {'dur':>7} "
              f"{'echolot':>7} {'hunt':>4}  first prompt")
        for reader, ref, s in picked:
            subs = reader.echolot_subcommands(s)
            hunts = len(facts_mod.hunt_agents(s))
            first = next((t.text for t in s.turns if t.role == "user" and t.kind == "text"), "")
            dur = s.duration_s()
            print(f"{short[ref.id]:10} {s.agent:11} "
                  f"{(s.started or '')[:16].replace('T', ' '):17} "
                  f"{_fmt_dur(dur):>7} {len(subs):>7} {hunts:>4}  "
                  f"{' '.join(first.split())[:60]}")
        return 0

    # An explicit -c from here, as every other command and `--local` read it;
    # only the default is the project's. Joined to --project, `-c
    # app/echolot.yml` looked for app/app/echolot.yml, and a path that was
    # not there was dropped without a word.
    given = args.config != "echolot.yml"
    loaded: dict[Path, Config | None] = {}

    def load(path: Path) -> Config | None:
        if path not in loaded:
            loaded[path] = None
            if path.exists() or given:
                try:
                    loaded[path] = Config.load(path, args.local)
                except ConfigError as e:
                    print(f"config ignored: {e}", file=sys.stderr)
        return loaded[path]

    runs = recorder.read(project / recorder.LOG_FILE)

    # The same rule as -c above: an explicit -o is a path from here, as every
    # other command reads one; only the default belongs to the project.
    # Joined to --project, `-o r-out` run from a folder of one's own wrote an
    # untracked directory into the analysed app's checkout (#548).
    out_dir = Path(args.out) if args.out else project / ".echolot" / "reflect"
    out_dir.mkdir(parents=True, exist_ok=True)

    reports = []
    written = []
    for _reader, ref, session in picked:
        # The project's log, and the logs of the directories this session's
        # own commands wrote to: a `-c work-a/echolot.yml` logged its runs
        # under work-a/ (#543). Another session's lines in any of them are
        # left out by the id they carry.
        own = runs + [r for log in facts_mod.run_logs(session, project)
                      for r in recorder.read(log)]
        cfg_path, source, logged = (
            (Path(args.config), "given", []) if given
            else _session_config(session, own, project))
        cfg = load(cfg_path)
        facts = facts_mod.gather(session, cfg, own, project)
        if cfg is not None:
            now = (recorder._config_stamp(str(cfg_path)) or {}).get("sha")
            facts.config.update(source=source, logged_sha=logged, sha_now=now,
                                changed=bool(logged and now and now not in logged))
        sigs = signals_mod.run(session, facts, cfg)
        rep = reflect_render.build(session, facts, sigs)
        stem = short[ref.id]
        (out_dir / f"{stem}.json").write_text(
            reflect_render.to_json(rep), encoding="utf-8")
        (out_dir / f"{stem}.md").write_text(
            reflect_render.to_markdown(rep), encoding="utf-8")
        written += [out_dir / f"{stem}.md", out_dir / f"{stem}.json"]
        reports.append(rep)

    recorder.note(sessions=len(reports),
                  warn=sum(r["summary"]["signals"].get("warn", 0) for r in reports))

    if len(reports) == 1 and (getattr(args, "full", False) or sys.stdout.isatty()):
        print(reflect_render.to_markdown(reports[0]))
    elif len(reports) == 1:
        # The rule `analyze` follows (#492): the whole report is for a person
        # at a terminal, 15 to 36 KB of it for one session, and the agent the
        # layer runs reads `<id>.json` after it anyway. Outside a terminal it
        # gets the row `--all` prints for the session, and where the rest is.
        print(_reflect_summary(reports, short))
        print("\nThe summary, because the output is not a terminal: the whole "
              "report is the .md beside the .json, and `reflect --full` prints it.")
    else:
        summary = _reflect_summary(reports, short)
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


def _session_config(session, runs: list[dict], project: Path
                    ) -> tuple[Path, str, list[str]]:
    """The config one session worked with, where it was, and the hashes its
    runs logged for it.

    The session's own: when every command of it that reads a config read
    one copy (`-c work-a/echolot.yml`), the checks are judged by that copy.
    Read off the project instead, a session that never touched the project's
    config was shown it, with a scenario another session added while this
    one worked on its copies (#544). Several, or none, and it is the
    project's, as before.
    """
    own = project / "echolot.yml"
    used = facts_mod.session_configs(session, runs, project)
    if len(used) == 1 and Path(used[0]["path"]).is_file() \
            and Path(used[0]["path"]) != own.resolve():
        return Path(used[0]["path"]), "session", used[0]["shas"]
    shas = next((u["shas"] for u in used if Path(u["path"]) == own.resolve()), [])
    return own, "project", shas


def _parse_since(text: str) -> float:
    """`2h`, `30m`, `3d` → epoch seconds of the cut-off.

    A `ConfigError` like every other refusal of something the user typed. It
    used to raise `SystemExit`, which is the one exit path in the CLI that
    goes round the command's own `return 2` — and `main` re-raises it, so it
    came out through the recorder's `except BaseException` and was filed as a
    run that crashed rather than one that said no.
    """
    m = re.fullmatch(r"(\d+)\s*([mhd])", text.strip())
    if not m:
        raise ConfigError(f"--since expects e.g. 2h, 30m, 3d; got '{text}'")
    n, unit = int(m.group(1)), m.group(2)
    return time.time() - n * {"m": 60, "h": 3600, "d": 86400}[unit]


def _fmt_dur(seconds) -> str:
    if seconds is None:
        return "—"
    s = int(seconds)
    return f"{s // 60}m" if s < 3600 else f"{s // 3600}h{(s % 3600) // 60:02d}"


def _short_ids(ids: list[str], least: int = 8) -> dict[str, str]:
    """Each id by the shortest prefix, `least` long at least, unique among them."""
    out = {}
    for i in ids:
        n = least
        while n < len(i) and any(o != i and o.startswith(i[:n]) for o in ids):
            n += 1
        out[i] = i[:n]
    return out


def _reflect_summary(reports: list[dict], short: dict[str, str] | None = None) -> str:
    """Several sessions on one page: a row each, then how often each signal fires."""
    out = ["# Reflect Summary", "",
           f"{len(reports)} session(s), newest first.", ""]
    short = short or {}
    rows = []
    freq: dict[str, list[int]] = {}
    for r in reports:
        s = r["summary"]
        hunts = r.get("hunts") or []
        rows.append({
            "session": short.get(r["source"]["session"], r["source"]["session"][:8]),
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
