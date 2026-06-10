# -*- coding: utf-8 -*-
"""標籤 PDF 產生模組（整合自「標籤輸出」工具，移除 Excel/GUI，只保留渲染）。

對外：generate_pdf(records, output_path, layout="mac"|"iphone", progress_cb=None)

records: list[dict]，每筆可含下列鍵（缺的會顯示「—」或留白）：
    預約單號 / 會員代碼 / 姓名 / 手機號碼 / 梯次 / 預約產品 / 預計取機時間

layout:
    "mac"    → A4，5×6＝每頁 30 張小方貼
    "iphone" → A4，2×24＝每頁 48 張長條貼（72×12mm，含裁切虛線）

僅依賴 reportlab；中文用系統 CJK 字型，找不到則退回 Helvetica。
"""
from __future__ import annotations

import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.graphics.barcode import code128

# ──────────────────────────────────────────────────────────────────────────────
# 中文字型（一般 + 粗體）
# ──────────────────────────────────────────────────────────────────────────────
_FONT_NAME = "CJKFont"
_FONT_BOLD_NAME = "CJKFontBold"

_FONT_CANDIDATES = [
    ("/System/Library/Fonts/PingFang.ttc", True, 0),
    ("/System/Library/Fonts/STHeiti Medium.ttc", True, 0),
    ("/System/Library/Fonts/STHeiti Light.ttc", True, 0),
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", True, 0),
    (os.path.expanduser("~/Library/Fonts/NotoSansCJK-Regular.ttc"), True, 0),
    ("/Library/Fonts/Arial Unicode MS.ttf", False, 0),
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", False, 0),
]
_FONT_BOLD_CANDIDATES = [
    ("/System/Library/Fonts/PingFang.ttc", True, 5),  # Semibold
    ("/System/Library/Fonts/PingFang.ttc", True, 4),  # Medium
    ("/System/Library/Fonts/STHeiti Medium.ttc", True, 0),
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", True, 0),
    (os.path.expanduser("~/Library/Fonts/NotoSansCJK-Bold.ttc"), True, 0),
    ("/Library/Fonts/Arial Unicode MS.ttf", False, 0),
]


def _register(name: str, candidates: list) -> bool:
    for path, is_ttc, idx in candidates:
        if not os.path.exists(path):
            continue
        try:
            font = TTFont(name, path, subfontIndex=idx) if is_ttc else TTFont(name, path)
            pdfmetrics.registerFont(font)
            return True
        except Exception:
            continue
    return False


FONT = _FONT_NAME if _register(_FONT_NAME, _FONT_CANDIDATES) else "Helvetica"
FONT_BOLD = _FONT_BOLD_NAME if _register(_FONT_BOLD_NAME, _FONT_BOLD_CANDIDATES) else "Helvetica-Bold"


# ──────────────────────────────────────────────────────────────────────────────
# 共用小工具
# ──────────────────────────────────────────────────────────────────────────────
def _safe_str(val) -> str:
    s = str(val).strip()
    return "" if s in ("", "nan", "None", "NaT") else s


def _wrap_text(c, text: str, font: str, size: float, max_w: float, max_lines: int = 4) -> list:
    """字元級換行，最多 max_lines 行，末行超寬則補「…」。"""
    lines: list = []
    cur = ""
    for char in text:
        if c.stringWidth(cur + char, font, size) <= max_w:
            cur += char
        else:
            if cur:
                lines.append(cur)
            cur = char
            if len(lines) >= max_lines - 1:
                rest = text[text.find(char):]
                for ch in rest[1:]:
                    if c.stringWidth(cur + ch, font, size) <= max_w:
                        cur += ch
                    else:
                        break
                while len(cur) > 2 and c.stringWidth(cur + "…", font, size) > max_w:
                    cur = cur[:-2]
                cur += "…"
                break
    if cur:
        lines.append(cur)
    return lines[:max_lines]


# ──────────────────────────────────────────────────────────────────────────────
# Mac 版型：5×6＝30/頁 小方貼
# ──────────────────────────────────────────────────────────────────────────────
def _draw_barcode(c, val: str, ix, iw, cy, bc_h_mm=9, bar_w=0.75, font_sz=4) -> float:
    """繪製 Code128 條碼，回傳新的 cur_y。"""
    bc_h = bc_h_mm * mm
    try:
        bc = code128.Code128(val, barWidth=bar_w, barHeight=bc_h, humanReadable=True,
                             fontSize=font_sz, fontName="Helvetica")
        scale = min(iw / bc.width, 1.0)
        th = (bc_h + font_sz + 2) * scale
        cy -= th
        bx = ix + (iw - bc.width * scale) / 2
        c.saveState()
        c.translate(bx, cy)
        c.scale(scale, scale)
        bc.drawOn(c, 0, 0)
        c.restoreState()
    except Exception:
        c.setFont("Helvetica", font_sz + 1)
        cy -= font_sz + 4
        c.drawString(ix, cy, val)
    return cy


def _draw_cell_mac(c, x, y, w, h, record: dict):
    pad = 1.8 * mm
    inner_x = x + pad
    inner_w = w - 2 * pad

    c.setFillColor(colors.HexColor("#FFFEF2"))
    c.rect(x, y, w, h, stroke=0, fill=1)
    bar_h = 3 * mm
    c.setFillColor(colors.HexColor("#3C7BC6"))
    c.rect(x, y + h - bar_h, w, bar_h, stroke=0, fill=1)
    c.setStrokeColor(colors.HexColor("#AAAAAA"))
    c.setLineWidth(0.4)
    c.rect(x, y, w, h, stroke=1, fill=0)
    c.setFillColor(colors.black)
    cur_y = y + h - bar_h - pad

    fs = 6.5
    lh = fs + 2.0

    ono = _safe_str(record.get("預約單號", ""))
    if ono:
        cur_y = _draw_barcode(c, ono, inner_x, inner_w, cur_y, bc_h_mm=9, bar_w=0.70, font_sz=4)
        cur_y -= 0.8 * mm
    mno = _safe_str(record.get("會員代碼", ""))
    if mno:
        cur_y = _draw_barcode(c, mno, inner_x, inner_w, cur_y, bc_h_mm=7, bar_w=0.65, font_sz=4)
        cur_y -= 0.8 * mm

    c.setFont(FONT, fs)
    for label, key in (("姓名", "姓名"), ("手機", "手機號碼"), ("梯次", "梯次")):
        val = _safe_str(record.get(key, ""))
        txt = f"{label}：{val or '—'}"
        while c.stringWidth(txt, FONT, fs) > inner_w and len(txt) > 5:
            txt = txt[:-2] + "…"
        cur_y -= lh
        if cur_y >= y + pad:
            c.drawString(inner_x, cur_y, txt)

    pv = _safe_str(record.get("預約產品", ""))
    if pv.startswith("預約｜"):
        pv = pv[3:]
    pfull = f"產品：{pv or '—'}"
    for line in _wrap_text(c, pfull, FONT, fs, inner_w, max_lines=4):
        cur_y -= lh
        if cur_y < y + pad:
            break
        c.drawString(inner_x, cur_y, line)

    pickup = _safe_str(record.get("預計取機時間", ""))
    if pickup:
        c.setFont(FONT, fs + 0.5)
        txt = f"取機：{pickup}"
        while c.stringWidth(txt, FONT, fs + 0.5) > inner_w and len(txt) > 5:
            txt = txt[:-2] + "…"
        cur_y -= lh + 0.5
        if cur_y >= y + pad:
            c.drawString(inner_x, cur_y, txt)
        c.setFont(FONT, fs)
    c.setFillColor(colors.black)


# ──────────────────────────────────────────────────────────────────────────────
# iPhone 版型：2×24＝48/頁 長條貼（72×12mm）
# ──────────────────────────────────────────────────────────────────────────────
def _draw_cell_iphone(c, x, y, w, h, record: dict):
    c.setFillColor(colors.HexColor("#FFFFFF"))
    c.rect(x, y, w, h, stroke=0, fill=1)
    c.setStrokeColor(colors.HexColor("#BBBBBB"))
    c.setLineWidth(0.3)
    c.rect(x, y, w, h, stroke=1, fill=0)

    left_bar = 2 * mm
    c.setFillColor(colors.HexColor("#3C7BC6"))
    c.rect(x, y, left_bar, h, stroke=0, fill=1)

    ono = _safe_str(record.get("預約單號", ""))
    mno = _safe_str(record.get("會員代碼", ""))
    nm = _safe_str(record.get("姓名", ""))
    ph = _safe_str(record.get("手機號碼", ""))
    ph3 = ph[-3:] if len(ph) >= 3 else ph
    pv = _safe_str(record.get("預約產品", ""))
    if pv.startswith("預約｜"):
        pv = pv[3:]
    pickup = _safe_str(record.get("預計取機時間", ""))

    bc_x = x + left_bar + 0.5 * mm
    bc_area_w = 40 * mm
    bot_pad = 1.7 * mm
    top_pad = 0.3 * mm
    mid_gap = 0.5 * mm
    bc_h_each = (h - bot_pad - top_pad - mid_gap) / 2

    def _draw_bc(val, by_bottom, bh, bw_each):
        try:
            bc = code128.Code128(val, barWidth=0.42, barHeight=bh * 0.65, humanReadable=True,
                                 fontSize=2.5, fontName="Helvetica")
            scale = min(bw_each / bc.width, 1.0)
            c.saveState()
            c.translate(bc_x, by_bottom)
            c.scale(scale, scale)
            bc.drawOn(c, 0, 0)
            c.restoreState()
        except Exception:
            c.setFont("Helvetica", 3)
            c.setFillColor(colors.black)
            c.drawString(bc_x, by_bottom + 1, val[:20])

    bc2_bot = y + bot_pad
    bc1_bot = bc2_bot + bc_h_each + mid_gap
    if ono:
        _draw_bc(ono, bc1_bot, bc_h_each, bc_area_w)
    if mno:
        _draw_bc(mno, bc2_bot, bc_h_each, bc_area_w)

    txt_x = x + left_bar + bc_area_w + 1.5 * mm
    txt_w = w - (txt_x - x) - 1.2 * mm
    fs = 4.5
    lh = fs + 1.5
    tc = _safe_str(record.get("梯次", ""))

    def _trunc(txt, max_w, fz=None, font=None):
        fz = fz or fs
        font = font or FONT
        c.setFont(font, fz)
        while c.stringWidth(txt, font, fz) > max_w and len(txt) > 2:
            txt = txt[:-2] + "…"
        return txt

    fz_pv = fs - 1.2
    lh_pv = fz_pv + 1.2
    fs_nm = fs + 0.8

    tc_txt = _trunc(tc, txt_w, fs - 0.2)
    ph_txt = f"*{ph3}"
    ph_w = c.stringWidth(ph_txt, FONT_BOLD, fs_nm)
    nm_txt = _trunc(nm, txt_w / 2 - 2, fs_nm, FONT_BOLD)
    c.setFont(FONT_BOLD, fs_nm)
    pv_lines = _wrap_text(c, pv, FONT, fz_pv, txt_w, max_lines=3)

    n_top = 2
    n_pv = len(pv_lines)
    n_bot = 1 if pickup else 0
    total_txt = (n_top - 1) * lh + n_pv * lh_pv + n_bot * lh_pv
    cur_y = y + h / 2 + total_txt / 2

    c.setFont(FONT, fs - 0.2)
    c.setFillColor(colors.HexColor("#444444"))
    c.drawString(txt_x, cur_y, tc_txt)
    cur_y -= lh

    c.setFont(FONT_BOLD, fs_nm)
    c.setFillColor(colors.black)
    c.drawString(txt_x, cur_y, nm_txt)
    ph_x = txt_x + (txt_w - ph_w) / 2
    c.setFont(FONT_BOLD, fs_nm)
    c.setFillColor(colors.HexColor("#444444"))
    c.drawString(ph_x, cur_y, ph_txt)
    cur_y -= lh_pv

    for line in pv_lines:
        if cur_y < y + 0.3 * mm:
            break
        c.setFont(FONT, fz_pv)
        c.setFillColor(colors.HexColor("#333333"))
        c.drawString(txt_x, cur_y, line)
        cur_y -= lh_pv

    if pickup and cur_y >= y + 0.3 * mm:
        c.setFont(FONT, fz_pv)
        c.setFillColor(colors.HexColor("#C0392B"))
        c.drawString(txt_x, cur_y, f"取機:{pickup}")
    c.setFillColor(colors.black)


# ──────────────────────────────────────────────────────────────────────────────
# 版型表 + 對外產生函式
# ──────────────────────────────────────────────────────────────────────────────
LAYOUTS = {
    "mac": {"label": "Mac 版（每頁 30 張，小方貼）", "per_page": 30},
    "iphone": {"label": "iPhone 版（每頁 48 張，長條貼）", "per_page": 48},
}


def generate_pdf(records: list, output_path: str, layout: str = "mac", progress_cb=None) -> int:
    """產生標籤 PDF，回傳頁數。"""
    page_w, page_h = A4
    c = pdf_canvas.Canvas(output_path, pagesize=A4)
    total = len(records)

    if layout == "iphone":
        cols, rows = 2, 24
        cell_w, cell_h = 72 * mm, 12 * mm
        mx = (page_w - cols * cell_w) / 2
        my = (page_h - rows * cell_h) / 2
        drawer = _draw_cell_iphone
        cut_lines = True
    else:  # mac
        cols, rows = 5, 6
        mx = my = 5 * mm
        cell_w = (page_w - 2 * mx) / cols
        cell_h = (page_h - 2 * my) / rows
        drawer = _draw_cell_mac
        cut_lines = False

    per_page = cols * rows
    for i, record in enumerate(records):
        if progress_cb:
            progress_cb(i, total)
        pos = i % per_page
        if pos == 0 and i > 0:
            c.showPage()
        col_i = pos % cols
        row_i = pos // cols
        cx = mx + col_i * cell_w
        cy = page_h - my - (row_i + 1) * cell_h
        drawer(c, cx, cy, cell_w, cell_h, record)

        if cut_lines:
            c.setStrokeColor(colors.HexColor("#CCCCCC"))
            c.setLineWidth(0.3)
            c.setDash(3, 3)
            if col_i < cols - 1:
                lx = mx + (col_i + 1) * cell_w
                c.line(lx, my, lx, page_h - my)
            if row_i < rows - 1:
                ly = page_h - my - (row_i + 1) * cell_h
                c.line(mx, ly, page_w - mx, ly)
            c.setDash()

    c.save()
    if progress_cb:
        progress_cb(total, total)
    return (total + per_page - 1) // per_page if total else 0
