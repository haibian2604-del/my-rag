import { useEffect, useRef, useState } from "react";
import { ApiError, del, get, post, put, type Workspace } from "../api/client";
import { parseSSE, type Citation, type SSEEvent } from "../api/sse";
import MessageBubble, { type ChatMessage } from "../components/MessageBubble";

interface Conversation {
  id: number;
  workspace_id: number;
  title: string;
  created_at?: string;
}

const STARTERS = [
  "这份资料的主要内容是什么？",
  "帮我总结其中的要点",
  "资料里提到了哪些数据或步骤？",
];

export default function ChatPage({
  workspace,
  activeId,
  onActiveChange,
}: {
  workspace: Workspace;
  activeId: number | null;
  onActiveChange: (id: number | null) => void;
}) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState("");
  const [docSummary, setDocSummary] = useState<{ ready: number; total: number } | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const bootstrappedWs = useRef<number | null>(null);

  const refreshConversations = () =>
    get<Conversation[]>(`/api/workspaces/${workspace.id}/conversations`)
      .then(setConversations)
      .catch((e) => setError(e instanceof ApiError ? e.message : "加载会话列表失败"));

  const loadMessages = async (id: number) => {
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
  };

  // 挂载/切换工作区引导：有激活会话则恢复，否则复用未提问的空会话或自动新建
  useEffect(() => {
    if (bootstrappedWs.current === workspace.id) return;
    bootstrappedWs.current = workspace.id;
    setMessages([]);
    (async () => {
      try {
        const list = await get<Conversation[]>(
          `/api/workspaces/${workspace.id}/conversations`,
        );
        setConversations(list);
        if (activeId != null) {
          try {
            await loadMessages(activeId); // 从其他页面返回：恢复离开的会话
            return;
          } catch {
            onActiveChange(null); // 会话已不存在，走新建
          }
        }
        const reuse = list.find((c) => !c.title || c.title === "新对话");
        if (reuse) {
          onActiveChange(reuse.id);
          await loadMessages(reuse.id);
        } else {
          const conv = await post<Conversation>(
            `/api/workspaces/${workspace.id}/conversations`,
          );
          setConversations([conv, ...list]);
          onActiveChange(conv.id);
        }
      } catch (e) {
        setError(e instanceof ApiError ? e.message : "初始化会话失败");
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace.id]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // 空状态引导：显示可问答的文档数
  useEffect(() => {
    get<{ status: string }[]>(`/api/workspaces/${workspace.id}/documents`)
      .then((list) =>
        setDocSummary({
          ready: list.filter((d) => d.status === "ready").length,
          total: list.length,
        }),
      )
      .catch(() => undefined);
  }, [workspace.id, messages.length]);

  const openConversation = async (id: number) => {
    onActiveChange(id);
    setError("");
    try {
      await loadMessages(id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "加载会话失败");
    }
  };

  const newConversation = async () => {
    try {
      const conv = await post<Conversation>(`/api/workspaces/${workspace.id}/conversations`);
      await refreshConversations();
      onActiveChange(conv.id);
      setMessages([]);
      setError("");
      taRef.current?.focus();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "创建会话失败");
    }
  };

  const removeConversation = async (id: number) => {
    const conv = conversations.find((c) => c.id === id);
    const label = conv?.title && conv.title !== "新对话" ? `「${conv.title}」` : "该会话";
    if (!window.confirm(`删除会话${label}？其中的问答记录将一并删除，不可恢复。`)) return;
    try {
      await del(`/api/conversations/${id}`);
      await refreshConversations();
      if (activeId === id) {
        onActiveChange(null);
        setMessages([]);
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "删除会话失败");
    }
  };

  const ask = async (questionRaw?: string) => {
    const question = (questionRaw ?? input).trim();
    if (!question || !activeId || asking) return;
    if (!questionRaw) {
      setInput("");
      resizeTa();
    }
    setError("");
    setAsking(true);
    abortRef.current = new AbortController();
    const conv = conversations.find((c) => c.id === activeId);
    if (conv && (!conv.title || conv.title === "新对话")) {
      // 标题乐观更新 + 持久化到后端
      const newTitle = question.slice(0, 20);
      setConversations(
        conversations.map((c) => (c.id === activeId ? { ...c, title: newTitle } : c)),
      );
      void put<Conversation>(`/api/conversations/${activeId}`, { title: newTitle }).catch(
        () => undefined,
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
        signal: abortRef.current.signal,
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
            copy[copy.length - 1] = {
              ...copy[copy.length - 1],
              content: copy[copy.length - 1].content + ev.text,
            };
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
      void refreshConversations();
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        // 用户主动停止：保留已生成的部分
      } else {
        const msg = e instanceof Error ? e.message : "提问失败";
        setError(msg);
      }
      setMessages((prev) => {
        const copy = [...prev];
        const last = copy[copy.length - 1];
        if (last && last.role === "assistant" && !last.content) {
          copy.splice(copy.length - 1, 1);
        }
        return copy;
      });
    } finally {
      abortRef.current = null;
      setAsking(false);
    }
  };

  const stop = () => {
    abortRef.current?.abort();
  };

  const resizeTa = () => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // 中文输入法组词中的回车不发送
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void ask();
    }
  };

  const activeConv = conversations.find((c) => c.id === activeId);
  const waitingFirstToken =
    asking && messages[messages.length - 1]?.role === "assistant" && !messages[messages.length - 1]?.content;

  const emptyHint = () => {
    if (!activeId) return "新建或选择一个会话，向自己的文档提问";
    const { ready, total } = docSummary ?? { ready: 0, total: 0 };
    if (total === 0) return "书箧还是空的——先到「文档库」上传文档，再来提问";
    if (ready === 0) return "文档正在处理中，等它们变为「可问答」后就能提问了";
    return null;
  };
  const hint = emptyHint();

  return (
    <div className="flex h-full min-h-0">
      <aside className="flex w-52 shrink-0 flex-col border-r border-line bg-card max-md:hidden">
        <div className="p-2.5">
          <button className="btn-ghost w-full" onClick={() => void newConversation()}>
            新建会话
          </button>
        </div>
        <ul className="min-h-0 flex-1 overflow-y-auto pb-2">
          {conversations.length === 0 && (
            <li className="px-3 py-2 text-xs text-faint">暂无会话</li>
          )}
          {conversations.map((c) => (
            <li key={c.id} className="group relative">
              <button
                className={`w-full truncate px-3 py-2 pr-8 text-left text-sm transition-colors ${
                  activeId === c.id
                    ? "bg-iblue-soft font-medium text-iblue"
                    : "text-ink hover:bg-paper"
                }`}
                onClick={() => void openConversation(c.id)}
                title={c.title || `会话 #${c.id}`}
              >
                {c.title || `会话 #${c.id}`}
              </button>
              <button
                className="absolute right-2 top-2 hidden text-xs text-seal group-hover:block"
                onClick={() => void removeConversation(c.id)}
                title="删除会话"
              >
                删除
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="border-b border-line px-5 py-2.5">
          <p className="truncate font-display text-sm">
            {activeConv?.title || "未选择会话"}
          </p>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
          <div className="mx-auto max-w-[72ch] space-y-5">
            {messages.length === 0 && (
              <div className="py-14 text-center">
                {hint ? (
                  <p className="mt-8 text-sm text-faint">{hint}</p>
                ) : (
                  <>
                    <p className="mt-8 text-sm text-faint">
                      书箧中有 {docSummary?.ready} 篇文档可问答，试试这些问题：
                    </p>
                    <div className="mt-4 flex flex-col items-center gap-2">
                      {STARTERS.map((s) => (
                        <button
                          key={s}
                          className="rounded-full border border-line bg-card px-4 py-1.5 text-sm text-ink transition-colors hover:border-iblue hover:text-iblue"
                          onClick={() => void ask(s)}
                        >
                          {s}
                        </button>
                      ))}
                    </div>
                  </>
                )}
              </div>
            )}
            {messages.map((m) => (
              <MessageBubble key={m.id} message={m} />
            ))}
            {waitingFirstToken && (
              <p className="text-sm text-faint">正在检索资料并思考…</p>
            )}
            <div ref={bottomRef} />
          </div>
        </div>

        {error && <p className="px-5 pb-1 text-sm text-seal">{error}</p>}

        <form
          className="border-t border-line p-3"
          onSubmit={(e) => {
            e.preventDefault();
            void ask();
          }}
        >
          <div className="mx-auto flex max-w-[76ch] items-end gap-2">
            <textarea
              ref={taRef}
              rows={1}
              className="input max-h-40 resize-none"
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
                resizeTa();
              }}
              onKeyDown={onKeyDown}
              placeholder={activeId ? "问点什么，回车发送，Shift+回车换行" : "先新建一个会话"}
              disabled={!activeId || asking}
            />
            {asking ? (
              <button type="button" className="btn-ghost shrink-0" onClick={stop}>
                停止
              </button>
            ) : (
              <button
                type="submit"
                className="btn-primary shrink-0"
                disabled={!activeId || !input.trim()}
              >
                发送
              </button>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}
