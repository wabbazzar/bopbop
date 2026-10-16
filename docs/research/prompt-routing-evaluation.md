# Prompt routing investigation evaluation

## Scope and changes

This round improves native Ollama's ability to explain BopBop's own instruction
assembly. It does not implement transcript reset behavior or change the default
model, reasoning level, or context size.

- `5344aa2`: generated provenance describes the selected base, workspace files,
  skill index, packs, tools, and assembly source. Investigation guidance asks the
  model to trace conditions and distinguish replacement from concatenation.
- `cffe620`: offline `--show-prompt` previews fresh assembly; the owning
  [routing reference](../prompt-routing.md) explains native and wrapper paths.
- `72b3abb`: runtime context states lifecycle and harness boundaries explicitly.

## Method

Live probes used `gpt-oss:20b`, the default low reasoning setting, a 16,384-token
context, and the actual native tool loop. Each started a separate conversation.
The probe blocked writes and allowed source inspection and offline prompt
preview. It did not inject messages into the user's conversation or Signal.
Transient scripts, logs and answers are under ignored `tmp/prompt-routing/`.

These were iterative diagnostic probes, not a statistically reliable benchmark.
Some later prompts overlap facts added to the system instructions. They test
whether the model follows those facts, not general repository reasoning.

## Observed results

| Question | Result |
| --- | --- |
| Are workspace and dashboard bases combined? Does `ice_query` report selection switch them? | After adding the source reference and guide, the model correctly explained replacement, profile selection, and tool results as evidence. Its closing summary still oversimplified flag versus environment selection. |
| Compare empty profile plus a pack against a named profile; explain workspace overrides and environment defaults. | Improved, but inconsistent. Early runs combined bases or retained built-ins in profile mode. A later run got the main comparison right but contradicted itself about an omitted flag and misstated the pack alias/base relationship. |
| Does a fresh preview describe the running chat? What do refresh/reset retain? How do wrappers differ? | Explicit lifecycle instructions improved preview/live and wrapper distinctions. One answer still falsely claimed refresh clears history and treated saved conversation state as an exact request log. The instructions were refined to address those errors. |

One initial broad recursive search was aborted after repeated timeouts. A later
lifecycle probe failed with `Ollama stream ended before the final usage record`;
that run produced no answer and is not counted as a correctness result.
The retry completed but regressed: it claimed refresh rebuilds the prompt,
suggested adding logging instead of using F4, and invented wrapper tool-schema
details. Explicit facts in the system prompt did not prevent these errors.

## Verification and limitations

The Python suite passed: **247 passed, 1 skipped**, with one existing
Starlette/httpx deprecation warning. Fixture tests verify mutually exclusive
bases, loaded file provenance, skill indexing, tool names, profile/environment
precedence, and preview behavior without inference or handler execution.
Those passes establish implementation behavior, not reliable model answers.

The model still adds unsupported details after correct explanations. Do not treat
this round as proof of reliable self-investigation. Future evaluation should
include new questions and require evidence for each routing/lifecycle claim.

Start a new local conversation to load the new system instructions. Refresh
preserves the existing system message. Fresh previews and F4 Context serve
different purposes; see the owning routing reference.
