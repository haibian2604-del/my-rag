import { useCallback, useEffect, useState } from "react";
import { ApiError, get, post } from "../api/client";

interface SwitchState {
  state: "idle" | "running" | "done" | "failed";
  target_model?: string;
  total?: number;
  done?: number;
  error?: string;
  current_model: string | null;
}

/** 嵌入模型切换向导：重嵌 → 切换 → 回滚，同页完成。 */
export default function EmbeddingSwitcher() {
  const [state, setState] = useState<SwitchState | null>(null);
  const [targetModel, setTargetModel] = useState("");
  const [rollbackModel, setRollbackModel] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(async (): Promise<SwitchState | null> => {
    try {
      const data = await get<SwitchState>("/api/settings/embedding/switch");
      setState(data);
      return data;
    } catch {
      return null;
    }
  }, []);

  // 唯一轮询链：挂载时拉取一次；state 变为 running 后恢复 1.5s 轮询
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const tick = async () => {
      const data = await refresh();
      if (stopped) return;
      if (data?.state === "running") {
        timer = setTimeout(tick, 1500);
      }
    };
    void tick();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [refresh, state?.state]);

  const startSwitch = async () => {
    const target = targetModel.trim();
    if (!target) return;
    setError("");
    setBusy(true);
    try {
      await post("/api/settings/embedding/switch", { target_model: target });
      setTargetModel("");
      await refresh(); // 置为 running，由上方 effect 恢复轮询
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "启动重嵌失败");
    } finally {
      setBusy(false);
    }
  };

  const activate = async (model: string, isRollback: boolean) => {
    setError("");
    setBusy(true);
    try {
      await post("/api/settings/embedding/activate", { model: model.trim() });
      if (isRollback) setRollbackModel("");
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "切换失败");
    } finally {
      setBusy(false);
    }
  };

  const retry = async () => {
    // 重试即用原目标模型再次发起
    const target = state?.target_model?.trim();
    if (!target) return;
    setError("");
    setBusy(true);
    try {
      await post("/api/settings/embedding/switch", { target_model: target });
      await refresh(); // 置为 running，由上方 effect 恢复轮询
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "重试失败");
    } finally {
      setBusy(false);
    }
  };

  const s = state;
  const running = s?.state === "running";
  const done = s?.state === "done";
  const failed = s?.state === "failed";
  const switched = done && s?.current_model != null && s.current_model === s.target_model;
  const canSwitch = done && !switched && !!s?.target_model;
  const pct = running && s?.total && s.total > 0 ? Math.round(((s.done ?? 0) / s.total) * 100) : 0;

  return (
    <div className="panel space-y-4 p-4">
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm font-medium">嵌入模型切换</h3>
        {s?.current_model && (
          <span className="text-xs text-faint">
            当前模型：<span className="font-mono">{s.current_model}</span>
          </span>
        )}
      </div>

      {!running && (
        <label className="block text-sm">
          <span className="mb-1 block">新嵌入模型</span>
          <div className="flex gap-2">
            <input
              className="input font-mono text-[13px]"
              value={targetModel}
              onChange={(e) => setTargetModel(e.target.value)}
              placeholder="例如 bge-m3"
            />
            <button
              type="button"
              className="btn-primary shrink-0"
              disabled={busy || !targetModel.trim()}
              onClick={() => void startSwitch()}
            >
              开始重嵌
            </button>
          </div>
          <span className="mt-1 block text-xs text-faint">
            会对全部文档用新模型重新生成向量，完成后可再切换
          </span>
        </label>
      )}

      {running && (
        <div className="space-y-2">
          <div className="h-2 w-full overflow-hidden rounded-full bg-iblue-soft">
            <div className="h-full rounded-full bg-iblue transition-all" style={{ width: `${pct}%` }} />
          </div>
          <p className="text-xs text-faint">
            重嵌中 {s?.done ?? 0}/{s?.total ?? "?"}
          </p>
        </div>
      )}

      {canSwitch && (
        <div className="flex items-center gap-3">
          <span className="text-sm">重嵌完成，可切换</span>
          <button
            className="btn-primary"
            disabled={busy}
            onClick={() => void activate(s!.target_model!, false)}
          >
            切换到 {s!.target_model}
          </button>
        </div>
      )}

      {switched && (
        <div className="space-y-3">
          <p className="text-sm">已切换，旧模型向量保留</p>
          <label className="block text-sm">
            <span className="mb-1 block">回滚到旧模型</span>
            <div className="flex gap-2">
              <input
                className="input font-mono text-[13px]"
                value={rollbackModel}
                onChange={(e) => setRollbackModel(e.target.value)}
                placeholder="旧模型名称"
              />
              <button
                type="button"
                className="btn-ghost shrink-0"
                disabled={busy || !rollbackModel.trim()}
                onClick={() => void activate(rollbackModel, true)}
              >
                回滚
              </button>
            </div>
            <span className="mt-1 block text-xs text-faint">
              填写之前使用的模型名即可切回（其向量仍保留）
            </span>
          </label>
        </div>
      )}

      {failed && (
        <div className="flex items-center gap-3">
          <p className="text-sm text-seal">{s?.error ?? "重嵌失败"}</p>
          <button className="btn-ghost" disabled={busy} onClick={() => void retry()}>
            重试
          </button>
        </div>
      )}

      {error && <p className="text-sm text-seal">{error}</p>}
    </div>
  );
}
