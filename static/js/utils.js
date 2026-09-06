/* utils.js — 通用工具：DOM/转义/Markdown/防抖节流/Toast/全局状态 */

// 全局共享状态（跨模块只读，app.js 写入）
export const state = {
  sessionId: localStorage.getItem("agent_session") || "",
  selectedResume: localStorage.getItem("agent_resume") || "",
  running: false,
  toolCosts: {},
};

export const $ = (id) => document.getElementById(id);

export function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function now() {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

/** 极简 Markdown 渲染（标题/列表/加粗/链接/图片/代码），输出前已转义 */
export function md(text) {
  let s = esc(text);
  s = s.replace(/!\[([^\]]*)\]\(([^)]+)\)/g,
    (m, t, u) => `<img src="${u.startsWith("data/") ? "/files/" + u : u}" alt="${t}">`);
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/^### (.+)$/gm, "<h2>$1</h2>").replace(/^## (.+)$/gm, "<h2>$1</h2>");
  s = s.replace(/^\s*[-*] (.+)$/gm, "<li>$1</li>").replace(/^\s*\d+[.、] (.+)$/gm, "<li>$1</li>");
  s = s.replace(/(<li>[\s\S]*?<\/li>)(?!\s*<li>)/g, (m) => "<ul>" + m + "</ul>");
  s = s.split(/\n{2,}/)
    .map((p) => (/^<(h2|ul|img)/.test(p.trim()) ? p : p.replace(/\n/g, "<br>")))
    .join("<br>");
  return s;
}

/** 防抖：发送按钮等 */
export function debounce(fn, wait = 300) {
  let t = null;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), wait);
  };
}

/** 节流：滚动监听等 */
export function throttle(fn, wait = 100) {
  let last = 0;
  return (...args) => {
    const nowTs = Date.now();
    if (nowTs - last >= wait) {
      last = nowTs;
      fn(...args);
    }
  };
}

/** Toast 通知（3s 自动消失） */
export function toast(msg, type = "") {
  const box = document.getElementById("toasts");
  const d = document.createElement("div");
  d.className = `toast ${type}`;
  d.setAttribute("role", "status");
  d.textContent = msg;
  box.appendChild(d);
  setTimeout(() => d.remove(), 3000);
}

/** 滚动到消息底部（节流，平滑） */
export const scrollBottom = throttle(() => {
  const m = document.getElementById("messages");
  m.scrollTo({ top: m.scrollHeight, behavior: "smooth" });
}, 100);
