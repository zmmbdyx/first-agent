"""图片OCR工具：识别截图里的JD文字（rapidocr，首次调用懒加载）。
OCR后处理：领域词典纠错（如"数据分折"→"数据分析"），提高JD结构化准确率。"""
from pathlib import Path

from core.tools.base import Tool, ToolError

_ocr_engine = None

# 常见 OCR 错别字/形近字纠错（招聘领域）
TYPO_FIX = {
    "数据分折": "数据分析", "分折": "分析", "数椐": "数据", "教据": "数据",
    "熟炼": "熟练", "熟練": "熟练", "精酮": "精通", "拿握": "掌握",
    "崗位": "岗位", "刚位": "岗位", "職責": "职责", "职赍": "职责",
    "任職": "任职", "任取要求": "任职要求", "任职要沗": "任职要求",
    "应屆": "应届", "招骋": "招聘", "面拭": "面试", "简介试": "简历",
    "挑戰": "挑战", "优𠵿": "优先", "加薪项": "加分项", "加份项": "加分项",
    "本科及已上": "本科及以上", "負責": "负责", "负贲": "负责",
}


def fix_ocr_text(text: str) -> str:
    for wrong, right in TYPO_FIX.items():
        text = text.replace(wrong, right)
    return text


def _get_engine():
    global _ocr_engine
    if _ocr_engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as e:
            raise ToolError(f"OCR组件未安装: {e}，请执行 pip install rapidocr-onnxruntime")
        _ocr_engine = RapidOCR()
    return _ocr_engine


class ImageOcrTool(Tool):
    name = "image_ocr"
    description = "识别图片(png/jpg/webp/截图)中的文字。用于用户粘贴的JD截图，先OCR再分析。"
    args_desc = {"path": "图片文件路径"}
    cost = "高"
    avg_seconds = 3.5

    def run(self, path: str = "") -> dict:
        import io
        from core.tools.file_tools import _resolve
        p = Path(_resolve(path))
        if p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
            raise ToolError(f"不是支持的图片格式: {p.name}")
        try:
            import numpy as np
            from PIL import Image
            from core import secure_store
            img = Image.open(io.BytesIO(secure_store.read_bytes(p)))
            result, _ = _get_engine()(np.array(img))
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(f"OCR识别失败: {type(e).__name__}: {e}")
        if not result:
            raise ToolError("图片中未识别到文字（可能截图过小/过模糊），建议粘贴清晰的文字截图")
        text = fix_ocr_text("\n".join(str(r[1]) for r in result))
        return {"path": str(p), "chars": len(text), "text": text[:12000]}
