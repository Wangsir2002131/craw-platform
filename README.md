# Craw Platform 代码文档

## 1. 项目概览

Craw Platform 是一个面向多模型爬虫任务的调度与执行平台。以 MySQL 中的业务任务为入口，将任务拆分为可执行单元，按模型分发到 Redis 队列，由不同模型消费者执行爬虫并回写结果。

**支持的模型队列**：`afu`、`doubao`、`deepseek`、`yuanbao`

---

## 2. 项目目录结构

```
craw-platform/
├── craw_platform/              # 核心平台代码（主包）
│   ├── config.py               # 环境变量与基础配置
│   ├── main_server.py          # 集成主服务入口
│   ├── logging_config.py       # 日志配置
│   ├── account/                # 账号分配与状态管理
│   ├── alerts/                 # 告警监控系统
│   ├── api/routes/             # FastAPI 路由
│   ├── consumers/              # Redis 消费者
│   ├── dispatcher/             # 任务调度器
│   ├── heartbeat/              # 心跳与健康检查
│   ├── queue/                  # Redis 队列协议与存储
│   ├── store/                  # MySQL 数据访问层
│   └── tasks/                  # 结果监听
├── afu/                        # AFu 爬虫实现
├── deepseek/                   # DeepSeek 爬虫实现
├── doubao/                     # Doubao 爬虫实现
├── yuanbao/                    # Yuanbao 爬虫实现
├── pages/                      # 后台管理页面
├── scripts/                    # 辅助脚本
├── tests/                      # 测试
├── shared-methods/             # 共享工具方法
├── dashboard.html              # 后台首页
├── py_main.py                  # 老版单体运行入口
├── run_*_consumer.py           # 独立消费者启动脚本
└── requirements.txt            # Python 依赖
```

---

## 3. 核心模块详解

### 3.1 配置模块 (`craw_platform/config.py`)

集中管理所有环境变量和默认配置。

| 配置项 | 环境变量 | 默认值 | 说明 |
|--------|----------|--------|------|
| `DB_CONFIG` | `DB_HOST`/`DB_PORT`/`DB_USER`/`DB_PASSWORD`/`DB_NAME` | `127.0.0.1:3306 root/123456 test` | MySQL 连接 |
| `REDIS_URL` | `REDIS_URL` | `redis://127.0.0.1:6379/0` | Redis 连接 |
| `DISPATCH_INTERVAL` | `DISPATCH_INTERVAL` | `5` | 调度循环间隔(秒) |
| `BATCH_SIZE` | `BATCH_SIZE` | `100` | 每轮最大读取任务数 |
| `EXECUTE_CRAWLERS` | `EXECUTE_CRAWLERS` | `0` | 是否直接执行爬虫 |
| `CONSUMER_MAX_RETRIES` | `CONSUMER_MAX_RETRIES` | `3` | 消费失败最大重试 |
| `CONSUMER_START_HOUR` | `CONSUMER_START_HOUR` | `9` | 消费时间窗口起始 |
| `CONSUMER_END_HOUR` | `CONSUMER_END_HOUR` | `20` | 消费时间窗口结束 |
| `PRIORITY_QUEUE_MIN` | `PRIORITY_QUEUE_MIN` | `51` | 优先级队列最小分 |

**爬虫模块映射**：
```python
CRAWLER_MODULES = {
    "afu": "afu.afu",
    "doubao": "doubao.doubao",
    "deepseek": "deepseek.deepseek",
    "yuanbao": "yuanbao.yuanbao",
}
```

---

### 3.2 主服务入口 (`craw_platform/main_server.py`)

集成服务的入口，启动后同时运行以下组件：

#### 启动模式

| 模式 | 参数 | 说明 |
|------|------|------|
| 集成服务 | `--forever` | API + 调度 + 心跳 + 健康检查 + 结果监听 |
| 仅 API | `--api-only` | 只启动 FastAPI 服务 |
| 仅调度 | `--dispatcher-only` | 只启动调度循环 |
| 单次调度 | `--once` | 执行一轮调度后退出 |
| 托管消费者 | `--managed-consumers` | 主服务管理消费者线程 |

#### 后台线程架构

```
main_server
├── API Server (uvicorn/FastAPI)
├── dispatcher-loop (调度循环)
├── master-heartbeat (主服务心跳)
├── health-checker (健康检查)
├── result-listener (结果监听)
└── alert monitors (告警监控 ×4)
```

#### API 路由注册

| 路由前缀 | 模块 | 功能 |
|----------|------|------|
| `/tasks` | `api/routes/task.py` | 任务 CRUD |
| `/queues` | `api/routes/queue.py` | 队列管理 |
| `/accounts` | `api/routes/account.py` | 账号管理 |
| `/alerts` | `api/routes/alert.py` | 告警管理 |
| `/control` | `api/routes/control.py` | 服务控制 |
| `/health-status` | `api/routes/health_status.py` | 健康状态 |
| `/logs` | `api/routes/log.py` | 日志查看 |
| `/stats` | `api/routes/stats.py` | 统计信息 |
| `/api/tasks` | dashboard 任务面板 | |
| `/api/queues` | dashboard 队列面板 | |
| `/api/accounts` | dashboard 账号面板 | |

---

### 3.3 调度器模块 (`craw_platform/dispatcher/`)

#### MasterDispatcher (`master_dispatcher.py`)

核心调度器，负责从数据库读取任务并分发到 Redis 队列。

**核心方法**：

| 方法 | 功能 |
|------|------|
| `fetch_pending_tasks(limit)` | 从 `ent_data_product_llm_task` 查询 `Status='未开始'` 的任务 |
| `dispatch_once(limit)` | 执行一轮调度：读取→拆分→入队 |
| `publish_task(task_id, unit)` | 将任务消息推入 Redis 队列 |
| `execute_task(task_id, unit)` | 直接调用爬虫模块执行（兼容模式） |
| `run_forever(interval, limit)` | 持续循环调度 |

**调度流程**：
```
fetch_pending_tasks → TaskExpander.expand_task → ScheduleStrategy.calculate_priority
    → db_store.create_or_get_task_record → publish_task / execute_task
```

#### TaskExpander (`task_expander.py`)

将一条业务任务拆分为多个执行单元（按问题 × 轮次展开）。

**输入**：一条 `ent_data_product_llm_task` 记录（含关联问题）
**输出**：执行单元列表，每个单元包含：
```python
{
    "product_llm_task_id": str,
    "question_id": str,
    "question_name": str,
    "round_num": int,        # 1 到 MaxRounds
    "queue_name": str,       # 如 "queue:deepseek"
    "priority": int,         # 0-100
}
```

**LlmKey 映射**：支持别名解析（如 "豆包" → "doubao"，"元宝" → "yuanbao"）

#### ScheduleStrategy (`schedule_strategy.py`)

计算任务优先级分数（0-100），支持多种加权因素：

| 因素 | 配置项 | 说明 |
|------|--------|------|
| 源优先级 | `source_priority_weight` | 原始优先级权重 |
| 模型权重 | `model_weights` | 按模型加权 |
| 产品权重 | `product_weights` | 按产品加权 |
| 时间加速 | `age_boost_per_hour` | 每小时增加分数 |
| 轮次惩罚 | `round_penalty` | 高轮次降低优先级 |

#### TimeWindowController (`time_window.py`)

控制消费者工作时间窗口，默认 9:00-20:00。

| 方法 | 功能 |
|------|------|
| `is_open(time)` | 判断当前是否在工作窗口内 |
| `seconds_until_open(time)` | 距下次开窗的秒数 |
| `seconds_until_close(time)` | 距关窗的秒数 |

#### ResultCollector (`result_collector.py`)

收集爬虫执行结果并更新数据库状态。

- 成功 → `task_master_status.execute_status = 'completed'`
- 失败 → `task_master_status.execute_status = 'failed'`
- 所有子任务完成 → `ent_data_product_llm_task.Status = '爬网完成'`

---

### 3.4 队列模块 (`craw_platform/queue/`)

#### 队列协议 (`protocol.py`)

定义队列名称和消息格式。

**队列名称**：
| 类型 | 名称 |
|------|------|
| AFu | `queue:afu` |
| Doubao | `queue:doubao` |
| DeepSeek | `queue:deepseek` |
| Yuanbao | `queue:yuanbao` |
| 结果队列 | `queue:results` |
| 死信队列 | `queue:dead-letter` |

**消息类型**：`task`、`result`、`control`

**QueueTaskMessage 结构**：
```python
{
    "message_type": "task",
    "product_llm_task_id": str,
    "task_id": int,
    "question_id": str,
    "question_name": str,
    "queue_name": str,
    "round_num": int,
    "priority": int,           # 0-100
    "enqueued_at": str,        # ISO 时间
    "retry_count": int,
    "last_error": str,
}
```

#### RedisQueueStore (`redis_store.py`)

Redis 队列存储实现，封装所有队列操作。

**核心方法**：

| 方法 | 功能 |
|------|------|
| `push(queue_name, message)` | 消息入队（RPUSH） |
| `pop(queue_name)` | 非阻塞出队（LPOP） |
| `blocking_pop(queue_name, timeout)` | 阻塞出队（BLPOP） |
| `blocking_pop_latest(queue_name, timeout)` | LIFO 阻塞出队（BRPOP） |
| `length(queue_name)` | 获取队列长度 |
| `pop_highest_priority(queue_name)` | 弹出最高优先级消息 |
| `count_priority_messages(queue_name)` | 统计高优先级消息数 |
| `update_product_task_priorities(ids, delta/priority)` | 批量调整优先级 |
| `ping()` | 测试 Redis 连通性 |

#### QueueStrategyStore (`strategy_store.py`)

管理队列调度策略，存储在 Redis 中。

| 策略 | 说明 |
|------|------|
| `fifo` | 先进先出（默认） |
| `priority` | 优先级调度 |

---

### 3.5 消费者模块 (`craw_platform/consumers/`)

#### BaseQueueConsumer (`base.py`)

所有模型消费者的基类，实现完整的消费循环。

**消费流程**：
```
run() → 启动心跳线程
  └→ consume_once()
       ├→ 检查时间窗口 (TimeWindowController)
       ├→ _pop_message() (根据策略选择 FIFO/Priority)
       ├→ _execute_with_guard() (调用爬虫模块)
       ├→ 成功 → 推送结果到 queue:results
       ├→ 失败且可重试 → _retry_message()
       └→ 失败且超限 → _push_dead_letter()
```

**关键特性**：
- 自动心跳报告（每 10 秒）
- 时间窗口控制（默认 9:00-20:00）
- 失败自动重试（默认 3 次）
- 超限进入死信队列
- 支持 FIFO / Priority 策略切换
- 支持外部 stop_event 优雅停止

#### 具体消费者

| 类 | 文件 | 队列 | 爬虫模块 |
|----|------|------|----------|
| `AfuConsumer` | `afu_consumer.py` | `queue:afu` | `afu.afu` |
| `DeepseekConsumer` | `deepseek_consumer.py` | `queue:deepseek` | `deepseek.deepseek` |
| `DoubaoConsumer` | `doubao_consumer.py` | `queue:doubao` | `doubao.doubao` |
| `YuanbaoConsumer` | `yuanbao_consumer.py` | `queue:yuanbao` | `yuanbao.yuanbao` |

#### ConsumerManager (`manager.py`)

进程内消费者管理器，支持 dashboard 动态扩缩容。

**核心方法**：

| 方法 | 功能 |
|------|------|
| `configure(enabled, count)` | 启用/配置管理器 |
| `start_defaults()` | 按默认数量启动所有模型消费者 |
| `scale_to(model_key, count)` | 调整指定模型消费者数量 |
| `increment(model_key)` | 增加一个消费者 |
| `decrement(model_key)` | 减少一个消费者 |
| `status()` | 获取所有消费者状态 |
| `shutdown()` | 关闭所有消费者 |

#### ConsumerSupervisor (`supervisor.py`)

外部进程消费者监管器（用于独立启动消费者场景），特性：
- 管理多个 worker 线程
- 每 5 秒发布 supervisor 心跳
- 支持通过 Redis 控制队列接收 increment/decrement 指令

---

### 3.6 心跳与健康检查 (`craw_platform/heartbeat/`)

#### ConsumerHeartbeat (`consumer_heartbeat.py`)

消费者心跳发布器。

- **Redis Key**：`heartbeat:consumer:{consumer_id}`
- **TTL**：30 秒
- **更新频率**：每 10 秒
- **Payload**：`{consumer_id, queue_name, status, timestamp, ttl_seconds, extra}`

#### MasterHeartbeat (`master_heartbeat.py`)

主服务心跳发布器。

- **Redis Key**：`heartbeat:master:{server_id}`
- **TTL**：30 秒
- **更新频率**：每 10 秒

#### HealthChecker (`health_checker.py`)

检测失联消费者。

**判定逻辑**：扫描所有 `heartbeat:consumer:*` 键，如果 `timestamp` 距今超过 `stale_after_seconds`（默认 60 秒）则判定为异常。

---

### 3.7 数据存储 (`craw_platform/store/`)

#### TaskMasterStatusStore (`db_store.py`)

任务状态追踪表（`task_master_status`）的数据访问层。

**核心方法**：

| 方法 | 功能 |
|------|------|
| `fetch_pending_llm_tasks(limit)` | 查询待处理业务任务 |
| `create_or_get_task_record(unit)` | 创建/获取任务状态记录 |
| `update_status(task_id, status)` | 更新任务执行状态 |
| `update_business_task_status(id, status)` | 更新业务任务状态 |
| `count_unfinished_task_units(id)` | 统计未完成子任务数 |
| `adjust_task_priorities(ids, delta)` | 批量调整优先级 |
| `fetch_products_for_llm_task_ids(ids)` | 查询关联产品信息 |

**任务状态流转**：
```
pending → queued → running → completed / failed
```

**数据库表关系**：
```
ent_data_product_llm_task (业务任务)
    ├─ ent_data_product_question (产品-问题关联)
    │       └─ ent_data_question (问题表)
    └─ task_master_status (执行状态跟踪)
```

---

### 3.8 告警系统 (`craw_platform/alerts/`)

#### AlertManager (`alert_manager.py`)

集中告警管理，提供告警触发、存储、通知、确认。

**核心功能**：
- 告警配置管理（启用/禁用）
- 告警抑制（相同告警间隔控制，`suppress_seconds` 参数）
- 通知器注册与分发
- 告警事件存储（最多 1000 条）
- 告警确认（单条/批量）
- 统计汇总

**日志行为**：
- 后台周期巡检触发的告警**不输出终端日志**（`AlertManager.trigger()` 使用 `DEBUG` 级别，`LogNotifier` 不注册到全局 `AlertManager`）
- 告警仅静默写入内存事件列表，通过 UI 轮询或手动刷新查看

#### 监控器

| 监控器 | 检查内容 | 间隔 |
|--------|---------|------|
| `QueueMonitor` | 队列长度 > 100(黄) / > 500(红)、Redis 连接 | 30s |
| `TaskMonitor` | 任务超时、长时间 running | 60s |
| `AccountMonitor` | 账号可用性、错误率 | 60s |
| `SystemMonitor` | 系统资源（CPU/内存/磁盘） | 60s |

#### BaseMonitor — `force_check` 与 `reset_states` 解耦

`BaseMonitor` 提供两个独立操作，职责明确分离：

| 方法 | 作用 |
|------|------|
| `check()` | 执行一次监控检查，触发告警（由后台定时器调用） |
| `reset_states()` | 清空监控器内部去重状态（由调用方显式决定何时执行） |
| `force_check()` | 立即执行 `check()`，**不自动重置状态** |

**`/alerts/force-check` API 的 `clear_history` 参数行为**：

| `clear_history` | 行为 |
|----------------|------|
| `false`（默认） | 保留历史事件，在现有事件基础上增量追加；监控器状态不重置，不会对同一告警重复触发 |
| `true` | 先调用 `monitor.reset_states()` 清空内部去重状态，再执行 `check()`，同时清空历史事件列表 |

> **设计原因**：原始设计中 `force_check()` 内部调用 `reset_states()`，导致前端使用 `clear_history=false` 刷新时，
> `QueueMonitor` / `AccountMonitor` 状态被清空，`check()` 重新将当前状态评估为新告警，产生大量重复事件。
> 现将两者解耦，由 API 层根据 `clear_history` 参数显式控制是否重置状态。

#### TaskMonitor — 多轮次超时告警去重

`TaskMonitor` 使用 `_timed_out_task_ids`（`set`）对超时任务进行**轮次级别**去重：

- 去重键格式：`"{task_id}:{question_id}:{round_num}"`
- 同一任务的不同问题、不同轮次各自产生独立告警
- 已触发告警的轮次不会重复触发，直到该轮次从 `running` 列表消失（任务完成或失败）
- `reset_states()` 会清空该集合，仅在 `clear_history=true` 的强制刷新时触发

#### 告警等级

| 等级 | 值 | 说明 |
|------|-----|------|
| GREEN | `green` | 正常 |
| YELLOW | `yellow` | 警告 |
| RED | `red` | 危险 |
| ERROR | `error` | 严重错误 |

---

### 3.9 账号管理 (`craw_platform/account/`)

#### AccountAllocator (`account_allocator.py`)

统一的账号分配器，从 `account_master` 表分配可用账号。

**分配逻辑**：
1. 查找 `account_status = 'available'` 且 `current_task_count < max_concurrent_tasks` 的账号
2. 按 `priority DESC, last_allocated_at ASC` 排序
3. 行级锁定（`FOR UPDATE`）防止并发冲突
4. 更新计数并记录状态转换

**释放逻辑**：
- 成功 → 状态回 `available`
- 失败 → 状态变 `error`

#### AccountStateMachine (`account_state_machine.py`)

记录账号状态变更历史。

---

### 3.10 控制接口 (`craw_platform/api/routes/control.py`)

| 接口 | 方法 | 功能 |
|------|------|------|
| `/control/status` | GET | 获取服务状态 |
| `/control/pause` | POST | 暂停调度 |
| `/control/resume` | POST | 恢复调度 |
| `/control/restart` | POST | 标记重启 |
| `/control/strategy` | GET/POST | 查看/切换调度策略 |
| `/control/priority/products` | GET | 查看可调优先级的产品 |
| `/control/priority/apply` | POST | 应用优先级调整 |

---

### 3.11 结果监听 (`craw_platform/tasks/result_listener.py`)

持续监听 `queue:results` 队列，收到结果后调用 `ResultCollector` 回写数据库状态。

---

## 4. 队列仪表盘风险等级

队列页面的风险判断逻辑（`_resolve_queue_state` 函数）：

| 风险等级 | 状态标签 | 触发条件 |
|---------|---------|---------|
| 高风险 (high) | 异常 | 堆积 ≥ 100 **或** 消费者 == 0 |
| 中风险 (medium) | 积压 | 堆积 ≥ 50 **或** 消费者 == 1 |
| 低风险 (low) | 正常 | 其他情况 |

---

## 5. 数据流

```
┌─────────────────────────────────────────────────────────────┐
│                     MySQL 数据库                              │
│  ent_data_product_llm_task (Status='未开始')                  │
└────────────────────────┬────────────────────────────────────┘
                         │ fetch_pending_tasks
                         ▼
┌─────────────────────────────────────────────────────────────┐
│               MasterDispatcher                               │
│  TaskExpander → ScheduleStrategy → create_task_record        │
└────────────────────────┬────────────────────────────────────┘
                         │ publish_task (RPUSH)
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    Redis 队列                                 │
│  queue:afu | queue:doubao | queue:deepseek | queue:yuanbao   │
└────────────────────────┬────────────────────────────────────┘
                         │ blocking_pop (BLPOP)
                         ▼
┌─────────────────────────────────────────────────────────────┐
│              BaseQueueConsumer                                │
│  _execute() → crawler_module.execute_task(message)           │
└───────────┬────────────────────────────────┬────────────────┘
            │ 成功                            │ 失败(超重试)
            ▼                                ▼
    queue:results                    queue:dead-letter
            │
            ▼
┌─────────────────────────────────────────────────────────────┐
│         ResultListener → ResultCollector                      │
│  update task_master_status                                   │
│  update ent_data_product_llm_task.Status = '爬网完成'         │
└─────────────────────────────────────────────────────────────┘
```

---

## 6. 启动命令

### 集成服务
```powershell
python -m craw_platform.main_server --forever --host 127.0.0.1 --port 8000
```

### 集成服务 + 托管消费者
```powershell
python -m craw_platform.main_server --forever --managed-consumers --default-consumers-per-model 1
```

### 单独启动消费者
```powershell
python run_afu_consumer.py
python run_doubao_consumer.py
python run_deepseek_consumer.py
python run_yuanbao_consumer.py
```

### 手动入队脚本
```powershell
python scripts/enqueue_pending_tasks_to_redis.py
python scripts/enqueue_pending_tasks_to_redis.py --model deepseek --limit 50
python scripts/enqueue_pending_tasks_to_redis.py --dry-run
```

---

## 7. 依赖清单

| 包 | 版本 | 用途 |
|----|------|------|
| `fastapi` | 0.115.0 | API 框架 |
| `uvicorn[standard]` | 0.32.0 | ASGI 服务器 |
| `PyMySQL` | 1.1.1 | MySQL 驱动 |
| `redis` | 5.2.0 | Redis 客户端 |
| `playwright` | 1.55.0 | 浏览器自动化 |
| `curl-cffi` | 0.13.0 | HTTP 请求 |
| `beautifulsoup4` | 4.14.3 | HTML 解析 |
| `requests` | 2.33.1 | HTTP 请求 |
| `pydantic` | 2.9.2 | 数据校验 |
| `psutil` | ≥5.9.0 | 系统监控 |
| `pytest` | ≥8.0.0 | 测试框架 |

---

## 8. 数据库表结构

### task_master_status（执行状态跟踪）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INT AUTO_INCREMENT | 主键 |
| `product_llm_task_id` | VARCHAR | 业务任务 ID |
| `question_id` | VARCHAR | 问题 ID |
| `round_num` | INT | 执行轮次 |
| `queue_name` | VARCHAR | 目标队列名 |
| `execute_status` | VARCHAR | 状态：pending/queued/running/completed/failed |
| `priority` | INT | 优先级 0-100 |
| `account_id` | VARCHAR | 使用的账号 |
| `dispatched_at` | DATETIME | 派发时间 |
| `claimed_at` | DATETIME | 领取时间 |
| `completed_at` | DATETIME | 完成时间 |
| `fail_reason` | VARCHAR | 失败原因 |
| `retry_count` | INT | 重试次数 |

**唯一约束**：`(product_llm_task_id, question_id, round_num)`

### account_master（账号管理）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INT | 主键 |
| `platform_name` | VARCHAR | 平台名称 |
| `account_key` | VARCHAR | 账号标识 |
| `account_status` | VARCHAR | 状态：available/allocated/error |
| `priority` | INT | 分配优先级 |
| `max_concurrent_tasks` | INT | 最大并发任务数 |
| `current_task_count` | INT | 当前任务计数 |

---

## 9. 关键设计模式

### 9.1 生产者-消费者模式
- 调度器作为生产者将任务推入 Redis 队列
- 消费者从队列阻塞取出并执行

### 9.2 心跳检测机制
- 消费者每 10 秒写入 Redis 心跳（TTL 30 秒）
- 健康检查器每 30 秒扫描超时心跳（阈值 60 秒）

### 9.3 重试与死信
- 消费失败 → 重新入队（最多 3 次）
- 超过重试次数 → 进入死信队列

### 9.4 优先级调度
- 每条消息携带 priority 字段（0-100）
- 当策略为 `priority` 时，消费者优先弹出高优先级消息
- 支持 dashboard 动态调整优先级

### 9.5 时间窗口控制
- 消费者仅在配置时间内工作（默认 9:00-20:00）
- 窗口外消费者进入 idle 状态等待
