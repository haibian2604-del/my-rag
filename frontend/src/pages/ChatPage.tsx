import { useEffect, useRef, useState } from "react";
import { ApiError, del, get, post, type Workspace } from "../api/client";
import { parseSSE, type Citation, type SSEEvent } from "../api/sse";
import MessageBubble, { type ChatMessage } from "../components/MessageBubble";

interface Conversation {
  id: number;
  workspace_id: number;
  title: string;
}

export default function ChatPage({ workspace }: { workspace: Workspace }) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  const convListKey = `conversations.ws.${workspace.id}`;

  const loadConversations = (): Conversation[] => {
    try {
      return JSON.parse(localStorage.getItem(convListKey) ?? "[]") as Conversation[];
    } catch {
      return [];
    }
  };

  const saveConversations = (list: Conversation[]) => {
    localStorage.setItem(convListKey, JSON.stringify(list));
    setConversations(list);
  };

  // 后端无会话列表端点，会话侧栏用 localStorage 维护
  useEffect(() => {
    setConversations(loadConversations());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace.id]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const openConversation = async (id: number) => {
    setActiveId(id);
    setError("");
    try {
      const rows = await get<
        { id: number; role: string; content: string; citations: Citation[] | null }[]
      >(`/api/conversations/${id}/messages`);
      setMessages(
        rows.map((m) => ({
          id: m.id,
          role: m.role === "user" ? "user" : "assistant",
          content: m.content,
          citations: m.citations ?? undefined,
        })),
      );
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "加载会话失败");
    }
  };

  const newConversation = async () => {
    try {
      const conv = await post<Conversation>(`/api/workspaces/${workspace.id}/conversations`);
      saveConversations([conv, ...conversations]);
      setActiveId(conv.id);
      setMessages([]);
      setError("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "创建会话失败");
    }
  };

  const removeConversation = async (id: number) => {
    try {
      await del(`/api/conversations/${id}`);
      saveConversations(conversations.filter((c) => c.id !== id));
      if (activeId === id) {
        setActiveId(null);
        setMessages([]);
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "删除会话失败");
    }
  };

  const ask = async () => {
    if (!input.trim() || !activeId || asking) return;
    const question = input.trim();
    setInput("");
    setError("");
    setAsking(true);
    const conv = conversations.find((c) => c.id === activeId);
    if (conv && !conv.title) {
      saveConversations(
        conversations.map((c) => (c.id === activeId ? { ...c, title: question.slice(0, 20) } : c)),
      );
    }
    setMessages((prev) => [
      ...prev,
      { id: `u-${Date.now()}`, role: "user", content: question },
      { id: `a-${Date.now()}`, role: "assistant", content: "" },
    ]);
    try {
      const resp = await fetch(`/api/conversations/${activeId}/ask`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      if (!resp.ok || !resp.body) {
        let detail = `HTTP ${resp.status}`;
        try {
          const data = await resp.json();
          if (data?.detail) detail = data.detail;
        } catch {
          /* ignore */
        }
        throw new Error(detail);
      }
      let citations: Citation[] | undefined;
      let errorMsg: string | undefined;
      for await (const ev of parseSSE(resp.body) as AsyncGenerator<SSEEvent>) {
        if (ev.type === "citations") {
          citations = ev.items;
        } else if (ev.type === "delta") {
          setMessages((prev) => {
            const copy = [...prev];
            copy[copy.length - 1] = { ...copy[copy.length - 1], content: copy[copy.length - 1].content + ev.text };
            return copy;
          });
        } else if (ev.type === "error") {
          errorMsg = ev.message;
        } else if (ev.type === "done") {
          break;
        }
      }
      if (errorMsg) throw new Error(errorMsg);
      setMessages((prev) => {
        const copy = [...prev];
        copy[copy.length - 1] = { ...copy[copy.length - 1], citations };
        return copy;
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : "提问失败";
      setError(msg);
      setMessages((prev) => {
        const copy = [...prev];
        const last = copy[copy.length - 1];
        if (last && last.role === "assistant" && !last.content) {
          copy.splice(copy.length - 1, 1);
        }
        return copy;
      });
    } finally {
      setAsking(false);
    }
  };

  return (
    <div className="mx-auto flex h-[calc(100vh-8rem)] max-w-6xl gap-4">
      <aside className="flex w-56 shrink-0 flex-col rounded border border-gray-200 bg-white">
        <button
          className="m-2 rounded bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700"
          onClick={newConversation}
        >
          新建会话
        </button>
        <ul className="flex-1 overflow-y-auto">
          {conversations.length === 0 && (
            <li className="px-3 py-2 text-xs text-gray-400">暂无会话</li>
          )}
          {conversations.map((c) => (
            <li
              key={c.id}
              className={`group flex items-center gap-1 px-2 py-1.5 text-sm hover:bg-gray-50 ${
                activeId === c.id ? "bg-blue-50" : ""
              }`}
            >
              <button
                className="min-w-0 flex-1 truncate text-left"
                onClick={() => void openConversation(c.id)}
              >
                {c.title || `会话 #${c.id}`}
              </button>
              <button
                className="hidden text-xs text-red-500 group-hover:block"
                onClick={() => void removeConversation(c.id)}
              >
                删除
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col rounded border border-gray-200 bg-white">
        <div className="flex-1 space-y-4 overflow-y-auto p-4">
          {messages.length === 0 && (
            <p className="py-16 text-center text-sm text-gray-400">
              {activeId ? "输入问题开始对话" : "请先新建或选择一个会话"}
            </p>
          )}
          {messages.map((m) => (
            <MessageBubble key={m.id} message={m} />
          ))}
          <div ref={bottomRef} />
        </div>
        {error && <p className="px-4 pb-1 text-sm text-red-600">{error}</p>}
        <form
          className="flex gap-2 border-t border-gray-100 p-3"
          onSubmit={(e) => {
            e.preventDefault();
            void ask();
          }}
        >
          <input
            className="min-w-0 flex-1 rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={asking ? "回答中…" : "输入问题，回车发送"}
            disabled={!activeId || asking}
          />
          <button
            type="submit"
            className="rounded bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
            disabled={!activeId || asking || !input.trim()}
          >
            发送
          </button>
        </form>
      </div>
    </div>
  );
}
