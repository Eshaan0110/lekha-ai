// Server-sent events parser for POST /api/v1/ask/stream (EventSource only supports GET).
// Pure: feed it text as it arrives; it returns complete events and the unfinished rest.

export interface SseEvent {
  event: string;
  data: unknown;
}

export function parseSse(buffer: string): { events: SseEvent[]; rest: string } {
  const events: SseEvent[] = [];
  const normalised = buffer.replace(/\r\n/g, "\n");
  const blocks = normalised.split("\n\n");
  const rest = blocks.pop() ?? "";
  for (const block of blocks) {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event: ")) event = line.slice(7);
      else if (line.startsWith("data: ")) data.push(line.slice(6));
    }
    if (data.length === 0) continue;
    try {
      events.push({ event, data: JSON.parse(data.join("\n")) });
    } catch {
      // A malformed event is dropped rather than shown.
    }
  }
  return { events, rest };
}
