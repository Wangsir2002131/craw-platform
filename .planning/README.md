# AI模型爬虫调度系统 - 开发总览

> 更新时间：2026-04-07
> 项目路径：D:/python/craw-platform

---

## 🗺️ 阶段路线图

```
Phase A → Phase B → Phase C → Phase D → Phase E
(任务入口)  (队列拆分)  (账号接管)  (策略平台)  (API增强)
   ↓           ↓           ↓           ↓           ↓
 串行+并行    串行+并行    串行+并行     串行        并行
```

---

## 🚀 新窗口启动指南

### 第一步：读取当前进度

```bash
# 读取总览
D:/python/craw-platform/.planning/README.md

# 检查各阶段状态
D:/python/craw-platform/.planning/phase-a/status.md
D:/python/craw-platform/.planning/phase-b/status.md
D:/python/craw-platform/.planning/phase-c/status.md
D:/python/craw-platform/.planning/phase-d/status.md
D:/python/craw-platform/.planning/phase-e/status.md
```

### 第二步：找到你的任务

```
1. 从 Phase A 开始检查
2. 找到第一个 🔴 待开始 的任务
3. 检查前置依赖是否满足
4. 如果依赖满足 → 领取任务
5. 如果依赖不满足 → 检查下一个 Phase
```

### 第三步：领取任务

```
1. 在对应 status.md 中找到目标任务
2. 更新状态：🔴 待开始 → 🟡 进行中
3. 写入 Owner：你的窗口标识（如：窗口A）
4. 开始工作，只动「负责文件」列出的文件
```

### 第四步：完成任务

```
1. 完成开发后，运行验证命令
2. 更新状态：🟡 进行中 → 🟢 完成
3. 提交 commit，格式：[A-1-1] 完成任务展开器
```

---

## 📋 各阶段概览

| Phase | 目标 | Wave数 | 工作包数 | 并行窗口数 |
|-------|------|--------|----------|------------|
| A | 统一任务入口 | 3 | 10 | 最多4个 |
| B | 消费队列拆分 | 4 | 9 | 最多5个 |
| C | 账号主表接管 | 4 | 9 | 最多4个 |
| D | 调度策略平台化 | 1 | 5 | 1个 |
| E | 平台能力增强 | 3 | 12 | 最多8个 |

---

## ⚠️ 关键规则

### 文件所有权规则

```
1. 每个 Phase 有明确的文件所有权矩阵
2. 非所有权文件绝对禁止触碰
3. 如果需要修改其他文件，必须先协调
```

### 依赖等待规则

```
1. Wave 内串行任务必须按顺序完成
2. Phase 间必须等待前置 Phase 全部完成
3. 并行任务可以同时进行，但文件不能冲突
```

### Commit 规范

```
格式：[阶段-任务ID] 描述
示例：
  [A-1-1] 完成任务展开器核心逻辑
  [A-2-AFU] 完成afu执行器改造
  [B-1-1] 完成队列协议设计
```

---

## 🔧 环境准备

### 必需软件

```
1. Python 3.8+
2. MySQL 5.7+
3. Redis 6.0+
```

### Python 依赖

```
pymysql
redis
fastapi
uvicorn
selenium
playwright
```

### 配置文件

```
数据库配置：shared-methods/shared_methods.py → DB_CONFIG
Redis 配置：platform/config.py（Phase B 创建）
```

---

## 📞 协调机制

### 窗口分工建议

```
窗口A：专注 Phase A（串行 Wave A-1，然后并行 Wave A-2）
窗口B：等 Phase A 完成后，专注 Phase B
窗口C：等 Phase B 完成后，专注 Phase C
```

### 冲突解决

```
1. 如果两个窗口改了同一文件 → Git 会拒绝 push
2. 查看 commit message 前缀判断谁改的
3. 协调后决定保留哪个版本或合并
```

---

## 📊 当前进度

```
Phase A: 🔴 未开始
Phase B: 🔴 未开始（等 Phase A）
Phase C: 🔴 未开始（等 Phase B）
Phase D: 🔴 未开始（等 Phase C）
Phase E: 🔴 未开始（等 Phase D）
```

---

## 📝 更新日志

```
2026-04-07: 初始化项目开发总览
```
