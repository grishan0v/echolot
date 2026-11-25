"""A launcher that is an `<activity-alias>` (#1007).

`scan` writes the alias as `runner.activity`, which is right: it is the
component the launcher starts. The platform starts the activity behind it,
and `am start -W` answers with that one, so `collect` said under every set
that the app had gone on to another activity by itself; the trace names the
alias, `performCreate:<the alias>`, and `analyze` said the launch ended
there. One command apart, two activities, and no word of the alias.

`collect` reads what the alias stands for once, from the device or from the
project's manifests, says nothing of a launch that ended there, and keeps it
with the record; `analyze` names the alias with the activity it stands for.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture, recording, runner  # noqa: E402
from echolot.analyze import analyze_trace  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.render import to_markdown  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_collect_fake_adb import AM_START, PACKAGE, FakeAdb  # noqa: E402

LAUNCHER = f"{PACKAGE}/{PACKAGE}.Launcher"
SPLASH = f"{PACKAGE}/{PACKAGE}.SplashActivity"
OTHER = f"{PACKAGE}/{PACKAGE}.OnboardingActivity"

# `cmd package resolve-activity -n` of an alias, cut to what is read.
RESOLVED_ALIAS = ("priority=0 preferredOrder=0 match=0x0 specificIndex=-1 isDefault=false\n"
                  "ActivityInfo:\n  name=com.example.app.Launcher\n"
                  "  taskAffinity=com.example.app targetActivity=com.example.app.SplashActivity "
                  "persistableMode=PERSIST_ROOT_ONLY\n")
RESOLVED_NONE = RESOLVED_ALIAS.replace("targetActivity=com.example.app.SplashActivity",
                                       "targetActivity=null")

MANIFEST = """\
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
  <application>
    <activity android:name=".SplashActivity" />
    <!-- <activity-alias android:name=".Old" android:targetActivity=".Gone" /> -->
    <activity-alias android:name=".Launcher" android:targetActivity=".SplashActivity">
      <intent-filter>
        <action android:name="android.intent.action.MAIN" />
        <category android:name="android.intent.category.LAUNCHER" />
      </intent-filter>
    </activity-alias>
    <activity-alias android:name="com.example.app.Icon2" android:targetActivity="SplashActivity" />
  </application>
</manifest>
"""


def ended_on(component: str) -> str:
    """`am start -W` of the launcher, answered with `component`."""
    return AM_START.format(activity=LAUNCHER, total=300, wait=320).replace(
        f"Activity: {LAUNCHER}", f"Activity: {component}")


def collect(tmp_path, monkeypatch, ends: str, resolved: str, root: Path | None = None):
    fake = FakeAdb(script={"start": [ended_on(ends)]})
    monkeypatch.setattr(runner, "_run", fake)
    said: list[str] = []
    checkout = root or tmp_path / "empty"
    checkout.mkdir(exist_ok=True)
    fake.script["resolve"] = [resolved]
    made = runner.collect(PACKAGE, tmp_path / "t", 3, name="startup", log=said.append,
                          section={"activity": "com.example.app.Launcher",
                                   "project_root": str(checkout)})
    return made, said, fake


# --- the manifest ------------------------------------------------------------------

def test_the_manifest_s_aliases_are_read_by_their_full_names():
    got = runner.manifest_aliases(MANIFEST, "com.example.app")
    check("a dot, a bare name and a full one, each under the namespace where it needs one; "
          "a commented-out alias is no alias",
          got == {"com.example.app.Launcher": "com.example.app.SplashActivity",
                  "com.example.app.Icon2": "com.example.app.SplashActivity"}, got)
    check("without a namespace an alias of a short name is left out",
          runner.manifest_aliases(MANIFEST, None) == {}, runner.manifest_aliases(MANIFEST, None))


# --- collect -----------------------------------------------------------------------

def test_a_launch_that_ended_on_the_alias_s_activity_says_nothing(tmp_path, monkeypatch):
    made, said, fake = collect(tmp_path, monkeypatch, SPLASH, RESOLVED_ALIAS)
    check("no line under a repeat", not any("ended on" in s for s in said), said)
    check("nothing at the end of the set", runner.activities_said(made) is None,
          runner.activities_said(made))
    asked = [c for c in fake.calls if "resolve-activity" in " ".join(c)]
    check("the device is asked once, after the first launch's recording",
          len(asked) == 1 and asked[0][-2:] == ["-n", LAUNCHER], asked)
    kinds = fake.kinds()
    check("and not while perfetto records", kinds.index("resolve") > kinds.index("wait"), kinds)
    kept = [runner.launch_record(r["path"]) for r in made]
    check("each record keeps the activity the alias stands for",
          all(k["target"] == SPLASH and k["asked"] == LAUNCHER and not k["notes"]
              for k in kept), kept)


def test_the_project_s_manifest_says_it_where_the_device_does_not(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    (root / "app/src/main").mkdir(parents=True)
    (root / "app/build.gradle.kts").write_text(
        'android {\n    namespace = "com.example.app"\n}\n', encoding="utf-8")
    (root / "app/src/main/AndroidManifest.xml").write_text(MANIFEST, encoding="utf-8")
    made, said, _ = collect(tmp_path, monkeypatch, SPLASH, RESOLVED_NONE, root=root)
    check("nothing said", not any("ended on" in s for s in said), said)
    check("the record keeps the manifest's word",
          runner.launch_record(made[0]["path"])["target"] == SPLASH)


def test_with_nothing_to_say_it_the_line_stays(tmp_path, monkeypatch):
    made, said, _ = collect(tmp_path, monkeypatch, SPLASH, RESOLVED_NONE)
    check("said once", sum("the launch ended on" in s for s in said) == 1, said)
    check("and the record has no target", "target" not in runner.launch_record(made[0]["path"]))


def test_a_launch_that_went_on_from_the_alias_is_still_said(tmp_path, monkeypatch):
    made, said, _ = collect(tmp_path, monkeypatch, OTHER, RESOLVED_ALIAS)
    under = [s.strip() for s in said if "the launch ended on" in s]
    check("an activity the alias does not stand for is another one",
          under == [f"the launch ended on {PACKAGE}.OnboardingActivity, not on the "
                    f"{PACKAGE}.Launcher it started: the app went on to another activity "
                    f"by itself"], said)
    check("and the set says it", (runner.activities_said(made) or "").startswith(
        f"every launch of the 3 ended on {PACKAGE}.OnboardingActivity"))


def test_a_set_that_ended_on_the_alias_and_elsewhere_counts_the_alias_as_the_start():
    results = [{"repeat": i + 1, "activity": a, "asked": LAUNCHER, "target": SPLASH}
               for i, a in enumerate((SPLASH, OTHER, SPLASH))]
    said = runner.activities_said(results) or ""
    check("the alias's activity is the one started",
          said.startswith(f"the launches did not end on one activity: "
                          f"{PACKAGE}.OnboardingActivity in repeat 2, {PACKAGE}.Launcher in "
                          f"the other 2"), said)


# --- analyze -----------------------------------------------------------------------

CONFIG = {"project": {"package": PACKAGE, "process": PACKAGE},
          "scenario": {"name": "startup", "window": "launch"}}
NAMED = f"`{fixture.HANDOVER_ALIAS}` (an alias of `{fixture.HANDOVER_SPLASH}`)"


def _analyzed(tmp_path: Path, record: dict | None, **build) -> dict:
    trace = tmp_path / "startup_iter000.perfetto-trace"
    trace.write_bytes(fixture.handover(splash=fixture.HANDOVER_ALIAS, **build))
    if record is not None:
        recording.write(trace, {"runner": "launch", "launch": record})
    return analyze_trace(str(trace), Config(CONFIG))


def test_analyze_names_the_alias_with_its_activity(tmp_path):
    rep = _analyzed(tmp_path, {"asked": LAUNCHER, "activity": SPLASH, "target": SPLASH},
                    kept=True)
    check("the window keeps both", rep["window"]["alias"] == {
        "name": fixture.HANDOVER_ALIAS, "of": fixture.HANDOVER_SPLASH}, rep["window"])
    text = to_markdown(rep)
    check("and the line names both", f"The launch ended on {NAMED}." in text, text[:1500])


def test_a_record_kept_before_the_target_was_says_it_too(tmp_path):
    rep = _analyzed(tmp_path, {"asked": LAUNCHER, "activity": SPLASH}, kept=True)
    check("am start named an activity the trace never created, and the trace created "
          "the one started", rep["window"].get("alias") == {
              "name": fixture.HANDOVER_ALIAS, "of": fixture.HANDOVER_SPLASH}, rep["window"])


def test_a_launch_that_went_on_by_itself_is_no_alias(tmp_path):
    rep = _analyzed(tmp_path, {"asked": LAUNCHER, "activity": f"{PACKAGE}/{fixture.HANDOVER_MAIN}"},
                    kept=True)
    check("the trace created both, so the second is another activity",
          "alias" not in rep["window"], rep["window"])


def test_without_a_record_the_trace_s_name_stands(tmp_path):
    rep = _analyzed(tmp_path, None, kept=True)
    check("nothing to tell an alias by", "alias" not in rep["window"], rep["window"])
    check("the trace's name", f"The launch ended on `{fixture.HANDOVER_ALIAS}`." in
          to_markdown(rep))
