import time
import logging
from django import setup
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
setup()

from appointments.views import check_and_send_reminders

logger = logging.getLogger(__name__)

def run_scheduler():
    logger.info("✅ SMS2 Reminder Scheduler Started...")
    while True:
        try:
            check_and_send_reminders()
        except Exception as e:
            logger.error(f"Scheduler error: {e}")

        # هر 60 ثانیه یک‌بار
        time.sleep(60)
