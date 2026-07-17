# LangChain 与当前 Agent 项目架构对比

## 一句话结论

这个项目不是没有用 LangChain，而是采用了**局部 LangChain 化**的方式：

```text
业务系统自己控制
LangChain / LangGraph 负责 LLM 与工具之间的调度
```

更准确地说：

```text
当前项目 = 自研业务系统 + LangChain/LangGraph Agent 调度层
```

它不是：

```text
完全没有用 LangChain
```

也不是：

```text
整个项目都按 LangChain 完整架构重写
```

## 当前项目里 LangChain 做了什么

当前项目中，LangChain/LangGraph 主要负责 LLM 交互层：

```text
用户自然语言
 -> ChatOpenAI
 -> 判断是否需要调用工具
 -> StructuredTool
 -> 调用后端业务工具
 -> 工具结果返回给模型
 -> 模型继续回答
```

也就是，它主要封装了这些东西：

```text
模型调用接口
消息格式
工具定义格式
工具绑定给模型
agent -> tools -> agent 的循环
```

对应到项目里，大概是：

```text
ChatOpenAI        统一模型调用
SystemMessage     系统消息
HumanMessage      用户消息
AIMessage         模型消息
StructuredTool    把业务函数包装成模型可调用工具
StateGraph        控制 agent 与 tools 的循环
ToolNode          执行工具调用
```

所以当前项目已经用了 LangChain 生态，只是没有把所有 LLM 逻辑都抽象成 LangChain chain。

## 当前项目的业务逻辑在哪里

当前项目的核心业务逻辑仍然在自己的后端服务层里，例如：

```text
Java 文件分析
JUnit 测试生成
artifact 保存
测试编译
JaCoCo 覆盖率
测试修复
MySQL 持久化
MinIO 文件存储
Celery 后台任务
权限和重复调用限制
```

这些不是 LangChain 自动完成的。

LangChain 只看到高层工具入口，例如：

```text
generate_tests(file_id, goal)
compile_artifact(artifact_id)
run_coverage(artifact_id)
repair_artifact(artifact_id)
analyze_file(file_id)
read_code_context(file_id)
```

但工具内部真正怎么执行，仍然由项目自己的业务代码控制。

## 当前写法的特点

当前项目更像：

```text
一个 tool = 一个完整业务能力
```

每个 tool 里面自己处理：

```text
准备上下文
拼 prompt
调用 LLM
解析输出
业务校验
保存 artifact
写数据库
返回结果
```

例如测试生成工具可能是：

```text
tool_generate_tests()
 -> 准备 Java 分析结果
 -> render_generation_prompt()
 -> 调用模型
 -> extract_java()
 -> 修正 class/package/import
 -> 保存 artifact
 -> 返回 artifact_id
```

这种方式的特点是：业务 tool 比较厚，LLM 调用逻辑嵌在具体 tool 里面。

## 完整 LangChain 风格是什么样

更完整的 LangChain 风格，不是让 LangChain 接管数据库、文件和权限，而是把 LLM 应用层拆成更标准的组件：

```text
Prompt
Model
Parser
Retriever
Tool
Chain
Graph
```

比如测试生成可以被拆成：

```text
generation_prompt
generation_model
java_code_parser
generation_chain = generation_prompt -> model -> parser
```

修复测试可以被拆成：

```text
repair_prompt
repair_model
java_code_parser
repair_chain = repair_prompt -> model -> parser
```

解释 artifact 可以被拆成：

```text
explain_prompt
explain_model
text_parser
explain_chain = explain_prompt -> model -> parser
```

这样 tool 会变薄：

```text
generate_tests_tool()
 -> 准备业务上下文
 -> 调 generation_chain.invoke(...)
 -> 保存 artifact
 -> 返回结果
```

也就是说：

```text
当前写法：
Prompt、模型调用、解析逻辑写在具体 tool 里

更完整 LangChain 写法：
Prompt、模型、解析器抽象成独立 chain
tool 只负责准备业务上下文和处理业务结果
```

## 两种方式的核心区别

### 当前项目方式

```text
AgentService 是主骨架
LangChain/LangGraph 是调度外壳
```

优点：

```text
业务改动直接
每个工具可以高度定制 prompt
不同工具可以用不同模型参数
输出解析可以贴近具体业务
调试方式更像普通后端项目
业务边界非常清楚
```

缺点：

```text
Prompt、模型调用、解析逻辑容易分散在不同 tool 里
多个 tool 如果有重复 LLM 调用模式，复用性会差一些
后期 LLM 组件变多时，结构可能不够统一
```

### 更完整 LangChain 方式

```text
LLM 应用层是主骨架
业务 service 作为 tool / chain / graph node 接入
```

优点：

```text
Prompt、Model、Parser 更标准化
相似 LLM 调用逻辑更容易复用
RAG、retriever、parser、callback、tracing 更容易统一管理
复杂 agent workflow 可以更清楚地表达成 graph
```

缺点：

```text
前期抽象成本更高
简单业务可能显得重
如果为了 LangChain 而 LangChain，反而会让代码绕
业务复杂但 LLM 组件不多时，收益不一定明显
```

## 为什么当前项目这样做是合理的

这个项目的复杂度主要不在普通问答，而在业务执行链路：

```text
上传 Java
分析源码
生成 JUnit
保存 artifact
编译
跑覆盖率
修复失败测试
后台任务进度
历史记录
权限控制
```

这些部分需要确定性、可测试、可审计。

所以当前设计把核心业务沉在 service 层，再用 LangChain/LangGraph 做薄调度层，是合理的：

```text
业务逻辑：自己控制
数据库/文件/任务：自己控制
安全边界：自己控制
LLM 调工具循环：交给 LangChain/LangGraph 简化
```

## 这是不是画蛇添足

不是。

如果项目只是按钮式流程：

```text
点击生成测试
点击编译
点击修复
```

那么可以不用 agent，直接写固定后端流程。

但如果用户会自然语言提出任务：

```text
帮我看看这个类该测什么
生成测试然后看看为什么编译不过
覆盖率低，帮我修一下
解释上次生成的 artifact
```

那么 LangChain/LangGraph 这一层就有价值。

它让模型可以在有限工具中选择下一步，减少手写 tool call 循环、消息格式转换和工具结果回传。

## 面试回答版本

可以这样回答：

> 这个项目不是没有用 LangChain，而是局部使用 LangChain/LangGraph。  
> 我没有让 LangChain 接管整个业务架构，而是把它放在 LLM 编排层。  
> 项目的核心业务，包括 Java 分析、测试生成、artifact 保存、编译、覆盖率、修复、MySQL、MinIO、Celery 和权限控制，仍然由自己的 service 层负责。  
> LangChain 主要用于模型调用、消息封装、工具封装；LangGraph 用于 agent -> tools -> agent 的循环。

如果被问为什么不完全 LangChain 化，可以回答：

> 因为当前项目的复杂度主要在业务执行链路，而不是 LLM 组件复用。  
> 我选择先把确定性的业务能力沉在 service 层，再把少数安全、清晰、可审计的业务入口包装成工具交给模型调用。  
> 这样既能利用 LangChain/LangGraph 在 Agent 调度上的优势，又能保持业务系统的可测试性和可维护性。  
> 如果后续 Prompt、Parser、RAG、Graph 流程越来越多，我会再把这些 LLM 层逐步抽成 LangChain chains 和 LangGraph nodes。

## 最终理解

可以把当前项目理解成：

```text
我现在是把 LLM 调用逻辑写在业务 tool 里面；
更 LangChain 的写法是把 Prompt / Model / Parser / Chain 抽出来复用；
tool 只负责把业务上下文交给 chain，并处理保存、权限、状态这些业务事情。
```

最关键的一句话：

```text
LangChain 不替我写业务逻辑。
LangChain 抽象的是 LLM 应用部件。
业务流程、权限、持久化、任务状态，仍然由我自己的后端系统控制。
```
