#!/usr/bin/env python3
"""Everything action.yml does that is not an `echolot` command.

Standard library only, and outside the package: it runs on a CI runner beside
the echolot the action installed, and it ships with the action, never with the
wheel. Each subcommand is one step of the action, and passes what the next
step needs through $GITHUB_OUTPUT:

    ci.py analyze   the traces the input names, then `echolot analyze`
    ci.py baseline  the report.json the last good run on a branch kept
    ci.py compare   `echolot compare` against it, when there is one
    ci.py summary   the comparison, and the report, in the job summary
    ci.py comment   the same on the pull request, one comment kept up to date

Nothing here fails a job over what `compare` found. A step fails when the input
names no trace or `analyze` cannot run. A baseline the token cannot read, or an
artifact that is no archive, is a warning; one that does not exist yet, because
no run on the branch has kept a report, is a line in the summary. The run's own
report is kept either way.
"""

from __future__ import annotations

import argparse
import glob
import io
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# How far back the lookup goes. A nightly that has failed twenty nights running
# has a bigger problem than a missing comparison.
RECENT_RUNS = 20
# GitHub cuts a comment at 65,536 characters; the job summary holds the rest.
COMMENT_LIMIT = 60_000


def output(**values: str) -> None:
    """Values for the steps after this one, read as `steps.<id>.outputs.<key>`."""
    path = os.environ.get("GITHUB_OUTPUT")
    lines = "".join(f"{key}={value}\n" for key, value in values.items())
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(lines)
    else:
        sys.stdout.write(lines)


def warn(text: str) -> None:
    print(f"::warning title=echolot::{text}")


# --- the traces -------------------------------------------------------------

def expand(patterns: str, root: Path) -> tuple[list[Path], list[str]]:
    """The files the input names, each once, in the order named; and the lines
    that named none.

    One path or glob a line, relative to `root`; `**` reaches into
    subdirectories. A directory names nothing yet: `analyze` takes the traces
    of one test, and a benchmark's output directory holds every test it has
    ever run (#154 is the directory form, with the test picked by name).
    """
    files: list[Path] = []
    missed: list[str] = []
    for line in (raw.strip() for raw in patterns.splitlines()):
        if not line:
            continue
        where = line if os.path.isabs(line) else str(root / line)
        if any(c in line for c in "*?["):
            found = sorted(Path(p) for p in glob.glob(where, recursive=True)
                           if os.path.isfile(p))
        else:
            found = [Path(where)] if os.path.isfile(where) else []
        if not found:
            missed.append(line)
        files.extend(f for f in found if f not in files)
    return files, missed


def cmd_analyze(args: argparse.Namespace) -> int:
    files, missed = expand(args.traces, Path(args.root))
    for line in missed:
        hint = (" — it is a directory: name the traces of one test in it, "
                "e.g. `<dir>/**/StartupBenchmark_startup_iter*.perfetto-trace`"
                if os.path.isdir(line if os.path.isabs(line) else Path(args.root) / line)
                else "")
        print(f"::error title=echolot::`{line}` names no trace{hint}")
    if missed or not files:
        if not missed:
            print("::error title=echolot::the `traces` input is empty")
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"{len(files)} trace(s):", *(f"  {f}" for f in files), sep="\n")
    done = subprocess.run([args.echolot, "analyze", "-c", args.config, "-o", str(out),
                           *map(str, files)], check=False)
    if done.returncode != 0:
        return done.returncode
    output(report=str(out / "report.json"))
    return 0


# --- the baseline -----------------------------------------------------------

class HTTPFailure(Exception):
    def __init__(self, code: int, reason: str) -> None:
        super().__init__(f"{code} {reason}")
        self.code = code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # An artifact's archive answers with a redirect to signed storage, and
    # urllib would carry the token there too. The redirect is followed by
    # hand, without it.
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class GitHub:
    """The few REST calls the action makes, with the job's token."""

    def __init__(self, api: str, token: str) -> None:
        self.api = api.rstrip("/")
        self.token = token
        self._opener = urllib.request.build_opener(_NoRedirect)

    def _open(self, method: str, url: str, body: bytes | None = None,
              auth: bool = True) -> tuple[int, Any, bytes]:
        headers = {"Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "echolot-action"}
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        opener = self._opener if auth else urllib.request.build_opener()
        try:
            with opener.open(req, timeout=60) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                return e.code, e.headers, b""
            raise HTTPFailure(e.code, str(e.reason)) from e
        except urllib.error.URLError as e:
            raise HTTPFailure(0, str(e.reason)) from e

    def get(self, path: str) -> Any:
        return json.loads(self._open("GET", self.api + path)[2] or b"null")

    def send(self, method: str, path: str, payload: dict) -> Any:
        body = json.dumps(payload).encode()
        return json.loads(self._open(method, self.api + path, body)[2] or b"null")

    def download(self, url: str) -> bytes:
        code, headers, body = self._open("GET", url)
        if code in (301, 302, 303, 307, 308):
            code, headers, body = self._open("GET", headers["Location"], auth=False)
        return body


@dataclass
class Found:
    run: dict
    report: bytes


def workflow_file(workflow_ref: str) -> str:
    """`nightly.yml` out of `owner/repo/.github/workflows/nightly.yml@refs/heads/main`."""
    return workflow_ref.split("@", 1)[0].rsplit("/", 1)[-1]


def find_baseline(gh: GitHub, *, repo: str, workflow: str, branch: str,
                  artifact: str, current_run: int) -> tuple[Found | None, str]:
    """The report the newest successful run of `workflow` on `branch` kept.

    A run on a pull request is passed over even when its head branch has the
    base's name, as a fork's `main` does. So is this run, and so is a run whose
    artifact has expired: the next one back may still have its own.
    """
    quoted = urllib.parse.quote(branch, safe="")
    try:
        runs = gh.get(f"/repos/{repo}/actions/workflows/{workflow}/runs"
                      f"?branch={quoted}&status=success&per_page={RECENT_RUNS}"
                      f"&exclude_pull_requests=true")["workflow_runs"]
    except HTTPFailure as e:
        # A setup error, or a server's: each is worth an annotation, which a
        # line in the summary is not.
        if e.code == 404:
            note = (f"no workflow `{workflow}` with runs this token can see "
                    f"({e}): `baseline-workflow` is a file name such as "
                    f"`nightly.yml`, and the token needs `actions: read`")
        elif e.code in (401, 403):
            note = (f"the token may not read this repository's runs ({e}); "
                    f"it needs `actions: read`")
        else:
            note = f"the runs of `{workflow}` could not be read ({e})"
        warn(note)
        return None, note
    runs = [r for r in runs if r.get("id") != current_run
            and not str(r.get("event", "")).startswith("pull_request")]
    if not runs:
        return None, f"no run of `{workflow}` on `{branch}` has succeeded yet"
    name = urllib.parse.quote(artifact, safe="")
    for run in runs:
        try:
            listed = gh.get(f"/repos/{repo}/actions/runs/{run['id']}/artifacts"
                            f"?name={name}")["artifacts"]
            kept = next((a for a in listed
                         if a.get("name") == artifact and not a.get("expired")), None)
            if kept is None:
                continue
            report = _report_in(gh.download(kept["archive_download_url"]),
                                f"run {run.get('id')}: its `{artifact}`")
        except HTTPFailure as e:
            warn(f"run {run.get('id')}: its `{artifact}` could not be fetched ({e})")
            continue
        if report is not None:
            return Found(run, report), ""
    return None, (f"none of the last {len(runs)} successful run(s) of `{workflow}` "
                  f"on `{branch}` kept a report as `{artifact}`; artifacts expire, "
                  f"and the name has to match")


def _report_in(archive: bytes, what: str = "an artifact") -> bytes | None:
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            return z.read("report.json") if "report.json" in z.namelist() else None
    except zipfile.BadZipFile:
        warn(f"{what} is not a zip archive; passed over")
        return None


def cmd_baseline(args: argparse.Namespace) -> int:
    branch = args.branch
    if not branch:
        warn("no branch to look for a baseline on; nothing to compare against")
        output(path="", note="there was no branch to look on")
        return 0
    workflow = args.workflow or workflow_file(os.environ.get("GITHUB_WORKFLOW_REF", ""))
    gh = GitHub(os.environ.get("GITHUB_API_URL", "https://api.github.com"),
                os.environ.get("GITHUB_TOKEN", ""))
    found, note = find_baseline(gh, repo=os.environ["GITHUB_REPOSITORY"],
                                workflow=workflow, branch=branch,
                                artifact=args.artifact,
                                current_run=int(os.environ.get("GITHUB_RUN_ID", "0")))
    if found is None:
        print(f"No baseline: {note}.")
        output(path="", note=note)
        return 0
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "report.json").write_bytes(found.report)
    run = found.run
    print(f"Baseline: run {run.get('run_number')} on `{branch}`, {run.get('html_url')}")
    output(path=str(dest / "report.json"), run=str(run.get("run_number", "")),
           url=str(run.get("html_url", "")), note="")
    return 0


# --- the comparison ---------------------------------------------------------

def cmd_compare(args: argparse.Namespace) -> int:
    out = Path(args.out)
    baseline = Path(args.baseline) if args.baseline else None
    if baseline is None or not baseline.is_file():
        if baseline is not None:
            warn(f"the baseline `{baseline}` is not a file; nothing to compare against")
        output(baseline="", comparison="", moved="", appeared="", vanished="", failed="")
        return 0
    # Beside the report, under a name that reads well in the comparison's
    # header, and kept in the artifact: the comparison can be made again from
    # the artifact alone.
    kept = out / "baseline" / "report.json"
    kept.parent.mkdir(parents=True, exist_ok=True)
    if baseline.resolve() != kept.resolve():
        shutil.copyfile(baseline, kept)
    done = subprocess.run([args.echolot, "compare", "baseline/report.json", "report.json",
                           "-c", str(Path(args.config).resolve()), "-o", str(out.resolve())],
                          cwd=out, check=False)
    comparison = out / "comparison.json"
    if done.returncode != 0 or not comparison.is_file():
        warn(f"`echolot compare` exited {done.returncode}; the report is kept, "
             f"without a comparison")
        output(baseline="", comparison="", moved="", appeared="", vanished="",
               failed=f"`echolot compare` exited {done.returncode}")
        return 0
    # `moved` is the rows that grew or shrank. One that appeared, a new block
    # of the main thread, is counted apart, and a workflow reading `moved`
    # alone would see no change.
    summary = json.loads(comparison.read_text(encoding="utf-8"))["summary"]
    output(baseline=str(kept), comparison=str(comparison), moved=str(summary["moved"]),
           appeared=str(summary.get("appeared", 0)), vanished=str(summary.get("vanished", 0)),
           failed="")
    return 0


# --- what a person reads ----------------------------------------------------

def page(out: Path, *, artifact: str, branch: str, note: str, run: str,
         url: str, whole: bool, failed: str = "") -> str:
    """The job summary when `whole`, else the shorter pull request comment.

    `failed` is why a comparison with a baseline that was found did not come
    out: without it the page said no baseline was found.
    """
    report = out / "report.md"
    comparison = out / "comparison.md"
    head = [f"## echolot · `{artifact}`", ""]
    if comparison.is_file():
        source = (f"the report run [{run}]({url}) kept on `{branch}`" if url
                  else "the report it was given")
        head += [f"Compared with {source}. `compare` reports and never fails "
                 f"the job; whether the build got slower is the benchmark's "
                 f"answer.", "", comparison.read_text(encoding="utf-8").strip(), ""]
        if whole and report.is_file():
            head += ["<details>", "<summary>The report</summary>", "",
                     report.read_text(encoding="utf-8").strip(), "", "</details>", ""]
        return "\n".join(head)
    if failed:
        source = f"the report run [{run}]({url}) kept" if url else "the baseline"
        head += [f"The comparison with {source} failed: {failed}, and the log of "
                 f"the compare step says why. This run's report is kept as "
                 f"`{artifact}`.", ""]
    else:
        reason = f"{note}." if note else "no baseline was found."
        head += [f"Nothing to compare against: {reason} This run's report is kept "
                 f"as `{artifact}`. A run compares against the report the last good "
                 f"run on `{branch}` kept, so a run on `{branch}` has to keep one "
                 f"first.", ""]
    if whole and report.is_file():
        head += [report.read_text(encoding="utf-8").strip(), ""]
    return "\n".join(head)


def cmd_summary(args: argparse.Namespace) -> int:
    text = page(Path(args.out), artifact=args.artifact, branch=args.branch,
                note=args.note, run=args.run, url=args.url, whole=True,
                failed=args.failed)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        print(text)
    return 0


def marker(artifact: str) -> str:
    return f"<!-- echolot:{artifact} -->"


def upsert_comment(gh: GitHub, repo: str, pr: int, artifact: str, body: str) -> str:
    """One comment per artifact name on a pull request, edited on every push."""
    tag = marker(artifact)
    if len(body) > COMMENT_LIMIT:
        body = (body[:COMMENT_LIMIT] + "\n\n…cut here; the whole comparison is "
                "in the job summary.")
    body = f"{tag}\n{body}"
    for n in range(1, 11):
        listed = gh.get(f"/repos/{repo}/issues/{pr}/comments?per_page=100&page={n}")
        mine = next((c for c in listed if tag in (c.get("body") or "")), None)
        if mine is not None:
            gh.send("PATCH", f"/repos/{repo}/issues/comments/{mine['id']}", {"body": body})
            return "updated"
        if len(listed) < 100:
            break
    gh.send("POST", f"/repos/{repo}/issues/{pr}/comments", {"body": body})
    return "posted"


def cmd_comment(args: argparse.Namespace) -> int:
    if not args.pr:
        warn("`comment` is on, and this run is not on a pull request; nothing posted")
        return 0
    body = page(Path(args.out), artifact=args.artifact, branch=args.branch,
                note=args.note, run=args.run, url=args.url, whole=False,
                failed=args.failed)
    gh = GitHub(os.environ.get("GITHUB_API_URL", "https://api.github.com"),
                os.environ.get("GITHUB_TOKEN", ""))
    try:
        said = upsert_comment(gh, os.environ["GITHUB_REPOSITORY"], int(args.pr),
                              args.artifact, body)
    except HTTPFailure as e:
        warn(f"the comment could not be posted ({e}); the token needs "
             f"`pull-requests: write`, which a pull request from a fork does not get")
        return 0
    print(f"Comment {said} on #{args.pr}.")
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="ci.py", description=__doc__.split("\n")[0])
    sub = top.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("analyze")
    p.add_argument("--echolot", required=True)
    p.add_argument("--config", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--traces", required=True)
    p.add_argument("--root", default=os.environ.get("GITHUB_WORKSPACE", "."))
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("baseline")
    p.add_argument("--artifact", required=True)
    p.add_argument("--branch", default="")
    p.add_argument("--workflow", default="")
    p.add_argument("--dest", required=True)
    p.set_defaults(func=cmd_baseline)

    p = sub.add_parser("compare")
    p.add_argument("--echolot", required=True)
    p.add_argument("--config", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--baseline", default="")
    p.set_defaults(func=cmd_compare)

    for name, func in (("summary", cmd_summary), ("comment", cmd_comment)):
        p = sub.add_parser(name)
        p.add_argument("--out", required=True)
        p.add_argument("--artifact", required=True)
        p.add_argument("--branch", default="")
        p.add_argument("--note", default="")
        p.add_argument("--run", default="")
        p.add_argument("--url", default="")
        p.add_argument("--failed", default="")
        if name == "comment":
            p.add_argument("--pr", default="")
        p.set_defaults(func=func)
    return top


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
