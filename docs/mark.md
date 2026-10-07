# `mark`: the first markers, from the platform's vocabulary

[← Docs index](README.md) · [README](../README.md)

```bash
echolot mark                     # where the first markers go, and why there
echolot mark --apply             # put the applicable ones in
echolot collect -c echolot.yml -n 3 && echolot analyze .echolot/traces/coldStart_iter*.perfetto-trace -c echolot.yml
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
the project. Each row it prints carries one of these tags:

| tag | what it is | example |
|---|---|---|
| `manifest+lifecycle` | the manifest names the class — the launcher Activity is the one whose `<intent-filter>` has `MAIN` and `LAUNCHER`, the Application class is `android:name` on `<application>` — and `onCreate`, the SDK's method name, is the place in it, whatever the class is called | `override fun onCreate(`, `protected void onCreate(` |
| `api` | exact strings from someone else's library | `setContent {`, `setContentView(`, `Room.databaseBuilder(`, `startKoin {` |
| `call-from-setContent` | the composables invoked inside `setContent { }`, kept only when `@Composable fun Name(` is in this project's sources | `AppTheme { AppNavHost() }` → both, if defined here |
| `anr` | a frame of a stack from a freeze, with `--from-anr`: the function around the line the compiler wrote into it | see `--from-anr` below |
| `jdk` | a thread or a pool the JDK's default factory will name, with `--pools` | `Executors.newSingleThreadExecutor()`, `Thread(task)` |

There is no rule about `*ViewModel`, `*Repository`, `*Screen`, `*Fragment`
or any other convention. A project where the ViewModel is called `Presenter`
and the screen is called `Page` gets the same skeleton as one that follows
the Google samples — the self-check builds one tree with sensible names and
the same tree with nonsense names and requires the same proposals, source
for source.

## What it says when it cannot see

- no `AndroidManifest.xml` with a launcher under `src/main` — "no launcher
  Activity in any AndroidManifest.xml under src/main — this tree has no app
  entry point to mark", nothing proposed, and `--root` is how to point it at
  the app
- two modules with a launcher (an app and a wear app) — an ambiguity that
  stops the command until `--module` or `project.package` settles it
- two launcher activities in one manifest — the same stop, since `--module`
  chooses between modules and not between the entry points of one; the
  proposals are for the first, to mark by hand. An `<activity-alias>` is its
  `targetActivity`, not a second activity, and an `<activity … />` that
  closes itself has no intent-filter to be the launcher with
- the launcher Activity does not override `onCreate` — said, with the base
  class it inherits from (`: Base()` in Kotlin, `extends Base` in Java),
  because the override may live there
- the Application class is not in the manifest, or does not override
  `onCreate` — said, with what runs at `bindApplication` all the same: the
  Application's constructor, the ContentProviders and the libraries'
  initializers, which `app_init` lists
- an Application class with `@HiltAndroidApp` — a note, no row: the graph is
  generated, its cost sits inside `Application.onCreate`, and there is nothing
  separate to mark
- a block that cannot take a begin/end pair of whole lines — proposed but not
  applicable, with the reason; the cases are under `--apply` below
- a composable, a Room builder, a Koin block — proposed with the reason it
  is not applied mechanically (a call site, a builder chain), and where to
  wrap by hand

Each row carries its source, so the reader sees how firm the ground is. The
output is sorted by kind, then path, then line, and is byte-identical for
the same tree. The cap is seven proposals — a skeleton, not a survey — and
the count beyond it is printed.

## `--apply` and `--remove`

`--apply` inserts, at each applicable site, a begin line under the line that
holds the block's `{` and an end line over the line that holds its `}`,
indented like the body. In a function body the end goes in a `finally`, so the
section closes however the body is left — a `return`, a `throw`, an exception
from a callee, or no end at all after a `while (true)`, which in Java would
make a bare end line unreachable and the file stop compiling. A lambda's and a
composable's end go in bare: the Compose compiler refuses a `try` around
composable calls.

```kotlin
override fun onCreate(savedInstanceState: Bundle?) {
    android.os.Trace.beginSection("AGENTTMP_activity_oncreate") // echolot:mark
    try { // echolot:mark
    super.onCreate(savedInstanceState)
    setContent {
        android.os.Trace.beginSection("AGENTTMP_set_content") // echolot:mark
        AppTheme { AppNavHost() }
        android.os.Trace.endSection() // echolot:mark
    }
    } finally { android.os.Trace.endSection() } // echolot:mark
}
```

`android.os.Trace` is the framework's, so no dependency is added; every
inserted line ends with `// echolot:mark`; Java gets a semicolon. Each one is
a whole line put in between two of the file's own, ending the way the line
above it ends, and nothing else in the file changes — a CRLF file stays CRLF.
`--remove` deletes exactly those lines under `--root`, the ones carrying the
tag in the shape `--apply` writes, and gives every file back byte for byte:
the self-check applies, removes and compares, and `tests/test_mark_edits.py`
does the same over generated Kotlin and Java sources, CRLF files among them.
A line that carries the tag in any other shape has more on it than a marker,
so it stays where it is and `--remove` lists it with its file and line, to be
cleaned by hand. Applying twice adds nothing: a block whose begin and end are
already there is named as marked, and a block around one that is marked still
gets its own pair. `--remove` walks every source file under `--root`, the ones
outside a `src/` too, since `--from-anr` marks wherever a frame was placed. A
file that will not take the write is named and the run goes on. Braces are matched on a view of
the file with strings and comments blanked out, so a `}` inside a literal
does not count.

That promise decides what is refused. A block is proposed but not applied,
with the reason on its row, when a pair of whole lines cannot go in without
changing a line of the project's:

- **a `return` in a lambda or a composable's body** — its end goes in bare
  and would be skipped on that path;
- **a file in shared Kotlin source**, `src/commonMain` and the other source
  sets but `androidMain` — `android.os.Trace` does not resolve there;
- **a marker longer than 127 characters**, which `Trace.beginSection` throws
  on while recording — a name from a generated class is cut to fit, and a
  prefix that leaves no room is refused;
- **the whole body on one line**, `setContent { AppRoot() }` — there is no
  line between the braces to put anything on;
- **code after the `{`, or before the `}`, on the brace's own line** —
  `setContent { AppTheme {`, `} }` — the new line could only go in by
  splitting that line, and `--remove` deletes lines; it cannot join one back
  together. A comment after the `{` is not code: the begin line goes in under
  it, and the comment stays where it was.

Put the code on a line of its own and run `mark` again, or mark by hand.

`instrumentation.allowed` from `echolot.yml` is honoured, globs included: each
entry is matched path segment by path segment, so `feature/*/src/main` — the
form `scan` writes — covers every module directly under `feature/`. A site
outside it is still shown (the joint is where it is) but not applied, with the
note to mark the nearest allowed caller instead. A frame from `--from-anr` is
shown the same way rather than left out, and a place to name from `--pools`
says it is outside.

The config is the one `-c` names, else `echolot.yml` in the working directory
or under `--root`. A `-c` that names no file stops `mark` with exit 2, and so
does a config that does not load, except for `--remove`: an empty
`instrumentation.allowed` allows every place, so a mistake elsewhere in the
file must not lift the guard. With no config at all, `mark` says on stderr that
nothing is guarded.

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
- **a `return` in the body**, **a body on one line**, and **code sharing a
  line with the `{` or the `}`** — the refusals of `--apply` above;
- **a frame entered through a lambda** — `onCreate$lambda$0`,
  `Repo$refresh$1.invokeSuspend`, javac's `lambda$flush$0`. The lambda often
  runs later than the function that made it, a click listener or a `launch`,
  so a pair around that function would time making it; mark inside the
  lambda by hand;
- **a `suspend fun`** — `beginSection` and `endSection` act on the calling
  thread, and the function can resume on another after a `withContext` or a
  `delay`; mark a stretch with no suspension point by hand;
- **a frame outside `instrumentation.allowed`** — shown and not applied,
  like any site outside; the frame under it may be the allowed caller;
- **most frames landing nowhere** — one sentence naming the build the report
  came from. The working tree is not that build, and no line number in the
  report means anything until it is.

See [ANRs](anr.md) for the rest of that path.

## `--pools`: naming the threads instead of marking the work

The other two ways in start from a place in the code — the manifest, or a
stack from a freeze. This one starts from the report:

```
uninstrumented_cpu   pool-7-thread-1   3184 ms   98% of CPU outside slices
```

Nothing in the repository is called `pool-7-thread-1`. The JDK named it, from
the default factory that `Executors.new*` and a bare `Thread(` — or Kotlin's
`Thread { … }`, whose trailing lambda is the Runnable — hand their threads
to. A `ForkJoinPool` and `Executors.newWorkStealingPool()` name theirs
`ForkJoinPool-N-worker-M`, and the row says so. Grepping for the name finds nothing, and that is where a hunt
stalls.

Marking the work is the wrong first move there — you do not know what the work
is, which is the complaint. Naming the pool is cheaper and does not need to
know: one edit where the pool is made covers everything that will ever run on
it, and **every detector already groups by thread name**, so one round turns
the whole report from `pool-7-thread-1` into `cart-queue`. Only then are
markers worth placing, and by then you know where.

```bash
echolot mark --pools --root .
```

```
· app/src/main/java/…/di/RepositoryModule.kt:355   (name it)  [jdk]  Executors.newSingleThreadExecutor
· app/src/main/java/…/di/SchedulerModule.kt:24     (name it)  [jdk]  ThreadPoolExecutor
```

Nothing here is applicable, and the placeholder says so. The tool can find the
site — `Executors.new*` is an exact string like `Room.databaseBuilder(` — and
it cannot write the name: on two real projects a name taken from the
surrounding code came out as `provideproductr`, `onauthenticatio` and
`capacity`, because the enclosing symbol is a Dagger provider or a local
variable while what the pool is *for* is in the call it is passed to.

**At most 15 characters.** Linux truncates a thread's `comm` and the trace
carries what is left — `pool-12-thread-1` arrives as `pool-12-thread-`, and
the main thread of `com.example.myapp` as `m.example.myapp`. `cart-queue`,
not `CartQueueProcessorExecutor`.

A thread that is given a name is not a finding, and neither is a pool whose
factory names its threads. What counts as given:

- a string among `Thread(`'s own arguments — `Thread(r, "io")`,
  `new Thread(r, "io-" + n)`, `Thread("sync")`, `object : Thread("sync")`;
- a second argument of any kind — `Thread(r, name)` in a factory that was
  handed the name — since every constructor of the JDK's that takes two or
  more arguments takes a name, except `(ThreadGroup, Runnable)`;
- a factory built with `ThreadFactoryBuilder().setNameFormat(…)`, or one with
  a `newThread(` of its own;
- a factory that makes its threads with `Thread(` — Kotlin's
  `Executors.newFixedThreadPool(2) { r -> Thread(r, "io") }` passes it
  outside the parentheses. That `Thread(` decides: named, there is no row;
  not, it is the row, since it is where the name goes;
- a `ThreadFactory` handed to `Executors.new*` where it takes one — the
  second argument of `newFixedThreadPool` and `newScheduledThreadPool`, any
  argument of the others — whatever it is: a variable, the project's own
  class. The JDK's default name is then not what the threads get;
- `HandlerThread`, which takes a name as its first argument.

Counting those found fifteen sites on a codebase where four were real.

## The loop it fits into

```
analyze          → the report names system slices and threads; domains is empty
mark --apply     → up to 3 markers at the entry points (Application.onCreate,
                   the launcher's onCreate, setContent), one command, ~1 KB
collect, analyze → the report now says AGENTTMP_set_content 1200 ms self
                   and domains points at MainActivity.kt:69
read one place   → the file the report named — and, if needed, a second,
                   pointed layer of markers inside it by hand
mark --remove    → cleanup, and grep -rn -e AGENTTMP_ -e 'echolot:mark' to confirm
```

The command finds the entry and the first hop; the trace says where the
weight is; the agent opens the one file the trace named. That order is what
`perf-hunter.md` asks for.

## Two things that need no markers at all

- **`androidx.compose.runtime:runtime-tracing`** in the app module puts
  composable names into the trace with no code touched. `mark` says when a
  Compose app is missing it — one whose app module calls `setContent {` or
  declares a `@Composable` — and for such an app it is the first thing to
  add. An app without Compose is not told about it.
- **Callstack sampling** (`runner.sampling`, Perfetto's `linux.perf`) names
  the Java and Kotlin frames on a hot thread from symbols, no instrumentation,
  no naming. The build has to be profileable, and a minified one needs its R8
  mapping as `project.mapping`.
  `uninstrumented_cpu` reads it: a sampled row names what ran in the blind
  spot and the project's method under it, which is where markers go. See
  [Collecting](collecting.md#callstack-sampling).

## Where it will be wrong

Regexes over Kotlin and Java see structure, not semantics. A `BaseActivity`
in a library that owns the real `onCreate`, everything created through a DI
graph with no direct call, generated code, a Flutter or React Native shell —
`mark` will find the entry, say what it cannot follow, and stop. That is the
intended failure: three markers and an honest note beat seven guesses. From
there the trace leads, one hop at a time.

`--pools` reads the call and nothing after it. A name set once the thread
exists — `Thread(r).apply { name = "io" }`, `t.setName("io")` — is not seen,
and that thread is listed anyway; a `Thread(group, runnable)` has two
arguments and is taken for named when it is not. A factory handed to a pool
constructor (`ThreadPoolExecutor(…)`) is not looked for, and that pool is
listed as `pool-N-thread-M`.
