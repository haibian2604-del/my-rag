import { useEffect, useRef, useState } from "react";
import { ApiError, get, post, put, type Conversation, type Workspace } from "../api/client";
import { parseSSE, type Citation, type SSEEvent } from "../api/sse";
import MessageBubble, { type ChatMessage, type ToolStep } from "../components/MessageBubble";

const STARTERS = [
  "这份资料的主要内容是什么？",
  "帮我总结其中的要点",
  "资料里提到了哪些数据或步骤？",
];

// 检索阶段提示文案：后端 stage 事件 → 等待区展示
const STAGE_LABELS: Record<string, string> = {
  retrieving: "正在检索知识库…",
  reranking: "正在重排命中结果…",
  generating: "正在生成回答…",
};

export default function ChatPage({
  workspace,
  activeId,
  onActiveChange,
  conversations,
  onConversationsChange,
  onRefreshConversations,
}: {
  workspace: Workspace;
  activeId: number | null;
  onActiveChange: (id: number | null) => void;
  conversations: Conversation[];
  onConversationsChange: (list: Conversation[]) => void;
  onRefreshConversations: (wsId: number) => Promise<Conversation[]>;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [asking, setAsking] = useState(false);
  const [stage, setStage] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [followups, setFollowups] = useState<string[]>([]);
  const [docSummary, setDocSummary] = useState<{ ready: number; total: number } | null>(null);
  const [agentAvailable, setAgentAvailable] = useState(false);
  // Agent 模式为会话级开关：仅当后端探测到 LLM 支持工具调用时才展示
  const [agentMode, setAgentMode] = useState(false);
  const messagesAreaRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const bootstrappedWs = useRef<number | null>(null);


  const loadMessages = async (id: number) => {
    const rows = await get<
      {
        id: number;
        role: string;
        content: string;
        citations: Citation[] | null;
        trace: ToolStep[] | null;
      }[]
    >(`/api/conversations/${id}/messages`);
    setMessages(
      rows.map((m) => ({
        id: m.id,
        role: m.role === "user" ? "user" : "assistant",
        content: m.content,
        citations: m.citations ?? undefined,
        trace: m.trace ?? undefined,
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
        const list = await onRefreshConversations(workspace.id);
        if (activeId != null && list.some((c) => c.id === activeId)) {
          return; // 从其他页面返回：激活会话仍在本工作区，effect 会自动加载
        }
        if (activeId != null) onActiveChange(null); // 会话已不属于本工作区
        const reuse = list.find((c) => !c.title || c.title === "新对话");
        if (reuse) {
          onActiveChange(reuse.id);
        } else {
          const conv = await post<Conversation>(
            `/api/workspaces/${workspace.id}/conversations`,
          );
          await onRefreshConversations(workspace.id);
          onActiveChange(conv.id);
        }
      } catch (e) {
        setError(e instanceof ApiError ? e.message : "初始化会话失败");
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace.id]);

  // 激活会话变化（含从边栏选择/新建/置空）→ 加载对应消息
  const loadedIdRef = useRef<number | null>(null);
  useEffect(() => {
    if (asking) return; // 请求进行中不重载，避免回答/trace 回填错乱
    if (activeId == null) {
      loadedIdRef.current = null;
      setMessages([]);
      return;
    }
    if (loadedIdRef.current === activeId) return;
    loadedIdRef.current = activeId;
    setError("");
    setFollowups([]);
    loadMessages(activeId).catch((e) =>
      setError(e instanceof ApiError ? e.message : "加载会话失败"),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId, asking]);

  useEffect(() => {
    // 只滚动消息容器自身，避免 scrollIntoView 连带滚动整个文档把侧边栏顶走
    const el = messagesAreaRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [messages]);

  // 挂载时探测 Agent 能力：后端启动时对默认 LLM 做过最小 tools 请求探测
  useEffect(() => {
    get<{ available: boolean }>("/api/agent/capability")
      .then((r) => setAgentAvailable(Boolean(r.available)))
      .catch(() => setAgentAvailable(false));
  }, []);

  // 空状态引导：显示可问答的文档数 + 建议问题（生成失败静默不显示，回退默认引导问题）
  useEffect(() => {
    get<{ status: string }[]>(`/api/workspaces/${workspace.id}/documents`)
      .then((list) =>
        setDocSummary({
          ready: list.filter((d) => d.status === "ready").length,
          total: list.length,
        }),
      )
      .catch(() => undefined);
    get<{ questions: string[] }>(`/api/workspaces/${workspace.id}/suggestions`)
      .then((r) => setSuggestions(Array.isArray(r.questions) ? r.questions : []))
      .catch(() => setSuggestions([]));
  }, [workspace.id, messages.length]);

  const ask = async (questionRaw?: string) => {
    const question = (questionRaw ?? input).trim();
    if (!question || !activeId || asking) return;
    if (!questionRaw) {
      setInput("");
      resizeTa();
    }
    setError("");
    setAsking(true);
    setStage(null);
    setFollowups([]); // 新一轮提问前清掉上一轮追问
    abortRef.current = new AbortController();
    const conv = conversations.find((c) => c.id === activeId);
    if (conv && (!conv.title || conv.title === "新对话")) {
      // 标题乐观更新 + 持久化到后端
      const newTitle = question.slice(0, 20);
      onConversationsChange(
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
        body: JSON.stringify({ question, mode: agentMode ? "agent" : "rag" }),
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
        } else if (ev.type === "stage") {
          setStage(ev.stage); // 等待区按最新阶段提示
        } else if (ev.type === "delta") {
          setMessages((prev) => {
            const copy = [...prev];
            copy[copy.length - 1] = {
              ...copy[copy.length - 1],
              content: copy[copy.length - 1].content + ev.text,
            };
            return copy;
          });
        } else if (ev.type === "agent") {
          // 工具时间线实时累积到最后一条回答的 trace 上
          setMessages((prev) => {
            const copy = [...prev];
            const last = copy[copy.length - 1];
            if (!last || last.role !== "assistant") return copy;
            const trace = [...(last.trace ?? [])];
            if (ev.event === "tool_call") {
              trace.push({ tool: ev.tool, args: ev.args });
            } else {
              // tool_result：回填最近一次同名调用的 preview（找不到则单独成条）
              const idx = trace.findLastIndex(
                (s) => s.tool === ev.tool && s.preview === undefined,
              );
              if (idx >= 0) trace[idx] = { ...trace[idx], preview: ev.preview };
              else trace.push({ tool: ev.tool, preview: ev.preview });
            }
            copy[copy.length - 1] = { ...last, trace };
            return copy;
          });
        } else if (ev.type === "error") {
          errorMsg = ev.message;
        } else if (ev.type === "done") {
          setFollowups(Array.isArray(ev.followups) ? ev.followups : []);
          break;
        }
      }
      if (errorMsg) throw new Error(errorMsg);
      setMessages((prev) => {
        const copy = [...prev];
        copy[copy.length - 1] = { ...copy[copy.length - 1], citations };
        return copy;
      });
      void onRefreshConversations(workspace.id);
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
      setStage(null);
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
  // 空态展示的问题：优先后端生成的建议问题，无则回退默认引导
  const starters = suggestions.length > 0 ? suggestions : STARTERS;

  return (
    <div className="flex h-full min-h-0 flex-col">

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="border-b border-line px-5 py-2.5">
          <p className="truncate font-display text-sm">
            {activeConv?.title || "未选择会话"}
          </p>
        </div>

        <div ref={messagesAreaRef} className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
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
                      {starters.map((s) => (
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
            {messages.map((m, i) => (
              <MessageBubble
                key={m.id}
                message={m}
                traceLive={asking && i === messages.length - 1}
              />
            ))}
            {!asking && followups.length > 0 && (
              <div className="flex flex-col items-start gap-1.5">
                <p className="text-xs text-faint">可以继续追问：</p>
                <div className="flex flex-wrap gap-2">
                  {followups.map((q) => (
                    <button
                      key={q}
                      className="rounded-full border border-line bg-card px-3 py-1 text-xs text-ink transition-colors hover:border-iblue hover:text-iblue"
                      onClick={() => void ask(q)}
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {waitingFirstToken && (
              <p className="text-sm text-faint">
                {(stage && STAGE_LABELS[stage]) || "正在检索资料并思考…"}
              </p>
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
            {agentAvailable && (
              <label
                className="flex shrink-0 cursor-pointer select-none items-center gap-1.5 rounded-md border border-line bg-card px-2.5 py-2 text-xs text-faint transition-colors hover:border-iblue has-[:checked]:border-iblue has-[:checked]:bg-iblue-soft has-[:checked]:text-iblue"
                title="开启后由 Agent 自动检索知识库、抓取网页来回答"
              >
                <input
                  type="checkbox"
                  className="h-3.5 w-3.5 accent-[var(--color-iblue)]"
                  checked={agentMode}
                  onChange={(e) => setAgentMode(e.target.checked)}
                />
                Agent
              </label>
            )}
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
