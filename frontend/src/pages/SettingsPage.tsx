import { useEffect, useState } from "react";
import { ApiError, del, get, post, put, type Workspace } from "../api/client";
import EmbeddingSwitcher from "../components/EmbeddingSwitcher";
import ProviderForm, { type Provider } from "../components/ProviderForm";

const KINDS = [
  { key: "llm", label: "LLM", hint: "用来生成回答的模型。本地 oMLX 一般是 http://localhost:19723/v1（容器内用 http://host.docker.internal:19723/v1）。" },
  { key: "embedding", label: "嵌入", hint: "把文档和问题转成向量用于检索。中文场景推荐多语言模型（如 bge-m3）。" },
  { key: "rerank", label: "重排", hint: "可选。对检索结果做二次排序，M3 版本支持。" },
] as const;

interface WorkspaceSettings {
  top_k: number;
  score_threshold: number;
  use_rerank: boolean;
  use_hybrid: boolean;
  context_max_tokens: number;
}

interface ApiKeyItem {
  id: number;
  name: string;
  key_prefix: string;
  created_at: string;
  last_used_at: string | null;
}

export default function SettingsPage({ workspace }: { workspace: Workspace }) {
  const [tab, setTab] = useState<(typeof KINDS)[number]["key"]>("llm");
  const [providers, setProviders] = useState<Provider[]>([]);
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [pwMsg, setPwMsg] = useState("");
  const [pwMsgError, setPwMsgError] = useState(false);
  const [wsSettings, setWsSettings] = useState<WorkspaceSettings | null>(null);
  const [wsMsg, setWsMsg] = useState("");
  const [apiKeys, setApiKeys] = useState<ApiKeyItem[]>([]);
  const [keyName, setKeyName] = useState("");
  const [keyCreating, setKeyCreating] = useState(false);
  const [createdKey, setCreatedKey] = useState<string | null>(null);
  const [keyMsg, setKeyMsg] = useState("");
  const [keyMsgError, setKeyMsgError] = useState(false);
  const [copied, setCopied] = useState(false);

  const refreshProviders = async () => {
    setProviders(await get<Provider[]>("/api/settings/providers"));
  };
  const refreshWs = async () => {
    setWsSettings(await get<WorkspaceSettings>(`/api/workspaces/${workspace.id}/settings`));
  };
  const refreshKeys = async () => {
    setApiKeys(await get<ApiKeyItem[]>("/api/keys"));
  };

  useEffect(() => {
    void refreshProviders().catch(() => undefined);
    void refreshWs().catch(() => undefined);
    void refreshKeys().catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace.id]);

  const savePassword = async () => {
    setPwMsg("");
    setPwMsgError(false);
    if (newPassword !== confirmPassword) {
      setPwMsg("两次输入的新密码不一致");
      setPwMsgError(true);
      return;
    }
    try {
      await put("/api/auth/password", {
        old_password: oldPassword,
        new_password: newPassword,
      });
      setOldPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setPwMsg("密码已更新");
    } catch (e) {
      setPwMsg(e instanceof ApiError ? e.message : "保存失败");
      setPwMsgError(true);
    }
  };

  const saveWs = async () => {
    if (!wsSettings) return;
    setWsMsg("");
    try {
      await put(`/api/workspaces/${workspace.id}/settings`, wsSettings);
      setWsMsg("已保存");
    } catch (e) {
      setWsMsg(e instanceof ApiError ? e.message : "保存失败");
    }
  };

  const currentProvider = providers.find((p) => p.kind === tab) ?? null;
  const activeKind = KINDS.find((k) => k.key === tab)!;

  const createKey = async () => {
    setKeyMsg("");
    setKeyMsgError(false);
    if (!keyName.trim()) {
      setKeyMsg("请输入密钥名称");
      setKeyMsgError(true);
      return;
    }
    setKeyCreating(true);
    try {
      const created = await post<ApiKeyItem & { key: string }>("/api/keys", {
        name: keyName.trim(),
      });
      setCreatedKey(created.key);
      setKeyName("");
      setCopied(false);
      await refreshKeys();
    } catch (e) {
      setKeyMsg(e instanceof ApiError ? e.message : "创建失败");
      setKeyMsgError(true);
    } finally {
      setKeyCreating(false);
    }
  };

  const revokeKey = async (item: ApiKeyItem) => {
    if (!window.confirm(`确定吊销「${item.name}」吗？使用该密钥的客户端将立即无法连接。`)) return;
    setKeyMsg("");
    setKeyMsgError(false);
    try {
      await del(`/api/keys/${item.id}`);
      await refreshKeys();
    } catch (e) {
      setKeyMsg(e instanceof ApiError ? e.message : "吊销失败");
      setKeyMsgError(true);
    }
  };

  const copyKey = async () => {
    if (!createdKey) return;
    await navigator.clipboard.writeText(createdKey);
    setCopied(true);
  };

  const formatTime = (iso: string | null, empty: string) => {
    if (!iso) return empty;
    return new Date(iso).toLocaleString("zh-CN", { hour12: false });
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl px-4 py-6">
      <h1 className="font-display text-lg">设置</h1>

      <section className="mt-6">
        <div className="flex gap-1 border-b border-line pb-3">
          {KINDS.map((k) => (
            <button
              key={k.key}
              className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
                tab === k.key
                  ? "bg-iblue-soft font-medium text-iblue"
                  : "text-faint hover:text-ink"
              }`}
              onClick={() => setTab(k.key)}
            >
              {k.label}
            </button>
          ))}
        </div>
        <p className="mt-3 text-xs leading-5 text-faint">{activeKind.hint}</p>
        <div className="mt-3">
          <ProviderForm
            key={`${tab}-${currentProvider?.id ?? "new"}`}
            kind={tab}
            provider={currentProvider}
            onSaved={() => void refreshProviders()}
          />
          {tab === "embedding" && (
            <div className="mt-4">
              <EmbeddingSwitcher />
            </div>
          )}
        </div>
      </section>

      <div className="mt-8 grid grid-cols-1 items-start gap-x-12 gap-y-8 border-t border-line pt-6 lg:grid-cols-2">
        <section className="lg:border-r lg:border-line lg:pr-12">
          <h2 className="font-display text-base">检索参数</h2>
        <p className="mt-1 text-xs text-faint">控制每次回答时如何从文档中取材，改动只影响当前工作区。</p>
        {wsSettings && (
          <>
            <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
              <label className="block text-sm">
                <span className="mb-1 block">每次检索的段落数（1–20）</span>
                <input
                  type="number"
                  min={1}
                  max={20}
                  className="input"
                  value={wsSettings.top_k}
                  onChange={(e) => setWsSettings({ ...wsSettings, top_k: Number(e.target.value) })}
                />
                <span className="mt-1 block text-xs text-faint">取多少段最相关的原文给模型参考</span>
              </label>
              <label className="block text-sm">
                <span className="mb-1 block">相似度阈值（0–1）</span>
                <input
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  className="input"
                  value={wsSettings.score_threshold}
                  onChange={(e) =>
                    setWsSettings({ ...wsSettings, score_threshold: Number(e.target.value) })
                  }
                />
                <span className="mt-1 block text-xs text-faint">
                  低于这个相似度的段落直接丢弃，0 表示不过滤（仅关闭混合检索时生效）
                </span>
              </label>
              <label className="block text-sm">
                <span className="mb-1 block">单次回答的资料上限（500–8000 tokens）</span>
                <input
                  type="number"
                  min={500}
                  max={8000}
                  step={100}
                  className="input"
                  value={wsSettings.context_max_tokens}
                  onChange={(e) =>
                    setWsSettings({ ...wsSettings, context_max_tokens: Number(e.target.value) })
                  }
                />
                <span className="mt-1 block text-xs text-faint">一次回答最多带入多少原文，太大时回答会变慢</span>
              </label>
              <label className="flex items-start gap-2 pt-7 text-sm">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={wsSettings.use_rerank}
                  onChange={(e) => setWsSettings({ ...wsSettings, use_rerank: e.target.checked })}
                />
                <span>
                  启用重排
                  <span className="mt-0.5 block text-xs text-faint">对召回结果做二次排序（当前为简化实现）</span>
                </span>
              </label>
              <label className="flex items-start gap-2 pt-7 text-sm">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={wsSettings.use_hybrid}
                  onChange={(e) => setWsSettings({ ...wsSettings, use_hybrid: e.target.checked })}
                />
                <span>
                  启用混合检索
                  <span className="mt-0.5 block text-xs text-faint">向量召回与全文检索融合排序；关闭后仅用向量召回</span>
                </span>
              </label>
            </div>
            <div className="mt-4 flex items-center gap-3">
              <button className="btn-primary" onClick={() => void saveWs()}>
                保存
              </button>
              {wsMsg && <span className="text-sm text-faint">{wsMsg}</span>}
            </div>
          </>
        )}
      </section>

      <section className="min-w-0">
        <h2 className="font-display text-base">修改密码</h2>
        <p className="mt-1 text-xs leading-5 text-faint">
          修改后当前会话仍然有效，其他设备下次请求时需要用新密码重新登录。
        </p>
        <input
          type="password"
          className="input mt-4 max-w-56"
          value={oldPassword}
          onChange={(e) => setOldPassword(e.target.value)}
          placeholder="旧密码"
        />
        <input
          type="password"
          className="input mt-3 max-w-56"
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
          placeholder="新密码（至少 6 位）"
        />
        <input
          type="password"
          className="input mt-3 max-w-56"
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
          placeholder="确认新密码"
        />
        <div className="mt-4">
          <button
            className="btn-primary"
            disabled={!oldPassword || !newPassword || !confirmPassword}
            onClick={() => void savePassword()}
          >
            更新密码
          </button>
        </div>
        {pwMsg && (
          <p className={`mt-2 text-sm ${pwMsgError ? "text-seal" : "text-faint"}`}>{pwMsg}</p>
        )}
      </section>

      <section className="lg:border-r lg:border-line lg:pr-12">
        <h2 className="font-display text-base">API 密钥</h2>
        <p className="mt-1 text-xs leading-5 text-faint">
          外部 Agent（如 Claude Code）通过 MCP 协议连接知识库时使用的凭据，创建方法见 README「MCP 服务」。
        </p>
        <div className="mt-4 flex items-center gap-3">
          <input
            className="input max-w-56"
            value={keyName}
            onChange={(e) => setKeyName(e.target.value)}
            placeholder="密钥名称（如：Claude Code）"
          />
          <button className="btn-primary" disabled={!keyName.trim() || keyCreating} onClick={() => void createKey()}>
            创建密钥
          </button>
        </div>
        {keyMsg && <p className={`mt-2 text-sm ${keyMsgError ? "text-seal" : "text-faint"}`}>{keyMsg}</p>}
        {apiKeys.length > 0 && (
          <div className="panel mt-4 divide-y divide-line">
            {apiKeys.map((item) => (
              <div key={item.id} className="flex items-center gap-3 px-3 py-2.5 text-sm">
                <span className="min-w-0 flex-1 truncate">{item.name}</span>
                <span className="font-mono text-xs text-faint">{item.key_prefix}…</span>
                <span className="hidden text-xs text-faint sm:block">
                  创建于 {formatTime(item.created_at, "—")}
                </span>
                <span className="hidden text-xs text-faint md:block">
                  最近使用：{formatTime(item.last_used_at, "未使用")}
                </span>
                <button className="btn-ghost shrink-0 text-seal" onClick={() => void revokeKey(item)}>
                  吊销
                </button>
              </div>
            ))}
          </div>
        )}
      </section>
      </div>
      </div>

      {createdKey && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 p-4">
          <div className="panel w-full max-w-md p-5">
            <h3 className="font-display text-base">密钥已创建</h3>
            <p className="mt-2 text-xs leading-5 text-seal">
              这是唯一一次展示明文密钥，关闭后无法再查看，请妥善保存。
            </p>
            <div className="mt-3 break-all rounded-md bg-paper-deep p-2.5 font-mono text-[13px]">
              {createdKey}
            </div>
            <div className="mt-4 flex items-center justify-end gap-2">
              <button className="btn-ghost" onClick={() => void copyKey()}>
                {copied ? "已复制" : "复制"}
              </button>
              <button className="btn-primary" onClick={() => setCreatedKey(null)}>
                我已保存，关闭
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
