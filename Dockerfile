FROM python:3.10-slim

WORKDIR /app

COPY . .

RUN apt-get update && apt-get install -y supervisor

RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 7860

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisord.conf"]
