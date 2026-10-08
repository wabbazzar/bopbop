// Expressions come from the original character artwork. Strip streamed control
// tags without delaying ordinary HTML/Markdown or exposing split emote tags.
export const EMOTES = ['idling', 'happy', 'laughing', 'winking', 'angry', 'scared',
  'sleepy', 'pouting', 'thinking', 'lovesick', 'concentrating', 'searching',
  'calculating', 'puzzled', 'motivated', 'alert', 'inspired'] as const;
export type EmoteName = (typeof EMOTES)[number];
const EMOTE_SET: Set<string> = new Set(EMOTES);

export class EmoteStripper {
  private buf = '';
  constructor(private onEmote: (name: EmoteName) => void) {}
  feed(chunk: string): string {
    this.buf += chunk;
    let emitted = '';
    while (this.buf) {
      const index = this.buf.indexOf('<');
      if (index < 0) { emitted += this.buf; this.buf = ''; break; }
      emitted += this.buf.slice(0, index); this.buf = this.buf.slice(index);
      const tag = this.buf.match(/^<emote>(\w+)<\/emote>/i);
      if (tag) {
        const name = tag[1].toLowerCase();
        if (EMOTE_SET.has(name)) this.onEmote(name as EmoteName);
        this.buf = this.buf.slice(tag[0].length); continue;
      }
      const lower = this.buf.toLowerCase();
      let partial = '<emote>'.startsWith(lower);
      if (lower.startsWith('<emote>')) {
        const rest = lower.slice(7), end = rest.indexOf('<');
        partial = end < 0 ? /^\w*$/.test(rest)
          : /^\w+$/.test(rest.slice(0, end)) && '</emote>'.startsWith(rest.slice(end));
      }
      if (partial && this.buf.length <= 64) break;
      emitted += '<'; this.buf = this.buf.slice(1);
    }
    return emitted;
  }
  flush(): string { const rest = this.buf; this.buf = ''; return rest; }
}
