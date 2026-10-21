import { describe, expect, it } from "vitest";
import { explainAbstention, formatMs, splitAnswer } from "./answer";
import { parseSse } from "./sse";

describe("parseSse", () => {
  it("returns complete events and keeps the unfinished rest", () => {
    const { events, rest } = parseSse(
      'event: stage\ndata: {"stage":"searching"}\n\nevent: answer\ndata: {"answer":"20 [1]"}\n\nevent: sta',
    );
    expect(events).toEqual([
      { event: "stage", data: { stage: "searching" } },
      { event: "answer", data: { answer: "20 [1]" } },
    ]);
    expect(rest).toBe("event: sta");
  });

  it("joins chunks split mid-event and handles CRLF", () => {
    const first = parseSse('event: stage\r\ndata: {"stage":');
    expect(first.events).toEqual([]);
    const second = parseSse(first.rest + '"writing"}\r\n\r\n');
    expect(second.events).toEqual([{ event: "stage", data: { stage: "writing" } }]);
  });

  it("drops malformed events instead of showing them", () => {
    expect(parseSse("event: answer\ndata: {not json\n\n").events).toEqual([]);
  });
});

describe("splitAnswer", () => {
  it("turns verified markers into citations and leaves the rest as text", () => {
    expect(splitAnswer("You get 24 days [1] and 8 carry over [2].", new Set([1, 2]))).toEqual([
      { kind: "text", text: "You get 24 days " },
      { kind: "cite", number: 1 },
      { kind: "text", text: " and 8 carry over " },
      { kind: "cite", number: 2 },
      { kind: "text", text: "." },
    ]);
  });

  it("never turns an unverified marker into a citation", () => {
    expect(splitAnswer("See [3].", new Set([1]))).toEqual([{ kind: "text", text: "See [3]." }]);
  });
});

describe("explainAbstention", () => {
  it("explains known reasons and falls back safely", () => {
    expect(explainAbstention("no_sources")).toMatch(/access/);
    expect(explainAbstention("something_new")).toMatch(/enough evidence/);
    expect(explainAbstention(null)).toMatch(/enough evidence/);
  });
});

describe("formatMs", () => {
  it("shows meeting times like a player", () => {
    expect(formatMs(5_120)).toBe("00:05");
    expect(formatMs(760_000)).toBe("12:40");
    expect(formatMs(3_725_000)).toBe("1:02:05");
  });
});
