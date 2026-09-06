/* ui.js — 界面渲染：消息气泡 / 打字机 / 工作流面板 / 状态 / 快捷操作 / 横幅 */
import { $, esc, md, now, state, scrollBottom } from "./utils.js";
import { toast } from "./utils.js";

/* ---------- Agent 状态指示（空闲绿/运行蓝脉冲/异常红/等待琥珀） ---------- */
export function setStatus(kind, sub = "") {
  const dot = $("statusDot"), tx = $("statusText");
  if (!dot) return;
  dot.className = "dot " + (kind === "run" ? "run" : kind === "err" ? "err" : kind === "wait" ? "wait" : "");
  tx.textContent = { run: "运行中", idle: "空闲", err: "异常", wait: "等待输入" }[kind] || "空闲";
  $("statusSub").textContent = sub ? "· " + sub : "";
}

/** 执行阶段播报（思考中/调用工具/子任务完成…），作用于 typing 气泡 */
export function phase(text) {
  const el = document.querySelector("#typing .phaseTx");
  if (el) el.textContent = text;
}

/* ---------- 消息气泡 ---------- */
function statusIconText(role, opts) {
  if (opts && opts.status) return opts.status;
  return role === "user" ? "✓ 已发送" : "✓ 已完成";
}

export function addMsg(role, content, opts = {}) {
  const row = document.createElement("article");
  row.className = "row " + (role === "user" ? "me" : "ai");
  const av = `<div class="bAv" aria-hidden="true">${role === "user" ? "🧑" : "🧭"}</div>`;
  let bubbleHtml;
  if (role === "user") {
    const i = content.indexOf("【图片JD内容】");
    bubbleHtml = i >= 0
      ? esc(content.slice(0, i).trim()) + "<br>" +
        `<details class="imgjd"><summary>🖼 已识别截图中的JD（${content.slice(i).length}字，点开查看）</summary>` +
        `<pre>${esc(content.slice(i))}</pre></details>`
      : esc(content);
  } else {
    bubbleHtml = content; // 已是渲染后的 HTML
  }
  row.innerHTML = `${av}<div class="bWrap">
      <div class="bubble">${bubbleHtml}</div>
      <div class="bMeta"><span class="sic">${esc(statusIconText(role, opts))}</span><span>${now()}</span></div>
    </div>`;
  $("messages").appendChild(row);
  scrollBottom();
  return row;
}

/** 打字机气泡（运行中） */
export function showTyping() {
  setStatus("run");
  const row = document.createElement("article");
  row.className = "row ai";
  row.id = "typing";
  row.innerHTML = `<div class="bAv" aria-hidden="true">🧭</div><div class="bWrap">
      <div class="bubble"><span class="typingDots" aria-hidden="true"><i></i><i></i><i></i></span>
      <span class="phaseTx" style="margin-left:8px">思考中…</span></div>
      <div class="bMeta"><span class="sic" style="color:var(--primary)">● 执行中</span><span>${now()}</span></div>
    </div>`;
  $("messages").appendChild(row);
  scrollBottom();
}

export function dropTyping() {
  const t = $("typing");
  if (t) t.remove();
  setStatus("idle");
}

/** 流式打字机：把 markdown 文本渐进渲染进新建气泡 */
export function addMsgStreamed(content, opts = {}) {
  const row = addMsg("agent", "", opts);
  const bubble = row.querySelector(".bubble");
  if (content.length <= 140) {
    bubble.innerHTML = md(content);
    return;
  }
  const steps = 32;
  (function reveal(i) {
    bubble.innerHTML = md(content.slice(0, Math.ceil((content.length * i) / steps)));
    scrollBottom();
    if (i < steps) setTimeout(() => reveal(i + 1), 22);
  })(1);
}

/* ---------- 横幅 ---------- */
export function showBanner(id, text, autoHideMs = 0) {
  const el = $(id);
  el.classList.add("show");
  if (text !== undefined) el.childNodes.length && (el.querySelector(".banner-text") ? el.querySelector(".banner-text").textContent = text : null);
  if (autoHideMs) setTimeout(() => el.classList.remove("show"), autoHideMs);
}
export function hideBanner(id) { $(id).classList.remove("show"); }

/* ---------- 工作流面板 ---------- */
const taskStartTs = {};

function statusIcon(s) {
  return {
    pending: "⏳",
    running: '<span class="typingDots" aria-hidden="true"><i></i><i></i><i></i></span>',
    done: "✅", failed: "❌", waiting: "⏸", skipped: "⏭",
  }[s] || "⏳";
}

export function renderTasks(tasks, order) {
  const box = $("tasks");
  box.innerHTML = order.length ? "" : '<div class="tasksEmpty">尚无任务<br>发送目标后，Agent 的规划与执行将实时展示</div>';
  order.forEach((id, idx) => {
    const t = tasks[id];
    const el = document.createElement("article");
    el.className = "task";
    el.id = "card-" + id;
    el.dataset.status = t.status;
    el.innerHTML = `
      <div class="tHead" role="button" tabindex="0" aria-label="展开任务：${esc(t.title)}">
        <span class="tIcon">${statusIcon(t.status)}</span>
        <span class="tTitle">${idx + 1}. ${esc(t.title)}</span>
        ${badgeHtml(t.tool)}
        ${t.retries ? `<span class="tRetryB">🔁${t.retries}</span>` : ""}
      </div>
      <div class="tBody" id="body-${id}"><div><div class="tInner">
        ${t.detail ? `<div>${esc(t.detail)}</div>` : ""}
        <div id="steps-${id}"></div>
        ${t.result ? `<div class="tResult ${t.status === "failed" ? "fail" : ""}">${esc(t.result)}</div>` : ""}
      </div></div></div>`;
    box.appendChild(el);
  });
  updateProgress(tasks, order);
}

function badgeHtml(tool) {
  if (!tool || tool === "none") return '<span class="tBadge llm">💬 生成</span>';
  const c = state.toolCosts[tool] || "低";
  const cls = c === "高" ? " costHigh" : c === "中" ? " costMid" : "";
  return `<span class="tBadge${cls}" title="预估成本：${esc(c)}">🔧 ${esc(tool)}</span>`;
}

export function updateProgress(tasks, order) {
  const arr = order.map((id) => tasks[id]);
  const fin = arr.filter((t) => t.status === "done" || t.status === "failed").length;
  $("progressBar").style.width = (arr.length ? (fin / arr.length) * 100 : 0) + "%";
  $("progressText").textContent = `${fin} / ${arr.length}`;
}

export function setTaskStatus(id, status) {
  const el = $("card-" + id);
  if (!el) return;
  el.dataset.status = status;
  el.querySelector(".tIcon").innerHTML = statusIcon(status);
  if (status === "running") {
    taskStartTs[id] = Date.now();
    const body = $("body-" + id);
    if (body) body.classList.add("open");
    el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  if (status === "done" || status === "failed") {
    const start = taskStartTs[id];
    const secs = start ? ((Date.now() - start) / 1000).toFixed(1) + "s" : ""; // 无起点（如恢复的会话）不显示，避免NaN
    const head = el.querySelector(".tHead");
    let t = head.querySelector(".tTime");
    if (!secs) { if (t) t.remove(); return updateProgress(tasks, order_guard(id)); }
    if (!t) { t = document.createElement("span"); t.className = "tTime"; head.insertBefore(t, head.querySelector(".tBadge")); }
    t.textContent = secs;
  }
}
// 兼容旧调用签名的小工具：仅返回当前顺序数组
function order_guard(id) { return Object.keys(tasks); }

export function addStep(id, html, cls = "") {
  const box = $("steps-" + id);
  if (!box) return;
  const s = document.createElement("div");
  s.className = "step " + cls;
  s.innerHTML = html;
  box.appendChild(s);
}

export function markRetry(id) {
  const head = document.querySelector(`#card-${id} .tHead`);
  if (!head) return;
  let b = head.querySelector(".tRetryB");
  if (!b) { b = document.createElement("span"); b.className = "tRetryB"; head.appendChild(b); b.textContent = "🔁1"; return; }
  const n = parseInt(b.textContent.replace(/\D/g, "")) + 1;
  b.textContent = "🔁" + (Number.isFinite(n) ? n : 1);
}

/* ---------- 记忆面板 ---------- */
const MEM_LABEL = {
  name: "👤 姓名", target_role: "🎯 岗位", city: "🏙 城市", salary_range: "💰 薪资",
  education: "🎓 学历", experience_years: "💼 经验", work_mode: "🏢 方式",
  jd_paths: "📄 JD", resume_paths: "📋 简历", jd_text: "📝 JD文本", preferences: "⭐",
};

export function renderMemChips(facts) {
  if (!facts) return;
  const box = $("memChips");
  const empty = box.querySelector(".memEmpty");
  if (empty) empty.remove();
  const tsMap = facts._ts || {};
  Object.entries(facts)
    .filter(([k]) => !k.startsWith("_"))
    .forEach(([k, v]) => {
      const stale = tsMap[k] && Date.now() / 1000 - tsMap[k] > 30 * 86400;
      const val = Array.isArray(v) ? v.map((x) => String(x).split("/").pop()).join(", ") : String(v).slice(0, 18);
      const tag = document.createElement("span");
      tag.className = "memTag" + (stale ? " stale" : "");
      tag.title = stale ? "30天未更新，建议确认" : "";
      tag.innerHTML = `${MEM_LABEL[k] || "🧩"} <b>${esc(val)}</b>${stale ? " ⏳" : ""}`;
      box.prepend(tag);
      while (box.children.length > 10) box.lastElementChild.remove();
    });
}

export function renderStats(stats) {
  $("stLlm").textContent = stats.llm_calls || 0;
  $("stTool").textContent = stats.tool_calls || 0;
  $("stRetry").textContent = stats.tool_retries || 0;
  const notes = [];
  if (stats.high_cost_calls) notes.push(`高成本调用 ${stats.high_cost_calls} 次`);
  if (stats.elapsed_s) notes.push(`耗时 ${stats.elapsed_s}s`);
  if (stats.cache_disk_items) notes.push(`缓存 ${stats.cache_disk_items} 项`);
  if (notes.length) $("progressNote").textContent = "📊 " + notes.join(" · ");
  if (stats.llm_degraded) {
    const chip = $("chipProvider");
    chip.textContent = "⚠️ 已降级到备用模型";
    chip.className = "chip warn";
    toast("主模型不可用，已自动降级到备用模型", "warn");
    fetch("/api/models").then((r) => r.json()).then((d) => {
      const sel = $("modelSelect");
      if (sel && d.current) { sel.value = d.current; }
    }).catch(() => {});
  }
}

/* ---------- 快捷操作 Chips（报告后浮现） ---------- */
export function renderQuickActions(content, onPick) {
  const acts = [];
  if (/匹配分|面试问题清单|面试题/.test(content)) {
    acts.push(["📋 优化我的简历", "根据刚才的匹配结果，逐条给出我的简历修改建议，展示优化后的版本"]);
    acts.push(["🎯 出10道面试题", "针对这个岗位出10道高频面试题，并说明考察点"]);
    acts.push(["💰 薪资谈判建议", "给我这个岗位的市场薪资范围、谈判话术和策略"]);
  }
  if (/服务繁忙|失败/.test(content)) acts.push(["🔄 重试刚才的任务", "继续刚才未完成的任务"]);
  acts.push(["🗂 查看我的数据", "查看我的数据"]);
  const row = document.createElement("div");
  row.className = "qacts";
  row.setAttribute("role", "group");
  row.setAttribute("aria-label", "快捷操作");
  acts.forEach(([label, cmd]) => {
    const b = document.createElement("button");
    b.className = "qbtn";
    b.textContent = label;
    b.onclick = () => onPick(cmd);
    row.appendChild(b);
  });
  $("messages").appendChild(row);
  scrollBottom();
}

/** 图表容器：注入图片，失败显示骨架屏+重试 */
export function renderChart(containerId, src) {
  const box = $(containerId);
  if (!box || !src) return;
  box.innerHTML = '<div class="chart-skeleton">图表加载中…</div>';
  const img = new Image();
  img.alt = "匹配度图表";
  img.onload = () => { box.innerHTML = ""; box.appendChild(img); };
  img.onerror = () => {
    box.innerHTML = '<div class="chart-skeleton">⚠️ 图表加载失败</div>';
    const retry = document.createElement("button");
    retry.className = "link-btn";
    retry.textContent = "重试";
    retry.onclick = () => renderChart(containerId, src);
    box.appendChild(retry);
  };
  img.src = src;
}
