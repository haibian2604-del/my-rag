import { renderMarkdown } from "../api/markdown";
import { CitationCard, type Citation } from "./CitationCard";

export interface ChatMessage {
  id: number | string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  error?: string;
}

export default function MessageBubble({ message }: { message: ChatMessage }) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[70%] whitespace-pre-wrap rounded-xl rounded-br-sm bg-paper-deep px-4 py-2.5 text-[15px] leading-6">
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-[72ch]">
      {/* assistant 回答不用气泡，按阅读行长排成正文 */}
      <div
        className="md"
        // 内容经 marked + DOMPurify 消毒后再注入
        dangerouslySetInnerHTML={{ __html: renderMarkdown(message.content) }}
      />
      {message.error && (
        <p className="mt-1 text-sm text-seal">{message.error}</p>
      )}
      {message.citations && message.citations.length > 0 && (
        <div className="mt-3 border-t border-line pt-3">
          <p className="mb-2 text-xs text-faint">来源</p>
          <div className="space-y-2">
            {message.citations.map((c) => (
              <CitationCard key={c.n} citation={c} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
