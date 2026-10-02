# BriefForge 验证结果

后续改进与最新费用见[稳定输出与可见协作](STABILITY_RESULTS.md)。下列历史结果保留，不用新结果覆盖旧失败。

更新日期：2026-10-02。工程闭环、真实调用和公开网页获取均已运行；实测同时暴露了模型协议错误、套餐误读和遗漏。这里保留成功与失败，不把“生成完成”解释为“事实全部正确”。合成资料不能作为真实市场或用户反馈的证据。

## 已验证的范围

| 范围 | 当前结果 | 证据 |
| --- | --- | --- |
| Python 自动化测试 | 120 项通过，0 失败、0 跳过；Ruff 通过 | [final-pytest.xml](../artifacts/final-pytest.xml)、[final-verification.json](../artifacts/final-verification.json) |
| 前端 | 8 项单元测试通过，TypeScript 与生产构建通过 | `pnpm --dir frontend test`、`pnpm --dir frontend build` |
| 浏览器主流程 | 10 项通过，无浏览器运行错误 | [e2e-results.json](../artifacts/frontend/e2e-results.json) |
| 浏览器资料交互 | 6 项通过，无浏览器运行错误 | [interaction-results.json](../artifacts/frontend/interaction-results.json) |
| 研究范围设置 | 5 项通过：时间范围、仅上传资料模式、持久化、合成资料边界及无意外模型调用 | [brief-options-results.json](../artifacts/frontend/brief-options-results.json) |
| 最终页面检查 | 桌面 1440px、手机 390px；无横向溢出、浏览器错误或意外写入 | [final-readonly-results.json](../artifacts/frontend/final-readonly-results.json)、[最终截图](../artifacts/frontend/14-final-report-desktop.png) |
| PostgreSQL 应用集成 | 30 份资料、19 条结论、11 项任务、2 次补查，报告生成完成 | [integration.json](../artifacts/integration.json) |
| PostgreSQL 崩溃恢复 | 实际子进程退出后恢复成功，检查点和已完成任务得到复用 | [recovery/latest.json](../artifacts/recovery/latest.json) |
| 确定性回放对照 | 8 个测试场景 × 3 种架构，共 24 次完成 | [evaluation-replay-v2/summary.json](../artifacts/evaluation-replay-v2/summary.json) |
| 最终真实模型对照 | Gemini v4，24 / 24 次完成，294 次请求 | [evaluation-live-gemini-v4/summary.json](../artifacts/evaluation-live-gemini-v4/summary.json) |
| 可上传测试文件 | 30 份，TXT/MD/HTML/CSV/XLSX/DOCX 各 5 份，全部经实际导入函数往返验证 | [demo-files-validation.json](../artifacts/demo-files-validation.json)、[资料包](../data/demo-files/) |
| Word 与 PPT 导出 | Word 8 页，PPT 10 页；原生 Office 打开、渲染和逐页检查完成 | [export-validation.json](../artifacts/export-validation.json) |
| Docker Compose | 仅配置校验通过；本机 Docker daemon 不可用，未验证容器运行 | 不得将本机原生 PostgreSQL 结果表述为 Compose 运行通过 |

前端主流程覆盖持久化工作区加载、新建合成案例、研究需求保存、确认大纲并回放、逐字原文证据、持久化协作记录、不可变报告修订、两种文件可下载及移动端无页面横向溢出。资料交互还覆盖键盘输入焦点、公开工作区创建、资料版本、实际文件上传和网页导入入口。入口可见不等于真实搜索服务已验证。桌面与移动端截图保存在 [artifacts/frontend](../artifacts/frontend/)。

可上传资料包中的 5 份 DOCX 只做了正文导入往返校验，未做页面渲染检查；下文的正式 Word 报告样例另经原生 Office 渲染与逐页检查。资料生成器不读取参考答案，也不调用模型。

## 固定响应回放：只验证机制

本轮使用冻结的 v2 资料包及独立参考答案，8 个测试场景分别检查企业版 SSO 条件、SSO 未披露、仅月付价格、混合币种、提示注入、无用户反馈、旧资料更新及同日冲突。每个场景在 `single`、`pipeline`、`multi` 下各运行一次。四个开发场景不计入这 24 次测试。

| 架构 | 完成 / 总数 | 平均参考检查通过率 | 平均逐字引用有效率 | 补查事件总数 | 模型请求与费用 |
| --- | ---: | ---: | ---: | ---: | --- |
| single | 8 / 8 | 86.39% | 100% | 0 | 0 次 / US$0 |
| pipeline | 8 / 8 | 87.78% | 100% | 0 | 0 次 / US$0 |
| multi | 8 / 8 | 97.36% | 100% | 9 | 0 次 / US$0 |

参考检查通过率是每次运行中选定参考项通过比例的平均值，覆盖价格、套餐条件、版本冲突、缺失信息和定位。它不是所有报告句子的人工准确率。逐字引用有效率只说明引用片段确实存在于该报告冻结的来源中，不证明结论在语义上受到支持。动态系统的补查事件来自实际保存的 `followup_planned` 事件。

这些差异来自确定性提取和编排机制，**不能用来声称多 Agent 的真实模型质量优于单 Agent**。该评测也没有测量真实员工的时间节省。8 个场景是小型诊断集，不具备行业代表性；全部文本和公司均为合成测试资料。

每次运行的分数、冻结报告和事件均位于 [evaluation-replay-v2](../artifacts/evaluation-replay-v2/)。[fixture-manifest.json](../artifacts/evaluation-replay-v2/fixture-manifest.json) 保存资料及参考答案哈希；参考答案由评测器读取，研究引擎不读取。旧的 `evaluation-replay` 目录保留历史结果，不能与 v2 合并计算。

## Qwen 真实调用：未完成的诊断子集

默认配置为 `qwen-default`，固定 `qwen/qwen3-235b-a22b-2507` 和 `gmicloud/fp8` 线路。此轮在上游 HTTP 429 和工具参数结构错误后停止，**没有完成完整的 24 次对照，也没有产出完成报告**。

[interrupted-runs.json](../artifacts/evaluation-live/interrupted-runs.json) 保存了 6 次已创建运行的停止时快照，其中 5 次实际尝试了模型 HTTP 请求。3 个 `test-enterprise-sso-*-live.json` 文件只是这 6 次运行的子集，不能相加为 9 次。程序化摘要见 [partial-summary.json](../artifacts/evaluation-live/partial-summary.json)。

| 项目 | 停止时记录 |
| --- | ---: |
| 原计划运行数 | 24 |
| 已创建 / 未创建 | 6 / 18 |
| 完成报告 | 0 |
| 失败 / 取消 | 4 / 2 |
| 失败原因 | HTTP 429：3 次；工具参数结构校验失败：1 次 |
| 已保存逐案例结果 | 3 份，均失败 |
| 模型 HTTP 尝试 | 31 次，包含失败与重试 |
| 已确认费用 | US$0.007117 |
| 未结算预留 | US$0.034314 |

费用数字只描述该诊断子集的停止时快照，不是当前全局账本。预留并非已确认扣费，也不能据此断言失败请求免费。失败案例文件中的 `reference_accuracy: 0.0` 是失败占位值；没有报告可评分，因此不将它解释为模型事实准确率为零，不绘制质量排名。记录缺少场景关联的另外 3 次运行不推断其场景名称。

后续 Gemini 运行显式选择另一模型配置，另存目录并独立统计，不与 Qwen 混算。模型或线路变化后也不能将质量差异单独归因于 Agent 架构。

后来的默认线路试跑 `efe0732a-fb36-422b-a29b-7652ca8f3d74` 仍失败：14 次请求，确认 US$0.006024，待确认预留 US$0.004781。模型先返回缺少末尾括号的工具参数，单次修复又遇到上游 HTTP 400。没有自动切换供应商，也没有产生报告。具体诊断见 [failure-analysis.json](../artifacts/live/failure-analysis.json)。默认 Qwen 路线尚未证明可靠完成；可在界面高级选项显式选择已完成实测的 Gemini 配置。

避免重放损坏 JSON 工具历史后，再次试跑 [acf899c4…](../artifacts/live/acf899c4-37a3-4b3e-899d-a7696966d9aa.json) 仍在一次修复后收到不合规参数：14 次请求、确认 US$0.005331、未知费用预留 US$0.008242，未产生报告。此后停止重试，保留供应商协议兼容性限制；没有把修复代码通过测试等同于默认线路可用。

## 最终 Gemini 真实对照

模型固定为 `google/gemini-2.5-flash-lite`，线路 `google-ai-studio`；每个场景的每种架构预算上限均为 US$0.15。资料、参考答案与实现哈希分别保存。单 Agent 一次合并分析，固定流水线串行专业分工，动态系统允许补查及并行。这些实现的上下文和工具协议也不同，因此不能只把差异归因于 Agent 数量。

[最终 v4](../artifacts/evaluation-live-gemini-v4/summary.json) 的 24 次运行全部完成，没有失败、取消或未运行项。共 294 次收费请求、US$0.177924 确认费用，该轮待结算预留为 0。它使用三名独立 Worker，每项研究的专业任务最多三个并行；全部实现及资料哈希见 [implementation-manifest.json](../artifacts/evaluation-live-gemini-v4/implementation-manifest.json)。

| 架构 | 完成 / 总数 | 平均参考检查通过率 | 模型请求 | 已确认费用 | 平均端到端耗时 | 补查事件 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| single | 8 / 8 | 87.78% | 8 | US$0.021385 | 18.94 秒 | 0 |
| pipeline | 8 / 8 | 47.78% | 99 | US$0.055627 | 30.12 秒 | 0 |
| multi | 8 / 8 | 76.81% | 187 | US$0.100912 | 31.71 秒 | 10 |

端到端时间包含排队与共享资源竞争；同机还执行了一次单独记录的 Qwen 诊断，不能作为严格性能基准。三种架构的逐字引用有效率均为 100%，仍不等于语义正确率。动态多 Agent 在这组诊断上低于单 Agent，费用更高；不能据此宣传准确率提升或普遍优势。

v4 加入了“每年支付”等价格单位变体校验，并避免把损坏 JSON 工具历史重新发送给供应商。研究完成率改善，但撤回价格和漏答仍需单独评价；把错误改为未知不等于已经得到正确报价。独立语义复核见 [semantic-review.json](../artifacts/evaluation-live-gemini-v4/semantic-review.json)。

最终独立助手逐项审阅了全部 72 条价格和 90 条 SSO 相关结论：

- 未重现已知月/年单位错误；43 条报价通过金额、币种和周期的窄范围检查，20 条有资料却被撤回为未知，9 条只在结论正文中说明计费原则，没有直接给出报价。
- 5 条金额存在于同一冻结来源的其他文本中，但不在附带引文里；还存在漏写税费及企业版另行询价条件的问题。因此不能把 43 条解释为完整采购条件全部正确。
- SSO 仍有 2 条冲突断言问题和 2 条答案遗漏：其中一条结论正文承认来源冲突，但 `value` 仍保留单方面答案；另一条跨章节能力判断仍未充分表达冲突。27 条应保持未知的答案均保持未知，本次未发现重大日期或把旧档当当前事实的错误。

这次审阅尚未经真人标注复核，也不覆盖全部建议和叙事。**最终真实报告仍是需要复核的研究草稿，不是已通过完整语义验收的决策报告。** 错误、弃答和正确但条件不全的答案分别保存，没有把它们合并成一个“准确率”。

## 真实对照的修复历史与限制

已完成的修复前两轮保留如下，不能只挑最后成功的记录：

| 版本 | 完成 / 尝试 | 失败 | 已确认费用 | 用途 |
| --- | ---: | ---: | ---: | --- |
| [v2](../artifacts/evaluation-live-gemini-v2/analysis.json) | 19 / 24 | 5 | US$0.149030 | 工具选择没有修复机会时的诊断 |
| [v3](../artifacts/evaluation-live-gemini-v3/summary.json) | 23 / 24 | 1 | US$0.150947 | 增加一次有界工具修复及首版价格单位校验后的诊断 |

v2 中 single / pipeline / multi 的平均参考检查通过率分别为 89.17% / 32.50% / 44.44%；v3 分别为 89.17% / 51.81% / 64.44%。失败运行按零占位参与这里的平均值，不能解释为无报告运行的人工事实准确率。两轮都没有得到多 Agent 优于单 Agent 的证据。v2 使用一名 Worker，v3 使用三名 Worker，耗时含排队，跨版本耗时不用于比较算法速度。

初次 Gemini 对照目录 [evaluation-live-gemini](../artifacts/evaluation-live-gemini/summary.json) 另保留 4 次已尝试运行：1 次完成、3 次取消、20 次未运行。它因开发试跑的语义问题被主动停止，不能并入后续完整轮次。

参考检查仅覆盖指定事实和条件，不是每句话的完整语义准确率。所有已完成 v3 报告的引文都能逐字定位，但独立审阅仍发现“月单价被写成年付款金额”的错误；`value` 正确也不代表 `statement` 正确。相关原始报告和审阅结果保留在 [v3 目录](../artifacts/evaluation-live-gemini-v3/)。冻结测试被用于修复后重测，因此这些结果属于反复使用诊断集后的开发验证，不是未接触测试集上的泛化估计。

[v3 独立语义审阅](../artifacts/evaluation-live-gemini-v3/semantic-review.json) 检查了全部 69 条价格结论：24 条存在实质月/年单位错误，42 条在该审阅范围内正确，3 条被撤回为未知；错误涉及 8 份报告。69 条 SSO 结论和另外 20 条跨维度 SSO 断言中，发现 3 条冲突处理问题和 2 条已存在答案的漏读；26 条未披露项没有被错误写成“不支持”。这属于独立助手逐项核对，尚未经真人标注者复核，也不覆盖报告全部句子。

## PostgreSQL 恢复、版本与预算边界

[恢复验证](../artifacts/recovery/latest.json) 使用原生 PostgreSQL 的独立 `briefforge_recovery` 数据库和独立 schema；没有访问生产数据库，也没有停止应用服务。故障为子进程调用 `os._exit` 突然退出，退出码 86，并非捕获异常后继续执行。

- 崩溃前保存 4 个图检查点和 6 个已完成任务；恢复后复用完成输出，没有重新启动这些任务。
- 新进程获得新的租约令牌，旧工作进程被拒绝提交；最终任务数为 11，同一运行仅产生 1 份报告，完成图重复进入具有幂等性。
- 替换资料后，旧报告 JSON 和哈希保持不变，1 条受影响结论被标记待更新；新报告使用新价格。
- 独立预算并发测试中，16 次 US$0.02 预留竞争 US$0.15 单次上限，7 次获准、9 次被阻止；类别和全局上限、重复请求键、未知费用继续预留，以及 40 次请求上限均通过检查。

预算竞争使用模拟记账，模型 HTTP 请求为 0；测试预留最终结算为 0。它验证并发记账约束，不是一次实际消耗 US$10 的模型测试。恢复测试同样使用回放；尚不能证明进程在供应商已计费、响应尚未落盘时崩溃绝不会重复收费。

## Word 与 PPT 的一致性交付

样例来自应用实际保存在 PostgreSQL 的不可变报告 `ff26ff55-0bb6-4c57-b338-a57361da1b61`（v1），不是独立编造的演示稿。

- [Word：8 页](../artifacts/demo/briefforge-ff26ff55-0bb6-4c57-b338-a57361da1b61-v1.docx)
- [PowerPoint：10 页](../artifacts/demo/briefforge-ff26ff55-0bb6-4c57-b338-a57361da1b61-v1.pptx)

两种文件使用同一个报告快照，保留同一结论、适用条件、来源及报告版本；manifest 包括模型配置字段。文字、表格、原生图表和嵌入图表数据可编辑。价格图仅在币种、单位和计费口径一致时出现，未将缺失信息补写成事实。每页显示合成资料标识。

通过新建、隐藏的 Microsoft Word/PowerPoint 实例只读打开文件并导出页面，确认 Word 8 页、PPT 10 页，PowerPoint 文字溢出检测为 0。逐页目视检查全部 18 个页面后，仅更新 manifest 的最终版本再次渲染；全部页面与已检查版本像素一致。PPT 包结构和几何检查均为 0 项问题。验证摘要与交付文件 SHA-256 见 [export-validation.json](../artifacts/export-validation.json)。

随附文档渲染脚本因捆绑环境没有 LibreOffice 而未通过；本次没有调用桌面 LibreOffice，也不声称该脚本通过。实际视觉验证采用原生 Office。导出样例对应较早的冻结资料快照，行业证据不足被保留为未知；不会将后来加入资料包的行业文本混入旧报告。

## 真实公开网页研究：连接成功，交付质量仍有限

真实研究了 Trello、Asana 和 ClickUp，使用有预算预留的 OpenRouter 搜索，获取实际正文。一次完整联网试跑进行了 4 次搜索尝试，保存 8 个来源，4 个页面抓取失败；共 17 次收费请求、US$0.030864。搜索结果、正文 URL、获取时间、可识别的发布日期与失败原因见 [public-network-validation.json](../artifacts/live/public-network-validation.json)。它包含历史误收的一个错误页；之后加入了登录、访问验证和 CSS 错误页过滤。

独立核查发现第一版报告将 2FA 误读为 SSO、误解附加服务是否包含在套餐中，且没有抽取已经取得的官方价格。改进原文窗口和日期校验后的第二版完成 34 次请求、US$0.052920，10 条结论有 7 条附证据，13 个引用均能逐字定位。错误页已排除，一部分 SSO 判断改善；三家价格仍留空，Trello 套餐解释和旧厂商营销数字的范围仍有问题。

[第二版语义审阅](../artifacts/live/public-semantic-review-v2.json) 保存全部发现，包括来源 URL、引文和时间。网页“可以获取”、引文“可以定位”、结论“真的受到支持”是三种不同检查。本次证明了联网与持久化链路，**没有交付经过语义验收的真实市场决策报告**。随附 Word/PPT 样例均来自明确标注的回放，真实模型成果在网页与导出中标为研究草稿。

## 实际错误推动的修复

- 工具参数模式内联，Gemini 解码模式适配；本地完整约束仍强制执行。
- 工具选择或参数错误共用一次修复机会，失败、重试和修复均计费，不执行违规工具。
- 研究和核查读取带偏移的相关原文窗口，避免只读长网页开头而错过套餐条款。
- 对没有原文支持的完整日期和同币种同金额月/年单价矛盾撤回断言，并要求有界补查。
- 明确区分旧版材料与当前冲突；没有证据时同步撤回结论、值和条件，不靠多个 Agent 投票得出事实。

这些检查只针对明确可验证的约束，不替代一般语义核查。修复没有改写历史报告，错误样本仍可以复查。早期完整合成试跑中的日期错误、后续三条价格单位错误分别见 [pilot-semantic-review.json](../artifacts/live/pilot-semantic-review.json) 和 [final-pilot-semantic-review.json](../artifacts/live/final-pilot-semantic-review.json)。

## 费用账本

总上限维持 US$10，所有评测、试跑、修复调用使用同一本地 PostgreSQL 账本。[最终验证快照](../artifacts/final-verification.json) 记录确认费用、未知费用预留及四个类别的余额。预留既不等于已扣款，也不能当作免费；没有为改善结果而重置账本。

2026-10-02 12:10 UTC 快照：**确认费用 US$0.647956，待确认预留 US$0.113384**，合计占用 US$0.761340。开发确认费用 US$0.043665，评测 US$0.494445，联网 US$0.109846，演示修复预留类别尚未消耗。上述数字包含全部失败与修复历史，不仅是最终 24 次运行。

## 三分钟实际演示

[演示录像](../artifacts/demo/briefforge-demo.webm) 为实际浏览器操作，媒体时长 181 秒、1280×800。展示旧版 SSO 说明与当前条款、任务与补查记录、逐字证据、价格从 12 改为 15 的资料版本、局部重算、历史报告和实际 Word/PPT 导出。旁白字幕明确标记虚构资料与固定响应回放；两次录制中的研究均为 0 次模型请求、US$0。

[录屏验证](../artifacts/demo/recording-results.json) 保存场景时间、实际项目及运行记录，浏览器错误为 0。成片只裁去了 6.92 秒的录制启动等待；未经剪辑的原始录制及早先失败的脚本尝试保留在被忽略的本地诊断目录中。

目前验证范围是本机单用户工作台。多人认证、工作区权限、生产部署及真实用户效率提升不在已验证结论内。
