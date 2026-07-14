#!/bin/bash
set -e

# Copy supervisord config to correct path
cp /app/supervisord.conf /etc/supervisord.conf

# Raise pids limit if writable (Docker cgroup v2)
echo 12288 > /sys/fs/cgroup/pids.max 2>/dev/null || true

# Start supervisord with a watchdog loop — if supervisord ever exits,
# restart it immediately. The server must NEVER be down.
while true; do
    /usr/bin/supervisord -c /etc/supervisord.conf
    echo "[watchdog] supervisord exited with code $?, restarting in 2s..." >&2
    sleep 2
done