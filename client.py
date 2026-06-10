# -*- coding: utf-8 -*-
"""
StudioA 門市預約後台 — API 串接模組

封裝對 https://www.studioa.com.tw/backend/api 的呼叫：
  - 登入取得 token
  - 查詢預約清單 + 統計
  - 變更預約狀態

這支檔案不含任何畫面程式，可單獨用來測試 API。
詳細 API 規格見 api_notes.md。
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Optional

import requests


BASE = "https://www.studioa.com.tw/backend/api"

# 端點
EP_LOGIN = f"{BASE}/shopcms/admin-user-login/login"
EP_VALID = f"{BASE}/shopcms/admin-user-login/valid-token"
EP_LIST = f"{BASE}/shopcms/reservation-activity/reservation-user-list"
EP_UPDATE_STATUS = f"{BASE}/shopcms/reservation-activity/reservation-status"
EP_ACTIVITIES = f"{BASE}/shopcms/reservation-activity/dropdown-list"

# 狀態代碼對照（見 api_notes.md）
STATUS_CODE_TO_NAME: dict[int, str] = {
    3: "已預約",
    4: "已配貨",
    5: "已到貨",
    6: "保留",
    7: "已取貨",
    8: "放棄",
    21: "已取消",
}
STATUS_NAME_TO_CODE: dict[str, int] = {v: k for k, v in STATUS_CODE_TO_NAME.items()}

# 門市端官方 UI 開放手動變更的狀態（其餘狀態後台可能拒絕，呼叫後看回傳訊息）
SHOP_CHANGEABLE_CODES: list[int] = [5, 6, 7]  # 已到貨 / 保留 / 已取貨

# 配送方式：門市取貨 = 2
DELIVERY_METHOD_STORE_PICKUP = 2

TIME_FMT = "%Y-%m-%d %H:%M:%S"


class StudioAError(Exception):
    """API 呼叫相關錯誤，message 為可直接顯示給使用者的訊息。"""


def _fmt_time(value: "dt.datetime | dt.date | str") -> str:
    """把日期/時間轉成後台要的 'YYYY-MM-DD HH:MM:SS' 格式。"""
    if isinstance(value, str):
        return value
    if isinstance(value, dt.datetime):
        return value.strftime(TIME_FMT)
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day).strftime(TIME_FMT)
    raise TypeError(f"無法處理的時間型別：{type(value)!r}")


class StudioAClient:
    """StudioA 預約後台 API 客戶端。

    用法：
        c = StudioAClient()
        c.login("帳號", "密碼")
        data = c.fetch_reservations(start, end)
        c.update_status(["<shelfId>"], 5)
    """

    def __init__(self, timeout: int = 30) -> None:
        self.session = requests.Session()
        self.timeout = timeout
        self.token: Optional[str] = None
        self.shop_name: Optional[str] = None  # 登入後的門市名稱
        self.user_name: Optional[str] = None

    # ------------------------------------------------------------------ #
    # 共用
    # ------------------------------------------------------------------ #
    def _headers(self, with_auth: bool = True) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if with_auth:
            if not self.token:
                raise StudioAError("尚未登入（沒有 token）。請先登入。")
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[dict] = None,
        json: Optional[dict] = None,
        with_auth: bool = True,
    ) -> Any:
        """送出請求並回傳「成功時的 data 內容」；失敗時丟出 StudioAError。"""
        try:
            resp = self.session.request(
                method,
                url,
                params=params,
                json=json,
                headers=self._headers(with_auth),
                timeout=self.timeout,
            )
        except requests.exceptions.RequestException as exc:
            raise StudioAError(f"連線失敗：{exc}") from exc

        # 嘗試解析 JSON
        try:
            body = resp.json()
        except ValueError:
            raise StudioAError(
                f"伺服器回傳非 JSON（HTTP {resp.status_code}）。可能是網址錯誤或被擋下。"
            )

        # 回傳外層有兩種：成功 {data, code, message}；錯誤 {Code, Message, Data}
        code = body.get("code", body.get("Code"))
        message = body.get("message", body.get("Message", ""))

        if resp.status_code == 401 or code == 401:
            raise StudioAError("未授權（token 失效或未登入），請重新登入。")

        if resp.status_code >= 400:
            raise StudioAError(f"HTTP {resp.status_code}：{message or '請求失敗'}")

        # 後台成功時 code == 200
        if code is not None and code != 200:
            raise StudioAError(message or f"後台回傳代碼 {code}")

        # data 可能是 dict / list / bool
        return body.get("data", body.get("Data"))

    # ------------------------------------------------------------------ #
    # 登入
    # ------------------------------------------------------------------ #
    def login(self, username: str, password: str) -> dict:
        """以帳號密碼登入，成功後把 token 存在 self.token。回傳登入資料 dict。"""
        if not username or not password:
            raise StudioAError("請輸入帳號與密碼。")
        data = self._request(
            "POST",
            EP_LOGIN,
            json={"userName": username, "password": password},
            with_auth=False,
        )
        if not isinstance(data, dict) or not data.get("token"):
            raise StudioAError("登入失敗：帳號或密碼可能錯誤。")
        self.token = data["token"]
        self.user_name = data.get("userName")
        self.shop_name = data.get("name")
        return data

    def is_token_valid(self) -> bool:
        """詢問後台目前 token 是否仍有效。"""
        if not self.token:
            return False
        try:
            data = self._request("POST", EP_VALID, json={}, with_auth=True)
        except StudioAError:
            return False
        return bool(data)

    # ------------------------------------------------------------------ #
    # 查詢清單 + 統計
    # ------------------------------------------------------------------ #
    def fetch_reservations(
        self,
        start_time,
        end_time,
        *,
        status: Optional[int] = None,
        order_sno: Optional[str] = None,
        phone: Optional[str] = None,
        skip: int = 0,
        max_count: int = 100,
        delivery_method: int = DELIVERY_METHOD_STORE_PICKUP,
    ) -> dict:
        """查詢一頁預約清單。回傳後台 data dict（含統計數字與 items）。"""
        params = {
            "SkipCount": skip,
            "MaxResultCount": max_count,
            "DeliveryMethod": delivery_method,
            "StartTime": _fmt_time(start_time),
            "EndTime": _fmt_time(end_time),
        }
        if status is not None:
            params["Status"] = status
        if order_sno:
            params["OrderSNo"] = order_sno.strip()
        if phone:
            params["SubscriberContactNumber"] = phone.strip()

        data = self._request("GET", EP_LIST, params=params)
        if not isinstance(data, dict):
            raise StudioAError("查詢回傳格式異常。")
        return data

    def fetch_all_items(
        self,
        start_time,
        end_time,
        *,
        status: Optional[int] = None,
        order_sno: Optional[str] = None,
        phone: Optional[str] = None,
        page_size: int = 500,
        delivery_method: int = DELIVERY_METHOD_STORE_PICKUP,
        max_pages: int = 100,
    ) -> tuple[dict, list[dict]]:
        """分頁抓「全部」符合條件的預約。

        回傳 (stats, items)：
          stats = 第一頁回傳的統計數字（totalCount / 各狀態計數 / pickupRate ...）
          items = 全部明細
        """
        first = self.fetch_reservations(
            start_time, end_time, status=status, order_sno=order_sno, phone=phone,
            skip=0, max_count=page_size, delivery_method=delivery_method,
        )
        inner = first.get("userReservationListOutDtos", {}) or {}
        total = int(inner.get("totalCount", 0) or 0)
        items: list[dict] = list(inner.get("items", []) or [])

        page = 1
        while len(items) < total and page < max_pages:
            data = self.fetch_reservations(
                start_time, end_time, status=status, order_sno=order_sno, phone=phone,
                skip=page * page_size, max_count=page_size,
                delivery_method=delivery_method,
            )
            chunk = (data.get("userReservationListOutDtos", {}) or {}).get("items", []) or []
            if not chunk:
                break
            items.extend(chunk)
            page += 1

        # 統計數字只取第一頁那層（不含 items 明細）
        stats = {k: v for k, v in first.items() if k != "userReservationListOutDtos"}
        stats["itemsTotalCount"] = total
        return stats, items

    def fetch_activities(self) -> list[dict]:
        """取得『預約活動』下拉清單。回傳 [{'id': GUID, 'name': 名稱, ...}, ...]。

        每筆預約帶 `reservationActivityId`，可用本清單把 id 對應成活動名稱。
        """
        data = self._request("GET", EP_ACTIVITIES)
        return data if isinstance(data, list) else []

    def find_by_order_sno(self, order_sno: str) -> list[dict]:
        """用預約單號查單。回傳符合的明細清單（通常 1 筆）。

        日期區間用很寬的範圍以確保查得到。
        """
        order_sno = (order_sno or "").strip()
        if not order_sno:
            raise StudioAError("請輸入預約單號。")
        start = dt.datetime(2000, 1, 1)
        end = dt.datetime.now() + dt.timedelta(days=3650)
        _, items = self.fetch_all_items(start, end, order_sno=order_sno, page_size=100)
        return items

    def find_by_phone(self, phone: str) -> list[dict]:
        """用聯絡電話查單。回傳符合的明細清單（同一電話可能多筆）。"""
        phone = (phone or "").strip()
        if not phone:
            raise StudioAError("請輸入電話號碼。")
        start = dt.datetime(2000, 1, 1)
        end = dt.datetime.now() + dt.timedelta(days=3650)
        _, items = self.fetch_all_items(start, end, phone=phone, page_size=200)
        return items

    # ------------------------------------------------------------------ #
    # 變更狀態
    # ------------------------------------------------------------------ #
    def update_status(self, shelf_ids: list[str], status: int) -> str:
        """變更一或多筆預約的狀態。

        shelf_ids：各筆的 productOrderProductShelfId。
        status：新狀態代碼（見 STATUS_CODE_TO_NAME）。
        成功回傳後台訊息字串；失敗丟出 StudioAError（含後台原始訊息）。
        """
        if not shelf_ids:
            raise StudioAError("沒有要變更的單據。")
        if status not in STATUS_CODE_TO_NAME:
            raise StudioAError(f"未知的狀態代碼：{status}")
        payload = {"productOrderProductShelfIds": list(shelf_ids), "status": status}
        self._request("PUT", EP_UPDATE_STATUS, json=payload)
        return f"已將 {len(shelf_ids)} 筆變更為「{STATUS_CODE_TO_NAME[status]}」"


# ---------------------------------------------------------------------- #
# 命令列測試
#   python client.py                  -> 連通性檢查（用假 token，預期 401）
#   python client.py 帳號 密碼          -> 真實登入並印出統計摘要
# ---------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 3:
        c = StudioAClient()
        print("登入中…")
        info = c.login(sys.argv[1], sys.argv[2])
        print(f"登入成功：門市={c.shop_name} 帳號={c.user_name}")
        start = dt.datetime.now() - dt.timedelta(days=365)
        end = dt.datetime.now() + dt.timedelta(days=365)
        stats, items = c.fetch_all_items(start, end)
        print(f"總筆數={stats.get('totalCount')} 取貨率={stats.get('pickupRate')} 抓到明細={len(items)} 筆")
    else:
        print("連通性檢查（使用無效 token，預期得到『未授權』）…")
        c = StudioAClient()
        c.token = "invalid.token.for.connectivity.check"
        try:
            c.fetch_reservations(
                dt.datetime.now() - dt.timedelta(days=7), dt.datetime.now()
            )
            print("非預期：竟然成功了？")
        except StudioAError as e:
            print(f"OK，端點可連通，回應：{e}")
