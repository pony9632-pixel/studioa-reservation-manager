#!/bin/bash
# =====================================================================
# StudioA 門市預約管理 — Mac 一鍵安裝
# 同事只要雙擊本檔即可完成安裝；之後雙擊桌面的啟動器即可使用。
# =====================================================================
set -e
cd "$(dirname "$0")"            # 切到安裝包所在資料夾
SRC="$(pwd)"

APP_NAME="StudioA預約管理"
APP_DIR="$HOME/$APP_NAME"
LAUNCHER="$HOME/Desktop/${APP_NAME}.command"

echo "==================================================="
echo "   StudioA 門市預約管理 — 安裝程式"
echo "==================================================="
echo ""

# 1) 檢查 Python3 -------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  echo "⚠️  這台電腦還沒有 Python3。"
  echo "    將嘗試開啟 Apple 開發者工具安裝視窗，請按「安裝」。"
  xcode-select --install 2>/dev/null || true
  echo ""
  echo "    安裝完成後，請再次雙擊本檔（install.command）繼續。"
  read -n 1 -s -r -p "按任意鍵結束…"; echo
  exit 1
fi
echo "✅ Python：$(python3 --version)"

# 2) 複製程式到應用資料夾 ---------------------------------------------
echo "→ 複製程式到 $APP_DIR"
mkdir -p "$APP_DIR"
# 複製所有程式檔（*.py，含 labels.py 等未來新增的模組）與必要檔
cp -f "$SRC"/*.py "$APP_DIR"/ 2>/dev/null || true
for f in requirements.txt README.md api_notes.md update_config.json; do
  [ -f "$SRC/$f" ] && cp -f "$SRC/$f" "$APP_DIR/"
done
echo "   （自動更新已內建、免設定：每次開啟會自動檢查並更新到最新版。）"

# 3) 建立虛擬環境並安裝套件 -------------------------------------------
echo "→ 建立虛擬環境並安裝套件（第一次約需 1–3 分鐘，請稍候）…"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/python" -m pip install --upgrade pip >/dev/null 2>&1 || true
"$APP_DIR/venv/bin/python" -m pip install -r "$APP_DIR/requirements.txt"

# 4) 在桌面建立啟動器 -------------------------------------------------
echo "→ 在桌面建立啟動器：${APP_NAME}.command"
cat > "$LAUNCHER" <<EOF
#!/bin/bash
# StudioA 預約管理 啟動器（開啟前會自動檢查更新）
cd "$APP_DIR"
echo "正在檢查更新並開啟，請勿關閉此視窗…"
exec "$APP_DIR/venv/bin/python" app.py
EOF
chmod +x "$LAUNCHER"

echo ""
echo "==================================================="
echo "  ✅ 安裝完成！"
echo ""
echo "  開啟方式：雙擊桌面的「${APP_NAME}.command」"
echo "  （第一次開啟若出現安全性提醒，請對它按右鍵→打開）"
echo "==================================================="
read -n 1 -s -r -p "按任意鍵結束…"; echo
