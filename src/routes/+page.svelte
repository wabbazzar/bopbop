<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { streamChat } from '$lib/stream';
  import { EmoteStripper, type EmoteName } from '$lib/emote';
  import { renderMarkdown } from '$lib/markdown';
  import { STORAGE_KEY, restoreHistory, encodeHistory, boundedTrace, type Turn } from '$lib/history';
  import Trace from '$lib/components/Trace.svelte';
  import Character from '$lib/components/Character.svelte';
  import Settings from '$lib/components/Settings.svelte';
  import { watchViewport } from '$lib/viewport';

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
  let storageWarning = $state('');
  let elapsedMs = $state(0);
  let activity = $state('Thinking');
  let textarea: HTMLTextAreaElement;
  let transcript: HTMLDivElement;
  let settings: Settings;
  let keyboard = $state(false);
  let viewportHeight = $state(0);
  let stickBottom = $state(true);
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
    try { bearer = sessionStorage.getItem('bopbop_access_token') ?? ''; } catch {}
    restored = true;
    void checkConnection();
    const stopViewport = watchViewport(frame => {
      const pinned = stickBottom;
      const position = transcript?.scrollTop ?? 0;
      keyboard = frame.keyboard; viewportHeight = frame.height;
      document.documentElement.style.setProperty('--kbd-viewport-h', `${frame.height}px`);
      document.documentElement.style.setProperty('--kbd-viewport-t', `${frame.top}px`);
      void tick().then(() => {
        if (transcript) {
          transcript.scrollTop = pinned ? transcript.scrollHeight : position;
          stickBottom = pinned;
        }
      });
    });
    window.addEventListener('online', checkConnection);
    window.addEventListener('offline', checkConnection);
    return () => {
      stopViewport();
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

  function fillDraft(message: string) { draft = message; textarea?.focus(); }
  function latest() { stickBottom = true; transcript.scrollTo({ top: transcript.scrollHeight, behavior: 'instant' }); }
  function fitComposer() {
    if (!textarea) return;
    textarea.style.height = 'auto';
    const limit = keyboard ? Math.min(88, Math.max(44, viewportHeight * .22)) : 128;
    textarea.style.height = `${Math.min(limit, Math.max(44, textarea.scrollHeight))}px`;
  }
  $effect(() => { draft; keyboard; viewportHeight; void tick().then(fitComposer); });
  function hideKeyboard() { if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); }
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
    if (requiresToken && !bearer) { settings.open(); return; }
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
        if (event.kind === 'meta') {
          conversationId = event.conversation_id;
          update(t => ({ ...t, harness: event.agent_harness, model: event.model, profile: event.profile }));
        }
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
  <main class:conversation={turns.length > 0} class:keyboard>
    <header>
      <div class="identity"><span class="brand">BopBop<span class="brand-dot">.</span></span><span class="connection" class:disconnected={!connected} title={model || harness}><i></i>{!online ? 'Offline' : connected ? (model || harness || 'Connected') : checking ? 'Connecting' : 'Server unavailable'}</span></div>
      <div class="header-actions">
        {#if keyboard}<button class="quiet keyboard-dismiss" aria-label="Hide keyboard" title="Hide keyboard" onpointerdown={e => e.preventDefault()} onclick={hideKeyboard}><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="18" height="11" rx="2"/><path d="M7 7h.01M11 7h.01M15 7h.01M7 10h10M8 18l4 3 4-3"/></svg></button>{/if}
        <button class="quiet" onclick={newChat} disabled={busy || !turns.length} title="Start a fresh conversation; clears this device’s transcript"><span aria-hidden="true">＋</span> New chat</button>
        <button class="quiet settings-button" aria-label="Settings" onclick={() => settings.open()}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 17h16M9 4v6M15 14v6" /></svg></button>
      </div>
    </header>
    <section class="stage" aria-label="BopBop character">
      <div class="portrait"><Character {emote} /></div>
      <div class="stage-caption">{busy ? activity : turns.length ? 'Ready for the next thought.' : 'Here when you need me.'}{#if busy}<span aria-live="off">{(elapsedMs / 1000).toFixed(1)}s</span>{/if}</div>
    </section>
    <section class="chat" aria-label="Conversation">
      {#if !online}<div class="notice" role="status">You’re offline. Saved messages are available; reconnect to send.</div>{/if}
      {#if online && !connected && !checking}<div class="notice" role="status">Can’t reach the server. <button class="quiet" onclick={checkConnection}>Reconnect</button></div>{/if}
      {#if storageWarning}<div class="notice" role="status">{storageWarning}</div>{/if}
      <div class="transcript" bind:this={transcript} onscroll={() => { stickBottom = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 48; }}>
        {#if !turns.length}
          <div class="empty"><h1>What’s on your mind?</h1><p>A question, a project, a half-formed idea.<br/>Let’s take it from here.</p><div class="starters"><button onclick={() => fillDraft('What can you help me with?')}>What can you do? <span aria-hidden="true">↗</span></button><button onclick={() => fillDraft('Check the health of my projects.')}>Check project health <span aria-hidden="true">↗</span></button></div></div>
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
      {#if !stickBottom}<button class="jump" onclick={latest}>Latest reply ↓</button>{/if}
      <form class="input-row" onsubmit={e => { e.preventDefault(); void send(); }}>
        <textarea aria-label="Message" bind:this={textarea} bind:value={draft} onkeydown={onKey} placeholder="Message BopBop…" rows="1" oninput={fitComposer} maxlength="20000"></textarea>
        <button class="primary send" type="submit" disabled={busy || !online || !draft.trim()} aria-label="Send message" onpointerdown={e => { if (keyboard) e.preventDefault(); }}><span aria-hidden="true">↑</span></button>
      </form>
      <div class="composer-note">{busy ? 'You can draft your next message while BopBop works.' : 'Replies may use tools'}<span>{storageWarning ? 'History not saved' : 'Saved on this device'}</span></div>
    </section>
  </main>
</div>
<Settings bind:this={settings} bind:bearer {requiresToken} {keyboard} {viewportHeight} onchanged={() => { void checkConnection(); }} />

<style>
  .viewport { position: fixed; top: var(--kbd-viewport-t, 0); left: 0; right: 0; height: var(--kbd-viewport-h, 100dvh); display: flex; justify-content: center; overflow: hidden; }
  main { width: 100%; max-width: 760px; height: 100%; display: grid; grid-template-rows: auto auto minmax(0, 1fr); padding: max(env(safe-area-inset-top), 1rem) max(env(safe-area-inset-right), 1rem) max(env(safe-area-inset-bottom), .8rem) max(env(safe-area-inset-left), 1rem); }
  header { display: flex; align-items: center; justify-content: space-between; gap: .75rem; padding: .1rem .1rem .6rem; }
  .identity { display: grid; gap: .2rem; min-width: 0; }.brand { font: 700 26px/1.1 'Avenir Next', 'Trebuchet MS', system-ui, sans-serif; letter-spacing: -.065em; }.brand-dot { color: var(--accent); }
  .connection { display: flex; align-items: center; gap: .4rem; font: 11px/1.5 ui-monospace, monospace; color: var(--muted); max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.connection i { width: 5px; height: 5px; background: #3c796e; border-radius: 50%; flex: none; }.disconnected i { background: var(--muted); }
  .header-actions { display: flex; align-items: center; gap: .45rem; flex: none; }
  button { font: inherit; cursor: pointer; -webkit-tap-highlight-color: transparent; } button:disabled { opacity: .45; cursor: default; }
  .quiet { min-height: 44px; font-size: 12px; padding: .5rem .8rem; border: 1px solid var(--line); background: var(--panel); color: var(--ink); border-radius: 2rem; }.quiet span { margin-right: .15rem; }.settings-button { width: 44px; padding: 10px; }.settings-button svg { width: 20px; height: 20px; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; }
  .stage { height: clamp(155px, 24vh, 210px); min-height: 0; display: flex; flex-direction: column; justify-content: center; align-items: center; gap: .7rem; padding: .7rem 0 .8rem; }
  .portrait { min-height: 0; height: 100%; max-width: 210px; }.stage-caption { color: var(--muted); font-size: 12px; flex: none; display: flex; align-items: center; gap: .7rem; }.stage-caption span { font: 11px ui-monospace, monospace; }
  main.conversation .stage { height: 88px; flex-direction: row; justify-content: flex-start; padding: .3rem .35rem .65rem; gap: .8rem; }.conversation .portrait { height: 66px; }.conversation .stage-caption { flex-direction: column; align-items: flex-start; gap: .2rem; }
  .chat { position: relative; min-height: 0; display: flex; flex-direction: column; }.transcript { flex: 1; min-height: 0; overflow-y: auto; overscroll-behavior: contain; scrollbar-width: thin; padding: .6rem .15rem .5rem; }
  .empty { padding: 1.1rem .5rem; text-align: center; }.empty h1 { font: 600 25px/1.2 'Avenir Next', 'Trebuchet MS', system-ui; margin: 0 0 .65rem; letter-spacing: -.035em; }.empty p { font-size: 14px; line-height: 1.6; color: var(--muted); margin: 0; }.starters { display: flex; flex-wrap: wrap; justify-content: center; gap: .55rem; margin: 1.3rem auto 0; }.starters button { min-height: 44px; border: 1px solid var(--line); background: var(--panel); color: var(--ink); padding: .65rem .85rem; border-radius: 1rem; font-size: 12px; }.starters span { margin-left: .3rem; color: var(--muted); }
  .turn { display: flex; flex-direction: column; gap: .7rem; margin-bottom: 1.25rem; }.bubble { padding: .8rem 1rem; border-radius: 1.1rem; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.55; min-width: 0; }.user { align-self: flex-end; max-width: 85%; background: var(--accent); color: #fff; border-bottom-right-radius: .3rem; }.assistant { align-self: flex-start; max-width: 100%; background: var(--panel); color: var(--ink); border: 1px solid var(--line); border-bottom-left-radius: .3rem; }
  .md { white-space: normal; }.md :global(p) { margin: 0 0 .6em; }.md :global(p:last-child) { margin-bottom: 0; }.md :global(ul), .md :global(ol) { padding-left: 1.4em; margin: .3em 0 .6em; }.md :global(li) { margin: .2em 0; }.md :global(pre) { overflow-x: auto; max-width: 100%; padding: .8rem; border-radius: .5rem; background: var(--code); white-space: pre; }.md :global(code) { font: .85em/1.5 ui-monospace, monospace; background: var(--code); padding: .1em .25em; border-radius: .2rem; }.md :global(pre code) { background: none; padding: 0; }.md :global(a) { color: var(--accent-link); text-decoration-thickness: 1px; text-underline-offset: 2px; overflow-wrap: anywhere; }.md :global(blockquote) { border-left: 3px solid var(--accent); margin: .5rem 0; padding-left: .8rem; }.md :global(h1), .md :global(h2), .md :global(h3) { font-size: 1.05em; margin: .75em 0 .3em; }.md :global(table) { display: block; overflow-x: auto; border-collapse: collapse; font-size: 13px; max-width: 100%; }.md :global(th), .md :global(td) { border: 1px solid var(--line); padding: .3rem .5rem; }
  .working { color: var(--muted); font-size: 12px; padding-left: .2rem; }.dots { letter-spacing: .2rem; }.error, .notice { font-size: 13px; padding: .65rem .8rem; border-radius: .6rem; background: var(--panel); border: 1px solid var(--line); }.error { color: var(--error); }.notice { margin-top: .5rem; }
  .input-row { display: flex; align-items: flex-end; gap: .5rem; background: var(--solid); padding: .35rem; margin-top: .3rem; border: 1px solid var(--line); border-radius: 1.2rem; box-shadow: 0 4px 20px #233b6010; flex: none; }.input-row:focus-within { border-color: var(--accent); }
  textarea { min-width: 0; flex: 1; resize: none; padding: .65rem .55rem; min-height: 44px; max-height: 128px; border: 0; border-radius: .7rem; font: 16px/1.4 system-ui, sans-serif; color: var(--ink); background: transparent; } textarea:focus { outline: none; }.primary { width: 44px; height: 44px; flex: none; padding: 0; border: 0; border-radius: 50%; background: var(--accent); color: white; }.send span { font-size: 23px; line-height: 1; }.composer-note { display: flex; justify-content: space-between; gap: .6rem; margin: .45rem .35rem 0; color: var(--muted); font-size: 10px; }
  .jump { position: absolute; bottom: 95px; left: 50%; transform: translateX(-50%); padding: .5rem 1rem; border: 1px solid var(--line); border-radius: 2rem; background: var(--solid); color: var(--ink); font-size: 12px; min-height: 44px; box-shadow: 0 4px 18px #1c2b5320; }
  @media(max-width: 450px) { main { padding-left: .8rem; padding-right: .8rem; }.brand { font-size: 25px; }.connection { max-width: 180px; } }
  @media(max-height: 550px) { main { padding-top: .35rem; }.stage, main.conversation .stage { height: 54px; flex-direction: row; padding: .1rem .35rem; justify-content: flex-start; }.portrait, main.conversation .portrait { height: 45px; }.empty { padding: .6rem .3rem; }.empty h1 { font-size: 20px; }.starters { margin-top: .6rem; }.composer-note { display: none; } }
  main.keyboard { padding-top: max(.35rem, env(safe-area-inset-top)); padding-bottom: .35rem; grid-template-rows: auto 0 minmax(0, 1fr); }
  main.keyboard header { padding-bottom: .3rem; }.keyboard .brand { font-size: 20px; }.keyboard .connection { font-size: 10px; max-width: 150px; }
  main.keyboard .stage, .keyboard .composer-note, .keyboard .empty p, .keyboard .starters { display: none; }
  .keyboard .chat { grid-row: 3; }
  .keyboard .empty { padding: .5rem .3rem; }.keyboard .empty h1 { font-size: 19px; margin: 0; }.keyboard .transcript { padding-top: .3rem; }
  .keyboard-dismiss { width: 44px; padding: 10px; }.keyboard-dismiss svg { width: 22px; height: 22px; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }
  .keyboard textarea { max-height: 88px; }.keyboard .input-row { margin-top: .2rem; }
</style>
