# model_server：BGE-M3 嵌入（GPU）。基础镜像含 CUDA + torch。
# 仅在 docker compose --profile gpu up 时构建。
FROM pytorch/pytorch:2.3.1-cuda12.1.0-cudnn8-runtime

ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/models \
    HF_ENDPOINT=https://hf-mirror.com \
    TRANSFORMERS_OFFLINE=0

WORKDIR /app

# 国内 PyPI 镜像安装依赖
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    sentence-transformers>=3.0 fastapi uvicorn[standard]

COPY . /app

EXPOSE 8100
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8100"]
