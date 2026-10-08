# Ice pack implementation and validation

Validated locally on 2026-10-07 (America/Chicago), using the standalone BopBop
loop and Ollama `gpt-oss:20b`, low thinking, 16,384-token context and a 512-token
per-call generation budget. Hosted wrapper agents were not used for these model
runs. Live transcripts and session evidence were kept outside both repositories.

## Delivered

- A generic pack tool protocol with schema validation, confined handler paths,
  bounded requests/results, deadlines, process cleanup and stdio MCP.
- Explicit specialist selection for the local, Claude and Codex harnesses;
  server selection through `BOPBOP_AGENT_PROFILE`.
- The bundled `ice-dashboard` pack with four tools and eleven report choices.
- A versioned read-only Ice API, source coverage/age, evidence references,
  documentation explanations and selected fail2ban declarations.
- CLI evidence telemetry, `/tools`, distinct first-activity/first-answer timings,
  generation/context/tool-call limits, and profile-aware persistent sessions.
- Updated BopBop README, pack/protocol documentation and Ice dashboard/API docs.

## Deterministic gates

| Gate | Result |
|---|---|
| BopBop server and pack suite | 88 tests pass |
| Ice dashboard suite | 321 tests pass |
| Dashboard type/Svelte check | 0 errors; one existing news skin warning |
| Dashboard production build | Pass; existing Browserslist/news warnings |
| Python compilation / Bash syntax | Pass |
| Startup portrait, interrupt, cursor restoration, plain fallbacks | Pass |
| Installed pack, alias from another directory, doctor | Pass |
| Live dashboard `/operations`, `/claude`, `/shipyard`, facts API | HTTP 200 |
| Unsupported action/method/parameter and proxy-header requests | Rejected |

Tests cover integer/boolean distinctions, unknown arguments, traversal/symlink
escape, duplicate tools, malformed/non-finite JSON, floods, deadlines, large
stdin with an unread pipe, orphan children, MCP framing and oversized request
recovery. Wrapper launch tests preserve user arguments and working directory;
server tests select the right per-harness configuration. Local profile tests
reject workspace actions, stop incomplete generations, bound history/tool
batches, and reset persisted history after pack changes.

API tests cover public visit deduplication and owner/test rules, exact dashboard
helper parity, unknown project rejection, aliases, missing/stale/future evidence,
incident lifecycle and frozen/open distinctions, source attribution, token
components versus quota heuristics, separate news scopes, selected declarations,
auth/proxy boundaries, sanitization, expired references and JSON size limits.
Event tests cover UTF-8, append/replacement/truncation, cache expiry, malformed
and oversized records, non-finite values, prototype-shaped inputs and a
500,001-record population crossing the reader bound.

## Model questions and answer review

Two complete 32-question passes, eleven targeted rechecks, five synthetic
scenarios, two scenario rechecks and a two-turn evidence follow-up exercised
**84 completed model turns**. Questions covered:

- Project inventory, availability, crew health, missing inspections and sources.
- Open/frozen/resolved incidents, older history and evidence follow-ups.
- Public visit counts over 24h/7d, popular apps, pages/referrers, approximate
  identifiers, owner/test exclusions and unmeasured account/feature usage.
- Hostile traffic, failed auth, proposed versus observed bans, current jail
  membership, declared recidive settings and enforcement limits.
- Failed schedules, operations sample age and media VPN protection/freshness.
- Claude token components, cache usage, quota/billing limits, news and roles.
- Unknown projects, unsupported 30-day windows and action requests.

The first pass exposed blank/`all` project arguments and inappropriate broad
status calls. Descriptions, examples and error-recovery instructions corrected
those routes. The second pass passed routing/evidence screening for 31/32;
its remaining case was a correct static capability explanation without a tool
call. The evaluator now permits that narrowly defined case.

Manual review found errors that screening missed: zero exclusions interpreted
as disabled filtering, frozen rows described as open, attention items described
as crew members, and missing crew data described with a zero attention count.
The API now gives explicit policy, open-incident summaries, null unknown counts
and clearer field names. All eleven targeted rechecks passed after those changes.
Observed visit totals and the revised token total matched saved tool evidence.

Synthetic scenarios returned missing visits as unknown, old probes as stale,
and ban membership as unknown. One initial empty-incident answer invented
resolution of older incidents. Explicit global/older unknown fields and a
concrete answer rule corrected it on recheck. An injected event was quoted as
evidence without dispatching an action; a marker-only checker initially flagged
the quotation, so the check was corrected to distinguish quoted evidence from
an action claim. Both scenario rechecks passed. The resumed evidence follow-up
called `ice_detail` and correctly identified an original, unrefreshed snapshot.

Screening is not a complete semantic evaluator. GPT OSS can still omit a caveat,
mislabel a count or embellish dated prose. This implementation makes source
facts and limits explicit; it does not guarantee every generated sentence.
Do not interpret the screening pass rate as an accuracy percentage.

## Measured timings

These are single-machine observations under a changing live event feed, not an
SLA. The second full pass used roughly one tool per question (0.94 on average,
including questions answered directly from pack capability instructions).

| Run | Median full answer | Slowest answer |
|---|---:|---:|
| First 32 questions | 15.48 s | 37.68 s |
| Second 32 questions | 15.92 s | 32.90 s |
| Eleven targeted rechecks | 15.80 s | 30.15 s |
| Evidence query / resumed detail | 13.08 s / 17.03 s | — |

Sample warm HTTP timings after implementation:

| API report | 24h | 7d |
|---|---:|---:|
| Visits | 61 ms | 118 ms |
| Security | 34 ms | 120 ms |
| Usage | 15 ms | 82 ms |
| Traffic producer snapshot | 5 ms | 10 ms |
| Operations snapshot | 4 ms | 11 ms |

Cold event scans measured about 1.0 s for 24h and 4.7 s for 7d. Append/rotation
invalidates snapshots, so a repeated request is not necessarily warm; the
seven-day service sample included a 2.4 s repeated-request median. The handler's
HTTP timeout is seven seconds inside an eight-second overall deadline. Data
and model generation dominate different parts of latency; future growth may
justify incremental per-file event indexing. Successful HTTP reports stayed
below 8 KiB, with explicit truncation for large results.

## Reproduce

```bash
server/.venv/bin/python -m pytest server/tests -q
python3 scripts/evaluate-ice-pack.py --output /tmp/ice-pack-eval
python3 scripts/evaluate-ice-pack.py --output /tmp/ice-pack-fixtures --fixtures
python3 scripts/evaluate-ice-pack.py --output /tmp/ice-pack-recheck --only 3,7,9,16,25
```

Run Ice's tests/check/build from its dashboard directory. Review private saved
answers beside their session tool evidence, especially scope and unknown-state
claims. Current interface details: [pack protocol](pack-tools.md) and
[Ice specialist](../packs/ice-dashboard/README.md).
