"""Questions asked with `echolot hunt` at the same moment (#724).

`open_new` read `hunt.json`, archived it and wrote a new one with nothing
held across the three steps, and the next number was worked out inside that
window: two commands read the same record and took the same number, and the
second write replaced the first. Six questions came out as four records.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import hunt as hunt_mod  # noqa: E402
from tests.support import check  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ("project:\n  package: com.example.app\n  process: com.example.app\n"
          "scenario:\n  name: coldStart\n")


def test_six_questions_at_once_are_six_investigations(tmp_path: Path) -> None:
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    traces = tmp_path / ".echolot" / "traces"
    traces.mkdir(parents=True)
    for i in range(5):
        (traces / f"coldStart_iter00{i}.perfetto-trace").write_bytes(b"x")
    env = dict(os.environ, PYTHONPATH=str(ROOT), ECHOLOT_NO_RECORD="1")
    # Each one waits on stdin until all six have started: as close to the
    # same moment as processes get.
    start = ("import sys; sys.stdin.read(1); from echolot.main import main; "
             "sys.exit(main(['hunt', sys.argv[1]]))")
    procs = [subprocess.Popen([sys.executable, "-c", start, f"q{i}"], cwd=tmp_path, env=env,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) for i in range(6)]
    go = threading.Barrier(len(procs))

    def release(p: subprocess.Popen) -> None:
        go.wait()
        p.stdin.write("x")
        p.stdin.flush()
    threads = [threading.Thread(target=release, args=(p,)) for p in procs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    said = [p.communicate(timeout=120) for p in procs]
    codes = [p.returncode for p in procs]
    check("all six exited 0", codes == [0] * 6, said)
    records = hunt_mod.history(tmp_path)
    check("six records", len(records) == 6, [(h.get("n"), h.get("question")) for h in records])
    check("numbered 1 to 6", sorted(int(h["n"]) for h in records) == list(range(1, 7)),
          [(h.get("n"), h.get("question")) for h in records])
    check("each question once", sorted(h["question"] for h in records)
          == [f"q{i}" for i in range(6)], records)
    sets = [d for d in traces.iterdir() if d.is_dir()]
    check("the loose set moved aside whole, once", len(sets) == 1
          and len(list(sets[0].glob("*.perfetto-trace"))) == 5
          and not list(traces.glob("*.perfetto-trace")), [(d.name, len(list(d.iterdir()))) for d in sets])
    check("one of them open", sum(h.get("status") == "open" for h in records) == 1, records)
