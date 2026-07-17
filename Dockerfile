FROM python:3.13-slim

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

EXPOSE 7860

CMD ["/app/startup.sh"]