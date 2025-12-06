import time
import logging
import os
import sys
import django

# ✅ اضافه کردن ریشه پروژه به PYTHONPATH
BASE_DIR = "/app/dentai"
sys.path.append(BASE_DIR)

# ✅ تنظیمات Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dentai_core.settings')
django.setup()

from appointments.views import check_and_send_reminders

logger = logging.getLogger(__name__)

def run_scheduler():
    logger.info("✅ SMS2 Reminder Scheduler Started...")
    while True:
        try:
            check_and_send_reminders()
        except Exception as e:
            logger.error(f"Scheduler error: {e}")

        time.sleep(60)


if __name__ == "__main__":
    run_scheduler()
