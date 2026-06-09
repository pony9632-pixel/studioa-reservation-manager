#!/bin/bash
# =====================================================================
# 設定自動更新用的 GitHub「唯讀」權杖（只有管理者需要執行一次）
# 會在本資料夾建立 update_config.json（含權杖，已被 .gitignore 排除，不會上傳）
# =====================================================================
cd "$(dirname "$0")"
echo "==================================================="
echo "   設定自動更新權杖"
echo "==================================================="
echo ""
echo "請貼上 GitHub「唯讀」權杖（github_pat_ 開頭），然後按 Enter："
read -r TOKEN
TOKEN="$(printf '%s' "$TOKEN" | tr -d '[:space:]')"
if [ -z "$TOKEN" ]; then
  echo "❌ 沒有輸入內容，已取消。"
  read -n 1 -s -r -p "按任意鍵結束…"; echo; exit 1
fi
cat > update_config.json <<EOF
{
  "owner": "pony9632-pixel",
  "repo": "studioa-reservation-manager",
  "branch": "main",
  "token": "$TOKEN"
}
EOF
echo ""
echo "✅ 已建立 update_config.json（含權杖；此檔不會上傳 GitHub）。"
echo "   現在可以測試自動更新了。"
read -n 1 -s -r -p "按任意鍵結束…"; echo
