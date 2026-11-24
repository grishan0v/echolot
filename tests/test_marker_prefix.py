"""The temporary markers' prefix comes from one place, `Config.temp_prefix` (#829, #874).

It was read in fifteen places, each with its own fallback, and a detector
parameter holding the same prefix was read in none: `repeated_work`
reported no near miss for a project with its own prefix, and said
`AGENTTMP_` in a report whose `markers` section said `PERFHUNT_`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture, selftest  # noqa: E402
from echolot.analyze import DETECTOR_DIR, MARKER_PREFIX_PARAMS, analyze_trace, plan_detectors  # noqa: E402
from echolot.config import DEFAULT_PREFIX, Config  # noqa: E402
from echolot.tp import load_detectors  # noqa: E402
from tests.support import check  # noqa: E402

OWN = "PERFHUNT_"   # as long as the default, so the fixture's bytes stay a valid trace


def _config(**instrumentation) -> Config:
    raw = dict(selftest.FIXTURE_CONFIG)
    if instrumentation:
        raw["instrumentation"] = instrumentation
    return Config(raw)


def test_the_prefix_has_one_default() -> None:
    check("no config section, the default", _config().temp_prefix == DEFAULT_PREFIX)
    check("the project's own", _config(temp_prefix=OWN).temp_prefix == OWN)
    check("an empty one is the default", _config(temp_prefix="").temp_prefix == DEFAULT_PREFIX)


def test_every_prefix_parameter_follows_the_config() -> None:
    """The cause of #874: a parameter whose default is the markers' prefix
    and that `analyze` does not fill."""
    held = sorted(f"{d.id}.{k}" for d in load_detectors(DETECTOR_DIR)
                  for k, v in d.params.items() if v == DEFAULT_PREFIX)
    check("the detectors that hold the prefix", held, "none: nothing to follow")
    check("each one is a parameter analyze fills",
          all(k.split(".", 1)[1] in MARKER_PREFIX_PARAMS for k in held), held)
    by_id = {d.id: (over, src) for d, over, src in plan_detectors(_config(temp_prefix=OWN))}
    for name in held:
        det, key = name.split(".", 1)
        over, src = by_id[det]
        check(f"{name} takes the project's prefix", over.get(key) == OWN, over)
        check("and the prefix is no threshold the config tuned", src == "default", src)
    by_id = {d.id: (over, src) for d, over, src in plan_detectors(_config())}
    check("the default prefix changes nothing", all(not over for over, _ in by_id.values()), by_id)
    tuned = Config({**selftest.FIXTURE_CONFIG, "instrumentation": {"temp_prefix": OWN},
                    "detectors": {"repeated_work": {"marker_prefix": "OTHER_"}}})
    over, _ = {d.id: (o, s) for d, o, s in plan_detectors(tuned)}["repeated_work"]
    check("detectors: still wins", over["marker_prefix"] == "OTHER_", over)


def test_a_project_with_its_own_prefix_gets_the_near_miss(tmp_path: Path) -> None:
    """#874's run: the fixture with its markers renamed, the config naming the prefix."""
    trace = tmp_path / "fixture.perfetto-trace"
    trace.write_bytes(fixture.build().replace(DEFAULT_PREFIX.encode(), OWN.encode()))
    report = analyze_trace(trace, _config(temp_prefix=OWN))
    det = next(d for d in report["detectors"] if d["id"] == "repeated_work")
    check("the parameter is the project's prefix", det["params"]["marker_prefix"] == OWN,
          det["params"])
    check("as the markers section says", report["markers"]["prefix"] == OWN, report["markers"])
    rows = {r["location"]: r for r in det["rows"]}
    near = rows.get(f"{OWN}insert_preset")
    check("the near miss comes back", near is not None and near["detail"].startswith("near miss"),
          sorted(rows))
