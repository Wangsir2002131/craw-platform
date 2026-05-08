# Craw Platform 爬虫调度平台

Craw Platform 是一个面向多模型爬虫任务的调度与执行平台。项目以 MySQL 中的业务任务为入口，将任务拆分为可执行单元，按模型分发到 Redis 队列，再由不同模型消费者执行爬虫并回写结果。

当前支持的模型队列包括：

- `afu`
- `doubao`
- `deepseek`
- `yuanbao`

平台同时提供 FastAPI 接口和简单的后台页面，用于查看任务、队列、账号、日志、告警和运行状态。

## 核心能力

- 从 MySQL 表 `ent_data_product_llm_task` 读取待处理任务。
- 将业务任务拆分为模型执行单元，并写入 `task_master_status` 状态表。
- 通过 Redis 队列分发任务，队列名包括 `queue:afu`、`queue:doubao`、`queue:deepseek`、`queue:yuanbao`。
- 支持普通队列、优先级队列、结果队列和死信队列。
- 消费者执行任务后把结果写入 `queue:results`，失败超过重试次数后进入 `queue:dead-letter`。
- 主服务包含调度循环、API 服务、心跳、健康检查、结果监听等后台线程。
- 提供暂停、恢复、重启标记、优先级调整、队列清空、消费者扩缩容等控制接口。
- 提供 `dashboard.html` 和 `pages/` 下的后台页面。

## 项目结构

```text
craw-platform/
├── afu/                         # AFu 爬虫实现
├── deepseek/                    # DeepSeek 爬虫实现
├── doubao/                      # Doubao 爬虫实现
├── yuanbao/                     # Yuanbao 爬虫实现
├── platform/
│   ├── main_server.py           # 集成主服务入口
│   ├── config.py                # 环境变量与基础配置
│   ├── api/routes/              # FastAPI 路由
│   ├── account/                 # 账号分配与状态管理
│   ├── consumers/               # Redis 消费者与消费者管理
│   ├── dispatcher/              # 任务读取、拆分、调度、结果收集
│   ├── heartbeat/               # 主服务与消费者心跳、健康检查
│   ├── queue/                   # Redis 队列协议、指标、存储
│   ├── store/                   # MySQL 数据访问层
│   └── tasks/                   # 结果监听与任务回写
├── pages/                       # 后台页面
├── scripts/                     # 数据同步、补发队列等辅助脚本
├── tests/                       # 单元测试与压力测试
├── dashboard.html               # 后台首页
├── requirements.txt             # Python 依赖
└── run_*_consumer.py            # 单独启动消费者的脚本
```

## 运行环境

建议环境：

- Python 3.12+
- MySQL 8.x 或兼容版本
- Redis 5+
- Windows / Linux 均可运行

安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install
```

Linux / macOS 激活虚拟环境：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install
```

## 配置说明

配置主要通过环境变量读取，默认值定义在 `platform/config.py`。

| 变量名 | 默认值 | 说明 |
| --- | --- | --- |
| `DB_HOST` | `127.0.0.1` | MySQL 地址 |
| `DB_PORT` | `3306` | MySQL 端口 |
| `DB_USER` | `root` | MySQL 用户名 |
| `DB_PASSWORD` | `root` | MySQL 密码 |
| `DB_NAME` | `geo` | MySQL 数据库名 |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Redis 连接地址 |
| `DISPATCH_INTERVAL` | `5` | 调度循环间隔，单位秒 |
| `BATCH_SIZE` | `100` | 每轮最多读取的业务任务数 |
| `EXECUTE_CRAWLERS` | `0` | 是否由调度器直接执行爬虫，默认只入队 |
| `CONSUMER_MAX_RETRIES` | `3` | 消费失败最大重试次数 |
| `CONSUMER_START_HOUR` | `9` | 消费者允许开始消费的小时 |
| `CONSUMER_END_HOUR` | `20` | 消费者允许结束消费的小时 |
| `PRIORITY_QUEUE_MIN` | `51` | 进入优先级队列的最小优先级 |

PowerShell 示例：

```powershell
$env:DB_HOST="127.0.0.1"
$env:DB_PORT="3306"
$env:DB_USER="root"
$env:DB_PASSWORD="root"
$env:DB_NAME="geo"
$env:REDIS_URL="redis://127.0.0.1:6379/0"
```

## 启动方式

### 启动集成服务

集成服务会同时启动 API、调度循环、心跳、健康检查和结果监听。

```powershell
python -m platform.main_server --forever --host 127.0.0.1 --port 8000
```

访问地址：

- 后台首页：`http://127.0.0.1:8000/`
- 健康检查：`http://127.0.0.1:8000/health`
- API 文档：`http://127.0.0.1:8000/docs`

### 只启动 API

```powershell
python platform.main_server.py --api-only --host 127.0.0.1 --port 8000
```

### 只运行一轮调度

```powershell
python platform/main_server.py --once --limit 100
```

### 只启动调度服务

```powershell
python platform/main_server.py --dispatcher-only --interval 5 --limit 100
```

### 启动内置消费者管理

如果希望由主服务管理各模型消费者，并允许后台页面进行消费者扩缩容：

```powershell
python platform/main_server.py --forever --managed-consumers --default-consumers-per-model 1
```

## 单独启动消费者

每个模型消费者都可以独立启动。

```powershell
python run_afu_consumer.py
python run_doubao_consumer.py
python run_deepseek_consumer.py
python run_yuanbao_consumer.py
```

只消费一条消息后退出：

```powershell
python run_afu_consumer.py --once
```

常用参数：

- `--once`：最多消费一条消息后退出。
- `--timeout`：Redis 阻塞读取超时时间，默认 `5` 秒。
- `--idle-sleep`：空闲轮询间隔，默认 `1.0` 秒。
- `--log-level`：日志级别，例如 `DEBUG`、`INFO`、`WARNING`。

## 主要接口

服务启动后，可通过 `http://127.0.0.1:8000/docs` 查看完整 OpenAPI 文档。

常用接口分组：

- `/tasks`：任务创建、查询、取消。
- `/queues`：队列状态、队列清空、队列统计、消费者扩缩容。
- `/accounts`：账号查询与状态更新。
- `/alerts`：告警配置、告警触发、告警事件。
- `/control`：服务状态、暂停、恢复、重启标记、调度策略、优先级调整。
- `/logs`：日志列表与日志内容读取。
- `/stats`：平台运行汇总统计。

后台页面接口使用 `/api/tasks`、`/api/queues`、`/api/accounts` 等前缀。

## 队列协议

队列定义在 `platform/queue/protocol.py`。

| 类型 | 名称 |
| --- | --- |
| AFu 队列 | `queue:afu` |
| Doubao 队列 | `queue:doubao` |
| DeepSeek 队列 | `queue:deepseek` |
| Yuanbao 队列 | `queue:yuanbao` |
| 结果队列 | `queue:results` |
| 死信队列 | `queue:dead-letter` |

任务消息主要字段：

- `message_type`：固定为 `task`。
- `task_id`：平台内部任务 ID。
- `product_llm_task_id`：业务任务 ID。
- `question_id` / `question_name`：问题标识与名称。
- `queue_name`：目标模型队列。
- `round_num`：执行轮次。
- `priority`：优先级。
- `retry_count`：当前重试次数。

## 数据流

1. 调度器从 MySQL 表 `ent_data_product_llm_task` 读取 `Status='未开始'` 的业务任务。
2. `TaskExpander` 将业务任务拆分为按模型、问题、轮次执行的任务单元。
3. `MasterDispatcher` 写入或复用 `task_master_status` 中的任务状态记录。
4. 调度器把任务消息推送到对应 Redis 队列。
5. 消费者从 Redis 队列取任务，调用对应模型爬虫执行。
6. 消费者把执行结果写入 `queue:results`。
7. `ResultListener` 监听结果队列并回写 MySQL 状态。
8. 健康检查和心跳模块持续记录主服务与消费者状态。

## 测试

运行全部单元测试：

```powershell
python -m unittest discover tests
```

运行指定测试：

```powershell
python -m unittest tests.test_main_server
python -m unittest tests.test_consumer_manager
```

压力测试脚本：

```powershell
python tests/stress_test.py
```

## 日志

主服务日志默认写入 `logs/master_server.log`。同时，部分运行日志可通过 `/logs` 接口或后台页面查看。

## 开发注意事项

- 不要提交 `.venv/`、`.idea/`、`.planning/`、`__pycache__/`、`*.pyc`、运行日志和本地临时文件。
- Redis 和 MySQL 必须先启动，否则调度、队列、心跳和结果监听相关功能会失败。
- 默认 `EXECUTE_CRAWLERS=0`，调度器只负责入队；实际执行需要启动消费者。
- 如果设置 `EXECUTE_CRAWLERS=1`，调度器会直接调用模型爬虫模块，适合兼容或本地验证场景。
- 后台页面只是运维入口，真实接口以 FastAPI 路由为准。

## 常见问题

### 启动后没有任务被分发

检查 MySQL 中是否存在 `Status='未开始'` 的 `ent_data_product_llm_task` 记录，并确认数据库环境变量是否正确。

### 队列有任务但没有执行

确认对应消费者是否启动，例如 `python run_deepseek_consumer.py`。如果使用主服务托管消费者，需要加上 `--managed-consumers`。

### 任务一直失败重试

查看消费者日志、`queue:dead-letter` 死信队列以及 `task_master_status` 中的失败原因字段。

### 后台页面无法访问

确认主服务已启动，并访问 `http://127.0.0.1:8000/`。如果只启动了单独消费者，不会提供后台页面。

