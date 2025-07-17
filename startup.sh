#!/bin/bash

# Copy supervisord config to correct path
cp /app/supervisord.conf /etc/supervisord.conf

# Start supervisord
exec /usr/bin/supervisord -c /etc/supervisord.conf