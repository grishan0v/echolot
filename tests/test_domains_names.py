"""`echolot domains` on the names it wrote wrong, missed or counted twice:
escapes and templates, `traceAsync`, the end of a section, a logger's
`trace`, a local `val` above the call, and a name with GLOB characters.
"""

from __future__ import annotations

import fnmatch
import sqlite3
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import domains as dm  # noqa: E402
from tests.support import check  # noqa: E402


def _repo(root: Path, files: dict[str, str]) -> Path:
    (root / "build.gradle.kts").write_text("", encoding="utf-8")
    for rel, text in files.items():
        path = root / "app/src/main/java" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _map(root: Path) -> tuple[dict[str, list[dm.Site]], int, str]:
    sites, stats = dm.scan(root)
    by_name: dict[str, list[dm.Site]] = {}
    for s in sites:
        by_name.setdefault(s.name, []).append(s)
    return by_name, sum(s.dynamic for s in stats.values()), "\n".join(dm.render(sites, stats, root))


def test_escapes_are_read_and_templates_are_built_at_runtime(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "Bind.kt": "import android.os.Trace\nfun bind() {\n"
                   '    Trace.beginSection("bind ${holder.type}")\n'
                   '    Trace.beginSection("load $id")\n'
                   '    Trace.beginSection("cost \\$5")\n}\n',
        "Quote.java": "class Quote {\n    void q() {\n"
                      "        Trace.beginSection(\"it\\'s\");\n"
                      "        Trace.beginSection(\"price $5\");\n    }\n}\n"})
    names, dynamic, text = _map(root)
    check("the strings the code holds", set(names) == {"cost $5", "it's", "price $5"}, sorted(names))
    check("and the templates counted as built at runtime", dynamic == 2
          and "Another 2 calls build the name at runtime" in text, text)
    section = yaml.safe_load(text[text.index("domains:"):])
    check("a section that loads", sorted(e["slice"] for e in section["domains"])
          == ["cost $5", "it's", "price $5"], section)


def test_trace_async_with_a_literal_is_mapped(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "Marks.kt": 'object Marks { const val LOAD = "collection_load" }\n',
        "Refresh.kt": "import androidx.tracing.trace\nimport androidx.tracing.traceAsync\n"
                      'suspend fun refresh() = traceAsync("collection_refresh", 1) { fetch() }\n'
                      "suspend fun load() = traceAsync(Marks.LOAD, 2) { fetch() }\n"
                      'fun map() = trace("collection_map") { convert() }\n'
                      "suspend fun named(name: String) = traceAsync(name, 3) { fetch() }\n"})
    names, dynamic, _ = _map(root)
    check("all three", set(names) == {"collection_refresh", "collection_load", "collection_map"},
          sorted(names))
    check("and one with a variable counted", dynamic == 1, dynamic)


def test_the_end_of_a_section_and_a_logger_s_trace_are_no_sites(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "Adapter.kt": "import android.os.Trace\nclass Adapter {\n    fun onShown() {\n"
                      "        Trace.endAsyncSection(Marks.LOAD, 7)\n"
                      "        AppTraces.stop(Marks.LOAD)\n    }\n}\n",
        "Loader.kt": "import android.os.Trace\nclass Loader(private val traces: Traces) {\n"
                     "    fun load() {\n        Trace.beginAsyncSection(Marks.LOAD, 7)\n"
                     "        traces.trace<Items>(Marks.SPLIT) { fetch() }\n    }\n}\n",
        "Marks.kt": 'object Marks {\n    const val LOAD = "collection_load"\n'
                    '    const val SPLIT = "collection_split"\n'
                    '    const val LOAD_MSG = "loading"\n}\n',
        "Sync.kt": "class Sync {\n    fun run() {\n        log.trace(Marks.LOAD_MSG)\n"
                   "        LOGGER.trace(message)\n    }\n}\n"})
    names, dynamic, text = _map(root)
    check("one site for the section, at its begin",
          [s.path.name for s in names.get("collection_load", [])] == ["Loader.kt"], names)
    check("a wrapper's trace still counts", "collection_split" in names, sorted(names))
    check("and a logger's does not", "loading" not in names and dynamic == 0, (sorted(names), dynamic))
    check("the header counts calls, not ends", "# Instrumentation: 2 tracing calls" in text, text)


def test_a_logger_with_a_variable_is_no_instrumentation(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"Sync.kt": "class Sync {\n    fun run(message: String) {\n"
                                       "        log.trace(message)\n"
                                       "        log.trace(message.uppercase())\n    }\n}\n"})
    _, dynamic, text = _map(root)
    check("nothing built at runtime", dynamic == 0 and "Another" not in text, text)


def test_the_hint_names_the_function_around_the_call(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "StoreRepository.kt": "import android.os.Trace\nclass StoreRepository {\n"
                              "    val cached by lazy {\n"
                              '        Trace.beginSection("store_cached")\n    }\n'
                              "    fun load() {\n        val items = dao.all()\n"
                              '        Trace.beginSection("store_load")\n'
                              "        val sorted = run {\n"
                              '            Trace.beginSection("store_sort")\n        }\n    }\n}\n'
                              "class LoadItems {\n    operator fun invoke() {\n"
                              '        Trace.beginSection("store_invoke")\n    }\n}\n'
                              "@Suppress(\"unused\") internal suspend fun <T> withTrace() {\n"
                              '    Trace.beginSection("store_generic")\n}\n'})
    names, _, _ = _map(root)
    symbols = {name: found[0].symbol for name, found in names.items()}
    check("the function, not the local above",
          symbols == {"store_cached": "val cached", "store_load": "fun load",
                      "store_sort": "fun load", "store_invoke": "fun invoke",
                      "store_generic": "fun withTrace"}, symbols)


def test_a_name_with_glob_characters_matches_itself(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"Cache.kt": "import android.os.Trace\nfun hit() {\n"
                                        '    Trace.beginSection("cache[hit]")\n'
                                        '    Trace.beginSection("any*?")\n}\n'})
    _, _, text = _map(root)
    entries = [e["slice"] for e in yaml.safe_load(text[text.index("domains:"):])["domains"]]
    db = sqlite3.connect(":memory:")
    for name, glob in zip(("any*?", "cache[hit]"), entries, strict=True):
        check(f"{name} as its own GLOB",
              db.execute("SELECT ? GLOB ?", (name, glob)).fetchone()[0] == 1
              and fnmatch.fnmatchcase(name, glob), entries)
    check("and not another name's", db.execute("SELECT 'anyXY' GLOB ?", (entries[0],))
          .fetchone()[0] == 0, entries)
