<script lang="ts">
  type Catalog = {
    harness: string; model: string; models: Record<string, string>; profile: string;
    ollama_context: number; revision: string; local_models: string[];
    local_model_error: string | null; profiles: { id: string; name: string }[];
    harnesses: { id: string; available: boolean }[];
  };
  import { tick } from 'svelte';
  let { bearer = $bindable(''), requiresToken = false, keyboard = false, viewportHeight = 0, onchanged }: {
    bearer?: string; requiresToken?: boolean; keyboard?: boolean; viewportHeight?: number; onchanged: () => void;
  } = $props();
  let dialog: HTMLDialogElement;
  let catalog = $state<Catalog | null>(null);
  let harness = $state('');
  let model = $state('');
  let profile = $state('');
  let context = $state(16384);
  let tokenDraft = $state('');
  let loading = $state(false);
  let saving = $state(false);
  let error = $state('');
  let message = $state('');
  let requestId = 0;
  let remembered: Record<string, string> = {};

  function revealField() {
    const field = document.activeElement;
    if (!keyboard || !dialog?.open || !(field instanceof HTMLInputElement) || !dialog.contains(field)) return;
    const body = dialog.querySelector('.sheet-body')!;
    const fieldBox = field.getBoundingClientRect(), bodyBox = body.getBoundingClientRect();
    if (fieldBox.top < bodyBox.top + 8 || fieldBox.bottom > bodyBox.bottom - 8) field.scrollIntoView({ block: 'center', behavior: 'instant' });
  }
  $effect(() => { keyboard; viewportHeight; void tick().then(revealField); });

  function headers(): Record<string, string> { return bearer ? { Authorization: `Bearer ${bearer}` } : {}; }
  async function failure(res: Response): Promise<string> {
    if (res.status === 401) return 'Enter your access token below to load server settings.';
    const data = await res.json().catch(() => null);
    return typeof data?.detail === 'string' ? data.detail : `The server rejected this change (HTTP ${res.status}).`;
  }
  export function open() {
    tokenDraft = bearer; error = ''; message = '';
    if (!dialog.open) dialog.showModal();
    void load();
  }
  async function load() {
    const id = ++requestId;
    loading = true; error = ''; message = ''; catalog = null;
    try {
      const res = await fetch('/api/settings', { headers: headers(), cache: 'no-store', signal: AbortSignal.timeout(7000) });
      if (!res.ok) throw new Error(await failure(res));
      const data: Catalog = await res.json();
      if (id !== requestId) return;
      catalog = data; harness = data.harness; model = data.model;
      profile = data.profile; context = data.ollama_context; remembered = { ...data.models };
    } catch (e) { if (id === requestId) error = e instanceof Error ? e.message : 'Settings could not be loaded. Reconnect and try again.'; }
    finally { if (id === requestId) loading = false; }
  }
  function changeHarness(value: string) {
    remembered[harness] = model; harness = value; model = remembered[value] ?? '';
    message = '';
  }
  async function save() {
    if (!catalog) return;
    saving = true; error = ''; message = '';
    try {
      const res = await fetch('/api/settings', {
        method: 'PUT', headers: { ...headers(), 'Content-Type': 'application/json', 'X-BopBop-Settings': '1' },
        body: JSON.stringify({ harness, model: model.trim(), profile, ollama_context: context, revision: catalog.revision }),
        signal: AbortSignal.timeout(8000)
      });
      if (!res.ok) throw new Error(await failure(res));
      const updated = await res.json();
      catalog = { ...catalog, ...updated }; remembered = { ...updated.models };
      model = updated.model; message = 'Saved. New turns use these settings.';
      onchanged();
    } catch (e) { error = e instanceof Error ? e.message : 'Settings could not be saved. Try again.'; }
    finally { saving = false; }
  }
  function saveToken(e: SubmitEvent) {
    e.preventDefault(); bearer = tokenDraft.trim();
    try {
      if (bearer) sessionStorage.setItem('bopbop_access_token', bearer);
      else sessionStorage.removeItem('bopbop_access_token');
    } catch {}
    void load();
  }
</script>

<dialog bind:this={dialog} class:keyboard aria-labelledby="settings-title" onfocusin={() => { void tick().then(revealField); }} onclose={() => { requestId++; }}>
  <div class="sheet">
    <header><div><span class="eyebrow">BopBop</span><h2 id="settings-title">Settings</h2></div><button class="close" aria-label="Close settings" onclick={() => dialog.close()}>×</button></header>
    <div class="sheet-body">
      <section>
        <h3>Agent</h3><p class="description">Choose who handles the next message. Applies to this server’s browser and Signal conversations.</p>
        {#if loading}<p role="status" class="feedback">Loading server settings…</p>{/if}
        {#if error}<p role="alert" class="feedback error">{error} <button class="text-button" onclick={load} disabled={loading || saving}>Reload settings</button></p>{/if}
        {#if catalog}
          <form onsubmit={e => { e.preventDefault(); void save(); }}>
            <label for="harness">Harness</label>
            <select id="harness" value={harness} onchange={e => changeHarness(e.currentTarget.value)} disabled={saving || loading}>
              {#each catalog.harnesses as choice}<option value={choice.id} disabled={!choice.available}>{choice.id === 'ollama' ? 'Local · Ollama' : choice.id === 'codex' ? 'Codex' : 'Claude Code'}{!choice.available ? ' · not installed' : ''}</option>{/each}
            </select>
            <label for="agent-model">Model</label>
            {#if harness === 'ollama'}
              <select id="agent-model" bind:value={model} disabled={saving || loading}>
                {#if !catalog.local_models.includes(model)}<option value={model}>{model || 'Choose a model'} · unavailable</option>{/if}
                {#each catalog.local_models as name}<option value={name}>{name}</option>{/each}
              </select>
              <p class="hint">Models installed on the server. No downloads or cloud calls.</p>
              {#if catalog.local_model_error}<p class="feedback error">{catalog.local_model_error}</p>{/if}
            {:else}
              <input id="agent-model" bind:value={model} placeholder="Installation default" maxlength="128" autocomplete="off" spellcheck="false" disabled={saving || loading} />
              <p class="hint">Enter a model ID supported by your {harness === 'codex' ? 'Codex' : 'Claude Code'} installation. Leave blank to use its default.</p>
            {/if}
            <label for="agent-profile">Context pack</label>
            <select id="agent-profile" bind:value={profile} disabled={saving || loading}>
              <option value="">General agent</option>
              {#if profile && !catalog.profiles.some(p => p.id === profile)}<option value={profile}>{profile} · unavailable</option>{/if}
              {#each catalog.profiles as pack}<option value={pack.id}>{pack.name}</option>{/each}
            </select>
            {#if harness === 'ollama'}
              <label for="context-size">Context size</label>
              <select id="context-size" bind:value={context} disabled={saving || loading}>
                {#if ![4096,8192,16384,32768,65536,131072].includes(context)}<option value={context}>{context.toLocaleString()} tokens</option>{/if}
                {#each [4096,8192,16384,32768,65536,131072] as size}<option value={size}>{size.toLocaleString()} tokens</option>{/each}
              </select><p class="hint">Larger contexts use more memory. Stay within your model’s supported context.</p>
            {/if}
            <div class="save-row"><button class="primary" type="submit" disabled={saving || loading || (harness === 'ollama' && !catalog.local_models.includes(model))}>{saving ? 'Saving…' : 'Save agent settings'}</button><button class="text-button" type="button" onclick={load} disabled={saving || loading}>Reload models</button></div>
            {#if message}<p class="feedback success" role="status">{message}</p>{/if}
            <p class="hint">A model, harness, or pack change starts a fresh agent session. Running replies finish with their original settings; your visible history stays here.</p>
          </form>
        {/if}
      </section>
      <section class="access">
        <h3>Access token {requiresToken ? '(required)' : '(optional)'}</h3>
        <form onsubmit={saveToken}><label for="access-token">Access token</label><input id="access-token" type="password" bind:value={tokenDraft} autocomplete="off" spellcheck="false" /><p class="hint">Saved only for this tab’s session, separate from conversation history.</p><button class="secondary" type="submit">Save token</button></form>
      </section>
      <p class="privacy">The latest 40 turns and your draft stay on this device. Server credentials and network addresses are managed by your installation.</p>
    </div>
  </div>
</dialog>

<style>
  dialog { position: fixed; inset: calc(var(--kbd-viewport-t, 0px) + 12px) 0 auto; width: min(100%, 520px); max-width: none; max-height: calc(var(--kbd-viewport-h, 100dvh) - 12px); padding: 0; margin: 0 auto; border: 1px solid var(--line); border-radius: 24px 24px 0 0; color: var(--ink); background: var(--solid); box-shadow: 0 -10px 60px #24335630; }
  dialog::backdrop { background: #14203545; backdrop-filter: blur(5px); }
  dialog { overflow: hidden; }
  .sheet { display: flex; flex-direction: column; max-height: inherit; } header { padding: 1.25rem 1.4rem 1rem; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--line); flex: none; }
  .eyebrow { font: 11px ui-monospace, monospace; color: var(--muted); } h2 { margin: .15rem 0 0; font: 600 24px 'Avenir Next', system-ui; letter-spacing: -.035em; }
  .close { border: 1px solid var(--line); border-radius: 50%; width: 44px; height: 44px; background: var(--panel); font-size: 25px; color: var(--ink); cursor: pointer; }
  .sheet-body { padding: 1.2rem 1.4rem max(env(safe-area-inset-bottom), 1.4rem); overflow: auto; overscroll-behavior: contain; min-height: 0; }
  h3 { font-size: 15px; margin: 0 0 .4rem; } .description { font-size: 13px; color: var(--muted); margin: 0 0 .7rem; }
  label { display: block; font-size: 12px; font-weight: 600; margin: 1rem 0 .35rem; } input, select { width: 100%; height: 46px; padding: .6rem .75rem; border: 1px solid var(--line); border-radius: .7rem; color: var(--ink); background: var(--panel); font: 16px system-ui; }
  /* WebKit's native select clips text with explicit height + padding. Keep
     its native picker, but paint the field and chevron consistently. */
  select { appearance: none; padding-right: 2.25rem; background-image: url('data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16"%3E%3Cpath d="m4 6 4 4 4-4" fill="none" stroke="%236e7f9b" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/%3E%3C/svg%3E'); background-repeat: no-repeat; background-position: right .75rem center; }
  .hint { font-size: 12px; line-height: 1.5; color: var(--muted); margin: .45rem 0; }.save-row { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; margin-top: 1.1rem; }
  button { min-height: 44px; font: 13px system-ui; cursor: pointer; } button:disabled { opacity: .45; cursor: default; } .primary, .secondary { border: 0; border-radius: .7rem; padding: .7rem 1rem; background: var(--accent); color: white; }.secondary { background: var(--panel); color: var(--ink); border: 1px solid var(--line); }
  .text-button { border: 0; background: transparent; color: var(--accent-link); padding: .4rem; text-decoration: underline; text-underline-offset: 3px; }
  .feedback { padding: .65rem .8rem; font-size: 13px; border-radius: .65rem; background: var(--panel); }.error { color: var(--error); }.success { color: var(--ink); border: 1px solid var(--line); }
  .access { border-top: 1px solid var(--line); margin-top: 1.3rem; padding-top: 1.2rem; }.privacy { font-size: 11px; color: var(--muted); margin: 1.3rem 0 0; }
  dialog.keyboard header { padding: .4rem 1rem; } .keyboard .eyebrow, .keyboard .privacy { display: none; }.keyboard h2 { font-size: 20px; margin: 0; }.keyboard .sheet-body { padding: .75rem 1rem; }
  @media(min-width: 700px) { dialog { inset: calc(var(--kbd-viewport-t, 0px) + var(--kbd-viewport-h, 100dvh) * .05) 0 auto; border-radius: 24px; max-height: calc(var(--kbd-viewport-h, 100dvh) * .88); } }
</style>
