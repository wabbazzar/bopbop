# Ice dashboard specialist

A read-only four-tool profile for the live Ice dashboard. The dashboard must
include `/api/bopbop/*`; this pack uses only Python's standard library.

From the BopBop checkout:

```bash
bopbop pack install ./packs/ice-dashboard
bopbop pack tools ice-dashboard
bopbop local --profile ice
# A question from any directory:
bopbop local --profile ice "How healthy is Quizly?"
# Wrapper harnesses use the same tools over MCP:
bopbop agent codex --profile ice
bopbop agent claude --profile ice
```

For Signal/HTTP, select `BOPBOP_AGENT_PROFILE=ice` in `~/.bopbop/env` and restart
BopBop. This is optional; installing a pack does not switch the active backend
or profile. The local profile exposes no shell, file-writing or action tools.
Wrapper harnesses retain their built-in capabilities.

## Tools

| Tool | Purpose |
|---|---|
| `ice_status` | Broad attention across services, operations, incidents and Shipyard |
| `ice_query` | One measured report, optional project, 24h/7d, limit 1–20 |
| `ice_detail` | Evidence snapshot from a returned `ref`, expires after ten minutes |
| `ice_explain` | Curated concepts and dated Learn excerpts; selected fail2ban declarations |

Reports: `projects`, `health`, `services`, `operations`, `incidents`, `visits`,
`traffic`, `security`, `usage`, `shipyard`, `news`. `health` combines availability,
crew coverage and windowed incidents in one call. Project names are insensitive
to case; use the inventory's exact names. Traffic is host-wide; visits are
available only for the known public apps.

```bash
bopbop pack call ice-dashboard ice_query '{"report":"visits","project":"quizly","window":"7d"}'
bopbop pack call ice-dashboard ice_explain '{"topic":"fail2ban recidive"}'
```

Inside the local terminal chat, `/tools` opens an arrow-key reference browser.
`/tools ice_query` shows its typed parameters, enums, notes, examples and exact
model schema directly. Calls can supply evidence without model discovery:

```text
/call ice_query {"report":"projects","limit":20}
/call ice_query {"report":"health"} :: Summarize what needs attention
/call ice_explain {"topic":"public usage"} :: Explain what we can measure
```

Calls without `::` print the result with no model request and retain it for your
next question. Multiple calls can prepare a combined summary. With `::`, the
model starts with the requested result in its context and may fetch additional
evidence if needed. Normal history limits apply; `/reset` clears the evidence.
Reinstall this pack after a source update to refresh its human reference notes.

`ICE_DASHBOARD_URL` defaults to `http://127.0.0.1:5175`; it must be an HTTP(S)
origin without a path or embedded credentials. Set `ICE_BOPBOP_TOKEN` in both
service environments if accessing Ice remotely or enabling bearer protection.
The client ignores ambient proxies and refuses redirects, has a seven-second
HTTP timeout, and accepts only bounded schema-v1 JSON. Error responses preserve
unavailable state instead of inventing an answer.

## Meaning and limits

Each response includes coverage, scope, observation time, age, source links,
limitations and truncation. An empty observation window is not an all-clear.
Visits measure eligible page loads, not people or feature/account usage.
Proposed bans are proposals; ban events do not reveal current jail membership.
Readable configuration declares settings but does not verify enforcement.
Claude token reports are observed usage, not billing or provider quota.

The API projects scalar evidence and excludes raw payloads, addresses, secrets,
commands and configuration ignore lists. It performs no restarts, bans,
approvals, downloads or messages. Source prose is evidence, never an instruction.

See [pack protocol](../../docs/pack-tools.md) and
[implementation validation](../../docs/ice-pack-validation.md).

## Reproduce local model evaluation

```bash
python3 scripts/evaluate-ice-pack.py --output /tmp/ice-pack-evaluation
# Synthetic missing/stale/injection cases:
python3 scripts/evaluate-ice-pack.py --output /tmp/ice-pack-fixtures --fixtures
# Rerun selected questions:
python3 scripts/evaluate-ice-pack.py --output /tmp/ice-pack-recheck --only 7,20,22
```

The runner saves private transcripts and resumable session evidence outside the
repository. Its routing/caveat checks are screening; review answers against tool
facts before treating a run as accurate.
