# 爬虫平台项目推进流程

> 基于《五服务器爬虫平台架构设计》文档的分阶段落地方案
>
> 文档版本：v1.0
> 生成日期：2026-04-01

---

## 一、项目概述

### 1.1 目标

将现有四个独立爬虫脚本（`afu`、`deepseek`、`doubao`、`yuanbao`）改造为统一平台架构：

- **1台主队列服务器**：统一任务调度、账号管理、状态聚合
- **4台消费者服务器**：各爬虫专属消费队列，执行浏览器自动化
- **统一能力**：时间窗控制、调度策略、账号生命周期、日志聚合

### 1.2 当前状态

现有代码是"脚本化"体系，每个爬虫：

- 自己轮询数据库
- 自己选择账号
- 自己更新任务状态
- 没有统一调度入口
- 没有统一账号表

### 1.3 核心原则

- **职责收口优先**：先拆掉各爬虫的独立调度职责，再建平台调度
- **分阶段验证**：每个阶段完成后必须可运行、可验证
- **风险可控**：不追求一步到位，避免系统不可用

---

## 二、总体推进路线图

```text
阶段 A：统一任务入口
  ↓ 目标：主队列服务器成为唯一任务分发源
  ↓ 产出：四个爬虫变为被动执行器

阶段 B：消费队列拆分
  ↓ 目标：五台服务器基本调度链路打通
  ↓ 产出：Redis队列 + 基础主状态流转

阶段 C：账号主表接管
  ↓ 目标：账号生命周期平台化
  ↓ 产出：统一账号表 + 状态机 + 备用账号接管

阶段 D：调度策略平台化
  ↓ 目标：FIFO/LIFO/Priority 统一配置
  ↓ 产出：策略引擎 + 优先级字段

阶段 E：平台能力增强
  ↓ 目标：可视化管理 + 告警 + 日志
  ↓ 产出：Web管理界面 + CLS集成
```

---

## 三、阶段 A：统一任务入口

### 3.1 阶段目标

**目标**：让主队列服务器成为唯一任务分发源，四个爬虫停止自己轮询数据库

**验收标准**：

- ✅ `py_main.py` 改为 `platform/main_server.py`，作为主队列调度服务
- ✅ `afu.py`、`doubao.py`、`deepseek.py`、`yuanbao.py` 改为只接收任务参数，不再自己查库
- ✅ 主服务能正常从数据库取任务并分发到各爬虫（暂不依赖Redis，可直接调用）
- ✅ 主服务维护任务主状态表（新表 `task_master_status`）

### 3.2 详细任务清单

#### 任务 A-1：设计主任务展开逻辑

**内容**：

- 确定"问题执行单元"的数据结构
- 设计从 `ent_data_product_llm_task` 展开为问题任务的算法
- 确定 `QueueName` 与 `LlmKey` 的映射规则

**产出文件**：

- `D:\python\craw-platform\platform\dispatcher\task_expander.py`

**依赖**：无

**预计工时**：4小时

---

#### 任务 A-2：创建任务主状态表

**内容**：

```sql
CREATE TABLE task_master_status (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    product_llm_task_id CHAR(36) NOT NULL,
    question_id CHAR(36) NOT NULL,
    round_num INT NOT NULL,
    queue_name VARCHAR(32) NOT NULL,
    execute_status VARCHAR(16) NOT NULL,  -- pending/dispatched/claimed/running/completed/failed
    account_id VARCHAR(128) NULL,
    server_id VARCHAR(32) NULL,
    priority INT DEFAULT 50,
    dispatched_at DATETIME NULL,
    claimed_at DATETIME NULL,
    completed_at DATETIME NULL,
    fail_reason VARCHAR(255) NULL,
    retry_count INT DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_task_execution (product_llm_task_id, question_id, round_num)
);
```

**产出文件**：

- SQL 迁移脚本：`D:\python\craw-platform\platform\store\migrations\001_create_task_master_status.sql`

**依赖**：A-1

**预计工时**：2小时

---

#### 任务 A-3：实现主队列调度器核心逻辑

**内容**：

- 从数据库查询待处理任务（`ent_data_product_llm_task.Status = '未开始'`）
- 调用 `task_expander` 展开为问题执行单元
- 按 `LlmKey` 分配到对应爬虫执行函数
- 更新 `task_master_status` 状态流转

**产出文件**：

- `D:\python\craw-platform\platform\dispatcher\master_dispatcher.py`

**依赖**：A-1, A-2

**预计工时**：8小时

---

#### 任务 A-4：改造 py_main.py 为主服务入口

**内容**：

- 重写 `py_main.py`，改为调用 `master_dispatcher.run_once()` 或启动调度循环
- 移除原有的 `AIModelParallelRunner` 直接调度逻辑
- 增加配置文件读取（数据库配置、爬虫模块路径等）

**产出文件**：

- `D:\python\craw-platform\platform\main_server.py`
- 修改原 `py_main.py` 为备份文件 `py_main.py.backup`

**依赖**：A-3

**预计工时**：4小时

---

#### 任务 A-5：改造 afu.py 为执行器模式

**内容**：

- 移除 `afu.py` 中的数据库轮询逻辑
- 新增 `execute_task(task_info: dict)` 函数，接收主服务传入的任务参数
- `task_info` 包含：`product_llm_task_id`、`question_id`、`question_name`、`round_num`
- 任务完成后不更新主任务表，而是返回执行结果给调用方

**产出文件**：

- `D:\python\craw-platform\afu\afu.py`（改造后）

**依赖**：A-4

**预计工时**：6小时

---

#### 任务 A-6：改造 doubao.py 为执行器模式

**内容**：与 A-5 相同

**产出文件**：

- `D:\python\craw-platform\doubao\doubao.py`（改造后）

**依赖**：A-4

**预计工时**：6小时

---

#### 任务 A-7：改造 deepseek.py 为执行器模式

**内容**：

- 移除 `deepseek.py` 中的数据库轮询和 cookie 文件扫描调度逻辑
- 新增 `execute_task(task_info: dict, account_info: dict)` 函数
- `account_info` 包含：`account_id`、`cookie_file_path`
- 当前仍使用本地 cookie 文件，但由主服务指定

**产出文件**：

- `D:\python\craw-platform\deepseek\deepseek.py`（改造后）

**依赖**：A-4

**预计工时**：6小时

---

#### 任务 A-8：改造 yuanbao.py 为执行器模式

**内容**：与 A-7 相同

**产出文件**：

- `D:\python\craw-platform\yuanbao\yuanbao.py`（改造后）

**依赖**：A-4

**预计工时**：6小时

---

#### 任务 A-9：实现主服务结果收集与状态更新

**内容**：

- 主服务调用各爬虫 `execute_task` 后，收集返回结果
- 根据结果更新 `task_master_status.execute_status`
- 调用 `database_usage_example.py` 中的函数回写业务结果

**产出文件**：

- 修改 `D:\python\craw-platform\platform\dispatcher\master_dispatcher.py`

**依赖**：A-5, A-6, A-7, A-8

**预计工时**：6小时

---

#### 任务 A-10：阶段 A 集成测试

**内容**：

- 启动主服务
- 模拟数据库任务数据
- 验证任务能正确展开并调用各爬虫执行器
- 验证状态流转：`pending` → `dispatched` → `completed` / `failed`

**验收标准**：

- ✅ 主服务能取到任务
- ✅ 各爬虫执行器能接收任务并执行
- ✅ 任务状态能正确回写

**依赖**：A-1 至 A-9

**预计工时**：8小时

---

### 3.3 阶段 A 总结

**里程碑**：主队列服务器成为唯一调度入口

**风险点**：

- ⚠️ 各爬虫改造后可能出现执行异常（需要详细测试）
- ⚠️ 主服务与数据库交互频繁，需考虑连接池

**下一步**：进入阶段 B，引入 Redis 队列实现真正的异步分发

---

## 四、阶段 B：消费队列拆分

### 4.1 阶段目标

**目标**：通过 Redis 队列实现主服务与各爬虫服务器的解耦，建立五台服务器的基本调度链路

**验收标准**：

- ✅ Redis 部署就绪（本地或远程）
- ✅ 主服务将问题执行单元发布到对应消费队列
- ✅ 各爬虫服务器从专属队列消费任务
- ✅ 执行结果通过 `queue:result` 回报
- ✅ 主服务监听结果队列并更新状态
- ✅ 时间窗控制：20:00 后停止分发新任务

### 4.2 详细任务清单

#### 任务 B-1：设计队列协议

**内容**：

- 定义队列名称：
  - `queue:master_dispatch` - 主分发队列（可选，可直接发布到消费队列）
  - `queue:afu`、`queue:deepseek`、`queue:doubao`、`queue:yuanbao` - 消费队列
  - `queue:result` - 结果回报队列
- 定义消息格式（JSON）：

```json
{
  "message_type": "task_dispatch" | "task_result",
  "task_id": "生成唯一ID",
  "payload": { ... }
}
```

**产出文件**：

- `D:\python\craw-platform\platform\store\queue_protocol.py`

**依赖**：A 阶段完成

**预计工时**：4小时

---

#### 任务 B-2：实现 Redis 存储层

**内容**：

- 封装 Redis 连接（使用 `redis-py`）
- 实现 `push_to_queue(queue_name, message)` 函数
- 实现 `pop_from_queue(queue_name, timeout)` 函数
- 实现 `publish_result(result_data)` 函数

**产出文件**：

- `D:\python\craw-platform\platform\store\redis_store.py`

**依赖**：B-1

**预计工时**：4小时

---

#### 任务 B-3：改造主服务为队列发布者

**内容**：

- 修改 `master_dispatcher.py`，不再直接调用爬虫函数
- 改为：
  - 展开问题执行单元
  - 构造任务消息
  - 调用 `redis_store.push_to_queue(queue_name, message)`
  - 更新 `task_master_status.dispatch_status = 'dispatched'`
  - 记录 `dispatched_at`

**产出文件**：

- 修改 `D:\python\craw-platform\platform\dispatcher\master_dispatcher.py`

**依赖**：B-2

**预计工时**：4小时

---

#### 任务 B-4：改造各爬虫为队列消费者

**内容**：

- 为每个爬虫创建消费者循环：
  - `while True: message = redis_store.pop_from_queue(queue_name)`
  - 解析消息
  - 调用 `execute_task(task_info)`（A 阶段改造好的函数）
  - 构造结果消息
  - `redis_store.publish_result(result)`

**产出文件**：

- `D:\python\craw-platform\platform\consumers\afu_consumer.py`
- `D:\python\craw-platform\platform\consumers\deepseek_consumer.py`
- `D:\python\craw-platform\platform\consumers\doubao_consumer.py`
- `D:\python\craw-platform\platform\consumers\yuanbao_consumer.py`

**依赖**：B-2, A 阶段

**预计工时**：12小时（每个爬虫 3 小时）

---

#### 任务 B-5：改造主服务为结果监听者

**内容**：

- 主服务新增 `ResultListener` 线程/协程
- 从 `queue:result` 消费结果消息
- 根据 `task_id` 找到对应的 `task_master_status` 记录
- 更新状态：`running` → `completed` / `failed`
- 记录完成时间、失败原因等

**产出文件**：

- 修改 `D:\python\craw-platform\platform\dispatcher\master_dispatcher.py`
- 新增 `D:\python\craw-platform\platform\tasks\result_listener.py`

**依赖**：B-4

**预计工时**：6小时

---

#### 任务 B-6：实现时间窗控制

**内容**：

- 在主服务增加时间检查逻辑
- 定义时间范围：09:00 - 20:00
- 超过 20:00 后，`master_dispatcher` 停止向消费队列推送新任务
- 记录日志："时间窗已关闭，停止分发新任务"
- 注意：已分发任务不受影响，继续执行

**产出文件**：

- 修改 `D:\python\craw-platform\platform\dispatcher\time_window.py`

**依赖**：B-3

**预计工时**：4小时

---

#### 任务 B-7：阶段 B 集成测试

**内容**：

- 启动 Redis 服务
- 启动主服务（调度 + 结果监听）
- 分别启动四个消费者服务
- 模拟数据库任务数据，观察：
  - 任务是否正确分发到对应队列
  - 消费者是否正确领取并执行
  - 结果是否正确回报
  - 主状态是否正确更新
- 测试时间窗：手动调整系统时间或 mock 时间，验证 20:00 后停止分发

**验收标准**：

- ✅ 完整链路跑通：数据库 → 主队列 → 消费队列 → 爬虫执行 → 结果回报 → 状态更新
- ✅ 时间窗控制生效

**依赖**：B-1 至 B-6

**预计工时**：8小时

---

### 4.3 阶段 B 总结

**里程碑**：五台服务器基本调度链路打通

**风险点**：

- ⚠️ Redis 消息丢失或重复消费（需幂等设计）
- ⚠️ 消费者异常退出导致任务卡在 `claimed` 状态（需心跳机制，留待阶段 E）
- ⚠️ 网络分区导致状态不一致（需业务容忍）

**下一步**：进入阶段 C，引入统一账号表

---

## 五、阶段 C：账号主表接管

### 5.1 阶段目标

**目标**：将账号从"本地文件名/目录名"变为"平台资源"，实现生命周期持久化管理

**验收标准**：

- ✅ 创建 `crawler_accounts` 和 `crawler_account_events` 表
- ✅ 现有账号资源（Profile 目录、cookie 文件）全部登记入表
- ✅ 各爬虫不再自己轮换账号，改为先查表再分配
- ✅ 失败计数由数据库维护，进程重启不丢失
- ✅ 停用账号不会再次进入分配池
- ✅ 备用账号按规则接管（正常账号停用后启用）

### 5.2 详细任务清单

#### 任务 C-1：设计账号数据模型

**内容**：

- 确定 `crawler_accounts` 表字段（详见架构文档 7.4）
- 确定账号类型（`primary` / `backup`）
- 确定资源来源（`profile` / `cookie`）
- 确定状态枚举：`normal` / `running` / `suspicious` / `abnormal` / `disabled` / `backup`
- 设计 `crawler_account_events` 表记录所有状态变更

**产出文件**：

- SQL 迁移脚本：`D:\python\craw-platform\platform\store\migrations\002_create_crawler_accounts.sql`

**依赖**：B 阶段完成

**预计工时**：4小时

---

#### 任务 C-2：编写账号资源登记脚本

**内容**：

- 扫描现有资源：
  - `afu`: `D:/afu_real_profiles/account_*`
  - `doubao`: `D:/doubao_real_profiles/account_*`
  - `deepseek`: `D:/python/craw-platform/deepseek/deepseek_cookie_file/cookies*.json`
  - `yuanbao`: `D:/python/craw-platform/yuanbao/yuanbao_cookie_file/cookies*.json`
- 批量插入 `crawler_accounts` 表
- 标记所有账号初始状态为 `normal`
- 区分 `primary` 和 `backup` 账号（根据用户提供的信息）

**产出文件**：

- `D:\python\craw-platform\platform\accounts\account_initializer.py`

**依赖**：C-1

**预计工时**：4小时

---

#### 任务 C-3：实现账号分配器

**内容**：

- 封装 `AccountAllocator` 类
- 方法 `allocate(crawler_name: str) -> dict`：
  - 查询 `crawler_accounts` 表
  - 筛选条件：`crawler = crawler_name`、`status IN ('normal', 'running')`、`account_type = 'primary'`
  - 排除 `server_id` 与当前服务器不匹配的账号（Profile 绑定规则）
  - 随机选择一个，返回账号信息（含 `resource_path`）
  - 更新 `status = 'running'`，记录 `server_id`
- 线程安全：使用数据库事务和行锁

**产出文件**：

- `D:\python\craw-platform\platform\accounts\account_allocator.py`

**依赖**：C-1, C-2

**预计工时**：6小时

---

#### 任务 C-4：实现账号状态机

**内容**：

- 封装 `AccountStateMachine` 类
- 状态流转规则：
  - `normal` → `suspicious`（第一次失败）
  - `suspicious` → `abnormal`（第二次失败）
  - `abnormal` → `disabled`（第三次失败）
  - 任意状态 → `normal`（成功一次）
  - `disabled` 后不再参与分配
- 每次状态变更需写入 `crawler_account_events` 表

**产出文件**：

- `D:\python\craw-platform\platform\accounts\account_state_machine.py`

**依赖**：C-1

**预计工时**：4小时

---

#### 任务 C-5：改造 afu.py 使用账号表

**内容**：

- `execute_task(task_info)` 增加步骤：
  - 调用 `AccountAllocator.allocate('afu')` 获取账号
  - 传入的 `account_info` 包含 `resource_path`（Profile 目录）
  - 执行任务
  - 成功：`AccountStateMachine.recover(account_id)`
  - 失败：`AccountStateMachine.handle_fail(account_id, reason)`
  - 释放账号：`status` 恢复为 `normal`（成功）或保持失败状态

**产出文件**：

- 修改 `D:\python\craw-platform\afu\afu.py`

**依赖**：C-3, C-4

**预计工时**：4小时

---

#### 任务 C-6：改造 doubao.py 使用账号表

**内容**：与 C-5 相同

**产出文件**：

- 修改 `D:\python\craw-platform\doubao\doubao.py`

**依赖**：C-3, C-4

**预计工时**：4小时

---

#### 任务 C-7：改造 deepseek.py 使用账号表

**内容**：

- `execute_task(task_info, account_info)` 中 `account_info` 从 `AccountAllocator` 获取
- `resource_path` 为 cookie 文件路径
- 成功/失败调用状态机更新

**产出文件**：

- 修改 `D:\python\craw-platform\deepseek\deepseek.py`

**依赖**：C-3, C-4

**预计工时**：4小时

---

#### 任务 C-8：改造 yuanbao.py 使用账号表

**内容**：与 C-7 相同

**产出文件**：

- 修改 `D:\python\craw-platform\yuanbao\yuanbao.py`

**依赖**：C-3, C-4

**预计工时**：4小时

---

#### 任务 C-9：实现备用账号接管逻辑

**内容**：

- 在 `AccountAllocator` 中：
  - 当 `primary` 账号池耗尽或全部 `disabled` 时
  - 查询 `account_type = 'backup'` 且 `status = 'normal'` 的账号
  - 分配备用账号，并记录事件 `event_type = 'switch_backup'`
- 主服务需监控备用账号使用情况并告警（告警实现留待阶段 E）

**产出文件**：

- 修改 `D:\python\craw-platform\platform\accounts\account_allocator.py`

**依赖**：C-3

**预计工时**：4小时

---

#### 任务 C-10：阶段 C 集成测试

**内容**：

- 初始化账号表（执行 C-2 脚本）
- 模拟任务执行：
  - 正常执行，账号状态保持 `normal`
  - 模拟失败，观察失败计数和状态流转
  - 连续失败 3 次，验证账号变 `disabled`
  - 验证 `disabled` 账号不再被分配
  - 所有正常账号停用后，验证备用账号接管

**验收标准**：

- ✅ 账号状态持久化到数据库
- ✅ 失败次数跨进程正确累计
- ✅ 停用账号不再分配
- ✅ 备用账号按规则启用

**依赖**：C-1 至 C-9

**预计工时**：8小时

---

### 5.3 阶段 C 总结

**里程碑**：账号生命周期平台化

**风险点**：

- ⚠️ Profile 绑定的服务器规则需明确（如果服务器 A 宕机，其 Profile 无法被服务器 B 使用）
- ⚠️ 备用账号数量不足可能导致任务阻塞
- ⚠️ 账号状态更新竞争（多个任务同时失败需原子操作）

**下一步**：进入阶段 D，实现调度策略配置化

---

## 六、阶段 D：调度策略平台化

### 6.1 阶段目标

**目标**：将调度策略从"硬编码"变为"可配置"，支持 FIFO / LIFO / Priority 规则

**验收标准**：

- ✅ 主服务支持配置化调度策略（`priority_fifo` 为默认）
- ✅ 任务展开时支持优先级字段（`PriorityScore`）
- ✅ 主队列排序逻辑可动态调整（无需重启）
- ✅ 优先级字段在数据库和消息体中都存在

### 6.2 详细任务清单

#### 任务 D-1：设计优先级字段与默认规则

**内容**：

- 在 `task_master_status` 表增加 `priority` 字段（INT，0-100，默认 50）
- 定义优先级映射：
  - `high` → 100
  - `medium` → 50
  - `low` → 10
- 确定优先级来源：
  - 如果现有业务表没有优先级字段，则全部设为 `medium`
  - 后续可通过人工干预或业务规则调整

**产出文件**：

- SQL 迁移脚本：`D:\python\craw-platform\platform\store\migrations\003_add_priority_field.sql`

**依赖**：B 阶段完成

**预计工时**：2小时

---

#### 任务 D-2：实现调度策略引擎

**内容**：

- 创建 `SchedulePolicy` 类，支持策略：
  - `fifo`：按 `created_at` 升序
  - `lifo`：按 `created_at` 降序
  - `priority_fifo`：先按 `priority` 降序，再按 `created_at` 升序
  - `priority_lifo`：先按 `priority` 降序，再按 `created_at` 降序
- 策略可配置：从配置文件或数据库表读取当前策略
- 策略热更新：主服务支持运行时切换策略

**产出文件**：

- `D:\python\craw-platform\platform\dispatcher\schedule_policy.py`

**依赖**：D-1

**预计工时**：6小时

---

#### 任务 D-3：改造主队列排序逻辑

**内容**：

- 修改 `master_dispatcher`：
  - 查询待分发任务时，按当前策略排序
  - 将 `priority` 字段带入消息体
  - 记录日志："策略=priority_fifo，取出任务ID=xxx，优先级=100"

**产出文件**：

- 修改 `D:\python\craw-platform\platform\dispatcher\master_dispatcher.py`

**依赖**：D-2

**预计工时**：4小时

---

#### 任务 D-4：实现策略配置持久化

**内容**：

- 新增配置表 `platform_config`：
```sql
CREATE TABLE platform_config (
    config_key VARCHAR(64) PRIMARY KEY,
    config_value TEXT NOT NULL,
    description VARCHAR(255) NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
```
- 初始插入：`('schedule_policy', 'priority_fifo', '调度策略')`
- 主服务启动时读取，运行时可通过管理接口修改

**产出文件**：

- SQL 迁移脚本：`D:\python\craw-platform\platform\store\migrations\004_create_platform_config.sql`
- 修改 `master_dispatcher` 读取逻辑

**依赖**：D-3

**预计工时**：4小时

---

#### 任务 D-5：阶段 D 测试

**内容**：

- 准备不同优先级的任务数据
- 切换不同策略，验证队列顺序：
  - `fifo`：先进先出
  - `lifo`：后进先出
  - `priority_fifo`：高优先在前，同级先进先出
- 验证策略切换实时生效

**验收标准**：

- ✅ 策略配置化
- ✅ 排序逻辑正确
- ✅ 热更新有效

**依赖**：D-1 至 D-4

**预计工时**：4小时

---

### 6.3 阶段 D 总结

**里程碑**：调度策略平台化

**风险点**：

- ⚠️ 策略切换可能造成任务顺序剧烈变化，需控制权限
- ⚠️ 优先级字段若业务侧不支持，需人工维护

**下一步**：进入阶段 E，补全平台管理能力

---

## 七、阶段 E：平台能力增强

### 7.1 阶段目标

**目标**：补齐平台可观测性与运营能力，实现可视化管理

**验收标准**：

- ✅ FastAPI 后端提供 RESTful 接口
- ✅ 前端页面（或至少 JSON API）展示：任务面板、队列状态、账号面板、告警面板
- ✅ 腾讯云 CLS 日志集成（可选，可先用文件日志）
- ✅ 钉钉告警集成
- ✅ 人工启停、策略调整等控制能力

### 7.2 详细任务清单

#### 任务 E-1：设计 FastAPI 后端接口

**内容**：

- API 模块划分：
  - `GET /api/tasks`：任务列表（支持分页、状态过滤）
  - `GET /api/tasks/{id}`：任务详情
  - `GET /api/queues`：各队列长度、积压情况
  - `GET /api/accounts`：账号列表、状态统计
  - `POST /api/accounts/{id}/disable`：手动停用账号
  - `POST /api/config/policy`：修改调度策略
  - `GET /api/stats`：平台运行统计
  - `GET /api/logs`：日志查询接口

**产出文件**：

- `D:\python\craw-platform\platform\api\__init__.py`
- `D:\python\craw-platform\platform\api\tasks.py`
- `D:\python\craw-platform\platform\api\queues.py`
- `D:\python\craw-platform\platform\api\accounts.py`
- `D:\python\craw-platform\platform\api\alerts.py`

**依赖**：A-D 阶段完成

**预计工时**：12小时

---

#### 任务 E-2：实现队列监控接口

**内容**：

- 通过 Redis `LLEN queue:*` 获取各队列长度
- 通过 `task_master_status` 表统计各状态任务数量
- 返回 JSON：
```json
{
  "queues": {
    "queue:afu": {"length": 15, "consumers": 3},
    "queue:deepseek": {"length": 8, "consumers": 2}
  },
  "tasks": {
    "pending": 120,
    "dispatched": 30,
    "running": 25,
    "completed": 1000,
    "failed": 50
  }
}
```

**产出文件**：

- `D:\python\craw-platform\platform\api\queues.py`
- 辅助函数 `D:\python\craw-platform\platform\store\redis_store.py#get_queue_lengths()`

**依赖**：B 阶段

**预计工时**：4小时

---

#### 任务 E-3：实现账号面板接口

**内容**：

- `GET /api/accounts`：分页查询账号列表
- 支持筛选：`crawler`、`status`、`account_type`
- 返回字段：`account_id`、`status`、`fail_count`、`last_success_time`、`last_fail_time`
- 聚合统计：各状态账号数量

**产出文件**：

- `D:\python\craw-platform\platform\api\accounts.py`

**依赖**：C 阶段

**预计工时**：4小时

---

#### 任务 E-4：实现告警接口与钉钉集成

**内容**：

- 告警规则：
  - 账号连续失败：同一账号 3 次失败 → 钉钉告警
  - 备用账号接管：主账号池耗尽 → 钉钉告警
  - 队列积压：某队列长度 > 100 → 钉钉告警
  - 消费者失联：超过 10 分钟无心跳（需先实现心跳，留待后续）
- 调用 `shared-methods/shared_methods.py` 中的 `send_dingtalk_message()`
- 告警去重：相同类型告警 30 分钟内不重复

**产出文件**：

- `D:\python\craw-platform\platform\alerts\alert_engine.py`
- 修改 `D:\python\craw-platform\platform\api\alerts.py`

**依赖**：C 阶段

**预计工时**：6小时

---

#### 任务 E-5：实现管理控制接口

**内容**：

- `POST /api/control/pause`：暂停分发（设置 `master_dispatch_enabled = false`）
- `POST /api/control/resume`：恢复分发
- `POST /api/config/schedule-policy`：修改调度策略（写入 `platform_config`）
- `POST /api/accounts/{id}/force-disable`：强制停用账号

**产出文件**：

- 修改 `D:\python\craw-platform\platform\api\__init__.py`

**依赖**：D 阶段

**预计工时**：4小时

---

#### 任务 E-6：实现主服务心跳与健康检查

**内容**：

- 主服务启动时，向 Redis 写入心跳键：`SETEX platform:master:heartbeat 30 <timestamp>`
- 每 10 秒更新一次
- 新增 `GET /health` 接口，返回：
  - 主服务运行状态
  - 最后心跳时间
  - Redis 连接状态
  - 数据库连接状态

**产出文件**：

- 修改 `D:\python\craw-platform\platform\main_server.py`
- 修改 `D:\python\craw-platform\platform\api\__init__.py`

**依赖**：B 阶段

**预计工时**：4小时

---

#### 任务 E-7：实现消费者心跳上报

**内容**：

- 各消费者服务启动时，向 Redis 写入：`SETEX platform:consumer:{crawler_name}:{server_id} 30 <timestamp>`
- 每 10 秒更新一次
- `server_id` 可通过主机名或配置文件指定
- 主服务通过 `platform:consumer:*` 键判断消费者是否在线

**产出文件**：

- 修改四个消费者脚本：`afu_consumer.py` 等

**依赖**：B 阶段

**预计工时**：4小时

---

#### 任务 E-8：实现消费者失联检测与告警

**内容**：

- 主服务定时扫描 `platform:consumer:*` 键
- 超过 30 秒未更新视为失联
- 触发告警："消费者失联：afu@serverA"
- 失联消费者的待处理任务需要重新分发（后续优化）

**产出文件**：

- 修改 `D:\python\craw-platform\platform\alerts\alert_engine.py`

**依赖**：E-6, E-7

**预计工时**：4小时

---

#### 任务 E-9：日志查询接口（CLS 集成准备）

**内容**：

- 由于 CLS 需要腾讯云 SDK 和权限，本阶段只提供文件日志查询
- 各服务日志文件位置：
  - 主服务：`D:\python\craw-platform\logs\master_server.log`
  - 消费者：`D:\python\craw-platform\logs\afu_consumer.log` 等
- `GET /api/logs?service=master&lines=100`：读取最近 N 行
- 返回纯文本或 JSON 行列表

**产出文件**：

- `D:\python\craw-platform\platform\api\logs.py`

**依赖**：无

**预计工时**：4小时

---

#### 任务 E-10：启动主服务（整合所有模块）

**内容**：

- 创建 `D:\python\craw-platform\platform\main.py` 作为统一启动入口
- 初始化：
  - 数据库连接
  -  Redis 连接
  - 加载配置（策略、时间窗等）
  - 启动调度器线程
  - 启动结果监听线程
  - 启动 FastAPI 服务（可在同一进程或独立进程）

**产出文件**：

- `D:\python\craw-platform\platform\main.py`

**依赖**：A-D 阶段

**预计工时**：6小时

---

#### 任务 E-11：编写启动脚本与部署说明

**内容**：

- 主服务启动脚本：`D:\python\craw-platform\scripts\start_master.bat`
- 消费者启动脚本：`D:\python\craw-platform\scripts\start_afu_consumer.bat` 等
- 环境配置：`requirements.txt`、`.env.example`
- 部署文档：`D:\python\craw-platform\docs\deployment.md`

**产出文件**：

- `D:\python\craw-platform\scripts\` 下所有脚本
- `D:\python\craw-platform\requirements.txt`
- `D:\python\craw-platform\.env.example`
- `D:\python\craw-platform\docs\deployment.md`

**依赖**：E-10

**预计工时**：6小时

---

#### 任务 E-12：全链路压测与调优

**内容**：

- 模拟 1000 个任务并发
- 监控：
  - 主服务 CPU / 内存
  - Redis 内存与延迟
  - 数据库 QPS
  - 各爬虫执行速度
- 调整参数：
  - 消费者并发数（每个消费者进程的线程数）
  - 主服务轮询间隔
  - Redis 连接池大小

**验收标准**：

- ✅ 系统稳定运行 1 小时无异常
- ✅ 无任务丢失或重复
- ✅ 资源使用在合理范围

**依赖**：E-1 至 E-11

**预计工时**：8小时

---

#### 任务 E-13：编写用户操作手册

**内容**：

- 平台使用指南：
  - 如何查看任务状态
  - 如何查看账号状态
  - 如何手动干预（暂停/恢复、停用账号、调整策略）
  - 如何查看日志
  - 告警处理流程

**产出文件**：

- `D:\python\craw-platform\docs\user_manual.md`

**依赖**：E-1 至 E-12

**预计工时**：4小时

---

### 7.3 阶段 E 总结

**里程碑**：平台基础管理能力就绪

**风险点**：

- ⚠️ 五台服务器协调部署复杂度高
- ⚠️ 全链路稳定性需长期观察
- ⚠️ 告警规则需要根据实际情况调整

**后续方向**：

- 接入腾讯云 CLS 做日志聚合
- 开发 Vue3 前端管理界面
- 实现更精细的账号分配策略（按服务器负载）
- 实现任务重试队列与超时机制

---

## 八、总体任务汇总

### 8.1 工时估算

| 阶段 | 任务数 | 预计总工时 | 关键路径 |
|------|--------|-----------|---------|
| A | 10 | 46 小时 | A-4 → A-5~A-8 → A-9 |
| B | 7 | 34 小时 | B-2 → B-3/B-4 → B-5 |
| C | 10 | 42 小时 | C-1 → C-2 → C-3 → C-5~C-8 |
| D | 5 | 20 小时 | D-1 → D-2 → D-3 |
| E | 13 | 66 小时 | E-10 依赖前面所有 |
| **合计** | **45** | **208 小时**（约 26 个工作日） | - |

### 8.2 依赖关系图

```text
        A-1 → A-2 → A-3 → A-4 → A-5~A-8 → A-9 → A-10
                     ↓                    ↓
        B-1 → B-2 → B-3    B-4 ← B-5 ← B-6 ← B-7
                     ↓                    ↓
        C-1 → C-2 → C-3 → C-5~C-8 ← C-9 → C-10
                     ↓
        D-1 → D-2 → D-3 → D-4 → D-5
                     ↓
        E-1~E-9 → E-10 → E-11 → E-12 → E-13
```

### 8.3 风险清单

| 风险点 | 影响 | 缓解措施 |
|--------|------|---------|
| Redis 消息丢失/重复 | 任务状态不一致 | 设计幂等操作，任务去重ID |
| 账号状态竞争 | 失败计数错误 | 数据库行锁、事务 |
| Profile 绑定服务器 | 无法跨服务器迁移 | 明确绑定规则，不跨服务器分配 |
| 时间窗理解偏差 | 20:00 后强制终止 | 文档明确"停领不杀任务" |
| 状态一致性 | 分布式系统最终一致 | 接受短暂不一致，提供手动修复工具 |
| 五服务器协调 | 部署复杂度高 | 提供详细部署文档和检查清单 |

---

## 九、推进建议

### 9.1 开发顺序

1. **先做阶段 A**：核心是"职责收口"，让主服务成为唯一调度入口
2. **再做阶段 B**：引入队列解耦，这是平台化的关键一步
3. **接着做阶段 C**：账号生命周期是平台的核心价值
4. **然后做阶段 D**：策略配置化是运营的基础
5. **最后做阶段 E**：可视化和告警是"锦上添花"

### 9.2 验证策略

- 每个阶段完成后进行**集成测试**
- 保持生产环境**只运行一个阶段**，不跳跃
- 阶段间有**回滚方案**（备份原代码）

### 9.3 沟通要点

- 向决策者说明：这不是"加个页面"，而是"架构改造"
- 向运维说明：需要五台服务器协调部署
- 向业务方说明：短期可能不稳定，需灰度验证

---

## 十、附录

### 10.1 数据库变更清单

| 阶段 | 新增表 | 修改表 | 说明 |
|------|--------|--------|------|
| A | `task_master_status` | - | 任务主状态 |
| C | `crawler_accounts`、`crawler_account_events` | - | 账号管理与事件 |
| D | `platform_config` | `task_master_status`（加 `priority`） | 配置与优先级 |
| B | - | 无 | 纯 Redis 队列 |

### 10.2 配置文件清单

```
D:\python\craw-platform\
├── platform\
│   ├── config.yaml          # 主配置（数据库、Redis、策略等）
│   ├── main_server.py       # 主服务入口
│   └── ...
├── scripts\
│   ├── start_master.bat     # 启动主服务
│   ├── start_afu_consumer.bat
│   └── ...
├── .env.example             # 环境变量示例
└── requirements.txt         # Python 依赖
```

### 10.3 检查清单（每个阶段验收用）

**阶段 A 检查清单**：

- [ ] 主服务能正常从数据库取任务
- [ ] 各爬虫 `execute_task` 函数能接收参数并执行
- [ ] 主服务能更新 `task_master_status`
- [ ] 各爬虫不再自己轮询数据库

**阶段 B 检查清单**：

- [ ] Redis 正常运行
- [ ] 主服务能推送消息到队列
- [ ] 各消费者能领取并执行
- [ ] 结果能回报并更新状态
- [ ] 20:00 后停止分发新任务

**阶段 C 检查清单**：

- [ ] 账号表已初始化
- [ ] 账号分配器能正确分配账号
- [ ] 失败状态能正确流转
- [ ] `disabled` 账号不再分配
- [ ] 备用账号接管逻辑正常

**阶段 D 检查清单**：

- [ ] 优先级字段存在且正确
- [ ] 策略引擎支持四种策略
- [ ] 策略切换实时生效
- [ ] 排序逻辑符合预期

**阶段 E 检查清单**：

- [ ] FastAPI 接口全部可用
- [ ] 队列监控显示正确
- [ ] 账号面板显示正确
- [ ] 告警能触发钉钉消息
- [ ] 控制接口能生效
- [ ] 五台服务器能协调运行

---

*文档结束*

**下一步行动**：

1. 确认推进流程与决策者达成一致
2. 从阶段 A 任务 A-1 开始执行
3. 每完成一个任务，更新本文件的状态
4. 每完成一个阶段，进行集成测试并验收
