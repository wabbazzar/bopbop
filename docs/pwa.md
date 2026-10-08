# BopBop PWA

The original private BopBoo frontend is restored here as a portable, optional
Svelte 5 browser app. It uses the same `/api/chat` gateway, selected harness,
context directory, packs, and SQLite session routing as the other channels.
Browser and Signal conversations have separate sessions; their configured tools
and context are shared. This pass implements text chat, expressions, telemetry,
persistence, and an installable offline shell. Voice, image attachments, Rive,
a multi-conversation picker, and browsing Signal history are future work.

## Build and run

Node.js 22+ and npm are optional build/development dependencies. A running Python
server needs only the generated `build/` files. Generated files are ignored;
source, original expression PNGs, and `package-lock.json` are tracked.

```bash
npm ci                    # rerun after pulling dependency changes
bopbop web build          # works from any directory through the installed alias
# Edit ~/.bopbop/env: BOPBOP_PWA_ENABLED=1
systemctl --user restart bopbop.service
# Local browser: http://127.0.0.1:8090/
```

`bopbop doctor` checks the build when PWA serving is enabled. Leave the flag at
`0` for a Python/Signal/CLI-only install. Enabling it without a build shows the
setup placeholder; API routes remain available. Rebuild after frontend changes.
The build script supplies one version to every SvelteKit build process; do not
replace it with a timestamp recalculated inside `svelte.config.js`.

`bopbop web dev` binds Vite to loopback and proxies `/api` to the server on 8090.
`npm run preview` previews static output only; use the Python server for the API.
Server env values initialize the harness, model, pack profile and local context
size. Use the app's **Settings** to change these values live; see below for
persistence and session behavior. Terminal interactive commands still use
their own arguments/env, independently of browser preferences.

Phone installation/offline support requires a secure browser context (HTTPS,
except for localhost). Use your existing trusted private-network HTTPS proxy;
the app doesn't provision or publish a domain. The agent retains the authority
of its configured harness. Keep the established network boundary and enable
`BOPBOP_REQUIRE_BEARER=1` when bearer authentication is needed. Settings accepts
the token; it is stored in `sessionStorage` for that tab, not in the transcript,
URLs, build output, or offline cache. Re-enter it in another tab/device.

## Interface and data

- Sky blue/lavender palette and indigo dark mode. The character uses the same
  clipped Pearl body as `assets/cli/pearl.svg`, with the CLI's laughing/wink face
  sources and alignment. All 17 expressions place face overlays on this shared
  body, replacing rectangular expression cards. The body JPEG is extracted
  unchanged from the SVG; other face sources retain the original expression
  artwork. `Character.svelte` owns the clip and feathered masks, while
  `static/character/` holds shared rasters. `<emote>NAME</emote>` is parsed across
  streamed chunk boundaries.
- Pearl shrinks during conversations to give answers more space. Starter buttons
  fill a draft, the composer expands for multiline text, and a Latest reply
  button appears while reading older messages. Primary phone controls have
  44px touch targets. Settings is a native modal dialog with a fixed close
  button and one internally scrolling form; desktop uses a centered dialog.
- Text and Markdown answers, with parsed-DOM sanitization (DOMPurify). Scripts,
  images, SVG, forms, styles, event handlers, and unsafe link protocols are removed.
- Ctrl/Cmd+Enter sends; plain Enter creates a new line. Drafting remains available
  during an active turn. New chat starts a new conversation and clears this
  device's displayed transcript, without deleting server history.
- `visualViewport` follows mobile keyboard height/offset, safe areas are respected,
  input text is at least 16px, zoom stays available, and reduced motion stops the
  character bob. Hardware iPhone keyboard/safe-area behavior still needs a device
  check; emulated viewports and WebKit aren't a physical iPhone.
- A rolling maximum of 40 turns, partial replies, traces, conversation ID, and
  draft are saved in `localStorage` on this origin. Storage is bounded by dropping
  older turns; saved answers cap at 100,000 characters and tool argument/result
  previews at 8,000. Long live answers can therefore exceed their saved copy.
  History is local to this device; it is not fetched from SQLite or synchronized
  across tabs/devices. Reload-interrupted replies are visibly marked incomplete.
  Clearing browser storage loses this local transcript. Server sessions retain
  their existing warmth/expiry policy.
- `GET /api/client` returns harness/model/auth requirements without the private
  account/path fields from `/api/health`. API responses use `Cache-Control: no-store`.
- NDJSON parsing handles split UTF-8, multiple lines, CRLF, and a final line without
  a newline. Unknown future kinds are ignored. Malformed known events, oversized
  lines, HTTP errors, and streams missing a terminal event fail visibly. A failed
  turn can retain partial text; it isn't silently treated as a completed answer.

## Server settings

The Agent section selects an installed harness, model, native pack profile
(or General agent), and Ollama context size. Local models come from the
server's [Ollama model catalog](https://docs.ollama.com/api/tags); selection never
pulls a model. Reload models refreshes the list. Wrapper model IDs are checked
for safe argument syntax; availability is determined by that wrapper when a
turn runs. Only valid installed native pack profiles appear in the selector.

Preferences are written atomically with mode `0600` to
`$BOPBOP_DATA_DIR/agent-settings.json` (normally `~/.bopbop/data/`). An optional
`BOPBOP_SETTINGS_PATH` overrides the path. The file remembers a model per harness
and overrides env defaults once saved. It contains no credentials. Remove it
to restore env defaults. `bopbop doctor` uses this effective configuration.
Changing credentials, executable paths, network endpoints or workspace
authority remains an installation task; the app edits only agent preferences.

Saved settings apply to **new browser and Signal turns**, without restarting.
Each turn captures a frozen configuration before routing/invocation. Changing
the harness, model, pack or context size invalidates the resumed agent session
for that conversation; displayed browser history remains. Older sessions from
before this upgrade start fresh once because they have no configuration key.

API contract:

- `GET /api/settings` returns effective preferences, remembered models,
  installed harnesses/profiles, local models and catalog availability. It uses
  the existing bearer gate when enabled and never returns secrets.
- `PUT /api/settings` accepts `harness`, `model`, `profile`, `ollama_context`,
  and the `revision` from GET. Send `X-BopBop-Settings: 1`, JSON content type,
  and the bearer token when required. Browser writes must originate from this
  app; another origin is rejected. There is no cross-origin CORS permission.
- A stale revision returns `409` and the sheet offers Reload settings. Invalid
  selections return `422`; unavailable Ollama or an unreadable/unsavable
  configuration returns `503`. Failed writes preserve the last valid file.
  Authentication failures ask for the tab's access token. Settings API requests
  are never cached offline.

## Agent trace

The expandable trace keeps raw arguments and bounded tool result previews out
of the normal answer. It shows failed tools, partial reports, truncation, cache
hits/ages, source age, response bytes, and tool/API duration when reported.
Claude/Codex tool IDs correlate call and result labels. Ollama pack tools already
supply their names and richer measurements.
Wrapper tool durations measure the interval between the runner receiving a call
and its result, and result previews are bounded to 8,000 characters before being
sent to the browser. They are not independent API-request timings.
Each turn's trace records its captured harness, model and pack, so later settings
changes cannot relabel an older reply.

| Measurement | Meaning |
|---|---|
| Browser round trip | From clicking Send to closing the response stream |
| First visible text | Time until non-emote answer text reaches this browser |
| Agent duration | Harness-reported turn duration |
| Input/output/cached tokens | Counts reported by the harness; accounting varies by provider |
| Model call timings | Ollama load, prompt evaluation, generation, first chunk/text |
| Thinking characters | Ollama's reported count; internal reasoning text isn't displayed |
| Reported cost | Shown only when supplied; missing cost is never displayed as $0 |

Wrapper output can be buffered. A local model's first text timestamp can precede
text delivery to the browser. These are different measurements, not contradictory
latencies. Missing measurements show a dash or are omitted; none are estimated.

## Offline and updates

The service worker precaches the app shell, hashed assets, icons, and expression
art. Navigation is network first with offline shell fallback. `/api/*` and all
non-GET requests always use the network; chat/health responses are never cached.
Offline shows saved history and permits drafting, disables sending, and never
queues messages for automatic execution later.

Each build has its own `bopbop-shell-` cache. Activation removes only older caches
with that prefix and leaves unrelated application caches alone. A new worker
waits for existing tabs/windows to close before activating, preserving those
tabs' old lazy-loaded assets. After deploying, close all BopBop tabs/installed
windows and reopen to pick up the worker. Shell, manifest, and worker responses
are not browser-cached; hashed assets are immutable.

## Tests and release gates

```bash
server/.venv/bin/python -m pip install -r server/requirements-dev.txt
npx playwright install chromium webkit   # CI also installs OS dependencies
bopbop web test                          # check, unit tests, build, browser tests
server/.venv/bin/python -m pytest -q server/tests
```

The browser suite serves the production build with the real FastAPI endpoints,
a temporary SQLite DB, bearer authentication, and a deterministic fake agent.
It never connects Signal, a real workspace, a paid model, or the production DB.
Tests cover hydration, Markdown/emotes, API round trip, reload and true session
resume, draft persistence, new chat, token rejection/recovery, partial results,
tool failure, interrupted streams, script/URL sanitization, small/keyboard-sized
viewports, reduced motion, manifest/icons, offline reload and API cache exclusion.
Settings gates cover persistence, model/profile selection reaching the next
turn, session invalidation, canceled edits, stale saves, and changes while a
reply runs. All 17 face overlays are exercised through streamed emote tags.
Projects cover desktop Chromium, mobile Chromium, and mobile WebKit.
Dimension checks include iPhone SE (375×667), 13 (390×844), 15 (393×852),
Pro Max (430×932), and a keyboard-sized 393×340 viewport, including form
scrolling and touch target sizes. These are emulations, not hardware testing.

Screenshot baselines cover empty/chat states, expanded trace, the settings sheet,
an expression contact sheet, and light/dark
palettes. They mask live timing values. Establish/update them with
`npm run test:visual:update`, inspect the PNGs, then run `npm run test:e2e` without
updates. Do not regenerate baselines to bypass a rendering failure. CI runs all
these frontend gates and retains failure traces/screenshots. A passing fixture
suite validates the client contract; live model/tool checks remain separate.
