FROM python:3.10-slim

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

RUN echo "CELERY path: $(uv which celery)" && \
    echo "GUNICORN path: $(uv which gunicorn)"

RUN chmod +x /app/startup.sh

EXPOSE 7860

CMD ["/app/startup.sh"]