export type Usage = {
  input_tokens?: number; output_tokens?: number; cached_tokens?: number;
  cached_input_tokens?: number; model_calls?: number; tool_calls?: number;
  cache_read_input_tokens?: number;
};
export type ChatEvent =
  | { kind: 'meta'; conversation_id: string; agent_harness?: string; model?: string; profile?: string }
  | { kind: 'text'; delta: string }
  | { kind: 'tool'; name: string; args: unknown; id?: string }
  | { kind: 'tool_result'; name: string; ok: boolean; id?: string; preview?: string;
      duration_ms?: number; state?: string; report?: string; response_bytes?: number;
      source_age_s?: number | null; truncated?: boolean; cache_hit?: boolean;
      cache_age_s?: number; api_ms?: number }
  | { kind: 'model'; step?: number; prompt_tokens?: number; output_tokens?: number;
      cached_tokens?: number; cache_measured?: boolean; thinking_chars?: number;
      duration_ms?: number; first_chunk_ms?: number; first_answer_ms?: number;
      load_ms?: number; prompt_ms?: number; generation_ms?: number; stream_chunks?: number }
  | { kind: 'done'; cost_usd?: number | null; duration_ms?: number; usage?: Usage }
  | { kind: 'error'; message: string };
export type TraceEvent = Exclude<ChatEvent, { kind: 'meta' | 'text' }>;
const MAX_LINE = 2 * 1024 * 1024;

export function parseEvent(line: string): ChatEvent | null {
  const ev = JSON.parse(line);
  if (!ev || typeof ev !== 'object' || typeof ev.kind !== 'string') {
    throw new Error('Invalid event from the agent.');
  }
  const required: Record<string, Record<string, string>> = {
    meta: { conversation_id: 'string' }, text: { delta: 'string' },
    tool: { name: 'string' }, tool_result: { name: 'string', ok: 'boolean' },
    model: {}, done: {}, error: { message: 'string' }
  };
  // Forward-compatible: ignore unknown kinds, fail visibly on malformed known events.
  if (!Object.hasOwn(required, ev.kind)) return null;
  for (const [key, type] of Object.entries(required[ev.kind])) {
    if (typeof ev[key] !== type) throw new Error('Invalid event from the agent.');
  }
  return ev as ChatEvent;
}

export async function* readEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<ChatEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      let newline: number;
      while ((newline = buffer.indexOf('\n')) !== -1) {
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        if (line.length > MAX_LINE) throw new Error('Agent event exceeds the size limit.');
        if (line) { const ev = parseEvent(line); if (ev) yield ev; }
      }
      if (buffer.length > MAX_LINE) throw new Error('Agent event exceeds the size limit.');
      if (done) {
        if (buffer.trim()) { const ev = parseEvent(buffer); if (ev) yield ev; }
        break;
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export async function* streamChat(
  message: string, conversationId: string | null, bearer: string | null,
  signal?: AbortSignal
): AsyncGenerator<ChatEvent> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (bearer) headers.Authorization = `Bearer ${bearer}`;
  const res = await fetch('/api/chat', {
    method: 'POST', headers,
    body: JSON.stringify({ message, conversation_id: conversationId }), signal
  });
  if (!res.ok) {
    throw new Error(res.status === 401
      ? 'Access token required or incorrect. Open Settings to enter it.'
      : `The server could not start this reply (HTTP ${res.status}). Try again.`);
  }
  if (!res.body) throw new Error('The server returned an empty response.');
  let terminal = false;
  for await (const ev of readEvents(res.body)) {
    if (ev.kind === 'done' || ev.kind === 'error') terminal = true;
    yield ev;
  }
  if (!terminal) throw new Error('Connection ended before the reply finished. Send a message to continue.');
}
