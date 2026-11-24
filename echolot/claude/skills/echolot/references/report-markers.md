# `markers` — your own names, measured every time

A detector shows a marker only where a threshold says so. `markers` shows
every name that is the project's — whatever carries
`instrumentation.temp_prefix`, and whatever `domains` lists — with the same
columns as a detector's rows and the same merge across repeats: `runs`,
medians, `spread`. `self_ms` subtracts the children, so a marker that wraps
another reads as the difference: `AGENTTMP_store_update` minus
`AGENTTMP_store_update_locked` is how long the lock was waited for.
`detail` is the thread, or `(async)` for a `beginAsyncSection` span, which
is what most of a project's own markers are.

This is the table for the markers you planted. Read it from the report;
do not rebuild it by running `names` once per trace.

`unfinished: true` is a marker that never closed in at least one repeat: its
end did not run — an exception, a suspended coroutine, an end on another
thread — or the recording stopped first. Its numbers run to the end of the
window and are a floor; the markdown prints them as `≥ 955.0`. Say "at least"
when you pass such a number on.

`absent` lists the `domains` names the window never held in any repeat: a
map pointing at something this scenario does not run, or a name that
changed under it. The markdown says the same under "Not in the window".

`parents` on a temporary marker's row names the slices it sat directly in,
with the time it spent in each. A marker under a slice is its child, so that
slice's self time drops by this much in the round that adds it; `compare`
reads `parents` and judges such a slice on its total (`into_markers`,
`*(by total)*`). Do not report that drop as the slice getting faster.

`mixed` lists temporary markers found in some repeats and not others, with
the slices they sit in: the set is two rounds, one with markers and one
without, and those slices' self-time medians mix two measures. The markdown
warns under the markers table. Analyze the rounds apart.

`in_trace` is every prefixed name the traces hold, the window or not.
`unbuilt` appears when the code has temporary markers — the prefixed names
on lines tagged `// echolot:mark` — and none of them is in `in_trace`: the
app recorded was built before them. It carries their `names`, the `files`,
the `mode`, and `install`, the gradle tasks that build and install the
variant the config measures; the markdown says it at the top. Read nothing
about the markers off that round. Build and install the app and record
again; in gradle mode `collect` builds it, so recording again is the whole
step. A fresh round that still holds none of them means the scenario does
not run the code they wrap. A name the code puts together at run time
(`"AGENTTMP_load_$id"`) counts as there when a trace name starts with its
fixed part. `in_build: true` says `collect` read the build on the phone
before it recorded and found every one of them there: the scenario did not
run the code they wrap, and there is nothing to rebuild. When the app drew no
frame in any repeat, the markdown says the same.

`removed` appears when the traces hold prefixed names that no string in the
code makes any more, tagged or not: `mark --remove` took them out after the
app recorded was built, so that app has none of the changes made to the code
since, a fix among them. It carries their `names`, the `mode` and `install`,
as `unbuilt` does, and the markdown says it at the top. Compare nothing
against that round and read no fix off it: build and install the app, and
record again; in gradle mode, record again.
