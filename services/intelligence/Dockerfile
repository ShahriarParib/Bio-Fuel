# Build context = repo root:  docker build -f services/intelligence/Dockerfile .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# lightgbm needs the OpenMP runtime
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY services/intelligence/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt
COPY libs/fsip_common /opt/fsip_common
RUN pip install /opt/fsip_common
RUN python -c "import lightgbm, sklearn, pandas; print('ml ok', lightgbm.__version__)"
COPY services/intelligence/app /app/app
ARG APP_VERSION=dev
ARG GIT_SHA=unknown
ENV APP_VERSION=${APP_VERSION} GIT_SHA=${GIT_SHA}
RUN useradd -u 10001 -m app && mkdir -p /app/artifacts && chown -R app /app/artifacts
USER app
EXPOSE 8200
HEALTHCHECK --interval=10s --timeout=3s --retries=6 CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8200/health/live',timeout=2)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8200", "--no-access-log"]
