#!/bin/sh
set -eu
umask 077
mkdir -p /data/model-cache
exec python /app/app.py
