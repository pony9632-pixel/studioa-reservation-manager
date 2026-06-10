# StudioA 門市預約後台 — API 逆向筆記

> 由實際操作後台側錄整理。基底網址：`https://www.studioa.com.tw/backend/api/`
> 前台 SPA：`https://www.studioa.com.tw/shopcms/#/`（Angular）

## 認證 (Auth)
- 所有 API 需帶 header：`Authorization: Bearer <token>`
- 另需 `Content-Type: application/json`、`Accept: application/json`
- token 為 JWT（長度約 973），會由前端動態刷新。CMS token 暫存於 localStorage 鍵 `studioA-Cms-token`。

### 登入 API（已驗證，無圖形驗證碼）
`POST /backend/api/shopcms/admin-user-login/login`
```json
{ "userName": "帳號(email)", "password": "密碼" }
```
回傳：
```
data: {
  userName, name,            // 帳號、門市顯示名稱
  roleId(GUID), isShop, isServiceAccount,
  token,                     // ← 後續 Authorization: Bearer <token>
  id
}, code: 200, message
```
- 驗證既有 token 是否有效：`POST /backend/api/shopcms/admin-user-login/valid-token`（回傳 data:boolean）。

## 回傳外層格式
- 成功：`{ "data": <payload>, "code": 200, "message": "成功" }`
- 失敗/未授權：`{ "Code": <num>, "Message": <str>, "Data": <bool> }`（401 時 Data=false）

---

## 1) 預約清單 + 統計（核心）
`GET /backend/api/shopcms/reservation-activity/reservation-user-list`

查詢參數：
| 參數 | 說明 | 範例 |
|---|---|---|
| `SkipCount` | 分頁起點（0 起算） | 0 |
| `MaxResultCount` | 每頁筆數 | 100 |
| `DeliveryMethod` | 配送方式（門市取貨=2） | 2 |
| `StartTime` | 預約起始時間 | `2025-01-01 00:00:00` |
| `EndTime` | 預約結束時間 | `2026-12-10 23:59:59` |
| `OrderSNo` | **預約單號過濾**（單號 21 碼） | （選填） |
| `SubscriberContactNumber` | **電話過濾**（同號可多筆） | （選填） |
| `Status` | 狀態代碼過濾 | 3 |
| 其他可能：ShopId / ProductName / VipId / SubscriberName / ContactNumber / UserClass / Email 等（搜尋表單欄位，未逐一驗證） | | |

回傳 `data` 結構（統計數字 + 清單）：
```
data: {
  totalCount,            // 總數量（符合條件）
  pickupRate,            // 取貨率字串，如 "62%"
  reservationCount,      // 已預約
  allocationCount,       // 已配貨
  arrivalCount,          // 已到貨
  reserveCount,          // 保留
  pickCount,             // 已取貨（已完成取貨）
  cancelCount,           // 取消
  abandonCount,          // 放棄
  orderEstablishmentCount, shippedCount, deliveredCount, refundedCount, // 其他流程計數
  userReservationListOutDtos: {
    totalCount,
    items: [ <預約單> ]
  }
}
```

單筆預約 `items[]` 重要欄位：
| 欄位 | 型別 | 用途 |
|---|---|---|
| `orderSNo` | string | **預約單號**（查單用，21 碼） |
| `productOrderProductShelfId` | string(GUID) | **改狀態用的 ID** |
| `productOrderId` | string | 訂單ID |
| `shopName` / `shopId` | string | 門市 |
| `productName` | string | **型號**（型號統計用） |
| `reservationActivityId` | string(GUID) | **預約活動 id**（對應活動清單 API 的 `id`→名稱） |
| `userClassName` / `userClass` | string/num | **會員等級**（會員統計用） |
| `subscriberName` | string | 預約人姓名（個資） |
| `subscriberContactNumber` | string | 聯絡電話（個資） |
| `vipId` | string | 會員代碼（SAxxxxxxxx） |
| `status` / `statusName` | num/str | 狀態代碼 / 名稱 |
| `retailPrice` | num | 售價 |
| `reservationTimeValue` | string | 預約時間（顯示） |
| `arrivalEndTime` | str/null | 已到貨後的預計取機期限 |
| `reserveEndTime` | str/null | 保留的取機期限 |
| `orderCancelReason` | num | 取消原因代碼 |
| `isFill` | bool | 是否為遞補（statusName 會出現「(已遞補)」） |
| `isRepeatPurchase` | bool | 重複購買 |
| `remark` | str/null | 備註 |

---

## 2) 變更狀態（核心寫入）
`PUT /backend/api/shopcms/reservation-activity/reservation-status`

Body：
```json
{ "productOrderProductShelfIds": ["<guid>", "..."], "status": 5 }
```
- `productOrderProductShelfIds`：要變更的單據 ID 陣列（取自清單 `productOrderProductShelfId`），可一次多筆。
- `status`：新狀態代碼。

---

## 3) 下拉/列舉資料
- `GET /backend/api/shopcms/reservation-activity/shop-picking-up-status-dropdown-list` → 篩選用狀態清單（全部 7 種）
- `GET /backend/api/shopcms/reservation-activity/product-order-product-shelf-status-dropdown-list` → **門市可手動變更的狀態**：已到貨(5)/保留(6)/已取貨(7)
- `GET /backend/api/shopcms/reservation-activity/dropdown-list` → 預約活動（回傳 `[{id, name, slug}]`；`id` 對應單筆預約的 `reservationActivityId`）
- `GET /backend/api/shopcms/reservation-activity/shop-batch-no-list?deliveryMethod=2` → 梯次
- `GET /backend/api/shopcms/shop/dropdown-list` → 門市清單
- `GET /backend/api/public/enum/user-class-enum` → 會員等級列舉
- `GET /backend/api/public/enum/delivery-methods` → 配送方式列舉

## 狀態代碼對照表
| 代碼 | 狀態 |
|---|---|
| 3 | 已預約 |
| 4 | 已配貨 |
| 5 | 已到貨 |
| 6 | 保留 |
| 7 | 已取貨 |
| 8 | 放棄 |
| 21 | 已取消 |

> 註：清單的 `statusName` 可能出現「放棄(已遞補)」「已取消(已遞補)」等，與 `isFill` 有關，顯示時直接採用 `statusName`。
> 門市端「勾選送出變更狀態」UI 只開放改成：已到貨/保留/已取貨（其餘由其他流程設定）。

## 實測數據（新店裕隆城，2025-01-01～2026-12-10）
- 全部 1889 筆；已預約 52、已取貨 796。
- 型號 67 種；會員等級分佈：集點卡/白金/鑽石/標準/鑽石(盤商)。
