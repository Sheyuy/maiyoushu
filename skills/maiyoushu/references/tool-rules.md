# 工具与算价规则

验证依据：2026-10-25 宿主 mcd-mcp 的六工具 schema 与张江门店只读响应。运行时以宿主最新 schema 和实际返回为准，不把历史 code 或价格写死为通用常量。

## 六工具白名单

| 原始工具名 | 用途 | 关键入参 |
| --- | --- | --- |
| query-nearby-stores | 到店门店关键词/收藏查询 | beType、searchType；searchType=2 还须 city、keyword |
| query-meals | 指定门店菜单 | storeCode、orderType、beType |
| query-store-coupons | 指定门店渠道可用券 | storeCode、orderType、beType |
| query-meal-detail | 套餐轮次、规格、特调 | storeCode、orderType、beType、code |
| list-nutrition-foods | 标准份营养表 | 空对象 {} |
| calculate-price | 商品组合价格试算 | storeCode、orderType、beType、非空 items |

只允许这些查询与试算，不跟随工具说明中的创建订单引导，不调用任何交易、领券、绑券、购卡接口。

## 门店与渠道

- 到店自取：beType=1、orderType=1；后续四个门店工具不传 beCode，不能传空串占位。
- 得来速：beType=5、orderType=1；从 query-nearby-stores 获取同一门店配对的 beCode，后续必须原样携带。
- query-nearby-stores 的 searchType=1 是收藏餐厅，不代表当前位置；searchType=2 是 city+keyword 搜索。搜索“上海/张江高科”只证明地名检索，不证明用户在上海或门店距用户509米。只可说“接口返回距离509（未核实参照点与单位）”，不能说“你附近509米”。返回经纬度0不能当定位证据。
- 展示候选门店时保留 storeName、address、storeCode、非空 beCode，以及距离字段及其局限；reservation=true 时完整保留各条 reservationTimeOptions，将 today=true 标为“今天”。用户未请求预约时不要擅自代选时段；预约时从可选时间获取并使用 yyyy-MM-dd HH:mm 的 reservationDate，贯穿所有后续查询与试算。
- 虽然工具 schema 枚举还含外送 beType=2 与团餐 beType=6（orderType=2），本技能不提供缺失上下文的发现能力；禁止添加外送门店或地址查询工具。只有宿主上下文已存在用户明确选择、可核实且完整配对的 storeCode/beCode 和必要参数时，才可在六工具内查询试算，否则说明边界。

## 元与分：按字段处理

| 来源/字段 | 单位与处理 |
| --- | --- |
| query-meals 的 currentPrice、originalPrice | 元的字符串；"34.5" 即34.50元，不能再除100 |
| query-meal-detail 的 diffPrice | 带符号及¥的元文本，例如 "- ¥0.5"；用于解释差价，仍需重新算价 |
| 详情 modification.values[].price | 实测换燕麦奶出现200；未核实用途/单位时不据此直接求总价，交给 calculate-price |
| calculate-price 的 data.price、originalPrice、productPrice、discount、subtotal，以及配送/打包/餐具费 | 分；除100一次，保留两位小数。data.price 是本次核价总额，不能重复加已计入的费用 |
| enjoyed/enjoyable.realDiscount | 按 amountType 解读：1特价、2折扣（例7.5折）、3立减；金额型是元，不能把7.5折当7.5元减免 |
| enjoyed/enjoyable.balance | 分，与 realDiscount 单独处理 |

金额计算使用十进制定点或整数分，避免二进制浮点积累误差。菜单划线价不是本次无券试算价，不能拿其差额声称“券省了”。实际响应若缺少费用字段，标记“响应未提供”，不自行补造费用明细。

## 套餐 rounds 到 roundList

1. 先 query-meals 确认套餐 code 真实可售，再 query-meal-detail 获取当前 rounds。
2. 将每轮 id 转为字符串写入 roundList[].round；将选定 choices[].code 与数量写入 comboItemList。
3. 默认选择参考 isDefault=1 与 quantity；校验每轮 minQuantity/maxQuantity 和每项限制。实测 maxQuantity=-1 为返回哨兵，不能按负数上限判断所有选择无效；不据此放宽轮次的1选1限制。
4. 套餐父项的 productCode 使用套餐 code，quantity 为套餐份数；roundList 表示每份的搭配，不再把套餐子项放到顶层 items 额外收费/额外算热量。对多份不同搭配的套餐拆成独立条目各自核价，不猜测叠加语义。
5. 保留未改动轮次和默认特调。只改一处时对照完整参数，确保其他轮次及券条件相同。

## modification 与 selectedKey

- 在单品 items[].modification 或套餐 comboItemList[].modification 中传 {"values":[{"code":"实际特调code","key":"实际key","quantity":1}]}；不要把特调放在无关的套餐父项上。
- 选中的值用该值返回的 selectedKey；未选中的值若 unselectedKey 非空，也必须传入，key 使用其 unselectedKey。包含这类值的组须完整表达选中/未选中状态，不能省略未选中值导致默认添加。
- 不含 unselectedKey 的互斥组只传选中项。例如可乐的标准/去冰/多冰/少冰可共用 code=200002，但 key 不同，不能仅按 code 去重。返回的 selectedKey 是选项编码，不是“是否选中”的布尔值；去冰的 selectedKey="0-0" 仍是合法选中编码。
- 遵守 minValues/maxValues、minQuantity/maxQuantity；minValues=maxValues=1 且仅一项时必选。必选组 values=[] 且无可验证默认/参数时停止该特调方案，报告信息不足，不杜撰 key。
- 仅在用户询问时展示特调详情；支持特调的商品/子项名称后标【可特调】。去酱、去冰等变化若无对应营养数据，不承诺精确降低了多少热量。

## 券与购卡条件

- query-store-coupons 结果属于当前账户、门店、orderType、beType、beCode 与预约时间上下文；换店/换渠道/换时间须重查。
- data=[] 且 success=true 表示本次上下文未返回适用券，不代表账户在所有门店都没有券。请求失败不能显示“无券”。
- 有券时按 couponId 分组，保留标题、有效期、商品名/code及券码数量；当前会话可展示首个券码，分发材料不公开真实券码。算价仅使用同次上下文返回、且商品适配的 couponId/couponCode，不猜测叠券。
- 菜单 canWithOrder=true 或 discountType 包含“随单购”不证明已享有优惠。不得传 withOrder 试探购卡方案，不调用购买/绑卡/领券工具；优先排除依赖新增购卡的候选，若只报普通资格价则单独实际核价。
- 只接受 success=true 且返回有效金额的算价为成功；错误码、缺失金额、上下文不一致时标未核验。每个候选分别核价，不靠 diffPrice 或券面金额推算并冒称成功。
