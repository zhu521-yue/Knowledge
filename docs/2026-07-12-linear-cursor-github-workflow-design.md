# Cursor、Linear 与 GitHub 协作流程设计

日期：2026-07-12  
状态：已通过对话设计评审和规格自检，待用户书面复核  
适用项目：Linear `Knowledge` / GitHub `zhu521-yue/Knowledge`

## 1. 目标

建立一套贯通 Cursor、Linear 和 GitHub 的开发流程：

- Cursor 根据项目文档动态规划并执行任务。
- Linear `Knowledge` 项目实时展示当前任务结构和完成进度。
- Cursor 在 Milestone 内逐个实现、测试和交付 Sub-issue，用户只进行阶段性审核。
- 用户批准当前 Milestone 启动后，Cursor 可在该阶段内自主 Commit、Push、创建并合并 Pull Request；进入下一 Milestone 前必须重新获得用户批准。
- GitHub PR 与 Linear Issue 自动关联，PR 合并后同步完成状态。
- 执行期间发现的新工作、范围变化和阻塞必须同步到 Linear，不维护长期失真的独立任务清单。

当前范围覆盖项目文档中的全部阶段 M0–M8，而不只覆盖 MVP。

## 2. 系统职责

### 2.1 Cursor：动态规划与执行中心

Cursor 负责：

- 从项目文档拆解 M0–M8。
- 根据实现证据新增、拆分、合并或取消任务。
- 一次执行一个可测试的 Sub-issue。
- 编写代码、运行自动测试并记录可复现测试步骤。
- 在用户批准当前 Milestone 后，自主完成该阶段的 GitHub 交付。
- 将任务结构和状态变化实时同步到 Linear。

Cursor 可以改变计划，但不能让 Linear 长期落后于真实计划。

### 2.2 Linear：实时可视化镜像

Linear `Knowledge` 项目负责展示：

- M0–M8 的阶段进度。
- Parent Issue 和 Sub-issue 的当前状态。
- 依赖、阻塞、取消和重复关系。
- 关联的 GitHub 分支、Commit 和 PR。
- 每个任务的验收条件与用户测试要求。

Linear 不是静态初始计划。Cursor 中发生的有效任务变化必须同步到 Linear。

### 2.3 GitHub：阶段授权后的成果记录

GitHub 保存当前 Milestone 授权范围内通过自动验证的成果：

- 用户批准 Milestone 启动前，不创建该阶段任务 Commit、不 Push、不创建 PR。
- Milestone 获批后，Cursor 为各 Sub-issue 创建与 Linear Issue 编号关联的分支、Commit 和 PR，并在检查通过后合并。
- PR 未合并时，Linear Sub-issue 不得进入 `Done`。
- `.env`、凭据文件、本地数据和密钥不得提交。

## 3. 任务层级

采用三级结构：

```text
Knowledge Project
└── Milestone：阶段成果
    └── Parent Issue：可独立验收的功能模块
        └── Sub-issue：一次实现、自动测试和 GitHub 交付周期可完成的任务
```

拆解规则：

- Milestone 表示阶段性业务成果。
- Parent Issue 表示可以独立验收的模块。
- Sub-issue 按完整行为拆分，不按文件或技术层机械拆分。
- 一个 Sub-issue 应能在一次执行周期内完成实现、自动测试和可复现验证。
- 一个 Sub-issue 原则上对应一个 Git 分支和一个 PR。

采用滚动细化：

- M0–M8 Milestone 立即建立。
- M0–M8 Parent Issue 立即建立，用于展示完整路线。
- 只详细创建当前 Milestone 的 Sub-issue。
- 当前 Milestone 完成后，再细化下一 Milestone。

## 4. 阶段结构

### M0：冻结架构与契约

Parent Issues：

1. 统一项目范围与术语。
2. 冻结领域模块边界。
3. 冻结数据模型与状态机。
4. 冻结 API、错误和幂等协议。
5. 冻结隐私授权与 Provider 契约。
6. 冻结测试策略与性能预算。
7. 建立 Linear、Cursor、GitHub 协作规范。

### M1：基础设施与身份边界

Parent Issues：

1. 建立前后端项目骨架。
2. 建立 Docker Compose 本地运行环境。
3. 建立 MySQL 与 Alembic 迁移体系。
4. 建立 Milvus 与本地文件卷。
5. 建立 FastAPI API / Worker 双进程。
6. 建立 Outbox、任务租约和幂等执行框架。
7. 建立本地用户、管理员初始化和邀请码注册。
8. 建立服务端 Session 与安全 Cookie。
9. 建立 Provider 凭据加密存储。
10. 建立健康检查、日志和配置校验。

### M2：统一资料导入与检索索引

Parent Issues：

1. 建立 Topic 与资料领域模型。
2. 实现不可变资料版本和内容 hash 去重。
3. 实现文本型 PDF 上传。
4. 实现粘贴文本导入。
5. 实现静态网页 URL 导入与 SSRF 防护。
6. 实现统一 `ParsedDocument`。
7. 实现 Parent/Child Chunk。
8. 实现远程 Embedding Provider。
9. 实现 Dense 索引。
10. 实现 BM25/稀疏索引。
11. 实现 RRF 融合与 Parent 折叠。
12. 实现 Topic 内 Top 3 检索接口和测试页。
13. 实现 IngestionRun 进度、失败补偿和安全重试。
14. 实现资料归档、回收站和彻底删除。

### M3：知识点草稿与确认

Parent Issues：

1. 建立 `KnowledgePointDraft` 抽取协议。
2. 实现 LLM Provider 与结构化输出。
3. 实现知识点颗粒度控制。
4. 实现来源引用和可追溯性。
5. 实现相似知识点关联建议。
6. 实现草稿确认工作台。
7. 实现草稿编辑、通过和拒绝。
8. 实现正式 `KnowledgePoint`。
9. 实现 `review_policy`。
10. 实现手动创建和抽取失败降级。

### M4：SSE、费曼与 Rubric

Parent Issues：

1. 建立持久化学习 Session。
2. 实现 SSE 事件信封和有限重放。
3. 实现分层讲解流程。
4. 实现断线恢复、取消和重试。
5. 实现费曼复述提交。
6. 建立不可变 Rubric 版本。
7. 实现 0–4 逐项判题。
8. 实现答案与资料的双向证据。
9. 实现 `weakness_tags`。
10. 实现 Medium dispute 和补偿事件。
11. 实现评分审计与版本追踪。

### M5：BKT 与复习闭环

Parent Issues：

1. 建立不可变 `LearningEvent`。
2. 建立 `MasteryModelConfig`。
3. 实现 BKT v1 状态归约。
4. 实现 `MasteryState` 投影。
5. 实现事件重放与投影重建。
6. 实现时间衰减和有效掌握度。
7. 建立 `ReviewTask`。
8. 实现 Easy / Medium / Hard 复习。
9. 实现主动复习队列。
10. 实现动态间隔和下一次复习时间。
11. 实现复习熔断、延期和积压控制。
12. 完成一次真实到期复习闭环。

### M6：固定数据集与系统验收

Parent Issues：

1. 建立固定 PDF、文本和网页数据集。
2. 建立导入与去重回归测试。
3. 建立 Parent/Child 混合检索评测。
4. 建立知识点抽取与确认回归。
5. 建立 SSE 断线恢复测试。
6. 建立 Rubric 与 dispute 回归。
7. 建立 BKT 与复习闭环回归。
8. 建立隐私授权与外联检查。
9. 建立失败恢复与幂等测试。
10. 建立 MVP 性能预算报告。
11. 建立干净环境端到端验收。

### M7：资料发现与完整 RAG

Parent Issues：

1. 实现 `data/notes/` 扫描。
2. 建立 `SourceCandidate`。
3. 实现主题资料推荐。
4. 实现 GitHub Trending 数据源。
5. 实现外部论文与资讯搜索。
6. 实现候选资料确认入库。
7. 实现完整 RAG 引用问答。
8. 实现回答证据校验。
9. 实现 Markdown 对话导出。
10. 建立 RAG 固定评测集。

### M8：探索、伴侣与画像

Parent Issues：

1. 建立 Atlas v2 数据模型。
2. 实现知识关系可视化。
3. 建立 AI 伴侣工具权限模型。
4. 实现有副作用工具确认。
5. 实现跨会话记忆。
6. 建立认知画像。
7. 实现画像查看、编辑和删除。
8. 实现可选情绪时序。
9. 实现知识点合并与拆分。
10. 实现知识点质量优化。
11. 建立新能力与学习事实源的集成测试。

## 5. 状态机与审批门禁

Linear 使用现有状态：

```text
Backlog → Todo → In Progress → In Review → Done
```

辅助终态为 `Canceled` 和 `Duplicate`。

状态规则：

- `Backlog → Todo`：任务进入近期执行范围。
- `Todo → In Progress`：Cursor 实际开始处理。
- `In Progress → In Review`：实现和必要自动测试完成，验证记录已准备。
- `In Review → In Progress`：自动测试、PR 检查或复核失败。
- `In Review → GitHub`：当前 Milestone 已获得用户阶段授权后，由 Cursor 执行。
- `In Review → Done`：对应 PR 已合并。
- 未完成任务不再需要时标记 `Canceled`，不删除历史。
- 被其他任务覆盖时标记 `Duplicate`。

`Approved` 和 `PullRequest` 是流程逻辑状态，不新增 Linear Status。PR 创建后，Linear 保持 `In Review`，直到 PR 合并。

Parent Issue 完成规则：

- 必要 Sub-issue 全部 `Done` 后，Parent Issue 可由 Cursor 置为 `Done`。
- Parent Issue 的验收证据纳入 Milestone 阶段审核，不要求用户逐模块审批。

Milestone 完成规则：

- 必要 Parent Issue 全部 `Done`。
- 阶段端到端验收通过。
- 对应 PR 全部合并。
- 配置说明完整。
- 没有未记录的阻塞项。
- 下一阶段前置条件具备。

## 6. 逐阶段、逐任务执行

一次只推进一个 Milestone，一次只执行一个可测试的 Sub-issue：

```text
选择当前 Milestone
→ 用户批准阶段启动
→ 细化本阶段 Sub-issues
→ 选择一个 Todo
→ Cursor 实现
→ 自动测试
→ Linear: In Review
→ Commit / Push / PR
→ PR 检查通过并合并
→ Linear: Done
→ 处理本阶段下一个 Sub-issue
→ 阶段端到端验收
→ 用户阶段性审核
→ 用户批准后进入下一 Milestone
```

用户对当前 Milestone 的启动批准，授权 Cursor 连续交付该阶段内符合既定范围的 Sub-issue。出现账号、密钥、费用、服务地址、端口、数据目录、隐私授权、重大架构变化或阶段范围扩张时，授权自动暂停并等待用户决策。

后续 Milestone 保留 Parent Issue 路线图，但在进入该阶段前不提前生成大量细粒度 Sub-issue。

## 7. 验证记录与阶段审核包

Sub-issue 进入 `In Review` 时，Cursor 必须记录：

```text
任务：
实现内容：
启动方式：
测试前置条件：
测试步骤：
预期结果：
自动测试结果：
已知限制：
需要用户配置：
计划 Commit：
计划 PR：
```

可复现测试步骤必须描述可直接操作和观察的行为，不能只报告内部单元测试结果。Sub-issue 记录供 Cursor 自检和阶段审核汇总使用，不要求用户逐项确认。

Milestone 内不再要求逐 Sub-issue GitHub 审批。只有以下情况必须暂停并获得用户明确决定：

```text
进入新的 Milestone
需要账号、密钥、费用、隐私授权或环境配置
需要改变已批准的架构或阶段范围
阶段验收失败且需要调整目标
```

“继续”只表示继续当前已授权阶段，不自动授权进入下一 Milestone。

## 8. 用户配置参与

以下配置必须由用户参与，不由 Cursor 擅自决定或填入：

- GitHub、Linear 和第三方账号授权。
- LLM API Key。
- Embedding 网关 API Key。
- Provider 地址。
- 管理员初始密码和邀请码。
- 加密主密钥。
- 外部发送资料的隐私授权。
- 域名、HTTPS 证书和远程节点。
- 可能产生费用的模型选择。
- 端口冲突后的端口调整。
- 数据目录和磁盘预算调整。

配置协作流程：

1. Cursor 说明配置用途、格式和风险。
2. 用户在本机配置，不在对话中发送密钥明文。
3. Cursor 只验证配置是否生效，不展示密钥。
4. 配置成功后继续执行。
5. `.env`、凭据和本地业务数据不得提交 GitHub。

配置未完成时，任务保持 `In Progress` 并明确记录阻塞原因。

## 9. Linear Issue 规范

每个 Sub-issue 至少包含：

- 行为型标题。
- 所属 Parent Issue 和 Milestone。
- 问题背景。
- 实现范围与非范围。
- 验收条件。
- 用户测试步骤。
- 依赖和阻塞关系。
- 关联设计决策，例如 D04、D11。
- 配置需求。
- GitHub PR 链接。

标题使用明确动词，例如：

```text
实现 SourceRevision 内容哈希去重
支持 PDF 导入任务失败后安全重试
验证 SSE 断线后的事件重放
```

## 10. GitHub 规范

以 Linear Issue `LEA-23` 为例。

分支：

```text
feature/lea-23-source-revision-deduplication
fix/lea-23-ingestion-retry
test/lea-23-sse-replay
docs/lea-23-freeze-api-contract
```

Commit：

```text
LEA-23 implement content-addressed source revision deduplication
```

PR：

```text
LEA-23: implement source revision deduplication
```

规则：

- 一个 Sub-issue 原则上对应一个分支和一个 PR。
- 不把多个无关任务放入同一 PR。
- 分支、Commit 和 PR 必须包含 Linear Issue 编号。
- PR 正文包含验收条件、测试结果和所属 Milestone 的阶段授权记录摘要。
- PR 合并前 Linear 保持 `In Review`。

## 11. 动态变化与异常处理

任务变化：

- 小调整且不改变验收目标：更新当前 Issue 描述。
- 新增独立行为：新增 Sub-issue。
- 新需求跨多个模块：新增 Parent Issue。
- 当前任务明显过大：暂停并拆分。
- 整个任务失效：标记 `Canceled`。
- 被其他任务覆盖：标记 `Duplicate`。
- 不删除已有任务历史。

异常处理：

- 自动测试失败：保持 `In Progress`。
- 自动测试或复核失败：回到 `In Progress`。
- 阶段审核未通过：相关任务回到 `In Progress`，修复后重新执行阶段验收。
- Push 或 PR 创建失败：保持 `In Review`，记录错误，避免重复 Commit。
- PR 检查失败：保持 `In Review`，修复当前任务或新增修复 Sub-issue。
- PR 被关闭但未合并：保持 `In Review`。
- 外部服务不可用：记录阻塞，不伪造完成。
- 新发现不影响当前验收：创建新 Issue，不阻塞当前 PR。
- 架构问题导致范围变化：先提出调整方案，用户确认后更新任务结构。

## 12. 测试策略

每个 Sub-issue 至少验证与其职责匹配的层级：

- 纯函数和领域规则：单元测试。
- 数据库、任务、Provider Adapter：集成测试。
- API 协议：契约测试。
- 用户可见流程：端到端或可重复的手动测试步骤。
- 状态机和幂等行为：重复提交、失败重试和并发边界测试。
- 隐私相关功能：未授权外联检查和日志脱敏检查。

阶段验收不能只依赖单个 Sub-issue 测试，必须执行对应 Milestone 的端到端验收。

## 13. 已验证的连接

2026-07-12 已完成最小联动测试：

- Linear MCP 可读取和修改 `Learnz` 团队的 `Knowledge` 项目。
- GitHub CLI 已连接账号 `zhu521-yue`。
- GitHub 仓库 `zhu521-yue/Knowledge` 可管理。
- Linear 测试 Issue `LEA-5` 已自动关联 GitHub PR #1。

该结果证明 Linear MCP、GitHub 和 Linear GitHub Integration 的基础链路可用。

## 14. 开始实施前的阻塞项

- 本地目录 `d:\Python Project\个体知识库` 尚未检测到 Git 仓库，需要明确采用克隆远程仓库还是将现有目录初始化后关联远程仓库。
- Linear 测试任务 `LEA-5` 和 GitHub PR #1 尚未合并或清理，应由用户确认测试产物的处理方式。
- 进入任务实施前，先建立 M0–M8 Milestone 与 Parent Issue，再细化 M0 的 Sub-issues。