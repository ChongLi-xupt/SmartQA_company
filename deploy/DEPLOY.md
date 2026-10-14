# Smart QA 离线 Docker 部署手册

> 目标：把光伏发电 NL2SQL 系统打包成 Docker 镜像，部署到**不能连外网**的华为服务器。
> LLM 走内网 OpenAI 兼容服务 `http://10.222.2.2:60001/v1`（deepseek-v4-flash）。

## 0. 部署架构

```
┌─────────────────────────────────────────────────────┐
│ 华为服务器（不能连外网，Docker）                       │
│                                                     │
│  docker compose 默认 bridge 网络                     │
│   ├─ smart-qa 容器   → 5000:5000（对外）            │
│   │     └─ 连服务名 mysql 的 3306                    │
│   └─ mysql 容器(5.7) → 3307:3306（宿主机，供外部工具）│
│         └─ 首次启动自动建 pv_analysis 库 + 示例数据   │
│                                                     │
│  LLM：smart-qa 容器 → 内网 10.222.2.2:60001          │
└─────────────────────────────────────────────────────┘
```

**关键点**：
- 不用 `network_mode: host`（Windows Docker Desktop 不生效），用默认 bridge + 服务名连接，跨平台通用。
- 不用 Redis，`HISTORY_BACKEND=memory`，单 worker。
- `.env` 不进镜像（`.dockerignore` 排除），必须单独传到服务器。
- 容器化 MySQL 的只读账号是 `qa_ro@'%'`（外部连接来源非 localhost）。

## 1. 涉及的文件清单

| 文件 | 作用 | 是否进镜像 |
|---|---|---|
| `Dockerfile` | 构建应用镜像 | 不进（构建用） |
| `docker-compose.yml` | 联网测试用（会构建） | 不进 |
| `docker-compose.offline.yml` | **离线部署用**（不构建，直接用导入镜像） | 不进 |
| `.dockerignore` | 排除 .env/缓存等 | 不进 |
| `.env` | 运行时配置（DB/LLM/密钥） | **不进，必须单独传** |
| `deploy/init_pv_database.sql` | 建库建表+示例数据+只读账号 | 不进（挂载进 mysql 容器） |
| `rag/glossary.md` | RAG 术语表，运行时必需 | **进镜像**（不能误删） |

## 2. 本地打包（PowerShell）

```powershell
cd "D:\桌面\实习\nl2sql - 副本"

# 2.1 确认 .env 关键配置
# 应看到：DB_HOST=mysql、DB_NAME=pv_analysis、LLM_BASE_URL=http://10.222.2.2:60001/v1、HISTORY_BACKEND=memory
Select-String -Path .env -Pattern "DB_HOST|DB_USER|DB_NAME|LLM_BASE_URL|LLM_MODEL|HISTORY_BACKEND"

# 2.2 构建应用镜像
docker compose build

# 2.3 导出镜像（应用 + mysql:5.7）为 tar
docker save smart-qa:latest mysql:5.7 -o smart-qa-images.tar

# 2.4 查看 tar 大小（约 1~2GB）
dir smart-qa-images.tar
```

## 3. 上传到华为服务器

> 把 `<华为服务器IP>` 替换成实际 IP。

```powershell
# 3.1 服务器上先建目录（只需一次）
ssh root@<华为服务器IP> "mkdir -p /opt/smart-qa/deploy"

# 3.2 传镜像 tar（大文件，稍慢）
scp smart-qa-images.tar root@<华为服务器IP>:/opt/

# 3.3 传配置和编排文件（四个缺一不可）
scp .env                          root@<华为服务器IP>:/opt/smart-qa/.env
scp docker-compose.offline.yml    root@<华为服务器IP>:/opt/smart-qa/
scp deploy/init_pv_database.sql   root@<华为服务器IP>:/opt/smart-qa/deploy/
```

> ⚠️ `.env` 必须传：compose 启动靠它（`env_file: .env`），不传直接报错起不来；`app.py` 启动时校验 `SESSION_SECRET_KEY`（≥32 位），缺失则拒绝启动。

## 4. 服务器上部署（SSH 登录华为服务器）

```bash
# 4.1 导入镜像（不是 tar 解压！docker load 还原成 Docker 镜像）
docker load -i /opt/smart-qa-images.tar

# 4.2 确认两个镜像都在
docker images | grep -E "smart-qa|mysql"
# 应看到 smart-qa:latest 和 mysql:5.7

# 4.3 确认文件齐全
ls -l /opt/smart-qa/.env /opt/smart-qa/docker-compose.offline.yml /opt/smart-qa/deploy/init_pv_database.sql

# 4.4 启动（离线 compose，直接用导入的镜像，不在服务器上构建）
cd /opt/smart-qa
docker compose -f docker-compose.offline.yml up -d

# 4.5 看启动日志（等 mysql 出现 ready for connections，约十几秒）
docker compose -f docker-compose.offline.yml logs -f smart-qa
```

## 5. 验证

```bash
# 5.1 健康检查 —— 三个都 ok 才算完全成功
curl http://127.0.0.1:5000/healthz
# 预期 {"status":"ok","db":"ok","llm_configured":true}

# 5.2 中文落库验证（确认无乱码）
docker exec smart-qa-mysql mysql -u qa_ro -pQaRo@Pv2026 --default-character-set=utf8mb4 \
  -e "SELECT plant_name FROM pv_analysis.plant_info;"
# 预期：榆林光伏电站 / 敦煌光伏电站 / 银川光伏电站

# 5.3 实测 ask 接口（NL2SQL 全链路）
curl -X POST http://127.0.0.1:5000/api/v1/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"各电站装机容量是多少？"}'
```

## 6. 放行端口 + 对外访问

```bash
# CentOS
firewall-cmd --add-port=5000/tcp --permanent && firewall-cmd --reload
# Ubuntu
ufw allow 5000
```

对外访问地址：

| 用途 | 地址 |
|---|---|
| 前端问答页 | `http://<华为服务器IP>:5000/` |
| 接口文档（可在线试调） | `http://<华为服务器IP>:5000/docs` |
| ask 接口（程序调用） | `POST http://<华为服务器IP>:5000/api/v1/ask` |

ask 接口请求体：`{"question":"你的问题"}`，同步返回 10~60 秒，返回含 `answer`（Markdown）、`data`（表格数据）、`chart`（图表配置）。

## 7. 排错速查

| 现象 | 原因 / 解决 |
|---|---|
| `up` 报 `env_file not found` | `.env` 没传或不在 `/opt/smart-qa/.env` |
| `up` 报 `no such file` 找不到 yml | `docker-compose.offline.yml` 没传到 `/opt/smart-qa/` |
| `healthz` 的 `db` 不是 `ok` | `.env` 的 `DB_HOST`/`DB_PORT` 错，应是 `mysql`/`3306` |
| `healthz` 的 `llm_configured:false` | 服务器到 `10.222.2.2:60001` 不通，先 `curl -I http://10.222.2.2:60001/v1/chat/completions` 验证 |
| 电站名乱码 `æ¦†æž—…` | `init_pv_database.sql` 开头没 `SET NAMES utf8mb4` 或数据卷是旧的 → `docker compose -f docker-compose.offline.yml down -v` 清卷重来 |
| `manifest not found` | `docker load` 没成功，检查 `docker images` 里镜像名是否 `smart-qa:latest`/`mysql:5.7` |
| mysql 容器反复重启 | 查 `docker logs smart-qa-mysql`，多是 init SQL 语法错或 3307 端口被占 |

## 8. 常用运维命令

```bash
cd /opt/smart-qa

# 查看状态
docker compose -f docker-compose.offline.yml ps

# 查日志
docker compose -f docker-compose.offline.yml logs -f smart-qa
docker compose -f docker-compose.offline.yml logs -f mysql

# 重启应用（改了 .env 后生效）
docker compose -f docker-compose.offline.yml restart smart-qa

# 停止
docker compose -f docker-compose.offline.yml down

# 彻底重来（连数据卷一起删，库会重新初始化）
docker compose -f docker-compose.offline.yml down -v
docker compose -f docker-compose.offline.yml up -d

# 进容器排查
docker exec -it smart-qa bash
docker exec -it smart-qa-mysql mysql -u root -pRoot@Pv2026
```

## 9. 更新部署（代码变更后）

改了代码需要重新部署时：

```powershell
# 本地：重新构建 + 打包
cd "D:\桌面\实习\nl2sql - 副本"
docker compose build
docker save smart-qa:latest mysql:5.7 -o smart-qa-images.tar

# 传到服务器
scp smart-qa-images.tar root@<华为服务器IP>:/opt/
```

```bash
# 服务器：导入新镜像 + 重启应用（mysql 不用动，数据不丢）
docker load -i /opt/smart-qa-images.tar
cd /opt/smart-qa
docker compose -f docker-compose.offline.yml up -d
```

> 只改 `.env`（如换 LLM 地址）不用重新打包镜像，传新 `.env` 后 `restart smart-qa` 即可。
