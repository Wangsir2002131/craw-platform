# Phase C 状态看板

> 更新时间：2026-04-07
> 目标：账号主表接管，统一账号分配与状态管理

---

## 🚦 整体进度

| Wave | 状态 | 依赖 |
|------|------|------|
| C-1 账号数据模型 | 🟢 完成 | **Phase B 完成** |
| C-2 账号分配器 | 🟢 完成 | C-1 完成 |
| C-3 爬虫账号集成 | 🟢 完成 | C-2 完成 |
| C-4 备用账号与测试 | 🟢 完成 | C-3 完成 |

---

## ⚠️ 前置依赖检查

```
Phase B 必须全部完成才能开始 Phase C！
检查命令：读取 D:/python/craw-platform/.planning/phase-b/status.md
确认 Wave B-4 状态为 🟢 完成
```

---

## Wave C-1：账号数据模型（串行）

### C-1-1 账号数据模型SQL
- **状态**：🟢 完成
- **Owner**：窗口C
- **负责文件**：`platform/store/migrations/002_create_account_master.sql`
- **禁止触碰**：任何 .py 业务文件
- **交付物**：账号主表、账号资源表、账号状态日志表
- **验证**：SQL 语法正确

### C-1-2 账号资源登记脚本
- **状态**：🟢 完成
- **Owner**：窗口C
- **负责文件**：`platform/scripts/register_accounts.py`
- **禁止触碰**：爬虫文件
- **交付物**：账号登记脚本，能批量导入账号
- **验证**：能运行并导入账号数据

---

## Wave C-2：账号分配器与状态机（可并行）

> ⚠️ 必须等 C-1-2 完成后才能开始

### C-2-1 账号分配器
- **状态**：🟢 完成
- **Owner**：窗口C
- **负责文件**：`platform/account/account_allocator.py`
- **禁止触碰**：爬虫文件
- **交付物**：AccountAllocator 类，allocate/release 方法
- **验证**：能分配和释放账号

### C-2-2 账号状态机
- **状态**：🟢 完成
- **Owner**：窗口C
- **负责文件**：`platform/account/account_state_machine.py`
- **禁止触碰**：爬虫文件
- **交付物**：AccountStateMachine 类，状态流转逻辑
- **验证**：状态流转正确

---

## Wave C-3：爬虫账号表集成（4个窗口并行）

> ⚠️ 必须等 C-2 全部完成

### C-3-AFU afu使用账号表
- **状态**：🟢 完成
- **Owner**：窗口C
- **负责文件**：`afu/afu.py`（改造 execute_task）
- **禁止触碰**：其他爬虫目录、platform/（除调用 allocator）
- **交付物**：execute_task 使用 AccountAllocator
- **验证**：能从账号表获取账号

### C-3-DOUBAO doubao使用账号表
- **状态**：🟢 完成
- **Owner**：窗口C
- **负责文件**：`doubao/doubao.py`
- **禁止触碰**：其他爬虫目录
- **交付物**：execute_task 使用 AccountAllocator

### C-3-DEEPSEEK deepseek使用账号表
- **状态**：🟢 完成
- **Owner**：窗口C
- **负责文件**：`deepseek/deepseek.py`
- **禁止触碰**：其他爬虫目录
- **交付物**：execute_task 使用 AccountAllocator

### C-3-YUANBAO yuanbao使用账号表
- **状态**：🟢 完成
- **Owner**：窗口C
- **负责文件**：`yuanbao/yuanbao.py`
- **禁止触碰**：其他爬虫目录
- **交付物**：execute_task 使用 AccountAllocator

---

## Wave C-4：备用账号与测试

### C-4-1 备用账号接管逻辑
- **状态**：🟢 完成
- **Owner**：窗口C
- **负责文件**：`platform/account/backup_account_handler.py`
- **交付物**：BackupAccountHandler 类

### C-4-2 阶段C集成测试
- **状态**：🟢 完成
- **Owner**：窗口C
- **负责文件**：`tests/test_phase_c.py`
- **交付物**：测试脚本通过

---

## 📍 新窗口接手指南

```
1. 读取本文件：D:/python/craw-platform/.planning/phase-c/status.md
2. 检查前置依赖：Phase B 是否完成
3. 找到状态为 🔴 待开始 且 依赖满足 的第一个任务
4. 更新状态为 🟡 进行中，写入你的窗口标识
5. 开始工作，只动「负责文件」列出的文件
6. 完成后更新状态为 🟢 完成
7. 提交 commit，message 格式：[C-1-1] 完成账号数据模型SQL
```

---

## 🔒 文件所有权矩阵

| 文件/目录 | Owner | 其他窗口 |
|-----------|-------|----------|
| platform/store/migrations/ | C-1-1 | 禁止 |
| platform/scripts/ | C-1-2 | 禁止 |
| platform/account/ | C-2 | 禁止 |
| afu/afu.py（改造） | C-3-AFU | 禁止 |
| doubao/doubao.py（改造） | C-3-DOUBAO | 禁止 |
| deepseek/deepseek.py（改造） | C-3-DEEPSEEK | 禁止 |
| yuanbao/yuanbao.py（改造） | C-3-YUANBAO | 禁止 |

---

## 📝 工作日志

```
2026-04-07: 创建 Phase C 状态看板
```
