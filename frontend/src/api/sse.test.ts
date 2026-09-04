import { describe, expect, it } from "vitest";
import { parseSSE, type SSEEvent } from "./sse";

function streamFrom(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(encoder.encode(c));
      controller.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>): Promise<SSEEvent[]> {
  const events: SSEEvent[] = [];
  for await (const ev of parseSSE(stream)) events.push(ev);
  return events;
}

describe("parseSSE", () => {
  it("解析完整事件序列", async () => {
    const chunks = [
      'data: {"type":"citations","items":[{"n":1,"filename":"a.md","heading_path":"x/y","page_no":null,"snippet":"..."}]}\n\n',
      'data: {"type":"delta","text":"你好"}\n\n',
      'data: {"type":"delta","text":"世界"}\n\n',
      'data: {"type":"done"}\n\n',
    ];
    const events = await collect(streamFrom(chunks));
    expect(events).toEqual([
      {
        type: "citations",
        items: [
          { n: 1, filename: "a.md", heading_path: "x/y", page_no: null, snippet: "..." },
        ],
      },
      { type: "delta", text: "你好" },
      { type: "delta", text: "世界" },
      { type: "done" },
    ]);
  });

  it("处理跨 chunk 分割的事件", async () => {
    const events = await collect(streamFrom([
      'data: {"type":"del',
      'ta","text":"abc"}\n\ndata: {"type":"do',
      'ne"}\n\n',
    ]));
    expect(events).toEqual([
      { type: "delta", text: "abc" },
      { type: "done" },
    ]);
  });

  it("缓冲不完整尾部（不完整事件被丢弃）", async () => {
    const events = await collect(streamFrom([
      'data: {"type":"done"}\n\ndata: {"type":"delta","te',
    ]));
    expect(events).toEqual([{ type: "done" }]);
  });

  it("解析 error 事件", async () => {
    const events = await collect(streamFrom([
      'data: {"type":"error","message":"未配置 LLM"}\n\n',
    ]));
    expect(events).toEqual([{ type: "error", message: "未配置 LLM" }]);
  });
});
