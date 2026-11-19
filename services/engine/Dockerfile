# Build context = repo root:  docker build -f services/engine/Dockerfile .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY services/engine/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt
COPY libs/fsip_common /opt/fsip_common
RUN pip install /opt/fsip_common
# sanity check at build time: the bundled CBC solver must work inside the slim image
RUN python -c "import pulp; p=pulp.LpProblem('t', pulp.LpMinimize); x=pulp.LpVariable('x', 0, 10); p+=x; p+=x>=3; assert pulp.LpStatus[p.solve(pulp.PULP_CBC_CMD(msg=False))]=='Optimal'"
COPY services/engine/app /app/app
ARG APP_VERSION=dev
ARG GIT_SHA=unknown
ENV APP_VERSION=${APP_VERSION} GIT_SHA=${GIT_SHA}
RUN useradd -u 10001 -m app && mkdir -p /data && chown app /data
USER app
EXPOSE 8100
HEALTHCHECK --interval=10s --timeout=3s --retries=6 CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8100/health/live',timeout=2)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100", "--no-access-log"]
