# -*- coding: utf-8 -*-
"""介面主題（QSS）。

比照使用者設計：深色左側欄 + 淺灰底白圓角卡片 + 藍色強調 + Noto Sans TC。
字體找不到 Noto Sans TC 時自動退回 PingFang TC（macOS 內建，外觀相近）。
"""

FONT_STACK = '"Noto Sans TC", "PingFang TC", "Heiti TC", "Helvetica Neue", sans-serif'
BLUE = "#2f6bf0"

QSS = f"""
* {{ font-family: {FONT_STACK}; }}
QMainWindow, QDialog {{ background: #f4f6fa; }}
QWidget#contentArea {{ background: #f4f6fa; }}
QLabel {{ color: #20283a; background: transparent; }}
QToolTip {{ background: #20283a; color: #fff; border: none; padding: 4px 8px; }}

QGroupBox {{
  background: #ffffff; border: 1px solid #e9edf4; border-radius: 14px;
  margin-top: 16px; padding: 14px 14px 12px 14px; font-size: 14px; font-weight: 500;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 14px; top: 3px; padding: 0 4px; color: #79839a; }}

QPushButton {{
  background: #ffffff; color: #20283a; border: 1px solid #d7deea;
  border-radius: 10px; padding: 7px 14px;
}}
QPushButton:hover {{ background: #eef2fb; }}
QPushButton:pressed {{ background: #e3e9f5; }}
QPushButton:disabled {{ color: #aab2c4; background: #f4f6fa; }}
QPushButton:checked {{ background: {BLUE}; color: #ffffff; border-color: {BLUE}; }}
QPushButton#primary {{ background: {BLUE}; color: #ffffff; border: none; font-weight: 500; }}
QPushButton#primary:hover {{ background: #2356cc; }}
QPushButton#primary:pressed {{ background: #1f49ad; }}
QPushButton#primary:disabled {{ background: #9bb6f4; color: #eef2fb; }}

QLineEdit, QComboBox, QDateEdit, QAbstractSpinBox {{
  background: #ffffff; color: #20283a; border: 1px solid #d7deea;
  border-radius: 10px; padding: 6px 10px; min-height: 18px; selection-background-color: #dbe6ff;
}}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus {{ border-color: {BLUE}; }}
QComboBox::drop-down, QDateEdit::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
  background: #fff; border: 1px solid #e9edf4; selection-background-color: #e9f0ff;
  selection-color: #20283a; outline: none;
}}
QCheckBox {{ spacing: 6px; color: #20283a; }}

QTableWidget {{
  background: #ffffff; border: 1px solid #e9edf4; border-radius: 12px;
  gridline-color: #f0f3f9; outline: none;
}}
QTableWidget::item {{ padding: 5px 6px; color: #20283a; }}
QTableWidget::item:selected {{ background: #e9f0ff; color: #20283a; }}
QHeaderView::section {{
  background: #f6f8fc; color: #79839a; border: none;
  border-bottom: 1px solid #e9edf4; padding: 8px 6px; font-weight: 500;
}}
QTableCornerButton::section {{ background: #f6f8fc; border: none; }}

QProgressBar#statbar {{ background: #eef2f8; border: none; border-radius: 4px; }}
QProgressBar#statbar::chunk {{ background: {BLUE}; border-radius: 4px; }}

QTabWidget::pane {{ border: none; }}
QStatusBar {{ background: #ffffff; color: #79839a; border-top: 1px solid #e9edf4; }}
QStatusBar::item {{ border: none; }}
QMenuBar {{ background: #f4f6fa; color: #20283a; }}
QMenuBar::item:selected {{ background: #e9f0ff; }}
QMenu {{ background: #fff; border: 1px solid #e9edf4; }}
QMenu::item:selected {{ background: #e9f0ff; }}

QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #cfd6e4; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #b7c0d2; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #cfd6e4; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

QWidget#sidebar {{ background: #1c2540; }}
QListWidget#nav {{ background: transparent; border: none; outline: none; font-size: 14px; }}
QListWidget#nav::item {{ color: #b9c2db; padding: 11px 14px; border-radius: 10px; margin: 2px 10px; }}
QListWidget#nav::item:hover {{ background: rgba(255,255,255,0.07); color: #ffffff; }}
QListWidget#nav::item:selected {{ background: {BLUE}; color: #ffffff; }}
"""


def apply_theme(app):
    app.setStyleSheet(QSS)
