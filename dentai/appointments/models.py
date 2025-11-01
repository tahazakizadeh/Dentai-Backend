
# appointments/models.py

from django.db import models
from accounts.models import Patient

class Appointment(models.Model):
    STATUS_CHOICES = [
        ('waiting', 'در انتظار'),
        ('in_progress', 'در حال درمان'),
        ('completed', 'تکمیل شده'),
        ('cancelled', 'لغو شده'),
        ('absent', 'عدم حضور'),
    ]

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE)
    treatment_type = models.CharField(max_length=100)
    predicted_start_time = models.TimeField()
    predicted_duration = models.IntegerField()  # به دقیقه
    actual_start_time = models.TimeField(null=True, blank=True)
    actual_duration = models.IntegerField(null=True, blank=True)  # دقیقه
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='waiting')
    date = models.DateField()
    doctor_note = models.TextField(blank=True, null=True)  # یادداشت پزشک
    real_status = models.CharField(max_length=20, default='waiting')  
    logical_status = models.CharField(max_length=20, default='active')
    skip_reason = models.CharField(max_length=100, null=True, blank=True)

    def __str__(self):
        return f"{self.patient} - {self.treatment_type} ({self.status})"
