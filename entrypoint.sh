#!/bin/sh
set -e

cd /app/dentai

python manage.py migrate --noinput
python manage.py collectstatic --noinput

gunicorn dentai_core.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers 3 \
  --access-logfile '-' \
  --error-logfile '-'
