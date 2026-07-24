#!/bin/sh
set -e

cd /app/dentai

echo "✅ Running migrations..."
python manage.py migrate --noinput

echo "✅ Collecting static files..."
python manage.py collectstatic --noinput

echo "✅ Starting SMS2 Reminder Scheduler..."
python appointments/scheduler.py 

echo "✅ Starting Gunicorn..."
exec gunicorn dentai_core.wsgi:application \
    --bind 0.0.0.0:$PORT \
    --workers 1 \
    --access-logfile - \
    --error-logfile -
