FROM python:3.10-slim

WORKDIR /app

# 安装项目依赖
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" python-multipart

# 复制项目代码
COPY . /app

# 暴露后端端口
EXPOSE 8000

# 启动服务
CMD ["uvicorn", "server:app", "host", "0.0.0.0", "port", "8000"]