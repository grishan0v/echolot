# Collecting traces

[← Docs index](README.md) · [README](../README.md) · **English** · [Русский](ru/collecting.md)

```bash
echolot collect -c echolot.yml -n 5
echolot analyze .echolot/traces/coldStart_iter*.perfetto-trace -c echolot.yml
```

The first command captures N repeats of a scenario into `.echolot/traces/`
beside the config, as `<scenario>_iter000.perfetto-trace` and on; the second
merges them into one report. `coldStart` stands for the config's
`scenario.name`. The directory keeps the traces of every scenario recorded
into it, so the glob names one: `*.perfetto-trace` would merge two scenarios
into one report, and every median in it would mix them. That directory is
the one `echolot` counts and `hunt` sets aside, and `compare` compares the
reports `analyze` builds from it, so the default is the place to keep them; `-o` exists for the rare trace that belongs elsewhere.

Repeating is not belt-and-braces. A single run cannot tell a regression from a
random spike — on a live cold start the spread between iterations reaches tens
of percent. Both threshold calibration and report merging stand on the
distribution across repeats; without them, both are guessing.

A second `collect` into the same directory does not overwrite the first: the
existing `<scenario>_*` set moves into a sibling directory stamped with
when it was recorded (`.echolot/traces/cold_start-20260815-153720/`), and the
log says so. The set from before a change is the baseline every after-the-fix
comparison stands on, and it is the first thing lost otherwise — a
macrobenchmark's output directory is cleaned by gradle on the next run, a
rename inside it goes with the cleaning. If you record around the tool, copy
the traces out before re-recording; `echolot reflect` flags a re-record that
did not.

The set moves when the first new trace is about to take its place, and not
before. A `collect` that stops earlier — no device, a typo in the mode, a
gradle build that fails, a benchmark that writes nothing — leaves it where it
was, so the traces line of `echolot` and the next `analyze` still see it.
Everything of this scenario moves, a probe capture saved beside the set
included; another scenario's traces stay, and so do those of a scenario whose
name merely begins with this one's: re-recording `startup` leaves
`startup_warm_*` alone.

Beside each trace it records, `collect` keeps a short account of how it
recorded it, `<trace>.recording.json`: the runner, the reset policy,
whether the force-stop before the repeat found the app still running, and
how the phone was powered (see [Power](#power)). The
trace cannot say any of that itself, and `analyze` carries it into the
report's `recording` block, where `compare` sets it against the other side's
(see [How the two were recorded](compare.md#how-the-two-were-recorded)). It
moves with its trace when a set is set aside; copy it along if you move
traces by hand, or that set's runner reads as unknown.

The account also keeps two things `collect` reads of the device once a
round, before the first repeat, in launch and command modes:

- `animation_scales`, the window, transition and animator scales of
  Developer options. An animation lasts its length times its scale: on an
  SM-A515F a list scroll recorded with all three at 5 against one at 1 gave
  5 to 17 rows that held in `compare`, and nothing said the device had
  animated five times slower. UI-test setups set them to 0 on purpose, so
  `collect` records whatever they are rather than refusing.
- `always_finish_activities`, "Don't keep activities": the system destroys
  an activity as soon as it leaves the screen, so a return to a living
  process creates it again. On the SM-A515F `HOME` and `am start` of the
  launcher activity came back `WARM` in 431–469 ms with it on and `HOT` in
  144–179 ms with it off. It changes little in a cold start and everything
  in a scenario that returns to the app (`reset_policy: none`, or a command
  that goes home and back).

The option is read from ActivityManager, not from the setting it keeps.
`adb shell settings put global always_finish_activities 0` changes the
setting and not the running system, which reads it at boot: the option stays
on, and the account says so. Switch it in Developer options, which change
both, or restart the phone after `settings put`. The scales take effect at
once either way.

The report's header names any scale that is not 1, and says when the option
was on (see [What the device was set to do](analysing.md#what-the-device-was-set-to-do));
`compare` says when either differs between the two sides. In gradle mode
neither is read: the macrobenchmark drives the device, and a run with AGP's
`animationsDisabled` turns the scales to 0 for its own length.

The account also keeps how the repeat started, under `launch`: which repeat
of the set it was and which recording of that repeat (`attempt`, 2 and on for
one recorded again), `am start -W`'s `state` (`COLD`, `WARM`, `HOT`; `null` in
command mode) and `total_time_ms`, `cold` (`true` for a stopped app that
started cold, `false` for one that did not, `null` where there is nothing to
judge by), and `notes`, the lines `collect` printed under the repeat. Before,
they were in its output alone, and an agent that had cut that output with
`tail` recorded a whole round again to read them back. `echolot` reads them
off the set with no device and prints a `starts` line:
`startup: 5 COLD · set aside, not cold: startup_iter001-warm.perfetto-trace (WARM)`.

## Three modes

```yaml
runner:
  mode: launch              # launch | command | gradle
  iterations: 5
  duration_ms: 12000
  reset_policy: force-stop  # force-stop (cold) | none (nothing between repeats)
```

`duration_ms` is sized for a start, and a freeze does not fit in it. Hunting
one needs the block plus the five seconds the system waits before declaring an
ANR — forty-five is a working number, and twelve finds neither. See
[ANRs](anr.md).

The numbers are digits alone, in the unit the key's name ends with:
`duration_ms: 12000`. A value such as `12s` stops `collect` with a sentence
before the device is touched. A `reset_policy` other than `force-stop` or
`none` gets a warning that says `force-stop` will run, and then it does.

Launch and command modes build nothing: they record the app that is
installed on the phone. After a change to its code — `echolot mark --apply`,
a marker added by hand, `mark --remove` — build and install the variant the
config measures before the next `collect`, or the round measures the old
build. `mark --apply` ends with the gradle task that does it. When the code
has temporary markers, `collect` reads the build on the phone for them and
stops before a round that could not hold them
([The build on the phone](#the-build-on-the-phone)). `analyze` says it
afterwards both ways: when none of the code's temporary markers is in the
traces, and when the traces hold markers the code no longer has. Gradle mode
builds and installs the app itself, as part of the macrobenchmark's task.

### `launch` — we drive it

HOME, `force-stop`, a wait for the app's process to be gone, the cooldown when
`runner.cooldown` asks for one, a last look for the process, start recording,
`am start -W`, wait, pull. A cold start. With `reset_policy: none` nothing is
reset between repeats, and that is not a warm start: the app is still in front
from the repeat before, so `am start -W` has nothing to start and only the
first repeat can measure one. `collect` says so before it records more than
one, marks each repeat that started nothing with `[!]`, and ends with a `[!]`
line that names those traces: they hold no launch.

HOME comes first because the app is still on screen from the repeat before.
Stopped there, an app whose activity is bound to a service in a second
process of its own was started again by the system at once, for that
activity: `am_proc_start … top-activity` in `adb logcat -b events`, 65–95 ms
after the kill on an A51. `am start -W` then said `WARM`, and the trace held
neither the process start nor `bindApplication`. From the background, the
same app stayed dead after the force-stop, 8 times of 8. The wait after it
lasts until `pidof` finds no process of the app, five seconds at most, and
half a second more: the old process was gone at the first look, and the new
one was there a moment later.

The device cools after the stop, not before it. Cooled with the app in front,
a job the app had scheduled during the wait was due by the time the
force-stop came, and the system started the process for it 95–112 ms after
the kill (`am_proc_start … service` in the events log). On an A51, 47 of 59
repeats that had waited for the device began warm, against 0 of 51 that had
not; stopped first, 70 starts of the same app began cold 70 times, 31 of them
after a wait. Right before the recording `collect` looks for the process once
more, and a process something started while the device cooled is stopped
again, with a line under the repeat's own: `was running again after the 95s
cooling wait (pid 4242), and was stopped again before the recording`. A set
with a cooldown says at its start that the app is stopped before each wait.

A process the last look finds is stopped once more if that stop leaves it
there (`was stopped again, twice, before the recording`): a recording begun
with the process up is known not to be cold before it starts.

A repeat that was stopped and started warm all the same gets a line under its
own: `not a cold start`, with the pid when the process was running again
before the recording began. Its trace holds neither the process start nor
`bindApplication`, and in a set of cold starts it is another measurement: one
such repeat in five made the report's windows diverge 3.7x and put the warm
run's pid in its header. So it is set aside, out of the set, as
`<scenario>_iterNNN-warm.perfetto-trace` (`-hot`, `-relaunch`, or `-not-cold`
when `am start` gave no state), with its account beside it, and the repeat
is recorded again: `set aside, out of the set: repeat 2 is recorded again (1
of the 5 re-recordings the set is given)`. That name is no repeat's, so the
paths `collect` prints, the `next` line and `echolot`'s count of the set leave
it out, and it moves with its set when the set is set aside.

A set is given as many recordings more as it has repeats, and no more: a
device that starts the app again after every stop would otherwise be
recorded forever. Past them, a repeat that is not cold leaves the set one
cold start short. The line at the end names every recording set aside and
says whether the set is whole: `Each was recorded again, and the set holds 5
cold starts`, or `The set holds 3 cold starts of the 5 asked for`. A set with
no cold start at all is an error, exit 2, that names the recordings set
aside. On an A51, 51 of 124 starts of one day came out warm before the stop
moved ahead of the cooldown, and a block of five kept one to three cold
starts.

`analyze` reads the same account. Given a trace `collect` set aside among
cold ones, a glob that took the whole directory say, it leaves it out of the
medians and says which: `Left out: startup_iter001-warm.perfetto-trace
(WARM)`, in its output, in `report.md` under the runs, and in `report.json`
as `not_cold`. A set made only of such traces is read as given.

The scenario that needs nothing more is `scenario.window: launch`: its window
is the launch this mode starts, and the trace says where it ends, so nobody
picks an end anchor before the first recording. `echolot scan` drafts the
two together, this runner and that window, for an app with a launcher
Activity and no macrobenchmark, and [`echolot ui`](ui.md#setting-a-project-up)
writes that draft as `echolot.yml` at a press. See
[A window that is the launch](analysing.md#a-window-that-is-the-launch).
For a project whose macrobenchmark measures the launch, `scan` drafts the
same window with the [`gradle`](#gradle--the-macrobenchmark-writes-its-own)
runner.

`am start -W` prints `TotalTime`, which the runner reports — an independent
check that the window in the report is plausible. `TotalTime: 0` is the
system saying it timed no start, usually because the activity was already in
front and nothing was launched; `collect` counts those launches and leaves
them out of the spread.

`am start` of an app or an activity that is not on the device prints
`Error: Activity class {…} does not exist.` and exits 0. Taken as a launch,
that recorded five traces of the device and no app, and an agent spent its
whole loop on them. So before the set `collect` asks the device whether the
package is installed (`pm path`), and an app that is not there stops it with a
sentence, before anything is recorded. After each launch, an `Error:` line
from `am start` stops the set at that repeat: a `runner.activity` with a typo
in it, or a build installed under another activity. The repeat's own trace
is not kept, and the ones before it are.

When the code has temporary markers, the build that is there is read for them
next, and one that lacks any stops the set before it starts: see
[The build on the phone](#the-build-on-the-phone).

### `command` — something else drives it

We record the trace around a command of your choosing. Anything that can move
the app goes here: `adb input`, uiautomator, maestro, your own script.

```yaml
runner:
  mode: command
  reset_policy: none
  command: >
    adb shell am start -n com.example.app/.MainActivity &&
    adb shell input swipe 500 1500 500 600 400
```

The command comes from the project's own config — the same level of trust as
the gradle task next to it. If it outlasts the recording window, the runner
says the trace covers only its beginning.

It runs with `ANDROID_SERIAL` set to the device being recorded, so a bare
`adb shell …` inside it reaches that device — with a second one plugged in it
would otherwise stop at "more than one device", whatever `--device` said.

The scenario may install the app itself, so the build on the phone is read
for the code's temporary markers once more after the first repeat when it
lacked them before the set: see [The build on the phone](#the-build-on-the-phone).

### `gradle` — the macrobenchmark writes its own

```yaml
runner:
  mode: gradle
  gradle_task: ":benchmark:connectedBenchmarkBenchmarkAndroidTest"
  gradle_args: ["-Pandroid.testInstrumentationRunnerArguments.class=…"]
  project_root: ../my-app        # where ./gradlew lives
  gradle: ./gradlew              # the wrapper, relative to project_root (default)
  timeout_s: 3600                # the whole task, build included (default)
```

Macrobenchmark drops traces per iteration into an artifact directory whose path
depends on the build variant and the device model — awkward to find by hand.
The runner takes everything that appeared after the run started.

The benchmark runs as many iterations as its own `measureRepeated` asks for,
so `-n` and `runner.iterations` do not reach it, and `collect` says so when
either is given. `timeout_s` bounds the whole gradle run — the build, the
install and every iteration — and a run that outlasts it is stopped with its
whole process tree. `gradle` is the command in front of the task, run from
`project_root`.

A task that fails harvests nothing, whatever the benchmark wrote before it
failed: the traces of a half-finished run stay in the module's output
directory, and the previous set stays in `.echolot/traces`.

The connected test task uninstalls the app it tested when it ends, passed or
failed, and an install that was on the phone before the round goes with it,
its data too. The next `launch` round then stops on an app that is not
installed. So when `collect` can tell which phone the round runs on (the
one `--device`, `runner.device` or `ANDROID_SERIAL` names, or the one adb
lists), it asks whether the app is there before the task and after it, and
says when the round took it away:

```
  [!] com.example.app is no longer installed on R58M123ABC: the connected test task uninstalls the app it tested when it ends, passed or failed, …
```

Install the app again before a `launch` or `command` round. To have the
round leave it installed, add
`-Pandroid.injected.androidTest.leaveApksInstalledAfterRun=true` to
`runner.gradle_args` (Android Gradle plugin 8.2 and later). `collect` does
not pass it on its own: what is on the phone after a round stays what the
plugin documents. An app that was not there before the round gets no line,
and neither does a round whose phone `collect` cannot tell: several
attached and none named, or no adb on `PATH`, which gradle mode does not
need.

A startup macrobenchmark needs no end anchor. Every trace it writes holds
the launch it drove, so its scenario can be `scenario.window: launch`, as in
`launch` mode: see
[A window that is the launch](analysing.md#a-window-that-is-the-launch).
`echolot scan` drafts that window with this runner for a benchmark class
that measures the launch: one that constructs `StartupTimingMetric` and
names no `TraceSectionMetric`. The other classes keep the draft they had:

| the class | what `scan` drafts for its window |
|---|---|
| measures the launch (`StartupTimingMetric`) and names no section | `window: launch` |
| names a section (`TraceSectionMetric`) | `start: bindApplication` and the section as `end`: it is the developer's own statement of where "ready" is, so it comes before the launch |
| only sets `StartupMode` | `start: bindApplication` and an `end` to pick from a probe: a scroll benchmark starts cold too, and measures no launch |

`scan` says which of the three each class is, and takes one that measures
the launch before one that only sets `StartupMode`. It reads the class's own
file: a benchmark whose metrics are listed in a shared helper names none of
the three there, and keeps an `end` to pick. A section named by a constant,
`TraceSectionMetric(AppTraces.LIST_SHOWN)`, is looked up in the class's file
first and then across the project's sources, so a constants object in
another module is read too. When no declaration is found, the draft keeps
`end: "?"` and its `_evidence` names the constant to look up.

The class filter in `gradle_args` runs every test of the class. A test that
starts no launch inside its recording writes a trace with no launch in it,
and `analyze` refuses such a trace under this window, naming it. One refused
trace stops `analyze` for the whole set: move the trace it names out of
`.echolot/traces` and run it again.

The macrobenchmark chose what to record, so this mode builds no trace config
of its own. `environment`, `atrace_categories`, `buffer_kb`, `duration_ms`,
`reset_policy` and `sampling` are inert here, and `collect` says so when it
finds them — they look like they apply, and copying them in from a
launch-mode config is the obvious mistake. What the traces carry is the
benchmark's decision, and the report says which platform state it actually
found, and whether a callstack sampler ran. On the run these notes come from
that was the clock and memory but no thermal.

`project_root` is both where the task runs and where the traces are looked
for. A relative one is taken from the directory `echolot.yml` is in, the way
`-o` and the investigation are, and it defaults to that directory. It matters
as soon as `echolot.yml` does not sit beside `gradlew`: `./gradlew` is a
relative path, and it used to be looked for in whatever directory `echolot`
happened to be started from, so the same config ran in two places depending
on the shell.

"Everything that appeared after the run started" is a deliberate rule rather
than a convenience. A benchmark module keeps every trace it has ever written
— the directory this was first run in held fifteen from twelve days earlier —
and gathering by name would quietly merge two sittings into one report and
take medians across them.

Two practical notes. On an emulator the run refuses to start without a
suppress:

```bash
-Pandroid.testInstrumentationRunnerArguments.androidx.benchmark.suppressErrors=EMULATOR,LOW-BATTERY,UNLOCKED
```

And the task name is often ambiguous, because the baselineprofile plugin adds
flavours. `./gradlew :benchmark:tasks | grep -i connected` lists the options.

## Several scenarios

A project that guards a cold start and a feed scroll names both in one
config, under `scenarios:` instead of `scenario:`:

```yaml
scenarios:
  cold_start:
    start: {name: "bindApplication"}
    end: {name: "Choreographer#doFrame*"}
  feed_scroll:
    start: {name: "feed_scroll_start"}
    end: {name: "feed_scroll_end"}
    runner:
      mode: command
      command: "adb shell input swipe 500 1500 500 300 600"
runner:
  mode: launch
  iterations: 5
```

Each scenario holds its window, and the runner keys it changes, laid over
`runner:` for it alone: here the cold start is launched by `collect`, and the
scroll is driven by a command, five times each. The same macrobenchmark task
can serve both with another class in `gradle_args`.

Each scenario may carry thresholds of its own. Two journeys of one app can
differ two- or threefold in how long their slices and waits normally run, so
thresholds calibrated on one of them hide rows in the other, or fill its
report with rows that are only its normal run. A `detectors:` under a
scenario's name is laid over the config's for that scenario alone, a
threshold at a time, and `echolot calibrate --scenario feed_scroll` prints the
section that goes there: see [Calibrating](calibrate.md#several-scenarios).

```bash
echolot collect --scenario feed_scroll
echolot analyze .echolot/traces/feed_scroll_iter*.perfetto-trace --scenario feed_scroll
```

The name goes into the traces' names, so the two sets live side by side in
`.echolot/traces/`, and a new round of one sets aside that scenario's
previous traces and no other's. `collect`, `analyze` and `calibrate` take
`--scenario`; without it they take the first scenario and say so on stderr.

Each scenario keeps its own latest report too: `analyze` writes it to
`.echolot/out/<scenario>/` unless `-o` says otherwise, and the report names
its scenario, in `report.json` and at the top of `report.md`. `echolot
report --scenario` reads that scenario's report, the first one's without the
flag. `compare` refuses two reports of different scenarios, and compares a
report it is given with the latest report of that report's own scenario. A
config with one `scenario:` keeps `.echolot/out/`.

An investigation is about one scenario too: `echolot hunt --scenario
feed_scroll "<question>"` opens it on that one, and inside it the commands
take its scenario when no `--scenario` is given.

Inside it means said so. `collect --hunt <n>` and `analyze --hunt <n>` run
inside investigation `<n>`, the open one: the round `collect` pushes aside is
recorded under it, `analyze` files a copy of its report there, both count as
its work, and its scenario is the default. Without `--hunt` a run is outside
any investigation, whatever is open: nothing of it is filed, the
investigation is not counted as worked on, the first scenario is the default,
and stderr says `[i] outside investigation #<n>`. A cold-start baseline
recorded with a scroll investigation open used to be filed into it, renew it
as worked on that day, and in a config with several scenarios be measured as
the scroll (#498). A `--hunt` that names a number not open exits 2 before
anything is recorded. A round of the investigation that a run outside it
pushes aside is still recorded under the investigation: it is its evidence,
whoever moved it.

`echolot status` lists each scenario on a line of its own, with its traces
and its latest report, and marks the open investigation's. Its next step
names the scenario, `--scenario feed_scroll`: the open investigation's, else
the first.

In CI, the GitHub Action measures the scenario its `scenario` input names,
one a step or one a leg of a matrix, each kept under its own
`artifact-name`: [Comparing](compare.md#several-scenarios) shows the matrix.

## Which device

In `launch` and `command` modes, in the order they are consulted:

1. `--device <serial>` on the command line;
2. `runner.device` — usually in `local.yml`, since a serial belongs to one
   machine rather than to the project;
3. the one device `adb devices` lists in state `device`.

When none of them settles it, `collect` stops before anything is recorded and
says why: adb sees no devices at all; several are ready and none was named;
the device is `unauthorized` (accept the "Allow USB debugging?" prompt on its
screen); it is `offline` (replug, or `adb kill-server`); or, on Linux, `no
permissions` — adb can see it but may not open it, which a udev rule for the
phone's USB vendor fixes. A serial that is named but not attached is refused
with the list of those that are.

The chosen device reaches everything that runs on its behalf. In `command`
mode the scenario gets it as `ANDROID_SERIAL`. In `gradle` mode the task picks
its own device, and the connected test tasks run on every device adb lists
unless `ANDROID_SERIAL` names one — so `--device` or `runner.device` is passed
to gradle as `ANDROID_SERIAL`. With neither, an `ANDROID_SERIAL` already in
your environment passes through untouched, and `collect` says it is using it.

## The screen

A launch behind a dark, locked screen starts nothing that can be measured. On
the SM-A515F, connected over USB and not charging, its screen dozing and its
lock screen up, `am start -W` answered `LaunchState: UNKNOWN (0)` and no
`TotalTime`. The traces held the app's process and no start, and the report
could only say that the window's end anchor was missing.

So in `launch` and `command` modes `collect` reads the screen before the set
and again before each repeat:

- a screen that is asleep, dozing or showing a screensaver is woken;
- a lock screen that asks for nothing is dismissed;
- a lock screen that asks for a PIN, a pattern or a password stops `collect`
  before anything is recorded. Unlock the phone and collect again.

A screen that was on and unlocked gets no line. Whatever `collect` did to it
gets one.

The screen can go dark during a set: a repeat can wait minutes for
`runner.cooldown`, longer than a screen timeout. `stay_on_while_plugged_in`
(Developer options, Stay awake) keeps the screen on only while the phone
charges, and the A51 above was connected and not charging. When the screen
went dark between repeats, the line says whether the phone was charging. A
phone that locks with its screen stops the set there.

`am start -W` answering `UNKNOWN` after a `force-stop` means the system
measured no start. If the screen went dark or locked since the check,
`collect` stops at that repeat, and the traces recorded before it are kept;
the repeat's own trace is not. On a screen that is on and unlocked the
repeat is kept, and its line says that no start was measured.

In `gradle` mode the macrobenchmark drives the device, its screen included.
`collect` reads from it only what `runner.cooldown` waits on, the power
before the task, and whether the app is installed before and after it.

## Power

A phone that is not charging records its set on a battery that drains, and
one that runs low turns battery saver on, which slows it partway through a
set. An SM-A515F ran a whole session on battery, 49% to 45%, and a later hunt
on it from 44% to 39%, with nothing said: the read before each repeat knew
the phone was not charging, and said it only when the screen went dark.
Macrobenchmark refuses to start below 25% (`LOW-BATTERY`), charging or not.

So the read `collect` makes before the set and before each repeat (see
[The screen](#the-screen)) also takes `mIsPowered` and `mBatteryLevel` from
`dumpsys power`, and battery saver from `settings get global low_power`:

- a phone that is not charging and is under `runner.min_battery` stops
  `collect` before anything is recorded. The default is 25, Macrobenchmark's
  mark; `0` turns the check off. A charging phone is not stopped: its level
  rises. In gradle mode the floor applies before the task, when `collect`
  can tell which phone the round runs on;
- a phone that is not charging above it gets one line, and so does battery
  saver:
  `[!] power: R58M123ABC is not charging, 49%. The set runs on a draining battery, …`;
- a change between repeats, the cable out or saver on, is said before the
  repeat that found it: `[!] power before repeat 4 of 8: not charging, 71%,
  and charging, 72% before. …`.

A charging phone with saver off gets no line. Each trace's account keeps
`power` (`charging` or `not charging`), `battery_saver` (`on` or `off`) and
`battery_level`; what the device did not print is `null`, never `charging`
or `off`. `analyze` carries the first two into the report's `recording`
block, and `compare` says when the two sides differ in them, or when one
side's repeats do (see [How the two were recorded](compare.md#how-the-two-were-recorded)).
`charging` is the phone plugged in: a port too weak to keep up drains it all
the same, and the level in each account shows that.

```yaml
runner:
  min_battery: 25    # percent; 0 turns the check off. Belongs in local.yml
```

## The build on the phone

A temporary marker reaches a trace only through a build, and `launch` and
`command` modes record the build that is installed on the phone. A hunt ran
`mark --apply` and then recorded five cold starts with nothing built in
between. None of its markers was in the traces, the hunt put that down to the
trace, and it ended without the breakdown the markers were placed for.

So when the code has temporary markers — the prefixed names on lines tagged
`// echolot:mark`, in the checkout the config is in — `collect` reads the
build on the phone for them before the set:

- `pm path` lists the app's APKs, and the phone's own `unzip` and `grep` pick
  every name with the prefix out of their code. A marker's name is a string
  in the app's code, so the build holds it exactly when it was built with
  the marker in.
- A build that lacks any of the code's markers stops `collect` before
  anything is recorded. The sentence names them, the files they are in, and
  the step that puts them on the phone, with the gradle task `mark --apply`
  names:

  ```text
  collection error: the build of com.example.app on R58M123ABC lacks 1 of the
  2 temporary markers in the code (AGENTTMP_feed_load in
  app/src/main/kotlin/FeedViewModel.kt), so no trace of it would hold it.
  Build and install the app with `./gradlew :app:installBenchmark`, then
  collect again. …
  ```

- A build that has them all gets a line that says so, and the set goes on.

Any one marker the build lacks stops it. A build holds every marker its code
had, whatever the scenario runs, so a round's new markers missing from a
build that has the older ones are caught here. In the traces they would read
as code the scenario does not run, and `analyze` warns only when the traces
hold none of the code's markers. A build made from this code that still
lacks a marker leaves out the file it is in: a test source, another module
or flavour. Move the marker into code the app runs, or take it out. A name
the code puts together at run time, `"AGENTTMP_load_$id"`, is looked for by
its fixed start, the only part of it that is a string in the build.

When the phone cannot answer — no `unzip`, a `grep` that cannot read the
code, an APK the shell may not open — `collect` says the build could not be
read and records the set as before, and `analyze` says afterwards if the
traces lack the markers. The search first reads a short input whose answer is
known and counts the code files it found, so a phone whose tools behave
differently reads as "could not be read", never as a build without the
markers. The line says which tool could not: what `grep` printed for that
input, `pm path` listing no APK, `unzip` listing no code, or the status
`grep` ended with on the code. How `grep -z` ends each name it prints is its
own: toybox, on Android, ends it with a zero byte, as GNU grep does, and BSD
grep with a line break. Either is read.

The reading costs one `adb shell` call, and only when the code has temporary
markers. On the SM-A515F a large Kotlin app's APK, with 30 MB of code in
three files, took about 2 s, and a small app's 0.3 s. It runs before the
device is held still, so `runner.cooldown` takes the heat of it.

In `command` mode the scenario may install the app itself. A build that
lacks markers before the set gets a line instead of a stop, and is read again
once the first repeat has recorded: the set stops there if it still lacks
them, and that repeat's trace is not kept.

The time the app was installed is not asked. Held against the time the files
were written, it says when the build went on the phone, which is another
question: a checkout, a rebase or a formatter makes a file newer than a build
that has its markers, an APK built before them and installed after reads as
new, and a time says nothing of a marker in a file the build leaves out.

In `gradle` mode the macrobenchmark builds the app from the code, and the
build is not read.

## The device's state

A duration is the work done over the speed it was done at, and a phone does
not keep its speed. On the SM-A515F, eight launches of a large app
recorded back to back all ran with the kernel throttling the big cluster,
the first one included, and the clock they ran at fell repeat by repeat, from
2066 to 1915 MHz: the clock 7% lower at the last repeat than at the first,
for an app nobody changed. At the start of five of the eight recordings the
throttle was already on, left there by the repeat before.

Four keys under `runner:` hold the device still around a set. Each is off
unless set, and a device's numbers belong in `local.yml`:

```yaml
runner:
  cooldown:                 # before each repeat
    sensor: cpu             # battery (the default), cpu, gpu or skin
    max_c: 34
    timeout_s: 600
  fixed_performance: true   # the platform's fixed performance mode
  airplane_mode: true       # no radio while the set runs
  brightness: 128           # 0–255, auto-brightness off
```

- **`cooldown`** waits before each repeat until the sensor reads at or below
  `max_c`. Eight more starts on the same phone, each waiting for its `cpu`
  sensor to read 34 °C, ran at 2032 to 2049 MHz with no drift, and none began
  with the throttle on. Each still throttled during its own start, since one
  start of that app is enough to trip it: what the wait gives is the same
  phone at the start of every repeat. The starts' own spread
  stayed what the app gives it, 8.6% back to back and 11.4% with the wait.
  The wait costs time: 91 to 203 seconds a repeat there, 17 minutes for the
  set against 2. `battery` is read from `dumpsys battery`, which every
  device has; `cpu`, `gpu` and `skin` from the thermal HAL, `dumpsys
  thermalservice`, from Android 10 on. A sensor the device does not have
  stops `collect` before anything is touched, naming the ones it has. The
  kernel's own zones, the ones that trip the throttle, are closed to the shell
  on a production build. The HAL's sensors are smoothed and lag them, so
  `max_c` goes a little above the sensor's reading at rest: 34 for the A51's
  `cpu`, which reads about 31 there. A device that does not cool within
  `timeout_s` is recorded anyway, and the line says so; the report's
  `environment` shows how hot it ran. In launch and command modes the app is
  stopped before the wait: nothing of it runs while the device cools, and a
  job it scheduled during the recording is not yet due when the stop comes
  ([`launch`](#launch--we-drive-it)). The account `collect` keeps beside
  each trace, `<trace>.recording.json`, holds the cooldown and the sensor's
  reading before that repeat. When the kernel throttled anyway, the report
  reads it: a set cooled before every
  repeat was heated by the scenario itself, and waiting longer would not help,
  so the report says to compare it only with sets recorded the same way; a
  set recorded without `cooldown` is told to set it.
- **`fixed_performance`** asks for the platform's fixed performance mode,
  which holds the clock at a level the device can keep. Whether a device has
  one is up to its power HAL: the A51 takes the command and moves no frequency
  limit. `collect` reads the limits before and after, and says which it was.
- **`airplane_mode`** turns the radio off. An app that goes to the network at
  start does something else without it, so record both rounds of a comparison
  alike.
- **`brightness`** holds the screen at one level, auto-brightness off.

Whatever `collect` changed, it puts back when the set ends, a failed set
included, and says so a line each. In gradle mode the macrobenchmark runs its
repeats back to back, so `cooldown` waits once, before the task.

## While it runs, and when it fails

A macrobenchmark round is minutes of silence. `collect` writes where it stands
to `.echolot/log/collect.json` — when it starts, after every iteration it
drives, and when it ends — and `echolot` turns that into a `collect` line:
`running: coldStart, 7/15 iterations, started 3m ago`, or `the macrobenchmark
drives the iterations` in gradle mode. A run whose process is gone without
finishing reads `interrupted`. One that failed reads `failed 2m ago:` followed
by the line of the output that names the cause, such as the exception the
benchmark threw, and the fix when the failure is a known one. That line stays
until a newer set of traces exists. A file that does not parse, or one that
holds what `collect` never writes there, such as text where it writes a time,
gives no line: it describes no run, and the next `collect` writes it anew.

In launch and command modes `collect` reads the first repeat's trace before
it records the rest, with the window `analyze` would cut. When the app drew
no frame in it, or the end anchor opened and never closed, the scenario did
not reach its screen (no network, an error screen, a dialog in the way), and
every repeat after it would record the same: the round stops there, with that
reason and nothing kept. With `-n 1` the trace is kept and marked `[!]`. An
end anchor that did not occur at all is marked and the round goes on, since a
wrong name in the config reads the same; `echolot probe` on the trace says
which, and the mark names the trace's slices that read like the anchor.

### Anchors the code no longer writes

Before the first repeat, in every mode, `collect` holds the scenario's
anchors against the code, and `echolot` does the same for each scenario, on
an `anchor` line marked `!`. A name the app writes is a string in its Kotlin
or Java, so an anchor that no string there is, or can be put together into at
run time, is said, with what the code has that reads like it:

```text
[!] end anchor `list_shown_mark` is in no Kotlin or Java string here; the code has
`list_shown` (app/src/main/java/com/example/app/Marks.kt:4) — the traces are the
scenario's whatever it is called: put the name right in echolot.yml before `analyze`
```

The platform's own sections are in no file of a project and are never said:
`bindApplication`, an Activity's `activityStart` and `performCreate:…`,
`Choreographer#doFrame*`, `reportFullyDrawn*`, RecyclerView's `RV …`,
Compose's and `androidx.startup`'s. A name with the temporary markers' prefix
that the code no longer has is said as one. Any other name the code does not
hold, with nothing in it that reads like it, may be a library's: it is said
as a question, with the command that answers it (one recording, then `echolot
probe`), and no more once a report of the scenario has found it in its
recordings. An end anchor that is still `scan`'s `?` is said whatever the
code holds, and `next` says to pick it from one recording before recording
five.

None of this stops the round. The build on the phone is not the code, and a
set recorded under a wrong name is still the scenario's: `analyze` cuts the
window as it reads the traces, so the same set is read again once the name is
right. When a report's recordings do not hold an anchor, its warning names
their slices that read like it, `The trace holds list_shown, which reads like
it`, where it used to send the reader to `probe`.

Ctrl-C, or **Stop** in `echolot ui`, puts the phone back as a failure does
and ends with one line, `collect interrupted after 2 of 5 repeats: their 2
traces are kept in .echolot/traces`, and exit code 130. A trace whose copy
from the phone was cut short is deleted, so the traces kept are whole ones.

The run log, `.echolot/log/runs.jsonl`, keeps the same cause and fix at the
head of the run's `error`, followed by as much of the full message as fits in
the 800 characters it keeps.

Four gradle failures come with the one thing that fixes them:

| the output says | what it is | the fix |
|---|---|---|
| `Perfetto SDK` / `binary verification` | the SDK half of the tracing could not be set up in the app — usually a stale `libtracing_perfetto.so` in its code_cache | reinstall, or `pm clear` where the device allows it; or turn that half off with `-Pandroid.testInstrumentationRunnerArguments.androidx.benchmark.perfettoSdkTracing.enable=false` in `runner.gradle_args` |
| `ERRORS (not suppressed): EMULATOR, …` | the benchmark refuses the device or its state | fix the state, or `…androidx.benchmark.suppressErrors=` with the names it lists, `EMULATOR,LOW-BATTERY` for one in `runner.gradle_args` |
| `No online devices found` / `no devices/emulators found` / `device offline` | gradle found no device it could use | `adb devices` should list one as `device`; a serial named with `--device` or `runner.device` reaches gradle as `ANDROID_SERIAL` and has to be one of those listed |
| `INSTALL_FAILED` / `signatures do not match` | the APK did not install | uninstall the app from the device first — a build signed differently cannot go over the one that is there |

## `pm clear` is deliberately unsupported

`reset_policy` accepts `force-stop` and `none`; anything else is named in a
warning and runs as `force-stop`. Wiping application data changes the scenario
rather than repeating it: a cold start with an empty database and a real
user's cold start are different things, and only one of them is worth
measuring.

## What the trace config must contain

The runner builds the perfetto config itself, but if you capture by hand
(recipe in `echolot/claude/skills/echolot/references/collect.md`), four things
are mandatory and a fifth decides whether the report can tell a slower machine
from a slower app:

**`sched/sched_switch`** — without it there is no `thread_state`, and what
stands on it goes: `runnable_starvation`, `uninstrumented_cpu` and `io_wait`
go silent, `anr_risk` keeps only its slice source and loses a stall in code
nobody traced, and the CPU clock weighting under **Device** loses its input.
What a project with no instrumentation still gets is under
[Detectors for code nobody traced](analysing.md#detectors-for-code-nobody-traced).

**`linux.process_stats` with `scan_all_processes_on_start`** — the only source
of process names. Without it `process.name` is empty and the config matches
nothing.

**`atrace_apps`** with the package name — otherwise the trace holds system
slices only.

**`android.surfaceflinger.frametimeline`** — SurfaceFlinger's own record of
every frame, and the only source `frame_jank` has. It is a separate data
source rather than an atrace category:

```
data_sources: { config { name: "android.surfaceflinger.frametimeline" } }
```

Android 12 and up. On anything older the data source does not exist, perfetto
records the rest without complaining, and the detector is silent — which reads
exactly like "no bad frames". If jank is the question and the report says
nothing, check the Android version before believing it.

**The platform state** — `power/cpu_frequency`, `thermal/thermal_temperature`,
`thermal/cdev_update`, `sched/sched_blocked_reason`, and `linux.sys_stats`
polling memory once a second:

```
      ftrace_events: "power/cpu_frequency"
      ftrace_events: "sched/sched_blocked_reason"
      ftrace_events: "thermal/thermal_temperature"
      ftrace_events: "thermal/cdev_update"
```
```
data_sources: { config {
    name: "linux.sys_stats"
    sys_stats_config { meminfo_period_ms: 1000 vmstat_period_ms: 1000 }
} }
```

Not mandatory, and the one thing on this page that changes what a comparison
means. A duration is the work done divided by the speed the machine was doing
it at: the same code on a lower clock takes longer, and `compare` without
these numbers reports that as a regression. With them it says the clock moved
instead. `runner.environment: false` leaves them out for a device whose buffer
cannot afford them, and the report then says the device state was not
recorded — never that it held steady. `io_wait` goes silent as well: the disk
flag it reads comes from `sched/sched_blocked_reason`, one of these events.

`power/cpu_idle` is deliberately not in that list. It fires on every idle
transition on every core, which is the largest source of events on the page,
and it would add nothing: the clock is read only over intervals where the
app's own threads held a core, and a core running a thread is not idle.

What this costs, measured on an SM-A515F (Android 13, eight cores, two cpufreq
policies) over three twelve-second cold starts: `cpu_frequency` produced
4564–4940 samples per trace — about 400 a second, 1.6% of all ftrace events in
the same trace — and the buffer never overran. Thermal came to 160 samples and
memory to 396. The cost is not the reason to turn this off.

One of the four delivers less than it looks. `sched/sched_blocked_reason`
carries `io_wait`, which works, and `caller`, which perfetto turns into a
kernel function name by reading `/proc/kallsyms`. That file is unreadable on a
production build, so `blocked_function` comes back empty there — on the A51 it
was empty for all 6683 uninterruptible-sleep intervals in the trace, with and
without `symbolize_ksyms`, while `io_wait` was filled in for 6486 of them.
Expect the name of the blocking function on a userdebug kernel and nowhere
else; `io_wait` is what tells disk waiting from other blocking everywhere.

There is also a requirement on the app itself: slices arrive only if it is
**profileable or debuggable**. The manifest needs
`<profileable android:shell="true" />`.

## Callstack sampling

```yaml
runner:
  sampling: 100    # Hz. `true` means 100; `false`, or no key, leaves it off
```

`uninstrumented_cpu` finds a thread that burned CPU with no slice around it,
and without samples that is all it can say. What the thread was running takes
another round: markers around the likely code, a new recording, another look.
A callstack sampler records the answer alongside. Many times a second it notes
which functions were on the stack of each thread that held a CPU, and the
samples go into the same trace, on the same clock as everything else. The
report reads them in the round that finds the blind spot: the row names what
ran in the samples that fell there, and the nearest frame of the project's
own code under it
([Analysing](analysing.md#detectors-for-code-nobody-traced)). The
Perfetto UI shows the same samples as a flame graph for any stretch of time
you select.

It is off unless asked for, because it costs the app time. On the SM-A515F
(Android 13, a `user` build), six cold starts of about 1.36 s recorded the
way `collect` records them took 186 ms longer at 100 Hz than six without a
sampler, 95% sure between 68 and 263 ms. The two kinds of start alternated,
so the device's heat fell on both alike. A sampled set therefore compares
only with another sampled at the same rate. The report says on its header
whether a sampler ran, and `compare` warns when two rounds were not recorded
alike.

Three choices in the recording are deliberate, and the first two were
settled on that device:

- **No process filter.** The sampler judges a process once, by its name at
  its first sample, and drops one that does not match for the rest of the
  recording. A process just forked from zygote still carries zygote's name,
  so a filter by package name lost the cold start in four recordings of seven
  and got nothing of it at all. Without the filter every process is sampled,
  and only those that are profileable or debuggable come back with a stack,
  which in practice means the app's.
- **The app's frames, not the kernel's.** Kotlin and Java frames come back by
  name, interpreted, JIT-compiled and compiled ahead of time alike. Frames of
  the framework, compiled into the system image, come back without one, and
  the unwinder mostly stops at them: in the blind spots of six sampled cold
  starts, 69% of the stacks ended there. A minified build's frames carry
  their minified names, `a.b.c`, until `project.mapping` names the build's
  `mapping.txt` — see below.
- **A buffer of its own**, the size of `buffer_kb`, so the samples can never
  push out the sched and atrace events every detector reads.

`true` means 100 Hz. A higher rate buys more samples and costs about the
same — 250 Hz, filtered to the app, had cost the same start 156 ms — but the
unwinding falls behind: at 100 Hz every one of the app's samples in the
window came with a stack, wherever a recording got stacks at all; at 250 Hz
77 to 85% did, and at 1 kHz one in seven came without, every one of them in
the busiest seconds. Perfetto advises staying under 200 for Java and Kotlin
stacks, and `collect` warns above that.

Two requirements. The app has to be profileable or debuggable, which is the
same `<profileable android:shell="true" />` the slices need. And the device
needs Perfetto's sampler, `traced_perf`: the recordings above ran on
Android 13, and Perfetto documents CPU profiling for Android 15 and up. When
the recording asks for samples and no sampler starts, the report says so
rather than showing none.

What the report says, on the line under **Device**:

| the report says | what happened |
|---|---|
| Sampled callstacks at 100 Hz: 1482 samples of this process in the window, 99% with a stack | a sampler ran and unwound the app |
| … they came with a stack in 5 of 6 repeats and with none in the rest | the sampler lost the process as it started in one repeat, which has no stacks to read |
| … none with a stack | the app is not profileable or debuggable, or the sampler lost the process as it started; another round tells which |
| … none of the samples in the window is this process's | the recording was filtered by process name and missed the start |
| The recording asked for callstack samples … and none arrived | the device's sampler did not run |

The last three are warnings. A profileable app does lose every stack of a
cold start now and then: on the SM-A515F it happened in one recording of six,
and the other five had a stack on every sample. The sampler asks a process
for access to its memory once, at its first sample; that time it did not get
it, and every sample after that was written without a stack.

What the report reads is the trace, never the config, so a trace recorded
some other way says the same. In `gradle` mode the benchmark records with its
own config and `runner.sampling` does not apply, but the report still says
whether its traces hold samples.

### A minified build

A benchmark build is meant to run what the release runs, and that usually
means minified: the frames of the app's code, and of the libraries it ships,
come back with the names R8 gave them, `a.b.c`. Name the build's mapping in
the config:

```yaml
project:
  package: com.example.app.benchmark   # as installed on the device
  mapping: app/build/outputs/mapping/benchmark/mapping.txt
```

`echolot scan` writes the key into the config it drafts when the variant to
measure is minified: its build type sets `minifyEnabled`, or starts with
`initWith` from one that does, as a benchmark build type started from release
usually is. The path is where AGP writes the mapping when the variant is
built, so it may name a file that is not there yet. `gradle` mode builds the
variant with the first collect; in `launch` mode it is there once you build
the variant. Until then `analyze` says so, on stderr and at the top of the
report, and makes the report without it: R8's names stay as recorded.

`analyze` reads the file into a deobfuscation packet and hands it to
trace_processor after each trace, the way Perfetto's own tools attach one; the
trace on disk is not touched. trace_processor matches the packet to the
frames by the package in their file's path on the device, so the package has
to be the one installed, suffix and all. A method R8 gave the same name as
another — overloads usually share one — comes back as both,
`Store.load | Store.save`, because a sample has no line to tell them apart.

Reading a mapping costs seconds once per `analyze`: a synthetic one of 125 MB
took 4.6 s to read, and each trace loaded 1.5 s slower with it.

The runtime also writes frames as text, and trace_processor sees none of
those: both sides of a lock in ART's contention slice, and every thread of an
ANR record. They carry a line, and the line is what R8's own `retrace` goes
by. `analyze` names back the lock's two frames with the same mapping before
it places them ([From a row to a line](analysing.md#from-a-row-to-a-line-without-domains)),
and `echolot anr` names back a record's
([A minified build](anr.md#a-minified-build)). One frame comes back as the
method the runtime stood in, at the line it came from, and the methods it was
inlined into, by the names R8's `retrace` gives. What a build writes depends on
how it was built. A test app built three ways with R8 9.0 wrote this on an
SM-A515F with Android 13:

| the build | the lock slice | the ANR record |
|---|---|---|
| line numbers kept, `-renamesourcefileattribute SourceFile`, minimum API 24 | `a.a.run()(SourceFile:6)` | `a.a.run(SourceFile:6)` |
| no `-keepattributes` | `a.a.run()(r8-map-id-…:6)` | `a.a.run(r8-map-id-…:6)` |
| line numbers kept, minimum API 26 | `a.a.run()(SourceFile:-1)` | `a.a.run(unavailable:20)` |

The last build has no line table at all: R8 maps the offsets of the
instructions instead. The ANR record prints the offset where the line would
be, and that is named exactly. The lock slice prints `-1`, and its frames are
named by the methods R8 kept, with no line; their declarations are then the
place, as for a release build that kept its names.

The header says how the app's sampled methods came back, and what the
mapping made of the lock slices:

| the report says | what happened |
|---|---|
| … of the app's … sampled methods read as minified, `a.b.c` | a minified build and no `project.mapping` |
| `project.mapping` named … of the app's … sampled methods back | the mapping is this build's; any still minified arrived that way |
| … still read as minified after `project.mapping` renamed … | a mapping from another build: it renames the frames it happens to match, wrongly, and misses the rest |
| `project.mapping` named back the … frames R8 wrote into the lock slices | `code` names the real methods, and the evidence keeps the frames as recorded |
| … of the … frames R8 wrote into the lock slices have no place in `project.mapping` | a mapping from another build: no range of their method holds their line |
| `project.mapping` named back … of the app's … sampled methods, and they did not read as minified | the build recorded keeps its names, or the mapping is another build's |
| the lock slices name their files after mapping …, and `project.mapping` is … | another build's mapping, told by its id: the frames stay as recorded |
| `project.mapping` names …, and no file was there | the variant has not been built since the config named it: build it and analyze again |

The warnings about the sampled methods wait for a tenth of them. Some code
arrives minified from elsewhere — SDKs ship that way — and no mapping of this
build names it: a build that kept its names had 38 of its 3,309 sampled
methods so on the SM-A515F, a little over 1%. Another build's mapping is told
by what it misses: R8's names are short and reused, so it knows a few of this
build's names, renames those wrongly, and leaves the rest as they were. The
names it got wrong cannot be told from right ones; the ones it left are what
gives it away. A build that kept its names leaves nothing minified, and the
sign is the other way round: the mapping names back none of its methods.

The lock slices' frames carry a line, and another build's mapping is told by
lines no range of the method holds, which the right mapping never leaves; from
a tenth of those frames up, the header warns. Such a frame gets its class
back and keeps R8's method name, with no line. A build that names its files
after its mapping, `r8-map-id-<id>` when it kept no source file names, says
outright which mapping it has: when no frame carries the mapping's own
`pg_map_id`, nothing is named back. And a class R8 kept is in the mapping
under its own name, so its frame is named back on the strength of a line in
one of R8's ranges only when something else shows the build is minified: a
frame of a class or a method R8 renamed, a file R8 named, or a sampled method
the mapping named back. A build that kept its names keeps its lines.

## Composable names

A Compose app that carries `androidx.compose.runtime:runtime-tracing` can
name every composable call in a trace, and the `composables` detector ranks
the app's own by the time they hold
([Analysing](analysing.md#rows-of-compose)). The names come through the
app's Perfetto SDK, which is off until something switches it on:

```yaml
runner:
  compose_names: true
```

Before the first repeat, `collect` asks the app which version of
`tracing-perfetto` it carries and takes that version's library for the
device's ABI, `tracing-perfetto-binary` from Gradle's cache or from Google's
Maven, checked against the SHA-1 published beside it and kept in
`~/.cache/echolot`. It pushes the library into the app's own media
directory, switches cold-start tracing on, and adds the SDK's `track_event`
source to the recording. After the set it switches it off and removes the
library, a failed set included.

On an SM-A515F with a Compose app built for it, three cold starts each came
back with all 921 composable calls of the start, and the report ranked the
app's own with their files and lines. It costs the start: loading the SDK
took the app 54 to 87 ms, and a cold start of about 580 ms ran 67 ms longer
on the median. So a round with names compares only with another with names,
the report says which it was, and `compare` warns when two were not
recorded alike.

Two things the device decides:

- **Android 11 or later**, the library's own minimum. An app without
  `runtime-tracing` has no receiver to switch, and `collect` stops with a
  sentence before anything is recorded.
- **The recording starts differently.** `perfetto --background-wait`, which
  `collect` otherwise waits on, waits for every process on the device that
  has the Perfetto SDK to start its data source. Frozen background apps
  never answer, and on that device it came back after 22 seconds, past the
  end of a 12-second recording. With names on, the recording detaches at
  once and the scenario starts two seconds later.

In gradle mode the macrobenchmark makes the recording, and its own flag
switches the SDK on:
`-Pandroid.testInstrumentationRunnerArguments.androidx.benchmark.perfettoSdkTracing.enable=true`
in `runner.gradle_args`.

## Merging repeats

Numbers are merged by **median** — not by mean (one outlier would drag the
conclusion along) and not by maximum (then any random hiccup becomes a
"finding"). The one exception is **Max**, the longest single occurrence: it is
the longest any repeat saw, and each repeat's own is in `spread.max_ms`.

The whole point of repeating is the **Runs** column:

| Where | Runs | N | Total, ms |
|---|---|---|---|
| `m.example.app` | **3/3** | 80 | 56.92 |
| `RenderThread` | **1/3** | 30 | 39.02 |

The first row reproduces, the second happened once. Without that column they
look equally convincing and the agent goes off investigating an accident.

A detector reports a row only where it clears a threshold, and a row missing
from one repeat may have been there below it. So `analyze` of several
recordings runs each detector a second time with its calibrated thresholds at
zero and no limit on rows, and looks up there every row some repeat reported.
`Runs` counts the repeats where the thing happened; where that differs from
the repeats where it cleared the bar, the cell says so: `5/5, over the bar in
1`, with `fired: "1/5"` in `report.json`. The median and spread cover every
repeat it happened in.

A row is matched across repeats by its name, with the numbers the runtime
picks anew in each run left out: a lock owner's tid
(`Lock contention on a monitor lock (owner tid: #)`), a frame's number
(`Choreographer#doFrame #`), the number of a pool or unnamed thread
(`Thread-#`, `pool-#-thread-#`). One wait seen in every run is then one row,
`3/3`, and the names as each trace had them are kept in the row's `variants`
in `report.json`. Line numbers in a lock note, and the project's own markers,
keep their digits.

Two warnings come out of merging:

- the runner prints the `am start -W` spread and flags it above 30% — the
  device is under load and thresholds from such runs will be noisy (a launch
  that reports `TotalTime: 0` was not timed, and is counted apart);
- the report flags repeat windows that diverged more than twofold — those are
  not repeats of one scenario, and a median over them means nothing.

The recordings are put in one order first, by file name with numbers read as
numbers (`_iter2` before `_iter10`), whatever order the paths were given in:
the same set is the same report. That is the order `collect` recorded them in,
and the order of every `spread.values` list. The pid and window times in the
header are the first recording's, and the header says so.
