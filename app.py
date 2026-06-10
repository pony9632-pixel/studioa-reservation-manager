# -*- coding: utf-8 -*-
"""
StudioA 門市預約管理 — 桌面程式 (PySide6)

三個分頁：
  1. 預約總覽：剩餘已預約數、各狀態統計、型號統計、會員統計
  2. 區間查詢：指定日期區間，看新增預約筆數與分佈
  3. 狀態管理：用預約單號查單、變更狀態

執行：  python app.py
相依：  PySide6, requests   （見 requirements.txt）
"""

from __future__ import annotations

import base64
import collections
import datetime as dt
import json
import os
import sys
from functools import partial
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, QThread, Signal, QDate
from PySide6.QtGui import QFont, QAction
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDialog, QVBoxLayout, QHBoxLayout,
    QGridLayout, QFormLayout, QLabel, QLineEdit, QPushButton, QCheckBox,
    QComboBox, QDateEdit, QTableWidget, QTableWidgetItem, QTabWidget,
    QMessageBox, QFrame, QHeaderView, QAbstractItemView, QGroupBox, QSizePolicy,
    QButtonGroup, QInputDialog,
)

import changelog
import client
from client import StudioAClient, StudioAError, STATUS_CODE_TO_NAME
from version import __version__

CONFIG_PATH = Path.home() / ".studioa_reservation_app.json"

# 狀態變更下拉提供的選項（含使用者要求的全部狀態）
CHANGE_STATUS_OPTIONS = [3, 4, 5, 6, 7, 8, 21]  # 對照 client.STATUS_CODE_TO_NAME
# 「未取貨」視為仍佔用的狀態（用於型號/會員統計範圍）
UNPICKED_CODES = {3, 4, 5, 6}


# ====================================================================== #
# 背景執行緒：避免 API 呼叫卡住畫面
# ====================================================================== #
class Worker(QThread):
    ok = Signal(object)
    fail = Signal(str)

    def __init__(self, fn: Callable):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            self.ok.emit(self._fn())
        except StudioAError as e:
            self.fail.emit(str(e))
        except Exception as e:  # 非預期錯誤也別讓程式崩潰
            self.fail.emit(f"發生未預期錯誤：{e}")


def run_async(holder: QWidget, fn: Callable, on_ok: Callable, on_fail: Callable):
    """在背景執行 fn，完成後回主執行緒呼叫 on_ok / on_fail。"""
    w = Worker(fn)
    if not hasattr(holder, "_workers"):
        holder._workers = []
    holder._workers.append(w)

    def _cleanup():
        try:
            holder._workers.remove(w)
        except ValueError:
            pass

    w.ok.connect(on_ok)
    w.fail.connect(on_fail)
    w.finished.connect(_cleanup)
    w.start()
    return w


# ====================================================================== #
# 設定檔（記住帳號/密碼）
# ====================================================================== #
def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg: dict):
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _obscure(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


def _unobscure(s: str) -> str:
    try:
        return base64.b64decode(s.encode("ascii")).decode("utf-8")
    except Exception:
        return ""


# ====================================================================== #
# 登入視窗
# ====================================================================== #
class LoginDialog(QDialog):
    def __init__(self, api: StudioAClient, parent=None):
        super().__init__(parent)
        self.api = api
        self.setWindowTitle(f"登入 — StudioA 預約管理 v{__version__}")
        self.setMinimumWidth(360)

        cfg = load_config()
        layout = QVBoxLayout(self)

        title = QLabel("門市預約後台登入")
        f = title.font(); f.setPointSize(15); f.setBold(True); title.setFont(f)
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        ver_label = QLabel(f"版本 v{__version__}")
        ver_label.setAlignment(Qt.AlignCenter)
        ver_label.setStyleSheet("color:#868e96;")
        layout.addWidget(ver_label)

        form = QFormLayout()
        self.user_edit = QLineEdit(cfg.get("username", ""))
        self.user_edit.setPlaceholderText("帳號（email）")
        self.pwd_edit = QLineEdit(_unobscure(cfg.get("password", "")))
        self.pwd_edit.setPlaceholderText("密碼")
        self.pwd_edit.setEchoMode(QLineEdit.Password)
        form.addRow("帳號", self.user_edit)
        form.addRow("密碼", self.pwd_edit)
        layout.addLayout(form)

        self.remember_user = QCheckBox("記住帳號")
        self.remember_user.setChecked(bool(cfg.get("username")))
        self.remember_pwd = QCheckBox("記住密碼（存在本機，請勿在公用電腦勾選）")
        self.remember_pwd.setChecked(bool(cfg.get("password")))
        layout.addWidget(self.remember_user)
        layout.addWidget(self.remember_pwd)

        self.msg = QLabel("")
        self.msg.setStyleSheet("color:#c0392b;")
        self.msg.setWordWrap(True)
        layout.addWidget(self.msg)

        self.login_btn = QPushButton("登入")
        self.login_btn.setDefault(True)
        self.login_btn.clicked.connect(self.do_login)
        layout.addWidget(self.login_btn)

        self.pwd_edit.returnPressed.connect(self.do_login)

    def do_login(self):
        username = self.user_edit.text().strip()
        password = self.pwd_edit.text()
        self.msg.setText("")
        self.login_btn.setEnabled(False)
        self.login_btn.setText("登入中…")

        def task():
            return self.api.login(username, password)

        def ok(_info):
            cfg = load_config()
            cfg["username"] = username if self.remember_user.isChecked() else ""
            cfg["password"] = _obscure(password) if self.remember_pwd.isChecked() else ""
            save_config(cfg)
            self.accept()

        def fail(err):
            self.login_btn.setEnabled(True)
            self.login_btn.setText("登入")
            self.msg.setText(err)

        run_async(self, task, ok, fail)


# ====================================================================== #
# 共用小工具
# ====================================================================== #
def make_card(title: str, highlight: bool = False) -> tuple[QFrame, QLabel]:
    """回傳 (卡片框, 數值Label)。"""
    frame = QFrame()
    frame.setFrameShape(QFrame.StyledPanel)
    bg = "#1f6feb" if highlight else "#f1f3f5"
    fg = "#ffffff" if highlight else "#212529"
    sub = "#dbe9ff" if highlight else "#868e96"
    frame.setStyleSheet(
        f"QFrame{{background:{bg};border-radius:10px;}}"
        f"QLabel{{background:transparent;}}"
    )
    v = QVBoxLayout(frame)
    v.setContentsMargins(14, 10, 14, 10)
    t = QLabel(title); t.setStyleSheet(f"color:{sub};font-size:13px;")
    val = QLabel("—")
    val.setStyleSheet(f"color:{fg};font-size:26px;font-weight:bold;")
    v.addWidget(t)
    v.addWidget(val)
    return frame, val


def qdate_to_start(d: QDate) -> dt.datetime:
    return dt.datetime(d.year(), d.month(), d.day(), 0, 0, 0)


def qdate_to_end(d: QDate) -> dt.datetime:
    return dt.datetime(d.year(), d.month(), d.day(), 23, 59, 59)


def fill_count_table(table: QTableWidget, counter: collections.Counter, col_title: str):
    rows = counter.most_common()
    table.setSortingEnabled(False)  # 填表時先關排序，避免邊填邊排導致錯位
    table.clear()
    table.setColumnCount(2)
    table.setHorizontalHeaderLabels([col_title, "數量"])
    table.setRowCount(len(rows))
    for i, (name, cnt) in enumerate(rows):
        item_name = QTableWidgetItem(str(name) if name else "（無）")
        item_cnt = QTableWidgetItem()
        item_cnt.setData(Qt.DisplayRole, int(cnt))
        item_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        table.setItem(i, 0, item_name)
        table.setItem(i, 1, item_cnt)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
    table.setSortingEnabled(True)


# ====================================================================== #
# 分頁 1：預約總覽
# ====================================================================== #
class OverviewTab(QWidget):
    def __init__(self, api: StudioAClient, mw: "MainWindow"):
        super().__init__()
        self.api = api
        self.mw = mw
        self.items: list[dict] = []

        root = QVBoxLayout(self)

        # 篩選列
        bar = QHBoxLayout()
        bar.addWidget(QLabel("預約日期區間："))
        self.start_date = QDateEdit(QDate.currentDate().addDays(-180))
        self.end_date = QDateEdit(QDate.currentDate().addDays(180))
        for de in (self.start_date, self.end_date):
            de.setCalendarPopup(True); de.setDisplayFormat("yyyy-MM-dd")
        bar.addWidget(self.start_date)
        bar.addWidget(QLabel("～"))
        bar.addWidget(self.end_date)
        self.refresh_btn = QPushButton("查詢 / 重新整理")
        self.refresh_btn.clicked.connect(self.refresh)
        bar.addWidget(self.refresh_btn)
        bar.addStretch()
        root.addLayout(bar)

        # 統計卡片
        cards = QGridLayout()
        self.card_total = make_card("總數量")
        self.card_reserved = make_card("目前剩餘已預約", highlight=True)
        self.card_alloc = make_card("已配貨")
        self.card_arrival = make_card("已到貨")
        self.card_hold = make_card("保留")
        self.card_pick = make_card("已取貨")
        self.card_abandon = make_card("放棄")
        self.card_cancel = make_card("取消")
        self.card_rate = make_card("取貨率")
        cards_list = [
            self.card_reserved, self.card_total, self.card_alloc, self.card_arrival,
            self.card_hold, self.card_pick, self.card_abandon, self.card_cancel, self.card_rate,
        ]
        for idx, (frame, _) in enumerate(cards_list):
            cards.addWidget(frame, idx // 5, idx % 5)
        root.addLayout(cards)

        # 統計範圍選擇
        scope_bar = QHBoxLayout()
        scope_bar.addWidget(QLabel("型號 / 會員統計範圍："))
        self.scope = QComboBox()
        self.scope.addItem("僅已預約（剩餘）", "reserved")
        self.scope.addItem("未取貨（已預約+配貨+到貨+保留）", "unpicked")
        self.scope.addItem("全部", "all")
        self.scope.currentIndexChanged.connect(self.recompute_tables)
        scope_bar.addWidget(self.scope)
        scope_bar.addStretch()
        root.addLayout(scope_bar)

        # 型號統計 + 會員統計（並排）
        tables = QHBoxLayout()
        box1 = QGroupBox("型號統計")
        v1 = QVBoxLayout(box1)
        self.model_table = QTableWidget(); v1.addWidget(self.model_table)
        box2 = QGroupBox("會員等級統計")
        v2 = QVBoxLayout(box2)
        self.member_table = QTableWidget(); v2.addWidget(self.member_table)
        for t in (self.model_table, self.member_table):
            t.setEditTriggers(QAbstractItemView.NoEditTriggers)
            t.setSortingEnabled(True)
        tables.addWidget(box1, 3)
        tables.addWidget(box2, 2)
        root.addLayout(tables, 1)

    def refresh(self):
        start = qdate_to_start(self.start_date.date())
        end = qdate_to_end(self.end_date.date())
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("讀取中…")
        self.mw.status(f"讀取預約資料中（{start:%Y-%m-%d} ～ {end:%Y-%m-%d}）…")

        def task():
            return self.api.fetch_all_items(start, end)

        def ok(result):
            stats, items = result
            self.items = items
            changelog.record(items)
            self._update_cards(stats)
            self.recompute_tables()
            self.refresh_btn.setEnabled(True)
            self.refresh_btn.setText("查詢 / 重新整理")
            self.mw.status(f"完成：共 {stats.get('totalCount', len(items))} 筆。")

        def fail(err):
            self.refresh_btn.setEnabled(True)
            self.refresh_btn.setText("查詢 / 重新整理")
            self.mw.handle_error(err)

        run_async(self, task, ok, fail)

    def _update_cards(self, stats: dict):
        def setv(card, value):
            card[1].setText(str(value if value is not None else "—"))
        setv(self.card_total, stats.get("totalCount", 0))
        setv(self.card_reserved, stats.get("reservationCount", 0))
        setv(self.card_alloc, stats.get("allocationCount", 0))
        setv(self.card_arrival, stats.get("arrivalCount", 0))
        setv(self.card_hold, stats.get("reserveCount", 0))
        setv(self.card_pick, stats.get("pickCount", 0))
        setv(self.card_abandon, stats.get("abandonCount", 0))
        setv(self.card_cancel, stats.get("cancelCount", 0))
        setv(self.card_rate, stats.get("pickupRate", "—"))

    def recompute_tables(self):
        scope = self.scope.currentData()
        if scope == "reserved":
            items = [it for it in self.items if it.get("status") == 3]
        elif scope == "unpicked":
            items = [it for it in self.items if it.get("status") in UNPICKED_CODES]
        else:
            items = self.items
        model_counter = collections.Counter(it.get("productName") for it in items)
        member_counter = collections.Counter(it.get("userClassName") for it in items)
        fill_count_table(self.model_table, model_counter, "型號")
        fill_count_table(self.member_table, member_counter, "會員等級")


# ====================================================================== #
# 分頁 2：區間查詢（新增預約統計）
# ====================================================================== #
class RangeTab(QWidget):
    def __init__(self, api: StudioAClient, mw: "MainWindow"):
        super().__init__()
        self.api = api
        self.mw = mw
        root = QVBoxLayout(self)

        # 快捷區間
        quick = QHBoxLayout()
        quick.addWidget(QLabel("快速區間："))
        for label, days in [("今天", 0), ("最近7天", 6), ("最近14天", 13), ("最近30天", 29), ("本月", -1)]:
            b = QPushButton(label)
            b.clicked.connect(partial(self.apply_preset, days))
            quick.addWidget(b)
        quick.addStretch()
        root.addLayout(quick)

        # 自訂區間
        bar = QHBoxLayout()
        bar.addWidget(QLabel("預約日期區間："))
        self.start_date = QDateEdit(QDate.currentDate().addDays(-6))
        self.end_date = QDateEdit(QDate.currentDate())
        for de in (self.start_date, self.end_date):
            de.setCalendarPopup(True); de.setDisplayFormat("yyyy-MM-dd")
        bar.addWidget(self.start_date)
        bar.addWidget(QLabel("～"))
        bar.addWidget(self.end_date)
        self.query_btn = QPushButton("查詢")
        self.query_btn.clicked.connect(self.query)
        bar.addWidget(self.query_btn)
        bar.addStretch()
        root.addLayout(bar)

        # 大數字
        self.big = QLabel("—")
        self.big.setAlignment(Qt.AlignCenter)
        self.big.setStyleSheet("font-size:40px;font-weight:bold;color:#1f6feb;padding:10px;")
        root.addWidget(self.big)
        self.sub = QLabel("選擇區間後按「查詢」")
        self.sub.setAlignment(Qt.AlignCenter)
        self.sub.setStyleSheet("color:#868e96;")
        root.addWidget(self.sub)

        # 分佈表（按狀態 / 按日期）
        tables = QHBoxLayout()
        box1 = QGroupBox("各狀態筆數")
        v1 = QVBoxLayout(box1); self.status_table = QTableWidget(); v1.addWidget(self.status_table)
        box2 = QGroupBox("每日新增筆數")
        v2 = QVBoxLayout(box2); self.day_table = QTableWidget(); v2.addWidget(self.day_table)
        for t in (self.status_table, self.day_table):
            t.setEditTriggers(QAbstractItemView.NoEditTriggers)
            t.setSortingEnabled(True)
        tables.addWidget(box1)
        tables.addWidget(box2)
        root.addLayout(tables, 1)

    def apply_preset(self, days: int):
        today = QDate.currentDate()
        if days == -1:  # 本月
            self.start_date.setDate(QDate(today.year(), today.month(), 1))
            self.end_date.setDate(today)
        else:
            self.start_date.setDate(today.addDays(-days))
            self.end_date.setDate(today)
        self.query()

    def query(self):
        start = qdate_to_start(self.start_date.date())
        end = qdate_to_end(self.end_date.date())
        self.query_btn.setEnabled(False); self.query_btn.setText("查詢中…")
        self.mw.status("查詢區間預約中…")

        def task():
            return self.api.fetch_all_items(start, end)

        def ok(result):
            stats, items = result
            changelog.record(items)
            total = stats.get("totalCount", len(items))
            self.big.setText(f"{total} 筆")
            self.sub.setText(f"{start:%Y-%m-%d} ～ {end:%Y-%m-%d} 之間新增的預約")
            # 狀態分佈（用 statusName 較貼近後台顯示）
            status_counter = collections.Counter(it.get("statusName") for it in items)
            fill_count_table(self.status_table, status_counter, "狀態")
            # 每日分佈
            day_counter = collections.Counter()
            for it in items:
                d = self._extract_date(it.get("reservationTimeValue"))
                if d:
                    day_counter[d] += 1
            self._fill_day_table(day_counter)
            self.query_btn.setEnabled(True); self.query_btn.setText("查詢")
            self.mw.status(f"完成：{total} 筆。")

        def fail(err):
            self.query_btn.setEnabled(True); self.query_btn.setText("查詢")
            self.mw.handle_error(err)

        run_async(self, task, ok, fail)

    @staticmethod
    def _extract_date(value) -> Optional[str]:
        if not value or not isinstance(value, str):
            return None
        s = value.strip().replace("/", "-")
        return s[:10] if len(s) >= 10 else None

    def _fill_day_table(self, counter: collections.Counter):
        rows = sorted(counter.items())
        self.day_table.setSortingEnabled(False)
        self.day_table.clear()
        self.day_table.setColumnCount(2)
        self.day_table.setHorizontalHeaderLabels(["日期", "新增筆數"])
        self.day_table.setRowCount(len(rows))
        for i, (day, cnt) in enumerate(rows):
            it0 = QTableWidgetItem(day)
            it1 = QTableWidgetItem(); it1.setData(Qt.DisplayRole, int(cnt))
            it1.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.day_table.setItem(i, 0, it0)
            self.day_table.setItem(i, 1, it1)
        self.day_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.day_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.day_table.setSortingEnabled(True)


# ====================================================================== #
# 分頁 3：狀態管理（查單 + 改狀態）
# ====================================================================== #
def pickup_deadline(it: dict) -> str:
    """預計取機時間：已到貨用 arrivalEndTime、保留用 reserveEndTime，只取日期。"""
    v = (it.get("arrivalEndTime") or it.get("reserveEndTime")
         or it.get("arrivalEndTimeValue") or it.get("reserveEndTimeValue"))
    if not v:
        return ""
    return str(v).replace("/", "-")[:10]


PICKUP_DEADLINE_KEY = "_pickupDeadline"
ACTIVITY_COL_KEY = "_activity"
# 每筆預約帶 reservationActivityId（GUID）；活動「名稱」由
# client.fetch_activities()（reservation-activity/dropdown-list）對應而來。
ACTIVITY_ID_KEY = "reservationActivityId"


# 產品大分類：由型號字串（productName）關鍵字判斷
PRODUCT_CATEGORIES = ["Mac", "iPhone", "iPad", "AirPods"]


def product_category(product_name: Optional[str]) -> str:
    """把型號歸到 Mac / iPhone / iPad / AirPods，其餘為「其他」。"""
    s = (product_name or "").lower()
    if "airpods" in s:
        return "AirPods"
    if "ipad" in s:
        return "iPad"
    if "iphone" in s:
        return "iPhone"
    if "mac" in s:  # MacBook / iMac / Mac mini / Mac Studio / Mac Pro
        return "Mac"
    return "其他"


COLUMNS = [
    ("orderSNo", "預約單號"),
    ("statusName", "狀態"),
    ("subscriberName", "姓名"),
    ("subscriberContactNumber", "電話"),
    (PICKUP_DEADLINE_KEY, "預計取機時間(已到貨/保留)"),
    ("productName", "型號"),
    ("batchNo", "梯次"),
    ("userClassName", "會員等級"),
    ("vipId", "會員代碼"),
    ("reservationTimeValue", "預約時間"),
    ("shopName", "門市"),
]

# 標籤版型（見 labels.py）
LABEL_LAYOUT_LABELS = {
    "mac": "Mac 版（每頁 30 張，小方貼）",
    "iphone": "iPhone 版（每頁 48 張，長條貼）",
}


def _ensure_reportlab():
    """確保 reportlab 可用；缺少時嘗試以目前 Python 自動安裝（首次列印用）。"""
    try:
        import reportlab  # noqa: F401
        return
    except ImportError:
        pass
    import subprocess
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "reportlab",
             "--quiet", "--disable-pip-version-check"],
            check=True,
        )
    except Exception as e:
        raise StudioAError(
            "列印需要 reportlab 元件，但自動安裝失敗。\n"
            f"請在終端機執行：pip3 install reportlab\n（{e}）"
        )
    try:
        import reportlab  # noqa: F401
    except ImportError:
        raise StudioAError("已安裝 reportlab 但仍無法載入，請關閉程式重開再試。")


class StatusTab(QWidget):
    def __init__(self, api: StudioAClient, mw: "MainWindow"):
        super().__init__()
        self.api = api
        self.mw = mw
        self.rows_items: list[dict] = []
        self._all_items: list[dict] = []
        self._cat: str = "全部"
        self._activity_map: dict[str, str] = {}  # reservationActivityId -> 活動名稱
        self._cat_buttons: dict[str, QPushButton] = {}
        root = QVBoxLayout(self)

        # 查詢列 1：依「預約單號」或「電話」查詢
        bar = QHBoxLayout()
        bar.addWidget(QLabel("查詢方式："))
        self.search_type = QComboBox()
        self.search_type.addItem("預約單號", "sno")
        self.search_type.addItem("電話", "phone")
        self.search_type.currentIndexChanged.connect(self._update_placeholder)
        bar.addWidget(self.search_type)
        self.kw_edit = QLineEdit()
        self.kw_edit.returnPressed.connect(self.do_search)
        bar.addWidget(self.kw_edit, 2)
        self.search_btn = QPushButton("查詢")
        self.search_btn.clicked.connect(self.do_search)
        bar.addWidget(self.search_btn)
        root.addLayout(bar)
        self._update_placeholder()

        # 查詢列 2：依狀態查詢（可複選；不需指定日期，自動涵蓋全部）
        bar2 = QHBoxLayout()
        bar2.addWidget(QLabel("或依狀態查詢（可複選）："))
        self._status_checks: dict[int, QCheckBox] = {}
        for code in CHANGE_STATUS_OPTIONS:
            cb = QCheckBox(STATUS_CODE_TO_NAME[code])
            if code in (5, 6):  # 預設勾「已到貨」「保留」（最常用）
                cb.setChecked(True)
            self._status_checks[code] = cb
            bar2.addWidget(cb)
        self.query_btn = QPushButton("查詢")
        self.query_btn.clicked.connect(self.query_by_status)
        bar2.addWidget(self.query_btn)
        bar2.addStretch()
        root.addLayout(bar2)

        # 漏斗篩選（篩目前載入的清單）
        filt = QGroupBox("篩選（縮小目前載入的清單）")
        filt.setStyleSheet("QPushButton:checked{background:#1f6feb;color:white;}")
        fv = QVBoxLayout(filt)

        # 第一層：產品大分類（一排按鈕，單選）
        rowA = QHBoxLayout()
        rowA.addWidget(QLabel("大分類："))
        self.cat_group = QButtonGroup(self)
        self.cat_group.setExclusive(True)
        for name in ["全部"] + PRODUCT_CATEGORIES + ["其他"]:
            b = QPushButton(name)
            b.setCheckable(True)
            if name == "全部":
                b.setChecked(True)
            self.cat_group.addButton(b)
            self._cat_buttons[name] = b
            rowA.addWidget(b)
        self.cat_group.buttonClicked.connect(self._on_cat_clicked)
        rowA.addStretch()
        fv.addLayout(rowA)

        # 第二層：活動 / 會員等級 / 狀態（下拉）＋ 符合筆數
        rowB = QHBoxLayout()
        rowB.addWidget(QLabel("活動："))
        self.f_activity = QComboBox()
        self.f_activity.addItem("全部活動", None)
        self.f_activity.setMinimumWidth(160)
        self.f_activity.currentIndexChanged.connect(self._apply_filters)
        rowB.addWidget(self.f_activity)
        rowB.addWidget(QLabel("梯次："))
        self.f_batch = QComboBox()
        self.f_batch.addItem("全部梯次", None)
        self.f_batch.setMinimumWidth(130)
        self.f_batch.currentIndexChanged.connect(self._apply_filters)
        rowB.addWidget(self.f_batch)
        rowB.addWidget(QLabel("會員等級："))
        self.f_level = QComboBox()
        self.f_level.addItem("全部等級", None)
        self.f_level.currentIndexChanged.connect(self._apply_filters)
        rowB.addWidget(self.f_level)
        rowB.addWidget(QLabel("狀態："))
        self.f_status = QComboBox()
        self.f_status.addItem("全部狀態", None)
        self.f_status.currentIndexChanged.connect(self._apply_filters)
        rowB.addWidget(self.f_status)
        rowB.addStretch()
        self.count_label = QLabel("—")
        self.count_label.setStyleSheet("color:#1f6feb;font-weight:bold;")
        rowB.addWidget(self.count_label)
        fv.addLayout(rowB)

        root.addWidget(filt)

        # 結果表（第 0 欄為勾選框）
        self.table = QTableWidget()
        self.table.setColumnCount(len(COLUMNS) + 1)
        self.table.setHorizontalHeaderLabels(["✓"] + [c[1] for c in COLUMNS])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        root.addWidget(self.table, 1)

        # 改狀態列
        change = QHBoxLayout()
        self.check_all_btn = QPushButton("全選")
        self.check_all_btn.clicked.connect(lambda: self._set_all_checks(True))
        self.uncheck_all_btn = QPushButton("全不選")
        self.uncheck_all_btn.clicked.connect(lambda: self._set_all_checks(False))
        change.addWidget(self.check_all_btn)
        change.addWidget(self.uncheck_all_btn)
        change.addSpacing(16)
        change.addWidget(QLabel("將選取的單據改為："))
        self.new_status = QComboBox()
        for code in CHANGE_STATUS_OPTIONS:
            mark = "（門市標準）" if code in client.SHOP_CHANGEABLE_CODES else ""
            self.new_status.addItem(f"{STATUS_CODE_TO_NAME[code]}{mark}", code)
        change.addWidget(self.new_status)
        self.apply_btn = QPushButton("送出變更")
        self.apply_btn.setStyleSheet("font-weight:bold;")
        self.apply_btn.clicked.connect(self.apply_change)
        change.addWidget(self.apply_btn)
        change.addSpacing(18)
        self.print_btn = QPushButton("🖨 列印標籤")
        self.print_btn.clicked.connect(self.print_labels)
        change.addWidget(self.print_btn)
        change.addStretch()
        self.hint = QLabel("提示：在表格最左欄「✓」勾選要處理的單據（可多選），再按送出變更或列印標籤。門市標準可改「已到貨/保留/已取貨」。")
        self.hint.setStyleSheet("color:#868e96;")
        root.addWidget(self.hint)
        root.addLayout(change)

        # 背景載入「預約活動」清單（id→名稱），供活動篩選與表格顯示
        self._load_activities()

    # ---- 查詢 ---- #
    def _update_placeholder(self):
        if self.search_type.currentData() == "phone":
            self.kw_edit.setPlaceholderText("輸入電話後按 Enter 或「查詢」")
        else:
            self.kw_edit.setPlaceholderText("輸入預約單號後按 Enter 或「查詢」")

    def do_search(self):
        kw = self.kw_edit.text().strip()
        if not kw:
            self.mw.handle_error("請先輸入要查詢的預約單號或電話。")
            return
        self.search_btn.setEnabled(False); self.search_btn.setText("查詢中…")
        if self.search_type.currentData() == "phone":
            self.mw.status(f"查詢電話 {kw} …")
            fn = (lambda: self.api.find_by_phone(kw))
        else:
            self.mw.status(f"查詢單號 {kw} …")
            fn = (lambda: self.api.find_by_order_sno(kw))
        run_async(self, fn, self._on_rows, self._on_fail)

    def query_by_status(self):
        codes = [code for code, cb in self._status_checks.items() if cb.isChecked()]
        start = dt.datetime(2000, 1, 1)
        end = dt.datetime.now() + dt.timedelta(days=3650)
        self.query_btn.setEnabled(False); self.query_btn.setText("查詢中…")
        names = "、".join(STATUS_CODE_TO_NAME[c] for c in codes) if codes else "全部狀態"
        self.mw.status(f"依狀態查詢（{names}）中…")

        def task():
            if not codes:  # 沒勾選 = 查全部狀態
                return self.api.fetch_all_items(start, end)[1]
            merged, seen = [], set()
            for code in codes:  # 各狀態分別查再合併
                for it in self.api.fetch_all_items(start, end, status=code)[1]:
                    key = it.get("productOrderProductShelfId") or it.get("orderSNo") or id(it)
                    if key not in seen:
                        seen.add(key)
                        merged.append(it)
            return merged

        run_async(self, task, self._on_rows, self._on_fail)

    def _on_rows(self, items):
        self.search_btn.setEnabled(True); self.search_btn.setText("查詢")
        self.query_btn.setEnabled(True); self.query_btn.setText("查詢")
        self._all_items = items
        changelog.record(items)
        self._populate_filter_options(items)
        self._apply_filters()
        if not items:
            self.mw.status("查無資料。")
        else:
            self.mw.status(f"找到 {len(items)} 筆（可用下方篩選縮小）。")

    # ---- 漏斗篩選 ---- #
    def _on_cat_clicked(self, button):
        self._cat = button.text()
        self._apply_filters()

    def _activity_label(self, aid) -> str:
        """活動 id → 顯示名稱；沒對到名稱時退而顯示 id 片段。"""
        if not aid:
            return ""
        return self._activity_map.get(aid) or f"活動 {str(aid)[:8]}…"

    def _load_activities(self):
        """背景抓『預約活動』清單，建立 id→名稱對照。容錯：失敗就用 id 片段顯示。"""
        def task():
            return self.api.fetch_activities()

        def ok(lst):
            self._activity_map = {
                a.get("id"): (a.get("name") or "").strip()
                for a in (lst or []) if a.get("id")
            }
            if self._all_items:  # 名稱晚到：重整活動下拉與表格
                self._populate_filter_options(self._all_items)
                self._apply_filters()

        def fail(_err):
            pass  # 抓不到活動名稱不影響其他功能

        run_async(self, task, ok, fail)

    def _populate_filter_options(self, items):
        def fill(combo, all_label, values):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(all_label, None)
            for v in values:
                combo.addItem(str(v), v)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)

        # 活動：用本批資料出現過的 reservationActivityId，名稱來自活動清單 API
        seen, act_ids = set(), []
        for it in items:
            aid = it.get(ACTIVITY_ID_KEY)
            if aid and aid not in seen:
                seen.add(aid)
                act_ids.append(aid)
        act_ids.sort(key=self._activity_label)
        self.f_activity.blockSignals(True)
        self.f_activity.clear()
        self.f_activity.addItem("全部活動", None)
        for aid in act_ids:
            self.f_activity.addItem(self._activity_label(aid), aid)
        self.f_activity.setCurrentIndex(0)
        self.f_activity.setEnabled(bool(act_ids))
        self.f_activity.blockSignals(False)

        batches = sorted({it.get("batchNo") for it in items if it.get("batchNo")})
        fill(self.f_batch, "全部梯次", batches)
        levels = sorted({it.get("userClassName") for it in items if it.get("userClassName")})
        fill(self.f_level, "全部等級", levels)
        statuses = sorted({it.get("statusName") for it in items if it.get("statusName")})
        fill(self.f_status, "全部狀態", statuses)

        self._cat = "全部"
        btn = self._cat_buttons.get("全部")
        if btn:
            self.cat_group.blockSignals(True)
            btn.setChecked(True)
            self.cat_group.blockSignals(False)

    def _apply_filters(self):
        cat = self._cat
        act = self.f_activity.currentData()
        bat = self.f_batch.currentData()
        lvl = self.f_level.currentData()
        sta = self.f_status.currentData()
        out = []
        for it in self._all_items:
            if cat and cat != "全部" and product_category(it.get("productName")) != cat:
                continue
            if act is not None and it.get(ACTIVITY_ID_KEY) != act:
                continue
            if bat is not None and it.get("batchNo") != bat:
                continue
            if lvl is not None and it.get("userClassName") != lvl:
                continue
            if sta is not None and it.get("statusName") != sta:
                continue
            out.append(it)
        self.rows_items = out
        self._render_rows(out)
        self.count_label.setText(f"符合 {len(out)} / 共 {len(self._all_items)} 筆")

    def _render_rows(self, items):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(items))
        for r, it in enumerate(items):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            chk.setCheckState(Qt.Unchecked)
            chk.setData(Qt.UserRole, r)  # 對應 rows_items 的索引（排序後仍正確）
            chk.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, 0, chk)
            for c, (key, _title) in enumerate(COLUMNS):
                if key == PICKUP_DEADLINE_KEY:
                    val = pickup_deadline(it)
                else:
                    val = it.get(key)
                self.table.setItem(r, c + 1, QTableWidgetItem("" if val is None else str(val)))
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()

    def _on_fail(self, err):
        self.search_btn.setEnabled(True); self.search_btn.setText("查詢")
        self.query_btn.setEnabled(True); self.query_btn.setText("查詢")
        self.mw.handle_error(err)

    # ---- 改狀態 ---- #
    def _row_to_item(self, view_row: int) -> Optional[dict]:
        it0 = self.table.item(view_row, 0)
        if it0 is None:
            return None
        idx = it0.data(Qt.UserRole)
        if isinstance(idx, int) and 0 <= idx < len(self.rows_items):
            return self.rows_items[idx]
        return None

    def _selected_items(self) -> list[dict]:
        """以「勾選」為主；若一個都沒勾，退而採用反白選取的列（沿用舊習慣）。"""
        checked = []
        for row in range(self.table.rowCount()):
            it0 = self.table.item(row, 0)
            if it0 is not None and it0.checkState() == Qt.Checked:
                rec = self._row_to_item(row)
                if rec is not None:
                    checked.append(rec)
        if checked:
            return checked
        out = []
        for row in sorted({idx.row() for idx in self.table.selectionModel().selectedRows()}):
            rec = self._row_to_item(row)
            if rec is not None:
                out.append(rec)
        return out

    def _set_all_checks(self, checked: bool):
        state = Qt.Checked if checked else Qt.Unchecked
        for row in range(self.table.rowCount()):
            it0 = self.table.item(row, 0)
            if it0 is not None:
                it0.setCheckState(state)

    def apply_change(self):
        selected = self._selected_items()
        if not selected:
            self.mw.handle_error("請先在表格中選取要變更的單據（可多選）。")
            return
        code = self.new_status.currentData()
        name = STATUS_CODE_TO_NAME[code]
        shelf_ids = [it.get("productOrderProductShelfId") for it in selected if it.get("productOrderProductShelfId")]
        if not shelf_ids:
            self.mw.handle_error("選取的資料缺少可變更的 ID。")
            return

        confirm = QMessageBox.question(
            self, "確認變更",
            f"確定要把選取的 {len(shelf_ids)} 筆預約狀態改為「{name}」嗎？\n此操作會直接更新後台資料。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        self.apply_btn.setEnabled(False); self.apply_btn.setText("送出中…")
        self.mw.status("送出狀態變更…")

        def ok(msg):
            self.apply_btn.setEnabled(True); self.apply_btn.setText("送出變更")
            QMessageBox.information(self, "完成", msg)
            self.mw.status(msg)
            # 變更後自動重查，讓畫面顯示最新狀態
            if self.kw_edit.text().strip():
                self.do_search()
            else:
                self.query_by_status()

        def fail(err):
            self.apply_btn.setEnabled(True); self.apply_btn.setText("送出變更")
            self.mw.handle_error(f"變更失敗：{err}")

        run_async(self, lambda: self.api.update_status(shelf_ids, code), ok, fail)

    # ---- 列印標籤 ---- #
    def _item_to_label_record(self, it: dict) -> dict:
        return {
            "預約單號": it.get("orderSNo") or "",
            "會員代碼": it.get("vipId") or "",
            "姓名": it.get("subscriberName") or "",
            "手機號碼": it.get("subscriberContactNumber") or "",
            "梯次": it.get("batchNo") or "",
            "預約產品": it.get("productName") or "",
            "預計取機時間": pickup_deadline(it),
        }

    def _pick_layout(self) -> Optional[str]:
        items = [LABEL_LAYOUT_LABELS["mac"], LABEL_LAYOUT_LABELS["iphone"]]
        choice, ok = QInputDialog.getItem(
            self, "選擇標籤版型", "要用哪種標籤紙？", items, 0, False)
        if not ok:
            return None
        return "mac" if choice.startswith("Mac") else "iphone"

    def print_labels(self):
        selected = self._selected_items()
        if not selected:
            self.mw.handle_error("請先在表格中選取要列印的單據（可多選）。")
            return
        # 提醒：選到非「已到貨」(5) 的列
        not_arrived = [it for it in selected if it.get("status") != 5]
        if not_arrived:
            confirm = QMessageBox.question(
                self, "確認列印",
                f"選取的 {len(selected)} 筆中有 {len(not_arrived)} 筆不是「已到貨」。\n仍要列印標籤嗎？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if confirm != QMessageBox.Yes:
                return

        layout = self._pick_layout()
        if not layout:
            return

        records = [self._item_to_label_record(it) for it in selected]
        out_path = os.path.join(
            os.path.expanduser("~/Desktop"),
            f"標籤_{dt.datetime.now():%Y%m%d_%H%M%S}.pdf",
        )
        self.print_btn.setEnabled(False); self.print_btn.setText("產生中…")
        self.mw.status("產生標籤 PDF（首次可能需安裝列印元件）…")

        def task():
            _ensure_reportlab()
            import labels
            pages = labels.generate_pdf(records, out_path, layout=layout)
            return (out_path, pages)

        def ok(result):
            path, pages = result
            self.print_btn.setEnabled(True); self.print_btn.setText("🖨 列印標籤")
            os.system(f'open "{path}"')
            self.mw.status(f"已產生 {len(records)} 張標籤（{pages} 頁）。")
            QMessageBox.information(
                self, "完成",
                f"已產生 {len(records)} 張標籤，共 {pages} 頁：\n{path}\n\n"
                "已自動開啟，請按 Cmd+P 列印。",
            )

        def fail(err):
            self.print_btn.setEnabled(True); self.print_btn.setText("🖨 列印標籤")
            self.mw.handle_error(f"產生標籤失敗：{err}")

        run_async(self, task, ok, fail)


# ====================================================================== #
# 分頁 4：變更紀錄（狀態變更偵測）
# ====================================================================== #
class ChangeLogTab(QWidget):
    def __init__(self, mw: "MainWindow"):
        super().__init__()
        self.mw = mw
        root = QVBoxLayout(self)

        info = QLabel(
            "此頁用「快照比對」偵測狀態變更：App 每次載入資料時，發現某筆狀態和上次不同就記一筆"
            "（含後台那邊直接改的，例如放棄／取消）。\n"
            "註：從開始使用本功能後才會累積；「偵測時間」是 App 發現的時間（非實際變更時間）；"
            "後台未提供「誰改的」，故不顯示操作者。"
        )
        info.setStyleSheet("color:#868e96;")
        info.setWordWrap(True)
        root.addWidget(info)

        bar = QHBoxLayout()
        self.refresh_btn = QPushButton("重新整理")
        self.refresh_btn.clicked.connect(self.reload)
        bar.addWidget(self.refresh_btn)
        bar.addWidget(QLabel("篩新狀態："))
        self.f_status = QComboBox()
        self.f_status.addItem("全部", None)
        for code in CHANGE_STATUS_OPTIONS:
            self.f_status.addItem(STATUS_CODE_TO_NAME[code], STATUS_CODE_TO_NAME[code])
        self.f_status.currentIndexChanged.connect(self.reload)
        bar.addWidget(self.f_status)
        bar.addStretch()
        self.count_label = QLabel("—")
        self.count_label.setStyleSheet("color:#1f6feb;font-weight:bold;")
        bar.addWidget(self.count_label)
        root.addLayout(bar)

        cols = ["偵測時間", "預約單號", "姓名", "型號", "原狀態", "新狀態"]
        self.table = QTableWidget()
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        root.addWidget(self.table, 1)

    def reload(self):
        flt = self.f_status.currentData()
        rows = list(reversed(changelog.all_changes()))  # 最新在上
        if flt:
            rows = [r for r in rows if flt in (r.get("newStatusName") or "")]
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [r.get("detectedAt"), r.get("orderSNo"), r.get("subscriberName"),
                    r.get("productName"), r.get("oldStatusName"), r.get("newStatusName")]
            for c, v in enumerate(vals):
                self.table.setItem(i, c, QTableWidgetItem("" if v is None else str(v)))
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()
        self.count_label.setText(f"共 {len(rows)} 筆變更")

    def showEvent(self, event):
        super().showEvent(event)
        self.reload()


# ====================================================================== #
# 主視窗
# ====================================================================== #
class MainWindow(QMainWindow):
    def __init__(self, api: StudioAClient):
        super().__init__()
        self.api = api
        self.setWindowTitle(f"StudioA 門市預約管理 v{__version__} — {api.shop_name or ''}")
        self.resize(1150, 760)

        tabs = QTabWidget()
        self.overview = OverviewTab(api, self)
        self.range = RangeTab(api, self)
        self.status_tab = StatusTab(api, self)
        self.changelog_tab = ChangeLogTab(self)
        tabs.addTab(self.overview, "預約總覽")
        tabs.addTab(self.range, "區間查詢")
        tabs.addTab(self.status_tab, "狀態管理")
        tabs.addTab(self.changelog_tab, "變更紀錄")
        self.setCentralWidget(tabs)

        self.statusBar().showMessage(f"已登入：{api.shop_name}（{api.user_name}）")

        # 選單：重新登入
        m = self.menuBar().addMenu("帳號")
        relogin = QAction("重新登入", self)
        relogin.triggered.connect(self.relogin)
        m.addAction(relogin)

        # 啟動時自動載入總覽
        self.overview.refresh()

    def status(self, text: str):
        self.statusBar().showMessage(text)

    def handle_error(self, message: str):
        if "未授權" in message or "重新登入" in message:
            QMessageBox.warning(self, "需要重新登入", message)
            self.relogin()
        else:
            QMessageBox.critical(self, "錯誤", message)
        self.status("發生錯誤。")

    def relogin(self):
        dlg = LoginDialog(self.api, self)
        if dlg.exec() == QDialog.Accepted:
            self.setWindowTitle(f"StudioA 門市預約管理 v{__version__} — {self.api.shop_name or ''}")
            self.status(f"已重新登入：{self.api.shop_name}")


def main():
    app_dir = os.path.dirname(os.path.abspath(__file__))

    # 開啟前先檢查更新（容錯：連不上/未設定都會直接開啟）
    try:
        from updater import check_and_update
        status, message, _ver = check_and_update(app_dir)
        print(f"[更新檢查] {message}")
        if status == "updated":
            # 已套用新版，用新程式碼重新啟動自己
            os.environ["STUDIOA_JUST_UPDATED"] = "1"
            os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])
            return
    except Exception as e:  # 更新流程絕不可擋住開啟
        print(f"[更新檢查] 略過（{e}）")

    app = QApplication(sys.argv)
    app.setApplicationName("StudioA 預約管理")

    api = StudioAClient()
    login = LoginDialog(api)
    if login.exec() != QDialog.Accepted:
        sys.exit(0)

    win = MainWindow(api)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
