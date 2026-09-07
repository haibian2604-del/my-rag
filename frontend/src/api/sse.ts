export interface Citation {
  n: number
  filename: string
  heading_path: string | null
  page_no: number | null
  snippet: string
}

export type SSEEvent =
  | { type: "citations"; items: Citation[] }
  | { type: "delta"; text: string }
  | { type: "stage"; stage: "retrieving" | "reranking" | "generating" }
  | { type: "done"; followups?: string[] }
  | { type: "error"; message: string }
  // agent 模式工具时间线事件（tool_call 带参数 args；tool_result 带结果预览 preview）
  | {
      type: "agent";
      event: "tool_call" | "tool_result";
      tool: string;
      args?: unknown;
      preview?: string;
    };

export async function* parseSSE(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<SSEEvent> {
  const reader = body
    .pipeThrough(new TextDecoderStream() as unknown as ReadableWritablePair<string, Uint8Array>)
    .getReader();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) return;
    buf += value;
    const parts = buf.split("\n\n");
    buf = parts.pop() ?? "";
    for (const p of parts) {
      const line = p.split("\n").find((l) => l.startsWith("data: "));
      if (line) yield JSON.parse(line.slice(6)) as SSEEvent;
    }
  }
}
