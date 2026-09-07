import { useEffect, useRef } from "react";

/** 通用确认弹窗：替代 window.confirm 的应用内实现（纸墨风格遮罩 + 面板）。 */
export default function ConfirmDialog({
  open,
  title,
  message,
  confirmText = "确定",
  cancelText = "取消",
  danger = false,
  busy = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const confirmRef = useRef<HTMLButtonElement>(null);

  // 打开时聚焦确认按钮；Esc 取消
  useEffect(() => {
    if (!open) return;
    confirmRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 p-4"
      onClick={onCancel}
    >
      <div
        className="panel w-full max-w-sm p-5"
        role="alertdialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="font-display text-base">{title}</h3>
        <p className="mt-2 text-sm leading-6 text-faint">{message}</p>
        <div className="mt-4 flex items-center justify-end gap-2">
          <button className="btn-ghost" onClick={onCancel} disabled={busy}>
            {cancelText}
          </button>
          <button
            ref={confirmRef}
            className={danger ? "btn-ghost text-seal" : "btn-primary"}
            onClick={onConfirm}
            disabled={busy}
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}
