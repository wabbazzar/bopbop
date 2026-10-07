# BopBop local harness experiments

Run `python3 compare.py`. This is a standalone, standard-library-only probe of
three small agent loops against the installed Ollama `gpt-oss:20b` model. Each
trial gets a fresh fixture under `tmp/harness_experiments/runs/`; no live
BopBop configuration, service, or user files are changed.

The trials compare native calls to several narrow tools, native calls to one
generic workspace tool, and a JSON action protocol. They test file reading,
editing, and using test feedback. Results are written to `results.json`.

Try the resulting narrow-tool harness with:

```bash
python3 ../../bin/bopbop-local.py --workspace /path/to/a/test/project
```

It opens an interactive terminal loop; type `/exit` to leave. Or append a
prompt for one turn. It reads top-level `AGENTS.md`, `CLAUDE.md`, and
`personality.md` if present. `run_command` runs with your user permissions,
and this prototype has no approval step. Use a test workspace while exploring.
The terminal displays activity during each model call and prints exact token
and timing telemetry from Ollama when that call completes.
See the main README's "Experimental local terminal agent" section for slash
commands, the interactive suggestion menu, and telemetry details.
