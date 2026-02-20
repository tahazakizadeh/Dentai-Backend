from kavenegar import KavenegarAPI, APIException, HTTPException
from django.db.models import Case, When, Value, IntegerField, Count
from rest_framework import viewsets, status as http_status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.generics import get_object_or_404
from datetime import datetime, timedelta
from django.utils.timezone import now
from fuzzywuzzy import process
from django.conf import settings
import jdatetime, time, pickle, logging, numpy as np
from django.utils import timezone
import threading

from django.db import transaction

from .models import Appointment
from accounts.models import Patient
from .serializers import (
    BatchAppointmentInputSerializer,
    AppointmentOutputSerializer,
    AppointmentSerializer,
    AppointmentDoctorNoteUpdateSerializer,
)

logger = logging.getLogger(__name__)

QUEUE_STEP = 10  # فاصله‌ی queue_order ها برای insert راحت‌تر

# -------------------------------
# 📦 مدل ML و Encoder بارگذاری
# -------------------------------
with open('ai/duration_predictor_model_v2.pkl', 'rb') as f:
    model = pickle.load(f)

with open('ai/treatment_encoder_v2.pkl', 'rb') as f:
    encoder = pickle.load(f)


# -------------------------------
# 🕒 توابع کمکی
# -------------------------------
def to_persian_date_str(date_str):
    gregorian_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    persian_date = jdatetime.date.fromgregorian(date=gregorian_date)
    return persian_date.strftime("%Y-%m-%d")


def send_appointment_sms(patient, date, time):
    try:
        api = KavenegarAPI(settings.KAVENEGAR_API_KEY)
        phone = "+98" + patient.user.phone_number[1:]
        params = {
            'sender': '2000660110',
            'receptor': phone,
            'token': "مراجع",
            'token2': to_persian_date_str(str(date)),
            'token3': str(time),
            'template': 'SMS1',
            'type': 'sms'
        }
        api.verify_lookup(params)
        return True
    except Exception as e:
        logger.warning(f"SMS sending failed: {e}")
        return False


def send_reminder_sms(appointment):
    """
    ارسال SMS یادآوری یک ساعت قبل از نوبت
    Template: SMS2
    Token: زمان نوبت (HH:MM)
    """
    try:
        api = KavenegarAPI(settings.KAVENEGAR_API_KEY)
        patient = appointment.patient
        phone = "+98" + patient.user.phone_number[1:]

        time_str = appointment.predicted_start_time.strftime("%H:%M")

        params = {
            'sender': '2000660110',
            'receptor': phone,
            'token': time_str,
            'template': 'SMS2',
            'type': 'sms',
        }

        api.verify_lookup(params)

        appointment.sms2_sent = True
        appointment.save(update_fields=["sms2_sent"])

        return True

    except Exception as e:
        logger.warning(f"Reminder SMS (SMS2) failed: {e}")
        return False


def predict_duration(treatment_type):
    treatment_type = treatment_type.strip()
    if treatment_type not in encoder.classes_:
        best_match, score = process.extractOne(treatment_type, encoder.classes_)
        if score > 80:
            treatment_type = best_match
        else:
            raise ValueError(f"Unknown treatment type: {treatment_type}")
    encoded = encoder.transform([treatment_type])[0]
    return round(model.predict([[840, encoded]])[0], 2)


# -------------------------------
# ✅ نرمال‌سازی صف روز (هسته‌ی سیستم)
# -------------------------------
def normalize_day_queue(date, now_dt=None):
    """
    منبع حقیقت ترتیب صف = queue_order.
    predicted_start_time فقط خروجی زمان‌بندی است.

    قواعد:
      - فقط نوبت‌های active در صف (waiting / waiting_room) زمان‌بندی می‌شوند.
      - مبنا = max(now, پایان in_progress اگر وجود دارد)
      - صف بر اساس queue_order مرتب می‌شود (fallback: predicted_start_time, id)
    """
    now_dt = now_dt or timezone.now()

    with transaction.atomic():
        day_qs = (Appointment.objects
                  .select_for_update()
                  .filter(date=date, logical_status='active'))

        in_prog = (day_qs.filter(status='in_progress')
                   .order_by('-actual_start_time', '-id')
                   .first())

        base_dt = now_dt

        if in_prog:
            if not in_prog.actual_start_time:
                in_prog.actual_start_time = now_dt.time().replace(second=0, microsecond=0)
                in_prog.save(update_fields=['actual_start_time'])

            start_dt = datetime.combine(date, in_prog.actual_start_time)
            end_dt = start_dt + timedelta(minutes=int(in_prog.predicted_duration or 0))
            if end_dt > base_dt:
                base_dt = end_dt

        queue = list(
            day_qs.filter(status__in=['waiting_room', 'waiting'])
                  .order_by('queue_order', 'predicted_start_time', 'id')
        )

        cur = base_dt
        for ap in queue:
            ap.predicted_start_time = cur.time().replace(second=0, microsecond=0)
            cur += timedelta(minutes=int(ap.predicted_duration or 0))

        if queue:
            Appointment.objects.bulk_update(queue, ['predicted_start_time'])


# -------------------------------
# 🔁 (Legacy) بازسازی زمان‌بندی روز
# -------------------------------
def recalculate_schedule(date):
    """قدیمی: فقط بر اساس predicted_start_time دوباره می‌چیند.
    در سیستم جدید ترجیحاً از normalize_day_queue استفاده کن."""
    appointments = Appointment.objects.filter(
        date=date, logical_status='active'
    ).order_by('predicted_start_time')

    if not appointments.exists():
        return

    current_time = datetime.combine(date, appointments.first().predicted_start_time)

    for appt in appointments:
        appt.predicted_start_time = current_time.time().replace(microsecond=0)
        current_time += timedelta(minutes=appt.predicted_duration)
        appt.save(update_fields=['predicted_start_time'])


def check_and_send_reminders():
    now_dt = timezone.now()
    target_start = now_dt + timedelta(hours=1)
    target_end = now_dt + timedelta(hours=1, minutes=1)
    today = now_dt.date()

    appointments = Appointment.objects.filter(
        logical_status='active',
        status__in=['waiting', 'waiting_room'],
        sms2_sent=False,
        date=today,
        predicted_start_time__gte=target_start.time(),
        predicted_start_time__lte=target_end.time(),
    )

    for appt in appointments:
        send_reminder_sms(appt)


# -------------------------------
# 📋 ViewSet اصلی
# -------------------------------
class AppointmentViewSet(viewsets.ModelViewSet):
    queryset = Appointment.objects.all().order_by("id")
    serializer_class = AppointmentSerializer

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        appointment_date = instance.date
        instance.delete()

        # ✅ بعد از حذف، صف همان روز را یک‌دست کن
        normalize_day_queue(appointment_date, now_dt=timezone.now())

        return Response(
            {"message": "نوبت با موفقیت حذف شد و زمان‌بندی مجدد انجام شد."},
            status=http_status.HTTP_200_OK
        )

    def get(self, request):
        appointments = Appointment.objects.annotate(
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
        ).order_by('status_order', 'queue_order', 'predicted_start_time', 'id')

        serializer = AppointmentSerializer(appointments, many=True)
        return Response(serializer.data)


# -------------------------------
# 🚀 افزودن گروهی نوبت‌ها
# -------------------------------
class BatchAppointmentAddView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = BatchAppointmentInputSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        date = serializer.validated_data['date']
        start_time = serializer.validated_data['start_time']
        appointments_data = serializer.validated_data['appointments']

        scheduled = []
        current_time = datetime.combine(date, start_time)

        # شروع queue_order از بیشترین مقدار موجود + step
        last_q = (Appointment.objects
                  .filter(date=date, logical_status='active', status__in=['waiting', 'waiting_room'])
                  .order_by('-queue_order')
                  .values_list('queue_order', flat=True)
                  .first())
        if last_q is None:
            last_q = 0
        next_q = last_q + QUEUE_STEP

        for item in appointments_data:
            patient_id = int(item.get('patient_id'))
            treatment_type = item.get('treatment_type')

            try:
                patient = Patient.objects.get(id=patient_id)
            except Patient.DoesNotExist:
                continue

            duration = predict_duration(treatment_type)

            appt = Appointment.objects.create(
                patient=patient,
                treatment_type=treatment_type,
                predicted_start_time=current_time.time().replace(second=0, microsecond=0),
                predicted_duration=int(duration),
                real_status='waiting',
                status='waiting',
                logical_status='active',
                date=date,
                queue_order=next_q,
            )

            threading.Thread(
                target=send_appointment_sms,
                args=(patient, date.strftime('%Y-%m-%d'), current_time.time()),
                daemon=True
            ).start()

            current_time += timedelta(minutes=int(duration))
            next_q += QUEUE_STEP
            scheduled.append(appt)

        # ✅ زمان‌ها را یک‌دست کن
        normalize_day_queue(date, now_dt=timezone.now())

        output = AppointmentOutputSerializer(scheduled, many=True)
        return Response({"status": "generated", "scheduled": output.data})


#-------------------------------------
#    جابه‌جایی ترتیب نوبت‌های یک روز:
#    - UI تغییر نکند:
#      payload: {"date":"YYYY-MM-DD","appointments":[{"id":...},...]} (کل لیست)
#    - اعتبارسنجی روی همه نوبت‌های همان تاریخ (active + skipped)
#    - reorder واقعی فقط روی waiting/waiting_room های active
#--------------------------------------
class AppointmentReorderView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        items = request.data.get("appointments")
        if not isinstance(items, list) or not items:
            return Response(
                {"error": "فیلد appointments الزامی است و باید یک لیست باشد."},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        date_str = request.data.get("date")
        if not date_str:
            return Response({"error": "فیلد date الزامی است."}, status=http_status.HTTP_400_BAD_REQUEST)
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return Response({"error": "فرمت date باید YYYY-MM-DD باشد."}, status=http_status.HTTP_400_BAD_REQUEST)

        try:
            new_ids = [int(item["id"]) for item in items]
        except (KeyError, TypeError, ValueError):
            return Response(
                {"error": "هر آیتم در appointments باید شامل فیلد عددی id باشد."},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        # ✅ همه نوبت‌های این تاریخ (active + skipped) با همان ترتیب داشبورد
        original_qs = (Appointment.objects
            .select_for_update()
            .filter(date=target_date, logical_status__in=['active', 'skipped'])
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
            .order_by('status_order', 'queue_order', 'predicted_start_time', 'id')
        )

        original_ids = [a.id for a in original_qs]
        if not original_ids:
            return Response(
                {"error": "برای این تاریخ نوبتی یافت نشد."},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        # UI کل لیست را می‌فرستد → باید دقیقاً همان باشد
        if len(original_ids) != len(new_ids) or set(original_ids) != set(new_ids):
            return Response(
                {
                    "error": "لیست appointments باید دقیقاً شامل همه نوبت‌های همین تاریخ باشد.",
                    "expected_ids": original_ids,
                },
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        id_to_appt = {a.id: a for a in original_qs}

        # بلوک‌های وضعیت از ترتیب اصلی
        blocks = []
        current_status = None
        current_ids = []

        for ap in original_qs:
            if ap.status != current_status:
                if current_status is not None:
                    blocks.append((current_status, current_ids))
                current_status = ap.status
                current_ids = [ap.id]
            else:
                current_ids.append(ap.id)
        if current_status is not None:
            blocks.append((current_status, current_ids))

        # چک ساختار بلوک‌ها (عدم جابجایی بین کتگوری‌ها)
        idx = 0
        for status, orig_block_ids in blocks:
            block_len = len(orig_block_ids)
            new_block_ids = new_ids[idx:idx + block_len]

            if len(new_block_ids) != block_len:
                return Response(
                    {"error": "ساختار بلوک‌های وضعیت در داده‌ی ارسالی خراب شده است."},
                    status=http_status.HTTP_400_BAD_REQUEST,
                )

            for ap_id in new_block_ids:
                ap = id_to_appt.get(ap_id)
                if ap is None:
                    return Response(
                        {"error": f"نوبت با آیدی {ap_id} یافت نشد."},
                        status=http_status.HTTP_400_BAD_REQUEST,
                    )
                if ap.status != status:
                    return Response(
                        {
                            "error": "جابجایی بین کتگوری‌ها مجاز نیست.",
                            "detail": f"نوبت با آیدی {ap_id} در وضعیت {ap.status} است "
                                      f"و نمی‌تواند در بلوک وضعیت {status} قرار بگیرد.",
                        },
                        status=http_status.HTTP_400_BAD_REQUEST,
                    )

            if status not in ['waiting', 'waiting_room'] and new_block_ids != orig_block_ids:
                return Response(
                    {
                        "error": "فقط نوبت‌های در انتظار (waiting و waiting_room) قابل جابجایی هستند.",
                        "detail": f"نوبت‌های وضعیت {status} نباید جابجا شوند.",
                    },
                    status=http_status.HTTP_400_BAD_REQUEST,
                )

            idx += block_len

        # ✅ reorder واقعی فقط روی waiting/waiting_room های ACTIVE
        new_waiting_ids = [
            pk for pk in new_ids
            if id_to_appt[pk].logical_status == 'active'
            and id_to_appt[pk].status in ['waiting', 'waiting_room']
        ]

        if not new_waiting_ids:
            return Response({"status": "nothing_to_reorder"}, status=http_status.HTTP_200_OK)

        base = 10
        to_update = []
        for ap_id in new_waiting_ids:
            ap = id_to_appt[ap_id]
            ap.queue_order = base
            base += QUEUE_STEP
            to_update.append(ap)

        Appointment.objects.bulk_update(to_update, ['queue_order'])

        normalize_day_queue(target_date, now_dt=timezone.now())

        return Response({"status": "reordered"}, status=http_status.HTTP_200_OK)


#--------------------------------
# منطق اضافه کردن بیمار اتفاقی (Order-based)
#--------------------------------
class RandomAppointmentAddView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        patient_id = request.data.get("patient_id")
        treatment_type = request.data.get("treatment_type")

        if not patient_id or not treatment_type:
            return Response(
                {"error": "patient_id و treatment_type الزامی هستند"},
                status=http_status.HTTP_400_BAD_REQUEST
            )

        try:
            patient = Patient.objects.get(id=patient_id)
        except Patient.DoesNotExist:
            return Response({"error": "بیمار یافت نشد"}, status=404)

        today = timezone.now().date()

        try:
            duration = predict_duration(treatment_type)
        except Exception as e:
            return Response({"error": str(e)}, status=400)

        # کمترین order صف فعلی
        min_q = (Appointment.objects
                 .filter(date=today, logical_status='active', status__in=['waiting_room', 'waiting'])
                 .order_by('queue_order')
                 .values_list('queue_order', flat=True)
                 .first())
        if min_q is None:
            min_q = 10

        now_time = timezone.now().time().replace(second=0, microsecond=0)

        new_appt = Appointment.objects.create(
            patient=patient,
            treatment_type=treatment_type,
            predicted_start_time=now_time,  # خروجی نهایی با normalize ست می‌شود
            predicted_duration=int(duration),
            date=today,
            status='waiting_room',
            real_status='waiting_room',
            logical_status='active',
            queue_order=min_q - QUEUE_STEP,
        )

        threading.Thread(
            target=send_appointment_sms,
            args=(patient, today, now_time),
            daemon=True
        ).start()

        normalize_day_queue(today, now_dt=timezone.now())

        output = AppointmentOutputSerializer(new_appt).data
        return Response({"status": "random_added", "appointment": output})


# -------------------------------
# 🔄 تغییر وضعیت نوبت‌ها (Normalize محور)
# -------------------------------
class AppointmentStatusUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, pk):
        appointment = get_object_or_404(Appointment, pk=pk)
        new_status = request.data.get("status")
        if not new_status:
            return Response({"error": "status is required"}, status=http_status.HTTP_400_BAD_REQUEST)

        original_logical = appointment.logical_status

        now_dt = timezone.now()
        now_time = now_dt.time().replace(second=0, microsecond=0)

        # هم real_status و هم status
        appointment.real_status = new_status
        appointment.status = new_status

        # ========================
        # 1) رفتن به in_progress
        # ========================
        if new_status == "in_progress":
            MAX_CONCURRENT = 1
            cnt = Appointment.objects.filter(
                date=appointment.date,
                logical_status='active',
                status='in_progress'
            ).exclude(pk=appointment.pk).count()

            if cnt >= MAX_CONCURRENT:
                return Response(
                    {"error": "در حال حاضر یک درمان در حال انجام است."},
                    status=http_status.HTTP_409_CONFLICT
                )

            appointment.actual_start_time = now_time
            appointment.predicted_start_time = now_time  # sync
            appointment.logical_status = "active"
            appointment.save(update_fields=["actual_start_time", "predicted_start_time", "status", "real_status", "logical_status"])

            normalize_day_queue(appointment.date, now_dt=now_dt)
            return Response({"status": "in_progress_started"}, status=http_status.HTTP_200_OK)

        # ========================
        # 2) رفتن به waiting_room (از وسط لیست هم بزنند، خودش می‌آید اول صف)
        # ========================
        if new_status == "waiting_room":
            today = appointment.date
            min_q = (Appointment.objects
                     .filter(date=today, logical_status='active', status__in=['waiting_room', 'waiting'])
                     .exclude(pk=appointment.pk)
                     .order_by('queue_order')
                     .values_list('queue_order', flat=True)
                     .first())
            if min_q is None:
                min_q = 10

            appointment.queue_order = min_q - QUEUE_STEP
            appointment.logical_status = "active"
            appointment.save(update_fields=["queue_order", "status", "real_status", "logical_status"])

            normalize_day_queue(today, now_dt=now_dt)
            return Response({"status": "waiting_room_set"}, status=http_status.HTTP_200_OK)

        # ========================
        # 3) رفتن به completed
        # ========================
        if new_status == "completed":
            if not appointment.actual_start_time:
                appointment.actual_start_time = now_time

            start_dt = datetime.combine(appointment.date, appointment.actual_start_time)
            end_dt = now_dt
            actual_duration = max(0, int((end_dt - start_dt).total_seconds() / 60))
            appointment.actual_duration = actual_duration
            appointment.save(update_fields=["actual_duration", "actual_start_time", "status", "real_status"])

            normalize_day_queue(appointment.date, now_dt=now_dt)
            return Response({"status": "completed"}, status=http_status.HTTP_200_OK)

        # ========================
        # 4) cancelled / absent
        # ========================
        if new_status in ["cancelled", "absent"]:
            appointment.logical_status = "skipped"
            appointment.skip_reason = "no_show" if new_status == "absent" else "cancelled"
            appointment.save(update_fields=["logical_status", "skip_reason", "status", "real_status"])
            # طبق سیاست شما: روی صف تاثیر نگذارد
            return Response({"status": "updated"}, status=http_status.HTTP_200_OK)

        # سایر وضعیت‌ها
        appointment.save(update_fields=["status", "real_status"])
        return Response({"status": "updated"}, status=http_status.HTTP_200_OK)


# -------------------------------
# ⏱ افزودن زمان اضافی (Normalize محور)
# -------------------------------
class AppointmentAddTimeView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, pk):
        appointment = get_object_or_404(Appointment, pk=pk)
        extra = int(request.data.get("extra_minutes", 0))

        appointment.predicted_duration = int(appointment.predicted_duration or 0) + extra
        appointment.save(update_fields=["predicted_duration"])

        normalize_day_queue(appointment.date, now_dt=timezone.now())
        return Response({"status": "extra_time_added"}, status=http_status.HTTP_200_OK)


# -------------------------------
# 🔍 جستجوی نوبت بر اساس تاریخ
# -------------------------------
class AppointmentSearchByDateView(APIView):
    def get(self, request):
        date_str = request.query_params.get("date")
        if not date_str:
            return Response({"error": "پارامتر date الزامی است."}, status=400)

        try:
            date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return Response({"error": "فرمت تاریخ معتبر نیست. از YYYY-MM-DD استفاده کنید."}, status=400)

        appointments = Appointment.objects.filter(date=date).annotate(
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
        ).order_by('status_order', 'queue_order', 'predicted_start_time', 'id')

        serializer = AppointmentOutputSerializer(appointments, many=True)
        return Response(serializer.data)


# -------------------------------
# 🩺 یادداشت پزشک
# -------------------------------
class AppointmentDoctorNoteUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        appointment = get_object_or_404(Appointment, pk=pk)
        serializer = AppointmentDoctorNoteUpdateSerializer(appointment, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response({"status": "doctor_note_updated"})
        return Response(serializer.errors, status=400)


class TreatmentDurationListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        all_treatments = encoder.classes_
        results = []

        for treatment in all_treatments:
            encoded = encoder.transform([treatment])[0]
            start_minutes = 0
            predicted = model.predict(np.array([[start_minutes, encoded]]))[0]
            results.append({
                "treatment_type": treatment,
                "predicted_duration": round(float(predicted), 2)
            })

        return Response(results)
