#!/bin/bash
# StudioA 預約管理 啟動器（開啟前會自動檢查更新）
cd "/Users/apple/StudioA預約管理"
echo "正在檢查更新並開啟，請勿關閉此視窗…"
exec "/Users/apple/StudioA預約管理/venv/bin/python" app.py
