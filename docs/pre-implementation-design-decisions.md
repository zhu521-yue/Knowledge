# 开工前设计决策记录

版本：v0.2  
状态：D01-D14 已收敛，D12 延期  
关联方案：[personal-knowledge-base.md](./personal-knowledge-base.md)

## 1. 文档目的

本文档用于记录正式编码前必须冻结的设计决策，避免在建表、定义 API、实现 Worker 或联调前端时反复修改核心模型。

讨论原则：

- 一次只讨论一个决策点。
- 每项必须形成明确结论、理由、约束和验收条件。
- 未冻结的事项不得被实现代码隐式决定。
- 已冻结结论如需修改，必须记录变更原因和影响范围。
- 本文档记录讨论过程；最终稳定结论同步到正式项目文档。

## 2. 已冻结的基础前提

以下内容不在本轮重复讨论：

- MVP 闭环：导入 → 解析与父子切片 → Dense/BM25 + RRF Top 3 检索 → 知识点草稿 → 用户确认 → SSE 讲解 → 费曼复述 → Rubric 判题 → `weakness_tags` → 主动复习 → 更新 `MasteryState`。
- 本机保存业务数据并只监听 localhost；外部 LLM 和远程 Embedding 都必须按数据类别获得明确授权。
- Docker Compose 部署。
- MySQL 保存全部结构化业务数据。
- Milvus 保存向量，本地挂载卷保存原始资料和派生产物。
- LLM 默认调用用户配置的官网或兼容中转站 API；Embedding 默认调用受 HTTPS API Key 网关保护的远程 Ollama。
- 第一版建立正式 `User`、本地登录、邀请码和 `user_id` 数据归属。
- FastAPI API 与 Worker 分离，MVP 不引入 Redis / Celery。
- 父子切片与 Topic 内 Top 3 检索进入 MVP；主题推荐、完整 RAG 回答、Atlas、AI 伴侣和画像不进入 MVP 主线。

## 3. 决策状态总览

| 编号 | 优先级 | 决策主题 | 状态 |
| --- | --- | --- | --- |
| D01 | P0 | 知识对象归属与去重语义 | 已冻结 |
| D02 | P0 | 学习事件与掌握度归约规则 | 已冻结 |
| D03 | P0 | Rubric 与判题版本协议 | 已冻结 |
| D04 | P0 | 后台任务与跨存储一致性 | 已冻结 |
| D05 | P0 | 模型运行契约 | 已冻结 |
| D06 | P1 | 用户初始化与会话安全 | 已冻结 |
| D07 | P1 | 资料版本与删除语义 | 已冻结 |
| D08 | P1 | SSE Session 生命周期 | 已冻结 |
| D09 | P1 | 时间与复习队列语义 | 已冻结 |
| D10 | P1 | 隐私授权执行语义 | 已冻结 |
| D11 | P2 | 统一 API 错误与幂等协议 | 已冻结 |
| D12 | P2 | 备份、恢复与升级 | 已延期 |
| D13 | P2 | MVP 性能预算 | 已冻结 |
| D14 | P1 | 父子文档与混合检索协议 | 已冻结 |

状态只使用：`待讨论`、`讨论中`、`已冻结`、`已延期`、`重新评估`。

## 4. 推荐讨论顺序

```mermaid
flowchart LR
  D01[知识对象归属] --> D02[事件与掌握度]
  D02 --> D03[Rubric 版本协议]
  D03 --> D04[任务与跨存储一致性]
  D04 --> D05[模型与 Provider 契约]
  D05 --> D06[身份与 Session]
  D06 --> D07[资料版本与删除]
  D07 --> D08[SSE 生命周期]
  D08 --> D09[时间与复习队列]
  D09 --> D10[隐私授权]
  D10 --> D11[API 协议]
  D11 --> D12[备份与升级]
  D12 --> D13[性能预算]
```

## 5. P0：建表和接口前必须冻结

### D01. 知识对象归属与去重语义

状态：已冻结

核心问题：

- `KnowledgePoint` 是某份资料内部的知识点，还是跨资料共享的用户知识概念？
- 同一概念来自多份资料时，是创建多个知识点，还是一个知识点关联多个来源？
- 相同文件重复上传、相同 URL 重复导入、网页内容更新时如何处理？
- AI 重复抽取相似知识点时，自动合并、提示用户还是始终创建新草稿？
- 删除一个来源后，共享知识点及其学习历史是否保留？

推荐方向：

```text
KnowledgePoint
  └── KnowledgePointSourceRef[]
        ├── SourceDocument / SourceRevision
        ├── Chunk / 页码 / URL 定位
        └── 来源支持的具体内容
```

- `KnowledgePoint` 表示用户确认的、可跨资料复用的知识概念。
- 来源使用多值关联 `KnowledgePointSourceRef`，不在知识点聚合根中写死单个 `source_document_id`。
- MVP 每个知识点可以只有一个来源，但模型允许后续增加来源。
- 去重只生成建议，不自动修改正式知识点。

已确认结论：采用“共享概念 + 多来源关联”模型。

仍待确认：

- 删除最后一个来源后，知识点和学习历史的保留规则。

资料重复与版本处理已确认：

- 内容 hash 完全相同：复用现有 `SourceRevision`，不重复解析、抽取和向量化。
- 同一 URL 内容 hash 变化：创建新的不可变 `SourceRevision`，并将其设为当前版本。
- 同名本地文件内容 hash 变化：提示用户是否作为当前资料的新版本，不仅凭文件名自动判断。
- 用户界面默认只展示当前版本，历史版本在版本记录中查看。
- 新的解析、抽取和学习流程默认使用当前版本；旧知识点来源、Rubric、评分和学习事件继续引用原版本。
- 只有用户执行彻底删除时，才清理历史版本和对应派生产物。

相似知识点处理已确认：

- 系统只生成“关联到已有知识点”的建议。
- 用户确认后，为已有 `KnowledgePoint` 增加新的 `KnowledgePointSourceRef`。
- 系统不得根据相似度自动合并或自动改写正式知识点。
- 用户拒绝关联建议时，原草稿继续作为独立候选。

最后来源删除规则已确认：

- 删除最后一个 `KnowledgePointSourceRef` 时，保留 `KnowledgePoint`、`LearningEvent`、`MasteryState` 和历史评分。
- 将知识点标记为 `source_missing`，从新的证据型讲解、判题和自动复习调度中暂停。
- 历史记录继续可查看，不因来源删除而改写或丢失。
- 用户补充并确认新来源后，清除 `source_missing` 并恢复正常学习状态。
- 用户明确要求彻底删除知识点时，才进入独立的数据删除流程。

D01 最终结论：

```text
KnowledgePoint（跨资料概念）
  ├── KnowledgePointSourceRef[]（引用不可变 SourceRevision）
  ├── LearningEvent[]（不随来源删除）
  └── MasteryState（事件投影）
```

影响范围：

- `KnowledgePoint`
- `KnowledgePointDraft`
- `KnowledgePointSourceRef`
- 来源删除和版本更新
- Milvus metadata
- 未来知识融合

### D02. 学习事件与掌握度归约规则

状态：已冻结

必须明确：

- `LearningEvent` 的事件类型、payload schema 和版本。
- 事件全局顺序与单知识点顺序。
- 重复消费和并发复习处理。
- Easy / Medium / Hard 的证据权重。
- 首次学习、通过、失败、补救讲解和 dispute 补偿如何更新 BKT。
- BKT 参数升级后是否重放历史。

推荐原则：`LearningEvent` 是事实，`MasteryState` 是可重建投影。

已确认：

- `LearningEvent` 是不可变的学习事实，不因 dispute、算法调整或来源删除而覆盖。
- `MasteryState` 是持久化投影，用于高效查询，不是独立事实源。
- 正常学习命令在同一个 MySQL 事务中追加事件并增量更新投影。
- dispute 通过补偿事件修正，不修改原事件。
- 系统必须提供按 `user_id + knowledge_point_id` 重放事件并重建投影的能力。

复习证据强度已确认：

- Easy：用户自评，成功和失败都只作为弱证据，避免主观状态大幅改变掌握度。
- Medium：AI 基于标准复述判题，作为标准证据。
- Hard：AI 基于完整费曼解释和追问判题，作为强证据。
- 成功与失败都按对应模式的证据强度进入固定版本 BKT 更新。
- Easy 自评失败仍可缩短下次复习间隔，但掌握度只做弱负向更新。

MVP 固定 BKT 参数已确认：

| 参数 | Easy | Medium | Hard |
| --- | ---: | ---: | ---: |
| `p_guess` | 0.40 | 0.20 | 0.08 |
| `p_slip` | 0.35 | 0.15 | 0.10 |

全局参数：

- `p_init = 0.20`。
- `p_learn = 0.10`，只在产生有效复述或复习结果时应用。
- `p_mastery` 截断范围为 0.05-0.98。
- 参数属于固定 `model_version = bkt-v1`，MVP 不提供动态调整。
- 实现前使用固定样例验证：Easy 小幅变化、Medium 标准变化、Hard 明显变化、连续通过逐渐收敛且单次偶然失败不会摧毁高掌握度。

时间衰减模型已确认：

- `MasteryState.p_mastery` 保存最近一次有效掌握证据经过归约后的“证据锚点”，并记录 `mastery_evaluated_at`；它不由每日 cron 反复改写。
- 查询、展示、复习推荐和下一次新证据归约使用随时间变化的 `effective_mastery(at_time)`：

\[
effectiveMastery(t) = p_{floor} + (p_{mastery} - p_{floor}) \times 2^{-elapsedDays / halfLifeDays}
\]

- `p_floor = 0.05`；首次通过标准费曼考核后 `half_life_days = 7`，半衰期截断在 0.5-180 天。
- 成功证据按模式延长半衰期：Easy ×1.2、Medium ×1.6、Hard ×2.0；失败证据按模式缩短：Easy ×0.9、Medium ×0.6、Hard ×0.5。
- 新的有效考核事件到来时，先根据上一锚点、上一半衰期和两个事件的 UTC 时间差计算事件发生时的 `effective_mastery`，再把它作为固定 `bkt-v1` 的先验执行本次更新，形成新的 `p_mastery` 锚点。
- 衰减属于独立固定 `retention_model_version = retention-decay-v1`，不替换 BKT 参数版本；`MasteryState` 同时保存 `half_life_days`、`mastery_evaluated_at` 和两个版本号。
- 补救讲解、打开页面、进入复习模块、延期和未完成尝试都不重置衰减起点；只有带有效 `mastery_effect` 的事件形成新锚点。
- 不创建“每天衰减一次”的 `LearningEvent`。查询结果可以缓存，但必须携带 `evaluated_at`，缓存失效不能改变事实投影。
- dispute 重放按事件自身 `occurred_at` 顺序重新执行衰减与 BKT 更新，因此在指定评估时间下可得到确定结果。
- 实现前增加固定时钟样例，验证无新证据时有效掌握度单调下降、强证据比弱证据延长更久、失败缩短半衰期且重放结果稳定。

补救讲解规则已确认：

- `remedial_tutoring_completed` 只记录一次接触和补救行为，不直接更新 `p_mastery`。
- 讲解完成后必须通过新的费曼复述或复习 Attempt 产生掌握证据。
- 禁止因播放完、阅读完或 SSE 输出完成而把“接触过”推断成“已掌握”。

事件顺序与重复提交规则已确认：

- 产品层面，同一用户对同一知识点保持单线复习；技术层必须防止重复点击、网络重试、SSE 恢复和 Worker 接管造成重复完成。
- 同一 `user_id + knowledge_point_id` 同时最多存在一个 active `ReviewAttempt`。
- 创建 Attempt 时生成唯一 `attempt_id`，提交命令携带 `Idempotency-Key`。
- 同一 Attempt 只能完成一次；重复命令返回第一次持久化的结果，不追加新事件。
- 不同 Attempt 更新同一 `MasteryState` 时，使用数据库行锁并按知识点级单调 `sequence` 串行提交。
- `LearningEvent` 对 `(user_id, knowledge_point_id, sequence)` 建立唯一约束，对幂等键建立唯一约束。
- 禁止使用最后写入覆盖前一次掌握度的策略。

BKT 版本策略已确认：

- MVP 固定使用一个 BKT 算法和一套参数，不提供在线升级、自动历史重放或迁移 UI。
- `LearningEvent.model_version` 与 `MasteryState.model_version` 仍保留固定版本值，用于复现和解释结果。
- 如未来确需修改算法，必须作为新的设计决策处理，不在当前系统中预埋自动迁移行为。

LearningEvent 记录范围已确认：

- 记录讲解完成、补救讲解、考核结果、复习结果、dispute 修正和调度变化等领域事件。
- 只有显式携带 `mastery_effect` 的事件参与 BKT 归约。
- 页面点击、展开、滚动和暂停等 UI 遥测不进入 `LearningEvent`。
- 调试日志与学习事实分离，不能使用日志重建业务状态。

MVP 事件类型已确认：

| 事件 | 是否影响掌握度 | 说明 |
| --- | --- | --- |
| `tutoring_started` | 否 | 开始正式讲解 |
| `tutoring_completed` | 否 | 完成正式讲解 |
| `remedial_tutoring_started` | 否 | 开始补救讲解 |
| `remedial_tutoring_completed` | 否 | 完成补救讲解 |
| `assessment_started` | 否 | 开始费曼考核 |
| `assessment_completed` | 是 | 考核产生通过或失败证据 |
| `assessment_corrected` | 是 | dispute 推翻或替换原判，触发重放 |
| `review_started` | 否 | 开始到期复习 |
| `review_completed` | 是 | Easy / Medium / Hard 复习结果 |
| `review_scheduled` | 否 | 创建或调整下一次复习任务 |

开始但未完成的流程由 Attempt / Session 状态负责超时或取消；不为每个 UI 中断额外制造学习事件。新增事件类型必须经过设计评审。

统一 LearningEvent 信封已确认：

- 事件表显式保存：`event_id`、`schema_version`、`user_id`、`knowledge_point_id`、`sequence`、`event_type`、`attempt_id`、`correlation_id`、`causation_id`、`idempotency_key`、`occurred_at`。
- 类型特有数据放入版本化 `payload`，但不得把常用查询键全部塞入 JSON。
- 影响掌握度的事件 payload 还必须包含：`mode`、`outcome`、`score`、`model_version`、`retention_model_version`、事件发生时的 `effective_mastery_before`、`mastery_after`、`half_life_before`、`half_life_after` 和 `mastery_effect`。
- `assessment_corrected` 必须引用被修正事件；归约器忽略被替换的判题结果并按完整事件流重建，不能尝试用简单负数抵消非线性 BKT 更新。

D02 最终结论：`LearningEvent` 是不可变、可排序、可幂等处理的学习事实；`MasteryState` 是使用固定 `bkt-v1` 与 `retention-decay-v1` 生成的可重建投影。存储的 `p_mastery` 是最近证据锚点，对外展示和复习推荐使用按指定时间计算的 `effective_mastery`。

### D03. Rubric 与判题版本协议

状态：已冻结

必须明确：

- Rubric 的生成时机和失效条件。
- Rubric、Prompt、模型和评分结果的版本关系。
- 结构化判题输出 schema。
- 来源证据格式。
- dispute 的重评次数和最终裁决权。
- 用户是否能人工接受或否决 AI 评分。

Rubric 生成时机已确认：

- `KnowledgePoint` 通过用户确认后，创建异步 Rubric 生成任务。
- 知识点确认事务不等待模型调用；Rubric 状态使用 `pending` / `generating` / `ready` / `failed`。
- 首次考核只能使用 `ready` Rubric；尚未生成完成时明确展示等待或重试，不临时使用无版本判题标准。
- Rubric 生成失败不回滚已经确认的 `KnowledgePoint`。

Rubric 失效规则已确认：

- 修改标题、核心解释、来源引用、难度或 `review_policy` 等语义字段时，当前 Rubric 标记为 `superseded`，异步生成新版本。
- 仅修改标签、展示顺序等非语义字段时，Rubric 继续有效。
- 已完成的 `AssessmentAttempt` 永远引用当时使用的 Rubric 版本，不随新版本改写。
- 新 Rubric 未进入 `ready` 前，不允许使用已失效版本开始新的考核。

Dispute 裁决规则已确认：

- 每个 `AssessmentAttempt` 最多发起一次自动重评，重评必须使用同一 Rubric 版本和同一用户答案，避免改变题目后伪装成复核。
- 重评生成新的评分版本，不覆盖原评分。
- 重评后用户可以：接受原判、接受重评、或将本次 Attempt 标记为“不计入掌握度”。
- 用户不能直接手填分数或强制改为通过。
- 选择“不计入掌握度”时追加 `assessment_corrected`，引用原判题事件并重建 `MasteryState`；历史评分仍保留可查看。
- 已完成裁决的 dispute 不允许重复发起；用户可以通过新的 Attempt 重新作答。

判题证据规则已确认：

- 每个 Rubric 评分项必须保存用户答案证据 `answer_quotes[]`，包含原文片段及字符位置。
- 每个评分项必须保存标准依据 `source_refs[]`，引用不可变 `SourceRevision`、Chunk、页码或段落定位。
- 每个评分项必须保存 `missing_points[]` 和简明反馈。
- 无法提供来源依据时，该评分项不得给满分；来源不足时应标记 `insufficient_evidence`，不能用模型常识伪装资料依据。
- AI 的文字理由只是解释，不可替代结构化双向证据。

Rubric 与评分存储已确认：

- 使用关系表保存可约束、可查询的业务事实：`RubricVersion`、`RubricCriterion`、`AssessmentAttempt`、`AssessmentGradeVersion`、`ScoreItem`。
- `RubricCriterion` 保存稳定 criterion key、0-4 评分锚点、权重、是否必需及来源要求。
- `ScoreItem` 逐项关联 criterion，并保存分数、`answer_quotes`、`source_refs`、`missing_points` 和证据状态。
- 同时保存生成时的 `schema_snapshot` 与脱敏 `raw_output`，仅用于审计和故障分析。
- 模型输出必须先通过 schema 校验并写入关系表，业务逻辑不得直接读取 `raw_output` 判定通过或更新掌握度。

完整版本链已确认：

- 每个 `RubricVersion` 必须记录 `knowledge_point_revision_id`、`prompt_version`、`schema_version`、Provider、模型、模型配置摘要和生成时间。
- 每个 `AssessmentGradeVersion` 必须记录 `rubric_version_id`、判题 Prompt 版本、判题 schema 版本、Provider、模型、模型配置摘要和判题时间。
- 本地模型可记录可获得的模型 digest；无法获得 digest 时至少记录模型标签、量化规格和 Ollama manifest 摘要。
- 缺失知识点版本、Rubric 版本、Prompt 版本或 schema 版本的评分不得生成影响掌握度的 `LearningEvent`。
- 历史评分引用完整版本链，不因当前模型、Prompt 或知识点变化而改写。

Rubric 评分项结构已确认：

- 固定五个维度作为模板：概念准确性、关键要点覆盖、因果 / 机制解释、举例或迁移能力、边界与误区。
- AI 只能为知识点生成维度内的具体检查点、评分锚点和来源要求，不能自由创造不可比较的顶层维度。
- 不适用维度可标记 `disabled`，剩余权重重新归一化。
- 默认权重为 30% / 25% / 20% / 15% / 10%，每项使用 0-4 分。
- 通过条件保持为：总分至少 70/100、概念准确性至少 2/4，且不存在严重事实错误。

D03 最终结论：Rubric 和判题结果使用关系事实 + 不可变审计快照；完整绑定知识点、Rubric、Prompt、schema 和模型版本；逐项评分必须具备用户答案与资料来源双向证据；dispute 不覆盖历史且用户可选择不计入掌握度。

### D04. 后台任务与跨存储一致性

状态：已冻结

必须明确：

- MySQL、文件卷、Milvus 的提交顺序和检查点。
- 是否采用 MySQL Outbox。
- 租约时长、续租、最大重试和退避规则。
- Worker 崩溃后的任务接管。
- 取消任务的语义和不可取消阶段。
- 幂等键作用域。
- 文件或向量写入成功、MySQL 回写失败时的补偿。

推荐原则：MySQL 是事实源，不使用分布式事务；外部写入可重试、可校验、可重建。

Outbox 策略已确认：

- 业务状态和 Outbox 记录在同一个 MySQL 事务中提交，禁止事务提交后再依赖进程内动作创建任务。
- Worker 从持久化 Job / Outbox 表领取任务，成功、失败、重试和租约状态都可查询。
- Worker 执行必须幂等；重复领取同一 Outbox 记录不能重复创建业务事实。
- API 进程崩溃、Worker 延迟启动或容器重启都不得导致任务丢失。
- MVP 不使用进程内 `BackgroundTasks` 承担关键任务，也不引入 Redis / Celery。

跨存储 Saga 已确认：

1. 上传或抓取内容先流式写入临时目录，计算 hash 并完成类型、大小和完整性校验。
2. 通过校验后使用原子 rename 写入内容寻址的最终路径；相同 hash 复用已有文件。
3. 在一个 MySQL 事务中创建 `SourceRevision`、文件引用和下一步 Outbox 任务。
4. 数据库事务失败时，未被引用的文件由孤儿清理任务回收，不尝试危险的同步跨存储回滚。
5. Worker 根据检查点生成解析产物、Chunk 和 Embedding；每个阶段完成后回写 MySQL 状态并创建下一阶段 Outbox。
6. Milvus 使用由索引版本与业务记录 ID 派生的确定性向量 ID 执行幂等 upsert。
7. 只有 Milvus 写入成功并校验后，MySQL 才把对应索引版本标记为 `indexed`。
8. MySQL 回写失败时允许重复 upsert；Milvus 或派生文件异常时可依据 MySQL 与原始文件重建。

禁止使用“任何一步失败就同步回滚 MySQL、文件和 Milvus”的伪分布式事务。

租约与重试规则已确认：

- 默认租约 60 秒，Worker 每 20 秒续租；不同 `job_type` 可覆盖租约时长，但必须保持同一状态语义。
- 租约过期后，其他 Worker 可以接管任务；原 Worker 失去租约后不得提交最终结果。
- 可重试错误最多自动重试 3 次，默认退避为 10 秒、30 秒、2 分钟。
- MySQL 短暂断连、Ollama / Milvus 暂时不可用和可修复 schema 输出属于可重试错误。
- 文件损坏、格式不支持、超过限制、SSRF 拒绝和业务前置条件缺失属于不可重试错误，直接进入 `failed`。
- 自动重试耗尽后保留脱敏错误和检查点，允许用户手动重试。

文件预处理原子发布规则已确认：

- 每次文件预处理创建一个 `IngestionRun`，统一编排解析、知识点草稿抽取、Chunk 生成和向量化，不向用户暴露为互不相关的最终任务。
- 内部仍拆分阶段以支持进度、重试和检查点：`parsing` → `extracting` → `chunking` → `embedding` → `validating` → `publishing`。
- 各阶段产物携带同一个 `ingestion_run_id` 并写入暂存状态；在 Run 发布前，普通业务查询、知识点确认、检索和学习流程都不可见。
- MySQL 不保持跨模型调用和文件处理的长事务。各阶段可以短事务保存暂存记录，但只有最终发布事务才能切换 `SourceRevision.active_ingestion_run_id`。
- Milvus 向量写入独立的暂存索引版本或使用 `ingestion_run_id + index_version` 隔离；发布前不得被正式检索过滤条件命中。
- `validating` 必须检查解析产物、草稿、Chunk、向量数量、引用完整性和版本链全部一致。
- 验证通过后，在一个短 MySQL 事务中把 Run 标记为 `succeeded`、切换 active 指针并创建后续 Outbox；这一步构成业务上的原子发布点。
- 任一阶段失败或用户取消时，Run 进入 `compensating`；删除或标记清理本 Run 独占的暂存记录、派生文件和向量，恢复到 Run 开始前的业务可见状态。
- 原始 `SourceRevision`、此前已发布的 Run、正式知识点和历史学习记录不属于本次补偿范围。
- 补偿失败时 Run 进入 `compensation_failed`，其产物继续保持不可见，并由清理任务重试。

因此系统保证的是“原子可见性”：一条预处理流水线只有全部成功后才整体发布；失败时用户看不到半成品。

重新预处理的可见性已确认：

- 已有成功 Run 时，新 Run 在暂存区完整执行，旧 Run 继续承担知识点引用、检索和学习服务。
- 新 Run 通过验证并完成原子发布后，`active_ingestion_run_id` 一次性切换到新 Run。
- 新 Run 失败、取消或补偿失败时，active 指针不变，旧 Run 持续可用。
- 禁止按阶段实时替换旧产物，避免解析、Chunk、草稿和向量来自不同 Run。

IngestionRun 幂等规则已确认：

- 幂等配置摘要由 `user_id + source_revision_id + pipeline_version + parser_config + extraction_model_config + chunking_config + embedding_index_version` 计算。
- 相同摘要在 `pending`、`running`、`cancel_requested` 或 `compensating` 状态下只能存在一个 Run；重复请求返回已有 `run_id`。
- 相同摘要已有成功 Run 时，普通重复请求返回现有结果，不重新消耗解析和模型资源。
- 用户明确选择“强制重建”时，必须创建唯一 `rebuild_request_id`、记录原因并生成新 Run；仍采用暂存和原子发布。
- 各内部 Job 使用 `ingestion_run_id + stage + stage_version` 作为幂等作用域。

D04 最终结论：文件预处理以 `IngestionRun` 作为一致性聚合，内部阶段可重试但只整体发布；MySQL Outbox 保证任务不丢失，暂存与补偿保证失败后业务数据完整、干净且不可见。

### D05. 模型运行契约

状态：已冻结（已按最终部署拓扑修订）

部署边界已确认：

- Web、FastAPI、Worker、MySQL、Milvus、原始资料、解析产物、学习记录和用户 Provider 凭据全部运行或保存在用户本机。
- 远程 GPU 服务器配置为 24 GB 显存、90 GB 内存、30 GB 系统盘和 50 GB 数据盘，只运行 Ollama 模型服务与安全 API 网关，不运行知识库业务服务或持久化业务数据。
- LLM 任务默认调用用户配置的模型官网或兼容中转站 API；远程 Ollama 在 MVP 中默认承担 Embedding，不作为 LLM 主路由。
- PDF 以文本信息为主，文本提取、规范化、分页定位和 Chunk 切分在用户本机使用确定性代码完成，不调用模型。

远程 Ollama 安全契约已确认：

- 禁止将 Ollama 原生端口直接暴露公网。公网只开放 HTTPS API 网关，网关转发到仅内部可见的 Ollama。
- 网关使用独立高熵 API Key 认证，并配置 TLS、请求体上限、连接/请求速率限制、并发限制和超时。
- 网关与 Ollama 日志不得记录请求正文、Embedding 输入、Authorization 头或完整响应；只允许记录 request ID、模型、耗时、状态和大小等脱敏元数据。
- API Key 在本机按 D06 的密钥规则加密保存；远程网关只保存不可逆校验值或由 Secret 注入的认证材料。
- 网关拒绝未认证请求，不允许列出、拉取或删除模型等管理接口对公网开放。

LLM 路由已确认：

| 任务 | 默认 Provider | 失败语义 |
| --- | --- | --- |
| PDF 文本解析、清洗、定位和切分 | 本机确定性代码 | 不调用模型 API |
| 知识点草稿抽取 | 用户配置的外部 LLM API | API 失败后任务失败或按 D04 重试 |
| Rubric 初稿生成 | 用户配置的外部 LLM API | API 失败后任务失败或按 D04 重试 |
| 分层讲解和摘要 | 用户配置的外部 LLM API | API 失败后返回统一失败状态 |
| 正常费曼判题 | 用户配置的外部 LLM API | 无有效版本或证据时不得形成 BKT 事件 |
| dispute 的唯一一次自动重评 | 用户配置的外部 LLM API | 必须创建独立评分版本 |
| Embedding | 远程 Ollama | 失败时允许整条 Run 切换外部 Embedding API |

- MVP 默认外部 LLM 逻辑配置仍可使用阿里云百炼 `aliyun-qwen-plus` / `qwen-plus`，但 Provider、`base_url` 和模型名由每个用户独立配置。
- 应用层统一限制通用请求最多输入 12,000 tokens、输出 4,096 tokens；知识点抽取单 Chunk 最多输入 6,000 tokens、输出 2,048 tokens；分层讲解最多输入 8,000 tokens、输出 4,096 tokens。
- 超限内容必须在本机分段、逐段调用并结构化归并，禁止静默截断或默认发送整份 PDF。
- 凡参与 Rubric 或判题的调用，必须记录配置模型名、Provider 返回模型标识、Prompt 版本和 schema 版本；版本链不足的结果不能成为 BKT 证据。
- 外部 LLM API 的授权、数据最小化、错误分类与审计服从 D10。

结构化输出协议已确认：

- 所有抽取、Rubric 和判题调用使用版本化 JSON Schema；Provider Adapter 只返回统一领域 DTO，不向业务层暴露供应商原始响应格式。
- 处理顺序固定为：限制响应大小 → JSON 解析 → schema 校验 → 领域不变量校验 → 引用完整性校验 → 持久化。
- 可修复结构错误最多执行一次受约束修复请求；修复后仍无效则当前逻辑调用失败，不把错误 JSON 当业务结果。
- 模型臆造来源、引用越界、知识点为空或严重事实冲突不得通过程序补默认值伪装修复。
- 所有调用记录 `provider`、规范化目标主机、配置模型名、实际模型标识、Prompt/schema 版本、token、修复状态和错误分类；普通日志不保存正文、完整 Prompt 或密钥。

Embedding 主路由已确认：

- 远程 Ollama 默认模型为 `qwen3-embedding:4b`，固定输出 2,048 维；部署清单锁定模型 digest，不只记录可漂移标签。
- 模型支持的长上下文不等于 Chunk 可以无限增长；Chunk 大小继续服从 D13 的检索与请求预算。
- 本机 Worker 只向远程网关发送当前批次必要 Chunk 文本，接收向量后写入本机 Milvus；远程服务器不持久化 Chunk 或向量。
- 索引版本至少由 `provider + target_host + model_identifier/model_digest + dimension + normalization + distance_metric + chunking_version` 派生。
- 不同模型、Provider、digest、维度、归一化方式或 Chunk 规则不得写入同一 active 索引版本。

Embedding 外部 API fallback 已确认：

1. `IngestionRun` 默认在隔离的暂存索引版本中调用远程 Ollama 完成全部 Chunk 的 Embedding。
2. 远程 Ollama 在 D04 重试后仍失败，且用户已配置并授权外部 Embedding API 时，Run 放弃并清理本次已生成的 Ollama 暂存向量。
3. Worker 使用外部 Embedding API 对该 Run 的全部 Chunk 从头重新向量化，不允许只补失败批次。
4. 外部 API 结果使用独立 `embedding_index_version`，即使输出维度同为 2,048，也不得与 Ollama 向量混写。
5. 全部向量数量、维度、归一化和引用校验通过后，D04 才允许原子发布该外部索引版本。
6. 外部 Embedding API 也失败、未配置或未授权时，当前 `IngestionRun` 整体失败并补偿，不发布解析、草稿或部分向量。
7. 后续从外部 API 切回 Ollama 时必须创建新索引版本并全量重建，不能原地切换 Provider。

D05 最终结论：业务与持久化数据留在用户本机；LLM 默认使用用户配置的外部 API，Embedding 默认使用受 HTTPS 网关保护的远程 Ollama。Embedding fallback 只能以整条 Run 全量重算和独立索引版本实现，任何 Provider 之间都禁止向量混写。

## 6. P1：端到端实现前必须冻结

### D06. 用户初始化与会话安全

状态：已冻结

访问拓扑已确认（按最终部署拓扑修订）：

- 知识库 Web 与 API 仅供本机浏览器访问，Docker 端口绑定 `127.0.0.1`，不得绑定 `0.0.0.0` 或直接暴露公网。
- FastAPI、Worker、MySQL、Milvus 和文件卷均运行在用户本机；MySQL、Milvus 和 Worker 只加入 Docker 内部网络，不发布宿主机端口。
- 本机访问不依赖公网域名或公网反向代理；如未来开放局域网或公网访问，必须作为新部署模式重新评审 TLS、可信代理和 Cookie 策略。
- 远程 GPU 服务器只提供受 HTTPS 网关保护的 Ollama Embedding API，不承载 Web 登录、业务 API 或数据库。

首个管理员初始化已确认：

- 用户表为空时，API 启动流程读取 `BOOTSTRAP_ADMIN_USERNAME` 与 `BOOTSTRAP_ADMIN_PASSWORD` 创建首个管理员。
- 优先支持 `BOOTSTRAP_ADMIN_PASSWORD_FILE`，由 Docker Secret 或只读挂载文件承载密码；直接密码环境变量仅作为兼容方案。
- 初始化只允许执行一次。用户表非空时必须忽略 Bootstrap 凭据，禁止在重启时覆盖管理员密码或创建重复账号。
- 禁止内置默认密码、空密码或弱密码；初始化失败时输出不含凭据的诊断信息，不允许退化为匿名可用状态。
- 首次创建成功后，部署者必须从 Compose 环境移除 Bootstrap 凭据。

账号与邀请注册已确认：

- 系统不开放公网自由注册。管理员在后台创建一次性邀请码，用户凭有效邀请码注册独立账号。
- 邀请码使用密码学安全随机值，数据库只保存哈希；默认 72 小时失效，只能成功消费一次，并支持管理员提前撤销。
- 邀请创建、撤销、消费和失败原因必须形成安全审计记录，但日志和列表接口不得返回完整邀请码。
- 管理员可以启用或禁用用户。禁用不删除用户数据；重新启用后仍保留原 `user_id`、资料、知识点、学习历史、掌握度和复习队列。
- 用户禁用后立即撤销其全部 Session，并禁止新建任务或发起新的本地/云端模型调用；已经运行的任务如何停止或补偿由 D04 和 D10 的规则共同决定。
- 管理员不能登录为其他用户、查看用户原密码或读取用户云端 API Key 明文。

密码与认证已确认：

- 密码长度限制为 12 至 128 个 Unicode 字符，允许密码管理器生成的长密码和空格；拒绝常见弱密码，不强制容易诱导固定模式的字符组合规则。
- 密码使用 Argon2id 保存，并在用户本机首次部署时校准成本；数据库只保存算法、参数、salt 和 hash，不做可逆加密。
- 登录、注册和重置密码使用统一的通用失败响应，避免通过错误文案枚举用户名、邀请码或账号状态。
- 修改密码和管理员重置密码成功后，撤销该用户全部旧 Session。

Session 已确认：

- Web 认证使用 MySQL 持久化的服务端 Session，不使用 JWT，也不在浏览器 `localStorage` 保存认证令牌。
- Cookie 只保存高熵随机 Session Token；数据库只保存 Token 哈希、`user_id`、创建时间、最后活动时间、绝对过期时间、撤销时间和必要的安全元数据。
- Cookie 固定启用 `HttpOnly` 和 `SameSite=Strict`，Path 为 `/`；本机开发使用 `http://localhost` 时不强制 `Secure`，一旦启用 HTTPS 或扩大访问范围必须启用 `Secure`。
- 默认 Session 的绝对有效期为 24 小时、空闲有效期为 8 小时；勾选“记住我”后绝对有效期为 30 天、空闲有效期为 7 天。
- `last_seen_at` 采用限频更新，避免每次 API 请求都写 MySQL；空闲超时判断不能依赖客户端时间。
- 登录成功后轮换 Session Token，防止 Session fixation；退出当前设备只撤销当前 Session，“退出所有设备”、修改密码和禁用账号撤销该用户全部 Session。
- 状态变更请求必须执行 CSRF 防护：同源校验配合 CSRF Token；不能仅依赖 `SameSite` Cookie。

密码重置已确认：

- MVP 不依赖邮件服务。管理员为指定用户生成一次性密码重置码，用户使用重置码设置新密码。
- 重置码使用安全随机值，只保存哈希，默认 30 分钟过期，只能成功消费一次，并允许管理员提前撤销。
- 重置密码只更新认证凭据，不创建新用户或改变 `user_id`；用户所有业务记录和 Provider 配置保持不变。
- 重置成功后撤销全部旧 Session，但不得删除资料、知识点、学习事件、掌握度、复习队列或云端 Provider 配置。

登录限流已确认：

- 同时按标准化账号标识和客户端 IP 维护失败窗口，避免单纯 IP 限流被共享网络误伤，也避免单纯账号锁定被攻击者用于拒绝服务。
- 同一账号/IP 组合在 15 分钟内连续失败后采用指数退避，延迟从 1 秒开始并封顶 60 秒；累计 10 次失败后冻结该组合 15 分钟。
- 对单 IP 的跨账号尝试设置额外速率上限；达到阈值时返回通用 `429`，不暴露账号是否存在。
- 成功登录清除对应账号/IP 组合的短期失败计数，但安全审计记录按保留策略保存。
- MVP 不强制验证码；限流状态必须存于 MySQL 或其他共享持久层，不能只保存在单个 API 进程内存中。

用户级云端 Provider 配置已确认：

- 每个用户独立配置自己的 `base_url`、模型名和 API Key；禁止默认借用管理员或其他用户的云端凭据。
- API Key 使用服务端主密钥进行认证加密后存储，主密钥不进入 MySQL；读取接口只返回掩码和配置状态，绝不返回可恢复的明文。
- Provider 连通性测试、模型调用和审计记录必须携带 `user_id`，后台任务只能解析任务所有者自己的凭据。
- 用户删除或替换 API Key 后，旧密文不得继续用于新调用；主密钥备份和轮换流程在 D12 冻结。
- 远程 Ollama Embedding 网关作为系统共享计算资源，但输入 Chunk、调用结果、任务、授权和审计必须按 `user_id` 隔离；网关凭据与用户 LLM/Embedding API 凭据分开管理。

D06 最终结论：知识库应用与全部持久化数据仅在用户本机运行并绑定 localhost；系统保留一次性环境初始化管理员、邀请码注册、服务端 Session 和用户级外部 Provider 凭据。远程 GPU 节点只提供受认证 HTTPS 网关保护的 Embedding 计算，不参与 Web 登录和业务持久化。

### D07. 资料版本与删除语义

状态：已冻结

资料生命周期已确认：

- `SourceDocument` 使用 `active`、`archived`、`trashed`、`purging`、`purged` 等明确状态，不用单一 `is_deleted` 混合不同语义。
- 用户操作分为归档、移入回收站和彻底删除三级；归档不进入删除倒计时，回收站默认保留 30 天。
- 回收站到期后由持久化 Job 触发彻底删除；用户可以在到期前恢复，也可以主动清空回收站。
- 所有状态变更都校验 `user_id`、记录操作者与时间，并使用幂等命令；重复归档、恢复或删除不得重复创建清理任务。

归档语义已确认：

- 归档资料从默认资料列表隐藏，禁止启动新的抓取、替换文件和重新处理。
- 归档资料从正式资料检索、RAG 候选和新的上下文组装中排除。
- 已确认知识点、`KnowledgePointSourceRef`、历史引用、学习事件、掌握度和复习任务继续有效。
- 用户查看已有知识点或历史评分引用时仍可打开归档资料的对应内容。
- 恢复归档后重新加入正式检索，原 active Revision 与索引仍通过完整性校验时不需要重新处理。

回收站语义已确认：

- 移入回收站后，资料、解析内容、Chunk 和向量立即退出普通查询、引用打开、检索和模型上下文，物理产物在保留期内暂不删除。
- 待执行的处理任务立即取消；运行中任务按 D04 进入 `cancel_requested` 和补偿流程。
- 每个关联知识点只停用来自该资料的 `KnowledgePointSourceRef`。仍有其他有效来源时，知识点继续正常使用。
- 若删除的是最后一个有效来源，知识点标记 `source_missing`，保留正式知识点、Rubric、`LearningEvent`、`MasteryState`、复习历史和历史评分，但暂停新的证据型判题。
- 30 天内恢复时重新启用原来源关联和原 active Run；仅当文件、派生产物或向量完整性校验失败时才创建新的 `IngestionRun`。
- 回收站保留期限按服务端 UTC 时间判断，具体用户时区展示规则由 D09 冻结。

彻底删除与补偿已确认：

- 彻底删除是异步 Saga：先将资料置为 `purging` 并保持业务不可见，再删除当前资料独占的原文、解析产物、暂存产物、Chunk 和 Milvus 向量。
- 内容寻址文件或产物仍被其他 Revision 引用时只减少引用计数，不得删除共享物理对象；引用计数归零并通过孤儿校验后才能清理。
- 正式知识点、Rubric、学习事件、掌握度、复习记录和历史评分不随资料级联删除。
- 清理完成后保留不可恢复的最小墓碑：稳定 ID、资料类型、脱敏标题、删除时间、操作者和删除原因。
- URL、原文件名、内容 hash、正文、解析文本和可恢复来源信息不保留；历史引用统一显示“来源已删除”，不得再用于新判题。
- 文件或 Milvus 清理失败时保持 `purging` 或进入 `purge_failed`，由清理 Job 重试；禁止在外部产物尚未核验清理时标记 `purged`。
- 彻底删除完成后不可通过产品功能恢复，只能依赖符合 D12 规则的整套备份恢复。

版本模型已确认：

- `SourceDocument` 是稳定逻辑资料；`SourceRevision` 是不可变业务版本；`ContentBlob` 是按内容 hash 去重的物理原文；`IngestionRun` 是版本化派生产物流水线。
- 用户每次明确执行 URL 重新抓取或 PDF 替换都创建新的 `SourceRevision`，即使新旧内容 hash 相同，也保留这次版本操作事实。
- Revision 至少记录创建原因、前序 Revision、抓取或上传时间、内容引用、响应元数据、创建者和版本状态，不覆盖旧 Revision。
- URL 抓取结果和 PDF 替换文件先生成候选 Revision；只有对应产物可复用或新 `IngestionRun` 完整成功后，才能原子切换 `active_revision_id`。
- 新 Revision 失败或取消时继续使用旧 active Revision，不允许解析、草稿、Chunk 和向量跨 Revision 混合。

相同内容与产物复用已确认：

- 新 Revision 与历史版本内容 hash 相同时复用同一个 `ContentBlob`，不复制物理原文。
- 当内容 hash、parser 配置、抽取 Prompt/模型配置、Chunk 配置和 Embedding 索引版本全部兼容，并且历史 Run 已通过完整性校验时，可以复用已验证的 `IngestionRun` 产物。
- 复用仍需创建新 Revision 到共享内容及已验证产物的正式关系，并执行引用计数和权限校验；不能绕过发布事务直接复制 active 指针。
- 任一配置不兼容、产物缺失、向量校验失败或归属不匹配时必须创建新 Run。
- 物理内容允许在同一用户范围内去重；不同用户之间默认不共享可推断存在性的内容记录，避免利用 hash 探测其他用户资料。

历史版本恢复已确认：

- 用户可以查看自己资料的 Revision 历史，并将一个仍具备完整已验证产物的历史 Revision 恢复为当前版本。
- 恢复不修改历史 Revision，也不覆盖后续版本；系统创建不可变的“恢复版本”操作记录，并在短 MySQL 事务中原子切换 active Revision/Run 指针。
- 历史产物已清理或索引版本不再兼容时，恢复请求先创建新的 `IngestionRun`；完整成功前当前版本继续服务。
- 恢复操作不自动改写已经确认的正式知识点。新旧来源差异只生成待用户确认的关联或更新建议。

Topic 删除语义已确认：

- Topic 是组织关系，不拥有资料、知识点或学习历史的生命周期。
- 删除 Topic 只删除 Topic 及其关联关系；仅属于该 Topic 的资料进入“未分类”，不会进入回收站。
- 同一资料还属于其他 Topic 时只移除被删除 Topic 的关联。
- Topic 删除不得级联删除 `SourceDocument`、`SourceRevision`、`KnowledgePoint`、学习事件、掌握度、复习任务、文件或向量。

D07 最终结论：资料采用不可变 Revision、内容寻址存储和三级生命周期。归档只退出正式检索，回收站立即停用来源但保留 30 天恢复能力，彻底删除异步清理可恢复内容并保留最小墓碑；任何删除和版本切换都不破坏正式知识点及学习历史。

### D08. SSE Session 生命周期

状态：已冻结

Session 与连接模型已确认：

- 每次流式讲解先创建持久化 `GenerationSession` 和对应 Job，再由客户端连接 SSE；模型生成不附着于某一条 HTTP 连接。
- `GenerationSession` 至少包含 `user_id`、业务对象与版本、Prompt/Provider 配置版本、状态、幂等键、创建时间、终态时间和最终结果引用。
- 状态使用明确状态机：`pending` → `running` → `completed | failed | cancel_requested | cancelled`，禁止依赖连接开关推断业务状态。
- SSE 只是 Session 的只读观察通道。浏览器断线、刷新或标签页关闭不会取消后台生成。
- 重连必须继续观察原 `session_id`，不得重新调用模型或生成第二份正式结果。

事件协议已确认：

- 每个 Session 的事件使用单调递增、不可复用的 `event_id`；事件同时携带 `session_id`、`event_type`、`created_at` 和版本化 payload。
- MVP 事件类型至少包含 `session_started`、`provider_selected`、`content_delta`、`fallback_started`、`result_snapshot`、`session_done`、`session_failed` 和 `session_cancelled`。
- 心跳每 15 秒发送一次 SSE comment 或独立 heartbeat，不占用业务 `event_id`，用于穿透反向代理空闲超时和检测断开。
- token 不逐个写入 MySQL。Worker 按最多约 200 毫秒或 512 字符聚合 `content_delta`，以先达到的条件为准；刷新批次仍必须保持文本顺序。
- payload 不允许携带 API Key、完整 Provider 请求头、内部异常堆栈或其他用户数据。

断线重连与重放已确认：

- 客户端使用标准 `Last-Event-ID` 请求续传；服务端只返回严格大于该 ID 的事件。
- 客户端按 `session_id + event_id` 去重，重复收到事件不得重复追加文本、触发完成动作或提交学习事件。
- 单次重放最多 1,000 个业务事件或 2 MiB，任一上限先达到即停止增量重放，改发当前 `result_snapshot` 和后续事件。
- 已完成 Session 的增量事件保留 24 小时；最终完整讲解作为正式结果长期保存，不依赖增量事件重建。
- 增量事件过期后，重连直接收到最终 `result_snapshot` 与终态事件；失败或取消的 Session 返回对应终态和受限诊断，不伪装成成功快照。
- 事件清理由持久化 Job 执行；清理增量事件不得删除最终结果、Session 元数据或学习历史。

多连接规则已确认：

- 同一用户对同一 Session 最多保持 3 条并发 SSE 观察连接，支持多个标签页或设备查看同一生成过程。
- 所有连接读取同一事件流，不能各自占有 Worker、租约或 Provider 请求。
- 第 4 条连接返回明确的连接上限错误，不自动踢掉现有连接。
- 连接建立时校验 Session 所有权和服务端登录 Session；每次心跳周期重新检查账号与认证状态。认证过期或用户被禁用时关闭 SSE，但后台生成是否继续按任务授权和 D10 撤销规则决定。

取消语义已确认：

- `pending` Session 可以立即转为 `cancelled`，并取消尚未领取的 Job。
- `running` Session 先转为 `cancel_requested`；Worker 在安全检查点中止本地或云端生成，释放租约后转为 `cancelled`。
- 已生成的部分文本仅保存为受限大小的诊断快照，界面明确标记“未完成”；不得作为正式讲解、费曼题目、Rubric 输入或 BKT 证据。
- Provider 在取消后返回的迟到增量或最终响应，必须依据 Session 状态、调用幂等键和 Worker 租约丢弃。
- 所有观察连接共享同一取消结果，并收到唯一的 `session_cancelled` 终态事件。
- 取消接口本身必须幂等；重复取消返回当前终态，不重复调用 Provider 取消或创建领域事件。

完成、失败与事务顺序已确认：

1. Worker 完成生成后先执行输出、引用和版本链校验。
2. 在一个短 MySQL 事务中保存最终完整结果、Provider/模型/Prompt 版本和 `completed` 状态。
3. 事务提交成功后，持久化唯一的 `session_done` 事件并通知当前 SSE 连接。
4. 客户端收到 `session_done` 时，最终结果查询必须已经可见；禁止先发送完成事件再异步落库。
5. 最终结果事务失败时不得发送 `session_done`，Job 按 D04 重试；幂等键保证重试不会产生多个正式结果。
6. 不可恢复错误先持久化脱敏错误分类和 `failed` 状态，再发送唯一 `session_failed`。网络断开不能把运行中的 Session误判为失败。

资源与安全边界已确认：

- 反向代理必须关闭 SSE 响应缓冲，并把读取超时设置为大于心跳间隔；SSE 响应使用 `Cache-Control: no-cache`。
- 每个用户的活动 Generation Session、观察连接和待执行生成任务都受配额限制，具体数值与生成最长时间在 D13 实测后冻结。
- SSE 接口只允许 `GET` 观察；创建、取消等状态变更使用普通受 CSRF 保护的命令 API，禁止通过 SSE URL 执行业务命令。
- Session 最终结果、事件和日志全部按 `user_id` 隔离；顺序 ID 不作为授权凭证。

D08 最终结论：生成任务与 SSE 连接解耦，断线后继续执行并支持基于 `Last-Event-ID` 的有限重放；最终结果先事务提交再发送终态事件。多连接只共享观察能力，取消、失败和重试不会生成重复结果或掌握度证据。

### D09. 时间与复习队列语义

状态：已冻结

产品边界已确认：

- 新用户的主路径是按文档来源学习：选择资料、学习已确认知识点、完成费曼复述和判题，再更新掌握度。
- 复习是用户主动进入的独立模块，不是自动弹窗、强制待办或后台自动开始的流程。
- 系统可以维护 `next_review_at`、逾期程度和推荐优先级，但这些字段只表达“适合复习的时间”，不表示用户必须完成。
- 用户未进入复习模块、未选择知识点或关闭页面，不生成失败事件，不降低 `p_mastery`，也不影响既有学习记录。
- 系统不设置“每日 20 题”或“单 Session 20 题”等固定业务限制，也不要求用户预先承诺每日学习时长。

时间存储与用户时区已确认：

- 数据库时间戳统一保存 UTC；调度计算、学习日归属和前端展示使用用户配置的 IANA 时区。
- 注册时读取浏览器 IANA 时区并让用户确认，默认建议 `Asia/Shanghai`；用户可在设置中修改。
- 修改时区不改写历史事件 UTC 时间戳，也不重算已经形成的学习事实；只影响后续本地日期归属、候选展示和新调度。
- 每次调度结果记录计算时使用的时区、调度规则版本、`calculated_at` 和 UTC `next_review_at`，确保结果可解释。
- 禁止使用服务器本地时区或固定 UTC offset 代替 IANA 时区；夏令时转换必须交由时区库处理。

学习日边界已确认：

- 用户本地时间凌晨 4:00 切换学习日；`00:00–03:59` 归入前一学习日。
- 学习统计、连续学习天数和“今日”筛选使用该边界，但复习到期时间本身仍是精确 UTC 时间点。
- 积分和 streak 只来源于真实完成事件，不能因为系统推荐或任务到期自动增加。

复习调度已确认：

- 首次通过标准费曼判题后，在同一业务事务中更新 `MasteryState` 并写入首次 `next_review_at = evidence_at + 12 hours`。
- `0.5 天` 固定解释为从有效证据时间起精确增加 12 小时，按 UTC 持续时间计算，不按本地自然日取整。
- 首次复习之后，采用既有 BKT 掌握度动态间隔公式：

\[
intervalDays = clamp\left(7 \times \frac{\log(p_{mastery} / 0.4)}{\log(2)}, 0.5, 60\right)
\]

- 调度公式使用事件完成后的 `effective_mastery`（此时也是新的证据锚点），并服从 D02 的截断边界；调度规则保存独立 `schedule_version`，不得把公式变化伪装成同一版本。
- 只有新的有效掌握证据才重写 `MasteryState.p_mastery` 锚点并重新计算 `next_review_at`；没有新证据时，`effective_mastery(now)` 仍按 `retention-decay-v1` 随时间下降，但不制造每日学习事件或反复改写到期时间。
- 每次重调度保留 `previous_due_at`、触发事件、公式输入、输出间隔和新 `next_review_at`，用于解释和审计。

主题复习流程已确认：

1. 用户主动进入复习模块并选择一个 Topic。
2. 系统查询该 Topic 下已到期、逾期、低有效掌握度以及补救后可重试的知识点，生成推荐候选，而不是自动创建正在进行的复习会话。
3. 系统按补救重试、逾期程度、低 `effective_mastery(now)`、近期失败和原始到期时间生成稳定排序；相同优先级使用稳定 ID 打破平局。
4. 用户可以增删候选知识点并自行选择本次数量；移出候选不算失败，不生成学习事件，也不修改 BKT。
5. 只有用户点击“开始复习”后才创建 `ReviewSession` 和不可变 `ReviewSessionItem` 快照。
6. Session 开始后不因后台状态变化自动增删题目；重复提交、跨标签页和网络重试按 D02/D11 幂等规则收敛。
7. 单次题量由用户决定，仅受 D13 的请求大小和资源安全上限约束；资源上限不是学习产品配额。

候选优先级已确认：

- `next_review_at <= now` 才属于到期候选；未来任务可以在主题详情中查看，但默认不进入推荐列表。
- 到期候选优先考虑补救重试和近期失败，再考虑逾期程度、低有效掌握度及来源重要性；不得仅按创建时间排序。
- 逾期只提高推荐优先级，不产生负向学习事件，也不触发弹窗强制复习；有效掌握度的自然下降来自统一时间衰减公式，而不是“逾期惩罚”。
- `source_missing`、来源在回收站或版本链无效的知识点不进入证据型复习候选；界面展示不可复习原因。

失败补救已确认：

- 复习失败后，在事务中写入失败证据、更新 BKT，并结束当前 `ReviewSessionItem`，禁止原题原记录反复覆盖提交。
- 系统立即提供补救讲解；补救只记录 exposure，不直接提升 `p_mastery`。
- 该知识点在 30 分钟后成为 `remedial_retry` 推荐候选；只有用户再次主动进入复习模块并选择它，才开始新的考核。
- 系统不得在 30 分钟到期时自动弹窗、自动打开 Session 或把未参与重试记为失败。

手动延期已确认：

- 用户可对推荐候选选择稍后复习：1 小时、1 天、3 天或自定义，单次最多 7 天。
- 延期保留原 `next_review_at`，单独记录 `defer_until`、操作者、原因和时间；不得通过改写原到期时间掩盖逾期事实。
- `now < defer_until` 时从默认推荐候选隐藏，但仍可在已延期列表中查看并提前恢复。
- 延期不修改 BKT，不生成成功或失败事件；到达 `defer_until` 后自动重新进入候选排序。

D09 最终结论：系统按 BKT 证据锚点和版本化时间衰减计算当前有效掌握度，并维护可解释的建议复习时间；复习完全由用户主动进入模块、选择 Topic 和确认题目后开始。固定 20 题限制被删除；逾期、延期和未选择不生成负向事件，只有真实完成的复述与考核形成新的掌握度锚点。

### D10. 隐私授权执行语义

状态：已冻结

外部模型节点定义已确认（按最终部署拓扑修订）：

- 外部调用包含两类：用户配置的 LLM/Embedding 官网或中转站 API，以及项目自托管在远程 GPU 服务器上的 Ollama Embedding HTTPS 网关。
- 两类节点都会接收从本机发送的资料片段，因此都属于本机信任边界之外的数据接收方；“自托管 Ollama”不等于内容留在用户本机。
- 远程 GPU 节点只执行 Embedding 计算，不保存知识库业务数据；用户 API 默认承担知识点抽取、Rubric、讲解、摘要和判题，实际路由服从 D05。
- 本项目不在远程节点部署 Web、FastAPI、Worker、MySQL 或 Milvus。

凭据与授权分离已确认：

- 保存 Provider 配置只代表服务可用，不自动授予发送资料内容的权限。
- 用户可以开启长期“允许外部模型处理”授权，并可在创建单次任务时覆盖为“仅本机确定性处理”或“本次允许外部模型”；若任务本身必须依赖 LLM/Embedding，禁止外部调用意味着该任务不可执行，而不是静默绕过授权。
- 授权优先级为：单次明确禁止 > 单次明确允许 > 用户长期设置；管理员和系统默认值不能越过用户的单次禁止。
- 发起外部请求前必须同时满足：任务所有者账号有效、Provider 配置有效、授权有效、任务类型允许外部执行、待发送数据通过最小化检查。
- 每次外部调用保存不可变 `consent_snapshot`，至少记录授权来源、授权版本、用户、任务、Provider 配置版本、检查时间和允许的数据类别，避免运行中设置变化导致语义漂移。
- API Key 的加密存储、用户隔离和明文不可回读继续服从 D06。

用户界面已确认：

- 实际外部调用以及远程 Ollama Embedding → 外部 Embedding API 的切换过程不在学习、抽取或复习前端展示，不弹出逐次确认窗口。
- 前端只展示统一的任务状态：处理中、完成或失败；不得暴露内部请求头、API Key、Provider 原始错误或中转站响应正文。
- 用户首次开启长期兜底时展示一次授权说明，明确资料片段、来源引用和用户答案可能发送到其配置的外部地址。
- 单次任务覆盖选项属于高级设置，不干扰默认学习流程。
- 调用过程不可见不等于不可审计；用户可以在个人隐私设置中查看自己的外部调用记录。

数据最小化已确认：

- 每次 API 调用只发送完成当前任务所需的文本片段、必要来源引用、用户答案和结构化输出约束，禁止默认发送整份资料或用户全部知识库。
- 后台在发送前生成内部结构化摘要：任务类型、资料/Topic ID、片段数量、字符数、数据类别、是否包含用户答案和来源引用。
- 结构化摘要用于策略校验和审计，不在普通学习前端展示，也不包含完整正文或完整 Prompt。
- 密码、Session Token、API Key、其他用户数据、内部路径、无关元数据和系统日志不得进入模型上下文。
- Provider Adapter 必须在最终 HTTPS 请求边界再次执行字段白名单，不能只依赖上游 Prompt 构造正确。

`base_url` 与中转站安全已确认：

- 用户可以配置模型官网或兼容 API 中转站，但必须使用 HTTPS；开发环境的显式本地配置例外不得进入生产默认值。
- 系统保存并展示规范化后的主机名，让用户知道数据实际发送到哪个域名；中转站不得伪装成官方 Provider。
- 连通性测试和正式调用都执行 SSRF 防护：解析后拒绝 loopback、私网、链路本地、云元数据和其他保留地址。
- 禁止自动跟随到不受信任域名或私网地址的重定向；DNS 解析结果必须在连接边界复核，降低 DNS rebinding 风险。
- 用户接受自定义中转站的数据处理风险；系统不把“接口兼容”解释为“与官网具有相同隐私保证”。

运行中设置变化已确认：

- 单次 HTTPS 请求按发起时的 `consent_snapshot` 执行；设置页面不提供对已发出请求的逐个交互式撤回。
- 用户关闭长期授权后，禁止尚未发起的外部 LLM、远程 Ollama Embedding、后续分页请求、修复请求和自动重试；已发出的单次请求可以结束并按原快照处理。
- 用户禁用账号或删除对应 Provider 配置后，同样禁止新请求和后续重试。
- 授权关闭不影响本机 PDF 解析、数据管理和历史学习记录；依赖外部 LLM 或 Embedding 的任务进入明确不可执行或失败状态。

Provider 失败链已确认：

- LLM 链路：用户配置的外部 LLM API 是唯一主 Provider；成功并通过结构化校验后采用结果，重试耗尽后任务失败，不自动切换未配置的第三方模型。
- Embedding 链路：远程 Ollama 是主 Provider；失败并耗尽本阶段重试后，只有在用户已配置并授权外部 Embedding API 时，才按 D05 对全部 Chunk 重新向量化并发布独立索引版本。
- 外部 Embedding API 也失败时，当前 `IngestionRun` 整体失败，不切换第三个 Provider，也不在两个 Provider 之间循环。
- 属于 `IngestionRun` 的任何失败都按 D04 保持整体不可见并在重试耗尽后补偿，不发布半成品。

错误分类已确认：

- `401/403`、API Key 无效、账户欠费或配额余额不足、模型无权限、模型不存在和不兼容请求属于不可重试错误，直接结束当前逻辑调用。
- 网络瞬断、连接超时、明确可恢复的 `429` 和 Provider `5xx` 属于可重试错误，服从 D04 的统一重试次数与退避；重试前必须重新确认授权仍有效。
- 用户未配置所需 API、配置校验失败或未授权时，依赖该 Provider 的任务直接失败，不静默使用管理员或其他用户凭据。
- 前端返回脱敏且可操作的错误类别，例如“模型服务不可用”“API 凭据无效”或“API 配额不足”，不返回 Provider 原始响应正文。

调用审计已确认：

- 每次实际外部调用记录用户、任务、`ingestion_run_id`/Session、Provider 配置 ID、规范化目标主机、模型、触发原因、授权快照、数据类别、片段数、字符数、耗时、状态、错误类别和可用的 token/费用元数据。
- 审计记录不保存完整资料正文、完整用户答案、API Key、完整 Prompt、请求头或 Provider 原始响应。
- 用户只能查看和删除自己的调用审计；管理员只能查看脱敏系统健康与聚合统计，不能读取用户内容。
- 调用审计默认保留 180 天，到期由持久化清理 Job 删除；用户可提前删除可见审计记录。
- 为防止删除动作本身不可追踪，可以保留不含 Provider、模型、目标主机和内容摘要的最小删除墓碑，仅记录记录 ID、用户、删除时间和删除动作。

D10 最终结论：用户配置的模型 API 与项目自托管的远程 Ollama 都属于本机之外的模型节点，凭据配置与内容授权严格分离。外部调用和 Embedding Provider 切换对学习前端透明，但每次发送都执行授权快照、数据最小化、目标验证和 180 天脱敏审计；主 Provider 与单层 fallback 都失败时任务明确失败，不继续扩散到其他 Provider。

## 7. P2：联调和发布前必须冻结

### D11. 统一 API 错误与幂等协议

状态：已冻结

错误分层已确认：

- 日志系统与前端使用不同信息层级，但通过同一个 `request_id` 关联。
- 内部日志记录错误分类、异常链、发生阶段、`request_id`、`user_id`、资源 ID、Job/Session ID 和脱敏 Provider 信息。
- 前端只接收稳定业务 `code`、安全提示、字段错误、是否可重试和 `request_id`；不得接收 SQL、堆栈、文件路径、内部主机、API Key 或 Provider 原始响应。
- 未知异常统一返回 `INTERNAL_ERROR`，用户提示不猜测根因，详细诊断只进入脱敏日志。
- 日志记录失败不能改变原业务响应；任何日志字段都必须先经过密钥、Token、正文和用户答案脱敏。

错误响应格式已确认：

- 错误响应采用 RFC 9457 Problem Details，媒体类型为 `application/problem+json`。
- 标准字段使用 `type`、`title`、`status`、`detail`、`instance`；扩展字段使用 `code`、`request_id`、`retryable` 和可选 `errors`。
- `code` 是前端判断逻辑的稳定标识，不得把中文文案、异常类名或 HTTP reason phrase 当作业务 code。
- `detail` 是安全的默认展示文案；前端可按 `code` 本地化，但未知 code 必须回退到 `detail`。
- 字段错误 `errors[]` 至少包含 `path`、`code` 和安全 `message`；数组与嵌套对象路径采用统一 JSON Pointer。
- 成功响应不包裹无意义的 `success/data/message` 外壳，直接返回资源、集合或操作资源；成功与失败不共用形状。

HTTP 状态映射已确认：

| 状态 | 语义 |
| --- | --- |
| `400` | 请求语法可解析但不符合通用协议前置条件 |
| `401` | 未登录、Session 过期或认证失败 |
| `403` | 已认证但无权访问；不能借此泄露其他用户资源存在性 |
| `404` | 资源不存在，或为防越权枚举而隐藏资源 |
| `409` | 业务状态冲突、幂等键内容冲突或重复活动 Attempt |
| `412` | `If-Match` 与当前资源版本不一致 |
| `422` | 字段、schema 或领域输入校验失败 |
| `428` | 修改资源但缺少要求的 `If-Match` |
| `429` | 登录、用户命令或 Provider 调用达到限流 |
| `503` | 必要依赖暂时不可用且请求当前无法受理 |

- `500` 只用于未分类服务端异常；已知业务失败不得一律包装成 `500`。
- `429` 和可恢复 `503` 应携带合理的 `Retry-After`；不可重试错误不得误导客户端自动重试。

Request ID 已确认：

- 入口反向代理或 API 为每次 HTTP 请求生成不可预测的 `request_id`；只接受符合格式且来自受信代理的上游 ID。
- `request_id` 写入响应头和 Problem Details，并传播到 Outbox、Job、Provider 调用和结构化日志。
- `request_id` 只用于链路关联，不作为幂等键、授权凭证或业务主键。

幂等键范围已确认：

- 以下关键命令强制要求 `Idempotency-Key`：上传/抓取、创建 `IngestionRun`、确认或拒绝草稿、创建或取消 Generation Session、提交费曼答案、完成 Review Attempt、dispute 与裁决、资料归档/删除/恢复/彻底删除、邀请码消费和密码重置。
- 普通 Topic 名称、资料展示元数据和非关键偏好编辑不强制幂等键，但必须使用资源版本控制。
- 幂等键由客户端为一次用户意图生成；网络自动重试复用原键，用户明确发起新操作生成新键。服务端不得为每次重试自动生成不同键。
- 幂等作用域固定为 `user_id + command_name + Idempotency-Key`；不能跨用户或跨命令复用结果。
- 服务端保存规范化请求摘要。相同作用域和键但摘要不同返回 `409 IDEMPOTENCY_KEY_REUSED`，不得执行新请求或返回不匹配的旧结果。

幂等执行状态已确认：

1. 首次请求在执行业务逻辑前原子创建 `in_progress` 幂等记录并锁定请求摘要。
2. 重复请求遇到 `in_progress` 时返回同一个 `operation_id`、Attempt 或资源引用，不启动第二次执行。
3. 首次请求完成后保存终态、HTTP 状态码和受限响应快照；重复请求返回第一次已提交结果，并通过响应头标识 replay。
4. 首次事务失败且没有提交业务事实时，幂等记录可以安全标记为失败并允许按同一键重试；是否可重试由稳定错误分类决定。
5. 幂等响应记录默认保留 7 天；之后删除 HTTP 快照，但领域唯一约束、`attempt_id`、`run_id`、邀请码哈希和资源状态继续长期防重。
6. 不缓存 Session Cookie、API Key 明文、资料正文、下载内容、临时签名 URL 或其他不能安全重放的响应。

乐观并发已确认：

- 可编辑聚合使用单调整数 `version`；读取单资源时通过强 `ETag` 暴露当前版本。
- PATCH、PUT、DELETE 以及基于当前状态的命令必须携带 `If-Match`；缺失返回 `428 PRECONDITION_REQUIRED`。
- 版本不一致返回 `412 VERSION_CONFLICT`，并返回当前 ETag；服务端不自动覆盖，也不自动合并知识点语义字段。
- 用户刷新后查看当前状态或差异，再基于新 ETag 重新提交。
- `Idempotency-Key` 防止同一意图重复执行；`If-Match` 防止旧快照覆盖新状态。关键状态命令可以同时要求两者。
- Worker 内部并发继续使用 D02/D04 的行锁、租约、sequence 和唯一约束，HTTP ETag 不能替代数据库并发控制。

异步操作响应已确认：

- 上传处理、抽取、Rubric 生成、SSE 生成准备等不能在请求内完成的命令统一返回 `202 Accepted`。
- 响应包含 `operation_id`、`status_url` 和可选 `resource_url`；`Location` 指向操作资源，可附 `Retry-After` 建议轮询间隔。
- 操作资源统一状态为 `pending`、`running`、`succeeded`、`failed`、`cancel_requested`、`cancelled`，并关联 D04 Job 或 D08 Session。
- 重复幂等请求返回同一个操作资源；不得因为第一次仍在运行而创建第二个 Job。
- `202` 只表示请求已可靠受理，不表示业务成功。最终失败必须在操作资源中使用 Problem Details 兼容的安全错误快照。
- 已同步完成且创建资源的命令使用 `201 Created + Location`；同步状态变更按语义使用 `200` 或 `204`，不得为了格式统一把所有命令伪装成异步。

错误码治理已确认：

- 错误码按领域前缀管理，例如 `AUTH_*`、`SOURCE_*`、`INGESTION_*`、`KNOWLEDGE_*`、`ASSESSMENT_*`、`REVIEW_*`、`PROVIDER_*`、`VERSION_*` 和 `RATE_LIMITED`。
- 已发布 code 的语义不得原地改变或复用于其他错误；废弃时保留兼容映射。
- Provider、MySQL、Milvus、文件系统和解析库的异常必须在 Adapter 边界映射为领域错误，禁止第三方异常类型泄漏到 API。
- 日志可以记录更细的内部 `diagnostic_code`，但前端业务 code 应保持有限、稳定且可操作。

D11 最终结论：API 使用 Problem Details 承载稳定业务错误，内部日志与前端提示通过 `request_id` 关联但严格分层。关键命令同时依赖幂等记录、领域唯一约束和必要的 `If-Match`，异步操作统一返回可查询的 `202 Operation`，从协议层防止重复提交和并发覆盖。

### D12. 备份、恢复与升级

状态：已延期（当前阶段不建设后期维护能力）

当前范围已确认：

- 知识库 Web、MySQL、Milvus、文件卷、配置和学习记录全部保存在用户本机；远程 GPU 服务器只运行 Ollama Embedding，不保存知识库业务数据。
- MVP 当前不实现定时备份、异地备份、对象存储备份、自动升级或迁移失败自动回滚。
- 不创建强制的升级前临时快照；用户接受本机磁盘损坏、Docker 卷误删、数据库损坏或错误迁移时可能无法恢复数据。
- 产品界面、README 和发布说明不得宣称当前版本具备灾难恢复或无损升级能力。

当前仅保留的工程边界：

- MySQL 仍是业务事实源；原始文件与解析产物保存在本机挂载卷；Milvus 向量是可重建派生产物。
- 远程 Ollama 的模型文件和运行配置不纳入业务备份，节点丢失后通过锁定模型标识重新部署。
- 开发期允许删除卷并从空数据库重新初始化，但该方式不是用户数据恢复方案。
- Alembic 仍用于从零创建当前 schema 和开发期迁移管理；在 D12 重新冻结前，不承诺跨已发布版本的原地升级兼容性。
- 不允许因为没有备份方案就把 API Key 明文、原始资料或数据库复制到日志、代码仓库和远程 GPU 节点。

重新打开 D12 的触发条件：

- 首次正式发布且需要保留真实用户数据升级之前；
- 用户要求迁移电脑、重装系统、跨版本升级或灾难恢复时；
- 应用开始承载不可接受丢失的长期学习记录之前。

重新讨论时必须补齐：MySQL 一致性导出、原始文件和配置清单、主密钥保护、恢复顺序、Milvus 重建、Alembic expand/contract 策略、失败回滚和至少一次干净环境恢复演练。

D12 当前结论：备份、恢复与无损升级明确延期，不伪装成已实现能力。当前环境适用于 MVP 开发与验证；在真实数据需要跨版本保留前，D12 必须重新评审并冻结。

### D13. MVP 性能预算

状态：已冻结

运行基线已确认：

- 用户本机为 Windows + Docker Desktop / WSL2，16 GB 物理内存，为知识库容器、卷和业务数据预留约 20 GB 磁盘。
- 本机不运行 LLM 或 Embedding 模型；LLM 使用用户配置的外部 API，Embedding 默认使用远程 24 GB GPU 服务器上的 Ollama HTTPS 网关。
- 性能预算优先保证 Windows 桌面可用性、数据完整性和交互请求，不追求多文件并行吞吐。
- 所有限制必须在任务创建或阶段开始前验证并返回 D11 稳定错误码，不允许通过 OOM、磁盘写满或静默截断体现限制。

单资料输入上限已确认：

- 单个 PDF 最大 20 MB、200 页、解析后 1,000,000 个有效字符，任一上限先达到即拒绝进入正式 `IngestionRun`。
- 限制检查包含上传流字节数、PDF 页数和解析后的规范化字符数；压缩比异常、页对象异常或解析资源异常可以提前拒绝。
- 超限资料返回明确、不可重试的 `SOURCE_LIMIT_EXCEEDED`，展示实际值与允许上限；不得只处理前 N 页后伪装成完整成功。
- 粘贴文本和静态网页正文同样限制为 1,000,000 个有效字符；网页下载字节上限与 PDF 保持同等级，并服从 SSRF 和解压资源限制。
- MVP 面向文本型 PDF，不承诺扫描件 OCR、复杂表格恢复或多模态页面理解。

Chunk 与模型请求预算已确认：

- 单个 `IngestionRun` 最多生成 1,500 个可检索 Chunk；超过时停止发布并返回可诊断错误，不能随机丢弃 Chunk。
- Chunk 规则由版本化配置管理，必须保留页码/字符区间和来源引用；实际 Chunk 字符/token 范围在实现时通过检索质量样例校准，但不能突破 D05 的模型输入上限。
- 远程 Embedding 每批最多 32 个 Chunk，同一 Run 最多 2 个在途请求；响应体和向量维度在写入 Milvus 前校验。
- 外部 LLM 抽取调用按 D05 最多输入 6,000 tokens、输出 2,048 tokens；归并步骤必须分层执行，禁止把全部 Chunk 一次塞入模型。
- 每个用户和 Provider 的并发还必须服从外部 API 限流；收到 `429` 时按 D04 退避，不能通过无限并发绕过供应商限制。

本机并发与优先级已确认：

- 同时只允许 1 个 `IngestionRun` 进入解析、抽取、Chunk、Embedding、验证或发布计算阶段；其余 Run 持久化排队。
- 文件上传校验、状态查询和轻量只读请求可以并行，但不得绕过单 Run 计算信号量。
- 交互式讲解、判题和用户命令优先于后台抽取；后台任务等待不会篡改其创建时间、幂等键或原始优先级审计。
- API 与 Worker 使用独立且有上限的数据库连接池；初始建议 API 10、Worker 5，总连接数必须低于 MySQL 配置并为迁移/健康检查保留余量。
- 不使用按请求无限创建线程、进程或 Provider 客户端的实现；PDF 解析子进程/任务必须有内存和超时边界。

交互式延迟与超时已确认：

- SSE 讲解目标为请求被 Provider 接受后 3 秒内产生首个有效内容增量；这是服务目标，不是对外部 Provider 的绝对保证。
- 15 秒没有首段响应时判定本次 Provider 尝试超时；开始输出后使用 30 秒流空闲超时。
- 单次完整讲解最长 5 分钟；结构化判题单次最长 2 分钟。超时后按 D04/D10 分类为可重试或最终失败。
- SSE 心跳继续使用 D08 的 15 秒间隔；心跳不能被误认为模型内容或首段成功。
- 性能验收分别记录排队时间、Provider 首字节时间、首个有效增量时间和总耗时，不能只记录一个模糊 duration。

IngestionRun 时间预算已确认：

- 单个合规资料从 `running` 到进入成功、失败或补偿终态的业务执行预算为 60 分钟。
- 每个阶段必须持续更新检查点和进度心跳；无进度不能仅靠延长总超时掩盖 Worker 卡死。
- 60 分钟超时后 Run 转为失败/补偿流程，不发布暂存产物；补偿和清理可以在业务超时后继续，不能被强杀而留下可见半成品。
- 队列等待时间不计入 60 分钟执行预算，但必须向用户展示 `queued_at`、`started_at` 和当前排队状态。

内存预算已确认：

- WSL2/Docker 总内存目标上限为约 10 GB，至少为 Windows 和浏览器保留约 6 GB；允许配置约 2 GB swap 作为短时缓冲，但不能依赖持续换页维持正常运行。
- 初始预算建议：Milvus 及其依赖约 3.5 GB、MySQL 1.5 GB、API/Worker 与解析峰值 3 GB、前端/网关/系统辅助容器 1 GB、预留约 1 GB。
- Worker 在开始解析大文件前检查可用内存和磁盘；资源不足时保持任务排队或返回 `RESOURCE_BUDGET_EXCEEDED`，不得启动后等待 OOM Killer。
- 解析临时文件和模型响应必须流式或有大小上限，禁止把原 PDF、全部解析文本、全部 Chunk 和全部向量同时复制多份到内存。

20 GB 磁盘预算已确认：

| 类别 | 预算 |
| --- | ---: |
| 原始资料与解析/导出产物 | 8 GB |
| MySQL 数据 | 3 GB |
| Milvus、etcd/minio 与索引 | 4 GB |
| 上传、Run 暂存和补偿临时区 | 2 GB |
| 文件系统与增长安全余量 | 3 GB |

- 总预算使用率达到 80% 时在系统设置和导入页显示警告；达到 90% 时禁止新资料导入和重建索引，但允许读取、导出、删除与清理。
- 暂存区不足时新 Run 不得启动；正在补偿的清理任务不受“禁止新导入”限制。
- 内容寻址去重只在同一用户边界内生效；不能为节省空间破坏 D07 的跨用户隐私约束。
- 远程 GPU 服务器磁盘只保存 Ollama 模型与运行依赖，不计入本机 20 GB 业务预算，也不得作为资料备份位置。

远程 Embedding 网关预算已确认：

- 远程节点使用 `qwen3-embedding:4b` 锁定 digest；默认仅允许当前应用最多 2 个并发 Embedding 请求。
- 网关请求体上限必须覆盖 32 Chunk 批次但低于无界上传；实际字节阈值在集成测试中按 Chunk 上限计算并固定配置。
- 网关超时、限流和 OOM 必须返回可分类状态；不得在网关内部静默切换模型或改变输出维度。
- 远程节点不持久化输入正文、向量结果或业务日志，模型缓存和镜像可以重新部署。

验收基线已确认：

- 使用接近 20 MB、200 页但不超过 100 万有效字符的文本 PDF 完成一次干净端到端处理，峰值不突破本机预算且无半成品可见。
- 并发提交至少 3 个文件时只能 1 个 Run 计算，其余稳定排队，交互式讲解仍可发起。
- 模拟远程 Ollama 中断时，已授权的外部 Embedding fallback 必须清理 Ollama 暂存向量、全量重算并发布独立索引版本。
- 模拟磁盘达到 90% 时拒绝新导入但允许删除与补偿；释放空间后无需修改数据库即可恢复受理。
- 所有性能测试记录机器配置、Docker/WSL2 限额、Provider、模型标识、样本大小和各阶段耗时，避免脱离环境比较数字。

D13 最终结论：MVP 以 16 GB Windows 本机和 20 GB 业务磁盘为硬预算，采用单 IngestionRun、有限批量和外部模型计算。系统在接受任务前主动执行页数、字符、Chunk、内存和磁盘保护，并以原子失败替代截断、混写或资源耗尽。

### D14. 父子文档与混合检索协议

状态：已冻结

MVP 范围已确认：

- MVP 实现父子切片、Dense 向量索引、BM25/稀疏索引、Topic 内混合召回、RRF 融合和 Top 3 父块返回。
- MVP 提供内部检索服务/API及固定测试集，用于验证 Top 3 结果和来源；完整 Chat/RAG 对话界面、查询改写、回答生成和多轮记忆继续后置。
- 检索索引属于 D04 `IngestionRun` 的发布内容；父块、子块、Dense 和 Sparse 索引必须全部校验成功后一起可见。

文档身份已确认：

- 每份逻辑 PDF 使用稳定 `source_document_id`；替换文件、重新上传或重新抓取不会改变该逻辑 ID。
- 每个不可变内容版本使用 `source_revision_id`；每次解析、父子切片和索引构建使用 `ingestion_run_id`。
- 检索只能命中当前 `SourceRevision.active_ingestion_run_id` 所发布的块，禁止旧 Revision、暂存 Run、失败 Run 和补偿中产物参与召回。
- 每个 ID 都必须同时受 `user_id` 归属约束；顺序 ID、Milvus 主键和内容 hash 不能替代授权检查。

父子切片模型已确认：

```text
SourceDocument
└── SourceRevision
    └── IngestionRun
        └── ParentChunk[]
            └── ChildChunk[]
```

- `ParentChunk` 负责保持可读上下文，以 Markdown 标题层级、语义段落、页码连续性和结构块完整性编排，目标约 1,200-2,500 tokens。
- `ChildChunk` 负责精确召回，由父块继续切分，目标约 300-600 tokens，overlap 约 60-100 tokens；禁止跨父块 overlap。
- 表格、公式、代码块和列表优先保持完整；结构块超过上限时允许分片，但必须保存共同结构标识和连续序号。
- 每个 Child 只能属于一个 Parent；Parent 不允许跨 `source_revision_id` 或 `ingestion_run_id` 聚合。
- D13 的单 Run 最多 1,500 个 Chunk 具体指可索引的 `ChildChunk`；Parent 数量单独统计，但不得通过制造大量空 Parent 绕过资源预算。

确定性标识已确认：

- `parent_chunk_id` 由 `ingestion_run_id + parent_ordinal + chunking_version` 确定性生成。
- `child_chunk_id` 由 `parent_chunk_id + child_ordinal + chunking_version` 确定性生成。
- 相同 Run 和切片版本重试必须得到相同 ID，支持 MySQL/Milvus 幂等 upsert；切片规则变化必须创建新的 `chunking_version` 和索引版本。
- ID 稳定性不表示正文可修改；父子块在已发布 Run 内不可变，修改解析或切片结果必须创建新 Run。

最小 metadata 已确认：

- Parent 至少记录：`user_id`、`topic_id`、`source_document_id`、`source_revision_id`、`ingestion_run_id`、`parent_chunk_id`、`parent_ordinal`、`heading_path`、`page_start`、`page_end`、字符区间、token 数和正文引用。
- Child 至少记录：全部父级身份、`child_chunk_id`、`child_ordinal`、父内字符区间、页码区间、token 数、Dense/Sparse 索引版本和正文引用。
- Milvus metadata 必须携带过滤和回源所需 ID，但父块完整正文保存在本机文件/业务存储中，不把 Milvus 当作正文事实源。

混合召回已确认：

1. 查询必须先限定 `user_id + selected_topic_id + active_ingestion_run_id + source_state=active`，再执行召回；不能召回后才做权限过滤。
2. 使用与 active Dense 索引一致的 Embedding Provider/模型生成 query vector。
3. Dense 通道召回 Child Top 20；BM25/稀疏通道召回 Child Top 20。
4. 两个通道返回独立排名，不直接比较向量相似度与 BM25 原始分数。
5. 使用 `retrieval-v1` 的 RRF 融合 Child 排名：

\[
rrfScore(child) = \sum_{ranking \in \{dense,bm25\}} \frac{1}{60 + rank_{ranking}(child)}
\]

- RRF 常数 `k = 60`；未出现在某通道 Top 20 的 Child 在该通道贡献 0。
- 相同 RRF 分数按最佳单通道排名、`parent_chunk_id`、`child_chunk_id` 依次稳定打破平局，确保重复查询可复现。
- Dense/BM25 候选数、RRF k、过滤规则和 tie-break 共同组成 `retrieval_version`，参数变化不得沿用 `retrieval-v1` 名称。

父块编排与 Top 3 已确认：

- RRF 先对子块融合，再按 `parent_chunk_id` 折叠；父块分数取该父块最高 RRF 子块分数，不累加所有子块，避免切得更碎的父块天然占优。
- 每个父块最多附带 RRF 最高的 2 个命中子块作为命中证据；第二个子块用于解释，不额外提高父块排名。
- 最终返回分数最高的 3 个不同 `ParentChunk`。Topic 只有不足 3 个合格父块时按实际数量返回，不用低质量内容凑满。
- 不强制 Top 3 来自不同 PDF；同一 PDF 可以返回多个不同父块，但重复或高度重叠父块必须依据父级区间去重。
- 父块正文过长时不得静默截断来源引用；上下文组装可以在父块内部围绕命中子块裁剪，但必须返回原父块 ID、原页码范围和裁剪区间。

检索响应已确认：

- 每个结果返回 `rank`、`rrf_score`、父块正文或裁剪正文、PDF 标题、`source_document_id`、`source_revision_id`、`parent_chunk_id`、页码范围和命中子块列表。
- 每个命中子块返回 `child_chunk_id`、Dense 排名、BM25 排名、RRF 分数、命中文本和页码/字符区间。
- 响应记录 `retrieval_version`、Dense/Sparse 索引版本、查询范围 Topic 和 active Run 集合，便于诊断与离线评估。
- 检索接口只返回用户有权访问的资料；归档资料默认排除，回收站、已删除、`source_missing` 或未发布 Run 必须排除。

质量验收已确认：

- 构造覆盖单 PDF、多 PDF、同义表达、精确术语和重复段落的固定查询集，保存期望来源与可接受父块范围。
- 验证 Dense 独有命中、BM25 独有命中和双路共同命中都能通过 RRF 正确排序。
- 验证同一父块多个子块命中不会重复占据 Top 3，也不会通过子块数量刷高父块分数。
- 验证 Topic、用户、active Run、归档与删除过滤在召回前生效，不发生跨用户或旧版本泄漏。
- 检索质量报告至少记录 Top 3 来源命中率、MRR、无结果率和每阶段耗时；完整回答正确率留到 Chat/RAG 阶段评估。

D14 最终结论：每份 PDF 由稳定 `SourceDocument` 标识，并在不可变 Revision/Run 内生成父子切片。Child 同时参与 Dense 与 BM25 Top 20 召回，经 `k=60` 的 RRF 融合后按 Parent 折叠，最终在用户选择的 Topic 内返回 3 个不同父块及其命中子块和来源信息。

## 8. 单项决策记录模板

每项讨论完成后按以下格式记录：

```text
决策编号：Dxx
状态：已冻结
结论：
理由：
拒绝的备选方案：
数据模型影响：
API / 事件影响：
迁移与兼容约束：
验收条件：
冻结日期：
```