/* app.js — 应用入口：模块装配 / SSE事件分发（rAF批处理）/ 会话恢复 / 响应式面板 */
import { $, esc, md, state, toast, debounce } from "./utils.js";
import { chatStream, fetchSession, fetchHealth } from "./sse.js";
import {
  setStatus, phase, addMsg, showTyping, dropTyping, addMsgStreamed,
  renderTasks, updateProgress, setTaskStatus, addStep, markRetry,
  renderMemChips, renderStats, renderQuickActions,
} from "./ui.js";
import { initDragdrop } from "./dragdrop.js";
import { clearMemoryLocal, initResumePanel, loadResumes, updateResumeChip } from "./memory.js";

let tasks = {};
let taskOrder = [];

/* ================= SSE 事件分发 ================= */

function handleEvent(evt) {
  switch (evt.type) {
    case "session_info":
      state.sessionId = evt.session_id;
      localStorage.setItem("agent_session", state.sessionId);
      $("chipSession").textContent = "会话 " + state.sessionId.slice(0, 8);
      break;

    case "user_message":
      addMsg("user", evt.content);
      break;

    case "facts_updated":
      renderMemChips(evt.facts);
      phase("更新记忆…");
      break;

    case "plan_created": {
      $("goalText").className = "goal";
      $("goalText").textContent = "🎯 " + evt.goal;
      $("flowNote").textContent = evt.replanned ? "♻️ 已重新规划" : "";
      if (evt.costs) state.toolCosts = evt.costs;
      tasks = {};
      taskOrder = [];
      (evt.tasks || []).forEach((t) => {
        tasks[t.id] = { title: t.title, detail: t.detail, tool: t.tool, status: t.status, retries: 0, result: "" };
        taskOrder.push(t.id);
      });
      renderTasks(tasks, taskOrder);
      phase("规划完成，执行中…");
      addMsg("agent", md(`**已拆解为 ${evt.tasks.length} 个子任务**，开始执行 👇 中间面板可查看实时进度`));
      break;
    }

    case "plan_fallback":
      $("flowNote").textContent = "⚠️ 使用兜底规划";
      break;

    case "task_start":
      setTaskStatus(evt.task_id, "running");
      phase(`执行：${evt.title}`);
      break;

    case "thought":
      addStep(evt.task_id, `<div class="sh">💭 ${esc(evt.thought)}</div>`);
      phase("思考中…");
      break;

    case "tool_call": {
      const argsStr = JSON.stringify(evt.args || {});
      addStep(evt.task_id, `<div class="st">🔧 ${esc(evt.tool)}(${esc(argsStr.slice(0, 140))})</div>`);
      phase(`调用 ${evt.tool}…${evt.cost ? `（成本:${evt.cost}）` : ""}`);
      break;
    }

    case "tool_result":
      addStep(evt.task_id, `<div class="sr">↳ ${esc(evt.brief)}</div>`, "ok");
      phase("处理工具结果…");
      break;

    case "tool_error":
      addStep(evt.task_id, `<div class="sr">↳ ${esc(evt.error)}</div>`, "fail");
      setStatus("err", "工具异常，自动重试中");
      break;

    case "retry":
      markRetry(evt.task_id);
      addStep(evt.task_id, `<div class="sr">🔁 第${evt.attempt}次重试: ${esc(evt.error)}</div>`, "rt");
      setStatus("run", "自动重试中");
      break;

    case "task_finish": {
      setTaskStatus(evt.task_id, evt.status);
      const t = tasks[evt.task_id];
      if (!t) break;
      t.result = evt.status === "done" ? evt.result_brief || "" : evt.error || "";
      const inner = document.querySelector(`#card-${evt.task_id} .tInner`);
      if (inner) {
        const r = document.createElement("div");
        r.className = "tResult" + (evt.status === "failed" ? " fail" : "");
        r.textContent = (evt.status === "done" ? "✔ " : "✘ ") + t.result;
        inner.appendChild(r);
      }
      if (evt.status === "failed") setStatus("err", "子任务失败，降级继续");
      else phase("子任务完成");
      break;
    }

    case "security_block":
      // 敏感信息拦截：红色提示条 + 自动消失
      showSecurityBanner(evt.message || "检测到敏感信息已拦截");
      break;

    case "ask_user": {
      setTaskStatus(evt.task_id, "waiting");
      $("askBanner").classList.add("show");
      addMsg("agent", md(`**需要你补充信息：**\n\n${evt.question}`), { status: "⏸ 等待输入" });
      setStatus("wait", "等待你补充材料");
      $("input").focus();
      break;
    }

    case "resumed":
      $("askBanner").classList.remove("show");
      setStatus("run");
      break;

    case "final_answer": {
      dropTyping();
      const content = evt.content || "";
      addMsgStreamed(content, { status: "✓ 已完成" });
      if (evt.stats) {
        renderStats(evt.stats);
        if ((evt.stats.failed_tasks || []).length)
          addMsg("agent", md(`⚠️ 以下子任务失败但流程已降级完成：${evt.stats.failed_tasks.join("、")}`), { status: "⚠ 降级完成" });
        renderQuickActions(content, pickQuickAction);
      }
      setStatus("idle", "任务完成");
      break;
    }

    case "error":
      dropTyping();
      addMsg("agent", `<span class="errTx">⚠️ ${esc(evt.message)}</span>`, { status: "✗ 异常" });
      setStatus("err", evt.message.slice(0, 22));
      break;

    case "session_snapshot":
      break; // 状态已由事件流覆盖
  }
}

/** 安全敏感拦截提示条（6s 自动消失） */
function showSecurityBanner(msg) {
  const b = $("securityBanner");
  b.querySelector(".banner-text").textContent = "⚠️ " + msg + "，请勿发送身份证号/银行卡等隐私内容";
  b.classList.add("show");
  setTimeout(() => b.classList.remove("show"), 6000);
}

/* ================= rAF 批处理：避免逐事件重排卡顿 ================= */
const evtQueue = [];
let rafScheduled = false;

function enqueue(evt) {
  evtQueue.push(evt);
  if (!rafScheduled) {
    rafScheduled = true;
    requestAnimationFrame(flushEvents);
  }
}

function flushEvents() {
  const batch = evtQueue.splice(0, 10);
  for (const evt of batch) {
    try { handleEvent(evt); } catch (e) { console.warn("[evt]", e); }
  }
  if (evtQueue.length) requestAnimationFrame(flushEvents);
  else rafScheduled = false;
}

/* ================= 发送 ================= */

async function doSend() {
  const text = $("input").value.trim();
  if (!text || state.running) return;
  $("input").value = "";
  autoHeight();
  $("askBanner").classList.remove("show");
  $("offlineBar").classList.remove("show");
  state.running = true;
  $("send").disabled = true;
  // 用户气泡由服务端 user_message 事件回传渲染（本地不再重复画，避免双条）
  showTyping();
  try {
    await chatStream(
      { session_id: state.sessionId, message: text, resume_path: state.selectedResume },
      enqueue
    );
  } catch (e) {
    // 降级策略：断开时显示重连入口与离线提示，保留上下文
    $("offlineBar").classList.add("show");
    addMsg("agent", `<span class="errTx">连接中断：${esc(e.message)}</span>`, { status: "✗ 连接中断" });
    setStatus("err", "连接中断");
  }
  state.running = false;
  $("send").disabled = false;
  dropTyping();
}

/** 快捷操作点击 → 填入指令并触发发送 */
function pickQuickAction(cmd) {
  $("input").value = cmd;
  autoHeight();
  debouncedSend();
}
const debouncedSend = debounce(doSend, 300);

/* ================= 输入框 ================= */

function autoHeight() {
  const el = $("input");
  el.style.height = "auto";                       // 先回到内容高度基准
  el.style.height = Math.min(el.scrollHeight, 150) + "px";
}

function initInput() {
  const sendDebounced = () => debouncedSend();
  $("send").addEventListener("click", sendDebounced);
  $("input").addEventListener("input", autoHeight);
  $("input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendDebounced();
    }
  });
  // 滚动节流监听（预留：仅贴近底部时跟随滚动）
  $("messages").addEventListener(
    "scroll",
    (() => {
      let last = 0;
      return () => {
        const nowTs = Date.now();
        if (nowTs - last < 100) return;
        last = nowTs;
        const m = $("messages");
        const nearBottom = m.scrollHeight - m.scrollTop - m.clientHeight < 120;
        m.dataset.follow = nearBottom ? "1" : "";
      };
    })()
  );
}

/* ================= 左侧抽屉：Tab 切换 / 折叠 / 移动端 overlay ================= */

function activateTab(name) {
  document.querySelectorAll(".sb-tab[data-tab]").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === name)
  );
  document.querySelectorAll(".sb-pane").forEach((p) =>
    p.classList.toggle("active", p.id === "tab-" + name)
  );
}

function closeMobileSidebar() {
  $("sidebar").classList.remove("open");
  $("drawerMask").classList.remove("show");
}

function initSidebar() {
  const sb = $("sidebar");
  const mask = $("drawerMask");
  // 折叠状态持久化（仅桌面有意义）
  if (localStorage.getItem("agent_sidebar") === "collapsed") sb.classList.add("collapsed");

  $("burger").onclick = () => {
    if (window.matchMedia("(max-width: 900px)").matches) {
      sb.classList.add("open");                       // 移动端：overlay 抽屉
      mask.classList.add("show");
      return;
    }
    const collapsed = sb.classList.toggle("collapsed"); // 桌面：图标条 ↔ 完整面板
    localStorage.setItem("agent_sidebar", collapsed ? "collapsed" : "open");
  };

  document.querySelectorAll(".sb-tab[data-tab]").forEach((b) => {
    b.addEventListener("click", () => {
      sb.classList.remove("collapsed");
      localStorage.setItem("agent_sidebar", "open");
      closeMobileSidebar();
      activateTab(b.dataset.tab);
    });
  });
  $("sbExpand").onclick = () => {
    sb.classList.remove("collapsed");
    localStorage.setItem("agent_sidebar", "open");
  };
  mask.addEventListener("click", closeMobileSidebar);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMobileSidebar(); });
}

/* ================= 模型切换（多模型下拉） ================= */

function renderModelSelect(models, current) {
  const sel = $("modelSelect");
  sel.innerHTML = "";
  (models.length ? models : [current]).forEach((m) => {
    const o = document.createElement("option");
    o.value = m;
    o.textContent = m;
    sel.appendChild(o);
  });
  sel.value = current;
  if (sel.value !== current) {  // 当前模型不在列表（如降级模型），追加显示
    const o = document.createElement("option");
    o.value = o.textContent = current;
    sel.appendChild(o);
    sel.value = current;
  }
}

async function switchModel(model) {
  try {
    const r = await (await fetch("/api/model/select", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model }),
    })).json();
    if (!r.ok) throw new Error(r.error || "切换失败");
    const chip = $("chipProvider");
    chip.textContent = `⚡ ${r.current}`;
    chip.className = "chip on";
    toast(`已切换模型：${r.current}（对新消息生效）`, "success");
  } catch (e) {
    toast("切换失败: " + e.message, "err");
    // 回退下拉选中项
    $("modelSelect").value = state.currentModel || "";
  }
  state.currentModel = $("modelSelect").value;
}

async function initModelSelect() {
  try {
    const r = await (await fetch("/api/models")).json();
    state.currentModel = r.current;
    renderModelSelect(r.models || [], r.current);
    $("modelSelect").addEventListener("change", (e) => switchModel(e.target.value));
  } catch { /* 服务未连接时忽略 */ }
}

/* ================= 清空聊天记录（本地重置 + 删除服务端存档） ================= */

function initClearChat() {
  $("btnClearChat").addEventListener("click", async () => {
    if (!confirm("清空聊天记录？当前会话的消息与服务端存档将一并删除（简历库与记忆保留）")) return;
    if (state.sessionId) {
      await fetch("/api/session/" + state.sessionId, { method: "DELETE" }).catch(() => {});
    }
    localStorage.removeItem("agent_session");
    state.sessionId = "";
    tasks = {}; taskOrder = [];
    $("messages").innerHTML = "";
    $("tasks").innerHTML = "";
    $("chipSession").textContent = "会话 -";
    $("goalText").className = "goal empty";
    $("goalText").textContent = "输入求职目标后，Agent 的规划与执行将实时展示在这里";
    $("progressBar").style.width = "0";
    $("progressText").textContent = "0 / 0";
    $("progressNote").textContent = "";
    $("flowNote").textContent = "";
    showWelcome();
    toast("聊天记录已清空，已开启全新会话", "success");
  });
}

/* ================= 清除记忆（本地，不调LLM） ================= */

function initClearMemory() {
  $("btnClearMem").addEventListener("click", () => {
    if (!confirm("清除本地记忆？将开启全新会话（后端历史文件保留）")) return;
    clearMemoryLocal({ resetWelcome: () => showWelcome() });
  });
}

function showWelcome() {
  addMsg("agent", "你好，我是<b>求职智囊</b>。请告诉我你的姓名和目标职位，我马上开始为你服务。", { status: "● 在线" });
}

/* ================= 重连（SSE 断开降级） ================= */

function initReconnect() {
  $("btnReconnect").addEventListener("click", async () => {
    $("offlineBar").classList.remove("show");
    toast("正在重新同步会话…");
    if (state.sessionId) {
      const s = await fetchSession(state.sessionId);
      if (s) {
        $("messages").innerHTML = "";
        (s.messages || []).slice(-14).forEach((m) =>
          addMsg(m.role === "user" ? "user" : "agent", m.role === "user" ? m.content : md(m.content))
        );
        if (s.tasks && s.tasks.length) {
          tasks = {}; taskOrder = [];
          s.tasks.forEach((t) => {
            tasks[t.id] = { title: t.title, detail: t.detail, tool: t.tool, status: t.status, retries: t.retries, result: t.result || t.error };
            taskOrder.push(t.id);
          });
          renderTasks(tasks, taskOrder);
        }
        renderMemChips(s.facts || {});
        toast("会话已恢复", "success");
        return;
      }
    }
    location.reload();
  });
}

/* ================= 初始化 ================= */

(async function init() {
  updateResumeChip();
  setStatus("idle", "就绪");
  initInput();
  initSidebar();
  initResumePanel();
  initClearMemory();
  initClearChat();
  initReconnect();
  await initModelSelect();

  // 支持 ?session=会话ID 直达指定会话（也便于恢复/分享）
  const urlSession = new URLSearchParams(location.search).get("session");
  if (urlSession) {
    state.sessionId = urlSession;
    localStorage.setItem("agent_session", urlSession);
  }

  const h = await fetchHealth();
  if (h) {
    state.toolCosts = h.tool_costs || {};
    const chip = $("chipProvider");
    if (h.provider === "mock") {
      chip.textContent = "⚠️ mock 模式";
      chip.className = "chip warn";
    } else {
      chip.textContent = `⚡ ${h.provider}`;
      chip.className = "chip on";
    }
    if (h.encrypted_storage === false) {
      const c = document.createElement("span");
      c.className = "chip err";
      c.textContent = "⚠️ 存储未加密";
      document.querySelector(".top-chips").prepend(c);
    }
  } else {
    $("chipProvider").textContent = "服务未连接";
    setStatus("err", "服务未连接");
  }

  await loadResumes();

  // 会话恢复
  let restored = 0;
  if (state.sessionId) {
    const s = await fetchSession(state.sessionId);
    if (s) {
      $("chipSession").textContent = "会话 " + state.sessionId.slice(0, 8);
      (s.messages || []).slice(-14).forEach((m) => {
        restored++;
        addMsg(m.role === "user" ? "user" : "agent", m.role === "user" ? m.content : md(m.content));
      });
      if (s.tasks && s.tasks.length) {
        $("goalText").className = "goal";
        $("goalText").textContent = "🎯 " + (s.title || "历史任务");
        tasks = {};
        taskOrder = [];
        s.tasks.forEach((t) => {
          tasks[t.id] = { title: t.title, detail: t.detail, tool: t.tool, status: t.status, retries: t.retries, result: t.result || t.error };
          taskOrder.push(t.id);
        });
        renderTasks(tasks, taskOrder);
      }
      renderMemChips(s.facts || {});
      if (s.status === "awaiting_input") {
        $("askBanner").classList.add("show");
        setStatus("wait", "等待你补充材料");
      }
    }
  }
  if (!restored) showWelcome();

  // 调试/自动化入口（控制台可用：__agent.send() 等）
  window.__agent = {
    send: () => debouncedSend(),
    activateTab,
    clearChat: () => $("btnClearChat").click(),
    setInput: (t) => { $("input").value = t; autoHeight(); },
  };

  // 拖拽/粘贴依赖注入（send/上传回调来自本模块与 memory 模块）
  initDragdrop({
    isRunning: () => state.running,
    autoHeight,
    send: () => debouncedSend(),
    uploadResumeFile: async (f) => {
      const { uploadResumeFile } = await import("./memory.js");
      const r = await uploadResumeFile(f);
      if (r.ok) {
        addMsg("agent", md(`📄 简历 **${r.name}** 已入库并设为使用中`), { status: "✓ 已入库" });
        loadResumes(r.name);
      }
    },
  });
})();
