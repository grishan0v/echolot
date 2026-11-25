# Names vulture reports as unused that are used, by something it cannot see.
#
# vulture reads this file as code alongside the package ([tool.vulture] in
# pyproject.toml): a name mentioned here counts as used. Nothing imports or
# runs it. CI runs vulture at 80% confidence, which only the arguments at the
# bottom reach; the rest is here so that `vulture --min-confidence 60` reads
# only what is new, when someone goes looking for code left behind by a
# refactor (#995).
#
# A name here is ignored everywhere in the package, not only where it was
# found: keep each one specific, and say who uses it.
#
# The self-checks in selftest.py are all called `_` and found by the `@check`
# decorator that registers them; pyproject.toml ignores that decorator rather
# than listing `_` here.

# --- fields of Perfetto's protobuf messages, set on the trace fixture.py writes,
# and read by trace_processor.
_.callstack_iid
_.comm
_.cpu_id
_.cpu_mode
_.extendee
_.frequency
_.function_name_id
_.io_wait
_.kernel_frames
_.mapping_id
_.next_prio
_.prev_prio
_.prio
_.rel_pc
_.sample_skipped_reason
_.scan_all_processes_on_start
_.sequence_flags
_.success
_.target_buffer
_.target_cpu
_.thermal_zone
_.trusted_packet_sequence_id
_.type_name

# --- fields of Perfetto's deobfuscation packet, set by mapping.py and read by
# trace_processor.
_.obfuscated_name
_.deobfuscated_name

# --- the shapes of the files echolot writes (files.py TypedDicts). A field is
# there to say what the file holds; the code reads it with `.get("…")`, which
# vulture does not count.
generated_at
config_path
matched_by
delta_ms
noise_floor
holds_against
opened_at
touched_at
verdict_check
finished

# --- fields of reflect's `Ask`, filled from the session and read by
# render.py as a dict after `asdict`.
options
recommended
answered_after_s

# --- the standard library calls these by name.
# ui.py: BaseHTTPRequestHandler dispatches `do_<METHOD>` and reads the version
# strings; HTTPServer and ThreadingMixIn read the rest.
_.do_GET
_.do_HEAD
_.do_POST
_.do_PUT
_.do_PATCH
_.do_DELETE
_.do_OPTIONS
_.log_message
_.handle_error
_.server_name
_.server_port
server_version
sys_version
protocol_version
daemon_threads
# parallel.py: `_Kept` stands in for sys.stdout and sys.stderr while workers
# run, and `writelines` is part of what a stream is: any code that writes to
# one may call it.
_.writelines
# main.py: rich_argparse reads it from the formatter class.
_.group_name_formatter

# --- a config key declared so that a config that sets it loads (config.key,
# #834): the agent reads it, as its skill says, and no code does.
ON_EXHAUSTED

# --- used by the tests only, and kept for them.
# fixture.py: the trace test_outlier_families.py builds to test #912; the
# fixture module holds every synthetic trace, the ones only tests build
# included.
nested_block
# moves.py and pages/kit.py: the whole vocabulary, which the tests hold the
# words, tints and icons against (test_moves.py, test_compare_page.py,
# test_answer_page.py).
MOVEMENTS
MOVES
STATES

# --- arguments a signature requires and the body does not read. These are
# what CI's 80% reaches.
# `__exit__(self, *exc)`: the context manager protocol.
exc
# argparse.Action.__call__.
option_string
# socketserver's handle_error.
client_address
# mapping.py: part of an lru_cache key, so that a file written again is
# read again.
mtime_ns
