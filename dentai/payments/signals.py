# payments/signals.py

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.db.models import Sum
from django.db import transaction

from appointments.models import Appointment
from accounts.models import Patient
from .models import Payment, PatientBalance


@receiver(post_save, sender=Appointment)
def create_payment_on_completion(sender, instance, **kwargs):
    """
    اگر نوبت completed شد، برایش یک Payment (صفر تومانی) بساز
    که منشی بعداً مبلغ را وارد کند.
    """
    if instance.status == 'completed' and instance.patient_id:
        Payment.objects.get_or_create(
            appointment=instance,
            defaults={
                "patient": instance.patient,
                "amount": 0
            }
        )



def _safe_recalculate_after_commit(patient_id: int):
    """
    این تابع فقط بعد از commit دیتابیس اجرا می‌شود
    بنابراین race condition کامل حذف می‌شود.
    """

    # اگر Patient بعد از commit وجود ندارد → هیچ کاری نکن
    if not Patient.objects.filter(id=patient_id).exists():
        return

    total = (
        Payment.objects
        .filter(patient_id=patient_id)
        .aggregate(s=Sum('amount'))
        .get('s') or 0
    )

    balance, _ = PatientBalance.objects.get_or_create(patient_id=patient_id)
    balance.total_paid = total
    balance.save()


@receiver(post_save, sender=Payment)
def update_balance_on_payment_save(sender, instance, **kwargs):
    # اگر به هر دلیلی payment بدون patient ساخته شد، اینجا سیستم کرش نکنه
    if not instance.patient_id:
        return

    patient_id = instance.patient_id

    transaction.on_commit(
        lambda: _safe_recalculate_after_commit(patient_id)
    )

@receiver(post_delete, sender=Payment)
def update_balance_on_payment_delete(sender, instance, **kwargs):
    if not instance.patient_id:
        return

    patient_id = instance.patient_id

    transaction.on_commit(
        lambda: _safe_recalculate_after_commit(patient_id)
    )
