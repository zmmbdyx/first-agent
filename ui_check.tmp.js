
const $ = id => document.getElementById(id);
let sessionId = localStorage.getItem("agent_session") || "";
let selectedResume = localStorage.getItem("agent_resume") || "";
let running = false;
let tasks = {}, taskOrder = [];
let toolLogCount = 0;

// ---------- 状态指示 ----------
function setStatus(kind, sub) {
  const dot = $("statusDot"), tx = $("statusText");
  dot.className = "dot " + (kind === "run" ? "run" : kind === "err" ? "err" : kind === "wait" ? "wait" : "");
  tx.textContent = { run: "运行中", idle: "空闲", err: "异常", wait: "等待输入" }[kind] || "空闲";
  $("statusSub").textContent = sub ? "· " + sub : "";
}
function phase(phaseName, detail) {
  const el = document.querySelector("#typing .phaseTx");
  if (el) el.textContent = detail || phaseName;
}

// ---------- markdown ----------
function md(text) {
  let s = text.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  s = s.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (m,t,u) => `<img src="${u.startsWith("data/") ? "/files/" + u : u}" alt="${t}">`);
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/^### (.+)$/gm, "<h2>$1</h2>").replace(/^## (.+)$/gm, "<h2>$1</h2>");
  s = s.replace(/^\s*[-*] (.+)$/gm, "<li>$1</li>").replace(/^\s*\d+[.、] (.+)$/gm, "<li>$1</li>");
  s = s.replace(/(<li>[\s\S]*?<\/li>)(?!\s*<li>)/g, m => "<ul>" + m + "</ul>");
  s = s.split(/\n{2,}/).map(p => /^<(h2|ul|img)/.test(p.trim()) ? p : p.replace(/\n/g,"<br>")).join("<br>");
  return s;
}
function esc(s){ return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;"); }
function now() { const d = new Date(); return String(d.getHours()).padStart(2,"0") + ":" + String(d.getMinutes()).padStart(2,"0"); }

// ---------- 消息气泡 ----------
function addMsg(role, content, opts = {}) {
  const row = document.createElement("div");
  row.className = "row " + (role === "user" ? "me" : "ai");
  const av = `<div class="bAv">${role === "user" ? "🧑" : "🤖"}</div>`;
  let bubbleHtml;
  if (role === "user") {
    const i = content.indexOf("【图片JD内容】");
    bubbleHtml = i >= 0
      ? (esc(content.slice(0, i).trim()) + "<br>" +
         `<details class="imgjd"><summary>🖼 已识别截图中的JD（${content.slice(i).length}字，点开查看）</summary><pre>${esc(content.slice(i))}</pre></details>`)
      : esc(content);
  } else bubbleHtml = content;
  const sic = opts.status || (role === "user" ? "✓ 已发送" : "✓ 已完成");
  row.innerHTML = `${av}<div class="bWrap"><div class="bubble">${bubbleHtml}</div>
    <div class="bMeta"><span class="sic">${sic}</span><span>${now()}</span></div></div>`;
  $("messages").appendChild(row);
  $("messages").scrollTop = 1e9;
  return row;
}
function showTyping() {
  setStatus("run");
  const row = document.createElement("div");
  row.className = "row ai"; row.id = "typing";
  row.innerHTML = `<div class="bAv">🤖</div><div class="bWrap"><div class="bubble">
    <span class="typingDots"><i></i><i></i><i></i></span>
    <span class="phaseTx" style="margin-left:8px">思考中…</span></div>
    <div class="bMeta"><span class="sic" style="color:var(--run)">● 执行中</span><span>${now()}</span></div></div>`;
  $("messages").appendChild(row);
  $("messages").scrollTop = 1e9;
}
function dropTyping() { const t = $("typing"); if (t) t.remove(); setStatus("idle"); }

// ---------- 任务卡片（侧栏） ----------
function statusIcon(s) {
  return {pending:"⏳", running:'<span class="typingDots"><i></i><i></i><i></i></span>', done:"✅", failed:"❌", waiting:"⏸"}[s] || "⏳";
}
function renderTaskList() {
  const box = $("tasks");
  box.innerHTML = taskOrder.length ? "" : '<div class="tasksEmpty">尚无任务</div>';
  taskOrder.forEach(id => {
    const t = tasks[id];
    const el = document.createElement("div");
    el.className = "task"; el.id = "card-" + id; el.dataset.status = t.status;
    el.innerHTML = `<div class="tHead" onclick="toggleTask('${id}')">
        <span class="tIcon">${statusIcon(t.status)}</span>
        <span class="tTitle">${taskOrder.indexOf(id)+1}. ${esc(t.title)}</span>
        ${t.tool && t.tool!=="none" ? `<span class="tBadge">🔧 ${esc(t.tool)}</span>` : `<span class="tBadge llm">💬 生成</span>`}
        ${t.retries ? `<span class="tRetryB">🔁${t.retries}</span>` : ""}
      </div>
      <div class="tBody" id="body-${id}"><div><div class="tInner">
        ${t.detail ? `<div style="margin-bottom:4px">${esc(t.detail)}</div>` : ""}
        <div id="steps-${id}"></div>
        ${t.result ? `<div class="tResult ${t.status==="failed"?"fail":""}">${esc(t.result)}</div>` : ""}
      </div></div></div>`;
    box.appendChild(el);
  });
  updateProgress();
}
function updateProgress() {
  const arr = taskOrder.map(id => tasks[id]);
  const fin = arr.filter(t => t.status==="done" || t.status==="failed").length;
  $("progressBar").style.width = (arr.length ? fin/arr.length*100 : 0) + "%";
  $("progressText").textContent = `${fin} / ${arr.length}`;
}
function toggleTask(id) { $("body-"+id).classList.toggle("open"); }
function setTaskStatus(id, status) {
  if (!tasks[id]) return;
  tasks[id].status = status;
  const el = $("card-"+id); if (!el) return;
  el.dataset.status = status;
  el.querySelector(".tIcon").innerHTML = statusIcon(status);
  if (status === "running") { el.querySelector(".tBody").classList.add("open"); el.scrollIntoView({behavior:"smooth", block:"nearest"}); }
  updateProgress();
}
function addStep(id, html, cls) {
  const box = $("steps-"+id); if (!box) return;
  const s = document.createElement("div");
  s.className = "step " + (cls||"");
  s.innerHTML = html;
  box.appendChild(s);
  const body = $("body-"+id);
  if (body && body.classList.contains("open")) box.scrollTop = 1e9;
}
function markRetry(id) {
  const t = tasks[id]; if (!t) return;
  t.retries = (t.retries||0) + 1;
  const el = $("card-"+id);
  if (el && !el.querySelector(".tRetryB")) {
    const b = document.createElement("span"); b.className = "tRetryB"; b.textContent = "🔁1";
    el.querySelector(".tHead").appendChild(b);
  } else if (el) el.querySelector(".tRetryB").textContent = "🔁"+t.retries;
}
// ---------- 工具调用记录（侧栏时间线） ----------
function pushToolLog(tool, brief, fail, expandable) {
  const box = $("toolLog");
  const empty = box.querySelector(".tasksEmpty"); if (empty) empty.remove();
  const d = document.createElement("div");
  d.className = "tl"; 
  const full = expandable || brief || "";
  d.innerHTML = `<div class="tlHead ${fail?"fail":""}" onclick="this.nextElementSibling.classList.toggle('open')">
      <span class="ico">${fail ? "❌" : "🔧"}</span><span class="nm">${esc(tool)}</span>
      <span class="br">${esc(brief || "")}</span><span class="tm">${now()}</span></div>
    <div class="tlBody"><div><div class="tlInner">${esc(full)}</div></div></div>`;
  box.prepend(d);
  if (++toolLogCount > 40) { box.lastElementChild.remove(); toolLogCount = 40; }
}

// ---------- 记忆面板 ----------
function addMemChips(facts) {
  if (!facts) return;
  const box = $("memChips");
  const empty = box.querySelector(".rEmpty"); if (empty) empty.remove();
  const label = {target_role:"🎯", city:"🏙", jd_paths:"📄JD", resume_paths:"📋简历", jd_text:"📝JD文本", experience_years:"💼", preferences:"⭐"};
  Object.entries(facts).filter(([k]) => !k.startsWith("_")).forEach(([k, v]) => {
    const val = Array.isArray(v) ? v.map(x => String(x).split("/").pop()).join(",") : String(v).slice(0, 16);
    const tag = document.createElement("span");
    tag.className = "memTag";
    tag.innerHTML = `${label[k] || "🧩"} <b>${esc((label[k] ? "" : k + " ") + val)}</b>`;
    box.prepend(tag);
    while (box.children.length > 8) box.lastElementChild.remove();
  });
}

// ---------- SSE 事件 ----------
async function handleEvent(evt) {
  const type = evt.type;
  if (type === "session_info") {
    sessionId = evt.session_id; localStorage.setItem("agent_session", sessionId);
    $("sessionChip").textContent = "会话 " + sessionId.slice(0, 8);
  }
  else if (type === "user_message") addMsg("user", evt.content);
  else if (type === "facts_updated") { addMemChips(evt.facts); phase("更新记忆…"); }
  else if (type === "plan_created") {
    $("goalText").className = "goal";
    $("goalText").textContent = "🎯 " + evt.goal;
    $("flowNote").textContent = evt.replanned ? "♻️ 已重新规划" : "";
    tasks = {}; taskOrder = [];
    (evt.tasks||[]).forEach(t => { tasks[t.id] = {title:t.title, detail:t.detail, tool:t.tool, status:t.status, retries:0, result:""}; taskOrder.push(t.id); });
    renderTaskList();
    phase("规划完成，执行中…");
    addMsg("agent", md(`**已拆解为 ${evt.tasks.length} 个子任务**，开始执行 👇 左侧可查看实时进度`));
  }
  else if (type === "plan_fallback") { $("flowNote").textContent = "⚠️ 兜底规划"; }
  else if (type === "task_start") { setTaskStatus(evt.task_id, "running"); phase(`执行：${evt.title}`); }
  else if (type === "thought") { addStep(evt.task_id, `<div class="sh">💭 ${esc(evt.thought)}</div>`); phase("思考中…"); }
  else if (type === "tool_call") {
    const argsStr = JSON.stringify(evt.args || {});
    addStep(evt.task_id, `<div class="st">🔧 ${esc(evt.tool)}(${esc(argsStr.slice(0,140))})</div>`);
    pushToolLog(evt.tool, argsStr.slice(0, 40), false, `${evt.tool}(${argsStr})`);
    phase(`调用 ${evt.tool}…`);
  }
  else if (type === "tool_result") {
    addStep(evt.task_id, `<div class="sr">↳ ${esc(evt.brief)}</div>`, "ok");
    pushToolLog("result", evt.brief.slice(0, 44), false, evt.brief);
  }
  else if (type === "tool_error") {
    addStep(evt.task_id, `<div class="sr">↳ ${esc(evt.error)}</div>`, "fail");
    pushToolLog("error", evt.error.slice(0, 44), true, evt.error);
    setStatus("err", "工具异常，自动重试中");
  }
  else if (type === "retry") {
    markRetry(evt.task_id);
    addStep(evt.task_id, `<div class="sr">🔁 第${evt.attempt}次重试: ${esc(evt.error)}</div>`, "rt");
    pushToolLog("retry", evt.tool, true, `第${evt.attempt}次重试: ${evt.error}`);
    setStatus("run", `${evt.tool} 重试中`);
  }
  else if (type === "task_finish") {
    setTaskStatus(evt.task_id, evt.status);
    const t = tasks[evt.task_id]; if (!t) return;
    t.result = evt.status === "done" ? (evt.result_brief || "") : (evt.error || "");
    const body = $("body-"+evt.task_id);
    if (body) {
      const r = document.createElement("div");
      r.className = "tResult" + (evt.status==="failed" ? " fail" : "");
      r.textContent = (evt.status==="done" ? "✔ " : "✘ ") + t.result;
      body.querySelector(".tInner").appendChild(r);
    }
    if (evt.status === "failed") setStatus("err", "子任务失败，降级继续");
    else phase("子任务完成");
  }
  else if (type === "ask_user") {
    setTaskStatus(evt.task_id, "waiting");
    $("askBanner").classList.add("show");
    addMsg("agent", md(`**需要你补充信息：**\n\n${evt.question}`), {status: "⏸ 等待输入"});
    setStatus("wait", "等待你补充材料");
    $("input").focus();
  }
  else if (type === "resumed") { $("askBanner").classList.remove("show"); setStatus("run"); }
  else if (type === "final_answer") {
    dropTyping();
    addMsg("agent", md(evt.content || ""), {status: "✓ 已完成"});
    if (evt.stats) {
      $("stLlm").textContent = evt.stats.llm_calls || 0;
      $("stTool").textContent = evt.stats.tool_calls || 0;
      $("stRetry").textContent = evt.stats.tool_retries || 0;
      if ((evt.stats.failed_tasks||[]).length)
        addMsg("agent", md(`⚠️ 以下子任务失败但流程已降级完成：${evt.stats.failed_tasks.join("、")}`), {status: "⚠ 降级完成"});
    }
    setStatus("idle", "任务完成");
  }
  else if (type === "error") {
    dropTyping();
    addMsg("agent", `<span class="errTx">⚠️ ${esc(evt.message)}</span>`, {status: "✗ 异常"});
    setStatus("err", evt.message.slice(0, 20));
  }
}

// ---------- 发送 ----------
async function send() {
  const text = $("input").value.trim();
  if (!text || running) return;
  $("input").value = ""; autoHeight();
  $("askBanner").classList.remove("show");
  running = true; $("send").disabled = true;
  addMsg("user", text);
  showTyping();
  try {
    const resp = await fetch("/api/chat", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({session_id: sessionId, message: text, resume_path: selectedResume})});
    if (!resp.ok) { const e = await resp.json().catch(()=>({})); throw new Error(e.error || resp.statusText); }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buf += decoder.decode(value, {stream:true});
      const parts = buf.split("\n\n");
      buf = parts.pop();
      for (const p of parts) {
        const line = p.trim();
        if (line.startsWith("data:")) {
          try { await handleEvent(JSON.parse(line.slice(5).trim())); } catch(e) { console.warn("bad evt", e); }
        }
      }
    }
  } catch(e) {
    addMsg("agent", `<span class="errTx">请求失败: ${esc(e.message)}</span>`, {status: "✗ 失败"});
    setStatus("err", e.message.slice(0, 24));
  }
  running = false; $("send").disabled = false;
  dropTyping();
}
function autoHeight() {
  const el = $("input");
  el.style.height = "26px";
  el.style.height = Math.min(el.scrollHeight, 160) + "px";
}
$("send").onclick = send;
$("input").addEventListener("input", autoHeight);
$("input").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});

// ---------- 粘贴截图 → OCR ----------
$("input").addEventListener("paste", async e => {
  const items = [...(e.clipboardData?.items || [])];
  const imgItem = items.find(i => i.type.startsWith("image/"));
  if (!imgItem) return;
  e.preventDefault();
  const file = imgItem.getAsFile();
  if (file) await ocrFlow(file);
});
async function ocrFlow(file) {
  if (running) { alert("Agent 正在执行，请稍候再粘贴图片"); return; }
  const tip = $("ocrTip");
  tip.classList.add("show");
  tip.textContent = "🖼 正在识别截图中的JD文字…";
  try {
    const fd = new FormData();
    fd.append("file", file, file.name || "paste.png");
    const r = await fetch("/api/ocr", {method: "POST", body: fd});
    const data = await r.json();
    if (!r.ok || !data.ok) throw new Error(data.error || "识别失败");
    tip.textContent = `🖼 识别到 ${data.chars} 字，已开始分析`;
    setTimeout(() => tip.classList.remove("show"), 4000);
    const head = $("input").value.trim();
    $("input").value = (head ? head + "\n\n" : "") + "【图片JD内容】\n" + data.text;
    autoHeight();
    send();
  } catch(err) {
    tip.textContent = "❌ " + err.message;
    setTimeout(() => tip.classList.remove("show"), 5000);
  }
}

// ---------- 文件拖拽 / 选择 ----------
const ALLOW_IMG = ["png","jpg","jpeg","webp","bmp"], ALLOW_RESUME = ["pdf","docx","txt","md"];
function classify(file) {
  const ext = file.name.split(".").pop().toLowerCase();
  if (ALLOW_IMG.includes(ext)) return "image";
  if (ALLOW_RESUME.includes(ext) && /简历|resume|cv/i.test(file.name)) return "resume";
  if (ALLOW_RESUME.includes(ext)) return "material";
  return null;
}
async function handleFiles(files) {
  for (const f of files) {
    const kind = classify(f);
    if (kind === "image") await ocrFlow(f);
    else if (kind === "resume") {
      const r = await uploadResumeFile(f);
      if (r.ok) { addMsg("agent", md(`📄 简历 **${r.name}** 已入库并设为使用中`), {status:"✓ 已入库"}); await loadResumes(r.name); }
    }
    else if (kind === "material") {
      try {
        const fd = new FormData(); fd.append("file", f);
        const r = await (await fetch("/api/upload", {method:"POST", body: fd})).json();
        if (!r.ok) throw new Error(r.error);
        $("input").value = `帮我分析 ${r.path} 这份材料`;
        autoHeight();
        send();
      } catch(e) { addMsg("agent", `<span class="errTx">上传失败: ${esc(e.message)}</span>`, {status:"✗ 失败"}); }
    }
    else addMsg("agent", md(`⚠️ 不支持的文件类型：**${f.name}**（支持图片 / pdf / docx / txt / md）`), {status:"⚠ 已跳过"});
  }
}
["dragenter", "dragover"].forEach(ev => document.addEventListener(ev, e => {
  e.preventDefault();
  if (e.dataTransfer?.types?.includes("Files")) $("dropMask").style.display = "flex";
}));
$("dropMask").addEventListener("dragleave", () => $("dropMask").style.display = "none");
$("dropMask").addEventListener("drop", async e => {
  e.preventDefault(); $("dropMask").style.display = "none";
  await handleFiles([...e.dataTransfer.files]);
});
$("clip").onclick = () => $("filePick").click();
$("filePick").onchange = async e => { await handleFiles([...e.target.files]); e.target.value = ""; };

// ---------- 简历库 ----------
async function uploadResumeFile(f) {
  const fd = new FormData(); fd.append("file", f);
  const r = await (await fetch("/api/resumes/upload", {method:"POST", body: fd})).json();
  if (r.ok) { selectedResume = r.name; localStorage.setItem("agent_resume", r.name); updateResumeChip(); }
  return r;
}
async function loadResumes(selectName) {
  const list = $("resumeList");
  const data = await (await fetch("/api/resumes")).json();
  const items = data.resumes || [];
  if (selectName) selectedResume = selectName;
  if (selectedResume && !items.some(i => i.name === selectedResume)) selectedResume = "";
  if (!selectedResume && items.length) { selectedResume = items[0].name; localStorage.setItem("agent_resume", selectedResume); }
  updateResumeChip();
  if (!items.length) { list.innerHTML = '<div class="rEmpty">简历库还是空的，先上传一份吧</div>'; return; }
  list.innerHTML = "";
  items.forEach(i => {
    const d = document.createElement("div");
    d.className = "rItem" + (i.name === selectedResume ? " active" : "");
    const kb = (i.size/1024).toFixed(0);
    d.innerHTML = `<span class="rName">${esc(i.name)}<br><span class="rMeta">${kb}KB · ${new Date(i.mtime*1000).toLocaleString()}</span></span>
      <span class="rUse">${i.name === selectedResume ? "✓ 使用中" : "使用"}</span>
      <span class="rDel" title="删除">🗑</span>`;
    d.querySelector(".rUse").onclick = () => { selectedResume = i.name; localStorage.setItem("agent_resume", i.name); loadResumes(); };
    d.querySelector(".rDel").onclick = async () => {
      if (!confirm(`确定删除简历「${i.name}」？`)) return;
      await fetch("/api/resumes/" + encodeURIComponent(i.name), {method: "DELETE"});
      if (selectedResume === i.name) { selectedResume = ""; localStorage.removeItem("agent_resume"); }
      loadResumes();
    };
    list.appendChild(d);
  });
}
function updateResumeChip() {
  $("resumeChip").textContent = selectedResume ? `📄 ${selectedResume.slice(0, 14)}` : "📄 简历库";
  $("resumeChip").title = selectedResume ? `当前使用: ${selectedResume}` : "未选择简历（Agent 将自动使用最新上传的简历）";
}
$("resumeChip").onclick = () => { $("resumePanel").style.display = "block"; $("resumeMask").style.display = "block"; loadResumes(); };
$("resumeClose").onclick = $("resumeMask").onclick = () => { $("resumePanel").style.display = "none"; $("resumeMask").style.display = "none"; };
$("dropZone").onclick = () => $("resumePick").click();
$("resumePick").onchange = async e => { for (const f of e.target.files) await uploadResumeFile(f); loadResumes(); e.target.value = ""; };
["dragover", "dragenter"].forEach(ev => $("dropZone").addEventListener(ev, e => { e.preventDefault(); e.stopPropagation(); $("dropZone").classList.add("over"); }));
$("dropZone").addEventListener("dragleave", () => $("dropZone").classList.remove("over"));
$("dropZone").addEventListener("drop", async e => {
  e.preventDefault(); e.stopPropagation(); $("dropZone").classList.remove("over");
  for (const f of [...e.dataTransfer.files]) await uploadResumeFile(f);
  loadResumes();
});

// ---------- 侧栏折叠（移动端） ----------
$("burger").onclick = () => { $("sidebar").classList.add("open"); $("sidebarMask").style.display = "block"; };
$("sidebarMask").onclick = closeSidebar;
function closeSidebar() { $("sidebar").classList.remove("open"); $("sidebarMask").style.display = "none"; }

// ---------- 初始化 ----------
(async function init() {
  updateResumeChip();
  setStatus("idle", "就绪");
  try {
    const h = await (await fetch("/api/health")).json();
    const chip = $("providerChip");
    if (h.provider === "mock") { chip.textContent = "⚠️ mock 模式"; chip.className = "chip warn"; }
    else { chip.textContent = `⚡ ${h.provider}/${h.model}`; chip.className = "chip on"; }
  } catch(e) { $("providerChip").textContent = "服务未连接"; setStatus("err", "服务未连接"); }
  await loadResumes();
  if (sessionId) {
    const r = await fetch("/api/session/" + sessionId).catch(()=>null);
    if (r && r.ok) {
      const s = await r.json();
      $("sessionChip").textContent = "会话 " + sessionId.slice(0, 8);
      (s.messages||[]).slice(-14).forEach(m => addMsg(m.role==="user"?"user":"agent", m.role==="user"? m.content : md(m.content)));
      if (s.tasks && s.tasks.length) {
        $("goalText").className = "goal";
        $("goalText").textContent = "🎯 " + (s.title || "历史任务");
        tasks = {}; taskOrder = [];
        s.tasks.forEach(t => { tasks[t.id] = {title:t.title, detail:t.detail, tool:t.tool, status:t.status, retries:t.retries, result:t.result||t.error}; taskOrder.push(t.id); });
        renderTaskList();
      }
      if (s.facts) addMemChips(s.facts);
      if (s.status === "awaiting_input") { $("askBanner").classList.add("show"); setStatus("wait", "等待你补充材料"); }
    }
  }
})();
