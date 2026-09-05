/** 路由 → 后端 endpoint_class 映射（KEEP③：RBAC on 时菜单/⌘K 导航过滤用）。
 * classes 为 undefined（RBAC off 或 /me 未探测）→ 全显（零行为变更）。 */
export const ROUTE_CLASS: Record<string, string> = {
  "/chat": "chat",
  "/documents": "documents",
  "/graph": "graph",
  "/sync": "sync",
  "/monitor": "monitor",
  "/eval": "eval",
};

export const canSeeRoute = (route: string, classes: string[] | undefined): boolean =>
  !classes || classes.includes("*") || classes.includes(ROUTE_CLASS[route] ?? "");
