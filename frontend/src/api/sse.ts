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
  | { type: "done" }
  | { type: "error"; message: string };

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
