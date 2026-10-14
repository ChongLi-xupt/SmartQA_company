# 智慧问数系统（Smart QA）运行镜像
# 项目要求 Python >= 3.10，这里用 3.11 slim 精简镜像
FROM python:3.11-slim

# 不生成 .pyc；stdout/stderr 无缓冲，日志实时可见
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先装依赖，利用 Docker 层缓存（依赖不变时无需重建）
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝源码（.dockerignore 已排除 .env/.git/缓存等）
COPY . .

EXPOSE 5000

# 单 worker 启动：会话历史用内存后端（不用 Redis），多 worker 才需要 Redis 共享
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5000"]