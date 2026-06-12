#!/bin/bash
# StudioA 預約管理（網頁版）啟動器：啟動本機伺服器並自動開啟瀏覽器
cd "$(dirname "$0")"
echo "正在啟動網頁版（會自動開啟瀏覽器），請保持此視窗開啟…"
exec python3 web_app.py
