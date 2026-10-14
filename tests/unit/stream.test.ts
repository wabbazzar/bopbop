import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readEvents, parseEvent, streamChat } from '../../src/lib/stream';
import { EmoteStripper } from '../../src/lib/emote';
import { restoreHistory, encodeHistory, boundedTrace } from '../../src/lib/history';

function body(chunks: Uint8Array[]) {
  return new ReadableStream<Uint8Array>({ start(c) { for (const chunk of chunks) c.enqueue(chunk); c.close(); } });
}
async function collect(b: ReadableStream<Uint8Array>) { const out = []; for await (const e of readEvents(b)) out.push(e); return out; }
const encoder = new TextEncoder();

test('NDJSON preserves every split of UTF-8, CRLF and the final unterminated line', async () => {
  const events = [{ kind: 'meta', conversation_id: 'test' }, { kind: 'text', delta: 'Hi 🌷 café' }, { kind: 'done' }];
  const bytes = encoder.encode(events.map(e => JSON.stringify(e)).join('\r\n\r\n'));
  for (let split = 0; split <= bytes.length; split++) {
    assert.deepEqual(await collect(body([bytes.slice(0, split), bytes.slice(split)])), events);
  }
  assert.deepEqual(await collect(body([...bytes].map(b => Uint8Array.of(b)))), events);
});
test('forward-compatible kinds are ignored; malformed known events fail visibly', async () => {
  assert.equal(parseEvent('{"kind":"future"}'), null);
  assert.equal(parseEvent('{"kind":"session","session_id":"private"}'), null);
  assert.equal(parseEvent('{"kind":"toString"}'), null);
  for (const line of ['oops', 'null', '{}', '{"kind":"text","delta":null}', '{"kind":"tool_result","name":"x","ok":1}']) {
    await assert.rejects(collect(body([encoder.encode(line)])));
  }
});
test('oversized unfinished events fail instead of growing without limit', async () => {
  await assert.rejects(collect(body([encoder.encode('x'.repeat(2 * 1024 * 1024 + 1))])), /size limit/);
});
test('reader releases its stream when iteration stops early', async () => {
  const b = body([encoder.encode('{"kind":"done"}\n')]);
  for await (const _ of readEvents(b)) break;
  assert.equal(b.locked, false);
});
test('HTTP authentication errors and truncated turns are actionable', async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response('unauthorized', { status: 401 });
    await assert.rejects(async () => { for await (const _ of streamChat('hi', null, null)) {} }, /Settings/);
    globalThis.fetch = async () => new Response('{"kind":"text","delta":"partial"}\n');
    await assert.rejects(async () => { for await (const _ of streamChat('hi', null, null)) {} }, /before the reply finished/);
  } finally { globalThis.fetch = original; }
});
test('heartbeats keep quiet work visible and network errors explain uncertain execution', async () => {
  const original = globalThis.fetch;
  try {
    assert.deepEqual(parseEvent('{"kind":"progress","elapsed_ms":5000}'), { kind: 'progress', elapsed_ms: 5000 });
    globalThis.fetch = async () => new Response('{"kind":"progress","elapsed_ms":5000}\n{"kind":"done"}\n');
    const events = []; for await (const event of streamChat('hello', null, null)) events.push(event);
    assert.deepEqual(events.map(e => e.kind), ['progress', 'done']);
    globalThis.fetch = async () => { throw new TypeError('Load failed'); };
    await assert.rejects(async () => { for await (const _ of streamChat('hello', null, null)) {} }, /check its status before retrying/);
    globalThis.fetch = async () => new Response(new ReadableStream({
      start(controller) { controller.error(new TypeError('Load failed')); }
    }));
    await assert.rejects(async () => { for await (const _ of streamChat('hello', null, null)) {} }, /Connection to BopBop/);
  } finally { globalThis.fetch = original; }
});
test('emote tags are removed at every boundary without losing visible text', () => {
  const source = 'Hello <emote>winking</emote> world <emote>happy</emote>!';
  for (let i = 0; i <= source.length; i++) {
    const emotes: string[] = [];
    const strip = new EmoteStripper(e => emotes.push(e));
    const visible = strip.feed(source.slice(0, i)) + strip.feed(source.slice(i)) + strip.flush();
    assert.equal(visible, 'Hello  world !'); assert.deepEqual(emotes, ['winking', 'happy']);
  }
});
test('invalid history is safe; interrupted turns are marked and the session survives', () => {
  for (const raw of [null, '{', '{}', '{"version":1,"turns":[null,{}]}']) assert.equal(restoreHistory(raw).turns.length, 0);
  const saved = encodeHistory({ conversationId: 'same-session', draft: 'next', turns: [{ id: 't', user: 'u', answer: 'partial', events: [], status: 'running', elapsedMs: 1, startedAt: 2 }] });
  const restored = restoreHistory(saved);
  assert.equal(restored.conversationId, 'same-session'); assert.equal(restored.draft, 'next');
  assert.equal(restored.turns[0].status, 'error'); assert.equal(restored.turns[0].answer, 'partial');
  assert.equal(restored.turns[0].events[0].kind, 'error');
});
test('persistence bounds large transcripts and tool previews', () => {
  const saved = encodeHistory({ conversationId: null, draft: '', turns: Array.from({ length: 100 }, (_, i) => ({ id: `${i}`, user: 'u', answer: 'a'.repeat(100000), events: [], status: 'done', elapsedMs: 1, startedAt: i })) });
  assert.ok(saved.length < 600000);
  assert.ok(restoreHistory(saved).turns.length <= 40);
  const e = boundedTrace({ kind: 'tool_result', name: 'big', ok: true, preview: 'a'.repeat(50000) });
  assert.ok(e.kind === 'tool_result' && e.preview!.length <= 8000);
});
