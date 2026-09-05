import { useState } from "react";
import { ApiError, get, post, put } from "../api/client";

export interface Provider {
  id: number;
  kind: "llm" | "embedding" | "rerank";
  provider: string;
  base_url: string;
  model: string;
  api_key: string | null;
  is_default: boolean;
  params: Record<string, unknown>;
}

const KIND_LABEL: Record<Provider["kind"], string> = {
  llm: "LLM",
  embedding: "嵌入",
  rerank: "重排",
};

export default function ProviderForm({
  kind,
  provider,
  onSaved,
}: {
  kind: Provider["kind"];
  provider: Provider | null;
  onSaved: () => void;
}) {
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? "");
  const [model, setModel] = useState(provider?.model ?? "");
  const [apiKey, setApiKey] = useState("");
  const [isDefault, setIsDefault] = useState(provider?.is_default ?? false);
  const [models, setModels] = useState<string[]>([]);
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const refreshModels = async () => {
    setError("");
    setModels([]);
    if (!baseUrl.trim()) {
      setError("请先填写 Base URL");
      return;
    }
    try {
      const params = new URLSearchParams({ base_url: baseUrl.trim() });
      if (apiKey.trim()) params.set("api_key", apiKey.trim());
      const data = await get<{ models: string[] }>(`/api/settings/models?${params}`);
      setModels(data.models);
      if (data.models.length === 0) setError("服务未返回模型列表");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "获取模型列表失败");
    }
  };

  const testConnection = async () => {
    setError("");
    setTestResult(null);
    try {
      const started = performance.now();
      const res = await post<{ ok: boolean; dim?: number }>("/api/settings/providers/test", {
        kind,
        base_url: baseUrl.trim(),
        model: model.trim(),
        api_key: apiKey.trim() || undefined,
      });
      const ms = Math.round(performance.now() - started);
      const extra = kind === "embedding" && res.dim != null ? `，向量维度 ${res.dim}` : "";
      setTestResult({ ok: true, text: `连接成功${extra}，耗时 ${ms}ms` });
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "测试失败";
      setTestResult({ ok: false, text: msg });
    }
  };

  const save = async () => {
    setError("");
    setSaving(true);
    const body = {
      kind,
      base_url: baseUrl.trim(),
      model: model.trim(),
      api_key: apiKey.trim() || undefined,
      is_default: isDefault,
      params: provider?.params ?? {},
    };
    try {
      if (provider) await put(`/api/settings/providers/${provider.id}`, body);
      else await post("/api/settings/providers", body);
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="panel space-y-4 p-4">
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm font-medium">{KIND_LABEL[kind]}配置</h3>
        {!provider && <span className="text-xs text-faint">尚未配置，填写后保存</span>}
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <label className="block text-sm">
          <span className="mb-1 block">服务地址</span>
          <input
            className="input font-mono text-[13px]"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="http://localhost:19723/v1"
          />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block">API Key</span>
          <input
            type="password"
            className="input font-mono text-[13px]"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={provider?.api_key ?? "本地服务通常留空"}
          />
          <span className="mt-1 block text-xs text-faint">留空则保留已保存的密钥</span>
        </label>
        <label className="block text-sm">
          <span className="mb-1 block">模型</span>
          <div className="flex gap-2">
            {models.length > 0 ? (
              <select className="input" value={model} onChange={(e) => setModel(e.target.value)}>
                <option value="">请选择模型</option>
                {models.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            ) : (
              <input
                className="input font-mono text-[13px]"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="模型名称"
              />
            )}
            <button type="button" className="btn-ghost shrink-0 text-xs" onClick={refreshModels}>
              拉取列表
            </button>
          </div>
          <span className="mt-1 block text-xs text-faint">可从服务自动发现的模型中选择</span>
        </label>
        <label className="flex items-start gap-2 pt-6 text-sm">
          <input
            type="checkbox"
            className="mt-1"
            checked={isDefault}
            onChange={(e) => setIsDefault(e.target.checked)}
          />
          <span>
            设为默认
            <span className="mt-0.5 block text-xs text-faint">同一类模型只保留一个默认</span>
          </span>
        </label>
      </div>

      <div className="flex items-center gap-3">
        <button
          className="btn-primary"
          disabled={saving || !baseUrl.trim() || !model.trim()}
          onClick={save}
        >
          {saving ? "保存中…" : "保存"}
        </button>
        <button className="btn-ghost" onClick={testConnection}>
          测试连接
        </button>
        {testResult && (
          <span className={`text-sm ${testResult.ok ? "text-ok" : "text-seal"}`}>
            {testResult.text}
          </span>
        )}
      </div>
      {error && <p className="text-sm text-seal">{error}</p>}
    </div>
  );
}
