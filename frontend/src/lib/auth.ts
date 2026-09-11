/**
 * 访问令牌：唯一读写点是 localStorage['pf.token']（契约 HARDENING §7）。
 *
 * 为什么集中在这一个小模块：
 * - 三条出口（REST / SSE / WebSocket）必须带同一份凭据，分散取用迟早会漏掉一条；
 * - WebSocket 构造器**不允许自定义请求头**，只能把令牌放到查询串里，
 *   这个差异被 `withAuth()` 吸收，调用方无需感知；
 * - 隐私模式 / 存储被禁用时 localStorage 读写会直接抛异常，
 *   这里一律静默兜底（拿不到令牌就当匿名请求），绝不让取令牌把界面搞崩。
 */

/** 契约冻结的存储键名，不要改 */
const TOKEN_KEY = 'pf.token'

/** 读取访问令牌；任何存储异常（隐私模式、配额、被禁用）都退化为「无令牌」 */
export function getToken(): string {
  try {
    // trim：用户从别处粘贴时常带首尾空白或换行，带着空白发出去后端必然 403
    return (window.localStorage.getItem(TOKEN_KEY) ?? '').trim()
  } catch {
    return ''
  }
}

/** 写入访问令牌；空串表示清除（留空即不带头，契约 §7） */
export function setToken(t: string): void {
  const value = t.trim()
  try {
    if (value) window.localStorage.setItem(TOKEN_KEY, value)
    else window.localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* 存储不可用时静默忽略：本次请求仍可发送，只是不带凭据 */
  }
}

/** 请求头：有令牌时返回 Bearer 头，无令牌返回空对象（可直接展开合并） */
export function authHeaders(): Record<string, string> {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

/**
 * WebSocket 专用：把令牌附加为查询参数（`?token=...`，已有 query 时用 `&`）。
 *
 * 浏览器 WebSocket 构造器没有 headers 选项，查询参数是唯一可行的通道；
 * 同源相对路径（`/ws/agent/xxx`）也能正确处理。
 */
export function withAuth(url: string): string {
  const token = getToken()
  if (!token) return url
  // 先切掉 #fragment：令牌必须落在 query 段里，拼到 hash 后面后端读不到
  const hashAt = url.indexOf('#')
  const base = hashAt === -1 ? url : url.slice(0, hashAt)
  const hash = hashAt === -1 ? '' : url.slice(hashAt)
  const sep = base.includes('?') ? '&' : '?'
  return `${base}${sep}token=${encodeURIComponent(token)}${hash}`
}
