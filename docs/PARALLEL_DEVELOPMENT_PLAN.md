# 多窗口并行开发工作包

> 版本：v1.0
> 生成日期：2026-04-07
> 目的：支持多个 AI 窗口并行开发，每个窗口独立工作，互不冲突

---

## 使用说明

### 如何使用本文档

1. **一个窗口领取一个工作包**：每个工作包独立，无文件冲突
2. **按依赖顺序启动**：先启动 Wave 1，完成后启动 Wave 2，以此类推
3. **完成后标记状态**：在工作包标题标记 `[完成]`
4. **交付物检查**：每个工作包有明确的交付物清单

### 状态标记说明

- `[待开始]` - 未开始
- `[进行中]` - 正在开发
- `[完成]` - 已完成并验证

---

# 阶段 A：统一任务入口

## Wave A-1：基础架构（串行）

---

### 工作包 A-1-1：任务展开器

**状态**：[待开始]

**任务ID**：A-1

**目标**：设计并实现主任务展开逻辑

**你要写的文件**（只动这些）：
```
D:\python\craw-platform\platform\dispatcher\task_expander.py
D:\python\craw-platform\platform\dispatcher\__init__.py
```

**绝对不能碰的文件**：
- 任何现有爬虫文件（afu.py, doubao.py, deepseek.py, yuanbao.py）
- py_main.py
- shared-methods 目录下任何文件

**详细需求**：

1. 创建 `TaskExpander` 类
2. 实现方法 `expand_task(product_llm_task: dict) -> list[dict]`
   - 输入：从 `ent_data_product_llm_task` 表查到的一条任务记录
   - 输出：展开后的问题执行单元列表

3. 问题执行单元数据结构：
```python
{
    "product_llm_task_id": str,
    "question_id": str,
    "question_name": str,
    "round_num": int,
    "queue_name": str,  # 根据LlmKey映射：afu/deepseek/doubao/yuanbao
    "priority": int,     # 默认50
}
```

4. LlmKey 到 QueueName 映射规则：
```python
LLM_KEY_TO_QUEUE = {
    "afu": "queue:afu",
    "deepseek": "queue:deepseek",
    "doubao": "queue:doubao",
    "yuanbao": "queue:yuanbao",
    # 可能有其他别名，需要容错
}
```

**依赖的现有代码**（只读参考）：
- `D:\python\craw-platform\main.py` - 理解现有任务结构
- `D:\python\craw-platform\shared-methods\shared_methods.py` - 使用 DB_CONFIG

**交付物**：
- [ ] `task_expander.py` 文件存在
- [ ] `TaskExpander` 类可导入
- [ ] `expand_task` 方法可调用
- [ ] 包含单元测试代码（在文件末尾）

**验证命令**：
```python
from platform.dispatcher.task_expander import TaskExpander
expander = TaskExpander()
# 测试用例
test_task = {"ProductLlmTaskId": "090a71b5-e9ea-11f0-a151-1c34da64f880", "LlmKey": "afu", "MaxRounds": 3}
result = expander.expand_task(test_task)
print(result)  # 应输出展开后的问题列表
```

---

### 工作包 A-1-2：任务主状态表SQL

**状态**：[待开始]

**任务ID**：A-2

**前置依赖**：A-1-1 完成

**目标**：创建任务主状态表的SQL迁移脚本

**你要写的文件**：
```
D:\python\craw-platform\platform\store\migrations\001_create_task_master_status.sql
D:\python\craw-platform\platform\store\__init__.py
```

**绝对不能碰的文件**：
- 任何 .py 业务文件

**SQL 脚本内容**：
```sql
-- 任务主状态表
CREATE TABLE IF NOT EXISTS task_master_status (
    id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
    product_llm_task_id CHAR(36) NOT NULL COMMENT '产品LLM任务UUID',
    question_id CHAR(36) NOT NULL COMMENT '问题UUID',
    round_num INT NOT NULL COMMENT '轮次号',
    queue_name VARCHAR(32) NOT NULL COMMENT '队列名称',
    execute_status VARCHAR(16) NOT NULL DEFAULT 'pending' COMMENT '执行状态: pending/dispatched/claimed/running/completed/failed',
    account_id VARCHAR(128) NULL COMMENT '执行账号ID',
    server_id VARCHAR(32) NULL COMMENT '执行服务器ID',
    priority INT DEFAULT 50 COMMENT '优先级 0-100',
    dispatched_at DATETIME NULL COMMENT '分发时间',
    claimed_at DATETIME NULL COMMENT '领取时间',
    completed_at DATETIME NULL COMMENT '完成时间',
    fail_reason VARCHAR(255) NULL COMMENT '失败原因',
    retry_count INT DEFAULT 0 COMMENT '重试次数',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    UNIQUE KEY uk_task_execution (product_llm_task_id, question_id, round_num),
    INDEX idx_execute_status (execute_status),
    INDEX idx_queue_name (queue_name),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='任务主状态表';
```

**交付物**：
- [ ] SQL文件存在
- [ ] SQL语法正确（可用 `mysql --help` 验证）
- [ ] 包含注释

---

### 工作包 A-1-3：主队列调度器核心

**状态**：[待开始]

**任务ID**：A-3

**前置依赖**：A-1-1, A-1-2 完成

**目标**：实现主队列调度器核心逻辑

**你要写的文件**：
```
D:\python\craw-platform\platform\dispatcher\master_dispatcher.py
D:\python\craw-platform\platform\store\db_store.py
```

**绝对不能碰的文件**：
- 任何现有爬虫文件
- py_main.py

**详细需求**：

1. 创建 `MasterDispatcher` 类

2. 实现方法：
```python
class MasterDispatcher:
    def __init__(self, db_config: dict):
        """初始化数据库连接"""
        pass

    def fetch_pending_tasks(self, limit: int = 100) -> list[dict]:
        """从ent_data_product_llm_task获取Status='未开始'的任务"""
        pass

    def dispatch_once(self) -> int:
        """执行一次调度：取任务 -> 展开 -> 分发 -> 更新状态"""
        pass

    def run_forever(self, interval: int = 5):
        """持续调度循环"""
        pass
```

3. 数据库操作封装 `db_store.py`：
```python
class TaskMasterStatusStore:
    def create_task_record(self, task_unit: dict) -> int:
        """创建任务记录，返回ID"""
        pass

    def update_status(self, task_id: int, status: str, **kwargs):
        """更新任务状态"""
        pass

    def get_task_by_id(self, task_id: int) -> dict:
        """获取任务详情"""
        pass
```

**依赖的现有代码**（只读参考）：
- `shared_methods.DB_CONFIG` 获取数据库配置
- `database_usage_example.py` 参考数据库操作方式

**交付物**：
- [ ] `master_dispatcher.py` 文件存在
- [ ] `db_store.py` 文件存在
- [ ] `MasterDispatcher` 类可实例化
- [ ] `fetch_pending_tasks` 方法能从数据库查任务

---

### 工作包 A-1-4：主服务入口

**状态**：[待开始]

**任务ID**：A-4

**前置依赖**：A-1-3 完成

**目标**：创建主服务入口，改造原 py_main.py

**你要写的文件**：
```
D:\python\craw-platform\platform\main_server.py
D:\python\craw-platform\platform\config.py
D:\python\craw-platform\platform\__init__.py
```

**要备份的文件**：
```
D:\python\craw-platform\main.py -> D:\python\craw-platform\main.py.backup
```

**绝对不能碰的文件**：
- 任何爬虫文件
- shared-methods 目录

**详细需求**：

1. `config.py` - 配置管理：
```python
import os
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent

# 数据库配置（从环境变量或默认值）
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "your_database"),
}

# 爬虫模块映射
CRAWLER_MODULES = {
    "afu": "afu.afu",
    "doubao": "doubao.doubao",
    "deepseek": "deepseek.deepseek",
    "yuanbao": "yuanbao.yuanbao",
}

# 调度配置
DISPATCH_INTERVAL = 5  # 秒
BATCH_SIZE = 100
```

2. `main_server.py` - 主服务入口：
```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主队列调度服务入口
"""
import sys
import logging
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from platform.config import *
from platform.dispatcher.master_dispatcher import MasterDispatcher

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(PROJECT_ROOT / 'logs' / 'master_server.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

def main():
    logger.info("主队列调度服务启动...")
    dispatcher = MasterDispatcher(DB_CONFIG)
    dispatcher.run_forever(interval=DISPATCH_INTERVAL)

if __name__ == "__main__":
    main()
```

**交付物**：
- [ ] `main_server.py` 文件存在
- [ ] `config.py` 文件存在
- [ ] `py_main.py.backup` 备份存在
- [ ] 能运行 `python platform/main_server.py` 不报错

---

## Wave A-2：爬虫执行器改造（4个窗口并行）

> ⚠️ 以下4个工作包可同时分配给4个AI窗口并行开发

---

### 工作包 A-2-AFU：afu执行器改造

**状态**：[待开始]

**任务ID**：A-5

**前置依赖**：A-1-4 完成

**目标**：改造 afu.py 为执行器模式

**你要写的文件**（只动这一个）：
```
D:\python\craw-platform\afu\afu.py
```

**绝对不能碰的文件**：
- doubao/, deepseek/, yuanbao/ 目录下任何文件
- platform/ 目录下任何文件
- shared-methods/ 目录下任何文件
- py_main.py

**改造要点**：

1. **保留原有功能**：不要删除任何现有逻辑

2. **新增执行器入口函数**：
```python
def execute_task(task_info: dict) -> dict:
    """
    执行器入口函数 - 由主服务调用

    Args:
        task_info: {
            "product_llm_task_id": str,
            "question_id": str,
            "question_name": str,
            "round_num": int,
            "account_info": {  # 可选，后续阶段使用
                "account_id": str,
                "resource_path": str
            }
        }

    Returns:
        {
            "success": bool,
            "answer": str,
            "error": str,  # 失败时
            "account_id": str
        }
    """
    # 实现逻辑：
    # 1. 解析 task_info
    # 2. 选择账号（当前阶段仍使用本地轮换，后续改）
    # 3. 执行爬虫逻辑（复用现有代码）
    # 4. 返回结果
    pass
```

3. **移除数据库轮询逻辑**：
   - 找到原 `main()` 或调度循环代码
   - 注释或删除数据库轮询部分
   - 保留浏览器自动化核心逻辑

4. **保留独立运行能力**：
```python
if __name__ == "__main__":
    # 独立运行模式（用于测试）
    test_task = {
        "product_llm_task_id": "090a71b5-e9ea-11f0-a151-1c34da64f880",
        "question_id": "3f92f9ce-3ebb-11f1-8b90-6018952c5b3e",
        "question_name": "测试问题",
        "round_num": 1
    }
    result = execute_task(test_task)
    print(result)
```

**现有 afu.py 关键结构参考**：
- Profile目录：`D:/afu_real_profiles/account_N`
- 账号锁：`_profiles_in_use`
- 核心执行函数需要找到并复用

**交付物**：
- [ ] `execute_task` 函数存在
- [ ] `execute_task` 能被外部调用
- [ ] 移除了数据库轮询逻辑
- [ ] 保留了浏览器自动化核心逻辑
- [ ] 可以独立运行测试

---

### 工作包 A-2-DOUBAO：doubao执行器改造

**状态**：[待开始]

**任务ID**：A-6

**前置依赖**：A-1-4 完成

**目标**：改造 doubao.py 为执行器模式

**你要写的文件**（只动这一个）：
```
D:\python\craw-platform\doubao\doubao.py
```

**绝对不能碰的文件**：
- afu/, deepseek/, yuanbao/ 目录下任何文件
- platform/ 目录下任何文件
- shared-methods/ 目录下任何文件
- py_main.py

**改造要点**：与 A-2-AFU 完全相同

**现有 doubao.py 关键结构参考**：
- Profile目录：`D:/doubao_real_profiles/account_N`
- 账号锁：`_profiles_in_use`

**交付物**：
- [ ] `execute_task` 函数存在
- [ ] `execute_task` 能被外部调用
- [ ] 移除了数据库轮询逻辑
- [ ] 保留了浏览器自动化核心逻辑

---

### 工作包 A-2-DEEPSEEK：deepseek执行器改造

**状态**：[待开始]

**任务ID**：A-7

**前置依赖**：A-1-4 完成

**目标**：改造 deepseek.py 为执行器模式

**你要写的文件**（只动这一个）：
```
D:\python\craw-platform\deepseek\deepseek.py
```

**绝对不能碰的文件**：
- afu/, doubao/, yuanbao/ 目录下任何文件
- platform/ 目录下任何文件
- shared-methods/ 目录下任何文件
- py_main.py

**改造要点**：

```python
def execute_task(task_info: dict, account_info: dict = None) -> dict:
    """
    执行器入口函数

    Args:
        task_info: {
            "product_llm_task_id": str,
            "question_id": str,
            "question_name": str,
            "round_num": int
        }
        account_info: {
            "account_id": str,
            "cookie_file_path": str  # cookie文件路径
        }

    Returns:
        {
            "success": bool,
            "answer": str,
            "error": str,
            "account_id": str
        }
    """
    pass
```

**现有 deepseek.py 关键结构参考**：
- Cookie文件：`D:/python/craw-platform/deepseek/deepseek_cookie_file/cookies1~31.json`
- Playwright 方式
- ThreadPoolExecutor 并发

**交付物**：
- [ ] `execute_task` 函数存在
- [ ] 支持 `account_info` 参数
- [ ] 移除了数据库轮询和cookie扫描调度逻辑
- [ ] 保留了浏览器自动化核心逻辑

---

### 工作包 A-2-YUANBAO：yuanbao执行器改造

**状态**：[待开始]

**任务ID**：A-8

**前置依赖**：A-1-4 完成

**目标**：改造 yuanbao.py 为执行器模式

**你要写的文件**（只动这一个）：
```
D:\python\craw-platform\yuanbao\yuanbao.py
```

**绝对不能碰的文件**：
- afu/, doubao/, deepseek/ 目录下任何文件
- platform/ 目录下任何文件
- shared-methods/ 目录下任何文件
- py_main.py

**改造要点**：与 A-2-DEEPSEEK 完全相同

**现有 yuanbao.py 关键结构参考**：
- Cookie文件：`D:/python/craw-platform/yuanbao/yuanbao_cookie_file/cookies1~31.json`
- Playwright 方式

**交付物**：
- [ ] `execute_task` 函数存在
- [ ] 支持 `account_info` 参数
- [ ] 移除了数据库轮询和cookie扫描调度逻辑
- [ ] 保留了浏览器自动化核心逻辑

---

## Wave A-3：集成与测试（串行）

---

### 工作包 A-3-1：结果收集与状态更新

**状态**：[待开始]

**任务ID**：A-9

**前置依赖**：A-2-AFU, A-2-DOUBAO, A-2-DEEPSEEK, A-2-YUANBAO 全部完成

**目标**：主服务能调用各爬虫并收集结果

**你要写的文件**：
```
D:\python\craw-platform\platform\dispatcher\result_collector.py
```

**你要修改的文件**：
```
D:\python\craw-platform\platform\dispatcher\master_dispatcher.py
```

**绝对不能碰的文件**：
- 任何爬虫文件（afu, doubao, deepseek, yuanbao）

**详细需求**：

1. `result_collector.py`：
```python
class ResultCollector:
    def __init__(self, db_store: TaskMasterStatusStore):
        self.db_store = db_store

    def collect_result(self, task_id: int, result: dict) -> bool:
        """
        收集执行结果并更新状态

        Args:
            task_id: 任务ID
            result: 爬虫返回的结果

        Returns:
            是否成功更新
        """
        pass

    def write_back_to_business(self, task_unit: dict, result: dict):
        """
        回写业务结果到原业务表
        调用 database_usage_example.py 中的函数
        """
        pass
```

2. 修改 `master_dispatcher.py`：
   - 增加动态导入爬虫模块的逻辑
   - 调用 `execute_task` 并收集结果
   - 调用 `result_collector` 处理结果

**交付物**：
- [ ] `result_collector.py` 文件存在
- [ ] `master_dispatcher.py` 能调用各爬虫
- [ ] 执行结果能正确更新到 `task_master_status`

---

### 工作包 A-3-2：阶段A集成测试

**状态**：[待开始]

**任务ID**：A-10

**前置依赖**：A-3-1 完成

**目标**：完整验证阶段A功能

**你要写的文件**：
```
D:\python\craw-platform\tests\test_phase_a.py
D:\python\craw-platform\tests\__init__.py
```

**测试用例清单**：

```python
import unittest

class TestPhaseA(unittest.TestCase):

    def test_task_expander(self):
        """测试任务展开"""
        pass

    def test_master_dispatcher_fetch(self):
        """测试任务获取"""
        pass

    def test_afu_executor(self):
        """测试afu执行器"""
        pass

    def test_doubao_executor(self):
        """测试doubao执行器"""
        pass

    def test_deepseek_executor(self):
        """测试deepseek执行器"""
        pass

    def test_yuanbao_executor(self):
        """测试yuanbao执行器"""
        pass

    def test_full_flow(self):
        """测试完整流程"""
        pass

if __name__ == "__main__":
    unittest.main()
```

**验收标准**：
- [ ] 主服务能从数据库取任务
- [ ] 各爬虫执行器能接收任务并执行
- [ ] 任务状态能正确回写
- [ ] 各爬虫不再自己轮询数据库

**交付物**：
- [ ] 测试文件存在
- [ ] 所有测试用例通过

---

# 阶段 B：消费队列拆分

## Wave B-1：Redis基础（串行）

---

### 工作包 B-1-1：队列协议设计

**状态**：[待开始]

**任务ID**：B-1

**前置依赖**：阶段A全部完成

**目标**：定义队列消息协议

**你要写的文件**：
```
D:\python\craw-platform\platform\store\queue_protocol.py
```

**详细需求**：

```python
from dataclasses import dataclass
from typing import Literal, Optional, Any
import json
import uuid
from datetime import datetime

# 队列名称常量
QUEUE_NAMES = {
    "afu": "queue:afu",
    "deepseek": "queue:deepseek",
    "doubao": "queue:doubao",
    "yuanbao": "queue:yuanbao",
    "result": "queue:result",
}

# 消息类型
MessageType = Literal["task_dispatch", "task_result", "heartbeat", "control"]

@dataclass
class QueueMessage:
    """队列消息基类"""
    message_type: MessageType
    message_id: str
    timestamp: str
    payload: dict

    @classmethod
    def create(cls, message_type: MessageType, payload: dict) -> "QueueMessage":
        return cls(
            message_type=message_type,
            message_id=str(uuid.uuid4()),
            timestamp=datetime.now().isoformat(),
            payload=payload
        )

    def to_json(self) -> str:
        return json.dumps({
            "message_type": self.message_type,
            "message_id": self.message_id,
            "timestamp": self.timestamp,
            "payload": self.payload
        }, ensure_ascii=False)

    @classmethod
    def from_json(cls, json_str: str) -> "QueueMessage":
        data = json.loads(json_str)
        return cls(**data)


@dataclass
class TaskDispatchPayload:
    """任务分发消息载荷"""
    task_id: int
    product_llm_task_id: int
    question_id: int
    question_name: str
    round_num: int
    queue_name: str
    priority: int
    account_info: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "product_llm_task_id": self.product_llm_task_id,
            "question_id": self.question_id,
            "question_name": self.question_name,
            "round_num": self.round_num,
            "queue_name": self.queue_name,
            "priority": self.priority,
            "account_info": self.account_info
        }


@dataclass
class TaskResultPayload:
    """任务结果消息载荷"""
    task_id: int
    success: bool
    answer: Optional[str] = None
    error: Optional[str] = None
    account_id: Optional[str] = None
    execution_time: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "success": self.success,
            "answer": self.answer,
            "error": self.error,
            "account_id": self.account_id,
            "execution_time": self.execution_time
        }
```

**交付物**：
- [ ] `queue_protocol.py` 文件存在
- [ ] 所有数据类可导入使用
- [ ] JSON序列化/反序列化正常

---

### 工作包 B-1-2：Redis存储层

**状态**：[待开始]

**任务ID**：B-2

**前置依赖**：B-1-1 完成

**目标**：封装Redis操作

**你要写的文件**：
```
D:\python\craw-platform\platform\store\redis_store.py
```

**详细需求**：

```python
import redis
import json
from typing import Optional, List
from .queue_protocol import QueueMessage, MessageType

class RedisStore:
    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        self.client = redis.from_url(redis_url)
        self._test_connection()

    def _test_connection(self):
        """测试连接"""
        try:
            self.client.ping()
        except redis.ConnectionError as e:
            raise ConnectionError(f"无法连接Redis: {e}")

    def push_to_queue(self, queue_name: str, message: QueueMessage, priority: int = None):
        """
        推送消息到队列

        如果指定priority，使用有序集合实现优先级队列
        否则使用列表实现FIFO队列
        """
        pass

    def pop_from_queue(self, queue_name: str, timeout: int = 0) -> Optional[QueueMessage]:
        """
        从队列弹出消息

        Args:
            queue_name: 队列名称
            timeout: 阻塞超时秒数，0表示非阻塞

        Returns:
            消息对象，如果队列为空返回None
        """
        pass

    def get_queue_length(self, queue_name: str) -> int:
        """获取队列长度"""
        pass

    def get_all_queue_lengths(self) -> dict:
        """获取所有队列长度"""
        pass

    def set_heartbeat(self, key: str, value: str, ttl: int = 30):
        """设置心跳键"""
        self.client.setex(key, ttl, value)

    def get_heartbeat(self, key: str) -> Optional[str]:
        """获取心跳值"""
        return self.client.get(key)

    def publish_result(self, result_message: QueueMessage):
        """发布结果到结果队列"""
        self.push_to_queue("queue:result", result_message)
```

**交付物**：
- [ ] `redis_store.py` 文件存在
- [ ] `RedisStore` 类可实例化
- [ ] 能正常连接Redis（假设Redis已运行）
- [ ] `push_to_queue`, `pop_from_queue` 方法可用

---

## Wave B-2：主服务改造与消费者开发（并行）

---

### 工作包 B-2-MASTER：主服务队列发布者改造

**状态**：[待开始]

**任务ID**：B-3

**前置依赖**：B-1-2 完成

**目标**：改造主服务为队列发布者

**你要修改的文件**：
```
D:\python\craw-platform\platform\dispatcher\master_dispatcher.py
D:\python\craw-platform\platform\config.py
```

**绝对不能碰的文件**：
- 任何爬虫文件
- platform/consumers/ 目录（由其他工作包负责）

**改造要点**：

1. 修改 `MasterDispatcher.__init__`：
   - 增加Redis连接
   - 增加是否使用队列的配置项

2. 修改 `dispatch_once`：
   - 不再直接调用爬虫函数
   - 改为调用 `redis_store.push_to_queue`

```python
def dispatch_once(self) -> int:
    tasks = self.fetch_pending_tasks()
    dispatched = 0

    for task in tasks:
        # 展开任务
        task_units = self.expander.expand_task(task)

        for unit in task_units:
            # 创建数据库记录
            task_id = self.db_store.create_task_record(unit)

            # 构造消息
            payload = TaskDispatchPayload(
                task_id=task_id,
                **unit
            ).to_dict()

            message = QueueMessage.create("task_dispatch", payload)

            # 推送到队列
            self.redis_store.push_to_queue(unit["queue_name"], message)

            # 更新状态为dispatched
            self.db_store.update_status(task_id, "dispatched", dispatched_at=datetime.now())

            dispatched += 1

    return dispatched
```

**交付物**：
- [ ] 主服务能推送消息到Redis队列
- [ ] 任务状态更新为 `dispatched`

---

### 工作包 B-2-AFU-CONSUMER：afu消费者

**状态**：[待开始]

**任务ID**：B-4 (afu部分)

**前置依赖**：B-1-2 完成, A-2-AFU 完成

**目标**：创建afu消费者服务

**你要写的文件**：
```
D:\python\craw-platform\platform\consumers\afu_consumer.py
D:\python\craw-platform\platform\consumers\__init__.py
```

**绝对不能碰的文件**：
- afu/afu.py（已由A-2-AFU完成）
- platform/dispatcher/ 目录
- 其他 consumer 文件

**详细需求**：

```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AFu 消费者服务
"""
import sys
import logging
import time
import signal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from platform.store.redis_store import RedisStore
from platform.store.queue_protocol import QueueMessage, TaskResultPayload
from afu.afu import execute_task

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("afu_consumer")

QUEUE_NAME = "queue:afu"
CONSUMER_ID = f"afu_consumer_{int(time.time())}"

running = True

def signal_handler(signum, frame):
    global running
    logger.info(f"收到停止信号 {signum}")
    running = False

def process_message(message: QueueMessage) -> QueueMessage:
    """处理单条消息"""
    payload = message.payload
    task_id = payload["task_id"]

    logger.info(f"开始处理任务 {task_id}")

    start_time = time.time()

    try:
        # 调用执行器
        result = execute_task(payload)

        execution_time = time.time() - start_time

        # 构造结果消息
        result_payload = TaskResultPayload(
            task_id=task_id,
            success=result.get("success", False),
            answer=result.get("answer"),
            error=result.get("error"),
            account_id=result.get("account_id"),
            execution_time=execution_time
        )

        return QueueMessage.create("task_result", result_payload.to_dict())

    except Exception as e:
        logger.error(f"任务 {task_id} 执行异常: {e}")
        result_payload = TaskResultPayload(
            task_id=task_id,
            success=False,
            error=str(e)
        )
        return QueueMessage.create("task_result", result_payload.to_dict())

def main():
    global running

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    redis_store = RedisStore()
    logger.info(f"AFu消费者启动，ID={CONSUMER_ID}")

    while running:
        try:
            # 阻塞获取消息，超时5秒
            message = redis_store.pop_from_queue(QUEUE_NAME, timeout=5)

            if message is None:
                continue

            # 处理消息
            result_message = process_message(message)

            # 发布结果
            redis_store.publish_result(result_message)

            logger.info(f"任务 {message.payload['task_id']} 处理完成")

        except Exception as e:
            logger.error(f"消费者异常: {e}")
            time.sleep(1)

    logger.info("消费者停止")

if __name__ == "__main__":
    main()
```

**交付物**：
- [ ] `afu_consumer.py` 文件存在
- [ ] 能从队列获取消息
- [ ] 能调用 `execute_task` 执行
- [ ] 能发布结果到结果队列

---

### 工作包 B-2-DOUBAO-CONSUMER：doubao消费者

**状态**：[待开始]

**任务ID**：B-4 (doubao部分)

**前置依赖**：B-1-2 完成, A-2-DOUBAO 完成

**目标**：创建doubao消费者服务

**你要写的文件**：
```
D:\python\craw-platform\platform\consumers\doubao_consumer.py
```

**绝对不能碰的文件**：
- doubao/doubao.py
- platform/dispatcher/ 目录
- 其他 consumer 文件

**内容**：与 B-2-AFU-CONSUMER 类似，替换：
- `QUEUE_NAME = "queue:doubao"`
- `from doubao.doubao import execute_task`
- `logger = logging.getLogger("doubao_consumer")`

**交付物**：
- [ ] `doubao_consumer.py` 文件存在
- [ ] 消费者逻辑正常

---

### 工作包 B-2-DEEPSEEK-CONSUMER：deepseek消费者

**状态**：[待开始]

**任务ID**：B-4 (deepseek部分)

**前置依赖**：B-1-2 完成, A-2-DEEPSEEK 完成

**目标**：创建deepseek消费者服务

**你要写的文件**：
```
D:\python\craw-platform\platform\consumers\deepseek_consumer.py
```

**内容**：与 B-2-AFU-CONSUMER 类似，注意 deepseek 的 `execute_task` 需要两个参数

**交付物**：
- [ ] `deepseek_consumer.py` 文件存在

---

### 工作包 B-2-YUANBAO-CONSUMER：yuanbao消费者

**状态**：[待开始]

**任务ID**：B-4 (yuanbao部分)

**前置依赖**：B-1-2 完成, A-2-YUANBAO 完成

**目标**：创建yuanbao消费者服务

**你要写的文件**：
```
D:\python\craw-platform\platform\consumers\yuanbao_consumer.py
```

**交付物**：
- [ ] `yuanbao_consumer.py` 文件存在

---

## Wave B-3：结果监听与时间窗（可并行）

---

### 工作包 B-3-1：结果监听器

**状态**：[待开始]

**任务ID**：B-5

**前置依赖**：B-2 全部完成

**目标**：主服务监听结果队列

**你要写的文件**：
```
D:\python\craw-platform\platform\tasks\result_listener.py
D:\python\craw-platform\platform\tasks\__init__.py
```

**你要修改的文件**：
```
D:\python\craw-platform\platform\dispatcher\master_dispatcher.py
D:\python\craw-platform\platform\main_server.py
```

**详细需求**：

```python
# platform/tasks/result_listener.py
import threading
import logging
import time
from platform.store.redis_store import RedisStore
from platform.store.db_store import TaskMasterStatusStore
from platform.store.queue_protocol import QueueMessage

logger = logging.getLogger("result_listener")

class ResultListener(threading.Thread):
    def __init__(self, redis_store: RedisStore, db_store: TaskMasterStatusStore):
        super().__init__(daemon=True)
        self.redis_store = redis_store
        self.db_store = db_store
        self.running = True

    def run(self):
        logger.info("结果监听器启动")
        while self.running:
            try:
                message = self.redis_store.pop_from_queue("queue:result", timeout=5)
                if message is None:
                    continue

                self.process_result(message)

            except Exception as e:
                logger.error(f"结果处理异常: {e}")
                time.sleep(1)

    def process_result(self, message: QueueMessage):
        payload = message.payload
        task_id = payload["task_id"]
        success = payload["success"]

        logger.info(f"收到任务 {task_id} 结果: {'成功' if success else '失败'}")

        # 更新状态
        if success:
            self.db_store.update_status(
                task_id,
                "completed",
                completed_at=datetime.now()
            )
        else:
            self.db_store.update_status(
                task_id,
                "failed",
                fail_reason=payload.get("error"),
                completed_at=datetime.now()
            )

    def stop(self):
        self.running = False
```

**交付物**：
- [ ] `result_listener.py` 文件存在
- [ ] 结果能正确更新到数据库

---

### 工作包 B-3-2：时间窗控制

**状态**：[待开始]

**任务ID**：B-6

**前置依赖**：B-2-MASTER 完成

**目标**：实现时间窗控制

**你要写的文件**：
```
D:\python\craw-platform\platform\dispatcher\time_window.py
```

**详细需求**：

```python
from datetime import datetime, time

class TimeWindow:
    """时间窗控制器"""

    def __init__(self, start_hour: int = 9, end_hour: int = 20):
        """
        Args:
            start_hour: 开始小时（含）
            end_hour: 结束小时（不含）
        """
        self.start_hour = start_hour
        self.end_hour = end_hour

    def is_in_window(self, dt: datetime = None) -> bool:
        """检查当前时间是否在时间窗内"""
        if dt is None:
            dt = datetime.now()

        current_hour = dt.hour
        return self.start_hour <= current_hour < self.end_hour

    def get_next_window_start(self, dt: datetime = None) -> datetime:
        """获取下一个时间窗开始时间"""
        if dt is None:
            dt = datetime.now()

        if self.is_in_window(dt):
            return dt

        # 如果当前时间早于开始时间，返回今天的开始时间
        if dt.hour < self.start_hour:
            return dt.replace(
                hour=self.start_hour, minute=0, second=0, microsecond=0
            )

        # 否则返回明天的开始时间
        return (dt.replace(hour=self.start_hour, minute=0, second=0, microsecond=0)
                + timedelta(days=1))

    def should_dispatch(self) -> bool:
        """是否应该分发新任务"""
        return self.is_in_window()

    def get_status_message(self) -> str:
        """获取状态消息"""
        if self.is_in_window():
            return f"时间窗开启中（{self.start_hour}:00-{self.end_hour}:00）"
        else:
            next_start = self.get_next_window_start()
            return f"时间窗已关闭，下次开启时间：{next_start.strftime('%Y-%m-%d %H:%M')}"
```

**修改 master_dispatcher.py**：
```python
def run_forever(self, interval: int = 5):
    while True:
        if self.time_window.should_dispatch():
            dispatched = self.dispatch_once()
            logger.info(f"本轮分发 {dispatched} 个任务")
        else:
            logger.info(self.time_window.get_status_message())

        time.sleep(interval)
```

**交付物**：
- [ ] `time_window.py` 文件存在
- [ ] 主服务在20:00后停止分发新任务

---

## Wave B-4：集成测试

---

### 工作包 B-4-1：阶段B集成测试

**状态**：[待开始]

**任务ID**：B-7

**前置依赖**：B-3 全部完成

**目标**：验证阶段B完整功能

**你要写的文件**：
```
D:\python\craw-platform\tests\test_phase_b.py
```

**验收标准**：
- [ ] 完整链路跑通：数据库 → 主队列 → 消费队列 → 爬虫执行 → 结果回报 → 状态更新
- [ ] 时间窗控制生效

---

# 阶段 C：账号主表接管

## Wave C-1：账号数据模型（串行）

---

### 工作包 C-1-1：账号数据模型SQL

**状态**：[待开始]

**任务ID**：C-1

**前置依赖**：阶段B全部完成

**目标**：创建账号相关表

**你要写的文件**：
```
D:\python\craw-platform\platform\store\migrations\002_create_crawler_accounts.sql
```

**SQL内容**：
```sql
-- 账号主表
CREATE TABLE IF NOT EXISTS crawler_accounts (
    id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
    account_id VARCHAR(128) NOT NULL COMMENT '账号唯一标识',
    crawler VARCHAR(32) NOT NULL COMMENT '所属爬虫: afu/doubao/deepseek/yuanbao',
    account_type VARCHAR(16) NOT NULL DEFAULT 'primary' COMMENT '账号类型: primary/backup',
    status VARCHAR(16) NOT NULL DEFAULT 'normal' COMMENT '状态: normal/running/suspicious/abnormal/disabled',
    resource_type VARCHAR(16) NOT NULL COMMENT '资源类型: profile/cookie',
    resource_path VARCHAR(512) NOT NULL COMMENT '资源路径',
    server_id VARCHAR(32) NULL COMMENT '绑定服务器ID',
    fail_count INT DEFAULT 0 COMMENT '连续失败次数',
    success_count INT DEFAULT 0 COMMENT '连续成功次数',
    last_success_at DATETIME NULL COMMENT '最后成功时间',
    last_fail_at DATETIME NULL COMMENT '最后失败时间',
    last_fail_reason VARCHAR(255) NULL COMMENT '最后失败原因',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_account_id (account_id),
    INDEX idx_crawler_status (crawler, status),
    INDEX idx_server_id (server_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='爬虫账号表';

-- 账号事件表
CREATE TABLE IF NOT EXISTS crawler_account_events (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    account_id VARCHAR(128) NOT NULL COMMENT '账号ID',
    event_type VARCHAR(32) NOT NULL COMMENT '事件类型',
    old_status VARCHAR(16) NULL COMMENT '旧状态',
    new_status VARCHAR(16) NOT NULL COMMENT '新状态',
    reason VARCHAR(255) NULL COMMENT '原因',
    task_id BIGINT NULL COMMENT '关联任务ID',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_account_id (account_id),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='账号状态事件表';
```

**交付物**：
- [ ] SQL文件存在
- [ ] SQL语法正确

---

### 工作包 C-1-2：账号资源登记脚本

**状态**：[待开始]

**任务ID**：C-2

**前置依赖**：C-1-1 完成

**目标**：扫描现有资源并登记入库

**你要写的文件**：
```
D:\python\craw-platform\platform\accounts\account_initializer.py
D:\python\craw-platform\platform\accounts\__init__.py
```

**详细需求**：

```python
import os
import glob
from pathlib import Path
from typing import List
import pymysql

class AccountInitializer:
    """账号资源登记器"""

    # 资源路径配置
    RESOURCE_CONFIGS = {
        "afu": {
            "resource_type": "profile",
            "pattern": "D:/afu_real_profiles/account_*",
            "parser": lambda p: os.path.basename(p)  # account_1 -> account_1
        },
        "doubao": {
            "resource_type": "profile",
            "pattern": "D:/doubao_real_profiles/account_*",
            "parser": lambda p: os.path.basename(p)
        },
        "deepseek": {
            "resource_type": "cookie",
            "pattern": "D:/python/craw-platform/deepseek/deepseek_cookie_file/cookies*.json",
            "parser": lambda p: os.path.basename(p).replace(".json", "")  # cookies1 -> cookies1
        },
        "yuanbao": {
            "resource_type": "cookie",
            "pattern": "D:/python/craw-platform/yuanbao/yuanbao_cookie_file/cookies*.json",
            "parser": lambda p: os.path.basename(p).replace(".json", "")
        }
    }

    def __init__(self, db_config: dict):
        self.db_config = db_config
        self.conn = None

    def connect(self):
        self.conn = pymysql.connect(**self.db_config)

    def scan_resources(self, crawler: str) -> List[dict]:
        """扫描指定爬虫的账号资源"""
        config = self.RESOURCE_CONFIGS[crawler]
        pattern = config["pattern"]
        parser = config["parser"]

        accounts = []
        for path in glob.glob(pattern):
            account_id = parser(path)
            accounts.append({
                "account_id": f"{crawler}_{account_id}",
                "crawler": crawler,
                "account_type": "primary",  # 默认为主账号
                "status": "normal",
                "resource_type": config["resource_type"],
                "resource_path": path
            })

        return accounts

    def register_account(self, account: dict):
        """登记单个账号"""
        sql = """
        INSERT INTO crawler_accounts
        (account_id, crawler, account_type, status, resource_type, resource_path)
        VALUES (%(account_id)s, %(crawler)s, %(account_type)s, %(status)s, %(resource_type)s, %(resource_path)s)
        ON DUPLICATE KEY UPDATE
        resource_path = %(resource_path)s,
        status = %(status)s
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, account)
        self.conn.commit()

    def run(self):
        """执行扫描登记"""
        self.connect()

        total = 0
        for crawler in self.RESOURCE_CONFIGS.keys():
            accounts = self.scan_resources(crawler)
            for account in accounts:
                self.register_account(account)
                print(f"已登记: {account['account_id']}")
                total += 1

        print(f"\n总计登记 {total} 个账号")
        self.conn.close()

if __name__ == "__main__":
    from platform.config import DB_CONFIG
    initializer = AccountInitializer(DB_CONFIG)
    initializer.run()
```

**交付物**：
- [ ] `account_initializer.py` 文件存在
- [ ] 能扫描并登记所有账号资源

---

## Wave C-2：账号分配器与状态机（可并行）

---

### 工作包 C-2-1：账号分配器

**状态**：[待开始]

**任务ID**：C-3

**前置依赖**：C-1-1, C-1-2 完成

**目标**：实现账号分配逻辑

**你要写的文件**：
```
D:\python\craw-platform\platform\accounts\account_allocator.py
```

**详细需求**：

```python
import random
import pymysql
from datetime import datetime
from typing import Optional, Dict

class AccountAllocator:
    """账号分配器"""

    def __init__(self, db_config: dict, server_id: str = None):
        self.db_config = db_config
        self.server_id = server_id  # 当前服务器ID

    def _get_connection(self):
        return pymysql.connect(**self.db_config)

    def allocate(self, crawler: str) -> Optional[Dict]:
        """
        分配一个可用账号

        Args:
            crawler: 爬虫名称

        Returns:
            账号信息字典，如果没有可用账号返回None
        """
        conn = self._get_connection()
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                # 查询可用账号
                sql = """
                SELECT * FROM crawler_accounts
                WHERE crawler = %s
                AND account_type = 'primary'
                AND status IN ('normal', 'running')
                AND (server_id IS NULL OR server_id = %s)
                ORDER BY RAND()
                LIMIT 1
                FOR UPDATE
                """
                cursor.execute(sql, (crawler, self.server_id))
                account = cursor.fetchone()

                if account is None:
                    # 尝试使用备用账号
                    return self._allocate_backup(conn, crawler)

                # 更新状态为running
                update_sql = """
                UPDATE crawler_accounts
                SET status = 'running', server_id = %s
                WHERE id = %s
                """
                cursor.execute(update_sql, (self.server_id, account['id']))
                conn.commit()

                return account

        finally:
            conn.close()

    def _allocate_backup(self, conn, crawler: str) -> Optional[Dict]:
        """分配备用账号"""
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            sql = """
            SELECT * FROM crawler_accounts
            WHERE crawler = %s
            AND account_type = 'backup'
            AND status = 'normal'
            ORDER BY RAND()
            LIMIT 1
            FOR UPDATE
            """
            cursor.execute(sql, (crawler,))
            account = cursor.fetchone()

            if account is None:
                return None

            # 更新状态
            update_sql = """
            UPDATE crawler_accounts
            SET status = 'running', server_id = %s
            WHERE id = %s
            """
            cursor.execute(update_sql, (self.server_id, account['id']))

            # 记录事件
            self._record_event(conn, account['account_id'], 'switch_backup', None, 'running', '主账号池耗尽，启用备用账号')

            conn.commit()
            return account

    def release(self, account_id: str, success: bool = True):
        """释放账号"""
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                if success:
                    sql = """
                    UPDATE crawler_accounts
                    SET status = 'normal', fail_count = 0, success_count = success_count + 1,
                        last_success_at = %s
                    WHERE account_id = %s
                    """
                    cursor.execute(sql, (datetime.now(), account_id))
                else:
                    sql = """
                    UPDATE crawler_accounts
                    SET status = 'normal', fail_count = fail_count + 1, success_count = 0,
                        last_fail_at = %s
                    WHERE account_id = %s
                    """
                    cursor.execute(sql, (datetime.now(), account_id))

                conn.commit()
        finally:
            conn.close()

    def _record_event(self, conn, account_id: str, event_type: str, old_status: str, new_status: str, reason: str = None):
        """记录账号事件"""
        with conn.cursor() as cursor:
            sql = """
            INSERT INTO crawler_account_events
            (account_id, event_type, old_status, new_status, reason)
            VALUES (%s, %s, %s, %s, %s)
            """
            cursor.execute(sql, (account_id, event_type, old_status, new_status, reason))
```

**交付物**：
- [ ] `account_allocator.py` 文件存在
- [ ] `allocate` 方法能正确分配账号
- [ ] `release` 方法能释放账号

---

### 工作包 C-2-2：账号状态机

**状态**：[待开始]

**任务ID**：C-4

**前置依赖**：C-1-1 完成

**目标**：实现账号状态流转逻辑

**你要写的文件**：
```
D:\python\craw-platform\platform\accounts\account_state_machine.py
```

**详细需求**：

```python
from datetime import datetime
import pymysql

class AccountStateMachine:
    """账号状态机"""

    # 状态流转阈值
    FAIL_THRESHOLD_SUSPICIOUS = 1   # 第1次失败 -> suspicious
    FAIL_THRESHOLD_ABNORMAL = 2     # 第2次失败 -> abnormal
    FAIL_THRESHOLD_DISABLED = 3     # 第3次失败 -> disabled

    # 状态枚举
    STATUS_NORMAL = 'normal'
    STATUS_RUNNING = 'running'
    STATUS_SUSPICIOUS = 'suspicious'
    STATUS_ABNORMAL = 'abnormal'
    STATUS_DISABLED = 'disabled'

    def __init__(self, db_config: dict):
        self.db_config = db_config

    def _get_connection(self):
        return pymysql.connect(**self.db_config)

    def handle_success(self, account_id: str) -> str:
        """
        处理成功：重置失败计数，状态恢复为normal

        Returns:
            新状态
        """
        conn = self._get_connection()
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                # 获取当前状态
                cursor.execute(
                    "SELECT status FROM crawler_accounts WHERE account_id = %s FOR UPDATE",
                    (account_id,)
                )
                row = cursor.fetchone()
                old_status = row['status'] if row else None

                # 更新状态
                cursor.execute("""
                    UPDATE crawler_accounts
                    SET status = 'normal', fail_count = 0, success_count = success_count + 1,
                        last_success_at = %s
                    WHERE account_id = %s
                """, (datetime.now(), account_id))

                # 记录事件
                self._record_event(conn, account_id, 'success', old_status, 'normal', '任务执行成功')

                conn.commit()
                return 'normal'

        finally:
            conn.close()

    def handle_fail(self, account_id: str, reason: str = None, task_id: int = None) -> str:
        """
        处理失败：根据失败次数更新状态

        Returns:
            新状态
        """
        conn = self._get_connection()
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                # 获取当前状态和失败次数
                cursor.execute(
                    "SELECT status, fail_count FROM crawler_accounts WHERE account_id = %s FOR UPDATE",
                    (account_id,)
                )
                row = cursor.fetchone()
                if not row:
                    return None

                old_status = row['status']
                fail_count = row['fail_count'] + 1

                # 计算新状态
                new_status = self._calculate_status(old_status, fail_count)

                # 更新数据库
                cursor.execute("""
                    UPDATE crawler_accounts
                    SET status = %s, fail_count = %s, success_count = 0,
                        last_fail_at = %s, last_fail_reason = %s
                    WHERE account_id = %s
                """, (new_status, fail_count, datetime.now(), reason, account_id))

                # 记录事件
                self._record_event(
                    conn, account_id, 'fail', old_status, new_status, reason, task_id
                )

                conn.commit()
                return new_status

        finally:
            conn.close()

    def _calculate_status(self, current_status: str, fail_count: int) -> str:
        """计算新状态"""
        if fail_count >= self.FAIL_THRESHOLD_DISABLED:
            return self.STATUS_DISABLED
        elif fail_count >= self.FAIL_THRESHOLD_ABNORMAL:
            return self.STATUS_ABNORMAL
        elif fail_count >= self.FAIL_THRESHOLD_SUSPICIOUS:
            return self.STATUS_SUSPICIOUS
        else:
            return current_status

    def recover(self, account_id: str) -> str:
        """
        手动恢复账号为normal状态

        Returns:
            新状态
        """
        return self.handle_success(account_id)

    def disable(self, account_id: str, reason: str = None) -> str:
        """
        手动停用账号

        Returns:
            新状态
        """
        conn = self._get_connection()
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(
                    "SELECT status FROM crawler_accounts WHERE account_id = %s FOR UPDATE",
                    (account_id,)
                )
                row = cursor.fetchone()
                old_status = row['status'] if row else None

                cursor.execute(
                    "UPDATE crawler_accounts SET status = 'disabled' WHERE account_id = %s",
                    (account_id,)
                )

                self._record_event(conn, account_id, 'manual_disable', old_status, 'disabled', reason)

                conn.commit()
                return 'disabled'

        finally:
            conn.close()

    def _record_event(self, conn, account_id: str, event_type: str, old_status: str,
                      new_status: str, reason: str = None, task_id: int = None):
        """记录账号事件"""
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO crawler_account_events
                (account_id, event_type, old_status, new_status, reason, task_id)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (account_id, event_type, old_status, new_status, reason, task_id))
```

**交付物**：
- [ ] `account_state_machine.py` 文件存在
- [ ] `handle_success` 方法正确
- [ ] `handle_fail` 方法能正确流转状态

---

## Wave C-3：爬虫账号表集成（4个窗口并行）

---

### 工作包 C-3-AFU：afu使用账号表

**状态**：[待开始]

**任务ID**：C-5

**前置依赖**：C-2-1, C-2-2 完成

**目标**：改造afu使用账号表

**你要修改的文件**（只动这一个）：
```
D:\python\craw-platform\afu\afu.py
```

**改造要点**：

修改 `execute_task` 函数：

```python
def execute_task(task_info: dict) -> dict:
    """
    执行器入口函数
    """
    # 导入账号模块
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))

    from platform.accounts.account_allocator import AccountAllocator
    from platform.accounts.account_state_machine import AccountStateMachine
    from platform.config import DB_CONFIG

    allocator = AccountAllocator(DB_CONFIG)
    state_machine = AccountStateMachine(DB_CONFIG)

    # 分配账号
    account = allocator.allocate('afu')
    if account is None:
        return {
            "success": False,
            "error": "无可用账号"
        }

    account_id = account['account_id']
    profile_path = account['resource_path']

    try:
        # 使用指定profile执行爬虫逻辑
        # ... 原有执行逻辑 ...

        # 成功
        state_machine.handle_success(account_id)
        return {
            "success": True,
            "answer": answer,
            "account_id": account_id
        }

    except Exception as e:
        # 失败
        state_machine.handle_fail(account_id, str(e))
        return {
            "success": False,
            "error": str(e),
            "account_id": account_id
        }

    finally:
        allocator.release(account_id)
```

**交付物**：
- [ ] `execute_task` 使用账号表分配账号
- [ ] 成功时调用 `handle_success`
- [ ] 失败时调用 `handle_fail`

---

### 工作包 C-3-DOUBAO：doubao使用账号表

**状态**：[待开始]

**任务ID**：C-6

**前置依赖**：C-2-1, C-2-2 完成

**目标**：改造doubao使用账号表

**你要修改的文件**：
```
D:\python\craw-platform\doubao\doubao.py
```

**改造要点**：与 C-3-AFU 相同

**交付物**：
- [ ] 账号表集成完成

---

### 工作包 C-3-DEEPSEEK：deepseek使用账号表

**状态**：[待开始]

**任务ID**：C-7

**前置依赖**：C-2-1, C-2-2 完成

**目标**：改造deepseek使用账号表

**你要修改的文件**：
```
D:\python\craw-platform\deepseek\deepseek.py
```

**改造要点**：与 C-3-AFU 类似，注意 cookie 文件路径

**交付物**：
- [ ] 账号表集成完成

---

### 工作包 C-3-YUANBAO：yuanbao使用账号表

**状态**：[待开始]

**任务ID**：C-8

**前置依赖**：C-2-1, C-2-2 完成

**目标**：改造yuanbao使用账号表

**你要修改的文件**：
```
D:\python\craw-platform\yuanbao\yuanbao.py
```

**交付物**：
- [ ] 账号表集成完成

---

## Wave C-4：备用账号与测试

---

### 工作包 C-4-1：备用账号接管逻辑

**状态**：[待开始]

**任务ID**：C-9

**前置依赖**：C-2-1 完成

**目标**：完善备用账号接管逻辑

**你要修改的文件**：
```
D:\python\craw-platform\platform\accounts\account_allocator.py
```

**交付物**：
- [ ] 主账号池耗尽时能自动切换备用账号

---

### 工作包 C-4-2：阶段C集成测试

**状态**：[待开始]

**任务ID**：C-10

**前置依赖**：C-3 全部完成

**目标**：验证阶段C完整功能

**你要写的文件**：
```
D:\python\craw-platform\tests\test_phase_c.py
```

**验收标准**：
- [ ] 账号状态持久化到数据库
- [ ] 失败次数跨进程正确累计
- [ ] 停用账号不再分配
- [ ] 备用账号按规则启用

---

# 阶段 D：调度策略平台化

## Wave D-1：策略引擎（串行）

---

### 工作包 D-1-1：优先级字段

**状态**：[待开始]

**任务ID**：D-1

**前置依赖**：阶段C完成

**目标**：添加优先级字段

**你要写的文件**：
```
D:\python\craw-platform\platform\store\migrations\003_add_priority_field.sql
```

**SQL内容**：
```sql
ALTER TABLE task_master_status
ADD COLUMN priority INT DEFAULT 50 COMMENT '优先级 0-100' AFTER execute_status;

CREATE INDEX idx_priority ON task_master_status(priority);
```

**交付物**：
- [ ] SQL文件存在

---

### 工作包 D-1-2：调度策略引擎

**状态**：[待开始]

**任务ID**：D-2

**前置依赖**：D-1-1 完成

**目标**：实现调度策略引擎

**你要写的文件**：
```
D:\python\craw-platform\platform\dispatcher\schedule_policy.py
```

**详细需求**：

```python
from enum import Enum
from typing import List, Dict

class SchedulePolicyType(Enum):
    FIFO = "fifo"
    LIFO = "lifo"
    PRIORITY_FIFO = "priority_fifo"
    PRIORITY_LIFO = "priority_lifo"

class SchedulePolicy:
    """调度策略引擎"""

    def __init__(self, policy_type: str = "priority_fifo"):
        self.policy_type = SchedulePolicyType(policy_type)

    def sort_tasks(self, tasks: List[Dict]) -> List[Dict]:
        """按策略排序任务列表"""
        if self.policy_type == SchedulePolicyType.FIFO:
            return sorted(tasks, key=lambda t: t.get('created_at', ''))

        elif self.policy_type == SchedulePolicyType.LIFO:
            return sorted(tasks, key=lambda t: t.get('created_at', ''), reverse=True)

        elif self.policy_type == SchedulePolicyType.PRIORITY_FIFO:
            return sorted(tasks, key=lambda t: (-t.get('priority', 50), t.get('created_at', '')))

        elif self.policy_type == SchedulePolicyType.PRIORITY_LIFO:
            return sorted(tasks, key=lambda t: (-t.get('priority', 50), t.get('created_at', '')), reverse=False)

        return tasks

    def get_order_by_sql(self) -> str:
        """获取SQL ORDER BY子句"""
        if self.policy_type == SchedulePolicyType.FIFO:
            return "created_at ASC"

        elif self.policy_type == SchedulePolicyType.LIFO:
            return "created_at DESC"

        elif self.policy_type == SchedulePolicyType.PRIORITY_FIFO:
            return "priority DESC, created_at ASC"

        elif self.policy_type == SchedulePolicyType.PRIORITY_LIFO:
            return "priority DESC, created_at DESC"

        return "created_at ASC"
```

**交付物**：
- [ ] `schedule_policy.py` 文件存在
- [ ] 支持四种策略

---

### 工作包 D-1-3：主队列排序改造

**状态**：[待开始]

**任务ID**：D-3

**前置依赖**：D-1-2 完成

**目标**：改造主队列使用策略排序

**你要修改的文件**：
```
D:\python\craw-platform\platform\dispatcher\master_dispatcher.py
```

**交付物**：
- [ ] 任务按策略排序

---

### 工作包 D-1-4：策略配置持久化

**状态**：[待开始]

**任务ID**：D-4

**前置依赖**：D-1-3 完成

**目标**：实现策略配置存储

**你要写的文件**：
```
D:\python\craw-platform\platform\store\migrations\004_create_platform_config.sql
```

**SQL内容**：
```sql
CREATE TABLE IF NOT EXISTS platform_config (
    config_key VARCHAR(64) PRIMARY KEY,
    config_value TEXT NOT NULL,
    description VARCHAR(255) NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

INSERT INTO platform_config (config_key, config_value, description)
VALUES ('schedule_policy', 'priority_fifo', '调度策略')
ON DUPLICATE KEY UPDATE config_value = 'priority_fifo';
```

**交付物**：
- [ ] SQL文件存在
- [ ] 主服务能读取配置

---

### 工作包 D-1-5：阶段D测试

**状态**：[待开始]

**任务ID**：D-5

**前置依赖**：D-1-4 完成

**目标**：验证阶段D功能

**你要写的文件**：
```
D:\python\craw-platform\tests\test_phase_d.py
```

---

# 阶段 E：平台能力增强

## Wave E-1：API接口（可并行）

---

### 工作包 E-1-1：FastAPI基础框架

**状态**：[待开始]

**任务ID**：E-1

**前置依赖**：阶段D完成

**目标**：搭建FastAPI基础框架

**你要写的文件**：
```
D:\python\craw-platform\platform\api\__init__.py
D:\python\craw-platform\platform\api\app.py
D:\python\craw-platform\platform\api\routers\__init__.py
```

**详细需求**：

```python
# platform/api/app.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="爬虫平台管理API",
    description="爬虫任务调度、账号管理、监控告警API",
    version="1.0.0"
)

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
from .routers import tasks, queues, accounts, alerts, control, logs, stats

app.include_router(tasks.router, prefix="/api/tasks", tags=["任务管理"])
app.include_router(queues.router, prefix="/api/queues", tags=["队列监控"])
app.include_router(accounts.router, prefix="/api/accounts", tags=["账号管理"])
app.include_router(alerts.router, prefix="/api/alerts", tags=["告警管理"])
app.include_router(control.router, prefix="/api/control", tags=["控制操作"])
app.include_router(logs.router, prefix="/api/logs", tags=["日志查询"])
app.include_router(stats.router, prefix="/api/stats", tags=["统计信息"])

@app.get("/health")
async def health_check():
    return {"status": "healthy"}
```

**交付物**：
- [ ] FastAPI应用可启动
- [ ] `/health` 接口可访问

---

### 工作包 E-1-2：任务API

**状态**：[待开始]

**任务ID**：E-1 (tasks部分)

**前置依赖**：E-1-1 完成

**目标**：实现任务管理API

**你要写的文件**：
```
D:\python\craw-platform\platform\api\routers\tasks.py
```

**接口列表**：
- `GET /api/tasks` - 任务列表（分页、状态过滤）
- `GET /api/tasks/{id}` - 任务详情

**交付物**：
- [ ] 任务API可访问

---

### 工作包 E-1-3：队列API

**状态**：[待开始]

**任务ID**：E-2

**前置依赖**：E-1-1 完成

**目标**：实现队列监控API

**你要写的文件**：
```
D:\python\craw-platform\platform\api\routers\queues.py
```

**交付物**：
- [ ] 队列API可访问

---

### 工作包 E-1-4：账号API

**状态**：[待开始]

**任务ID**：E-3

**前置依赖**：E-1-1 完成

**目标**：实现账号管理API

**你要写的文件**：
```
D:\python\craw-platform\platform\api\routers\accounts.py
```

**交付物**：
- [ ] 账号API可访问

---

### 工作包 E-1-5：告警API

**状态**：[待开始]

**任务ID**：E-4

**前置依赖**：E-1-1 完成

**目标**：实现告警API和钉钉集成

**你要写的文件**：
```
D:\python\craw-platform\platform\api\routers\alerts.py
D:\python\craw-platform\platform\alerts\alert_engine.py
D:\python\craw-platform\platform\alerts\__init__.py
```

**交付物**：
- [ ] 告警API可访问
- [ ] 钉钉告警能发送

---

### 工作包 E-1-6：控制API

**状态**：[待开始]

**任务ID**：E-5

**前置依赖**：E-1-1 完成

**目标**：实现控制操作API

**你要写的文件**：
```
D:\python\craw-platform\platform\api\routers\control.py
```

**接口列表**：
- `POST /api/control/pause` - 暂停分发
- `POST /api/control/resume` - 恢复分发
- `POST /api/control/schedule-policy` - 修改调度策略

**交付物**：
- [ ] 控制API可访问

---

### 工作包 E-1-7：日志API

**状态**：[待开始]

**任务ID**：E-9

**前置依赖**：E-1-1 完成

**目标**：实现日志查询API

**你要写的文件**：
```
D:\python\craw-platform\platform\api\routers\logs.py
```

**交付物**：
- [ ] 日志API可访问

---

### 工作包 E-1-8：统计API

**状态**：[待开始]

**前置依赖**：E-1-1 完成

**目标**：实现统计信息API

**你要写的文件**：
```
D:\python\craw-platform\platform\api\routers\stats.py
```

**交付物**：
- [ ] 统计API可访问

---

## Wave E-2：心跳与健康检查（可并行）

---

### 工作包 E-2-1：主服务心跳

**状态**：[待开始]

**任务ID**：E-6

**前置依赖**：E-1-1 完成

**目标**：实现主服务心跳

**你要修改的文件**：
```
D:\python\craw-platform\platform\main_server.py
D:\python\craw-platform\platform\api\app.py
```

**交付物**：
- [ ] 心跳写入Redis
- [ ] `/health` 返回完整状态

---

### 工作包 E-2-2：消费者心跳

**状态**：[待开始]

**任务ID**：E-7

**前置依赖**：B-2 消费者完成

**目标**：实现消费者心跳上报

**你要修改的文件**：
```
D:\python\craw-platform\platform\consumers\afu_consumer.py
D:\python\craw-platform\platform\consumers\doubao_consumer.py
D:\python\craw-platform\platform\consumers\deepseek_consumer.py
D:\python\craw-platform\platform\consumers\yuanbao_consumer.py
```

**交付物**：
- [ ] 所有消费者上报心跳

---

### 工作包 E-2-3：消费者失联检测

**状态**：[待开始]

**任务ID**：E-8

**前置依赖**：E-2-1, E-2-2 完成

**目标**：实现消费者失联检测

**你要修改的文件**：
```
D:\python\craw-platform\platform\alerts\alert_engine.py
```

**交付物**：
- [ ] 失联检测正常
- [ ] 触发告警

---

## Wave E-3：整合与部署

---

### 工作包 E-3-1：主服务整合

**状态**：[待开始]

**任务ID**：E-10

**前置依赖**：Wave E-1, E-2 完成

**目标**：整合所有模块

**你要写的文件**：
```
D:\python\craw-platform\platform\main.py
```

**交付物**：
- [ ] 统一启动入口

---

### 工作包 E-3-2：启动脚本与部署文档

**状态**：[待开始]

**任务ID**：E-11

**前置依赖**：E-3-1 完成

**目标**：编写部署文档和启动脚本

**你要写的文件**：
```
D:\python\craw-platform\scripts\start_master.bat
D:\python\craw-platform\scripts\start_afu_consumer.bat
D:\python\craw-platform\scripts\start_doubao_consumer.bat
D:\python\craw-platform\scripts\start_deepseek_consumer.bat
D:\python\craw-platform\scripts\start_yuanbao_consumer.bat
D:\python\craw-platform\scripts\start_api.bat
D:\python\craw-platform\requirements.txt
D:\python\craw-platform\.env.example
D:\python\craw-platform\docs\deployment.md
```

**交付物**：
- [ ] 所有启动脚本存在
- [ ] 部署文档完整

---

### 工作包 E-3-3：全链路压测

**状态**：[待开始]

**任务ID**：E-12

**前置依赖**：E-3-2 完成

**目标**：压测验证

**验收标准**：
- [ ] 系统稳定运行1小时无异常
- [ ] 无任务丢失或重复

---

### 工作包 E-3-4：用户操作手册

**状态**：[待开始]

**任务ID**：E-13

**前置依赖**：E-3-3 完成

**目标**：编写用户手册

**你要写的文件**：
```
D:\python\craw-platform\docs\user_manual.md
```

---

# 附录：并行开发矩阵

## 阶段 A 并行窗口安排

| Wave | 工作包 | 可并行数 | 说明 |
|------|--------|---------|------|
| A-1 | A-1-1, A-1-2, A-1-3, A-1-4 | 1 (串行) | 基础架构依赖关系 |
| A-2 | A-2-AFU, A-2-DOUBAO, A-2-DEEPSEEK, A-2-YUANBAO | **4** | 四个爬虫独立改造 |
| A-3 | A-3-1, A-3-2 | 1 (串行) | 集成依赖 |

## 阶段 B 并行窗口安排

| Wave | 工作包 | 可并行数 | 说明 |
|------|--------|---------|------|
| B-1 | B-1-1, B-1-2 | 1 (串行) | Redis基础依赖 |
| B-2 | B-2-MASTER, B-2-AFU-CONSUMER, B-2-DOUBAO-CONSUMER, B-2-DEEPSEEK-CONSUMER, B-2-YUANBAO-CONSUMER | **5** | 主服务和消费者可并行 |
| B-3 | B-3-1, B-3-2 | **2** | 结果监听和时间窗可并行 |

## 阶段 C 并行窗口安排

| Wave | 工作包 | 可并行数 | 说明 |
|------|--------|---------|------|
| C-1 | C-1-1, C-1-2 | 1 (串行) | 数据模型依赖 |
| C-2 | C-2-1, C-2-2 | **2** | 分配器和状态机可并行 |
| C-3 | C-3-AFU, C-3-DOUBAO, C-3-DEEPSEEK, C-3-YUANBAO | **4** | 四个爬虫独立改造 |

## 阶段 E 并行窗口安排

| Wave | 工作包 | 可并行数 | 说明 |
|------|--------|---------|------|
| E-1 | E-1-1 ~ E-1-8 | **8** | 所有API可并行开发 |
| E-2 | E-2-1, E-2-2, E-2-3 | **3** | 心跳相关可并行 |

---

# 快速启动指南

## 阶段 A 启动顺序

```
Day 1: Wave A-1 (串行)
  窗口1: A-1-1 → A-1-2 → A-1-3 → A-1-4

Day 2-3: Wave A-2 (4窗口并行)
  窗口1: A-2-AFU
  窗口2: A-2-DOUBAO
  窗口3: A-2-DEEPSEEK
  窗口4: A-2-YUANBAO

Day 4: Wave A-3 (串行)
  窗口1: A-3-1 → A-3-2
```

## 阶段 B 启动顺序

```
Day 5: Wave B-1 (串行)
  窗口1: B-1-1 → B-1-2

Day 6-7: Wave B-2 (5窗口并行)
  窗口1: B-2-MASTER
  窗口2: B-2-AFU-CONSUMER
  窗口3: B-2-DOUBAO-CONSUMER
  窗口4: B-2-DEEPSEEK-CONSUMER
  窗口5: B-2-YUANBAO-CONSUMER

Day 8: Wave B-3 (2窗口并行)
  窗口1: B-3-1
  窗口2: B-3-2

Day 9: Wave B-4
  窗口1: B-4-1
```

---

*文档结束*

**使用建议**：
1. 复制本文档到 `D:\python\craw-platform\docs\PARALLEL_DEVELOPMENT_PLAN.md`
2. 每个AI窗口领取一个工作包
3. 完成后更新状态标记
4. 遇到问题在对应工作包下添加备注

