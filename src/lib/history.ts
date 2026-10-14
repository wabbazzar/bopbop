import type { TraceEvent } from './stream';
export const STORAGE_KEY = 'bopbop_history_v1';
export type Turn = {
  id: string; user: string; answer: string; events: TraceEvent[];
  status: 'running' | 'done' | 'error'; elapsedMs: number;
  firstTextMs?: number; startedAt: number;
  harness?: string; model?: string; profile?: string;
};
export type History = { conversationId: string | null; turns: Turn[]; draft: string };

export function restoreHistory(raw: string | null): History {
  const empty: History = { conversationId: null, turns: [], draft: '' };
  if (!raw || raw.length > 1_000_000) return empty;
  try {
    const data = JSON.parse(raw);
    if (data.version !== 1 || !Array.isArray(data.turns)) return empty;
    const turns: Turn[] = data.turns.slice(-40).filter((t: Turn) =>
      t && typeof t.id === 'string' && typeof t.user === 'string' &&
      typeof t.answer === 'string' && Array.isArray(t.events) &&
      ['running', 'done', 'error'].includes(t.status) &&
      Number.isFinite(t.elapsedMs) && Number.isFinite(t.startedAt)
    ).map((t: Turn) => {
      const events = t.events.filter(e => e && ['progress', 'tool', 'tool_result', 'model', 'done', 'error'].includes(e.kind)).slice(-120);
      if (t.status === 'running') events.push({ kind: 'error', message: 'This reply was interrupted by a reload. Send a message to continue.' });
      return { ...t, events, status: t.status === 'running' ? 'error' : t.status };
    });
    return {
      conversationId: typeof data.conversationId === 'string' ? data.conversationId : null,
      turns, draft: typeof data.draft === 'string' ? data.draft.slice(0, 20000) : ''
    };
  } catch { return empty; }
}

export function encodeHistory(history: History): string {
  const turns = history.turns.slice(-40).map(t => ({ ...t, user: t.user.slice(0, 20000), answer: t.answer.slice(0, 100000), events: t.events.slice(-120).map(boundedTrace) }));
  // Persist a bounded rolling transcript. Access credentials never belong here.
  let encoded = JSON.stringify({ version: 1, ...history, turns });
  while (encoded.length > 600_000 && turns.length > 1) {
    turns.shift(); encoded = JSON.stringify({ version: 1, ...history, turns });
  }
  return encoded;
}

export function boundedTrace(event: TraceEvent): TraceEvent {
  if (event.kind === 'tool') {
    const raw = JSON.stringify(event.args) ?? '';
    return { ...event, args: raw.length > 8000 ? raw.slice(0, 8000) + '… (truncated)' : event.args };
  }
  if (event.kind === 'tool_result') return { ...event, preview: event.preview?.slice(0, 8000) };
  return event;
}
