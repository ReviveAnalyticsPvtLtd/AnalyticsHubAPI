FROM python:3.10-slim

WORKDIR /app

COPY . .

COPY /app/supervisord.conf /etc/supervisord.conf

RUN apt-get update && apt-get install -y \
    supervisor \
    libgomp1 \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

RUN pip install uv

RUN uv venv .venv && . .venv/bin/activate && uv sync

RUN chmod +x /app/startup.sh

EXPOSE 7860

CMD ["/app/startup.sh"]