import { useEffect } from "react";
import { useAppStore } from "../stores/app";

/**
 * 全局 ⌘K / Ctrl+K 打开命令面板。挂在 Workbench 根节点 → 所有路由生效。
 * 只注册开快捷键；关闭由 Modal 的 onCancel / Esc / 选中后自行处理。
 */
export function useHotkey(): void {
  const setCmdkOpen = useAppStore((s) => s.setCmdkOpen);
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCmdkOpen(true);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [setCmdkOpen]);
}
