<script lang="ts">
  import type { EmoteName } from '$lib/emote';
  let { emote = 'happy' }: { emote?: EmoteName } = $props();
  const source = $derived(emote);
  // Original expression cards have different face positions. Anchor each eye
  // and mouth individually onto Pearl instead of scaling the entire card.
  const anchors: Partial<Record<EmoteName, number[][]>> = {
    angry: [[121,196], [198,196], [160,218]],
    scared: [[122,191], [198,191], [160,223]],
    sleepy: [[118,232], [199,232], [160,253]],
    pouting: [[116,232], [191,232], [153,253]],
    thinking: [[129,226], [208,222], [170,249]],
    lovesick: [[120,221], [199,221], [160,249]],
    concentrating: [[121,176], [198,176], [162,198]],
    searching: [[119,198], [201,198], [161,223]],
    calculating: [[124,197], [197,197], [160,219]],
    puzzled: [[122,196], [198,196], [160,229]],
    motivated: [[118,233], [199,233], [160,258]],
    alert: [[113,231], [194,231], [154,263]],
    inspired: [[126,230], [207,230], [166,260]],
  };
  function facePatches(name: EmoteName) {
    const points = anchors[name];
    if (!points) return [];
    return [
      { region: 'left', cx: 433, cy: 501, rx: 78, ry: 98 },
      { region: 'right', cx: 653, cy: 501, rx: 78, ry: 98 },
      { region: 'mouth', cx: 548, cy: 576, rx: 95, ry: 75 },
    ].map((patch, i) => ({ ...patch,
      transform: `translate(${patch.cx - points[i][0] * 2.9} ${patch.cy - points[i][1] * 2.9}) scale(2.9)`
    }));
  }
  const patches = $derived(emote === 'happy' || emote === 'idling' ? [] : emote === 'winking' ? [
    { region: 'right', cx: 653, cy: 501, rx: 54, ry: 46, transform: 'translate(72 28) scale(2.9)' }
  ] : emote === 'laughing' ? [
    { region: 'left', cx: 433, cy: 501, rx: 54, ry: 46, transform: 'translate(79 35) scale(2.9)' },
    { region: 'right', cx: 653, cy: 501, rx: 54, ry: 46, transform: 'translate(79 35) scale(2.9)' },
    { region: 'mouth', cx: 548, cy: 576, rx: 95, ry: 75, transform: 'translate(81.1 30.8) scale(2.9)' }
  ] : facePatches(emote));
</script>

<!-- Same body clip and wink alignment as assets/cli/pearl.svg and the CLI
     generator. SVG stays inline so shared raster sources can be loaded once. -->
<svg viewBox="150 230 790 730" class="stage-blob" role="img" aria-label={`BopBop ${emote}`}>
  <defs>
    <clipPath id="pearl-body"><path d="M 540 266 C 413 266 341 345 277 449 C 204 564 183 646 190 746 C 198 819 214 859 299 881 C 467 902 678 900 820 865 C 882 846 905 792 904 719 C 904 607 862 486 791 384 C 724 294 621 266 540 266 Z" /></clipPath>
    <radialGradient id="pearl-feather"><stop offset=".8" stop-color="white"/><stop offset="1" stop-color="black"/></radialGradient>
    {#each patches as patch}
      <mask id={`pearl-${patch.region}`} maskUnits="userSpaceOnUse" x="150" y="230" width="790" height="730">
        <ellipse cx={patch.cx} cy={patch.cy} rx={patch.rx} ry={patch.ry} fill="url(#pearl-feather)" />
      </mask>
    {/each}
  </defs>
  <g clip-path="url(#pearl-body)">
    <image href="/character/body.jpg" width="1088" height="1132" />
    {#each patches as patch}
      <g mask={`url(#pearl-${patch.region})`}><image href={`/character/expressions/${source}.png`} width="320" height="320" transform={patch.transform} /></g>
    {/each}
  </g>
</svg>

<style>
  svg { height: 100%; width: auto; max-width: 100%; overflow: visible; filter: drop-shadow(0 8px 14px #5064a01f); animation: float 4.2s ease-in-out infinite; }
  @keyframes float { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-3px); } }
  @media (prefers-reduced-motion: reduce) { svg { animation: none; } }
</style>
