# UniLink 扫码登录服务
# 构建: docker build -t unilink-auth .
FROM python:3.12-slim

# 只装运行期需要的东西；cryptography 有预编译 wheel，无需编译工具链
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./

# 以非 root 运行。keys/ 需要可写（首次启动生成 RSA 签名私钥），
# 因此目录归属交给该用户，并在编排文件里挂成持久卷。
RUN useradd --system --create-home --uid 10001 unilink \
 && mkdir -p /app/keys \
 && chown -R unilink:unilink /app
USER unilink

EXPOSE 8790

# 容器内固定监听 0.0.0.0，对外暴露与否由 compose 的端口映射决定
ENV UNILINK_HOST=0.0.0.0 \
    UNILINK_PORT=8790

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8790/healthz',timeout=4).status==200 else 1)"

CMD ["python", "app.py"]
