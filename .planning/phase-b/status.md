# Phase B 状态看板

> 更新时间：2026-04-07
> 目标：消费队列拆分，引入 Redis 实现队列解耦

---

## 🚦 整体进度

| Wave | 状态 | 依赖 |
|------|------|------|
| B-1 Redis基础 | 🟢 完成 | **Phase A 完成** |
| B-2 主服务改造 | 🟢 完成 | B-1 完成 |
| B-3 结果监听与时间窗 | 🟢 完成 | B-2 完成 |
| B-4 集成测试 | 🟢 完成 | B-3 完成 |

---

## ⚠️ 前置依赖检查

```
Phase A 必须全部完成才能开始 Phase B！
检查命令：读取 D:/python/craw-platform/.planning/phase-a/status.md
确认 Wave A-3 状态为 🟢 完成
```

---

## Wave B-1：Redis基础（串行）

### B-1-1 队列协议设计
- **状态**：🟢 完成
- **Owner**：窗口B
- **负责文件**：`platform/queue/protocol.py`, `platform/queue/__init__.py`
- **禁止触碰**：爬虫文件、py_main.py
- **交付物**：队列名称常量、消息类型定义
- **验证**：`from platform.queue.protocol import QUEUE_NAMES` 不报错

### B-1-2 Redis存储层
- **状态**：🟢 完成
- **Owner**：窗口B
- **负责文件**：`platform/queue/redis_store.py`
- **禁止触碰**：爬虫文件、py_main.py
- **交付物**：RedisQueueStore 类，push/pop 方法
- **验证**：能连接 Redis 并执行基本操作

---

## Wave B-2：主服务改造与消费者开发（并行）

> ⚠️ 必须等 B-1-2 完成后才能开始

### B-2-MASTER 主服务队列发布者改造
- **状态**：🟢 完成
- **Owner**：窗口B
- **负责文件**：`platform/dispatcher/master_dispatcher.py`（改造）
- **禁止触碰**：爬虫文件
- **交付物**：MasterDispatcher 支持 Redis 队列发布
- **验证**：任务能分发到 Redis 队列

### B-2-AFU-CONSUMER afu消费者
- **状态**：🟢 完成
- **Owner**：窗口B
- **负责文件**：`platform/consumers/afu_consumer.py`
- **禁止触碰**：其他爬虫消费者文件、afu/afu.py
- **交付物**：AfuConsumer 类，run 方法
- **验证**：能从 Redis 队列消费任务

### B-2-DOUBAO-CONSUMER doubao消费者
- **状态**：🟢 完成
- **Owner**：窗口B
- **负责文件**：`platform/consumers/doubao_consumer.py`
- **禁止触碰**：其他爬虫消费者文件
- **交付物**：DoubaoConsumer 类

### B-2-DEEPSEEK-CONSUMER deepseek消费者
- **状态**：🟢 完成
- **Owner**：窗口B
- **负责文件**：`platform/consumers/deepseek_consumer.py`
- **禁止触碰**：其他爬虫消费者文件
- **交付物**：DeepseekConsumer 类

### B-2-YUANBAO-CONSUMER yuanbao消费者
- **状态**：🟢 完成
- **Owner**：窗口B
- **负责文件**：`platform/consumers/yuanbao_consumer.py`
- **禁止触碰**：其他爬虫消费者文件
- **交付物**：YuanbaoConsumer 类

---

## Wave B-3：结果监听与时间窗（可并行）

> ⚠️ 必须等 B-2 全部完成

### B-3-1 结果监听器
- **状态**：🟢 完成
- **Owner**：窗口B
- **负责文件**：`platform/tasks/result_listener.py`
- **交付物**：ResultListener 类

### B-3-2 时间窗控制
- **状态**：🟢 完成
- **Owner**：窗口B
- **负责文件**：`platform/dispatcher/time_window.py`
- **交付物**：TimeWindowController 类

---

## Wave B-4：集成测试

### B-4-1 阶段B集成测试
- **状态**：🟢 完成
- **Owner**：窗口B
- **负责文件**：`tests/test_phase_b.py`
- **交付物**：测试脚本通过

---

## 📍 新窗口接手指南

```
1. 读取本文件：D:/python/craw-platform/.planning/phase-b/status.md
2. 检查前置依赖：Phase A 是否完成
3. 找到状态为 🔴 待开始 且 依赖满足 的第一个任务
4. 更新状态为 🟡 进行中，写入你的窗口标识
5. 开始工作，只动「负责文件」列出的文件
6. 完成后更新状态为 🟢 完成
7. 提交 commit，message 格式：[B-1-1] 完成队列协议设计
```

---

## 🔒 文件所有权矩阵

| 文件/目录 | Owner | 其他窗口 |
|-----------|-------|----------|
| platform/queue/ | B-1 | 禁止 |
| platform/dispatcher/（改造） | B-2-MASTER | 禁止 |
| platform/consumers/afu_consumer.py | B-2-AFU | 禁止 |
| platform/consumers/doubao_consumer.py | B-2-DOUBAO | 禁止 |
| platform/consumers/deepseek_consumer.py | B-2-DEEPSEEK | 禁止 |
| platform/consumers/yuanbao_consumer.py | B-2-YUANBAO | 禁止 |
| platform/tasks/result_listener.py | B-3-1 | 禁止 |
| platform/dispatcher/time_window.py | B-3-2 | 禁止 |

---

## 📝 工作日志

```
2026-04-07: 创建 Phase B 状态看板
```
