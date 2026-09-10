# ============================================================================
# 拓径 PATHFORGE — 前后端分离部署镜像
# 多阶段构建：① Node 构建前端静态产物 ② Python 运行时托管后端 + 前端产物
# ============================================================================

# ---------- 阶段一：构建前端 ----------
FROM node:20-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- 阶段二：后端运行时 ----------
FROM python:3.12-slim

WORKDIR /app

# 系统依赖：matplotlib / onnxruntime 运行所需；git 供工作区变更面板使用
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libgl1 git \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ ./backend/
# 前端构建产物由后端以静态站点方式托管（单端口部署）
COPY --from=web /web/dist ./frontend/dist

# 首次启动预下载 OCR 模型，避免首个请求超时
RUN python -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR()" || true

ENV PYTHONIOENCODING=utf-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
