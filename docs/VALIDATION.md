# 麦有数验证记录

日期：2026-10-25。验证环境为Windows、本机Python标准库服务与真实Microsoft Edge，Playwright驱动，无订单、付款、领券或购卡操作。

## 真实服务与浏览器

- 官方MCP初始化、工具列表读取、六工具调用成功。
- 城市上海、地标张江高科返回5家门店，选择1450359。
- 读取菜单、160条标准份营养及适用券；券成功返回空数组。
- 板烧套餐默认三轮与特调参数核价：34.50元、827千卡。
- 仅饮料换无糖：34.50元、680千卡；仅薯条换玉米：34.00元、591千卡。
- 玉米和无糖一起换：34.00元、444千卡，明确是两项变化。
- 浏览器生成15个核价成功候选，35元/850千卡目标下6个可行。
- 选基线后单项替换：重新核价基线与6个替换，核对请求只有一个round改变。
- 真实1元预算返回零个满足项，保持原预算并列出超额；0元非法预算在调用前阻止。
- 真实缺营养餐品返回未知；390px手机页面无横向溢出。
- 浏览器22项检查通过，无未捕获JavaScript异常。

## 故障注入（不是上游真实事故）

模拟单次价格502、全部价格失败、部分候选失败、营养查询失败、优惠查询失败。验证错误可见、按钮恢复、失败候选不作为成功报价、未知能量不作0、优惠失败与无券分开。测试控制台中的注入502不代表实际麦当劳服务故障。

## 自动测试

- `python -m unittest -v test_server`：35项通过，覆盖白名单、静态路径隔离、Origin/Host、请求格式、恶意请求、凭证回显阻断、进程内访问令牌（缺失/为空/错误/重复均403，页面注入而磁盘不含令牌）与官方schema失败关闭。
- `node --test test_app.js`：6项通过，包含TOON、营养缺失、0值、默认特调和单变量替换。
- `node --test test_audit_frontend.js`：8项通过，覆盖输入校验、营养歧义、约束检查与可替换项限定。

## 未覆盖限制的解决复测（14:30，临时重启服务，验证后停止）

- 访问令牌：服务每次运行随机生成并只注入所服务页面；无令牌、空令牌、错令牌、重复令牌头的 `/api/status`、`/api/schema`、`/api/call` 均返回 403 且不触达上游；静态文件不需令牌。
- 官方 schema 兼容：六个白名单工具逐一获取真实官方 inputSchema，失败关闭策略全部接受；缺必填参数（query-meals 无 storeCode）被 400 拒绝，不触达上游。
- 真实只读调用：门店 5 家、菜单 112 个在售条目、优惠券 0 张、板烧三件套 3 轮、营养 6016 条、核价 ¥34.50，与此前直接调用 MCP 的结果一致。
- 浏览器关键路径 10 项通过：连接、令牌注入、门店搜索、菜单与详情、优惠状态、当前核价 ¥34.50·827 千卡、15 候选生成（7 个满足目标）、3 个单项替换重算、比较 JSON 导出。

证据：`outputs/revalidation-real-link.json`、`outputs/revalidation-browser.json`、截图 `outputs/revalidation-browser-01-stores.png`、`revalidation-browser-03-results.png`、`revalidation-browser-04-comparison.png`。复测脚本在 `.workbuddy/runtime/`，可重跑。

安全审计发现并修复的问题记录在 `outputs/audit-backend.json` 与 `outputs/AUDIT_REPORT.md`。

截图：`outputs/browser-01-stores.png`、`outputs/browser-02-current-quote.png`、`outputs/browser-03-results.png`、`outputs/browser-04-comparison.png`、`outputs/browser-10-mobile.png`。

范围只覆盖指定门店与即时自取，未验证所有套餐、所有门店及外送/预约。Skill参数和安装包已验证，未在全新宿主完成自动安装触发验证。比较范围有限，不宣称全局最优。
