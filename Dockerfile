# DataFoundry 服务镜像(通用:本机 docker run / Hugging Face Space / 任意容器平台)
FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml ./
COPY datafoundry ./datafoundry
RUN pip install --no-cache-dir .

# HF Space 以非 root(uid 1000)运行,数据目录需可写;/data 挂持久盘时自动沿用
RUN mkdir -p /data && chmod 777 /data
ENV DATAFOUNDRY_HOME=/data

EXPOSE 8321
# 引导管理员经环境变量注入(平台 secret):DATAFOUNDRY_BOOTSTRAP_ADMIN="用户名:口令"
CMD ["sh", "-c", "datafoundry serve --host 0.0.0.0 --port ${PORT:-8321}"]
