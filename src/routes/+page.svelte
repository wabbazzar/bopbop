<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { streamChat } from '$lib/stream';
  import { EmoteStripper, type EmoteName } from '$lib/emote';
  import { renderMarkdown } from '$lib/markdown';
  import { STORAGE_KEY, restoreHistory, encodeHistory, boundedTrace, type Turn } from '$lib/history';
  import Trace from '$lib/components/Trace.svelte';

  let draft = $state('');
  let conversationId = $state<string | null>(null);
  let turns = $state<Turn[]>([]);
  let busy = $state(false);
  let emote = $state<EmoteName>('happy');
  let restored = $state(false);
  let online = $state(true);
  let connected = $state(false);
  let checking = $state(true);
  let model = $state('');
  let harness = $state('');
  let requiresToken = $state(false);
  let bearer = $state('');
  let tokenDraft = $state('');
  let storageWarning = $state('');
  let elapsedMs = $state(0);
  let activity = $state('Thinking');
  let textarea: HTMLTextAreaElement;
  let transcript: HTMLDivElement;
  let settings: HTMLDetailsElement;
  let stickBottom = true;
  let settleTimer: ReturnType<typeof setTimeout> | undefined;
  let turnAbort: AbortController | undefined;

  async function checkConnection() {
    online = navigator.onLine;
    if (!online) { connected = false; checking = false; return; }
    checking = true;
    try {
      const res = await fetch('/api/client', { cache: 'no-store', signal: AbortSignal.timeout(5000) });
      if (!res.ok) throw new Error('unavailable');
      const health = await res.json();
      connected = health.ok === true;
      harness = typeof health.agent_harness === 'string' ? health.agent_harness : '';
      model = typeof health.model === 'string' && health.model !== 'default' ? health.model : '';
      requiresToken = health.require_bearer === true;
    } catch { connected = false; }
    finally { checking = false; }
  }

  onMount(() => {
    try {
      const history = restoreHistory(localStorage.getItem(STORAGE_KEY));
      turns = history.turns; conversationId = history.conversationId; draft = history.draft;
    } catch { storageWarning = 'History can’t be saved in this browser. Keep this tab open to keep your conversation.'; }
    try { bearer = sessionStorage.getItem('bopbop_access_token') ?? ''; tokenDraft = bearer; } catch {}
    restored = true;
    void checkConnection();
    const updateViewport = () => {
      const vv = window.visualViewport;
      if (!vv) return;
      document.documentElement.style.setProperty('--kbd-viewport-h', `${vv.height}px`);
      document.documentElement.style.setProperty('--kbd-viewport-t', `${vv.offsetTop}px`);
    };
    updateViewport();
    window.visualViewport?.addEventListener('resize', updateViewport);
    window.visualViewport?.addEventListener('scroll', updateViewport);
    window.addEventListener('resize', updateViewport);
    window.addEventListener('online', checkConnection);
    window.addEventListener('offline', checkConnection);
    return () => {
      window.visualViewport?.removeEventListener('resize', updateViewport);
      window.visualViewport?.removeEventListener('scroll', updateViewport);
      window.removeEventListener('resize', updateViewport);
      window.removeEventListener('online', checkConnection);
      window.removeEventListener('offline', checkConnection);
      clearTimeout(settleTimer); turnAbort?.abort();
    };
  });

  $effect(() => {
    if (!restored) return;
    try { localStorage.setItem(STORAGE_KEY, encodeHistory({ conversationId, turns, draft })); }
    catch { storageWarning = 'History couldn’t be saved. Free browser storage before closing this tab.'; }
  });
  $effect(() => {
    // Read the changing text/events so streamed replies trigger scrolling.
    turns.map(t => `${t.answer.length}:${t.events.length}`); busy;
    void tick().then(() => { if (transcript && stickBottom) transcript.scrollTop = transcript.scrollHeight; });
  });

  function saveToken(e: SubmitEvent) {
    e.preventDefault(); bearer = tokenDraft.trim();
    try {
      if (bearer) sessionStorage.setItem('bopbop_access_token', bearer);
      else sessionStorage.removeItem('bopbop_access_token');
    } catch {}
    settings.open = false;
  }
  function newChat() {
    if (busy) return;
    turns = []; conversationId = null; draft = ''; emote = 'happy'; stickBottom = true;
    textarea?.focus();
  }
  function onKey(e: KeyboardEvent) {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); void send(); }
  }

  async function send() {
    const message = draft.trim();
    if (!message || busy || !online) return;
    if (requiresToken && !bearer) { settings.open = true; return; }
    busy = true; draft = ''; emote = 'thinking'; activity = 'Thinking'; elapsedMs = 0;
    clearTimeout(settleTimer); stickBottom = true;
    const id = crypto.randomUUID();
    const started = performance.now();
    turns = [...turns.slice(-39), { id, user: message, answer: '', events: [], status: 'running', elapsedMs: 0, startedAt: Date.now() }];
    const timer = setInterval(() => { elapsedMs = performance.now() - started; }, 200);
    const update = (fn: (turn: Turn) => Turn) => { turns = turns.map(t => t.id === id ? fn(t) : t); };
    const append = (text: string) => {
      if (!text) return;
      update(t => ({ ...t, answer: t.answer + text, firstTextMs: t.firstTextMs ?? performance.now() - started }));
      activity = 'Replying';
    };
    const stripper = new EmoteStripper(name => { emote = name; });
    turnAbort = new AbortController();
    try {
      for await (const event of streamChat(message, conversationId, bearer || null, turnAbort.signal)) {
        if (event.kind === 'meta') conversationId = event.conversation_id;
        else if (event.kind === 'text') append(stripper.feed(event.delta));
        else {
          update(t => ({ ...t, events: [...t.events.slice(-119), boundedTrace(event)],
            status: event.kind === 'error' ? 'error' : t.status }));
          if (event.kind === 'tool') { activity = 'Using tools'; emote = 'searching'; }
          else if (event.kind === 'model') activity = 'Thinking';
        }
      }
    } catch (error) {
      update(t => ({ ...t, status: 'error', events: [...t.events,
        { kind: 'error', message: error instanceof Error ? error.message : 'The reply failed. Try again.' }] }));
    } finally {
      append(stripper.flush());
      update(t => ({ ...t, status: t.status === 'error' ? 'error' : 'done', elapsedMs: performance.now() - started }));
      clearInterval(timer); busy = false; turnAbort = undefined;
      settleTimer = setTimeout(() => { if (!busy) emote = 'happy'; }, 2000);
      // Don't reopen a mobile keyboard after the user has dismissed it.
    }
  }
</script>

<svelte:head><title>BopBop</title><meta name="description" content="Chat with your personal agent and see its tools at work." /></svelte:head>

<div class="viewport">
  <main>
    <header>
      <div class="identity"><span class="brand">BopBop</span><span class="connection" class:disconnected={!connected}><i></i>{!online ? 'Offline' : connected ? (model || harness || 'Connected') : checking ? 'Connecting' : 'Server unavailable'}</span></div>
      <div class="header-actions">
        <button class="quiet" onclick={newChat} disabled={busy || !turns.length} title="Start a fresh conversation; clears this device’s transcript">New chat</button>
        <details class="settings" bind:this={settings}>
          <summary aria-label="Settings">Settings</summary>
          <div class="settings-panel">
            <h2>Connection</h2>
            <p>{harness ? `Using ${harness}${model ? ` · ${model}` : ''}.` : 'Checking the server.'} Select the harness in your server configuration.</p>
            <form onsubmit={saveToken}>
              <label for="access-token">Access token {requiresToken ? '(required)' : '(optional)'}</label>
              <input id="access-token" type="password" bind:value={tokenDraft} autocomplete="off" spellcheck="false" />
              <p class="small">Stored for this tab’s session. Never included in saved history.</p>
              <button class="primary" type="submit">Save token</button>
            </form>
            <p class="small">The latest 40 turns and your draft are saved on this device. New chat clears that transcript and starts a fresh agent session.</p>
          </div>
        </details>
      </div>
    </header>
    <section class="stage" aria-label="BopBop character">
      <img src={`/emotes/${emote}.png`} alt={`BopBop ${emote}`} class="stage-blob" fetchpriority="high" />
      <div class="stage-caption" aria-live="polite">{busy ? activity : 'Here when you need me.'}{#if busy}<span>{(elapsedMs / 1000).toFixed(1)}s</span>{/if}</div>
    </section>
    <section class="chat" aria-label="Conversation">
      {#if !online}<div class="notice" role="status">You’re offline. Saved messages are available; reconnect to send.</div>{/if}
      {#if online && !connected && !checking}<div class="notice" role="status">Can’t reach the server. <button class="quiet" onclick={checkConnection}>Reconnect</button></div>{/if}
      {#if storageWarning}<div class="notice" role="status">{storageWarning}</div>{/if}
      <div class="transcript" bind:this={transcript} onscroll={() => { stickBottom = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 48; }}>
        {#if !turns.length}
          <div class="empty"><h1>What’s on your mind?</h1><p>Ask a question, check a project, or work through an idea.</p></div>
        {/if}
        {#each turns as turn (turn.id)}
          <article class="turn">
            <div class="bubble user">{turn.user}</div>
            {#if turn.answer}<div class="bubble assistant md" data-testid="answer">{@html renderMarkdown(turn.answer)}</div>{/if}
            {#each turn.events.filter(e => e.kind === 'error') as error}
              {#if error.kind === 'error'}<div class="error" role="alert">{error.message}</div>{/if}
            {/each}
            {#if turn.status === 'running'}<div class="working" role="status">{activity} <span class="dots">···</span></div>{/if}
            {#if turn.events.length || turn.status !== 'running'}<Trace {turn} elapsedMs={turn.status === 'running' ? elapsedMs : turn.elapsedMs} />{/if}
          </article>
        {/each}
      </div>
      <form class="input-row" onsubmit={e => { e.preventDefault(); void send(); }}>
        <textarea aria-label="Message" bind:this={textarea} bind:value={draft} onkeydown={onKey} placeholder="Message BopBop…" rows="2" maxlength="20000"></textarea>
        <button class="primary send" type="submit" disabled={busy || !online || !draft.trim()} aria-label="Send message">Send <span aria-hidden="true">↗</span></button>
      </form>
      <div class="composer-note">{busy ? 'You can draft your next message while BopBop works.' : 'Ctrl / ⌘ + Enter to send'}<span>Saved on this device</span></div>
    </section>
  </main>
</div>

<style>
  .viewport { position: fixed; top: var(--kbd-viewport-t, 0); left: 0; right: 0; height: var(--kbd-viewport-h, 100dvh); display: flex; justify-content: center; overflow: hidden; }
  main { width: 100%; max-width: 760px; height: 100%; display: grid; grid-template-rows: auto minmax(110px, 30%) minmax(0, 1fr); padding: max(env(safe-area-inset-top), 1rem) max(env(safe-area-inset-right), 1rem) max(env(safe-area-inset-bottom), .75rem) max(env(safe-area-inset-left), 1rem); }
  header { display: flex; align-items: center; justify-content: space-between; gap: .75rem; position: relative; z-index: 3; }
  .identity { display: grid; gap: .15rem; min-width: 0; }
  .brand { font: 700 24px/1.1 'Avenir Next', 'Trebuchet MS', system-ui, sans-serif; letter-spacing: -.05em; }
  .connection { display: flex; align-items: center; gap: .35rem; font: 11px/1.5 ui-monospace, monospace; color: var(--muted); overflow-wrap: anywhere; }
  .connection i { width: 5px; height: 5px; background: #3c796e; border-radius: 50%; flex: none; }.connection.disconnected i { background: var(--muted); }
  .header-actions { display: flex; align-items: center; gap: .5rem; flex: none; }
  button, summary { -webkit-tap-highlight-color: transparent; }
  button { font: inherit; cursor: pointer; } button:disabled { opacity: .45; cursor: default; }
  .quiet, .settings > summary { font-size: 12px; color: var(--ink); border: 1px solid var(--line); background: transparent; border-radius: 2rem; padding: .5rem .7rem; min-height: 40px; }
  .settings > summary { cursor: pointer; list-style: none; }.settings > summary::-webkit-details-marker { display: none; }
  .settings-panel { position: absolute; top: calc(100% + .7rem); right: 0; width: min(340px, calc(100vw - 2rem)); padding: 1.2rem; background: var(--solid); border: 1px solid var(--line); border-radius: 1rem; box-shadow: 0 10px 40px #25396625; }
  .settings-panel h2 { font-size: 16px; margin: 0 0 .5rem; }.settings-panel p { font-size: 13px; margin: .5rem 0 1rem; }.settings-panel .small { font-size: 11px; color: var(--muted); }
  label { font-size: 12px; display: block; margin-bottom: .35rem; } input { width: 100%; padding: .5rem; border: 1px solid var(--line); background: var(--panel); color: var(--ink); font: 16px system-ui; border-radius: .4rem; }
  .stage { min-height: 0; position: relative; display: flex; align-items: center; justify-content: center; padding: .5rem 0 1.7rem; }
  .stage-blob { height: 100%; max-width: 80%; object-fit: contain; animation: float 4.2s ease-in-out infinite; filter: drop-shadow(0 8px 20px #5064a022); }
  .stage-caption { position: absolute; bottom: .25rem; font-size: 12px; color: var(--muted); display: flex; gap: .8rem; }.stage-caption span { font: 11px/1.7 ui-monospace, monospace; }
  @keyframes float { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-5px); } }
  .chat { min-height: 0; display: flex; flex-direction: column; }.transcript { flex: 1; min-height: 0; overflow-y: auto; overscroll-behavior: contain; scrollbar-width: thin; padding: .8rem .15rem .4rem; }
  .empty { padding: 1.5rem .4rem; text-align: center; }.empty h1 { font: 600 20px 'Avenir Next', 'Trebuchet MS', system-ui; margin: 0 0 .4rem; letter-spacing: -.025em; }.empty p { font-size: 13px; color: var(--muted); margin: 0; }
  .turn { display: flex; flex-direction: column; gap: .7rem; margin-bottom: 1.25rem; }
  .bubble { padding: .75rem 1rem; border-radius: 1.05rem; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.55; min-width: 0; }
  .user { align-self: flex-end; max-width: 85%; background: var(--accent); color: #fff; border-bottom-right-radius: .25rem; }
  .assistant { align-self: flex-start; max-width: 100%; background: var(--panel); color: var(--ink); border: 1px solid var(--line); border-bottom-left-radius: .25rem; }
  .md { white-space: normal; }.md :global(p) { margin: 0 0 .6em; }.md :global(p:last-child) { margin-bottom: 0; }
  .md :global(ul), .md :global(ol) { padding-left: 1.4em; margin: .3em 0 .6em; }.md :global(li) { margin: .2em 0; }
  .md :global(pre) { overflow-x: auto; max-width: 100%; padding: .8rem; border-radius: .5rem; background: var(--code); white-space: pre; }
  .md :global(code) { font: .85em/1.5 ui-monospace, monospace; background: var(--code); padding: .1em .25em; border-radius: .2rem; }.md :global(pre code) { background: none; padding: 0; }
  .md :global(a) { color: var(--accent-link); text-decoration-thickness: 1px; text-underline-offset: 2px; overflow-wrap: anywhere; }
  .md :global(blockquote) { border-left: 3px solid var(--accent); margin: .5rem 0; padding-left: .8rem; }
  .md :global(h1), .md :global(h2), .md :global(h3) { font-size: 1.05em; margin: .75em 0 .3em; }
  .md :global(table) { display: block; overflow-x: auto; border-collapse: collapse; font-size: 13px; max-width: 100%; }.md :global(th), .md :global(td) { border: 1px solid var(--line); padding: .3rem .5rem; }
  .working { color: var(--muted); font-size: 12px; padding-left: .2rem; }.dots { letter-spacing: .2rem; }
  .error, .notice { font-size: 13px; padding: .65rem .8rem; border-radius: .6rem; background: var(--panel); border: 1px solid var(--line); }.error { color: var(--error); }.notice { margin-top: .5rem; }
  .input-row { display: flex; align-items: stretch; gap: .6rem; margin-top: .3rem; }
  textarea { min-width: 0; flex: 1; resize: none; padding: .7rem .85rem; border: 1px solid var(--line); border-radius: .9rem; font: 16px/1.4 system-ui, sans-serif; color: var(--ink); background: var(--panel); }
  .primary { padding: .65rem 1rem; border: 0; border-radius: .7rem; background: var(--accent); color: white; }.send { font-size: 13px; }.send span { margin-left: .25rem; font-size: 16px; }
  .composer-note { display: flex; justify-content: space-between; gap: .6rem; margin-top: .4rem; color: var(--muted); font-size: 10px; }
  @media (max-width: 450px) { main { padding-left: .75rem; padding-right: .75rem; }.brand { font-size: 22px; }.composer-note span { display: none; } }
  @media (max-height: 550px) { main { grid-template-rows: auto minmax(65px, 20%) minmax(0, 1fr); padding-top: .5rem; }.stage-caption { display: none; }.stage { padding-bottom: .3rem; }.composer-note { display: none; } }
  @media (prefers-reduced-motion: reduce) { .stage-blob { animation: none; } }
</style>
