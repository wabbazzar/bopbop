<script lang="ts">let { children } = $props();</script>
{@render children()}
<style>
  :global(html) {
    height: 100%; --ink: #181c2a; --muted: #4c5c7b; --accent: #426c9e;
    --accent-link: #285c97; --line: #687faf33; --panel: #ffffffc9;
    --solid: #f3f5fc; --code: #20395c0b; --error: #8e273d;
  }
  :global(body) { margin: 0; padding: 0; height: 100%; min-height: 100vh; overflow: hidden; color: var(--ink); font: 15px/1.45 -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; -webkit-text-size-adjust: 100%; background: linear-gradient(180deg, #a6d9f6 0%, #b1d5f5 25%, #bfccf6 55%, #e9f2f7 85%, #f6f3fc 100%); background-attachment: fixed; }
  :global(*), :global(*::before), :global(*::after) { box-sizing: border-box; }
  :global(:focus-visible) { outline: 2px solid var(--accent-link); outline-offset: 3px; }
  @media (prefers-color-scheme: dark) {
    :global(html) { --ink: #d6dcec; --muted: #a1aecb; --accent: #426c9e;
      --accent-link: #a6d9f6; --line: #b4c8f02e; --panel: #131a2cbf;
      --solid: #18233f; --code: #ffffff10; --error: #ffb7c5; }
    :global(body) { background: linear-gradient(180deg, #0c1632 0%, #18233f 60%, #1d2540 100%); }
  }
</style>
