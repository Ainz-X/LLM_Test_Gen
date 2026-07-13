# A3 Java Test Agent 项目复盘

更新日期：2026-07-13

## 1. 项目目标与当前形态

项目从一个“按照固定流程生成 JUnit 测试”的工作流，逐步演进为可以由人通过对话驱动的 Java 测试 Agent。

当前系统由 React 前端、FastAPI 后端、MySQL、Redis、MinIO、Milvus、Celery worker 和 Java/Maven 工具链组成。用户可以上传单个 Java 文件或项目 ZIP，查看源码与测试源码，询问代码问题，生成/预览/下载测试，执行 JaCoCo 覆盖率，并查看历史对话和任务进度。

核心设计不是让模型直接操作文件系统，而是让模型调用一组受后端控制的工具，例如：

- 文件与项目：`list_files`、`analyze_file`、`read_code_context`
- 测试产物：`generate_tests`、`batch_generate_tests`、`list_artifacts`、`read_artifact`
- 质量闭环：`compile_artifact`、`run_coverage`、`diagnose_artifact`、`repair_artifact`、`repair_low_coverage`
- 运行状态：`list_tool_history`、`read_memories`、`remember`

## 2. Celery 是什么

Celery 是 Python 的异步任务队列框架。它把耗时工作从 FastAPI 的 Web 请求进程移到独立 worker 中执行。

没有 Celery 时，浏览器请求会一直占着 FastAPI worker：用户上传项目后点击“提取上下文”，请求可能要等待 Maven 编译、SootUp 分析和 CSV 入库几分钟。期间浏览器断开、后端重启或用户切换对话，都很难正确处理。

使用 Celery 后，链路变为：

```text
前端点击“提取上下文”
  -> FastAPI 在 MySQL 创建 AgentJob(status=queued)
  -> extract_code_context_task.delay(job.id) 写入 Redis 队列
  -> Celery worker 取走任务并执行 Java/Maven/SootUp/JavaParser
  -> worker 持续更新 MySQL 中 AgentJob 的 progress/stage/message
  -> 前端通过 /jobs/{job_id}/stream 的 SSE 每秒读取进度
  -> 前端显示成功、失败或取消结果
```

### 2.1 当前项目是否真的在用 Celery

是，已经在运行，不只是预留依赖。

| 项目 | 当前实现 |
|---|---|
| Celery 实例 | `Agent_App/backend/app/celery_worker.py` |
| 消息 broker / 结果后端 | Redis，默认 `redis://redis:6379/0` |
| Docker worker | `Agent_App/docker-compose.yml` 的 `worker` 服务 |
| 已注册任务 | `extract_code_context` |
| 任务函数 | `Agent_App/backend/app/tasks/context_extraction.py` |
| 提交入口 | `POST /api/files/context/extract` |
| 进度和取消 | `AgentJob` 表、`/jobs/{job_id}/stream`、`/jobs/{job_id}/cancel` |

Celery 当前配置具有几个重要特征：

- `task_track_started=True`：任务进入执行态时可见。
- `worker_prefetch_multiplier=1`：一个 worker 不会提前抢很多任务，进度更公平、取消更可控。
- `task_acks_late=True`：任务完成后才确认，worker 意外退出时任务可能被重新投递。
- `result_expires=3600`：Redis 中的 Celery 结果保留一小时；真正给 UI 查询的长期状态保存在 MySQL 的 `AgentJob`。

### 2.2 Celery 现在没有承担什么

目前它主要负责“代码上下文提取”，而非所有耗时操作。

- 普通聊天和 LangGraph 工具循环仍通过 FastAPI 的 SSE 请求执行。
- 单文件覆盖率与修复目前可在聊天工具调用中同步执行。
- “批量生成未测文件”“全项目覆盖率”等大任务尚应继续迁移到 Celery，才能保证并发、取消、重试和真实进度。

因此，Celery 不是 Agent 本身，也不是 LangGraph 的替代品。它是执行长任务的后台劳动力。

## 3. 当前各组件的职责

| 组件 | 解决的问题 | 不应承担的问题 |
|---|---|---|
| FastAPI | 登录、HTTP API、SSE、鉴权、数据库事务、任务提交 | 长时间阻塞式 Maven/SootUp 执行 |
| React | 对话、文件树、进度弹窗、预览、下载、历史会话 | 业务权限和 Java 编译判断 |
| MySQL | 用户、会话、消息、文件元数据、产物、反馈、`AgentJob`、记忆 | 大 ZIP 或源码正文的唯一存储 |
| MinIO | ZIP、上传文件、生成测试、压缩下载等对象存储 | 查询会话关系、任务状态 |
| Redis | Celery broker、短期任务状态 | 业务事实的唯一来源 |
| Celery | 可排队、可取消、可恢复观察的后台任务 | 用户意图理解或聊天编排 |
| LangGraph | 对话式 Agent 的模型 -> 工具 -> 模型循环、工具决策、流式步骤 | Maven/Java 的进程隔离和任务队列 |
| LangChain | 模型和工具适配层，例如 DeepSeek 的 OpenAI 兼容模型 | 可靠的后台长任务调度 |
| JavaParser/SootUp | AST、FQN、方法签名、字段、Jimple 等代码上下文 | 自然语言决策 |
| Maven/JUnit/JaCoCo | 编译、运行测试、生成真实覆盖率数据 | 直接面向浏览器的交互 |

## 4. 主要困难、根因与处理方式

### 4.1 固定 Workflow 与真正 Agent 的边界

**困难**：原流程适合“上传 -> 分析 -> 生成测试”，但无法自然回答“当前 FQN 是什么”“为什么上次编译失败”“Jimple Code 是什么”“覆盖率低，修复一下”。

**根因**：Workflow 预先决定步骤；这些问题需要根据上下文选择不同工具，且可能有多轮观察与决策。

**处理**：把已有能力拆成明确工具，保留确定性 Java 逻辑，再将对话层接入 LangGraph ReAct 循环。

**结论**：不是所有事情都应 Agent 化。稳定的一次性测试生成仍可直接调用模型；需要“先看结果再决定下一步”的对话才交给 Agent。

### 4.2 意图路由误判导致权限链断裂

**现象**：用户说“立即修复当前文件的低覆盖率”，路由器可能判断为 `chat`；`general_chat` 只允许 `list_skills` 和 `read_memories`，导致 Agent 明明理解需求却不能调用覆盖率和修复工具。

**根因**：把“意图分类结果”同时当成“工具权限系统”。无论关键词规则还是单独 LLM 分类器，都不可能保证 100% 正确。

**处理**：引入 LangGraph 的标准 ReAct 图：模型节点 -> `ToolNode` -> 模型节点。所有工具对 Agent 可见，技能（skills）只作为工作流知识和审计标签；去重、次数上限、用户归属和文件归属校验下沉到具体工具执行层。

**验证标准**：即使调用方带有旧 `general_chat` 标签，`run_coverage` 也不会因为技能白名单被拒绝。

### 4.3 工具循环、重复读取与 token 浪费

**现象**：Agent 反复读取同一 Java 文件，或出现“每轮工具调用次数限制”后任务被截断。

**根因**：模型只看到局部信息；逐文件循环会把一个本应批处理的问题拆成很多回合；错误的路由还会让模型反复尝试不可用工具。

**处理**：

- 后端使用 `seen_calls` 和调用计数阻止同参重复工具调用。
- 对“所有未测文件”提供 `batch_generate_tests`，一次工具调用调度批量工作，而不是模型逐个文件循环。
- 对低覆盖率提供 `repair_low_coverage` 高层工具，封装“基线 -> 诊断 -> 修复 -> 验证”。
- LangGraph 设置递归上限，防止模型工具循环失控。

**遗留工作**：批量生成和全项目覆盖率仍应完全转为后台任务，而不是仅依赖对话请求内的上限。

### 4.4 Java 依赖、项目上传与编译环境

**现象**：单独上传一个 Java 文件时，文件包含大量 `import`，但工作区没有完整依赖，生成的测试无法编译；上传数百个文件也不现实。

**根因**：Java 的编译单位不是孤立 `.java` 文件，而是源目录、POM/Gradle 配置、第三方依赖、资源文件与测试目录的组合。

**处理**：支持上传项目 ZIP，后端解压到项目工作区，识别 Maven 项目并在项目副本中运行编译/测试。前端仍按 Java 文件树展示，让用户可选择单文件、部分文件或整个项目操作。

**原则**：用户上传的是项目快照；运行时永远复制到临时目录，不能直接修改原始上传项目。

### 4.5 Jimple 与静态上下文提取

**现象**：仅靠旧 CSV 或简单正则无法可靠回答“当前代码的 Jimple 是什么”。Jimple 依赖类路径、编译产物和 SootUp 分析。

**根因**：JavaParser 能从源代码得到 AST、方法源码和字段，但无法替代字节码级 Jimple；SootUp 又需要正确的编译和类路径。

**处理**：项目级代码上下文提取接入 Celery。任务按项目分组，编译/定位 class 文件，调用 SootUp/JavaParser，写入 `CodeContext`；若提取器没有匹配结果，则保存轻量级静态上下文并明确标注来源。

**遗留工作**：前端必须区分“尚未提取”“静态上下文”“SootUp 成功提取”“提取失败”，避免把无 Jimple 误表述为模型不知道。

### 4.6 JaCoCo 覆盖率难以稳定运行

**现象**：出现 Java 6 source/target 不被新 JDK 支持、Maven 超时、目标类未识别、已有测试干扰、只展示“行覆盖率未识别”等问题。

**根因**：覆盖率不是单一命令：需要正确项目根目录、依赖解析、测试源码路径、JaCoCo agent、报告 XML、目标 FQN 与 class 文件对应关系。

**处理**：

- 对旧 Maven 项目，在临时运行副本中兼容性修正 Java source/target 至至少 Java 8。
- 分阶段反馈 Maven compile/test/report 的状态和超时原因。
- 忽略不应参与本次运行的旧测试目录，避免项目自带测试干扰生成测试。
- 解析 JaCoCo XML，保存 instruction、branch、complexity、line、method、class 等指标，而非只保留行覆盖率。
- 覆盖率修复必须二次验证；候选测试未提升时保留供预览，但不能宣称修复成功。

### 4.7 长任务“卡死”、进度条不真实和不可中断

**现象**：用户看到长时间 Thinking、假进度条，无法知道 Maven/SootUp 运行到哪一步，也无法中断，持续消耗 token 和机器资源。

**根因**：把几十秒到数分钟的任务放进同步聊天请求；UI 只有估算进度，没有来自执行器的真实阶段状态。

**处理**：代码上下文提取已使用 `AgentJob + Celery + Redis + SSE`，并保存 `queued/running/succeeded/failed/cancelled`、`progress`、`stage` 和 `message`。取消采用协作式取消：前端写入 `cancel_requested`，worker 在安全检查点退出。

**遗留工作**：把批量生成测试、项目级覆盖率和批量修复迁移为同类 `AgentJob`。真正的进度必须来自完成文件数、Maven 阶段或子任务数，不能只使用时间估算。

### 4.8 流式对话、多会话并行与消息顺序

**现象**：对话生成时无法切换会话；消息偶尔出现在用户消息上方；普通聊天与长任务互相阻塞。

**根因**：一个页面状态若只保存“当前流”，切换会话会取消或覆盖其他会话的 SSE 状态；流式 delta 与消息持久化的时机不一致会造成排序错位。

**处理方向**：以 `conversation_id` 作为流、消息和任务的隔离键；前端维护多个会话的独立流状态；后端先持久化用户消息并创建对应 assistant 占位消息，再持续追加 delta。长任务与聊天流分离。

### 4.9 大 ZIP、对象存储和文件去重

**现象**：大项目 ZIP 曾触发 Nginx `413 Request Entity Too Large`；同一文件/项目重复上传会浪费存储和分析成本。

**根因**：HTTP 代理限制与数据库不适合保存大二进制；文件内容与用户上传记录是不同概念。

**处理方向**：调整 Nginx/后端上传限制；对象正文放 MinIO，MySQL 保存 SHA-256、对象 key、用户关联和项目快照元数据；以内容哈希实现对象去重，同一个二进制仅保存一份，但不同用户仍保留各自可见的上传记录。

### 4.10 UI 信息密度与可理解性

**现象**：文件区按钮重复或臃肿、产物与文件列表太挤、ZIP 上传后缺少树形预览、测试产物无法预览/下载、会话菜单被滚动条裁切。

**处理原则**：

- 生产源码与测试源码清晰分组和标记。
- 单文件操作放在文件行，项目级操作放在项目层，避免重复按钮。
- ZIP 上传后立即显示 Java 文件树、数量、构建系统和上传状态。
- 长任务使用弹窗或抽屉显示真实进度、当前文件、失败数和取消按钮。
- 产物提供预览弹窗、单文件下载、项目压缩下载和在线测试/覆盖率报告入口。

## 5. 当前架构的关键边界

### 5.1 LangGraph 不是后台队列

LangGraph 用于“模型根据观察结果选择下一步工具”。它适合处理：

```text
用户：覆盖率低，修复一下
  -> Agent 选择 repair_low_coverage
  -> 基线 JaCoCo
  -> 诊断
  -> 生成候选测试
  -> 验证指标
  -> 总结真实结果
```

但若一个步骤要跑几分钟、可能有 100 个用户同时执行，就必须将具体执行提交给 Celery。LangGraph 可以发起或观察任务，不能代替队列的并发控制、超时、重试和资源隔离。

### 5.2 Celery 不是持久化业务状态

Redis 中的 Celery 消息和短期结果会过期。用户可见状态必须写入 MySQL：`AgentJob` 是任务事实，Redis 只是分发渠道。

### 5.3 模型不能替代验证

模型生成的测试只是候选。是否可用必须由编译、JUnit、JaCoCo 和报告解析来判定。尤其“覆盖率提升”只能在候选测试的二次运行数据超过基线时成立。

## 6. 下一阶段优先级

1. 将批量生成测试、全项目覆盖率、批量修复迁移到 `AgentJob + Celery`，每个文件或每个项目阶段都写入真实进度。
2. 让 LangGraph 工具调用提交后台任务并返回 `job_id`，前端自动订阅任务 SSE，而不是等待同步聊天请求。
3. 增加任务幂等键：同一用户、同一项目快照、同一文件集合、同一操作参数不应重复排队。
4. 增加队列和资源限制：用户并发任务数、项目最大解压大小、Maven 超时、worker 并发数、失败重试策略。
5. 为 LangGraph 增加持久 checkpoint，或明确继续采用 MySQL 会话消息作为对话上下文来源；不能把“数据库有历史消息”误称为“图执行可恢复”。
6. 建立端到端测试：上传 ZIP -> 提取上下文 -> 问 FQN/Jimple -> 生成测试 -> 运行 JaCoCo -> 修复低覆盖率 -> 取消任务。

## 7. 一句话总结

这个项目的正确组合是：**FastAPI 负责接口和事务，LangGraph 负责 Agent 决策，Celery 负责长任务执行，MySQL 记录业务事实，MinIO 保存文件对象，Java 工具链负责验证模型产物。**
