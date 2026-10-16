# BopBop investigating its own terminal (2026-10-16)

## Scope

Improve investigation and tool recovery without implementing the requested
transcript reset. Existing unrelated working-tree changes were preserved.

The live question was:

> I would like the chat history panel to clear when using /reset, but it does
> not. Investigate which of BopBop's code files we need to change and explain
> why. Do not implement the changes.

## Engine changes

- Unsupported workspace tools return available tool names and a concrete
  `run_command` search example.
- Workspace instructions explain source searching, shell-free commands,
  error recovery, ownership references, and verification of the actual
  display-writing path before recommending changes.
- Malformed command arguments return a correctable error instead of reaching
  `shlex` with an array and raising an exception.
- Workspace turns allow 24 model calls rather than 12. Profile turns keep
  their four-step limit. More calls alone did not fix reasoning errors.
- `read_file` supports positive, inclusive `start_line`/`end_line` ranges,
  rejects unsupported argument names, and reports truncation within the
  existing 16,000-character output limit. This prevents silently treating
  a truncated file or ignored line-range request as complete evidence.

The first four changes are in separate commits: `1fa9da5`, `b596d22`, `f177a8c`,
and `ebce624`. No transcript-reset code was changed.

## Live evaluation and limits

Used the actual local engine, GPT-OSS 20B, and a 16,384-token context against
this working checkout. Isolated probe scripts intercepted file writes and
restricted command execution to source-reading utilities. They did not inject
messages into the user's active terminal conversation. Requests and outputs
remain under ignored `tmp/self-investigation/`; they are not public fixtures.

The first probe exposed an array-valued command and crashed in its own guard;
this also exposed the engine's missing command type check. Subsequent runs
recovered from malformed commands and unsupported tools, but several answers
were confidently wrong. They confused prompt-entry history with transcript
storage, claimed the engine did not clear messages, or invented a missing
worker completion event. A 24-step run still exhausted its budget. Medium
reasoning also produced an incorrect answer, so the default was not changed.

Direct steering asked the model to inspect the complete worker function,
UI constructor, and append method without giving it the answer. It then
identified `self.transcript.buffer` and the `set_document` write correctly.
A follow-up carrying excessive old tool output was pruned by the existing
context budget; carrying forward only the model's concise finding preserved
that finding for the next question. This is a remaining context-management
limitation, not a solved problem.

With its own corrected finding retained, the model correctly explained that
engine conversation reset leaves the UI transcript intact. Its proposed fix
still unnecessarily involved the engine, so another review asked it to
respect the architecture boundary and distinguish required changes from an
optional event protocol. This is a steered result, not evidence that arbitrary
self-investigation is now reliable without review.

## Verification

Full Python suite before the final-answer addition: **244 passed, 1 skipped**, with one existing dependency
deprecation warning. The skipped test is the opt-in completion-model test;
these investigation rides were separate live model calls. The first full run
after adding truncation notices caught an output-cap regression; the notice
was moved inside the existing cap and the full suite then passed. Tests cover
malformed argument recovery, line ranges, truncation, completion after the old
12-call limit, and the new 24-call bound. Whitespace and leak checks passed.

New sessions load the updated system guidance. Refresh preserves existing
conversation messages, including the original system prompt; it does not
retroactively replace the instructions in an existing conversation.


## Successful focused ride

After additional failed steering rounds, a focused run used GPT-OSS 20B with
`think=high`, a 32,768-token context and a 4,096-token per-call output limit.
The question above was supplemented with pointers to read `bin/bopbop_tui.py`
lines 360–480 and `bin/bopbop-local.py` lines 540–580, trace the displayed-buffer
writes and submit flow, and inspect the worker if needed. The prompt did not
state the diagnosis or provide a patch. No earlier answer was included.

It finished in **16 model calls / 15 tool calls**, correctly identifying:

- The existing `/reset` branch in `TerminalUI.submit()` clears attachments but
  leaves the displayed transcript intact.
- `self.transcript.buffer` stores the displayed chat; prompt history is different.
- The engine already clears model messages and session statistics.
- The smallest production change belongs in `bin/bopbop_tui.py`: clear the
  read-only transcript buffer through `set_document(..., bypass_readonly=True)`
  and reset its eviction count. No engine change is required for this behavior.

Its suggested patch was reviewed as text only. Neither the evaluator nor the
model applied it. The working-tree reset implementation remains unchanged.
The raw answer and calls are in ignored `tmp/self-investigation/ride-15-synthesis.json`.
This establishes a correct answer **with source-location steering and larger
inference budgets**, not reliable unaided investigation at the default settings.
High reasoning and 32K context were test overrides; defaults remain unchanged.

A prior high-reasoning run read a draft of this evaluation report, so it cannot
serve as independent evidence; it failed to finish anyway. The draft was moved
out of searchable source before the successful run. Several other runs failed
from context or tool-budget exhaustion. No failed run was counted as a pass.

## Final-answer budget

Workspace turns now reserve one additional model call after 24 tool-capable
steps. That final request has no tools and asks for verified findings and
explicit uncertainty. If a backend still returns tool calls, they are not
executed. Profile limits remain unchanged. The successful live run finished
before this fallback; its benefit is verified by fixture tests, not established
by that live result.

Final full-suite verification after `9aeb40b`: **245 passed, 1 skipped**, with
one dependency deprecation warning. Final-answer tests cover successful
synthesis and refusal to execute tools returned despite the no-tools request.
