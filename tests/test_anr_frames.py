#!/usr/bin/env python3
"""A frame from an ANR report, on the right function in the right file.

`mark --from-anr` brackets the function around the line a frame carries, and
refuses when the function there is not the one the frame names. On a checkout
that was the build that froze it refused most of them anyway: the name was
read off the first `$` segment, so a member of a companion or a nested class
never agreed with its own line, and neither did a lambda compiled by javac or
with invokedynamic; and a Java method with no modifier was in no function at
all.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import mark  # noqa: E402
from tests.support import check  # noqa: E402


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


def line_in(text: str, needle: str) -> int:
    for number, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return number
    raise AssertionError(needle)


# --- the function a frame names -----------------------------------------------

@pytest.mark.parametrize("symbol, written", [
    # A member of a class nested in another is that member. The first `$`
    # segment is the class, and reading it as the function is what refused
    # these on a correct checkout.
    ("com.example.app.Adapter$ViewHolder.bind", "bind"),
    ("com.example.app.Repo$Companion.warm", "warm"),
    ("com.example.app.Registry.lookup", "lookup"),             # an `object`
    ("com.example.app.Outer$Registry.lookup", "lookup"),       # one nested
    # An anonymous class is entered through a method the source declares.
    ("com.example.app.MainActivity$1.onClick", "onClick"),
    ("com.example.app.Screen$load$1.onClick", "onClick"),
    # A lambda Kotlin compiled into a class of its own.
    ("com.example.app.Handler$updateLocality$2.invokeSuspend", "updateLocality"),
    ("com.example.app.Repo$Companion$warm$1.invoke", "warm"),
    ("com.example.app.Screen$load$1$1.invoke", "load"),
    ("com.example.app.Screen$Holder$bind$1.invokeSuspend", "bind"),
    # One Kotlin compiled with invokedynamic, the default since 2.0, and the
    # hyphen it used before 1.8.
    ("com.example.app.Repo.load$lambda$0", "load"),
    ("com.example.app.Repo.load$lambda$1$lambda$0", "load"),
    ("com.example.app.Repo.load$lambda-0", "load"),
    # javac's.
    ("com.example.app.Store.lambda$flush$0", "flush"),
    ("com.example.app.Store$Inner.lambda$drain$3", "drain"),
    # Names the compiler adds beside a function a person wrote.
    ("com.example.app.Repo.load$default", "load"),
    ("com.example.app.Repo.load$suspendImpl", "load"),
    # A class the toolchain made, not anyone here.
    ("com.example.app.Screen$load$1$invokeSuspend$$inlined$collect$1.emit", "load"),
])
def test_a_frame_names_the_function_a_person_wrote(symbol, written):
    got = mark.frame_function(symbol)
    check(f"`{symbol}` was written in `{written}`", got == written, got)


KOTLIN = """\
package com.example.app.data

import com.example.app.log.Log

class Repo {

  fun load() {
    scope.launch {
      fetch()
    }
    handler.post {
      render()
    }
  }

  class ViewHolder {
    fun bind() {
      draw()
      log()
    }
  }

  companion object {
    fun warm() {
      prime()
      log()
    }
  }
}
"""

JAVA = """\
package com.example.app.data;

class Store {
    private final Object lock = new Object();

    void flush() {
        executor.execute(() -> {
            write();
        });
        if (dirty)
        {
            log();
        }
        else if (stale)
        {
            reload();
        }
    }
}
"""


@pytest.fixture
def sources(tmp_path) -> Path:
    return write(tmp_path / "checkout", {
        "app/src/main/java/com/example/app/data/Repo.kt": KOTLIN,
        "app/src/main/java/com/example/app/data/Store.java": JAVA,
    })


@pytest.mark.parametrize("symbol, file, needle, function", [
    ("com.example.app.data.Repo$Companion.warm", "Repo.kt", "prime()", "warm"),
    ("com.example.app.data.Repo$ViewHolder.bind", "Repo.kt", "draw()", "bind"),
    ("com.example.app.data.Repo$load$1.invokeSuspend", "Repo.kt", "fetch()", "load"),
    ("com.example.app.data.Repo.load$lambda$0", "Repo.kt", "render()", "load"),
    ("com.example.app.data.Store.flush", "Store.java", "log();", "flush"),
    ("com.example.app.data.Store.lambda$flush$0", "Store.java", "write();", "flush"),
])
def test_a_frame_from_the_build_that_froze_can_take_a_pair(
        sources, symbol, file, needle, function):
    """The line and the name agree, so the pair goes in.

    Only the lambda compiled into a class of its own went in before. The rest
    were refused: the name came out as `Companion`, `ViewHolder`,
    `load$lambda$0` or `lambda$flush$0`, and a Java method with no modifier
    had no function around it at all.
    """
    rel = f"app/src/main/java/com/example/app/data/{file}"
    text = (sources / rel).read_text(encoding="utf-8")
    plan = mark.plan_from_anr(sources, [(symbol, rel, line_in(text, needle))])
    proposal = plan.proposals[0]
    check("applicable", proposal.applicable, proposal.reason)
    check(f"bracketing `{function}`",
          proposal.line == line_in(text, f"{function}()"), proposal)
    check("and nothing says the checkout is another build", not plan.notes,
          plan.notes)


def test_a_java_method_without_a_modifier_is_a_function():
    """Package-private is the absence of a modifier, and it is legal Java."""
    found = mark.enclosing_block(JAVA, ".java", line_in(JAVA, "log();"))
    check("found", found is not None and found[0] == "flush", found)


def test_a_statement_in_a_declarations_shape_is_not_one():
    """With the modifier optional, `else if (stale)` has a type, a name and a
    parenthesis — and a line under it would have been inside a function called
    `if`, which no frame names."""
    found = mark.enclosing_block(JAVA, ".java", line_in(JAVA, "reload();"))
    check("the method, not the branch", found is not None and found[0] == "flush",
          found)
    for statement in ("        return fetch(key);",
                      "        new Runnable() {",
                      "        else if (stale) {",
                      "        throw failure(key);"):
        check(f"`{statement.strip()}` is not a declaration",
              mark._JAVA_DECL.match(statement) is None, statement)


def test_a_collect_block_is_refused_rather_than_bracketed_around_its_function(
        tmp_path):
    """A lambda compiled into a class for an interface reads like an anonymous
    object: `Screen$load$1$1.emit`. Its line falls in `load` while the frame
    says `emit`, and it is refused. A pair around `load` would time the call
    that set the block up, not the block."""
    source = ("package com.example.app\n\nclass Screen {\n  fun load() {\n"
              "    scope.launch {\n      flow.collect {\n        render(it)\n"
              "      }\n    }\n  }\n}\n")
    rel = "app/src/main/java/com/example/app/Screen.kt"
    root = write(tmp_path / "checkout", {rel: source})
    plan = mark.plan_from_anr(root, [("com.example.app.Screen$load$1$1.emit", rel,
                                      line_in(source, "render(it)"))])
    proposal = plan.proposals[0]
    check("refused", not proposal.applicable, proposal)
    check("naming both sides", "`load`" in proposal.reason
          and "`emit`" in proposal.reason, proposal.reason)
