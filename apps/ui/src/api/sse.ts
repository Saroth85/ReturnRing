// Server-sent events over fetch (ADR-15 section 4, agent): EventSource cannot POST, so the agent
// stream is read from a fetch Response body. Wire format, one event per message:
//   event: <type>\nid: <seq>\ndata: <event JSON>\n\n
import type { AgentStream } from './types.gen';

export type AgentEvent = AgentStream[number];

export interface SseMessage {
  event: string;
  id: string | null;
  data: string;
}

/** The frame the API writes for one agent event (`sse_frame` of apps/api/schemas.py). */
export function encodeSseFrame(event: AgentEvent): string {
  return `event: ${event.type}\nid: ${event.seq}\ndata: ${JSON.stringify(event)}\n\n`;
}

/** Parses complete SSE messages out of a text buffer; returns them and the unparsed rest. */
export function parseSseBuffer(buffer: string): { messages: SseMessage[]; rest: string } {
  const normalised = buffer.replace(/\r\n?/g, '\n');
  const blocks = normalised.split('\n\n');
  const rest = blocks.pop() ?? '';
  const messages: SseMessage[] = [];
  for (const block of blocks) {
    let event = 'message';
    let id: string | null = null;
    const data: string[] = [];
    for (const line of block.split('\n')) {
      if (line === '' || line.startsWith(':')) continue;
      const colon = line.indexOf(':');
      const field = colon === -1 ? line : line.slice(0, colon);
      let value = colon === -1 ? '' : line.slice(colon + 1);
      if (value.startsWith(' ')) value = value.slice(1);
      if (field === 'event') event = value;
      else if (field === 'id') id = value;
      else if (field === 'data') data.push(value);
    }
    if (data.length > 0) messages.push({ event, id, data: data.join('\n') });
  }
  return { messages, rest };
}

/** Reads SSE messages from a response body as they arrive. */
export async function* readSse(body: ReadableStream<Uint8Array>, signal?: AbortSignal): AsyncGenerator<SseMessage> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  const onAbort = () => {
    void reader.cancel().catch(() => undefined);
  };
  signal?.addEventListener('abort', onAbort, { once: true });
  try {
    for (;;) {
      if (signal?.aborted) return;
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const { messages, rest } = parseSseBuffer(buffer);
      buffer = rest;
      for (const message of messages) yield message;
    }
    buffer += decoder.decode();
    const { messages } = parseSseBuffer(`${buffer}\n\n`);
    for (const message of messages) yield message;
  } finally {
    signal?.removeEventListener('abort', onAbort);
    reader.releaseLock();
  }
}

/** Reads the agent events of a stream; the event name must equal the JSON type. */
export async function* readAgentEvents(body: ReadableStream<Uint8Array>, signal?: AbortSignal): AsyncGenerator<AgentEvent> {
  for await (const message of readSse(body, signal)) {
    const event = JSON.parse(message.data) as AgentEvent;
    if (event.type !== message.event) {
      throw new Error(`agent stream: event ${message.event} carries a ${event.type} payload`);
    }
    yield event;
  }
}
