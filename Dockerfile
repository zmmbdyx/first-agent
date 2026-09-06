FROM python:3.12-slim

WORKDIR /app

# 系统依赖：matplotlib/-onnxruntime 运行所需
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY . .

# 首次启动预下载OCR模型，避免首次请求超时
RUN python -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR()" || true

EXPOSE 8000
ENV PYTHONIOENCODING=utf-8
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
