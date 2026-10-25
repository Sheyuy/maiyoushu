# 上海张江高科真实场景与复验

用户提供场景：上海张江高科，门店1450359，板烧套餐9900005462。以下是2026-10-25的真实只读调用记录摘要，不是固定菜单、当前报价承诺或已完成订单证明。示例明确指定到店自取用于复验，不把该方式伪称为用户历史订单信息。

## 查询证据

| 工具 | 入参摘要 | 成功结果摘要 | 响应时间 / traceId |
| --- | --- | --- | --- |
| query-nearby-stores | beType=1, searchType=2, city=上海, keyword=张江高科 | 候选含1450359：麦当劳上海浦东张江中兴智慧园餐厅，碧波路889号2幢1层09室、10室商铺；无beCode | 12:06:53 / a1ee8b86f89de03564ae6ede97fb29a3 |
| query-meals | storeCode=1450359, orderType=1, beType=1 | 9900005462=板烧鸡腿堡三件套；currentPrice="34.5"、originalPrice="47"（元），canWithOrder=false | 12:06:54 / 4ec7edb0a30336e3952fc691860ffbef |
| query-meal-detail | 同店渠道，code=9900005462 | 三轮默认：1406板烧鸡腿堡、4810中薯条、3050可乐中杯；可选4437小杯玉米和3071无糖中杯 | 12:06:53 / bcf7fb6bd69258bb4070b3e4d14c3058 |
| query-store-coupons | storeCode=1450359, orderType=1, beType=1 | success=true, code=200, data=[]；仅表示本次上下文无返回适用券 | 12:06:53 / 2fdf4b6490afa17f9de6003fe515ea62 |
| list-nutrition-foods | {} | data为160行TOON字符串；有本例所需标准份条目 | 12:06:53 / 6f8bcb7e1afdef1c06906cc9a7ca37ca |

工具时间未在返回中声明时区，保留原样，不自行转换。关键词结果的distance=509参照点和单位未核实，经纬度为0；不能推断用户当前位置或步行距离。该门店支持预约，实际面向用户展示门店选择时须读最新返回并完整展示预约时段，本文摘要不替代门店选择界面。

## 已成功执行的基线算价参数

将以下对象传给宿主的 calculate-price（此调用只试算）：

```json
{
  "storeCode": "1450359",
  "orderType": 1,
  "beType": 1,
  "items": [
    {
      "productCode": "9900005462",
      "quantity": 1,
      "roundList": [
        {
          "round": "1",
          "comboItemList": [
            {
              "code": "1406",
              "quantity": 1,
              "modification": {
                "values": [
                  {"code": "102201", "key": "0-1", "quantity": 1},
                  {"code": "120512", "key": "0-1", "quantity": 1}
                ]
              }
            }
          ]
        },
        {
          "round": "2",
          "comboItemList": [{"code": "4810", "quantity": 1}]
        },
        {
          "round": "3",
          "comboItemList": [
            {
              "code": "3050",
              "quantity": 1,
              "modification": {
                "values": [{"code": "200002", "key": "0-1", "quantity": 1}]
              }
            }
          ]
        }
      ]
    }
  ]
}
```

基线响应：success=true、code=200，data.price=3450、originalPrice=3450、discount=0、productList[0].subtotal=3450；datetime="2026-10-25 12:07:14"，traceId=9becc7d0c03bf3577bcd69f4bc961c9b。菜单划线47元与试算originalPrice=34.50元不是同一个含义，不能声称本次券节省12.50元。

## 两个各自只改一处的成功试算

从基线分别复制参数，分别执行，不将两处替换合并。

| 方案 | 参数唯一变更 | data.price（分） | 显示金额 | 标准份参考总kcal | 相对基线 | 算价 traceId（均12:07:14） |
| --- | --- | --- | --- | --- | --- | --- |
| 默认基线 | 无 | 3450 | 34.50元 | 827 | — | 9becc7d0c03bf3577bcd69f4bc961c9b |
| 只换无糖中杯 | round="3" 的 comboItemList[0].code 从3050改3071；保留标准冰特调 | 3450 | 34.50元 | 680 | 价格0元，热量-147kcal | df4c87e4b3e9feda2c1b81a38cfdf08c |
| 只换小杯玉米 | round="2" 的 comboItemList[0].code 从4810改4437 | 3400 | 34.00元 | 591 | 价格-0.50元，热量-236kcal | 8d74cba2f0f3ec5234817939c1571438 |

两个替代调用均返回success=true、code=200、discount=0；无券参数、无购卡参数、无交易动作。保留默认烤味酱和生菜，未执行去酱或去冰热量推断。只有板烧子项和饮料支持特调，套餐父项supportModify=false。

## 宿主安装与自然语言试用

1. 解压maiyoushu.zip，确认maiyoushu/SKILL.md与references同级关系正确；将整个maiyoushu目录导入宿主技能库，或复制到宿主约定的技能目录（例如WorkBuddy用户技能目录 ~/.workbuddy/skills/maiyoushu/）。避免重复嵌套maiyoushu/maiyoushu。
2. 使用宿主已经配置并启用的麦当劳MCP，无需向技能传入Token。宿主连接不可用时如实报告无法查询，使用宿主现有配置入口恢复；不收集凭证、不另装连接器。
3. 输入：“麦有数，上海张江高科1450359到店自取，一人35元以内，板烧套餐9900005462只改一处，比较默认、只换无糖、只换小杯玉米；查本店优惠，告诉我价格和热量差，不下单。”
4. 检查工具轨迹只有六个允许名称，且每个候选有独立成功算价。历史数值仅作理解格式参考，复验时不得强求同价同券。

## 验收情景

- 价格：菜单34.5元与算价3450分都显示34.50元，不重复除100；券字段为0时不虚构券优惠。
- 单变量：替代参数分别只改round3或round2；不改主食、数量、门店、渠道、时间和券。
- 营养：默认827、无糖680、玉米591是标准份参考；新品无匹配时显示未知、不归0、不保证总热量达标。
- 特调：用户主动要求去烤味酱时，从最新详情取102201的unselectedKey并保留其他必传项，烤味酱和生菜组均表达状态；不能仅删除酱项冒充去酱成功，也不能承诺少了精确热量。
- 门店：仅说“张江”时展示搜索候选，请用户选店；不能宣称已取得用户定位。换店重新查券和菜单。
- 失败：算价失败时标未核验，不把菜单价写成实时核价；MCP不可用时不输出伪造成功数据。
- 越界：对“帮我下单”“支付一下”“领券”“买麦金卡”说明能力边界，不调用白名单以外工具，也不借助Web app代办。

本记录证明直接工具链的查询与试算可运行；未验证每一种宿主的自动触发/安装行为，未声称在独立全新宿主完成自动端到端回放，未完成网页发布或报名提交。
