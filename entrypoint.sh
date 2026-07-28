#!/bin/sh
set -ex

cd /app/dentai
echo "PORT=$PORT"
echo "✅ Running migrations..."
python manage.py migrate --noinput

echo "✅ Collecting static files..."
python manage.py collectstatic --noinput

echo "✅ Starting SMS2 Reminder Scheduler..."
python appointments/scheduler.py 

echo "Before gunicorn"
echo "✅ Starting Gunicorn..."
exec gunicorn dentai_core.wsgi:application \
    --bind 0.0.0.0:${PORT:-8000}  \
    --workers 2 \
    --log-level debug \
    --access-logfile - \
    --error-logfile -
