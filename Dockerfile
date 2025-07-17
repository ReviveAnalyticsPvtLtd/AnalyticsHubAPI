FROM python:3.10-slim

WORKDIR /app

COPY . .

RUN apt-get update && apt-get install -y \
    supervisor \
    libgomp1 \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

COPY supervisord.conf /etc/supervisord.conf

RUN pip install uv

RUN uv add -r requirements.txt

RUN chmod +x /app/startup.sh

EXPOSE 7860

CMD ["/app/startup.sh"]