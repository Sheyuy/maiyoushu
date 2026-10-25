# 麦有数全面测试与审计报告

日期：2026-10-25。范围：`server.py`、`app.js`、index/style、测试、文档与两个分发包。全程未执行下单、支付、领券、购卡，未提交任何比赛材料。下午审计按用户 13:18 要求未启动实时服务；14:30 应用户要求解决未覆盖限制，临时重启服务完成复测，验证后再次停止。

## 1. 测试执行结果

| 测试套件 | 命令 | 数量 | 结果 |
| --- | --- | ---: | --- |
| 后端离线回归 | `python -B -W error::ResourceWarning -m unittest test_server` | 35 | 全部通过（约2.2秒） |
| 前端计算回归 | `node --test test_app.js` | 6 | 全部通过 |
| 审计新增回归 | `node --test test_audit_frontend.js` | 8 | 全部通过 |
| 真实链路复测（服务端） | `.workbuddy/runtime/revalidate_real_link.py` | 15 | 全部通过 |
| 真实链路复测（浏览器） | `.workbuddy/runtime/revalidate_browser.js` | 10 | 全部通过 |

后端测试在硬性出站阻断下运行：`urllib.request.OpenerDirector.open` 默认抛错，真实授权读取默认抛错，每个假上游单独 mock。覆盖恶意 HTTP（重复头、恶意长度、请求走私式框架）、六工具白名单、静态文件隔离、Host/Origin/Sec-Fetch-Site 边界、CSP、凭证回显阻断、JSON/SSE 协议校验、schema 失败关闭、并发与截止时间。

审计新增回归覆盖：`validateTarget` 边界（预算 1—1000、人数 1—4 整数、能量 200—1600 步长 25）、`validateDetail` 结构（轮次数量、编码重复、选项归属）、`validateMenu` 字段、营养重名歧义（去标签后同名但原名不同判未知而非合并或计 0）、TOON 表头与引号校验、`constraintIssues` 区分价格/能量未验证与真实超额、单项替换只动饮料/薯条/玉米且含主食轮整体跳过、`makePlan` 人数与跨套餐选项拒绝。

真实链路复测证据：`outputs/revalidation-real-link.json`、`outputs/revalidation-browser.json`、截图 `outputs/revalidation-browser-01-stores.png`、`revalidation-browser-03-results.png`、`revalidation-browser-04-comparison.png`。

## 2. 发现并已修复的问题

### 服务端（详见 outputs/audit-backend.json，11 项：高 1、中 6、低 4）

| 编号 | 严重性 | 问题 | 修复 |
| --- | --- | --- | --- |
| B01 | 高 | 凭证回显保护可被 Unicode 转义绕过 | 递归规范化检测多层转义后的完整 Bearer 与裸 token，命中返回固定 502 |
| B02 | 中 | `1e400` 溢出为 Infinity、深层嵌套、孤立代理码点可进入调用 | 独立全树检查：深度 40、节点 20000、有限数值、UTF-8 文本与键 |
| B03 | 中 | 锁等待无上限、SSE 滴流可长期占用会话与线程 | 单一 45 秒截止覆盖锁等待/分页/分块读取；read1 分块；连接线程上限 16 |
| B04 | 中 | `format` 被声称支持但忽略；无效 schema 可能静默通过 | 验证完整 schema 树，未实现规则失败关闭返回 502 |
| B05 | 中 | 响应无 CSP | 统一 `default-src 'none'`、同源脚本/连接、禁 base/form/frame/object、限 mcd.cn 图片域 |
| B06 | 中 | JSON 重复键造成语义歧义 | 任意层级拒绝重复键 |
| B07 | 中 | 上游 `isError` 诊断原样返回；401/403 不清理会话 | 固定 502 脱敏；认证/会话失效清 schema 与 session 并返回 503；超时 504 |
| B08 | 低 | 重复 Fetch 头、空 Transfer-Encoding、恶意 Content-Length 触发 500 | 严格头校验；Content-Length 限定 1—10 位 ASCII 数字 |
| B09 | 低 | 空查询参数被忽略；JSON-RPC `id=true` 可匹配 `id=1` | 严格查询字段；id 必须为 int |
| B10 | 低 | Schema 数字语义混用（1.0 不算 integer 等） | 统一 JSON 值等价；multipleOf 用 Decimal |
| B11 | 低 | 含 NUL 的 Bearer 仍被采用 | 按 Bearer token 字符语法校验 |

### 限制解决轮新增加固（14:30）

| 编号 | 严重性 | 问题 | 修复 |
| --- | --- | --- | --- |
| C01 | 中 | 本地 API 无用户认证，不带 Origin 的本机脚本可调用 | 进程内随机访问令牌：服务每次运行生成，仅随所服务页面注入 meta；`X-Maiyoushu-Token` 头缺失/为空/错误/重复一律 403 且不触达上游；`MAIYOUSHU_API_TOKEN` 可指定固定值 |
| C02 | 低 | 病态官方 schema 正则无长度上限 | pattern 限长 200 字符并失败关闭；45 秒总截止兜底 |
| C03 | — | 分发包与文档未反映令牌机制 | README、MCP_INTEGRATION、VALIDATION 同步更新 |

### 前端（此前审计代理改动，已经 diff 逐项复核）

- 新增 `validateTarget`/`validateDetail`/`validateMenu`：预算、人数、能量步长、套餐轮次结构、菜单字段在调用前校验，非法输入不再发往核价。
- 新增 `invalidate()`：改变搭配或目标后清除旧结果、旧基线与导出数据，步骤 3 自动回退，杜绝过期数据被当成当前结论。
- 新增 `syncControls()`：按钮禁用状态集中管理，避免忙态与空态错配。
- 营养歧义：去展示标签后重名但原名不同的餐品判未知，不再取其一；引号不配对、表头缺失/重复的营养行显式报错。
- `defaultMod` 完整校验 modification 结构、min/max 值与 selectedKey；`supportModify` 商品无有效特调不再静默通过。
- 可替换项限定为饮料/薯条/玉米杯，含主食选项的轮次整体跳过，防止"只改一处"误换主食。
- 避开关键词按规范化后比较；候选构建失败计数并在结果摘要中明示"比较范围不完整"。
- 导出 JSON 增加失败计数，忙态或空结果不可导出；表格增加 caption 与 scope，下拉增加 label 与 aria-valuetext。
- 本轮新增 `apiToken()`：所有 `/api/call` 与 `/api/status` 请求携带运行令牌；连接检查对非 200 信封给出明确提示。

### 交付一致性（此前发现并修复）

- **分发包过期**：`outputs/maiyoushu-product-bundle.zip` 曾包含 5 个比磁盘旧的核心文件，已重建。
- **文档测试数量过时**：README、`docs/VALIDATION.md`、`workbuddy.md` 的"后端 9 项"已更新，本轮再更新为 35/6/8。

## 3. 安全扫描

对分发包内全部文本文件做凭证模式扫描，并与本机真实授权做进程内比对（不输出凭证值）：未发现真实凭证、邮箱、密钥或个人绝对路径。良性命中仅两处：`server.py` 中读取环境变量的代码、`test_server.py` 中硬编码的合成测试值。`mcp-config.example.json` 为脱敏结构示例。

令牌专项：磁盘与分发包中的 `index.html` 只含空占位符 `<meta name="maiyoushu-token" content="">`，真实令牌仅在服务运行时注入响应，不出现在任何分发文件、日志或错误消息中；测试以"磁盘文件不含当前令牌"断言固定该行为。

## 4. 未覆盖限制的解决状态

| 此前限制 | 状态 | 解决方式与证据 |
| --- | --- | --- |
| 本地 API 无用户认证 | **已解决** | C01；35 项后端测试含 3 项令牌测试（缺失/为空/错误/重复 403、静态文件免令牌、页面注入而磁盘不含、随机性与环境变量覆盖） |
| schema 失败关闭与真实官方 schema 的兼容性未验证 | **已解决** | 六个白名单工具逐一获取真实官方 inputSchema 并成功接受；真实调用全部成功；缺必填参数 400 不触达上游（`revalidation-real-link.json`） |
| 真实链路未复测 | **已解决** | 服务端 15 项＋浏览器 10 项通过：连接、令牌注入、5 家门店、112 个在售条目、券 0 张、板烧三件套 3 轮、营养 6016 条、核价 ¥34.50·827 千卡、15 候选（7 个满足目标）、3 个单项替换、JSON 导出 |
| 正则无硬中断 | **已缓解** | C02 限长 200 字符失败关闭；45 秒总截止兜底；接受极小残余风险 |
| 凭证检测非完备 | **仍存在（接受）** | 覆盖原文与最多 8 层 JSON 转义，不承诺检测任意编码、Base64 或切片变换；上游固定为可信官方服务 |
| 前端审计代理独立报告未产出 | **不影响结论** | 其磁盘改动已通过语法检查与三套测试（49 项）验证，diff 逐项复核有效 |

## 5. 结论

四套测试共 49 项离线项与 25 项真实链路项全部通过。服务端 11 项实证问题（含 1 项高危凭证回显绕过）与限制解决轮 2 项加固均已修复并有回归覆盖；前端完成输入校验、状态失效、营养歧义与无障碍加固；分发包与文档一致。作品仍保持只读边界：六工具白名单、无下单/支付/领券/购卡、凭证不离开本机、接口令牌不进入分发物。

已知限制：候选比较是有限范围，不宣称全局最优；网页仅支持即时到店自取；营养为标准份参考；凭证检测覆盖多层转义但不承诺任意编码。投稿继续暂停，等待用户确认。
