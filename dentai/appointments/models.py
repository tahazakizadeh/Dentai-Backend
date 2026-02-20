# appointments/models.py

from django.db import models
from accounts.models import Patient


class Appointment(models.Model):
    STATUS_CHOICES = [
        ('waiting', 'در انتظار'),
        ('waiting_room', 'در انتظار در مطب'),
        ('in_progress', 'در حال درمان'),
        ('completed', 'تکمیل شده'),
        ('cancelled', 'لغو شده'),
        ('absent', 'عدم حضور'),
    ]

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE)
    treatment_type = models.CharField(max_length=100)

    # ⬇️ خروجی زمان‌بندی (به‌وسیله normalize_day_queue تنظیم می‌شود)
    predicted_start_time = models.TimeField()
    predicted_duration = models.IntegerField()  # دقیقه

    # ⬇️ منبع حقیقت ترتیب صف (waiting / waiting_room)
    queue_order = models.IntegerField(null=True, blank=True, db_index=True)

    actual_start_time = models.TimeField(null=True, blank=True)
    actual_duration = models.IntegerField(null=True, blank=True)  # دقیقه

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='waiting')
    date = models.DateField()
    doctor_note = models.TextField(blank=True, null=True)

    real_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='waiting')
    logical_status = models.CharField(
        max_length=20,
        choices=[
            ('active', 'Active'),
            ('skipped', 'Skipped'),
        ],
        default='active'
    )
    skip_reason = models.CharField(max_length=100, null=True, blank=True)

    sms2_sent = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.patient} - {self.treatment_type} ({self.status})"
