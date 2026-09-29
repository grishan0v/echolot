# Planning

[← Docs index](README.md) · [README](../README.md)

Every improvement and every bug is a GitHub issue, and every open issue is a
card on the [Echolot Roadmap](https://github.com/users/grishan0v/projects/1)
board. There is no other list. A plan kept in a local file is one nobody else
can read, and it goes stale without anyone noticing.

The board is public: anyone can see what is planned and in what order. The
fields below are set by whoever maintains the tool.

## Filing an issue

Open it from a template: **Feature request** for an improvement, **Bug report**
for a bug. The template sets the label; an issue about documentation alone
takes `documentation`. The sections of the feature template — the problem, the
proposal, the alternatives, the effect on determinism — are what the issue is
sorted by later, so a filled template is most of the work.

Before filing:

- **Search the open issues.** An idea that is already there gets a comment on
  the issue that has it.
- **Check it against what this repository has already decided.** The decisions
  that come up most are [There is no performance gate, on
  purpose](compare.md#there-is-no-performance-gate-on-purpose), [Why a CLI
  and not an MCP server](agent-layer.md#why-a-cli-and-not-an-mcp-server), and
  [the one rule](../echolot/guide/overview.md#the-one-rule): never open the
  trace yourself. An idea that runs into one of them is rewritten to fit or
  closed with the reason. [#134](https://github.com/grishan0v/echolot/issues/134)
  went the first way, [#137](https://github.com/grishan0v/echolot/issues/137)
  the second.
- **Name no real project.** The repository is public. An example needs the
  shape of the thing, and the shape is the same under any name:
  `com.example.app`, `Store`, `collection_load`
  ([#105](https://github.com/grishan0v/echolot/pull/105)).
- **Write it in English**, like everything else here.

## The four fields

Besides Status, every card carries four fields. The definitions below are also
the descriptions of the options on the board, so they are in front of whoever
sorts the cards.

### Area: which part of the tool changes

| option | covers |
|---|---|
| Core | what the tool finds and how correct it is: recording, detectors, statistics, `compare` |
| UX | how a person or an agent works with it: commands, config, output, the speed of the hunt loop |
| Integrations | how the tool reaches people and their tools: the plugin, CI, other clients |
| Health | what users do not see: code structure, documentation, privacy |

Area keeps the work balanced. It has no say in the order.

### Value: how much it is needed

One question decides it: how much worse off is a user, or whoever maintains the
tool, if this is never done?

| option | if it is never done |
|---|---|
| High | the tool gives a wrong answer, misses what it should find, or something leaks |
| Medium | the answer comes slower or less clear, fewer people reach the tool, or the next change in that place gets expensive |
| Low | a convenience nobody has missed yet |

Between two issues of the same Value, the one that came up on real runs goes
first.

### Effort: how big the build is

| option | size |
|---|---|
| S | up to a day: one module, one pull request |
| M | a few days: several modules, a new report field, documentation and a self-check |
| L | a week or more, or a new dependency |

An L is split into S and M sub-issues before work starts — **Create sub-issue**
on the parent's page. The parent keeps Area and Value and leaves Effort and
Readiness empty, since its sub-issues carry them.
[#142](https://github.com/grishan0v/echolot/issues/142) is built that way.

### Readiness: can it be built now

| option | meaning |
|---|---|
| Ready | it is clear what to build and how, and nothing blocks it |
| Spike | a check has to come first |
| Waiting | something outside blocks it: data, a device, another issue |

The test for a spike: is there an open question whose answer could cancel the
issue, or change its size by more than one step? If there is, it is a Spike. If
there is not, it is Ready, and whatever is still unknown gets settled while
building.

A spike is a check of one or two days: on a device, on real traces, or with a
prototype. It has no Effort yet. It ends with a comment on the issue that
records the answer, and then one of two things happens: Effort is set and
Readiness becomes Ready, or the issue is closed with that answer as the reason.

## Picking the next issue

1. Ready and High, smallest Effort first.
2. Alongside that, the spikes with High Value: where the most needed work has
   an unknown outcome, a check pays the most.
3. An L is split before it starts.
4. Low together with L is a candidate for closing as not planned.
5. Once a month, look at how the open issues spread across Area, so that Health
   does not wait forever.

The order is not stored anywhere. It follows from the fields, so nobody has to
keep it up to date.

## The views

| view | shows | for |
|---|---|---|
| [Status](https://github.com/users/grishan0v/projects/1/views/1) | every card by Status: Todo, In Progress, Done | what is moving |
| [Next up](https://github.com/users/grishan0v/projects/1/views/2) | open cards that are Ready and Todo, grouped by Value and sorted by Effort | the next issue, in the top row |
| [Spikes](https://github.com/users/grishan0v/projects/1/views/3) | open spikes, grouped by Value | which checks to run |
| [Matrix](https://github.com/users/grishan0v/projects/1/views/4) | every open card, in columns by Effort and rows by Value | the whole backlog at once; the column without an Effort holds the spikes and the parents |

## From a card to Done

The board moves the cards itself:

| when | the board |
|---|---|
| an issue is added to the board | sets Status to Todo |
| an issue on the board gets sub-issues | adds them to the board |
| a pull request is linked to the issue | sets Status to In Progress |
| the issue is closed | sets Status to Done |
| a card is moved to Done | closes the issue |

What is left to do by hand is the link. A pull request that finishes an issue
says `Closes #N` in its description: opening it moves the card to In Progress,
and merging it closes the issue and moves the card to Done.

An issue that will not be built is closed as not planned, with a comment that
says why — the reason is what the next person with the same idea will find —
and its card is archived from the board.

## From the command line

The same steps with `gh`. The board needs the `project` scope on its token:
`gh auth refresh -s project`.

```bash
gh issue create --title "[Feature]: …" --label enhancement --body-file issue.md
gh project item-add 1 --owner grishan0v --url <issue URL>

# the ids item-edit asks for
gh project view 1 --owner grishan0v --format json --jq .id    # the board
gh project item-list 1 --owner grishan0v --format json         # the cards
gh project field-list 1 --owner grishan0v --format json        # fields and options
gh project item-edit --project-id <board id> --id <card id> \
  --field-id <field id> --single-select-option-id <option id>

gh issue close <N> --reason "not planned" --comment "why"
gh project item-archive 1 --owner grishan0v --id <card id>
```
