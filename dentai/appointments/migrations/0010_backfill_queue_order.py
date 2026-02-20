from django.db import migrations
from django.db.models import Case, When, Value, IntegerField

QUEUE_STEP = 10

def forwards(apps, schema_editor):
    Appointment = apps.get_model("appointments", "Appointment")

    qs = (Appointment.objects
          .filter(logical_status="active")
          .annotate(
              status_order=Case(
                  When(status='in_progress', then=Value(1)),
                  When(status='waiting_room', then=Value(2)),
                  When(status='waiting', then=Value(3)),
                  When(status='absent', then=Value(4)),
                  When(status='completed', then=Value(5)),
                  When(status='cancelled', then=Value(6)),
                  default=Value(7),
                  output_field=IntegerField(),
              )
          )
          .order_by("date", "status_order", "predicted_start_time", "id"))

    current_date = None
    i = 0
    for ap in qs.iterator():
        if ap.date != current_date:
            current_date = ap.date
            i = 0
        ap.queue_order = (i + 1) * QUEUE_STEP
        ap.save(update_fields=["queue_order"])
        i += 1

def backwards(apps, schema_editor):
    Appointment = apps.get_model("appointments", "Appointment")
    Appointment.objects.update(queue_order=None)

class Migration(migrations.Migration):
    dependencies = [
        ("appointments", "0009_appointment_queue_order"),
    ]
    operations = [
        migrations.RunPython(forwards, backwards),
    ]
