# -*- coding: utf-8 -*-
"""狀態變更偵測（本機快照比對）。

後台沒有開放變更歷史 API，因此改用「快照比對」：
每次 App 載入預約資料時，比對每筆的目前狀態與上次記住的狀態，
不同就記一筆變更（含後台那邊直接改的）。

限制：
  - 只在 App 有載入資料時才會偵測；兩次之間的中間狀態看不到。
  - 「偵測時間」是 App 發現的時間，非實際變更時間。
  - 看不出是「誰」改的（後台未提供）。

紀錄存在本機：~/.studioa_reservation_changelog.json
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

PATH = Path.home() / ".studioa_reservation_changelog.json"
KEY = "productOrderProductShelfId"
MAX_CHANGES = 10000  # 上限，避免無限成長


def _path(shop: str | None) -> Path:
    """shop=None 用預設檔（桌面版單店）；多人網頁版按門市分檔，避免互相混在一起。"""
    if not shop:
        return PATH
    safe = "".join(ch for ch in shop if ch.isalnum())[:40] or "shop"
    return Path.home() / f".studioa_reservation_changelog_{safe}.json"


def _load(shop: str | None = None) -> dict:
    try:
        data = json.loads(_path(shop).read_text(encoding="utf-8"))
        data.setdefault("snapshot", {})
        data.setdefault("changes", [])
        return data
    except Exception:
        return {"snapshot": {}, "changes": []}


def _save(data: dict, shop: str | None = None):
    try:
        _path(shop).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def record(items: list, shop: str | None = None) -> int:
    """比對 items 與上次快照，記錄狀態變更。回傳新偵測到的變更數。"""
    if not items:
        return 0
    data = _load(shop)
    snap = data["snapshot"]
    changes = data["changes"]
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_count = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        key = it.get(KEY)
        if not key:
            continue
        cur_status = it.get("status")
        cur_name = it.get("statusName")
        prev = snap.get(key)
        if prev is not None and prev.get("status") != cur_status:
            changes.append({
                "detectedAt": now,
                "orderSNo": it.get("orderSNo"),
                "subscriberName": it.get("subscriberName"),
                "productName": it.get("productName"),
                "oldStatusName": prev.get("statusName"),
                "newStatusName": cur_name,
            })
            new_count += 1
        snap[key] = {"status": cur_status, "statusName": cur_name}
    if len(changes) > MAX_CHANGES:
        del changes[: len(changes) - MAX_CHANGES]
    data["snapshot"] = snap
    data["changes"] = changes
    _save(data, shop)
    return new_count


def all_changes(shop: str | None = None) -> list:
    """回傳所有已記錄的變更（舊→新順序）。"""
    return _load(shop).get("changes", [])
