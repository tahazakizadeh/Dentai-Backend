# payments/signals.py

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.db.models import Sum

from appointments.models import Appointment
from accounts.models import Patient
from .models import Payment, PatientBalance


@receiver(post_save, sender=Appointment)
def create_payment_on_completion(sender, instance, **kwargs):
    """
    اگر نوبت completed شد، برایش یک Payment (صفر تومانی) بساز
    که منشی بعداً مبلغ را وارد کند.
    """
    if instance.status == 'completed':
        Payment.objects.get_or_create(
            appointment=instance,
            defaults={
                "patient": instance.patient,
                "amount": 0
            }
        )


def _recalculate_patient_paid(patient: Patient):
    """
    جمع کل پرداخت‌های بیمار را دوباره حساب می‌کند.
    """
    if not patient or not Patient.objects.filter(id=patient.id).exists():
        return
    total = (
        Payment.objects
        .filter(patient=patient)
        .aggregate(s=Sum('amount'))
        .get('s') or 0
    )

    balance, _ = PatientBalance.objects.get_or_create(patient=patient)
    balance.total_paid = total
    balance.save()


@receiver(post_save, sender=Payment)
def update_balance_on_payment_save(sender, instance, **kwargs):
    # اگر به هر دلیلی payment بدون patient ساخته شد، اینجا سیستم کرش نکنه
    if not instance.patient_id:
        return
    _recalculate_patient_paid(instance.patient)

@receiver(post_delete, sender=Payment)
def update_balance_on_payment_delete(sender, instance, **kwargs):
    if not instance.patient_id:
        return
    _recalculate_patient_paid(instance.patient)
