FROM python:3.10-slim

WORKDIR /app

COPY . .

RUN apt-get update && apt-get install -y \
    supervisor \
    libgomp1 \
    coreutils \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*

RUN pip install uv

RUN uv sync

RUN chmod +x /app/startup.sh

# Raise cgroup PID limit so gunicorn workers + anyio threads + celery don't exhaust it.
# Default Docker pids.max is often 4096; we need headroom for 8 workers × (8 threads + 2 sandbox) + celery.
RUN echo 12288 > /sys/fs/cgroup/pids.max 2>/dev/null || true

EXPOSE 7860

CMD ["/app/startup.sh"]