FROM python:3.10-slim

ENV UV_TOOL_BIN_DIR=/usr/local/bin

ENV PATH="/usr/local/bin:${PATH}"

WORKDIR /app

COPY . .

COPY supervisord.conf /etc/supervisord.conf

RUN apt-get update && apt-get install -y \
    supervisor \
    libgomp1 \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

RUN pip install uv

RUN uv sync

RUN chmod +x /app/startup.sh

EXPOSE 7860

CMD ["/app/startup.sh"]