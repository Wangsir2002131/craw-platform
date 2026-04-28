# Crawler Platform 部署说明

## 1. 环境要求

- Python 3.12+
- MySQL 5.7+
- Redis 6.0+
- 已安装 Python 包：`fastapi`、`uvicorn`、`redis`、`pymysql`

## 2. 基础配置

通过环境变量配置数据库与调度参数：

```bash
export DB_HOST=localhost
export DB_PORT=3306
export DB_USER=root
export DB_PASSWORD=your_password
export DB_NAME=your_database
export DISPATCH_INTERVAL=5
export BATCH_SIZE=100
export EXECUTE_CRAWLERS=0
```

可选 API 与心跳参数：

```bash
export HOST=127.0.0.1
export PORT=8000
export HEARTBEAT_INTERVAL=10
export HEALTH_CHECK_INTERVAL=30
export STALE_AFTER=60
```

## 3. 安装依赖

```bash
py -m pip install fastapi uvicorn redis pymysql
```

## 4. 启动方式

启动完整主服务：

```bash
./scripts/start_all.sh
```

只启动 API：

```bash
py -m platform.main_server --api-only --host 127.0.0.1 --port 8000
```

只启动调度与心跳：

```bash
py -m platform.main_server --dispatcher-only --forever
```

执行单次调度：

```bash
py -m platform.main_server --once --limit 100
```

## 5. 验证项

- `GET /health` 返回 `healthy`
- `GET /control/status` 可返回控制状态
- `GET /queues/stats` 可返回 Redis 队列统计
- `logs/master_server.log` 持续写入调度日志

## 6. 部署建议

- 使用 `systemd`、`supervisor` 或容器守护 `py -m platform.main_server --forever`
- Redis 应开启持久化
- MySQL 账号建议最小权限
- API 置于反向代理后，对外只暴露必要端口
