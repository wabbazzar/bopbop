# BopBop local harness experiments

This is an archived, standalone, standard-library-only probe of three small
agent loops against an installed Ollama `gpt-oss:20b` model. Each trial gets a
fresh fixture under the repo's ignored `tmp/harness-experiments/`; no live
BopBop configuration, service, or user files are changed.

The trials compare native calls to several narrow tools, native calls to one
generic workspace tool, and a JSON action protocol. They test file reading,
editing, and using test feedback. The original result is preserved in
`results.json`. Run `python3 compare.py` from this directory to repeat the probe;
new results go to the ignored `tmp/harness-experiments/results.json`.

Try the resulting narrow-tool harness with:

```bash
python3 ../../../bin/bopbop-local.py --workspace /path/to/a/test/project
```

It opens an interactive terminal loop; type `/exit` to leave. Or append a
prompt for one turn. It reads top-level `AGENTS.md`, `CLAUDE.md`, and
`personality.md` if present. `run_command` runs with your user permissions,
and this harness has no approval step. Use a test workspace while exploring.
The terminal displays activity during each model call and prints exact token
and timing telemetry from Ollama when that call completes.
See the main README's "Local Ollama agent" section for current CLI behavior.
