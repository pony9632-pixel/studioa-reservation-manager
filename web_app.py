# -*- coding: utf-8 -*-
"""
StudioA 門市預約管理 — 網頁版（HTML 介面）

啟動一個網頁伺服器，介面用瀏覽器操作（web/index.html），
功能與桌面版相同：預約總覽 / 區間查詢 / 狀態管理 / 門市遞補 / 變更紀錄。

為什麼需要這支伺服器：後台 API（studioa.com.tw）沒有開放瀏覽器跨網域
存取（CORS），純 HTML 檔無法直接呼叫，所以由本程式代轉 API。

兩種執行模式：
  本機模式   python3 web_app.py
             聽 127.0.0.1，自動開瀏覽器，啟動前檢查更新。
  伺服器模式  設定環境變數 PORT（雲端平台如 Render 會自動設定）
             聽 0.0.0.0，不開瀏覽器、不跑自動更新（雲端由 git 部署）。

多人使用：每個瀏覽器登入後發一個 session cookie，各自對應一份後台
token，互不干擾；session 閒置 12 小時後失效。

相依：requests（標籤列印另需 reportlab，本機首次列印會自動安裝）
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import changelog
import client
from client import StudioAClient, StudioAError, STATUS_CODE_TO_NAME
from version import __version__

APP_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(APP_DIR, "web")
PORT_PREFERRED = 8765
SERVER_MODE = bool(os.environ.get("PORT"))  # 雲端/伺服器模式
HOST = "0.0.0.0" if SERVER_MODE else "127.0.0.1"

SESSION_COOKIE = "studioa_sid"
SESSION_TTL = 12 * 3600          # 閒置 12 小時後要重新登入
LOGIN_FAIL_LIMIT = 15            # 同一來源 10 分鐘內最多失敗次數
LOGIN_FAIL_WINDOW = 600

# sid -> {"client": StudioAClient, "ts": 最後使用時間}
_sessions: dict[str, dict] = {}
_sessions_lock = threading.Lock()
# 登入失敗紀錄：來源 IP -> [失敗時間, ...]
_login_fails: dict[str, list] = {}


def _parse_date(s: str, *, end: bool = False) -> dt.datetime:
    d = dt.datetime.strptime(s.strip(), "%Y-%m-%d")
    return d.replace(hour=23, minute=59, second=59) if end else d


def _ensure_reportlab():
    """確保 reportlab 可用；本機模式缺少時嘗試自動安裝（首次列印用）。"""
    try:
        import reportlab  # noqa: F401
        return
    except ImportError:
        pass
    if SERVER_MODE:
        raise StudioAError("伺服器缺少 reportlab 元件，請於部署環境安裝。")
    import subprocess
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "reportlab",
             "--quiet", "--disable-pip-version-check"],
            check=True,
        )
    except Exception as e:
        raise StudioAError(
            f"列印需要 reportlab 元件，但自動安裝失敗。請在終端機執行：pip3 install reportlab（{e}）"
        )
    try:
        import reportlab  # noqa: F401
    except ImportError:
        raise StudioAError("已安裝 reportlab 但仍無法載入，請重新啟動程式再試。")


def _prune_sessions():
    now = time.time()
    with _sessions_lock:
        for sid in [s for s, v in _sessions.items() if now - v["ts"] > SESSION_TTL]:
            del _sessions[sid]


class Handler(BaseHTTPRequestHandler):
    server_version = f"StudioAWeb/{__version__}"

    # ---------------- 回應小工具 ---------------- #
    def _send(self, status: int, body: bytes, content_type: str, extra_headers: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, status: int = 200, extra_headers: dict | None = None):
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", extra_headers)

    def _error(self, message: str):
        status = 401 if ("未授權" in message or "重新登入" in message or "尚未登入" in message) else 400
        self._json({"error": message}, status)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            raise StudioAError("請求內容不是合法的 JSON。")

    def log_message(self, fmt, *args):  # 安靜一點
        pass

    # ---------------- Session ---------------- #
    def _client_ip(self) -> str:
        fwd = self.headers.get("X-Forwarded-For")
        if fwd:
            return fwd.split(",")[0].strip()
        return self.client_address[0]

    def _sid(self) -> str | None:
        cookie = self.headers.get("Cookie") or ""
        m = re.search(rf"{SESSION_COOKIE}=([A-Za-z0-9_\-]+)", cookie)
        return m.group(1) if m else None

    def _session(self) -> dict | None:
        sid = self._sid()
        if not sid:
            return None
        with _sessions_lock:
            sess = _sessions.get(sid)
            if sess:
                sess["ts"] = time.time()
        return sess

    def _api(self) -> StudioAClient:
        sess = self._session()
        if not sess:
            raise StudioAError("尚未登入（沒有 session）。請先登入。")
        return sess["client"]

    def _new_session(self, api: StudioAClient) -> dict:
        """登入成功後建立 session，回傳要附在回應上的 Set-Cookie 標頭。"""
        sid = secrets.token_urlsafe(32)
        with _sessions_lock:
            _sessions[sid] = {"client": api, "ts": time.time()}
        secure = "; Secure" if (self.headers.get("X-Forwarded-Proto") == "https") else ""
        return {"Set-Cookie":
                f"{SESSION_COOKIE}={sid}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL}{secure}"}

    def _check_login_rate(self):
        ip = self._client_ip()
        now = time.time()
        fails = [t for t in _login_fails.get(ip, []) if now - t < LOGIN_FAIL_WINDOW]
        _login_fails[ip] = fails
        if len(fails) >= LOGIN_FAIL_LIMIT:
            raise StudioAError("登入失敗次數過多，請 10 分鐘後再試。")

    # ---------------- 路由 ---------------- #
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        q = parse_qs(parsed.query)
        _prune_sessions()
        try:
            if path == "/" or path == "/index.html":
                self._serve_file("index.html")
            elif path == "/api/session":
                sess = self._session()
                api = sess["client"] if sess else None
                self._json({
                    "loggedIn": bool(api and api.token),
                    "shopName": api.shop_name if api else None,
                    "userName": api.user_name if api else None,
                    "version": __version__,
                })
            elif path == "/api/reservations":
                self._api_reservations(q)
            elif path == "/api/activities":
                self._json(self._api().fetch_activities())
            elif path == "/api/fill-list":
                start = _parse_date(q["start"][0])
                end = _parse_date(q["end"][0], end=True)
                self._json(self._api().fetch_fill_list(start, end))
            elif path == "/api/changelog":
                self._json(changelog.all_changes(shop=self._api().shop_name))
            else:
                self._send(404, "Not Found".encode(), "text/plain")
        except StudioAError as e:
            self._error(str(e))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            self._json({"error": f"發生未預期錯誤：{e}"}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        _prune_sessions()
        try:
            body = self._read_json()
            if path == "/api/login":
                self._check_login_rate()
                api = StudioAClient()
                try:
                    api.login((body.get("username") or "").strip(), body.get("password") or "")
                except StudioAError:
                    _login_fails.setdefault(self._client_ip(), []).append(time.time())
                    raise
                headers = self._new_session(api)
                self._json({"shopName": api.shop_name, "userName": api.user_name},
                           extra_headers=headers)
            elif path == "/api/update-status":
                ids = body.get("shelfIds") or []
                status = int(body.get("status"))
                msg = self._api().update_status(ids, status)
                self._json({"message": msg})
            elif path == "/api/fill":
                ids = body.get("shelfIds") or []
                msg = self._api().fill_reservations(ids)
                self._json({"message": msg})
            elif path == "/api/labels":
                self._api()  # 需登入
                self._api_labels(body)
            else:
                self._send(404, "Not Found".encode(), "text/plain")
        except StudioAError as e:
            self._error(str(e))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            self._json({"error": f"發生未預期錯誤：{e}"}, 500)

    # ---------------- 各 API 實作 ---------------- #
    def _api_reservations(self, q: dict):
        """查詢預約。參數：
          start / end       YYYY-MM-DD（查單號/電話/狀態時可省略 → 用很寬的區間）
          statuses          逗號分隔的狀態代碼，多個會分別查再合併
          orderSno / phone  關鍵字查詢
        回傳 {stats, items}。
        """
        api = self._api()
        if q.get("start"):
            start = _parse_date(q["start"][0])
        else:
            start = dt.datetime(2000, 1, 1)
        if q.get("end"):
            end = _parse_date(q["end"][0], end=True)
        else:
            end = dt.datetime.now() + dt.timedelta(days=3650)
        order_sno = (q.get("orderSno") or [None])[0]
        phone = (q.get("phone") or [None])[0]
        statuses_raw = (q.get("statuses") or [""])[0]
        codes = [int(s) for s in statuses_raw.split(",") if s.strip()]

        if len(codes) <= 1:
            stats, items = api.fetch_all_items(
                start, end,
                status=codes[0] if codes else None,
                order_sno=order_sno, phone=phone,
            )
        else:  # 多狀態：各查一次再合併（與桌面版相同）
            stats = {}
            merged, seen = [], set()
            for code in codes:
                s, chunk = api.fetch_all_items(start, end, status=code,
                                               order_sno=order_sno, phone=phone)
                stats = stats or s
                for it in chunk:
                    key = it.get("productOrderProductShelfId") or it.get("orderSNo") or id(it)
                    if key not in seen:
                        seen.add(key)
                        merged.append(it)
            items = merged

        changelog.record(items, shop=api.shop_name)
        self._json({"stats": stats, "items": items})

    def _api_labels(self, body: dict):
        """產生標籤 PDF 並直接回傳給瀏覽器開啟列印。"""
        records = body.get("records") or []
        layout = body.get("layout") or "mac"
        if not records:
            raise StudioAError("沒有要列印的資料。")
        _ensure_reportlab()
        import tempfile
        import labels
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            tmp_path = f.name
        try:
            labels.generate_pdf(records, tmp_path, layout=layout)
            with open(tmp_path, "rb") as f:
                pdf = f.read()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition", "inline; filename=labels.pdf")
        self.send_header("Content-Length", str(len(pdf)))
        self.end_headers()
        self.wfile.write(pdf)

    # ---------------- 靜態檔 ---------------- #
    def _serve_file(self, name: str):
        path = os.path.join(WEB_DIR, name)
        if not os.path.isfile(path):
            self._send(404, "Not Found".encode(), "text/plain")
            return
        with open(path, "rb") as f:
            self._send(200, f.read(), "text/html; charset=utf-8")


def _pick_port() -> int:
    env_port = os.environ.get("PORT")
    if env_port:
        return int(env_port)
    for port in [PORT_PREFERRED] + list(range(PORT_PREFERRED + 1, PORT_PREFERRED + 20)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("找不到可用的連接埠。")


def main():
    # 本機模式才檢查更新（雲端由 git 部署，檔案系統也可能是唯讀的）
    if not SERVER_MODE:
        try:
            from updater import check_and_update
            status, message, _ver = check_and_update(APP_DIR)
            print(f"[更新檢查] {message}")
            if status == "updated":
                os.environ["STUDIOA_JUST_UPDATED"] = "1"
                os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])
                return
        except Exception as e:
            print(f"[更新檢查] 略過（{e}）")

    port = _pick_port()
    server = ThreadingHTTPServer((HOST, port), Handler)
    print(f"StudioA 門市預約管理（網頁版）v{__version__}")
    if SERVER_MODE:
        print(f"伺服器模式：聽 {HOST}:{port}")
    else:
        url = f"http://127.0.0.1:{port}/"
        print(f"已啟動：{url}")
        print("請保持此視窗開啟；要結束請按 Ctrl+C 或直接關閉視窗。")
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已結束。")


if __name__ == "__main__":
    main()
