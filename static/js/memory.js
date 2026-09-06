/* memory.js — Agent 记忆面板 + 简历库（上传/选择/删除）+ 本地清除 */
import { $, esc, state, toast } from "./utils.js";
import { renderMemChips, addMsg } from "./ui.js";

/* ---------- 记忆 ---------- */

/** 「清除记忆」按钮：仅清理本地 localStorage 并重置界面，不调用 LLM/后端 */
export function clearMemoryLocal({ resetWelcome }) {
  localStorage.removeItem("agent_session");
  localStorage.removeItem("agent_resume");
  state.sessionId = "";
  state.selectedResume = "";
  $("sessionChip").textContent = "会话 -";
  $("messages").innerHTML = "";
  $("tasks").innerHTML = "";
  $("memChips").innerHTML = '<div class="memEmpty">暂无记忆条目</div>';
  $("progressBar").style.width = "0";
  $("progressText").textContent = "0 / 0";
  $("goalText").className = "goal empty";
  $("goalText").textContent = "输入求职目标后，Agent 的规划与执行将实时展示在这里";
  updateResumeChip();
  resetWelcome();
  toast("本地记忆已清除，已开启全新会话", "success");
}

/* ---------- 简历库 ---------- */

export function updateResumeChip() {
  const chip = $("chipResume"); // 顶栏简历入口已移入侧栏 Tab，元素可能不存在
  if (!chip) return;
  chip.textContent = selectedResumeLabel();
  chip.title = state.selectedResume
    ? `当前使用: ${state.selectedResume}`
    : "未选择简历（Agent 自动使用最新上传的一份）";
}

function selectedResumeLabel() {
  return state.selectedResume ? `📄 ${state.selectedResume.slice(0, 12)}` : "📄 简历库";
}

export async function uploadResumeFile(file) {
  const fd = new FormData();
  fd.append("file", file);
  const r = await (await fetch("/api/resumes/upload", { method: "POST", body: fd })).json();
  if (r.ok) {
    state.selectedResume = r.name;
    localStorage.setItem("agent_resume", r.name);
    updateResumeChip();
    toast(`简历「${r.name}」已入库`, "success");
  } else {
    toast(r.error || "上传失败", "err");
  }
  return r;
}

export async function loadResumes(selectName) {
  const list = $("resumeList");
  const data = await (await fetch("/api/resumes")).json();
  const items = data.resumes || [];
  if (selectName) state.selectedResume = selectName;
  if (state.selectedResume && !items.some((i) => i.name === state.selectedResume)) state.selectedResume = "";
  if (!state.selectedResume && items.length) {
    state.selectedResume = items[0].name;
    localStorage.setItem("agent_resume", state.selectedResume);
  }
  updateResumeChip();
  if (!items.length) {
    list.innerHTML = '<div class="rEmpty">简历库还是空的，拖入或点击上传</div>';
    return;
  }
  list.innerHTML = "";
  for (const i of items) {
    const d = document.createElement("div");
    d.className = "rItem" + (i.name === state.selectedResume ? " active" : "");
    const kb = (i.size / 1024).toFixed(0);
    d.innerHTML = `
      <span class="rName">${esc(i.name)}<br><span class="rMeta">${kb}KB · ${new Date(i.mtime * 1000).toLocaleString()}</span></span>
      <button class="rUse" aria-label="使用这份简历">${i.name === state.selectedResume ? "✓ 使用中" : "使用"}</button>
      <button class="rDel" aria-label="删除这份简历">🗑</button>`;
    // 事件委托：统一绑在卡片容器
    d.querySelector(".rUse").onclick = () => {
      state.selectedResume = i.name;
      localStorage.setItem("agent_resume", i.name);
      loadResumes();
    };
    d.querySelector(".rDel").onclick = async () => {
      if (!confirm(`确定删除简历「${i.name}」？`)) return;
      await fetch("/api/resumes/" + encodeURIComponent(i.name), { method: "DELETE" });
      if (state.selectedResume === i.name) {
        state.selectedResume = "";
        localStorage.removeItem("agent_resume");
      }
      loadResumes();
      toast("简历已删除", "success");
    };
    list.appendChild(d);
  }
}

export function initResumePanel() {
  // 简历库常驻左侧抽屉（Tab 切换由 app.js 的 initSidebar 负责）
  $("dropZone").onclick = () => $("resumePick").click();
  $("dropZone").addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") $("resumePick").click();
  });
  $("resumePick").addEventListener("change", async (e) => {
    for (const f of e.target.files) await uploadResumeFile(f);
    loadResumes();
    e.target.value = "";
  });
  ["dragover", "dragenter"].forEach((ev) =>
    $("dropZone").addEventListener(ev, (e) => {
      e.preventDefault(); e.stopPropagation();
      $("dropZone").classList.add("over");
    })
  );
  $("dropZone").addEventListener("dragleave", () => $("dropZone").classList.remove("over"));
  $("dropZone").addEventListener("drop", async (e) => {
    e.preventDefault(); e.stopPropagation();
    $("dropZone").classList.remove("over");
    for (const f of [...e.dataTransfer.files]) await uploadResumeFile(f);
    loadResumes();
  });
}
