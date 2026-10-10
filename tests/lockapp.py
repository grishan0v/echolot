"""A minified test app, as R8 9.0 built it and a Galaxy A51 recorded it.

The app is four small classes: a worker thread that takes a monitor and
spins inside it for twenty seconds, and the main thread that asks for the
same monitor a moment later. It was built with R8 9.0, installed on an
Android 13 phone, traced with the `dalvik` category while it froze, and
tapped until the system gave up on it; the ANR came back with `dumpsys
dropbox --print data_app_anr`. What R8 made of it, and what the phone wrote,
is below as it came out. The record keeps the two threads that matter and
one that does not.

It was built three ways, and each writes the same two frames differently:

- line numbers kept, `-renamesourcefileattribute SourceFile`, minimum API 24:
  `a.a.run()(SourceFile:6)` in the lock slice and the record alike;
- no `-keepattributes` at all: `(r8-map-id-…:6)` in both, with the same
  mapping as above apart from its id;
- line numbers kept, minimum API 26: R8 drops the line table and maps the
  instructions' offsets (`OFFSETS`). The record prints the offset as
  `(unavailable:20)`, and the lock slice has no line at all, `(SourceFile:-1)`.

R8's own `retrace` named every frame of the three the same way: the main
thread inside `Store.read` at line 17, inlined into `MainActivity.freeze` at
21; the worker inside `Store.hold` at line 10, inlined into `Holder.run` at
23. Without a line, the methods R8 kept: `MainActivity.freeze` and
`Holder.run`.
"""

from __future__ import annotations

from pathlib import Path

PACKAGE = "com.example.locks"

# The sources as they were compiled: the lines the mapping names are these.
SOURCES = {
    "MainActivity.java": """\
package com.example.locks;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;

public class MainActivity extends Activity {
    private final Store store = new Store();

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        setContentView(new View(this));
        Holder.start(store, 20000);
        new Handler(Looper.getMainLooper()).postDelayed(this::freeze, 1500);
    }

    private void freeze() {
        int seen = store.read();
        setTitle("read " + seen);
    }
}
""",
    "Holder.java": """\
package com.example.locks;

final class Holder implements Runnable {
    private final Store store;
    private final long ms;

    private Holder(Store store, long ms) {
        this.store = store;
        this.ms = ms;
    }

    static void start(Store store, long ms) {
        new Thread(new Holder(store, ms), "Holder").start();
    }

    @Override
    public void run() {
        try {
            Thread.sleep(1000);
        } catch (InterruptedException ignored) {
            return;
        }
        store.hold(ms);
    }
}
""",
    "Store.java": """\
package com.example.locks;

final class Store {
    private final Object lock = new Object();
    private long value;

    void hold(long ms) {
        synchronized (lock) {
            long end = System.nanoTime() + ms * 1_000_000L;
            while (System.nanoTime() < end) {
                value = Mix.step(value);
            }
        }
    }

    int read() {
        synchronized (lock) {
            return (int) value;
        }
    }
}
""",
    "Mix.java": """\
package com.example.locks;

final class Mix {
    static long step(long v) {
        v ^= v << 13;
        v ^= v >>> 7;
        v ^= v << 17;
        return v + 1;
    }
}
""",
}

# Line numbers kept, minimum API 24. The build with no `-keepattributes`
# wrote the same mapping under another id.
MAPPING = """\
# compiler: R8
# compiler_version: 9.0.3-dev
# min_api: 24
# compiler_hash: 750a21b4f4281b1f453b649e0b84f1ba9c04f4fc
# common_typos_disable
# {"id":"com.android.tools.r8.mapping","version":"2.2"}
# pg_map_id: c2d66c23dd18be785acf86ecf50d8d7c1abe10b6de3a101cc4f3f25f7dff18b6
# pg_map_hash: SHA-256 c2d66c23dd18be785acf86ecf50d8d7c1abe10b6de3a101cc4f3f25f7dff18b6
com.example.locks.Holder -> a.a:
# {"id":"sourceFile","fileName":"Holder.java"}
    com.example.locks.Store store -> a
      # {"id":"com.android.tools.r8.residualsignature","signature":"La/c;"}
    1:3:void <init>(com.example.locks.Store,long):7:7 -> <init>
      # {"id":"com.android.tools.r8.residualsignature","signature":"(La/c;)V"}
    4:6:void <init>(com.example.locks.Store,long):8:8 -> <init>
    1:1:void run():19:19 -> run
    2:2:void run():23:23 -> run
    3:3:void com.example.locks.Store.hold(long):8:8 -> run
    3:3:void run():23 -> run
      # {"id":"com.android.tools.r8.rewriteFrame","conditions":["throws(Ljava/lang/NullPointerException;)"],"actions":["removeInnerFrames(1)"]}
    4:7:void com.example.locks.Store.hold(long):8:11 -> run
    4:7:void run():23 -> run
    8:8:void com.example.locks.Store.hold(long):13:13 -> run
    8:8:void run():23 -> run
com.example.locks.MainActivity -> com.example.locks.MainActivity:
# {"id":"sourceFile","fileName":"MainActivity.java"}
    com.example.locks.Store store -> a
      # {"id":"com.android.tools.r8.residualsignature","signature":"La/c;"}
    1:2:void <init>():9:10 -> <init>
    1:2:void onCreate(android.os.Bundle):14:15 -> onCreate
    3:3:void com.example.locks.Holder.start(com.example.locks.Store,long):13:13 -> onCreate
    3:3:void onCreate(android.os.Bundle):16 -> onCreate
    4:4:void onCreate(android.os.Bundle):17:17 -> onCreate
com.example.locks.MainActivity$$ExternalSyntheticLambda0 -> a.b:
# {"id":"sourceFile","fileName":"R8$$SyntheticClass"}
# {"id":"com.android.tools.r8.synthesized"}
    com.example.locks.MainActivity com.example.locks.MainActivity$$InternalSyntheticLambda$1$646b9e1ec3cf9044f6da9cc88bd1af80f4a8a2363b7a3d8bccb53a5528b56df7$0.f$0 -> a
      # {"id":"com.android.tools.r8.synthesized"}
    1:1:void a.MainActivity$$ExternalSyntheticLambda0.<init>(com.example.locks.MainActivity):0:0 -> <init>
      # {"id":"com.android.tools.r8.synthesized"}
    1:1:void com.example.locks.MainActivity.freeze():21:21 -> run
    2:2:int com.example.locks.Store.read():17:17 -> run
    2:2:void com.example.locks.MainActivity.freeze():21 -> run
      # {"id":"com.android.tools.r8.rewriteFrame","conditions":["throws(Ljava/lang/NullPointerException;)"],"actions":["removeInnerFrames(1)"]}
    3:4:int com.example.locks.Store.read():17:18 -> run
    3:4:void com.example.locks.MainActivity.freeze():21 -> run
    5:5:void com.example.locks.MainActivity.freeze():22:22 -> run
    6:6:int com.example.locks.Store.read():19:19 -> run
    6:6:void com.example.locks.MainActivity.freeze():21 -> run
com.example.locks.Store -> a.c:
# {"id":"sourceFile","fileName":"Store.java"}
    java.lang.Object lock -> a
    long value -> b
    1:2:void <init>():3:4 -> <init>
"""

# Line numbers kept, minimum API 26: the ranges on the left are offsets of
# instructions, not lines.
OFFSETS = """\
# compiler: R8
# compiler_version: 9.0.3-dev
# min_api: 26
# {"id":"com.android.tools.r8.mapping","version":"2.2"}
com.example.locks.Holder -> a.a:
# {"id":"sourceFile","fileName":"Holder.java"}
    com.example.locks.Store store -> a
      # {"id":"com.android.tools.r8.residualsignature","signature":"La/c;"}
    0:2:void <init>(com.example.locks.Store,long):7:7 -> <init>
      # {"id":"com.android.tools.r8.residualsignature","signature":"(La/c;)V"}
    3:5:void <init>(com.example.locks.Store,long):8:8 -> <init>
    2:4:void run():19:19 -> run
    5:6:void run():23:23 -> run
    7:8:void com.example.locks.Store.hold(long):8:8 -> run
    7:8:void run():23 -> run
      # {"id":"com.android.tools.r8.rewriteFrame","conditions":["throws(Ljava/lang/NullPointerException;)"],"actions":["removeInnerFrames(1)"]}
    9:10:void com.example.locks.Store.hold(long):8:9 -> run
    9:10:void run():23 -> run
    11:19:void com.example.locks.Store.hold(long):9:9 -> run
    11:19:void run():23 -> run
    20:27:void com.example.locks.Store.hold(long):10:10 -> run
    20:27:void run():23 -> run
    28:51:void com.example.locks.Store.hold(long):11:11 -> run
    28:51:void run():23 -> run
    52:56:void com.example.locks.Store.hold(long):13:13 -> run
    52:56:void run():23 -> run
com.example.locks.MainActivity -> com.example.locks.MainActivity:
# {"id":"sourceFile","fileName":"MainActivity.java"}
    com.example.locks.Store store -> a
      # {"id":"com.android.tools.r8.residualsignature","signature":"La/c;"}
    0:2:void <init>():9:9 -> <init>
    3:10:void <init>():10:10 -> <init>
    0:2:void onCreate(android.os.Bundle):14:14 -> onCreate
    3:10:void onCreate(android.os.Bundle):15:15 -> onCreate
    11:27:void com.example.locks.Holder.start(com.example.locks.Store,long):13:13 -> onCreate
    11:27:void onCreate(android.os.Bundle):16 -> onCreate
    28:47:void onCreate(android.os.Bundle):17:17 -> onCreate
com.example.locks.MainActivity$$ExternalSyntheticLambda0 -> a.b:
# {"id":"sourceFile","fileName":"R8$$SyntheticClass"}
# {"id":"com.android.tools.r8.synthesized"}
    com.example.locks.MainActivity com.example.locks.MainActivity$$InternalSyntheticLambda$1$646b9e1ec3cf9044f6da9cc88bd1af80f4a8a2363b7a3d8bccb53a5528b56df7$0.f$0 -> a
      # {"id":"com.android.tools.r8.synthesized"}
    0:5:void a.MainActivity$$ExternalSyntheticLambda0.<init>(com.example.locks.MainActivity):0:0 -> <init>
      # {"id":"com.android.tools.r8.synthesized"}
    2:3:void com.example.locks.MainActivity.freeze():21:21 -> run
    4:5:int com.example.locks.Store.read():17:17 -> run
    4:5:void com.example.locks.MainActivity.freeze():21 -> run
      # {"id":"com.android.tools.r8.rewriteFrame","conditions":["throws(Ljava/lang/NullPointerException;)"],"actions":["removeInnerFrames(1)"]}
    6:7:int com.example.locks.Store.read():17:18 -> run
    6:7:void com.example.locks.MainActivity.freeze():21 -> run
    8:10:int com.example.locks.Store.read():18:18 -> run
    8:10:void com.example.locks.MainActivity.freeze():21 -> run
    11:29:void com.example.locks.MainActivity.freeze():22:22 -> run
    30:31:int com.example.locks.Store.read():19:19 -> run
    30:31:void com.example.locks.MainActivity.freeze():21 -> run
com.example.locks.Store -> a.c:
# {"id":"sourceFile","fileName":"Store.java"}
    java.lang.Object lock -> a
    long value -> b
    0:2:void <init>():3:3 -> <init>
    3:10:void <init>():4:4 -> <init>
"""

MAP_ID = "r8-map-id-4393c57b0c7168f3e64b60a24500a91f013ffb62811e70c4e12a368195dbb312"

# The frames each build wrote for the worker (owner) and the main thread
# (blocked), in the lock slice and in the record.
SLICE = {
    "lines": ("monitor contention with owner Holder (21322) at void a.a.run()(SourceFile:6) "
              "waiters=0 blocking from void a.b.run()(SourceFile:3)"),
    "default": (f"monitor contention with owner Holder (21747) at void a.a.run()({MAP_ID}:6) "
                f"waiters=0 blocking from void a.b.run()({MAP_ID}:3)"),
    "offsets": ("monitor contention with owner Holder (22106) at void a.a.run()(SourceFile:-1) "
                "waiters=0 blocking from void a.b.run()(SourceFile:-1)"),
}
FRAMES = {
    "lines": ("a.b.run(SourceFile:3)", "a.a.run(SourceFile:6)"),
    "default": (f"a.b.run({MAP_ID}:3)", f"a.a.run({MAP_ID}:6)"),
    "offsets": ("a.b.run(unavailable:6)", "a.a.run(unavailable:20)"),
}

_RECORD = """\
Drop box contents: 195 entries
Max entries: 1000
Searching for: data_app_anr

========================================
2026-10-10 21:23:57 data_app_anr (compressed text, 47748 bytes)
Process: com.example.locks
PID: 21289
UID: 10207
Frozen: false
Flags: 0x30a8be44
Package: com.example.locks v0
Foreground: Yes
Process-Runtime: 48672314
Activity: com.example.locks/.MainActivity
Loading-Progress: 1.0
Dropped-Count: 0

CPU usage from 0ms to 11366ms later (2026-10-10 21:23:45.645 to 2026-10-10 21:23:57.011) with 99% awake:
  62% 21289/com.example.locks: 13% user + 49% kernel / faults: 3860 minor 183 major
  45% 26771/system_server: 18% user + 26% kernel / faults: 21862 minor 1430 major
28% TOTAL: 9.1% user + 18% kernel + 0.6% iowait + 0.5% softirq
Subject: Input dispatching timed out (6b2eec com.example.locks/com.example.locks.MainActivity (server) is not responding. Waited 10003ms for MotionEvent)


----- pid 21289 at 2026-10-10 21:23:48.074648334+0200 -----
Cmd line: com.example.locks
ABI: 'arm64'
Build type: optimized
suspend all histogram:	Sum: 5.384ms 99% C.I. 8us-4162.559us Avg: 336.500us Max: 4353us
DALVIK THREADS (3):
"main" prio=5 tid=1 Blocked
  | group="main" sCount=1 ucsCount=0 flags=1 obj=0x7303d4e8 self=0x7f4b437800
  | sysTid=21289 nice=-10 cgrp=default sched=0/0 handle=0x7f4cafe500
  | state=S schedstat=( 171103638 3706303 266 ) utm=8 stm=8 core=5 HZ=100
  | stack=0x7fe70f3000-0x7fe70f5000 stackSize=8188KB
  | held mutexes=
  at {main}
  - waiting to lock <0x08e55d90> (a {monitor}) held by thread 14
  at android.os.Handler.handleCallback(Handler.java:942)
  at android.os.Handler.dispatchMessage(Handler.java:99)
  at android.os.Looper.loopOnce(Looper.java:226)
  at android.os.Looper.loop(Looper.java:313)
  at android.app.ActivityThread.main(ActivityThread.java:8762)
  at java.lang.reflect.Method.invoke(Native method)
  at com.android.internal.os.RuntimeInit$MethodAndArgsCaller.run(RuntimeInit.java:604)
  at com.android.internal.os.ZygoteInit.main(ZygoteInit.java:1067)
DumpLatencyMs: 5.06169

"HeapTaskDaemon" daemon prio=5 tid=6 WaitingForTaskProcessor
  | group="system" sCount=1 ucsCount=0 flags=1 obj=0x2011a20 self=0x7e98efe400
  | sysTid=21295 nice=4 cgrp=default sched=0/0 handle=0x7e8a9d5c30
  | state=S schedstat=( 2490924 892500 17 ) utm=0 stm=0 core=0 HZ=100
  | stack=0x7e8a8d2000-0x7e8a8d4000 stackSize=1039KB
  | held mutexes=
  native: #00 pc 000870e0  /apex/com.android.runtime/lib64/bionic/libc.so (syscall+32)
  native: #01 pc 00379f84  /apex/com.android.art/lib64/libart.so (art::ConditionVariable::TimedWait+296)
  at dalvik.system.VMRuntime.runHeapTasks(Native method)
  at java.lang.Daemons$HeapTaskDaemon.runInternal(Daemons.java:609)
  at java.lang.Daemons$Daemon.run(Daemons.java:135)
  at java.lang.Thread.run(Thread.java:1571)
DumpLatencyMs: 0.48

"Holder" prio=5 tid=14 Runnable
  | group="main" sCount=0 ucsCount=0 flags=0 obj=0x2200c60 self=0x7e98f69000
  | sysTid=21322 nice=0 cgrp=default sched=0/0 handle=0x7e7d7d2c30
  | state=R schedstat=( 15611645905 6284419 61 ) utm=300 stm=1260 core=6 HZ=100
  | stack=0x7e7d6cf000-0x7e7d6d1000 stackSize=1039KB
  | held mutexes= "mutator lock"(shared held)
  at {holder}
  - locked <0x08e55d90> (a {monitor})
  at java.lang.Thread.run(Thread.java:1571)
DumpLatencyMs: 4.56346

----- end 21289 -----
"""


def record(build: str = "lines", monitor: str = "java.lang.Object") -> str:
    """The device's record of the freeze, with the frames that build wrote."""
    main, holder = FRAMES[build]
    return _RECORD.format(main=main, holder=holder, monitor=monitor)


def checkout(root: Path) -> Path:
    """The app's sources in a checkout of the usual shape."""
    (root / "app").mkdir(parents=True, exist_ok=True)
    (root / "build.gradle.kts").write_text("", encoding="utf-8")
    (root / "app/build.gradle.kts").write_text("", encoding="utf-8")
    src = root / "app/src/main/java/com/example/locks"
    src.mkdir(parents=True, exist_ok=True)
    for name, text in SOURCES.items():
        (src / name).write_text(text, encoding="utf-8")
    return root
