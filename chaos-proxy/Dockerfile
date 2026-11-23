# Chaos proxy: passthrough to the simulator that can corrupt responses for the F6 "malformed data" drill.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt
COPY proxy.py /app/proxy.py
RUN useradd -u 10001 -m app
USER app
EXPOSE 8001
CMD ["uvicorn", "proxy:app", "--host", "0.0.0.0", "--port", "8001", "--no-access-log"]
