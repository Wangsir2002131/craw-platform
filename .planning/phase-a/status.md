# Phase A 状态看板

> 更新时间：2026-04-07
> 目标：统一任务入口，改造爬虫为执行器模式

---

## 🚦 整体进度

| Wave | 状态 | 依赖 |
|------|------|------|
| A-1 基础架构 | 🟢 完成 | 无 |
| A-2 爬虫改造 | 🟢 完成 | A-1 完成 |
| A-3 集成测试 | 🟢 完成 | A-2 完成 |

---

## Wave A-1：基础架构（串行，必须按顺序）

### A-1-1 任务展开器
- **状态**：🟢 完成
- **Owner**：窗口A
- **负责文件**：`platform/dispatcher/task_expander.py`, `platform/dispatcher/__init__.py`
- **禁止触碰**：爬虫文件、py_main.py、shared-methods/
- **交付物**：TaskExpander 类，expand_task 方法
- **验证**：`from platform.dispatcher.task_expander import TaskExpander` 不报错

### A-1-2 任务主状态表SQL
- **状态**：🟢 完成
- **Owner**：窗口A
- **负责文件**：`platform/store/migrations/001_create_task_master_status.sql`
- **禁止触碰**：任何 .py 业务文件
- **交付物**：SQL 文件，表创建语句
- **验证**：SQL 语法正确

### A-1-3 主队列调度器核心
- **状态**：🟢 完成
- **Owner**：窗口A
- **负责文件**：`platform/dispatcher/master_dispatcher.py`, `platform/store/db_store.py`
- **禁止触碰**：爬虫文件、py_main.py
- **交付物**：MasterDispatcher 类，fetch_pending_tasks 方法
- **验证**：能从数据库查任务

### A-1-4 主服务入口
- **状态**：🟢 完成
- **Owner**：窗口A
- **负责文件**：`platform/main_server.py`, `platform/config.py`, `platform/__init__.py`
- **备份**：`py_main.py` → `py_main.py.backup`
- **禁止触碰**：爬虫文件、shared-methods/
- **交付物**：main_server.py 可运行
- **验证**：`python platform/main_server.py` 不报错

---

## Wave A-2：爬虫改造（4个窗口并行）

> ⚠️ 必须等 A-1-4 完成后才能开始

### A-2-AFU afu执行器改造
- **状态**：🟢 完成
- **Owner**：窗口A
- **负责文件**：`afu/afu.py`（只动这一个）
- **禁止触碰**：其他爬虫目录、platform/、shared-methods/、py_main.py
- **交付物**：execute_task 函数
- **验证**：可独立运行测试

### A-2-DOUBAO doubao执行器改造
- **状态**：🟢 完成
- **Owner**：窗口A
- **负责文件**：`doubao/doubao.py`（只动这一个）
- **禁止触碰**：其他爬虫目录、platform/、shared-methods/、py_main.py
- **交付物**：execute_task 函数

### A-2-DEEPSEEK deepseek执行器改造
- **状态**：🟢 完成
- **Owner**：窗口A
- **负责文件**：`deepseek/deepseek.py`（只动这一个）
- **禁止触碰**：其他爬虫目录、platform/、shared-methods/、py_main.py
- **交付物**：execute_task 函数

### A-2-YUANBAO yuanbao执行器改造
- **状态**：🟢 完成
- **Owner**：窗口A
- **负责文件**：`yuanbao/yuanbao.py`（只动这一个）
- **禁止触碰**：其他爬虫目录、platform/、shared-methods/、py_main.py
- **交付物**：execute_task 函数

---

## Wave A-3：集成测试（串行）

### A-3-1 结果收集与状态更新
- **状态**：🟢 完成
- **Owner**：窗口A
- **负责文件**：`platform/dispatcher/result_collector.py`
- **交付物**：ResultCollector 类

### A-3-2 阶段A集成测试
- **状态**：🟢 完成
- **Owner**：窗口A
- **负责文件**：`tests/test_phase_a.py`
- **交付物**：测试脚本通过

---

## 📍 新窗口接手指南

```
1. 读取本文件：D:/python/craw-platform/.planning/phase-a/status.md
2. 找到状态为 🔴 待开始 且 依赖满足 的第一个任务
3. 更新状态为 🟡 进行中，写入你的窗口标识（如：窗口A）
4. 开始工作，只动「负责文件」列出的文件
5. 完成后更新状态为 🟢 完成
6. 提交 commit，message 格式：[A-1-1] 完成任务展开器
```

---

## 🔒 文件所有权矩阵

| 文件/目录 | Owner Wave | 其他窗口 |
|-----------|------------|----------|
| platform/dispatcher/ | A-1, A-3 | 禁止 |
| platform/store/ | A-1, A-3 | 禁止 |
| platform/config.py | A-1 | 禁止 |
| platform/main_server.py | A-1 | 禁止 |
| afu/afu.py | A-2-AFU | 禁止 |
| doubao/doubao.py | A-2-DOUBAO | 禁止 |
| deepseek/deepseek.py | A-2-DEEPSEEK | 禁止 |
| yuanbao/yuanbao.py | A-2-YUANBAO | 禁止 |

---

## 📝 工作日志

```
2026-04-07: 创建 Phase A 状态看板
```
