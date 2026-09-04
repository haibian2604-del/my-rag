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
  const isUser = message.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div className="max-w-3xl space-y-2">
        <div
          className={
            isUser
              ? "rounded-lg bg-blue-600 px-4 py-2 text-white whitespace-pre-wrap"
              : "prose prose-sm max-w-none rounded-lg bg-gray-100 px-4 py-2 text-gray-900 [&_p]:my-1 [&_pre]:my-2 [&_code]:text-xs"
          }
          // 用户消息纯文本展示；assistant 内容经 marked + DOMPurify 消毒
          {...(isUser
            ? {}
            : { dangerouslySetInnerHTML: { __html: renderMarkdown(message.content) } })}
        >
          {isUser ? message.content : null}
        </div>
        {message.error && (
          <div className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
            {message.error}
          </div>
        )}
        {message.citations && message.citations.length > 0 && (
          <div className="space-y-2">
            {message.citations.map((c) => (
              <CitationCard key={c.n} citation={c} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
