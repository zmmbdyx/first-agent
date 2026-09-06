/* dragdrop.js — 截图粘贴 / 文件拖拽 / 📎选择 → OCR 或上传分流 */
import { $, toast } from "./utils.js";
import { addMsg } from "./ui.js";

const ALLOW_IMG = ["png", "jpg", "jpeg", "webp", "bmp"];
const ALLOW_DOC = ["pdf", "docx", "txt", "md"];

/** 文件分流：图片→OCR；名字含简历→简历库；其余文档→材料上传并分析 */
function classify(file) {
  const ext = (file.name.split(".").pop() || "").toLowerCase();
  if (ALLOW_IMG.includes(ext)) return "image";
  if (ALLOW_DOC.includes(ext) && /简历|resume|cv/i.test(file.name)) return "resume";
  if (ALLOW_DOC.includes(ext)) return "material";
  return null;
}

/** OCR 识别截图并自动发送 */
export async function ocrFlow(file, ctx) {
  if (ctx.isRunning()) { toast("Agent 正在执行，请稍候再粘贴图片", "warn"); return; }
  const tip = $("ocrTip");
  tip.classList.add("show", "banner-info");
  tip.textContent = "🖼 正在识别截图中的JD文字…";
  try {
    const fd = new FormData();
    fd.append("file", file, file.name || "paste.png");
    const r = await fetch("/api/ocr", { method: "POST", body: fd });
    const data = await r.json();
    if (!r.ok || !data.ok) throw new Error(data.error || "识别失败");
    tip.textContent = `🖼 识别到 ${data.chars} 字，已开始分析`;
    setTimeout(() => tip.classList.remove("show"), 4000);
    const head = $("input").value.trim();
    $("input").value = (head ? head + "\n\n" : "") + "【图片JD内容】\n" + data.text;
    ctx.autoHeight();
    ctx.send();
  } catch (err) {
    tip.classList.add("banner-danger");
    tip.textContent = "❌ " + err.message;
    setTimeout(() => tip.classList.remove("show"), 5000);
  }
}

/** 通用材料上传后直接分析 */
async function materialFlow(file, ctx) {
  try {
    const fd = new FormData();
    fd.append("file", file);
    const r = await (await fetch("/api/upload", { method: "POST", body: fd })).json();
    if (!r.ok) throw new Error(r.error || "上传失败");
    $("input").value = `帮我分析 ${r.path} 这份材料`;
    ctx.autoHeight();
    ctx.send();
  } catch (e) {
    addMsg("agent", `<span class="errTx">上传失败: ${e.message}</span>`, { status: "✗ 失败" });
  }
}

/** 批量处理拖入/选择的文件 */
export async function handleFiles(files, ctx) {
  for (const f of files) {
    const kind = classify(f);
    if (kind === "image") await ocrFlow(f, ctx);
    else if (kind === "resume") await ctx.uploadResumeFile(f);
    else if (kind === "material") await materialFlow(f, ctx);
    else addMsg("agent", `⚠️ 不支持的文件类型：${f.name}（支持图片 / pdf / docx / txt / md）`, { status: "⚠ 已跳过" });
  }
}

/** 绑定粘贴、拖拽、📎选择（事件统一挂接，供 app.js 调用一次） */
export function initDragdrop(ctx) {
  // Ctrl+V 截图粘贴：拦截 paste 事件提取图片
  $("input").addEventListener("paste", async (e) => {
    const items = [...(e.clipboardData?.items || [])];
    const imgItem = items.find((i) => i.type.startsWith("image/"));
    if (!imgItem) return; // 纯文本走默认粘贴
    e.preventDefault();
    const file = imgItem.getAsFile();
    if (file) await ocrFlow(file, ctx);
  });

  // 拖拽上传（高亮虚线遮罩）
  const mask = $("dropMask");
  ["dragenter", "dragover"].forEach((ev) =>
    document.addEventListener(ev, (e) => {
      e.preventDefault();
      if (e.dataTransfer?.types?.includes("Files")) mask.style.display = "flex";
    })
  );
  mask.addEventListener("dragleave", () => (mask.style.display = "none"));
  mask.addEventListener("drop", async (e) => {
    e.preventDefault();
    mask.style.display = "none";
    await handleFiles([...e.dataTransfer.files], ctx);
  });

  // 📎 文件选择
  $("clip").addEventListener("click", () => $("filePick").click());
  $("filePick").addEventListener("change", async (e) => {
    await handleFiles([...e.target.files], ctx);
    e.target.value = "";
  });
}
