# -*- coding: utf-8 -*-
"""
自動更新模組

啟動時：
  1. 讀取 update_config.json（owner / repo / branch / token，唯讀權杖；此檔不進版控）
  2. 向 GitHub 取得遠端 version.py 的版本號
  3. 若遠端較新 → 下載 repo tarball、覆蓋本機檔案 → 由 app.py 重新啟動

設計原則：**容錯不擋路**。沒設定、連不上、下載失敗都只會「以目前版本開啟」，不會卡住使用者。
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import tarfile
import tempfile
from typing import Optional

import requests

CONFIG_NAME = "update_config.json"
# 覆蓋更新時，這些檔/資料夾永遠不動（本機環境與機密）
PROTECTED = {
    "venv", "__pycache__", ".git", ".gitignore",
    CONFIG_NAME, "config.json", ".update_token", ".DS_Store",
}

# 公開 repo 的預設更新來源：不需 token 即可檢查／下載更新。
# 若日後改為私有，於 update_config.json 補上 token 即可（會覆蓋以下預設）。
DEFAULT_OWNER = "pony9632-pixel"
DEFAULT_REPO = "studioa-reservation-manager"
DEFAULT_BRANCH = "main"


# ---------------------------------------------------------------- #
# 版本比較
# ---------------------------------------------------------------- #
def parse_version(text: str) -> Optional[str]:
    """從 version.py 內容字串取出 __version__。"""
    m = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", text or "")
    return m.group(1) if m else None


def _vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")) or (0,)


def is_newer(remote: str, local: str) -> bool:
    """remote 是否比 local 新。"""
    a, b = _vtuple(remote), _vtuple(local)
    n = max(len(a), len(b))
    a += (0,) * (n - len(a))
    b += (0,) * (n - len(b))
    return a > b


# ---------------------------------------------------------------- #
# 本機版本與設定
# ---------------------------------------------------------------- #
def current_version(app_dir: str) -> str:
    try:
        with open(os.path.join(app_dir, "version.py"), encoding="utf-8") as f:
            return parse_version(f.read()) or "0.0.0"
    except Exception:
        return "0.0.0"


def load_config(app_dir: str) -> Optional[dict]:
    """回傳更新設定。

    公開 repo 用內建預設即可（不需 token）；若存在 update_config.json，
    其中有設定的欄位會覆蓋預設（例如改私有後補上 token）。
    """
    cfg = {
        "owner": DEFAULT_OWNER,
        "repo": DEFAULT_REPO,
        "branch": DEFAULT_BRANCH,
        "token": "",
    }
    path = os.path.join(app_dir, CONFIG_NAME)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for k in ("owner", "repo", "branch", "token"):
            if data.get(k):
                cfg[k] = data[k]
    except Exception:
        pass
    return cfg if cfg.get("owner") and cfg.get("repo") else None


def _headers(token: str, raw: bool = False) -> dict:
    h = {
        "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:  # 公開 repo 可不帶 token；私有時才需要
        h["Authorization"] = f"Bearer {token}"
    return h


# ---------------------------------------------------------------- #
# 向 GitHub 查最新版 / 下載
# ---------------------------------------------------------------- #
def fetch_remote_version(cfg: dict, timeout: int = 15) -> Optional[str]:
    url = f"https://api.github.com/repos/{cfg['owner']}/{cfg['repo']}/contents/version.py"
    try:
        r = requests.get(url, params={"ref": cfg["branch"]},
                         headers=_headers(cfg["token"], raw=True), timeout=timeout)
        if r.status_code == 200:
            return parse_version(r.text)
    except requests.RequestException:
        pass
    return None


def download_and_apply(cfg: dict, app_dir: str, timeout: int = 60) -> bool:
    """下載 repo tarball 並覆蓋本機檔案。成功回傳 True。"""
    url = f"https://api.github.com/repos/{cfg['owner']}/{cfg['repo']}/tarball/{cfg['branch']}"
    try:
        r = requests.get(url, headers=_headers(cfg["token"]), timeout=timeout)
        if r.status_code != 200:
            return False
        data = r.content
    except requests.RequestException:
        return False

    tmp = tempfile.mkdtemp(prefix="studioa_update_")
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            tar.extractall(tmp)
        # tarball 內只有一個根資料夾
        roots = [d for d in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, d))]
        if not roots:
            return False
        src_root = os.path.join(tmp, roots[0])

        # 蒐集要複製的檔案（相對路徑），version.py 留到最後
        files: list[str] = []
        for dirpath, dirnames, filenames in os.walk(src_root):
            dirnames[:] = [d for d in dirnames if d not in PROTECTED]
            for fn in filenames:
                if fn in PROTECTED:
                    continue
                rel = os.path.relpath(os.path.join(dirpath, fn), src_root)
                files.append(rel)
        files.sort(key=lambda p: (p == "version.py", p))  # version.py 最後

        for rel in files:
            dst = os.path.join(app_dir, rel)
            os.makedirs(os.path.dirname(dst) or app_dir, exist_ok=True)
            shutil.copy2(os.path.join(src_root, rel), dst)
        return True
    except Exception:
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- #
# 對外主流程
# ---------------------------------------------------------------- #
def check_and_update(app_dir: str) -> tuple[str, str, str]:
    """檢查並（必要時）套用更新。

    回傳 (status, message, version)：
      status: "updated"（已更新，需重啟）/ "latest" / "skipped" / "error"
    """
    local = current_version(app_dir)

    # 剛更新完重啟的那一次，跳過檢查避免迴圈
    if os.environ.get("STUDIOA_JUST_UPDATED") == "1":
        return ("latest", f"目前版本 {local}", local)

    cfg = load_config(app_dir)
    if not cfg:
        return ("skipped", f"目前版本 {local}（未設定自動更新，略過檢查）", local)

    remote = fetch_remote_version(cfg)
    if not remote:
        return ("error", f"目前版本 {local}（無法連線檢查更新，將直接開啟）", local)

    if not is_newer(remote, local):
        return ("latest", f"已是最新版本 {local}", local)

    ok = download_and_apply(cfg, app_dir)
    if ok:
        return ("updated", f"已更新 {local} → {remote}，即將重新啟動…", remote)
    return ("error", f"目前版本 {local}（發現新版 {remote} 但下載失敗，將直接開啟）", local)


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    print("本機版本：", current_version(here))
    cfg = load_config(here)
    if not cfg:
        print("尚未設定 update_config.json（owner/repo/token），無法查遠端版本。")
    else:
        print("遠端版本：", fetch_remote_version(cfg) or "查詢失敗")
