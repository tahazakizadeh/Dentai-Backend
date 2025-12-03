FROM python:3.13-slim


ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# پکیج‌های سیستمی لازم
RUN apt-get update && apt-get install -y \
    build-essential libpq-dev curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# نیازمندی‌ها
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install gunicorn python-dotenv

# کدها
COPY dentai /app/dentai

# استاتیک/مدیا
RUN mkdir -p /vol/static /vol/media && chmod -R 777 /vol

# کپی اسکریپت ورود و اجرایی کردنش
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8000
CMD ["/app/entrypoint.sh"]

