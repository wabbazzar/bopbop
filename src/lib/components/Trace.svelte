<script lang="ts">
  import type { Turn } from '$lib/history';
  let { turn, elapsedMs }: { turn: Turn; elapsedMs: number } = $props();
  const done = $derived(turn.events.findLast(e => e.kind === 'done'));
  const usage = $derived(done?.kind === 'done' ? done.usage : undefined);
  const tools = $derived(turn.events.filter(e => e.kind === 'tool').length);
  const models = $derived(turn.events.filter(e => e.kind === 'model').length);
  const cachedTokens = $derived(usage?.cached_tokens ?? usage?.cached_input_tokens ?? usage?.cache_read_input_tokens);
  const cacheMeasured = $derived(!turn.events.some(e => e.kind === 'model' && e.cache_measured === false));
  function ms(n: unknown): string {
    return typeof n === 'number' && Number.isFinite(n) ? `${(n / 1000).toFixed(2)}s` : '—';
  }
  function num(n: unknown): string {
    return typeof n === 'number' && Number.isFinite(n) ? n.toLocaleString('en-US') : '—';
  }
  function resultName(index: number, name: string, id?: string): string {
    const call = turn.events.slice(0, index).findLast(e => e.kind === 'tool' && e.id && (e.id === id || e.id === name));
    return call?.kind === 'tool' ? call.name : name;
  }
</script>

<details class="trace" data-testid="trace">
  <summary>
    <span class="trace-label">Agent trace</span>
    <span>{ms(elapsedMs)}{#if tools} · {tools} tool{tools === 1 ? '' : 's'}{/if}</span>
    {#if usage?.input_tokens !== undefined || usage?.output_tokens !== undefined}
      <span class="tokens">{num(usage?.input_tokens)} in / {num(usage?.output_tokens)} out</span>
    {/if}
  </summary>
  <div class="trace-body">
    <dl class="totals">
      {#if turn.harness}<div><dt>Harness</dt><dd>{turn.harness}</dd></div>{/if}
      {#if turn.model}<div><dt>Model</dt><dd>{turn.model === 'default' ? 'Installation default' : turn.model}</dd></div>{/if}
      {#if turn.profile}<div><dt>Context pack</dt><dd>{turn.profile}</dd></div>{/if}
      <div><dt>First visible text</dt><dd>{ms(turn.firstTextMs)}</dd></div>
      <div><dt>Browser round trip</dt><dd>{ms(elapsedMs)}</dd></div>
      {#if done?.kind === 'done' && done.duration_ms !== undefined}
        <div><dt>Agent duration</dt><dd>{ms(done.duration_ms)}</dd></div>
      {/if}
      {#if models || usage?.model_calls !== undefined}<div><dt>Model calls</dt><dd>{num(usage?.model_calls ?? models)}</dd></div>{/if}
      {#if cacheMeasured && cachedTokens !== undefined}
        <div><dt>Cached input tokens</dt><dd>{num(cachedTokens)}</dd></div>
      {/if}
      {#if done?.kind === 'done' && typeof done.cost_usd === 'number'}
        <div><dt>Reported cost</dt><dd>${done.cost_usd.toFixed(4)}</dd></div>
      {/if}
    </dl>
    {#each turn.events as event, index}
      {#if event.kind === 'tool'}
        <div class="trace-event"><b>→ {event.name}</b><pre>{JSON.stringify(event.args, null, 2)}</pre></div>
      {:else if event.kind === 'tool_result'}
        <div class="trace-event" class:failed={!event.ok}>
          <b>← {resultName(index, event.name, event.id)}</b>
          <span>{event.ok ? 'ok' : 'failed'}{event.state ? ` · ${event.state}` : ''}{event.duration_ms !== undefined ? ` · ${ms(event.duration_ms)}` : ''}</span>
          {#if event.preview}<pre>{event.preview}</pre>{/if}
          <div class="result-meta">
            {#if event.response_bytes !== undefined}{num(event.response_bytes)} bytes · {/if}
            {#if event.cache_hit !== undefined}{event.cache_hit ? `cached (${num(event.cache_age_s)}s old)` : 'fresh fetch'}{/if}
            {#if event.api_ms !== undefined} · API {ms(event.api_ms)}{/if}
            {#if event.source_age_s != null} · source {num(event.source_age_s)}s old{/if}
            {#if event.truncated} · preview truncated{/if}
          </div>
        </div>
      {:else if event.kind === 'model'}
        <div class="trace-event">
          <b>Model call {event.step ?? index + 1}</b>
          <span>{ms(event.duration_ms)} · {num(event.prompt_tokens)} in / {num(event.output_tokens)} out</span>
          <dl class="model-metrics">
            {#if event.first_chunk_ms !== undefined}<div><dt>First stream chunk</dt><dd>{ms(event.first_chunk_ms)}</dd></div>{/if}
            {#if event.first_answer_ms !== undefined}<div><dt>First model text</dt><dd>{ms(event.first_answer_ms)}</dd></div>{/if}
            {#if event.load_ms !== undefined}<div><dt>Model load</dt><dd>{ms(event.load_ms)}</dd></div>{/if}
            {#if event.prompt_ms !== undefined}<div><dt>Prompt evaluation</dt><dd>{ms(event.prompt_ms)}</dd></div>{/if}
            {#if event.generation_ms !== undefined}<div><dt>Generation</dt><dd>{ms(event.generation_ms)}</dd></div>{/if}
            {#if event.thinking_chars !== undefined}<div><dt>Thinking characters</dt><dd>{num(event.thinking_chars)}</dd></div>{/if}
            {#if event.stream_chunks !== undefined}<div><dt>Stream chunks</dt><dd>{num(event.stream_chunks)}</dd></div>{/if}
          </dl>
        </div>
      {/if}
    {/each}
    <p class="note">Measurements depend on the harness. First model text can arrive before the harness sends visible text to the browser. Missing values mean they weren’t reported.</p>
  </div>
</details>

<style>
  .trace { width: 100%; color: var(--muted); font: 11px/1.55 ui-monospace, SFMono-Regular, Consolas, monospace; }
  summary { cursor: pointer; padding: .55rem .15rem; display: flex; flex-wrap: wrap; gap: .25rem .7rem; align-items: baseline; }
  summary::before { content: '▸'; } details[open] summary::before { content: '▾'; }
  .trace-label { color: var(--ink); } .tokens { margin-left: auto; }
  .trace-body { padding: .75rem; border: 1px solid var(--line); border-radius: .8rem; background: var(--panel); }
  dl { margin: 0; } dl div { display: flex; flex-wrap: wrap; justify-content: space-between; gap: .25rem 1rem; }
  dd { margin: 0; color: var(--ink); } .totals { padding-bottom: .6rem; }
  .trace-event { border-top: 1px solid var(--line); padding: .7rem 0; display: grid; gap: .2rem; }
  b { font-weight: 600; color: var(--ink); overflow-wrap: anywhere; }
  pre { margin: .25rem 0; white-space: pre-wrap; overflow-wrap: anywhere; max-height: 15rem; overflow: auto; color: var(--ink); }
  .failed, .failed b { color: var(--error); } .model-metrics { padding-top: .25rem; }
  .note { margin: .5rem 0 0; font: 12px/1.5 system-ui, sans-serif; }
</style>
