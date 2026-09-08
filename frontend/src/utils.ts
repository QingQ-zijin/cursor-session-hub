export function displayDate(time?: number) {
  if (!time) return "尚未同步";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(time * 1000));
}
export function bytes(value = 0) {
  if (value < 1024) return value + " B";
  if (value < 1048576) return (value / 1024).toFixed(1) + " KB";
  return (value / 1048576).toFixed(1) + " MB";
}
export function stringify(value: unknown): string {
  if (value == null) return "";
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}
export function boundedExpanded(current: number[], next: number, max = 3) {
  if (current.includes(next)) return current.filter((n) => n !== next);
  return [...current, next].slice(-max);
}
export const labels: Record<string, string> = {
  local: "仅本地",
  local_only: "仅本地",
  pending: "待同步",
  queued: "排队中",
  running: "处理中",
  paused: "已暂停",
  succeeded: "已完成",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  synced: "已同步",
  syncing: "同步中",
  changed: "更新待同步",
  update_pending: "更新待同步",
  indexed: "已入库",
  ready: "已入库",
  ready_with_diagnostics: "包含解析说明",
  ingest: "解析记录",
  bundle_import: "接收同步记录",
  discovered: "待入库",
  import: "解析记录",
  index: "解析记录",
  scan: "发现记录",
  export: "导出记录",
  sync: "同步服务器",
  bundle: "准备同步",
  active: "正常",
  partial: "部分解析",
  invalid: "格式异常",
};
export function label(s?: string) {
  return s ? labels[s] || s : "仅本地";
}
export function sessionStatus(s: { status?: string; sync_status?: string }) {
  return s.status &&
    ["queued", "running", "paused", "failed", "cancelled"].includes(s.status)
    ? s.status
    : s.sync_status || s.status || "local_only";
}
export function roundPreview(text: string) {
  return text.replace(/^(?:user|assistant) text /, "");
}
export function safeLink(href?: string) {
  if (!href) return "";
  try {
    const u = new URL(href, location.origin);
    return ["http:", "https:", "mailto:"].includes(u.protocol) ? href : "";
  } catch {
    return "";
  }
}
