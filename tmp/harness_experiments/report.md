# BopBop local GPT OSS 20B harness comparison

Tested on this machine with Ollama 0.30.10 and installed `gpt-oss:20b`, CPU
inference, 8,192 token request context. Each variant ran the same two isolated
tasks: find a code in a file, and fix a small Python function then run its test.
No live BopBop service or configuration was changed.

| Loop | Find | Edit + test + final reply | Warm elapsed time |
| --- | --- | --- | --- |
| Native Ollama calls, four narrow tools | Pass | Pass | 10.3s / 45.8s |
| Native Ollama calls, one generic workspace tool | Pass | Pass | 11.4s / 41.5s |
| Prompted JSON action protocol | Fail | Fail | Failed at first step |

The first native run also passed both tasks. It was initially capped at five
model steps and ended after the edit test without a final reply; the repeat
raised the limit to seven and required a final reply to pass. The JSON variant
returned an unrequested `repo_browser.list` tool call with empty content despite
receiving no tool schemas, so parsing it as a JSON action failed both times.

**Recommendation:** use Ollama native tool calls with narrow, explicit tools.
It was as fast as the generic tool in this small warm run, and each tool has a
clear argument schema and dispatch path. `../../bin/bopbop-local.py` is a standard-library
terminal prototype of that loop. It does not use Codex or Claude Code.

The benchmark only covers two small tasks, and its elapsed times include model
generation on a CPU. It does not establish reliability for broad coding tasks,
long conversation memory, image inputs, or Signal delivery. The prototype has
in-process conversation history and no durable sessions or approval mechanism.
Its `run_command` tool executes with the user's permissions.
