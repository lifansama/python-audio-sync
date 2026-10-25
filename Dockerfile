FROM python:slim

WORKDIR /app

# 安装项目依赖（加上 uvicorn[standard] 保证 websocket 完美支持）
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" uvloop python-multipart

# 复制项目代码
COPY . /app

# 暴露后端端口
EXPOSE 8000

# 修正：加上 --host 和 --port
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--loop", "uvloop", "--ws-ping-interval", "20", "--ws-ping-timeout", "30"]
