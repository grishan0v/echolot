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
        "process": _application_process(text),
        "profileable": bool(re.search(r"<profileable\b[^>]*android:shell\s*=\s*\"true\"", text)),
        "application_class": mark.application_class(text),
    }
    return app


def _application_process(manifest: str) -> str | None:
    """`android:process` of the `<application>` tag, and of nothing else.

    Over the whole manifest the first one anywhere won — a `<service>`'s
    `:sync` — and was handed to the agent as the app's process.
    """
    tag = mark._APPLICATION_TAG.search(mark._XML_COMMENT.sub("", manifest))
    m = re.search(r'android:process\s*=\s*"([^"]+)"', tag.group(1)) if tag else None
    return m.group(1) if m else None


def _profileable_note(facts: Facts) -> None:
    """Whether a release-like build will trace with the app's slices, said once.

    Decided with the build types in hand: one that is not debuggable and sets
    `isProfileable = true` is profileable without the manifest's tag, and the
    note used to say no application slices about the very build the scan
    recommended.
    """
    app = facts.app
    if not app or app["profileable"]:
        return
    by_type = [b["name"] for b in facts.build_types
               if b.get("profileable") and not b.get("debuggable")]
    if by_type:
        app["profileable_by"] = by_type
        return
    facts.notes.append(f"{app['manifest']} has no <profileable android:shell=\"true\" />: "
                       f"a release-like build will show no application slices in the "
                       f"trace. A debuggable build traces without it, and skews everything")


def flavor_dimensions(stext: str) -> list[str]:
    """`flavorDimensions` in order, however the script writes it, or []."""
    m = re.search(r"\bflavorDimensions\b[^\n]*", stext)
    return re.findall(r"[\"']([\w-]+)[\"']", m.group(0)) if m else []


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


def build_types_of(stext: str, baseline_profile: bool = False) -> list[dict[str, Any]]:
    """The build types of a module: those its script declares, `debug` and
    `release`, and, where it applies androidx.baselineprofile, the ones that
    plugin adds (`baseline_profile_types`)."""
    body = block(stext, "buildTypes")
    found = {}
    if body is not None:
        for name, inner in entries(body):
            debuggable = _flag(inner, "debuggable")
            found[name] = {
                "name": name,
                "application_id_suffix": _value(inner, "applicationIdSuffix"),
                # AGP's `debug` is debuggable unless the script says otherwise,
                # declared or not.
                "debuggable": True if debuggable is None and name == "debug" else debuggable,
                "profileable": _flag(inner, "profileable"),
                "minify": _flag(inner, "minifyEnabled"),
                "init_with": _init_with(inner),
            }
    # `debug` and `release` exist whether or not the script names them.
    for name in ("debug", "release"):
        found.setdefault(name, {"name": name, "application_id_suffix": None,
                                "debuggable": name == "debug", "profileable": None,
                                "minify": None, "init_with": None, "implicit": True})
    if baseline_profile:
        baseline_profile_types(found)
    return list(found.values())


def _init_with(body: str) -> str | None:
    """The build type `initWith` starts from: `initWith release`, and the
    Kotlin DSL's `initWith(getByName("release"))`, whose first word is a
    function and was read as the build type."""
    m = re.search(r"initWith\s*\(\s*(?:\w+\.)?(?:getByName|named)\s*\(\s*[\"']([\w-]+)[\"']", body) \
        or re.search(r"initWith\s*\(?\s*(\w+)", body)
    return m.group(1) if m else None


def _flag(body: str, key: str) -> bool | None:
    """`debuggable true`, `debuggable = false`, and Kotlin's `isDebuggable = …`."""
    kotlin = "is" + key[:1].upper() + key[1:]
    m = re.search(rf"\b(?:{re.escape(kotlin)}|{re.escape(key)})\s*=?\s*(true|false)\b", body)
    return None if not m else m.group(1) == "true"


# --- the androidx.baselineprofile plugin --------------------------------------

# Applied to an app module, the plugin adds build types no script names
# (#906): for `release`, `benchmarkRelease` to measure on and
# `nonMinifiedRelease` to generate profiles with. Applied to a
# `com.android.test` module, it gives that module the same two, which is
# where the benchmark runs. Its `.consumer` part alone adds none.
BASELINE_PROFILE = "androidx.baselineprofile"
BENCHMARK_PREFIX = "benchmark"
NON_MINIFIED_PREFIX = "nonMinified"
_BASELINE_PROFILE_ID = r"androidx\.baselineprofile(?:\.apptarget|\.producer)?"
_PLUGIN_ID = re.compile(rf"""(?:\bid\s*\(?\s*|\bapply\s+plugin\s*:\s*)["']{_BASELINE_PROFILE_ID}["']""")
_PLUGIN_ALIAS = re.compile(r"\balias\s*\(?\s*\w+\.plugins\.([\w.]+)")
# The names a version catalog usually gives it, for a catalog this does not read.
_USUAL_ALIAS = re.compile(r"(?:[\w.]*\.)?baseline\.?profile(?:\.apptarget|\.producer)?")
_CATALOG_ENTRY = re.compile(
    rf'^\s*([\w.-]+)\s*=\s*(?:\{{[^}}\n]*\bid\s*=\s*)?"{_BASELINE_PROFILE_ID}(?::[^"]*)?"',
    re.MULTILINE)


def catalog_aliases(root: Path) -> set[str]:
    """What `alias(libs.plugins.…)` calls the plugin, by `gradle/*.versions.toml`.

    `baselineprofile = { id = "androidx.baselineprofile", … }` is
    `libs.plugins.baselineprofile`; `androidx-baseline-profile =
    "androidx.baselineprofile:1.3.0"` is `libs.plugins.androidx.baseline.profile`.
    The alias is the catalog's to choose, so it is read there.
    """
    out = set()
    for toml in sorted((root / "gradle").glob("*.versions.toml")):
        try:
            text = toml.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        out.update(re.sub(r"[-_]", ".", m.group(1)) for m in _CATALOG_ENTRY.finditer(text))
    return out


def applies_baseline_profile(script: str, aliases: set[str]) -> bool:
    """Whether a build script, its comments out, applies androidx.baselineprofile.

    By its id, `id("androidx.baselineprofile")` or `apply plugin:`, or by
    the alias a version catalog gives it: `aliases` from `catalog_aliases`,
    and the names catalogs usually choose.
    """
    if _PLUGIN_ID.search(script):
        return True
    return any(m.group(1) in aliases or _USUAL_ALIAS.fullmatch(m.group(1))
               for m in _PLUGIN_ALIAS.finditer(script))


def baseline_profile_types(found: dict[str, dict[str, Any]]) -> None:
    """Add the build types androidx.baselineprofile makes, which no script names.

    For every build type that is not debuggable, `release` whether declared
    or not, and whose name does not already start with either prefix, the
    plugin creates `benchmark<Type>` and `nonMinified<Type>` with
    `initWith(<Type>)`, so both install with its applicationIdSuffix. It
    makes both not debuggable and profileable, and AGP then writes
    `<profileable android:shell="true"/>` into their merged manifest. The
    benchmark one is minified as its build type is, with `-dontobfuscate`
    added. The other is not minified at all: it is built to generate
    profiles, and runs code R8 never touched. A build type of either name
    the script declares itself is configured the same way rather than
    created.

    Read off the scripts alone they were not there: on an open-source app
    `scan` recommended its `release`, which shows no application slices,
    and said the app was not profileable, while the plugin's
    `benchmarkRelease` was the build to measure (#906).
    """
    types = list(found.values())
    for bt in types:
        if inherited(bt, types, "debuggable") is True \
                or bt["name"].startswith((BENCHMARK_PREFIX, NON_MINIFIED_PREFIX)):
            continue
        for prefix in (BENCHMARK_PREFIX, NON_MINIFIED_PREFIX):
            name = prefix + _cap(bt["name"])
            # `implicit`: there whether or not the script names it, as
            # `debug` and `release` are.
            made = found.setdefault(name, {"name": name, "application_id_suffix": None,
                                           "minify": None, "init_with": bt["name"],
                                           "implicit": True})
            made.update(debuggable=False, profileable=True, added_by=BASELINE_PROFILE)
            if prefix == NON_MINIFIED_PREFIX:
                made["minify"] = False


def for_profiles(variant: dict) -> bool:
    """A `nonMinified…` variant androidx.baselineprofile makes to generate profiles."""
    return bool(variant.get("added_by")) and variant["build_type"].startswith(NON_MINIFIED_PREFIX)


def flavor_combinations(flavors: list[dict], dimensions: list[str]) -> list[list[dict]] | None:
    """The flavour sets a variant is built from: one flavour of each dimension.

    In the order of `flavorDimensions`. With one dimension, or none named,
    each flavour on its own. None when the flavours span several dimensions
    and their order cannot be read: a variant's name depends on it, and a
    guessed name is a task no variant has.
    """
    if not flavors:
        return [[]]
    used = list(dict.fromkeys(f.get("dimension") for f in flavors if f.get("dimension")))
    if len(used) <= 1:
        return [[f] for f in flavors]
    if not dimensions or set(used) - set(dimensions):
        return None
    combos: list[list[dict]] = [[]]
    for dim in (d for d in dimensions if d in used):
        combos = [c + [f] for c in combos for f in flavors if f.get("dimension") == dim]
    return combos


def inherited(bt: dict, build_types: list[dict], key: str) -> Any:
    """A build type's own `key`, else that of the build type it starts from with `initWith`.

    AGP copies every property along with `initWith`, as `minified` says of
    `minifyEnabled`: a build type that starts from debug installs with
    debug's applicationIdSuffix and is debuggable as debug is. Read off its
    own block alone, such a build type on a large Kotlin app was taken to
    install as the store's package and to be no debug build; both were
    wrong. None where no build type in the chain names it.
    """
    by_name = {b["name"]: b for b in build_types}
    seen: set[str] = set()
    current: dict | None = bt
    while current is not None and current["name"] not in seen:
        seen.add(current["name"])
        if current.get(key) is not None:
            return current[key]
        current = by_name.get(current.get("init_with") or "")
    return None


def minified(bt: dict, build_types: list[dict]) -> tuple[bool | None, str | None]:
    """Whether a build type is minified, and the build type that says so.

    Its own `minifyEnabled` decides. Without one, the build type it starts
    from with `initWith` does, since AGP copies the flag along with the rest:
    a benchmark build type is usually `initWith(release)` and minified when
    release is. (None, None) for a build type that names neither, which AGP
    leaves unminified.
    """
    by_name = {b["name"]: b for b in build_types}
    seen: set[str] = set()
    current: dict | None = bt
    while current is not None and current["name"] not in seen:
        seen.add(current["name"])
        if current.get("minify") is not None:
            return current["minify"], current["name"]
        current = by_name.get(current.get("init_with") or "")
    return None, None


def mapping_path(app: dict[str, Any] | None, variant: str) -> str:
    """Where AGP writes a variant's R8 mapping, from the root.

    `<module>/build/outputs/mapping/<variant>/mapping.txt`, written when the
    variant is built and not before.
    """
    module = (app or {}).get("dir") or ""
    prefix = "" if module in ("", ".") else module.rstrip("/") + "/"
    return f"{prefix}build/outputs/mapping/{variant}/mapping.txt"


def variants_of(app: dict[str, Any] | None, flavors: list[dict], build_types: list[dict],
                dimensions: list[str] | None = None) -> list[dict]:
    """flavours × build type, with the applicationId each one installs as.

    Across dimensions, one flavour of each, as the Android Gradle plugin
    builds them: `freeStagingBenchmark`, never `freeBenchmark` beside
    `stagingBenchmark`. None at all when that cannot be read. A minified one
    carries where its mapping will be.
    """
    base = (app or {}).get("application_id")
    combos = flavor_combinations(flavors, dimensions or [])
    out = []
    for bt in build_types:
        # What `initWith` copies, read through the chain: see `inherited`.
        suffix = inherited(bt, build_types, "application_id_suffix")
        debuggable = inherited(bt, build_types, "debuggable")
        for combo in combos or []:
            app_id = next((f["application_id"] for f in combo if f.get("application_id")), None) or base
            if app_id:
                app_id += "".join(f.get("application_id_suffix") or "" for f in combo) \
                    + (suffix or "")
            words = [f["name"] for f in combo] + [bt["name"]]
            name = words[0] + "".join(w[:1].upper() + w[1:] for w in words[1:])
            minify, minified_by = minified(bt, build_types)
            out.append({"name": name,
                        "flavor": _flavor_word([f["name"] for f in combo]) or None,
                        "flavor_names": [f["name"] for f in combo],
                        "build_type": bt["name"], "application_id": app_id,
                        "debuggable": debuggable,
                        "measure": _measures({**bt, "debuggable": debuggable}),
                        "minified": minify, "minified_by": minified_by,
                        "mapping": mapping_path(app, name) if minify else None,
                        "added_by": bt.get("added_by")})
    return out


def _flavor_word(names: list[str]) -> str:
    """Flavour names as a variant's name joins them: `free`, `paid` → `freePaid`."""
    return "".join(_cap(n) if i else n for i, n in enumerate(names))


def _minify_evidence(variant: dict, script: str) -> str:
    """Why a variant is called minified: the script, the build type and, if
    it inherits the flag, how."""
    bt, by = variant["build_type"], variant["minified_by"]
    if by == bt:
        return f"minifyEnabled in buildTypes.{bt} of {script}"
    if variant.get("added_by"):
        return f"minifyEnabled in buildTypes.{by} of {script}, which {variant['added_by']}'s {bt} starts from"
    return f"minifyEnabled in buildTypes.{by} of {script}, which {bt} starts from with initWith"


def _measures(bt: dict) -> str:
    """Whether this build type is one to measure on, and why."""
    if bt.get("debuggable"):
        return "no — debuggable: the runtime runs slower and differently"
    if bt.get("added_by") and bt["name"].startswith(NON_MINIFIED_PREFIX):
        twin = BENCHMARK_PREFIX + bt["name"][len(NON_MINIFIED_PREFIX):]
        return (f"no — built without R8 to generate profiles, so its code is not what users "
                f"run: measure on {twin}")
    if bt.get("added_by"):
        return f"yes — release-like, and profileable by {bt['added_by']}"
    if bt.get("name") == "benchmark" or bt.get("profileable"):
        return "yes — release-like and profileable"
    if bt.get("name") == "release":
        return "release — profileable only if the manifest says so"
    return "unknown"


def _rank(variant: dict) -> tuple:
    """Benchmark over the plugin's benchmark build types over release over
    the rest, debuggable last; then the flavour a benchmark module runs
    against (`_benchmark_targets`), then by name."""
    bt = variant["build_type"]
    order = {"benchmark": 0, "release": 2}.get(bt, 3)
    if variant.get("added_by") and bt.startswith(BENCHMARK_PREFIX):
        order = 1
    return (order, variant.get("debuggable") is True, not variant.get("benchmarked"),
            variant["name"])


def preferred(variants: list[dict]) -> dict | None:
    """The variant to start from, as `_rank` orders them: a `benchmark` build
    type, then the one androidx.baselineprofile adds, then release."""
    ranked = sorted(variants, key=_rank)
    return ranked[0] if ranked else None


# --- the benchmark ------------------------------------------------------------

_TEST = re.compile(r"@Test\b[^\n]*\n\s*(?:public\s+)?(?:fun|void)\s+(\w+)")
_CLASS = re.compile(r"\bclass\s+(\w+)")
_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)", re.MULTILINE)
_METRIC = re.compile(r"TraceSectionMetric\s*\(\s*(?:\"([^\"]+)\"|([\w.]+))")
_RUNNER_ARG = re.compile(
    r'testInstrumentationRunnerArguments\s*(?:\[\s*|\.put\s*\(\s*)["\']([\w.]+)["\']\s*(?:\]\s*=|,)\s*["\']([^"\']*)["\']')
# `missingDimensionStrategy("default", "alpha")`, Groovy's without the
# parentheses, and a list whose first flavour is the one taken.
_MISSING_DIMENSION = re.compile(
    r'missingDimensionStrategy\s*\(?\s*["\']([\w-]+)["\']\s*,\s*(?:listOf\s*\(\s*|\[\s*)?["\']([\w-]+)["\']')


# What a benchmark class measures, by what its own file constructs: the
# metric called, in the code. An import, or a line commented out, measures
# nothing.
_STARTUP_METRIC = re.compile(r"\bStartupTimingMetric\s*\(")
_SECTION_METRIC = re.compile(r"\bTraceSectionMetric\s*\(")
_STARTUP_MODE = re.compile(r"\bStartupMode\b")

# The kinds of benchmark class the draft tells apart (#458), in `kind`.
SECTION = "section"            # names a TraceSectionMetric: where "ready" is
LAUNCH = "launch"              # measures the launch, and names no section
STARTUP_MODE = "startup_mode"  # only sets StartupMode: may measure no launch


def _kind(code: str, metrics: list[str]) -> str | None:
    """Which kind of benchmark a class is, read off its own file.

    A class was "startup" when its source had `StartupTimingMetric` or
    `StartupMode`, and the two say different things. The metric says the
    launch is what is measured. The mode says how each repeat begins: a
    scroll benchmark passes `startupMode = StartupMode.COLD` to
    `measureRepeated` and measures frames. So the draft could not tell the
    project best prepared for a launch scenario from one that measures a
    scroll, and left both with a question mark.

    `section` comes first: a `TraceSectionMetric` is the developer's own
    statement of where "ready" is, and it wins over the launch window, also
    when its name could not be read here. None for a class that names none
    of the three in its own file, as one whose metrics are listed in a
    shared helper.
    """
    if metrics or _SECTION_METRIC.search(code):
        return SECTION
    if _STARTUP_METRIC.search(code):
        return LAUNCH
    if _STARTUP_MODE.search(code):
        return STARTUP_MODE
    return None


def _as_glob(section: str) -> str:
    """A `TraceSectionMetric` name as the GLOB echolot matches anchors with.

    `%` is that metric's wildcard, and in a GLOB an ordinary character: an
    anchor copied over unchanged matched nothing, and the window ran to the
    end of the trace.
    """
    escaped = re.sub(r"([*?\[])", r"[\1]", section)
    return escaped.replace("%", "*")


def benchmarks_of(root: Path) -> list[dict[str, Any]]:
    """Every source with a MacrobenchmarkRule: the tests, what they measure, who they drive."""
    out = []
    by_module: dict[str, dict] = {}
    sources = source_files(root)
    # The project's constants, read only when a benchmark names one its own
    # file does not declare. A large app keeps its section names in a
    # constants object of another module (`TraceSectionMetric(AppTraces.
    # LIST_SHOWN)`), and the file alone resolved none of them: the class
    # measured nothing, and the draft's end anchor was "?" (#516). Its own
    # file comes first, as `domains` would see the nearest declaration.
    everywhere: dict[str, str | None] | None = None

    def resolve(ident: str, consts: dict[str, str | None]) -> str | None:
        nonlocal everywhere
        simple = ident.rsplit(".", 1)[-1]
        if simple in consts:
            return consts[simple]
        if everywhere is None:
            texts = {}
            for src in sources:
                try:
                    texts[src] = src.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
            everywhere = constants(texts)
        return everywhere.get(simple)

    for path in sources:
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
        # In the code, not in a comment: the KDoc Android Studio's template
        # puts above its benchmark says "This test class benchmarks…", and
        # `benchmarks` went into the skeleton's class filter.
        code = _strip(text)
        cls = _CLASS.search(code)
        metrics: list[str] = []
        unresolved: list[str] = []
        # From the code: a metric in a comment measures nothing, and its
        # name would otherwise be listed as a constant not found.
        for lit, ident in _METRIC.findall(code):
            name = lit or resolve(ident, consts)
            if name:
                if _as_glob(name) not in metrics:
                    metrics.append(_as_glob(name))
            elif ident not in unresolved:
                # Kept by its identifier, so the draft can say which name to
                # look up rather than only that one was not read.
                unresolved.append(ident)
        # Each class keeps its own metrics, so the skeleton takes the class,
        # its test and its end anchor from one file.
        entry["classes"].append({
            "file": mark._rel(path, root),
            "name": (pkg.group(1) + "." if pkg else "") + (cls.group(1) if cls else path.stem),
            "tests": _TEST.findall(text),
            "metrics": metrics,
            "unresolved": unresolved,
            "startup": "StartupTimingMetric" in text or "StartupMode" in text,
            # `StartupTimingMetric` by itself, apart from `StartupMode`:
            # whether the launch is what this class measures.
            "measures_launch": bool(_STARTUP_METRIC.search(code)),
            "kind": _kind(code, metrics),
        })
        for name in metrics:
            if name not in entry["metrics"]:
                entry["metrics"].append(name)
        target = (re.search(r'packageName\s*=\s*"([^"]+)"', text)
                  or re.search(r'packageName\s*=\s*([\w.]+)', text))
        if target and not entry["package_name"]:
            val = target.group(1)
            entry["package_name"] = val if '"' in target.group(0) else resolve(val, consts)
        if "StartupTimingMetric" in text or "StartupMode.COLD" in text:
            entry["startup"] = True
        mdir = path
        while mdir != root and not (mdir / "build.gradle.kts").exists() and not (mdir / "build.gradle").exists():
            mdir = mdir.parent
        entry["dir"] = mark._rel(mdir, root)
    aliases = catalog_aliases(root) if by_module else set()
    for entry in by_module.values():
        _, stext = _build_script(root / entry["dir"]) if entry["dir"] else (None, "")
        whole = _strip(stext)
        s = android_block(whole)
        entry["runner_args"] = dict(_RUNNER_ARG.findall(s))
        combos = flavor_combinations(flavors_of(s), flavor_dimensions(s)) or []
        entry["flavors"] = [_flavor_word([f["name"] for f in c]) for c in combos if c]
        # The plugin gives a test module `benchmarkRelease` and
        # `nonMinifiedRelease` whatever it declares, and the benchmark runs
        # in the first: its task is `connectedBenchmarkReleaseAndroidTest`.
        entry["baseline_profile"] = applies_baseline_profile(whole, aliases)
        entry["build_types"] = [b["name"] for b in build_types_of(s, entry["baseline_profile"])
                                if not b.get("implicit") or b.get("added_by")]
        # The app module it runs against, and the flavours it takes from it:
        # a dimension the module names here is one its variants do not have.
        entry["target"] = _value(s, "targetProjectPath")
        entry["missing_dimensions"] = dict(_MISSING_DIMENSION.findall(s))
        out.append(entry)
    return out


# The plugin applied, in every way a build script applies one. `android.test`
# anywhere was taken for it: `useLibrary("android.test.runner")` made the app
# module a test module, and `mark` then refused every site in it.
_TEST_PLUGIN = re.compile(
    r"""\bid\s*\(?\s*["']com\.android\.test["']"""
    r"""|\bapply\s+plugin\s*:\s*["']com\.android\.test["']"""
    r"""|\balias\s*\(?\s*[\w.]*?\bandroid[.-]test\b(?![.\w-])""")
# Gradle's own code: a convention plugin or buildSrc, which never runs on
# the device and is no place for a marker.
_BUILD_LOGIC = re.compile(r"""kotlin-dsl|\bjava-gradle-plugin\b|`kotlin-dsl`""")


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
    gradle will name, less the dimensions its `missingDimensionStrategy`
    settles. Its build types include those androidx.baselineprofile gives
    it. Either way `./gradlew :<module>:tasks --all | grep connected` is the
    list that exists.
    """
    module = bench["module"]
    names = []
    flavors = _module_flavors(bench, variants)
    types = bench.get("build_types") or sorted({v["build_type"] for v in variants})
    for bt in types:
        for fl in flavors or [None]:
            names.append(f"{module}:connected{_cap(fl) if fl else ''}{_cap(bt)}AndroidTest")
    return names


def _pinned(bench: dict) -> set[str]:
    """The app's flavours a benchmark module's `missingDimensionStrategy` takes."""
    return set((bench.get("missing_dimensions") or {}).values())


def _module_flavors(bench: dict, variants: list[dict]) -> list[str]:
    """The flavours in a benchmark module's task names.

    Its own where it declares them. Else the app's, from the variants it
    can run against, without the flavours `missingDimensionStrategy` picks:
    that dimension is not one of the module's. A module that settles the
    app's only dimension that way has no flavour in its task names, as
    `:baselineprofile:connectedBenchmarkReleaseAndroidTest` on an app whose
    every variant has one.
    """
    if bench.get("flavors"):
        return bench["flavors"]
    pinned = _pinned(bench)
    words = {_flavor_word([n for n in v.get("flavor_names") or [] if n not in pinned])
             for v in variants if pinned <= set(v.get("flavor_names") or [])}
    return sorted(words - {""})


def measuring_task(bench: dict, variants: list[dict], chosen: dict | None) -> str | None:
    """The benchmark module's task that runs against `chosen`, else its first.

    Built from the chosen variant's flavour, as the module's task names
    carry it, and its build type. A name match was all there was, and a
    variant's whole name is in no task of a module without its flavour
    dimension.
    """
    tasks = gradle_tasks(bench, variants)
    if chosen:
        word = chosen.get("flavor") if bench.get("flavors") else _flavor_word(
            [n for n in chosen.get("flavor_names") or [] if n not in _pinned(bench)])
        want = (f"{bench['module']}:connected{_cap(word) if word else ''}"
                f"{_cap(chosen['build_type'])}AndroidTest")
        if want in tasks:
            return want
    return next((t for t in tasks if chosen and chosen["name"].lower() in t.lower()),
                tasks[0] if tasks else None)


def _driven(bench: dict, variants: list[dict]) -> list[dict]:
    """The app's variants a benchmark module runs against: of the flavours
    its `missingDimensionStrategy` picks, in the build types it has where it
    names its own."""
    pinned, types = _pinned(bench), set(bench.get("build_types") or [])
    return [v for v in variants if pinned <= set(v.get("flavor_names") or [])
            and (not types or v["build_type"] in types)]


def _benchmark_targets(facts: Facts) -> None:
    """What the benchmark modules that target the app module say about it.

    The package a benchmark drives is the applicationId of the variant its
    module runs against, `targetProjectPath` naming the module. Where the
    code passes it on at run time, as a benchmark the baselineprofile
    plugin wires does (`InstrumentationRegistry.getArguments()
    .getString("targetAppId")`), no string in the code says it, and the
    module drove `?` (#906). And a module that takes one flavour with
    `missingDimensionStrategy` runs against that flavour only: among
    variants of one build type, the one to start from is of it, or the
    config would name a package the benchmark never starts.
    """
    app = facts.app
    if not app:
        return
    for bench in facts.benchmarks:
        if not bench.get("target") or bench["target"] != app["module"]:
            continue
        driven = _driven(bench, facts.variants)
        if _pinned(bench):
            for v in driven:
                v["benchmarked"] = True
        target = preferred(driven)
        if not bench.get("package_name") and target and target.get("application_id"):
            bench["package_name"] = target["application_id"]
            bench["package_by"] = {"target": bench["target"], "variant": target["name"]}


# --- the rest -----------------------------------------------------------------

def devices_attached() -> list[dict[str, Any]] | None:
    """`adb devices -l`, or None when adb is not there to ask.

    Any other failure of adb's — a server that does not answer within the
    timeout, an error of its own — is raised as the RunnerError that says
    so, for `describe` to pass on. It used to be None as well, and the note
    read "adb is not on PATH" over an adb that was there and had timed out.
    The lines are read by the runner's parser, the one `collect` picks a
    device with, so a Linux `no permissions (…)` is that state rather than
    a device in state `no`.
    """
    from .runner import AdbNotFound, _run, parse_devices
    try:
        out = _run(["adb", "devices", "-l"], timeout=30)
    except AdbNotFound:
        return None
    return [{"serial": d["serial"], "state": d["state"],
             "model": d["props"].get("model"), "device": d["props"].get("device"),
             "emulator": d["serial"].startswith("emulator-")}
            for d in parse_devices(out)]


def allowed_paths(root: Path) -> list[str]:
    """`instrumentation.allowed` as globs over the modules that have sources.

    One line per top-level tree rather than one per module: a project with
    a hundred modules under `feature/` gets `feature/*/src/main`, which is
    what a person writes.
    """
    seen = set()
    tests = test_modules(root)
    logic: dict[tuple[str, ...], bool] = {}
    for path in source_files(root):
        parts = path.relative_to(root).parts
        if "src" not in parts or "main" not in parts:
            continue
        i = parts.index("src")
        if i == 0 or parts[i + 1:i + 2] != ("main",):
            continue
        module = parts[:i]
        if "/".join(module) in tests or module[0] == "buildSrc":
            continue
        if module not in logic:
            _, script = _build_script(root.joinpath(*module))
            logic[module] = bool(_BUILD_LOGIC.search(_strip(script)))
        if logic[module]:
            continue
        pattern = "/".join([module[0]] + ["*"] * (len(module) - 1) + ["src", "main"])
        seen.add(pattern)
    return sorted(seen)


def _app_and_variants(root: Path, facts: Facts) -> None:
    """The app module, its flavours and build types, and the variants they make."""
    facts.app = app_module(root, facts)
    whole = ""
    if facts.app and facts.app.get("build_script"):
        whole = _strip((root / facts.app["build_script"]).read_text(encoding="utf-8", errors="replace"))
    stext = android_block(whole)
    plugin = bool(whole) and applies_baseline_profile(whole, catalog_aliases(root))
    if facts.app and plugin:
        facts.app["plugins"] = [BASELINE_PROFILE]
    facts.flavors = flavors_of(stext)
    facts.build_types = build_types_of(stext, plugin)
    dims = flavor_dimensions(stext)
    facts.variants = variants_of(facts.app, facts.flavors, facts.build_types, dims)
    if facts.flavors and flavor_combinations(facts.flavors, dims) is None:
        facts.notes.append("the flavours span several dimensions and flavorDimensions could "
                           "not be read, so no variant is named: `./gradlew :app:tasks --all` "
                           "lists them")


# More tasks than this is a list nobody picks from: a large Kotlin app whose
# flavours all install as one package had twelve.
INSTALL_TASKS_SHOWN = 3


def install_tasks(root: Path, package: str | None) -> list[str]:
    """The gradle tasks that install the variants installing as `package`.

    `:app:installBenchmark` and the like: in launch and command modes
    `collect` records the build on the phone, and a change to the code
    reaches a recording only through one of these (#390). Several when
    several variants install as it, the one to measure on first as
    `preferred` ranks them: which one is on the phone is not written
    anywhere this can read.

    Never a debuggable variant: a debug install would put a build that runs
    slower and differently in place of the one measured. On a large Kotlin
    app the only variants the script showed installing as the measured
    package were debug ones, while the build on the phone was not
    debuggable. Nor the `nonMinified…` one androidx.baselineprofile builds
    to generate profiles, whose code R8 never touched; its `benchmark…`
    one comes first, the build the plugin makes to measure (#906). Where
    more than INSTALL_TASKS_SHOWN install as the package, only those of the
    best build type, as `_rank` orders them. Empty then, and when nothing
    installs as the package, when more than INSTALL_TASKS_SHOWN of that
    build type do, or when the app module or its applicationId cannot be
    read: the caller says it in words.
    """
    if not package:
        return []
    facts = Facts(root=str(root))
    _app_and_variants(root, facts)
    if not facts.app:
        return []
    found = [v for v in facts.variants
             if v.get("application_id") == package and v.get("debuggable") is not True
             and not for_profiles(v)]
    if len(found) > INSTALL_TASKS_SHOWN:
        # Too many: those of the build type to measure on, if they are few
        # enough. The plugin's build types doubled what installs as one
        # package, and three flavours that installed as the store's named
        # their release builds before it and nothing after.
        best = min(_rank(v)[0] for v in found)
        found = [v for v in found if _rank(v)[0] == best]
    if len(found) > INSTALL_TASKS_SHOWN:
        return []
    module = facts.app["module"]
    return [f"{'' if module == ':' else module}:install{_cap(v['name'])}"
            for v in sorted(found, key=_rank)]


def _read_id(facts: Facts) -> str | None:
    """The applicationId the draft config names: the chosen variant's, else the module's."""
    chosen = preferred(facts.variants)
    return (chosen or {}).get("application_id") or (facts.app or {}).get("application_id")


def _id_note(facts: Facts) -> None:
    """Say when an app module was found and its applicationId was not read.

    Added here, with the other notes, so `render` prints it under "## Notes".
    `skeleton` used to add it, and `render` calls `skeleton` after it has
    printed that section: the note was never shown, and every further
    `skeleton` of the same facts added it once more (#550).
    """
    if facts.app and not _read_id(facts):
        where = facts.app.get("build_script") or "build.gradle"
        facts.notes.append(f"the applicationId could not be read from {where}: it is set "
                           f"through a constant or a convention plugin. The config below holds "
                           f"a placeholder")


def describe(root: Path, *, devices: bool = True) -> Facts:
    facts = Facts(root=str(root))
    _app_and_variants(root, facts)
    # Before the notes: which flavour a benchmark module runs against
    # decides the variant the applicationId note reads.
    facts.benchmarks = benchmarks_of(root)
    _benchmark_targets(facts)
    _profileable_note(facts)
    _id_note(facts)
    facts.allowed = allowed_paths(root)
    if devices:
        from .runner import RunnerError
        try:
            facts.devices = devices_attached()
        except RunnerError as e:
            facts.notes.append(f"adb is on PATH but did not list the devices: {e.gist}")
        else:
            if facts.devices is None:
                facts.notes.append("adb is not on PATH, so no device was asked")
    return facts


# --- the output ---------------------------------------------------------------

def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


# How a drafted launch window ends its evidence, whoever starts the launch:
# nobody picks the end, and the report says which one the trace gave.
_THE_TRACE_DECIDES = ("the trace decides the end: the launch's end as the platform records it, "
                      "or the app's reportFullyDrawn when that comes later, and the report says "
                      "which (window.defined_by)")


def skeleton(facts: Facts) -> list[str]:
    """An echolot.yml to start from, every value with where it came from."""
    app = facts.app or {}
    chosen = preferred(facts.variants)
    read_id = _read_id(facts)
    where = app.get("build_script") or "build.gradle"
    others = [v["name"] for v in facts.variants if chosen and v["name"] != chosen["name"]]
    bench = facts.benchmarks[0] if facts.benchmarks else None
    # One class for everything below: its test, its filter and its end
    # anchor. A cold start's first, for a window that opens at
    # bindApplication; then one that measures a section. The pieces used to
    # come from different classes of one module. Among those that name no
    # section, one that measures the launch comes before one that only sets
    # `StartupMode`: its window needs nothing picked (#458).
    pick = None
    if bench and bench["classes"]:
        pick = sorted(bench["classes"], key=lambda c: (not c.get("startup"),
                                                       not c.get("metrics"),
                                                       c.get("kind") != LAUNCH))[0]
    # A minified variant's frames come back as R8 named them, and the first
    # round would go on reading `a.b.c`. So its mapping is named, built or
    # not: AGP writes the file when the variant is built, which in gradle
    # mode the first collect does.
    minify = chosen if chosen and chosen.get("minified") else None
    mapping = [f"  mapping: {minify['mapping']}"] if minify else []
    why = (f"; the mapping, written when the variant is built: {_minify_evidence(minify, where)}"
           if minify else "")
    # A build type no script names says where it came from.
    plugin = (f', whose {chosen["build_type"]} {chosen["added_by"]} adds'
              if chosen and chosen.get("added_by") else "")
    if read_id:
        out = ["project:",
               f"  package: {read_id}",
               f'  process: "{read_id}*"',
               *mapping,
               "  _source: derived",
               f'  _evidence: "applicationId in {where}'
               + (f', variant {chosen["name"]}{plugin}' if chosen else "")
               + (f' — the others: {", ".join(others[:6])}' if others else "") + why + '"']
    else:
        # Not read is not derived: a constant or a convention plugin sets it,
        # and a placeholder written as `derived` was taken on trust. The note
        # that says so is `describe`'s: this reads the facts, never adds to them.
        out = ["project:",
               "  package: com.example.app",
               '  process: "com.example.app*"',
               *mapping,
               "  _source: default",
               f'  _evidence: "applicationId not read from {where} — set through a constant '
               f'or a convention plugin; find it there, or in `adb shell pm list packages`'
               + why + '"']
    out += ["",
            "scenario:",
            f"  name: {pick['tests'][0] if pick and pick['tests'] else 'coldStart'}",
            ]
    start = '  start: {name: "bindApplication", _source: default}'
    if pick and pick.get("metrics"):
        pattern = "; a pattern, `%` read as `*`" if "*" in pick["metrics"][0] else ""
        out += [start,
                f'  end: {{name: "{pick["metrics"][0]}", _source: derived, '
                f'_evidence: "TraceSectionMetric in {pick["file"]}{pattern}; '
                f'confirm against a probe"}}']
    elif pick and pick.get("kind") == LAUNCH:
        # A startup macrobenchmark, the project best prepared for the same
        # scenario (#458): the benchmark drives the launch and writes one
        # trace for each repeat, and the launch window reads such traces as
        # it reads the ones `launch` mode records. Only for a class that
        # measures the launch and names no section. `StartupMode` alone may
        # be a scroll that starts cold, and a report about the launch would
        # answer a person who asked about the scroll.
        out.append(
            '  window: {name: launch, _source: derived, _evidence: "StartupTimingMetric in '
            f'{pick["file"]}: the benchmark measures the launch, and the runner below runs it; '
            + _THE_TRACE_DECIDES + '"}')
    elif not bench and facts.app:
        # How the app opens, the one scenario whose window nobody has to
        # name (#446): the runner below starts the launcher Activity, and the
        # window is that launch as the platform records it. Where it ends is
        # the trace's to say, so there is no anchor to pick and no question
        # mark: a person can record this before any trace exists.
        out.append(
            '  window: {name: launch, _source: derived, _evidence: "the launcher Activity '
            f'{facts.app["launcher"]} in {facts.app["manifest"]}, started by the runner below; '
            + _THE_TRACE_DECIDES + '"}')
    elif pick and pick.get("unresolved"):
        out += [start,
                '  end: {name: "?", _source: default, _evidence: "TraceSectionMetric('
                f'{pick["unresolved"][0]}) in {pick["file"]}: its value was not read here. '
                'Put the string it holds, or pick from `echolot probe`"}']
    else:
        out += [start,
                '  end: {name: "?", _source: default, _evidence: "pick from `echolot probe` — '
                'the longest slice that means ready"}']
    out.append("")
    if bench:
        task = measuring_task(bench, facts.variants, chosen)
        cls = pick["name"] if pick else None
        out += ["runner:",
                "  mode: gradle",
                f'  gradle_task: "{task}"' if task else '  gradle_task: "?"',
                ]
        if cls:
            out.append("  gradle_args:")
            out.append(f'    - "-Pandroid.testInstrumentationRunnerArguments.class={cls}"')
        # No `iterations`: the benchmark's measureRepeated sets the count, and
        # collect warns that the key does not reach it.
        if bench.get("baseline_profile"):
            whose = f"; {BASELINE_PROFILE} in its build script gives the module its build types"
        elif bench.get("flavors") or bench.get("build_types"):
            whose = "; the module declares its own variants"
        else:
            whose = ""
        if _pinned(bench):
            whose += "; missingDimensionStrategy runs it against " + ", ".join(sorted(_pinned(bench)))
        out += [f'  _evidence: "MacrobenchmarkRule in {pick["file"] if pick else bench["module"]}'
                + whose + '"']
    else:
        out += ["runner:", "  mode: launch", "  iterations: 5", "  duration_ms: 20000",
                '  _evidence: "no MacrobenchmarkRule in the tree; force-stop + am start"']
    out += ["", "instrumentation:", "  allowed:"]
    # Every one: cut to twelve, the rest dropped out of the list a person
    # confirms, and `mark` refused every site in them.
    out += [f'    - "{p}"' for p in facts.allowed]
    out += ["  temp_prefix: AGENTTMP_", "  cleanup: always"]
    return out


def _kind_words(cls: dict) -> str:
    """Which kind a benchmark class is, in words, and what that leaves for its window.

    Said for every class, so the draft below explains itself: why one class
    got the launch as its window, another its section, and a third the
    question mark. A class that names none of the three is said too. Its
    metrics may be listed in a shared helper, which this did not read.
    """
    kind = cls.get("kind")
    if kind == SECTION:
        named = ", ".join(f"`{m}`" for m in cls.get("metrics") or [])
        unread = ", ".join(f"`{m}`" for m in cls.get("unresolved") or [])
        words = ("measures a section (`TraceSectionMetric`): "
                 + (f"{named}, where its window can end" if named
                    else "its name is set elsewhere and was not read"))
        if unread:
            words += (f"; {unread}: " if named else f", by {unread}: ") + (
                "a constant whose value was not found in the project")
        if cls.get("measures_launch"):
            words += ("; it measures the launch too (`StartupTimingMetric`), and the "
                      "section comes first")
        return words
    if kind == LAUNCH:
        return ("measures the launch (`StartupTimingMetric`): its window can be the launch, "
                "with no end to pick")
    if kind == STARTUP_MODE:
        return ("sets `StartupMode` and names no `StartupTimingMetric`: it may measure no "
                "launch, so its window needs an end picked from a probe")
    return ("names no `StartupTimingMetric`, `TraceSectionMetric` or `StartupMode` in its own "
            "file: its window needs an end picked from a probe")


def render(facts: Facts) -> str:
    out = ["# The repository", ""]
    app = facts.app
    if app:
        out.append(f"App module `{app['module']}` ({app['dir']}) · applicationId `{app.get('application_id') or '?'}`"
                   + (f" · namespace `{app['namespace']}`" if app.get("namespace") else "")
                   + (f" · process `{app['process']}`" if app.get("process") else "")
                   + f" · launcher `{app['launcher']}`")
        if app["profileable"]:
            line = "yes — `<profileable android:shell=\"true\"/>` in " + app["manifest"]
        elif app.get("profileable_by"):
            added = {b["name"]: b["added_by"] for b in facts.build_types if b.get("added_by")}
            own = [b for b in app["profileable_by"] if b not in added]
            made = [b for b in app["profileable_by"] if b in added]
            said = []
            if own:
                said.append(", ".join(f"`{b}`" for b in own) + ", which sets `isProfileable`")
            if made:
                said.append(", ".join(f"`{b}`" for b in made) + f", which {added[made[0]]} "
                            "makes profileable")
            line = "yes for " + "; ".join(said) + f"; {app['manifest']} has no tag"
        else:
            line = "**no** — release-like builds will trace without application slices"
        out.append("profileable: " + line)
    if facts.variants:
        out += ["", "## Variants", ""]
        out.append("| variant | installs as | debuggable | measure on it |")
        out.append("|---|---|---|---|")
        for v in facts.variants:
            out.append(f"| {v['name']} | `{v.get('application_id') or '?'}` | "
                       f"{'yes' if v.get('debuggable') else ('no' if v.get('debuggable') is False else '?')} | {v['measure']} |")
        added = [b["name"] for b in facts.build_types if b.get("added_by") and b.get("implicit")]
        if added and app:
            out.append("")
            out.append(", ".join(f"`{b}`" for b in added)
                       + f" {'is' if len(added) == 1 else 'are'} not in {app.get('build_script')}: "
                       f"{BASELINE_PROFILE}, applied there, adds them for each build type that is "
                       "not debuggable — not debuggable and profileable, the benchmark one "
                       "minified as its build type is, the other built without R8 to generate "
                       "profiles.")
        chosen = preferred(facts.variants)
        if chosen:
            out.append("")
            out.append(f"Start from **{chosen['name']}**: {chosen['measure']}.")
            if chosen.get("minified"):
                there = (Path(facts.root) / chosen["mapping"]).is_file()
                script = (app or {}).get("build_script") or "build.gradle"
                out.append(f"It is minified ({_minify_evidence(chosen, script)}), so the config below "
                           f"names its R8 mapping, `{chosen['mapping']}`"
                           + (" — there now." if there else
                              " — not there yet: AGP writes it when the variant is built."))
    out += ["", "## Benchmarks", ""]
    if not facts.benchmarks:
        out.append("_no MacrobenchmarkRule in the tree — `runner.mode: launch` drives a cold start by itself_")
    for b in facts.benchmarks:
        out.append(f"`{b['module']}` — drives `{b.get('package_name') or '?'}`"
                   + (", a cold start" if b.get("startup") else ""))
        if b.get("package_by"):
            by = b["package_by"]
            out.append(f"- no string in its code names the package: `{b['package_name']}` is what "
                       f"`{by['variant']}` of `{by['target']}`, the module its targetProjectPath "
                       "names, installs as")
        for c in b["classes"]:
            out.append(f"- `{c['name']}` ({c['file']}): " + (", ".join(c["tests"]) or "no @Test")
                       + " — " + _kind_words(c))
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
    probe = ("echolot collect -c echolot.yml" if facts.benchmarks
             else "echolot collect -c echolot.yml -n 1")
    out += ["", "## A config to start from", "",
            "Every value says where it came from; confirm the anchors against a probe "
            f"trace (`{probe}`, then `echolot probe`).", "",
            "```yaml", *skeleton(facts), "```"]
    return "\n".join(out)


def to_json(facts: Facts) -> dict[str, Any]:
    return asdict(facts)
