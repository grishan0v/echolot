# `mark`: the first markers, from the platform's vocabulary

[← Docs index](README.md) · [README](../README.md)

```bash
echolot mark                     # where the first markers go, and why there
echolot mark --apply             # put the applicable ones in
echolot collect -c echolot.yml -n 3 && echolot analyze .echolot/traces/*.perfetto-trace -c echolot.yml
echolot mark --remove            # take every one of them out, byte for byte
```

## The gap it closes

A project with no instrumentation gives the detectors nothing of the
application's to name. `main_thread_block` says `bindApplication`,
`activityStart`, `Compose:recompose`; `uninstrumented_cpu` says
`arch_disk_io_0..3` burned 900 ms. Those are facts, and none of them is a
file. `domains` has nothing to map them to, and the agent's next move — add
`AGENTTMP_` markers, re-record — needs a place to put them. In two hunts out
of two the agent found that place by reading the application: twenty-odd
`cat` and `sed -n` calls, forty to sixty percent of everything that entered
its window, before a single marker was placed.

`mark` does that step. It answers "where do five to seven markers go so that
the next report names this application's code" — and it answers the same
way on any project, which is the only form in which it belongs in a tool
that promises stable results.

## What it binds to, and what it refuses to

Everything `mark` looks at is fixed by the platform or a library, never by
the project:

| source | what it is | example |
|---|---|---|
| `manifest` | the launcher Activity is the `<intent-filter>` with `MAIN` and `LAUNCHER`; the Application class is `android:name` on `<application>` | structure, no names |
| `lifecycle` | `onCreate` — the SDK's method name, whatever the class is called | `override fun onCreate(`, `protected void onCreate(` |
| `api` | exact strings from someone else's library | `setContent {`, `setContentView(`, `Room.databaseBuilder(`, `startKoin {`, `@HiltAndroidApp` |
| `call-from-setContent` | the composables invoked inside `setContent { }`, kept only when `@Composable fun Name(` is in this project's sources | `AppTheme { AppNavHost() }` → both, if defined here |

There is no rule about `*ViewModel`, `*Repository`, `*Screen`, `*Fragment`
or any other convention. A project where the ViewModel is called `Presenter`
and the screen is called `Page` gets the same skeleton as one that follows
the Google samples — the self-check builds one tree with sensible names and
the same tree with nonsense names and requires the same proposals, source
for source.

## What it says when it cannot see

- no `AndroidManifest.xml` with a launcher under `src/main` — "no app entry
  point in this tree", nothing proposed
- two modules with a launcher (an app and a wear app) — an ambiguity that
  stops the command until `--module` or `project.package` settles it
- the launcher Activity does not override `onCreate` — said, with the base
  class it inherits from, because the override may live there
- the Application class is not in the manifest — said; `bindApplication` is
  the framework's alone
- a method with a `return` in its body — proposed but not applicable: a
  begin/end pair would lose its end on that path; mark it by hand
- a composable, a Room builder, a Koin block — proposed with the reason it
  is not applied mechanically (a call site, a builder chain), and where to
  wrap by hand

Each row carries its source, so the reader sees how firm the ground is. The
output is sorted by kind, then path, then line, and is byte-identical for
the same tree. The cap is seven proposals — a skeleton, not a survey — and
the count beyond it is printed.

## `--apply` and `--remove`

`--apply` inserts, at each applicable site, a begin line right after the
block's `{` and an end line right before its `}`, indented like the body:

```kotlin
override fun onCreate(savedInstanceState: Bundle?) {
    android.os.Trace.beginSection("AGENTTMP_activity_oncreate") // echolot:mark
    super.onCreate(savedInstanceState)
    setContent {
        android.os.Trace.beginSection("AGENTTMP_set_content") // echolot:mark
        AppTheme { AppNavHost() }
        android.os.Trace.endSection() // echolot:mark
    }
    android.os.Trace.endSection() // echolot:mark
}
```

`android.os.Trace` is the framework's, so no dependency is added; every
inserted line ends with `// echolot:mark`; Java gets a semicolon. `--remove`
deletes exactly the tagged lines under `--root` and restores every file byte
for byte — the self-check applies, removes and compares. Applying twice adds
nothing. Braces are matched on a view of the file with strings and comments
blanked out, so a `}` inside a literal does not count.

`instrumentation.allowed` from `echolot.yml` is honoured: a site outside it
is still shown (the joint is where it is) but not applied, with the note to
mark the nearest allowed caller instead.

## `--from-anr`: targets from a stack instead of the manifest

```bash
echolot mark --from-anr report.txt
```

Everything above proposes where instrumentation *usually* belongs on a project
that has none. A stack from a freeze is not a guess: it names the methods that
were on the thread when the system gave up, with the file and the line the
compiler wrote into each frame. Everything after the plan is the same code and
the same tag, so `--apply` and `--remove` are unchanged.

Expect most proposals to be refused, and read the reasons rather than working
around them:

- **the line falls inside another function than the frame names** — the
  compiler moved it, and bracketing where the line landed would name one
  function and measure another;
- **a `return` in the body**, and **a body on one line** — the same two
  refusals as above;
- **most frames landing nowhere** — one sentence naming the build the report
  came from. The working tree is not that build, and no line number in the
  report means anything until it is.

See [ANRs](anr.md) for the rest of that path.

## The loop it fits into

```
analyze          → the report names system slices and threads; domains is empty
mark --apply     → 3–5 markers at the entry points, one command, ~1 KB
collect, analyze → the report now says AGENTTMP_set_content 1200 ms self
                   and domains points at MainActivity.kt:69
read one place   → the file the report named — and, if needed, a second,
                   pointed layer of markers inside it by hand
mark --remove    → cleanup, and grep -rn AGENTTMP_ to confirm
```

The command finds the entry and the first hop; the trace says where the
weight is; the agent opens the one file the trace named. That order is what
`perf-hunter.md` asks for.

## Two things that need no markers at all

- **`androidx.compose.runtime:runtime-tracing`** in the app module puts
  composable names into the trace with no code touched. `mark` says when it
  is missing; for a Compose app it is the first thing to add.
- **Callstack sampling** (Perfetto's `linux.perf` on Android 12+) names Java
  frames on a hot thread from symbols, no instrumentation, no naming — with
  the caveats of profileable builds and R8 mapping. Not wired in yet; the
  convention-free bridge for the `uninstrumented_cpu` case.

## Where it will be wrong

Regexes over Kotlin and Java see structure, not semantics. A `BaseActivity`
in a library that owns the real `onCreate`, everything created through a DI
graph with no direct call, generated code, a Flutter or React Native shell —
`mark` will find the entry, say what it cannot follow, and stop. That is the
intended failure: three markers and an honest note beat seven guesses. From
there the trace leads, one hop at a time.
