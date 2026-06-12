# StudioA 門市預約管理（網頁版）— 雲端部署用
FROM python:3.12-slim

# 標籤 PDF 需要中文字型：必須是 TrueType 外框（reportlab 不支援
# fonts-noto-cjk 的 CFF 外框，會整份退回 Helvetica 讓中文變方塊）
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir requests reportlab

ENV PORT=8765
EXPOSE 8765
CMD ["python", "web_app.py"]
