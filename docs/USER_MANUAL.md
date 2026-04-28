# Crawler Platform 用户操作手册

## 1. 服务入口

- 健康检查：`GET /health`
- 控制面板：`GET /control/status`
- 任务接口：`/tasks`
- 队列接口：`/queues`
- 账号接口：`/accounts`
- 告警接口：`/alerts`
- 日志接口：`/logs`
- 统计接口：`/stats/summary`

## 2. 常用启动命令

完整服务：

```bash
py -m platform.main_server --forever --host 127.0.0.1 --port 8000
```

仅 API：

```bash
py -m platform.main_server --api-only
```

仅调度：

```bash
py -m platform.main_server --dispatcher-only --forever
```

## 3. 任务操作

查询任务：

```bash
curl http://127.0.0.1:8000/tasks/1
```

创建任务：

```bash
curl -X POST http://127.0.0.1:8000/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "product_llm_task_id": 1001,
    "question_id": 2001,
    "question_name": "示例问题",
    "llm_key": "afu",
    "round_num": 1,
    "priority": 80
  }'
```

取消任务：

```bash
curl -X POST http://127.0.0.1:8000/tasks/1/cancel \
  -H "Content-Type: application/json" \
  -d '{"reason":"manual_cancel"}'
```

## 4. 队列操作

- 查询队列状态：`GET /queues/status`
- 查询队列统计：`GET /queues/stats`
- 清空队列：`POST /queues/{queue_name}/clear`

示例：

```bash
curl -X POST http://127.0.0.1:8000/queues/queue:afu/clear
```

## 5. 账号操作

查询账号：

```bash
curl "http://127.0.0.1:8000/accounts?platform_name=afu&account_status=available"
```

更新账号状态：

```bash
curl -X PATCH http://127.0.0.1:8000/accounts/1/status \
  -H "Content-Type: application/json" \
  -d '{"status":"disabled","reason":"manual_offline"}'
```

## 6. 控制与告警

- 暂停调度：`POST /control/pause`
- 恢复调度：`POST /control/resume`
- 请求重启：`POST /control/restart`
- 写入告警配置：`POST /alerts/configs`
- 触发告警事件：`POST /alerts/trigger`

## 7. 日志与压测

- 查看日志文件列表：`GET /logs`
- 查看单个日志尾部：`GET /logs/master_server.log?lines=200`
- 压测命令：

```bash
py tests/stress_test.py --base-url http://127.0.0.1:8000 --path /health --concurrency 20 --iterations 50
```
