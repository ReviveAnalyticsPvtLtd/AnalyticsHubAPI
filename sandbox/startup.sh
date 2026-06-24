#!/bin/bash

cp /app/sandbox/supervisord.conf /etc/supervisord.conf

exec /usr/bin/supervisord -c /etc/supervisord.conf
