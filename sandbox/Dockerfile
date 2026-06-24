FROM python:3.10-slim

WORKDIR /app/sandbox

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN apt-get update && apt-get install -y \
    supervisor \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

RUN chmod +x /app/sandbox/startup.sh

EXPOSE 8000

CMD ["/app/sandbox/startup.sh"]
