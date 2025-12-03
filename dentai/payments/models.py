# payments/models.py

from django.db import models
from django.db.models import Sum
from appointments.models import Appointment
from accounts.models import Patient   


class PatientBalance(models.Model):
    patient = models.OneToOneField(Patient, on_delete=models.CASCADE)
    # مبلغ کل درمان که منشی تعیین می‌کند – می‌تواند خالی باشد
    total_debt = models.PositiveBigIntegerField(null=True, blank=True)
    total_paid = models.PositiveBigIntegerField(default=0)

    @property
    def remaining(self):
        if self.total_debt is None:
            return None
        return self.total_debt - self.total_paid

    def __str__(self):
        return f"Balance for {self.patient}"


class Payment(models.Model):
    # Payment به خود patient وصل است
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='payments')

    # اگر پرداخت مربوط به یک نوبت خاص باشد
    appointment = models.ForeignKey(
        Appointment,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='payments'
    )

    # مبلغ تراکنش
    amount = models.PositiveBigIntegerField(default=0)

    PAYMENT_METHOD_CHOICES = (
        ('pos', 'کارت‌خوان مطب'),
        ('gateway', 'درگاه آنلاین'),
    )
    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        blank=True
    )

    tracking_code = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        base = f"Payment {self.amount} for {self.patient}"
        if self.appointment_id:
            base += f" (appointment {self.appointment_id})"
        return base
