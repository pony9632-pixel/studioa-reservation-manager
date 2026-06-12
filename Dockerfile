# StudioA 門市預約管理（網頁版）— 雲端部署用
FROM python:3.12-slim

# 標籤 PDF 需要中文字型
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir requests reportlab

ENV PORT=8765
EXPOSE 8765
CMD ["python", "web_app.py"]
