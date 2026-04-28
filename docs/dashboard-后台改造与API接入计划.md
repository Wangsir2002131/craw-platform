# dashboard 后台改造与 API 接入计划

## 说明方法

本计划只写方案，不写代码。

本计划的目标是为后续开发提供一份可以跨窗口继续执行的详细实施文档，围绕当前静态原型 `D:\python\craw-platform\dashboard.html`，将其演进为更正式的后台布局，并逐步接入真实接口。

本计划严格以 `D:\python\craw-platform\docs\project-推进流程.md` 为主约束，尤其对应其中阶段 E 的管理能力目标，同时明确区分：

- 当前项目已经具备的数据与能力
- 依赖阶段 A-D 完成后才能完全真实化的能力
- 当前只能先做兼容视图、占位接口或部分能力的内容

本计划遵循以下原则：

- 只做推进流程范围内的事
- 不把静态展示误写成真实平台能力
- 先建立清晰的前后端边界，再逐步接入数据源
- 先做可运行、可验证、可回滚的管理层骨架
- 为后续其他 Claude 窗口接续开发提供明确交接信息

---

## 一、任务目标与范围边界

### 1.1 总目标

将当前单文件静态页面 `dashboard.html` 演进为一个正式的后台管理界面，并提供真实的后端 API 支撑，使页面逐步覆盖推进流程阶段 E 中要求的以下能力：

- 任务面板
- 队列状态面板
- 账号面板
- 告警面板
- 控制中心
- 健康检查
- 日志查看

### 1.2 本次计划覆盖范围

本次计划覆盖：

- 管理后台的正式目录结构规划
- 单文件静态页面拆分方案
- FastAPI 后端分层方案
- API 合同规划
- 每个页面板块的数据来源映射
- 验证路径与阶段性里程碑
- 风险、假设与跨窗口交接要点

### 1.3 明确不在本次范围内的事项

以下内容明确不纳入本次范围，防止开发时扩项：

- 不直接完成阶段 A-D 全部平台改造
- 不第一步就上 Vue3、Vite、TypeScript 等完整前端工程体系
- 不一次性完成五台服务器协同部署
- 不第一步就接入腾讯云 CLS 正式能力
- 不建设登录、权限、RBAC、审计系统
- 不重写现有各爬虫的执行逻辑
- 不为了页面完整而伪造真实运营数据

### 1.4 本次成果的正确定位

本次工作的正确定位应为：

**建立一个符合阶段 E 目标形态的后台管理层骨架，并先打通当前项目已经具备条件的数据接口，同时为阶段 A-D 后续产物预留稳定接口位。**

---

## 二、当前状态总结

### 2.1 当前前端状态

当前已有文件：

- `D:\python\craw-platform\dashboard.html`

该页面已经具备完整的后台原型结构，包含以下区块：

- Overview
- Tasks
- Queues
- Accounts
- Alerts
- Controls
- Health
- Logs

现有页面的价值在于：

- 信息架构已经形成
- 左侧导航适合作为正式后台骨架
- 主要区块已与阶段 E 的目标一致
- 后续不是从零设计，而是从静态演示升级为正式后台

### 2.2 当前后端可复用能力

#### 来自 shared_methods

可复用能力主要包括：

- 数据库连接配置
- 数据库连接管理器
- 全局日志初始化
- 钉钉消息发送

这意味着后端 API 不必重新发明数据库基础设施和基础日志能力。

#### 来自 py_main.py

当前存在任务监听与调度相关逻辑，说明：

- 当前真实任务来源是 MySQL
- 当前任务查询已经有可参考的表关联方式
- 当前任务状态更新仍直接作用于现有业务表
- 当前尚不是平台化的任务主状态表模型

#### 来自 database_usage_example.py

当前已有数据库写回辅助函数，说明：

- 系统已有明确的数据写回路径
- 后续任务详情可扩展出结果摘要视图
- 但本次不应把后台扩展成结果审核系统

### 2.3 当前缺失的关键平台能力

当前项目尚未发现以下阶段 E 所依赖的完整平台底座：

- `platform` 目录
- FastAPI Web 服务
- Redis 队列层
- `task_master_status` 表
- `crawler_accounts` 表
- `platform_config` 表
- 心跳键写入逻辑
- 消费者在线状态聚合
- 按服务拆分的正式日志规范

因此必须明确：

- 阶段 E 描述的是目标状态，不是当前已存在状态
- 本次实现必须支持“兼容模式”与“平台模式”两类数据源状态

---

## 三、目标架构设计

### 3.1 总体架构建议

建议采用：

- 前端：轻量静态页面拆分
- 后端：FastAPI 提供 REST API 和静态资源托管
- 数据层：先接 MySQL，再逐步接 Redis、日志文件、配置源
- 告警层：先复用现有钉钉发送能力

### 3.2 为什么不建议第一步直接上 Vue3

原因如下：

- 项目当前是 Python 为主的脚本体系
- 当前最迫切的是先把静态原型变成真实后台入口
- 引入前端工程体系会增加额外复杂度
- 阶段 E 的核心不是技术栈升级，而是运营能力可视化

因此建议的演进顺序是：

1. 单 HTML 拆成多文件静态前端
2. FastAPI 托管页面与 API
3. 页面改为真实接口驱动
4. 等接口稳定后，再评估是否迁移到 Vue3

### 3.3 分层建议

建议采用四层结构：

#### 路由层
负责：

- URL 暴露
- 请求参数接收
- 响应模型输出
- HTTP 错误处理

#### 服务层
负责：

- 聚合多个数据源
- 业务规则判断
- 兼容模式与平台模式切换
- 状态映射

#### 仓储层
负责：

- MySQL 查询
- Redis 查询
- 配置读取
- 文件日志读取

#### 适配层
负责：

- 复用现有 `shared_methods` 能力
- 复用当前任务查询思路
- 封装钉钉发送
- 避免 API 代码直接耦合旧脚本主流程

### 3.4 关键架构原则

#### 原则一
前端永远只调 API，不直接接数据库，不直接依赖旧脚本变量。

#### 原则二
后端以资源域分层，而不是在路由中直接写 SQL。

#### 原则三
阶段 E 接口先统一定义，再根据依赖成熟度分别实现为：

- ready
- partial
- placeholder
- not_ready

#### 原则四
旧脚本只复用基础能力与查询思路，不直接作为 Web API 运行主流程。

---

## 四、推荐的目录与文件布局

## 4.1 后端目录建议

建议新增：

- `D:\python\craw-platform\platform\`

推荐结构：

- `platform\main.py`
- `platform\api\__init__.py`
- `platform\api\tasks.py`
- `platform\api\queues.py`
- `platform\api\accounts.py`
- `platform\api\alerts.py`
- `platform\api\control.py`
- `platform\api\stats.py`
- `platform\api\health.py`
- `platform\api\logs.py`

- `platform\services\task_service.py`
- `platform\services\queue_service.py`
- `platform\services\account_service.py`
- `platform\services\alert_service.py`
- `platform\services\control_service.py`
- `platform\services\stats_service.py`
- `platform\services\health_service.py`
- `platform\services\log_service.py`

- `platform\repositories\task_repository.py`
- `platform\repositories\queue_repository.py`
- `platform\repositories\account_repository.py`
- `platform\repositories\config_repository.py`
- `platform\repositories\heartbeat_repository.py`
- `platform\repositories\log_repository.py`

- `platform\schemas\common.py`
- `platform\schemas\tasks.py`
- `platform\schemas\queues.py`
- `platform\schemas\accounts.py`
- `platform\schemas\alerts.py`
- `platform\schemas\control.py`
- `platform\schemas\stats.py`
- `platform\schemas\health.py`
- `platform\schemas\logs.py`

- `platform\adapters\shared_methods_adapter.py`
- `platform\adapters\legacy_task_adapter.py`
- `platform\adapters\dingtalk_adapter.py`

- `platform\core\config.py`
- `platform\core\logging.py`
- `platform\core\dependencies.py`
- `platform\core\exceptions.py`
- `platform\core\response.py`

## 4.2 前端目录建议

建议新增：

- `D:\python\craw-platform\platform\web\`

推荐结构：

- `platform\web\index.html`
- `platform\web\assets\css\base.css`
- `platform\web\assets\css\layout.css`
- `platform\web\assets\css\components.css`
- `platform\web\assets\css\pages.css`

- `platform\web\assets\js\api.js`
- `platform\web\assets\js\app.js`
- `platform\web\assets\js\state.js`

- `platform\web\assets\js\renderers\overview.js`
- `platform\web\assets\js\renderers\tasks.js`
- `platform\web\assets\js\renderers\queues.js`
- `platform\web\assets\js\renderers\accounts.js`
- `platform\web\assets\js\renderers\alerts.js`
- `platform\web\assets\js\renderers\control.js`
- `platform\web\assets\js\renderers\health.js`
- `platform\web\assets\js\renderers\logs.js`

## 4.3 为什么推荐单页后台继续演进

不建议第一步就拆成多个独立 HTML 页面，原因：

- 当前 `dashboard.html` 已经天然适合作为单页运营台
- 单页总览更符合阶段 E 管理能力展示形态
- 减少状态同步与导航复杂度
- 更利于先完成一个可用后台入口

## 4.4 推荐同步维护的说明文档

为方便跨窗口继续开发，建议后续同步维护：

- `D:\python\craw-platform\docs\admin-dashboard-spec.md`
- `D:\python\craw-platform\docs\platform-api-contract.md`
- `D:\python\craw-platform\docs\platform-stage-e-implementation-notes.md`

---

## 五、API 合同规划

本节按阶段 E 接口目标、当前 UI 区块、当前可实现程度进行规划。

## 5.1 Overview / Stats

### 建议接口

- `GET /api/stats`

### 对应 UI

- Overview 顶部状态区
- 指标卡片
- 策略与时间窗展示
- 最后刷新时间

### 建议返回内容

建议聚合返回：

- 任务统计
- 平台状态摘要
- 当前调度策略
- 时间窗配置
- 数据源健康情况
- 最近更新时间

### 当前可真实实现程度

较高。

当前可优先从现有任务表聚合真实统计；策略与时间窗可先来自配置文件或默认值；Redis、heartbeat 等可返回 unavailable 或 not_ready。

---

## 5.2 Tasks

### 阶段 E 对应接口

- `GET /api/tasks`
- `GET /api/tasks/{id}`

### 对应 UI

- Task Panel
- Task Summary

### 当前最值得优先打通

因为现有数据库中已经有任务和问题数据，任务接口是最容易先做成真实接口的部分。

### `GET /api/tasks` 建议支持

- 分页
- 状态过滤
- 模型过滤
- 关键字搜索
- 时间范围过滤
- 排序

### 当前数据来源建议

当前若尚无 `task_master_status`，先从现有业务表与问题表拼出兼容视图。

### 状态映射要求

现有业务状态与阶段 E 理想状态不一致，因此必须设计统一状态映射层。

建议在响应中增加：

- `data_mode`
- `status_precision`
- `status_source`

用于明确当前是：

- legacy_task_table 模式
- coarse 状态精度

### `GET /api/tasks/{id}` 建议返回

- 任务基础信息
- 关联问题信息
- 模型类型
- 最大轮次
- 当前状态
- 创建时间与更新时间
- 当前是否为兼容视图
- 若可获取，则附带结果摘要

---

## 5.3 Queues

### 阶段 E 对应接口

- `GET /api/queues`

### 对应 UI

- Queue Status

### 目标返回内容

- 各队列长度
- 消费者数量
- 积压情况
- 结果队列状态
- 任务状态汇总

### 当前现实约束

当前项目尚无 Redis 队列与消费者心跳，因此：

- 队列长度不可真实获取
- 消费者在线数不可真实获取
- 吞吐与 lag 不可真实获取

### 当前过渡实现建议

接口先建好，但需明确 capability：

- 队列名称清单可真实返回
- 任务状态概览可从 MySQL 兼容统计
- 队列实时监控字段返回 null 或 not_ready

禁止伪造随机队列数据。

---

## 5.4 Accounts

### 阶段 E 对应接口

- `GET /api/accounts`
- `POST /api/accounts/{id}/disable`

### 对应 UI

- Account Panel
- Account Statistics

### 目标数据源

阶段 E 设计依赖阶段 C 的：

- `crawler_accounts`
- `crawler_account_events`

### 当前现实约束

账号主表尚不存在，因此真正的账号生命周期面板当前无法完整真实化。

### 过渡策略建议

#### 推荐主策略

接口合同先设计好，但返回：

- `feature_status = not_ready`
- `dependency = stage_c_accounts_required`

#### 仅在必须时采用的兼容策略

如果确实需要先展示账号资源，可提供本地资源清单视图：

- AFU Profile 目录
- Doubao Profile 目录
- DeepSeek cookie 文件
- Yuanbao cookie 文件

但必须明确这是资源清单，不是生命周期面板。

---

## 5.5 Alerts

### 建议接口

- `GET /api/alerts`

### 对应 UI

- Alert Panel

### 当前可复用能力

- 钉钉发送函数

### 当前建议实现方式

第一版先实现告警查询能力和基础规则状态展示，不一开始就做完整事件中心。

### 当前适合先做的告警类型

- 任务失败异常聚合
- 数据库连接异常
- Redis 连接异常
- 日志中高频错误关键字

### 暂不建议第一批完整实现的告警

- 账号连续失败
- 备用账号接管
- 消费者失联
- 队列积压

这些依赖阶段 B/C/E 更完整的底座。

---

## 5.6 Control

### 阶段 E 对应接口

- `POST /api/control/pause`
- `POST /api/control/resume`
- `POST /api/config/schedule-policy`
- `POST /api/accounts/{id}/force-disable`

### 对应 UI

- Control Center

### 实施原则

控制类接口最容易造成“按钮存在但无真实作用”的误导，因此必须明确每个接口的生效等级：

- `applied`
- `accepted_not_effective_yet`
- `not_ready`

### 当前优先实现建议

- 调度策略修改可以先写到配置源
- pause/resume 只有在后台主服务真正读取共享配置后才算真实有效
- force-disable 依赖账号主表，当前应返回 not_ready

---

## 5.7 Health

### 阶段 E 对应接口

- `GET /health`

### 对应 UI

- Health and Heartbeats

### 当前最适合优先真实化

建议第一批就做成真实接口，返回：

- FastAPI 服务自身状态
- 数据库连接状态
- Redis 连接状态
- 当前运行模式
- 当前 capability 状态

### 当前不应伪造的内容

- 消费者在线状态
- 主服务调度线程在线状态
- 结果监听线程在线状态

如果未真正实现，则明确返回 not_ready。

---

## 5.8 Logs

### 阶段 E 对应接口

- `GET /api/logs`

### 对应 UI

- Log Viewer

### 当前建议实现方向

第一版先支持统一日志文件读取，支持：

- 指定最近 N 行
- 按关键字过滤
- 预留按服务过滤

### 后续演进方向

待平台服务独立后，再切换到按服务日志文件读取。

---

## 六、前端重构计划

## 6.1 当前原型应保留的优点

现有 `dashboard.html` 应保留的部分包括：

- 左侧导航结构
- 顶部概览结构
- 各面板名称与组织方式
- 统一的视觉风格
- 单页运营台模式

### 结论

不建议推翻重做，而应采用：

- 结构拆分
- 样式保留
- 演示数据替换为真实 API 数据

## 6.2 前端重构顺序

### 第一层：结构拆分

将单文件拆成：

- 页面骨架
- 样式文件
- API 请求文件
- 各面板渲染文件

### 第二层：数据接线

每个面板独立接自己的接口：

- overview -> `/api/stats`
- tasks -> `/api/tasks`
- queues -> `/api/queues`
- accounts -> `/api/accounts`
- alerts -> `/api/alerts`
- control -> 对应 POST 接口
- health -> `/health`
- logs -> `/api/logs`

### 第三层：页面状态管理

不引入复杂框架，先实现最小状态管理：

- 加载中状态
- 接口错误状态
- 当前筛选条件
- 当前刷新时间
- capability 状态
- 当前选中的任务详情

### 第四层：交互增强

建议补充：

- 自动刷新开关
- 面板级刷新
- 任务筛选
- 日志服务切换
- 控制按钮确认提示

## 6.3 布局演进建议

### 全局层

- 顶部平台状态条
- 全局错误提示
- 当前策略与时间窗
- 刷新状态

### 一级面板层

- Overview
- Tasks
- Queues
- Accounts
- Alerts
- Controls
- Health
- Logs

### 二级详情层

- 任务详情抽屉或弹层
- 告警详情弹层
- 日志展开视图

## 6.4 前端必须支持的空态与能力态

前端必须能清晰表达：

- 数据可用
- 数据部分可用
- 依赖未完成
- 占位模式
- 接口异常

例如：

- 当前未启用 Redis 队列，队列长度不可用
- 当前未完成账号主表接管，账号生命周期面板未激活

这是为了避免页面误导运营者。

---

## 七、后端实施计划

## 7.1 后端总体顺序

建议后端按以下顺序实现：

### 顺序一：基础骨架

- 平台入口
- 配置加载
- 日志初始化
- 路由注册
- 统一响应结构

### 顺序二：首批只读真实接口

优先实现：

1. `/health`
2. `/api/stats`
3. `/api/tasks`
4. `/api/tasks/{id}`
5. `/api/logs`

### 顺序三：部分占位或部分能力接口

再实现：

- `/api/queues`
- `/api/accounts`
- `/api/alerts`
- `/api/control/pause`
- `/api/control/resume`
- `/api/config/schedule-policy`

### 顺序四：与阶段 A-D 产物联动升级

待 A-D 逐步完成后，再真实替换：

- `task_master_status`
- Redis 队列
- `crawler_accounts`
- `platform_config`
- heartbeat
- 完整 alert engine

## 7.2 对现有模块的复用策略

### 可直接适配复用

- 数据库连接管理
- 数据库配置
- 日志初始化
- 钉钉消息发送

### 只参考不直接复用

- `py_main.py` 中的长轮询与调度主流程
- 当前集成式运行逻辑

### 可局部借鉴

- 当前任务查询 SQL 结构
- 当前任务状态更新思路
- 当前数据库写回模式

## 7.3 为什么 API 层不能直接依赖旧主流程

原因：

- 旧主流程面向长循环，不适合请求响应模式
- 旧脚本职责过重
- 路由层需要稳定返回结构，而旧脚本偏执行流

因此建议只提取：

- 数据查询思路
- 基础设施能力
- 告警能力
- 状态更新规则

而不是直接复用整个运行逻辑。

## 7.4 统一响应结构建议

建议所有接口响应统一包含：

- `data`
- `meta`
- `capability`

其中：

### data
具体业务数据

### meta
分页、刷新时间、数据源说明、模式说明

### capability
用于标明能力状态，例如：

- ready
- partial
- placeholder
- not_ready

这样前端可以统一处理。

## 7.5 兼容模式与平台模式

建议后端内部明确：

### legacy mode
基于现有业务表和旧脚本体系输出后台数据

### platform mode
基于阶段 A-D 形成的平台结构输出后台数据

服务层应根据以下条件决定当前使用哪种模式：

- 表是否存在
- Redis 是否可用
- 配置是否启用
- 心跳键是否存在

这样可以保证后续升级时前端几乎不需要重写。

---

## 八、页面板块与数据源映射

## 8.1 Overview

### 目标数据

- 平台在线状态
- Redis 状态
- 当前策略
- 时间窗
- 任务汇总指标
- 更新时间

### 当前可用来源

- 现有任务表
- 配置文件或默认配置
- 数据库探测
- Redis 可用性探测

### 当前可真实化程度

较高。

---

## 8.2 Tasks

### 目标数据

- 任务列表
- 任务详情
- 任务状态统计
- 优先级
- 队列名
- 账号
- 服务器
- 更新时间

### 阶段 E 理想来源

- `task_master_status`
- 原业务任务表
- 问题表
- 结果表

### 当前可用来源

- `ent_data_product_llm_task`
- `ent_data_product_question`
- `ent_data_question`

### 当前缺失字段

- queue_name
- account_id
- server_id
- dispatched_at
- claimed_at
- completed_at
- retry_count
- fail_reason
- priority

### 当前实现结论

- 任务列表和基础统计可先做真实版
- 缺失字段明确返回 null 或未接入
- 前端必须标明兼容模式

---

## 8.3 Queues

### 目标数据

- 队列长度
- 消费者数量
- 吞吐
- 最大等待时间
- 结果队列状态

### 阶段 E 理想来源

- Redis
- 消费者心跳键
- `task_master_status`

### 当前可用来源

- 预定义队列名称
- 任务统计的兼容汇总

### 当前实现结论

- 接口应存在
- 实时值应标记为 not_ready 或 unavailable
- 禁止虚构实时队列监控数值

---

## 8.4 Accounts

### 目标数据

- 账号列表
- 账号状态
- 账号类型
- 失败次数
- 最近成功/失败时间
- 账号状态统计

### 阶段 E 理想来源

- `crawler_accounts`
- `crawler_account_events`

### 当前可能的兼容来源

- 各平台本地 Profile 目录
- 各平台 cookie 文件目录

### 当前实现结论

- 真正账号面板依赖阶段 C
- 当前如需展示，只能作为资源清单模式
- 文案必须明确不是生命周期状态面板

---

## 8.5 Alerts

### 目标数据

- 当前活跃告警
- 最近告警记录
- 告警等级
- 告警来源
- 去重状态

### 当前可优先实现来源

- 任务失败聚合
- 数据库/Redis 探测异常
- 日志关键字扫描
- 钉钉发送能力

### 当前实现结论

- 第一版先做基础告警视图
- 告警规则分批落地，不一次写死所有阶段 E 规则

---

## 8.6 Controls

### 目标操作

- 暂停分发
- 恢复分发
- 修改策略
- 强制停用账号

### 当前可执行程度

- 策略修改可优先实现为配置写入
- pause/resume 是否真实生效依赖后台控制点
- force-disable 依赖账号主表，当前 not_ready

### 当前实现结论

控制接口必须返回明确执行状态，不能用静默成功误导用户。

---

## 8.7 Health

### 目标数据

- FastAPI 状态
- 数据库状态
- Redis 状态
- 消费者心跳
- 主服务心跳

### 当前可真实实现来源

- FastAPI 自身状态
- 数据库连接探测
- Redis 连接探测
- 当前模式状态

### 当前实现结论

健康检查应列入第一批真实接口。

---

## 8.8 Logs

### 目标数据

- 最近日志
- 指定服务日志
- 关键字过滤
- 指定行数

### 当前可用来源

- 现有统一日志文件

### 当前实现结论

日志查看可列入第一批真实接口。

---

## 九、验证计划与里程碑

## 9.1 里程碑一：后台骨架建立

### 目标

- 建立 FastAPI 入口
- 托管静态后台资源
- 将单文件页面拆成可维护结构

### 验收标准

- 页面可正常打开
- 左侧导航与各面板结构完整
- 页面可发起 API 请求
- 未接入接口可展示清晰空态或 not_ready 状态

---

## 9.2 里程碑二：首批真实只读接口打通

### 目标接口

- `/health`
- `/api/stats`
- `/api/tasks`
- `/api/tasks/{id}`
- `/api/logs`

### 验收标准

- 健康页显示真实数据库状态
- 概览卡片显示真实任务聚合
- 任务面板显示真实任务数据
- 日志面板显示真实日志内容
- 页面在接口失败时有清晰提示

---

## 9.3 里程碑三：阶段 E 接口合同补齐

### 目标接口

- `/api/queues`
- `/api/accounts`
- `/api/alerts`
- `/api/control/pause`
- `/api/control/resume`
- `/api/config/schedule-policy`

### 验收标准

- 路由存在
- 响应结构稳定
- capability 清晰
- 前端不会因为数据未准备好而崩溃

---

## 9.4 里程碑四：与阶段 A-D 联动升级

### 升级顺序建议

1. 接 `task_master_status`
2. 接 Redis 队列
3. 接 `crawler_accounts`
4. 接 `platform_config`
5. 接 heartbeat
6. 完善 alert engine

### 验收标准

- 前端无需大改即可切换真实数据源
- capability 从 partial 升级为 ready
- 原有兼容接口仍保持结构稳定

---

## 9.5 测试维度建议

### API 级测试

- 路由可访问
- 参数校验正确
- 分页与过滤正确
- 异常返回统一

### 页面级测试

- 首次加载成功
- 面板独立刷新
- 空状态展示正常
- 错误提示正常
- 长日志显示可用

### 数据源级测试

- 数据库断开时 `/health` 返回正确状态
- 任务无数据时 `tasks` 返回空列表而不是报错
- 日志文件不存在时 `logs` 接口返回清晰说明
- Redis 未部署时 `queues` 返回 not_ready

---

## 十、风险、假设与交接说明

## 10.1 主要风险

### 风险一

阶段 E 文档默认 A-D 已完成，但当前实际并非如此，容易导致误把目标平台能力当成当前现状。

### 风险二

当前 `dashboard.html` 中很多值只是演示值，不能当成数据库设计依据。

### 风险三

如果 FastAPI 路由直接耦合旧脚本长循环逻辑，会造成阻塞、维护困难与边界不清。

### 风险四

健康状态和队列状态如果被静态文本伪装，会误导后续使用者。

## 10.2 关键假设

后续开发默认以下条件成立，如不成立则需先调整方案：

- 允许新增 `platform` 目录
- 允许引入 FastAPI
- 当前阶段接受兼容模式后台
- Redis 可能尚未部署
- 阶段 C 账号主表尚未存在
- 当前日志文件可读取
- 当前任务表为真实可用数据源

## 10.3 给下一窗口的交接重点

下一 Claude 会话继续时，建议严格按以下顺序推进：

1. 确认 `platform` 目录结构
2. 先定义统一响应结构与 capability 语义
3. 先做 `/health`
4. 再做 `/api/stats`
5. 再做 `/api/tasks`
6. 再拆前端并接 overview、tasks、health、logs

### 不建议下一窗口一开始就做的事

- 直接迁移 Vue3
- 直接做完整告警引擎
- 直接做完整账号生命周期后台
- 直接假设 Redis、heartbeat、账号表已经存在
- 直接重写当前 `py_main.py` 为平台主服务

### 下一窗口应优先确认的问题

- 兼容模式与平台模式的切换依据
- 任务统一状态枚举映射规则
- 第一版日志接口读取哪个文件
- 控制接口哪些是真执行，哪些只是记录配置

### 推荐下一窗口产出物

- 平台目录树草案
- API 响应结构文档
- 各接口字段清单
- 前端面板与接口映射表
- 兼容模式字段映射规则表

---

## 十一、明确的范围外事项

以下事项必须排除，防止需求蔓延：

- 不把本次任务变成全平台改造
- 不把本次任务变成完整前端工程迁移
- 不加入登录权限系统
- 不直接做五机部署编排
- 不把 CLS 正式接入当作第一批完成标准
- 不把后台第一版扩展成完整结果审核后台

---

## 十二、推荐执行顺序清单

### 第一组：基础骨架

- 建立 `platform` 后端目录和 `web` 静态目录
- 定义统一响应格式
- 定义 capability 语义
- 建立 FastAPI 入口与静态资源托管

### 第二组：首批真实能力

- 实现 `/health`
- 实现 `/api/stats`
- 实现 `/api/tasks`
- 实现 `/api/tasks/{id}`
- 实现 `/api/logs`

### 第三组：前端接线

- 将 `dashboard.html` 拆成 `index.html + css + js`
- 用真实 API 替换 overview、tasks、health、logs 演示数据
- 为 queues、accounts、alerts、control 增加部分接入或未接入状态展示

### 第四组：阶段 E 合同补齐

- 建立 `/api/queues`
- 建立 `/api/accounts`
- 建立 `/api/alerts`
- 建立 `/api/control/pause`
- 建立 `/api/control/resume`
- 建立 `/api/config/schedule-policy`

### 第五组：与 A-D 联动升级

- A 完成后切任务接口到底层主状态表
- B 完成后接 Redis 队列与 heartbeat
- C 完成后接账号表
- D 完成后接平台配置表与策略热更新
- E 后续再补告警规则与更完整日志能力

---

## 结论

最合理的推进路线不是一次性做一个很重的后台系统，而是：

- 先把现有 `dashboard.html` 变成正式后台骨架
- 先接当前已经真实存在的任务和健康数据
- 再把阶段 E 中依赖 A-D 的能力通过统一 API 合同预留出来
- 最后随着平台底座完善，逐步把占位接口替换为真实数据源

这样做的直接收益是：

- 当前能尽快得到一个真正可打开、可查询的后台入口
- 后续 A-D 建设时不必反复重做前后端边界
- 其他 Claude 窗口可以按本计划分模块继续执行，不需要重新探索上下文

