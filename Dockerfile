FROM python:3.10-slim

WORKDIR /app

COPY . .

# Install OS dependencies
RUN apt-get update && apt-get install -y \
    supervisor \
    libgomp1 \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Make startup script executable
RUN chmod +x /app/startup.sh

# Expose FastAPI port
EXPOSE 7860

# Run startup script
CMD ["/app/startup.sh"]
