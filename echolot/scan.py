"""What the repository says about itself, before anybody reads it by hand.

Setup starts with facts an agent used to gather by reading build scripts:
the app module and its applicationId, the flavours and build types and
therefore the variants, which one is the measuring build, the module that
holds a macrobenchmark and what it measures, the gradle task that runs it,
the devices attached. On a real project that reading was the single largest
thing to enter the agent's window during setup — twenty-five thousand
characters of it, part of which was a `.class` file caught by a glob — and
the human still had to step in to say which variant to install.

Every fact here is read off text and says where it came from. Nothing is
evaluated: a build script is Groovy or Kotlin, and this reads it as the
person who wrote it reads it — blocks by name, strings by their quotes. Where
that is not enough the fact is left out and the note says so.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import mark
from .domains import constants, gradle_module, source_files

# --- reading a build script as text -------------------------------------------

_STRING = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')


def _strip(text: str) -> str:
    """Comments out, strings kept: braces inside either must not count."""
    out = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "\"'":
            m = _STRING.match(text, i)
            if m:
                out.append(m.group(0))
                i = m.end()
                continue
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def block(text: str, name: str) -> str | None:
    """The body of `name { … }`, braces balanced, or None."""
    m = re.search(rf"\b{re.escape(name)}\s*\{{", text)
    if not m:
        return None
    depth, i, start = 1, m.end(), m.end()
    while i < len(text) and depth:
        ch = text[i]
        if ch in "\"'":
            s = _STRING.match(text, i)
            if s:
                i = s.end()
                continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    return text[start:i - 1] if depth == 0 else None


# `beta { … }`, `create("beta") { … }`, `register("beta") { … }`,
# `"beta" { … }`, `getByName("beta") { … }`, `maybeCreate("beta").apply { … }`
_ENTRY = re.compile(
    r'(?:^|[\s;])(?:(?:create|register|getByName|named|maybeCreate)\s*\(\s*["\']([\w-]+)["\']\s*\)'
    r'(?:\s*\.\s*apply)?|["\']([\w-]+)["\']|([A-Za-z_]\w*))\s*\{')


def entries(body: str) -> list[tuple[str, str]]:
    """The named blocks at the top level of a container: (name, body)."""
    out = []
    i = 0
    while True:
        m = _ENTRY.search(body, i)
        if not m:
            break
        name = m.group(1) or m.group(2) or m.group(3)
        inner = block(body[m.start():], name) if m.group(3) else _balanced(body, m.end())
        if inner is None:
            break
        out.append((name, inner))
        i = m.end() + len(inner)
    return out


def _balanced(text: str, start: int) -> str | None:
    depth, i = 1, start
    while i < len(text) and depth:
        ch = text[i]
        if ch in "\"'":
            s = _STRING.match(text, i)
            if s:
                i = s.end()
                continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    return text[start:i - 1] if depth == 0 else None


def _value(body: str, key: str) -> str | None:
    """`key "v"`, `key = "v"`, `key("v")` — the first string after the key."""
    m = re.search(rf'\b{re.escape(key)}\s*(?:=|\(|\s)\s*["\']([^"\']+)["\']', body)
    return m.group(1) if m else None


def android_block(stext: str) -> str:
    """The `android { … }` block, or the whole script when there is none.

    Other plugins have a `defaultConfig` of their own — a publishing plugin
    on a real project declared one before `android` did, and the first
    `defaultConfig` in the file held no applicationId. What Android reads
    is what is under `android`.
    """
    return block(stext, "android") or stext


@dataclass
class Facts:
    root: str
    app: dict[str, Any] | None = None
    flavors: list[dict[str, Any]] = field(default_factory=list)
    build_types: list[dict[str, Any]] = field(default_factory=list)
    variants: list[dict[str, Any]] = field(default_factory=list)
    benchmarks: list[dict[str, Any]] = field(default_factory=list)
    devices: list[dict[str, Any]] | None = None
    allowed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --- the app module -----------------------------------------------------------

def _build_script(mdir: Path) -> tuple[Path | None, str]:
    for name in ("build.gradle.kts", "build.gradle"):
        f = mdir / name
        if f.exists():
            return f, f.read_text(encoding="utf-8", errors="replace")
    return None, ""


def app_module(root: Path, facts: Facts) -> dict[str, Any] | None:
    """The module whose manifest declares a launcher Activity — `mark`'s rule."""
    found = []
    for mf in mark.manifests(root):
        text = mf.read_text(encoding="utf-8", errors="replace")
        launchers = mark.launcher_activities(text)
        if launchers:
            found.append((mf, text, launchers))
    if not found:
        facts.notes.append("no AndroidManifest.xml under src/main declares a launcher "
                           "Activity — a library, or the app lives elsewhere (pass --root)")
        return None
    if len(found) > 1:
        facts.notes.append("several modules declare a launcher Activity: "
                           + ", ".join(gradle_module(m, root) for m, _, _ in found)
                           + " — the first is read; the others are apps too")
    mf, text, launchers = found[0]
    mdir = mark.module_dir_of(mf)
    script, stext = _build_script(mdir)
    stripped = android_block(_strip(stext))
    app = {
        "module": gradle_module(mf, root),
        "dir": mark._rel(mdir, root),
        "manifest": mark._rel(mf, root),
        "build_script": mark._rel(script, root) if script else None,
        "launcher": launchers[0],
        "application_id": _value(block(stripped, "defaultConfig") or stripped, "applicationId"),
        "namespace": _value(stripped, "namespace"),
        "process": (re.search(r'android:process\s*=\s*"([^"]+)"', text) or [None, None])[1],
        "profileable": bool(re.search(r"<profileable\b[^>]*android:shell\s*=\s*\"true\"", text)),
        "application_class": mark.application_class(text),
    }
    if not app["profileable"]:
        facts.notes.append(f"{app['manifest']} has no <profileable android:shell=\"true\" />: "
                           f"a release-like build will show no application slices in the "
                           f"trace. A debuggable build traces without it, and skews everything")
    return app


def flavors_of(stext: str) -> list[dict[str, Any]]:
    body = block(stext, "productFlavors")
    if body is None:
        return []
    out = []
    for name, inner in entries(body):
        out.append({"name": name,
                    "dimension": _value(inner, "dimension"),
                    "application_id_suffix": _value(inner, "applicationIdSuffix"),
                    "application_id": _value(inner, "applicationId")})
    return out


def build_types_of(stext: str) -> list[dict[str, Any]]:
    body = block(stext, "buildTypes")
    found = {}
    if body is not None:
        for name, inner in entries(body):
            found[name] = {
                "name": name,
                "application_id_suffix": _value(inner, "applicationIdSuffix"),
                "debuggable": _flag(inner, "debuggable"),
                "profileable": _flag(inner, "profileable"),
                "minify": _flag(inner, "minifyEnabled"),
                "init_with": (re.search(r"initWith\s*\(?\s*(\w+)", inner) or [None, None])[1],
            }
    # `debug` and `release` exist whether or not the script names them.
    for name in ("debug", "release"):
        found.setdefault(name, {"name": name, "application_id_suffix": None,
                                "debuggable": name == "debug", "profileable": None,
                                "minify": None, "init_with": None, "implicit": True})
    return list(found.values())


def _flag(body: str, key: str) -> bool | None:
    """`debuggable true`, `debuggable = false`, and Kotlin's `isDebuggable = …`."""
    kotlin = "is" + key[:1].upper() + key[1:]
    m = re.search(rf"\b(?:{re.escape(kotlin)}|{re.escape(key)})\s*=?\s*(true|false)\b", body)
    return None if not m else m.group(1) == "true"


def variants_of(app: dict[str, Any] | None, flavors: list[dict], build_types: list[dict]) -> list[dict]:
    """flavour × build type, with the applicationId each one installs as."""
    base = (app or {}).get("application_id")
    out = []
    for bt in build_types:
        for fl in flavors or [None]:
            app_id = (fl or {}).get("application_id") or base
            if app_id:
                app_id += ((fl or {}).get("application_id_suffix") or "") + (bt.get("application_id_suffix") or "")
            name = (fl["name"] + bt["name"][:1].upper() + bt["name"][1:]) if fl else bt["name"]
            out.append({"name": name, "flavor": fl["name"] if fl else None,
                        "build_type": bt["name"], "application_id": app_id,
                        "debuggable": bt.get("debuggable"),
                        "measure": _measures(bt)})
    return out


def _measures(bt: dict) -> str:
    """Whether this build type is one to measure on, and why."""
    if bt.get("debuggable"):
        return "no — debuggable: the runtime runs slower and differently"
    if bt.get("name") == "benchmark" or bt.get("profileable"):
        return "yes — release-like and profileable"
    if bt.get("name") == "release":
        return "release — profileable only if the manifest says so"
    return "unknown"


def preferred(variants: list[dict]) -> dict | None:
    """The variant to start from: benchmark over release over the rest, first flavour."""
    order = {"benchmark": 0, "release": 1}
    ranked = sorted(variants, key=lambda v: (order.get(v["build_type"], 2),
                                             v.get("debuggable") is True, v["name"]))
    return ranked[0] if ranked else None


# --- the benchmark ------------------------------------------------------------

_TEST = re.compile(r"@Test\b[^\n]*\n\s*(?:public\s+)?(?:fun|void)\s+(\w+)")
_CLASS = re.compile(r"\bclass\s+(\w+)")
_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)", re.MULTILINE)
_METRIC = re.compile(r"TraceSectionMetric\s*\(\s*(?:\"([^\"]+)\"|([\w.]+))")
_RUNNER_ARG = re.compile(
    r'testInstrumentationRunnerArguments\s*(?:\[\s*|\.put\s*\(\s*)["\']([\w.]+)["\']\s*(?:\]\s*=|,)\s*["\']([^"\']*)["\']')


def benchmarks_of(root: Path) -> list[dict[str, Any]]:
    """Every source with a MacrobenchmarkRule: the tests, what they measure, who they drive."""
    out = []
    by_module: dict[str, dict] = {}
    for path in source_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "MacrobenchmarkRule" not in text:
            continue
        module = gradle_module(path, root)
        entry = by_module.setdefault(module, {"module": module, "dir": None, "classes": [],
                                              "metrics": [], "package_name": None,
                                              "startup": False, "runner_args": {}})
        consts = constants({path: text})
        pkg = _PACKAGE.search(text)
        cls = _CLASS.search(text)
        entry["classes"].append({
            "file": mark._rel(path, root),
            "name": (pkg.group(1) + "." if pkg else "") + (cls.group(1) if cls else path.stem),
            "tests": _TEST.findall(text),
        })
        for lit, ident in _METRIC.findall(text):
            name = lit or consts.get(ident.rsplit(".", 1)[-1])
            if name and name not in entry["metrics"]:
                entry["metrics"].append(name)
        target = (re.search(r'packageName\s*=\s*"([^"]+)"', text)
                  or re.search(r'packageName\s*=\s*([\w.]+)', text))
        if target and not entry["package_name"]:
            val = target.group(1)
            entry["package_name"] = val if '"' in target.group(0) else consts.get(val.rsplit(".", 1)[-1])
        if "StartupTimingMetric" in text or "StartupMode.COLD" in text:
            entry["startup"] = True
        mdir = path
        while mdir != root and not (mdir / "build.gradle.kts").exists() and not (mdir / "build.gradle").exists():
            mdir = mdir.parent
        entry["dir"] = mark._rel(mdir, root)
    for entry in by_module.values():
        _, stext = _build_script(root / entry["dir"]) if entry["dir"] else (None, "")
        s = android_block(_strip(stext))
        entry["runner_args"] = dict(_RUNNER_ARG.findall(s))
        entry["flavors"] = [f["name"] for f in flavors_of(s)]
        entry["build_types"] = [b["name"] for b in build_types_of(s) if not b.get("implicit")]
        out.append(entry)
    return out


_TEST_PLUGIN = re.compile(r"\b(?:com\.android\.test|android[.-]test)\b")


def test_modules(root: Path) -> set[str]:
    """Module directories whose build script applies the `com.android.test` plugin.

    A macrobenchmark lives in one, and its sources are a test's — not a
    place for the app's temporary markers, and not the app's code to map
    findings to.
    """
    from .domains import files_named
    found = set()
    for name in ("build.gradle.kts", "build.gradle"):
        for script in files_named(root, name):
            try:
                text = script.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if _TEST_PLUGIN.search(_strip(text)):
                found.add(mark._rel(script.parent, root))
    return found


def gradle_tasks(bench: dict, variants: list[dict]) -> list[str]:
    """`connected<Variant>AndroidTest` for the variants the benchmark module has.

    A `com.android.test` module mirrors the app's flavours and build types
    it declares for itself; where it declares none, the app's are what
    gradle will name. Either way `./gradlew :<module>:tasks --all | grep
    connected` is the list that exists.
    """
    module = bench["module"]
    names = []
    flavors = bench.get("flavors") or sorted({v["flavor"] for v in variants if v["flavor"]})
    types = bench.get("build_types") or sorted({v["build_type"] for v in variants})
    for bt in types:
        for fl in flavors or [None]:
            cap = lambda s: s[:1].upper() + s[1:]  # noqa: E731
            names.append(f"{module}:connected{cap(fl) if fl else ''}{cap(bt)}AndroidTest")
    return names


# --- the rest -----------------------------------------------------------------

def devices_attached() -> list[dict[str, Any]] | None:
    """`adb devices -l`, or None when adb is not there to ask."""
    from .runner import RunnerError, _run
    try:
        out = _run(["adb", "devices", "-l"], timeout=30)
    except RunnerError:
        return None
    found = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        props = dict(p.split(":", 1) for p in parts[2:] if ":" in p)
        found.append({"serial": parts[0], "state": parts[1],
                      "model": props.get("model"), "device": props.get("device"),
                      "emulator": parts[0].startswith("emulator-")})
    return found


def allowed_paths(root: Path) -> list[str]:
    """`instrumentation.allowed` as globs over the modules that have sources.

    One line per top-level tree rather than one per module: a project with
    a hundred modules under `feature/` gets `feature/*/src/main`, which is
    what a person writes.
    """
    seen = set()
    tests = test_modules(root)
    for path in source_files(root):
        parts = path.relative_to(root).parts
        if "src" not in parts or "main" not in parts:
            continue
        i = parts.index("src")
        if i == 0 or parts[i + 1:i + 2] != ("main",):
            continue
        module = parts[:i]
        if "/".join(module) in tests:
            continue
        pattern = "/".join([module[0]] + ["*"] * (len(module) - 1) + ["src", "main"])
        seen.add(pattern)
    return sorted(seen)


def describe(root: Path, *, devices: bool = True) -> Facts:
    facts = Facts(root=str(root))
    facts.app = app_module(root, facts)
    stext = ""
    if facts.app and facts.app.get("build_script"):
        stext = android_block(_strip(
            (root / facts.app["build_script"]).read_text(encoding="utf-8", errors="replace")))
    facts.flavors = flavors_of(stext)
    facts.build_types = build_types_of(stext)
    facts.variants = variants_of(facts.app, facts.flavors, facts.build_types)
    facts.benchmarks = benchmarks_of(root)
    facts.allowed = allowed_paths(root)
    facts.devices = devices_attached() if devices else None
    if devices and facts.devices is None:
        facts.notes.append("adb is not on PATH, so no device was asked")
    return facts


# --- the output ---------------------------------------------------------------

def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def skeleton(facts: Facts) -> list[str]:
    """An echolot.yml to start from, every value with where it came from."""
    app = facts.app or {}
    chosen = preferred(facts.variants)
    app_id = (chosen or {}).get("application_id") or app.get("application_id") or "com.example.app"
    where = app.get("build_script") or "build.gradle"
    others = [v["name"] for v in facts.variants if chosen and v["name"] != chosen["name"]]
    bench = facts.benchmarks[0] if facts.benchmarks else None
    out = ["project:",
           f"  package: {app_id}",
           f'  process: "{app_id}*"',
           "  _source: derived",
           f'  _evidence: "applicationId in {where}'
           + (f', variant {chosen["name"]}' if chosen else "")
           + (f' — the others: {", ".join(others[:6])}' if others else "") + '"',
           "",
           "scenario:",
           f"  name: {bench['classes'][0]['tests'][0] if bench and bench['classes'] and bench['classes'][0]['tests'] else 'coldStart'}",
           '  start: {name: "bindApplication", _source: default}',
           ]
    if bench and bench["metrics"]:
        out.append(f'  end: {{name: "{bench["metrics"][0]}", _source: derived, '
                   f'_evidence: "TraceSectionMetric in {bench["classes"][0]["file"]}; '
                   f'confirm against a probe"}}')
    else:
        out.append('  end: {name: "?", _source: default, _evidence: "pick from `echolot probe` — '
                   'the longest slice that means ready"}')
    out.append("")
    if bench:
        tasks = gradle_tasks(bench, facts.variants)
        task = next((t for t in tasks if chosen and chosen["name"].lower() in t.lower()), tasks[0] if tasks else None)
        cls = bench["classes"][0]["name"] if bench["classes"] else None
        out += ["runner:",
                "  mode: gradle",
                f'  gradle_task: "{task}"' if task else '  gradle_task: "?"',
                ]
        if cls:
            out.append("  gradle_args:")
            out.append(f'    - "-Pandroid.testInstrumentationRunnerArguments.class={cls}"')
        out += ["  iterations: 5",
                f'  _evidence: "MacrobenchmarkRule in {bench["classes"][0]["file"] if bench["classes"] else bench["module"]}'
                + ('; the module declares its own variants' if bench.get("flavors") or bench.get("build_types") else "")
                + '"']
    else:
        out += ["runner:", "  mode: launch", "  iterations: 5", "  duration_ms: 20000",
                '  _evidence: "no MacrobenchmarkRule in the tree; force-stop + am start"']
    out += ["", "instrumentation:", "  allowed:"]
    out += [f'    - "{p}"' for p in facts.allowed[:12]]
    out += ["  temp_prefix: AGENTTMP_", "  cleanup: always"]
    return out


def render(facts: Facts) -> str:
    out = ["# The repository", ""]
    app = facts.app
    if app:
        out.append(f"App module `{app['module']}` ({app['dir']}) · applicationId `{app.get('application_id') or '?'}`"
                   + (f" · namespace `{app['namespace']}`" if app.get("namespace") else "")
                   + (f" · process `{app['process']}`" if app.get("process") else "")
                   + f" · launcher `{app['launcher']}`")
        out.append("profileable: " + ("yes — `<profileable android:shell=\"true\"/>` in " + app["manifest"]
                                      if app["profileable"] else
                                      "**no** — release-like builds will trace without application slices"))
    if facts.variants:
        out += ["", "## Variants", ""]
        out.append("| variant | installs as | debuggable | measure on it |")
        out.append("|---|---|---|---|")
        for v in facts.variants:
            out.append(f"| {v['name']} | `{v.get('application_id') or '?'}` | "
                       f"{'yes' if v.get('debuggable') else ('no' if v.get('debuggable') is False else '?')} | {v['measure']} |")
        chosen = preferred(facts.variants)
        if chosen:
            out.append("")
            out.append(f"Start from **{chosen['name']}**: {chosen['measure']}.")
    out += ["", "## Benchmarks", ""]
    if not facts.benchmarks:
        out.append("_no MacrobenchmarkRule in the tree — `runner.mode: launch` drives a cold start by itself_")
    for b in facts.benchmarks:
        out.append(f"`{b['module']}` — drives `{b.get('package_name') or '?'}`"
                   + (", a cold start" if b.get("startup") else ""))
        for c in b["classes"]:
            out.append(f"- `{c['name']}` ({c['file']}): " + (", ".join(c["tests"]) or "no @Test"))
        if b["metrics"]:
            out.append("- measures the sections: " + ", ".join(f"`{m}`" for m in b["metrics"])
                       + " — anchor candidates, and `domains` entries")
        if b.get("runner_args"):
            out.append("- runner arguments in its build script: "
                       + ", ".join(f"`{k}={v}`" for k, v in b["runner_args"].items()))
        tasks = gradle_tasks(b, facts.variants)
        if tasks:
            out.append("- gradle tasks, by the variants: " + ", ".join(f"`{t}`" for t in tasks[:6])
                       + (" …" if len(tasks) > 6 else "")
                       + f" — `./gradlew {b['module']}:tasks --all | grep connected` lists what exists")
    out += ["", "## Devices", ""]
    if facts.devices is None:
        out.append("_not asked_")
    elif not facts.devices:
        out.append("_none attached — `adb devices` is empty_")
    else:
        for d in facts.devices:
            out.append(f"- `{d['serial']}` {d['state']}"
                       + (f" · {d['model']}" if d.get("model") else "")
                       + (" · emulator" if d.get("emulator") else ""))
    if facts.notes:
        out += ["", "## Notes", ""] + [f"- {n}" for n in facts.notes]
    out += ["", "## A config to start from", "",
            "Every value says where it came from; confirm the anchors against a probe "
            "trace (`echolot collect -c echolot.yml -n 1`, then `echolot probe`).", "",
            "```yaml", *skeleton(facts), "```"]
    return "\n".join(out)


def to_json(facts: Facts) -> dict[str, Any]:
    return asdict(facts)
