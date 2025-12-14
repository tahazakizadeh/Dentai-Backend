
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

        # زمان نوبت، فقط ساعت و دقیقه
        time_str = appointment.predicted_start_time.strftime("%H:%M")

        params = {
            'sender': '2000660110',
            'receptor': phone,
            'token': time_str,   # ⏰ توکن = ساعت نوبت
            'template': 'SMS2',
            'type': 'sms',
        }

        api.verify_lookup(params)

        # فقط بعد از موفقیت، علامت بزن که ارسال شده
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
# 🔁 بازسازی زمان‌بندی روز
# -------------------------------
def recalculate_schedule(date):
    """مرتب‌سازی نوبت‌های فعال (logical_status='active') برای یک روز"""
    appointments = Appointment.objects.filter(
        date=date, logical_status='active'
    ).order_by('predicted_start_time')

    if not appointments.exists():
        return

    current_time = datetime.combine(date, appointments.first().predicted_start_time)

    for appt in appointments:
        appt.predicted_start_time = current_time.time().replace(microsecond=0)
        current_time += timedelta(minutes=appt.predicted_duration)
        appt.save()

def check_and_send_reminders():
    """
    این تابع باید مثلاً هر یک دقیقه یک‌بار توسط کران/شِدولِر صدا زده شود.
    کارش:
        - پیدا کردن نوبت‌هایی که حدوداً ۱ ساعت دیگر شروع می‌شوند
        - فقط برای نوبت‌های active + waiting / waiting_room
        - فقط وقتی sms2_sent=False
    """
    now_dt = timezone.now()

    # بازه زمانی تارگت: از 1 ساعت بعد تا 1 ساعت و 1 دقیقه بعد
    target_start = now_dt + timedelta(hours=1)
    target_end = now_dt + timedelta(hours=1, minutes=1)

    # فقط نوبت‌های امروز (برای سادگی)
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
        """حذف کامل نوبت و باززمان‌بندی سایر نوبت‌ها"""
        instance = self.get_object()
        appointment_date = instance.date
    
        instance.delete()
        recalculate_schedule(appointment_date)
    
        return Response(
            {"message": "نوبت با موفقیت حذف شد و زمان‌بندی مجدد انجام شد."},
            status=http_status.HTTP_200_OK
        )
    def get(self, request):
        appointments = Appointment.objects.annotate(
            status_order=Case(
                When(status='in_progress', then=Value(1)),
                When(status='waiting', then=Value(2)),
                When(status='absent', then=Value(3)),     
                When(status='completed', then=Value(4)),
                When(status='cancelled', then=Value(5)),
                default=Value(6),
                output_field=IntegerField(),
            )
        ).order_by('status_order', 'predicted_start_time')


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
                predicted_start_time=current_time.time(),
                predicted_duration=duration,
                real_status='waiting',
                logical_status='active',
                date=date
            )
            
            threading.Thread(
                target=send_appointment_sms,
                args=(patient,date.strftime('%Y-%m-%d'), current_time.time()),
                daemon=True
            ).start()

            current_time += timedelta(minutes=duration)
            scheduled.append(appt)

        output = AppointmentOutputSerializer(scheduled, many=True)
        return Response({"status": "generated", "scheduled": output.data})

#-------------------------------------
#    جابه‌جایی ترتیب نوبت‌های یک روز:
#    - محدودیت:
#        * فقط نوبت‌های waiting و waiting_room قابل جابه‌جایی هستند.
#          in_progress → waiting_room → waiting → absent → completed → cancelled
#        * اگر کسی تلاش کند نوبت را بین کتگوری‌ها جابه‌جا کند، خطا می‌دهد.
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

        # استخراج آیدی‌ها از ورودی
        try:
            new_ids = [int(item["id"]) for item in items]
        except (KeyError, TypeError, ValueError):
            return Response(
                {"error": "هر آیتم در appointments باید شامل فیلد عددی id باشد."},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        today = timezone.now().date()

        # نوبت‌های فعال امروز به همان ترتیبی که در داشبورد نشان می‌دهی
        original_qs = Appointment.objects.filter(
            date=today,
            logical_status='active',
        ).annotate(
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
        ).order_by('status_order', 'predicted_start_time')

        original_ids = [a.id for a in original_qs]

        if not original_ids:
            return Response(
                {"error": "برای امروز نوبتی یافت نشد."},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        # باید دقیقاً همان آیدی‌ها را بدهد، نه کم‌تر، نه بیشتر، نه تکراری
        if len(original_ids) != len(new_ids) or set(original_ids) != set(new_ids):
            return Response(
                {
                    "error": "لیست appointments باید دقیقاً شامل همه نوبت‌های فعال امروز باشد.",
                    "expected_ids": original_ids,
                },
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        id_to_appt = {a.id: a for a in original_qs}

        # ساخت بلوک‌های وضعیت از ترتیب اصلی
        blocks = []  # هر بلوک: (status, [ids...])
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

        # حالا روی ترتیب جدید حرکت می‌کنیم و می‌سنجیم آیا قوانین کتگوری رعایت شده یا نه
        idx = 0
        for status, orig_block_ids in blocks:
            block_len = len(orig_block_ids)
            new_block_ids = new_ids[idx:idx + block_len]

            if len(new_block_ids) != block_len:
                return Response(
                    {"error": "ساختار بلوک‌های وضعیت در داده‌ی ارسالی خراب شده است."},
                    status=http_status.HTTP_400_BAD_REQUEST,
                )

            # ۱) همه‌ی آیدی‌های این بلوک باید واقعاً همین status را داشته باشند
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

            # ۲) برای وضعیت‌هایی به جز waiting / waiting_room، ترتیب حتی داخل بلوک هم نباید عوض شود
            if status not in ['waiting', 'waiting_room'] and new_block_ids != orig_block_ids:
                return Response(
                    {
                        "error": "فقط نوبت‌های در انتظار (waiting و waiting_room) قابل جابجایی هستند.",
                        "detail": f"نوبت‌های وضعیت {status} نباید جابجا شوند.",
                    },
                    status=http_status.HTTP_400_BAD_REQUEST,
                )

            idx += block_len

        # اگر به اینجا رسیدیم، یعنی فقط داخل waiting / waiting_room جابجا شده و ساختار کلی حفظ شده 👍

        # حالا فقط زمان‌بندی waiting / waiting_room را از نو می‌چینیم
        # نقطه شروع = زمان شروع اولین نوبت صف انتظار در ترتیب اصلی
        waiting_original = [a for a in original_qs if a.status in ['waiting', 'waiting_room']]
        if not waiting_original:
            # هیچ نوبت صف انتظاری نیست، پس چیزی برای بازچینش نداریم
            return Response({"status": "nothing_to_reorder"}, status=http_status.HTTP_200_OK)

        first_waiting = waiting_original[0]
        current_dt = datetime.combine(today, first_waiting.predicted_start_time)

        # ترتیب جدید نوبت‌های صف انتظار از روی new_ids
        new_waiting_ids = [pk for pk in new_ids if id_to_appt[pk].status in ['waiting', 'waiting_room']]

        try:
            for ap_id in new_waiting_ids:
                ap = id_to_appt[ap_id]
                ap.predicted_start_time = current_dt.time().replace(microsecond=0)
                ap.save(update_fields=['predicted_start_time'])

                duration = ap.predicted_duration or 0
                current_dt += timedelta(minutes=duration)

        except Exception as e:
            logger.error(f"Error while reordering waiting appointments: {e}")
            return Response(
                {"error": "در حین ذخیره نوبت‌های جابجا شده خطایی رخ داد.", "detail": str(e)},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        return Response({"status": "reordered"}, status=http_status.HTTP_200_OK)

#--------------------------------
# منطق اضافه کردن بیمار اتفاقی
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

        # ----------------------
        # 1) دریافت بیمار
        # ----------------------
        try:
            patient = Patient.objects.get(id=patient_id)
        except Patient.DoesNotExist:
            return Response({"error": "بیمار یافت نشد"}, status=404)

        today = timezone.now().date()

        # ----------------------
        # 2) پیدا کردن اولین waiting
        # ----------------------
        first_waiting = Appointment.objects.filter(
            date=today,
            logical_status='active',
            status='waiting'
        ).order_by('predicted_start_time').first()

        # ----------------------
        # 3) محاسبه زمان شروع نوبت اتفاقی
        # ----------------------
        if first_waiting:
            predicted_start = first_waiting.predicted_start_time
        else:
            # اگر waiting وجود نداشت → آخرین active
            last_active = Appointment.objects.filter(
                date=today,
                logical_status='active'
            ).order_by('-predicted_start_time').first()

            predicted_start = last_active.predicted_start_time if last_active else timezone.now().time()

        # ----------------------
        # 4) محاسبه زمان مدت درمان (ML)
        # ----------------------
        try:
            duration = predict_duration(treatment_type)
        except Exception as e:
            return Response({"error": str(e)}, status=400)

        # ----------------------
        # 5) ساخت نوبت جدید
        # ----------------------
        new_appt = Appointment.objects.create(
            patient=patient,
            treatment_type=treatment_type,
            predicted_start_time=predicted_start,
            predicted_duration=duration,
            date=today,
            status='waiting_room',
            real_status='waiting_room',
            logical_status='active'
        )

        # ----------------------
        # 6) شیفت دادن تمام waitingها پس از زمان شروع
        # ----------------------
        waiting_to_shift = Appointment.objects.filter(
            date=today,
            logical_status='active',
            status='waiting',
            predicted_start_time__gte=predicted_start
        ).order_by('predicted_start_time')

        for w in waiting_to_shift:
            dt = datetime.combine(today, w.predicted_start_time)
            dt += timedelta(minutes=duration)
            w.predicted_start_time = dt.time().replace(microsecond=0)
            w.save(update_fields=["predicted_start_time"])

        # ----------------------
        # 7) ارسال SMS به صورت Thread
        # ----------------------
        threading.Thread(
            target=send_appointment_sms,
            args=(patient, today, predicted_start),
            daemon=True
        ).start()

        # ----------------------
        # 8) خروجی
        # ----------------------
        output = AppointmentOutputSerializer(new_appt).data
        return Response({"status": "random_added", "appointment": output})

# -------------------------------
# 🔄 تغییر وضعیت نوبت‌ها
# -------------------------------
class AppointmentStatusUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        appointment = get_object_or_404(Appointment, pk=pk)
        new_status = request.data.get("status")
        if not new_status:
            return Response({"error": "status is required"}, status=http_status.HTTP_400_BAD_REQUEST)

        original_logical = appointment.logical_status
        original_status = appointment.status

        # هم real_status (و status قدیمی که داری استفاده می‌کنی) رو هم به‌روز کن
        appointment.real_status = new_status
        appointment.status = new_status
        now_dt = datetime.now()
        now_time = now().time()

      # ========================
        # 1) رفتن به in_progress
        # ========================
        if new_status == "in_progress":
            
            # ⛔️ محدودیت همزمانی (خیلی مهم)
            MAX_CONCURRENT = 1  # فعلاً
        
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
        
            now_time = timezone.now().time()
        
            # --- ثبت زمان واقعی شروع ---
            appointment.actual_start_time = now_time
        
            # --- اگر از skipped برمی‌گردد ---
            if original_logical == "skipped":
                # زمان پیش‌بینی‌شده را برابر الان بگذار
                appointment.predicted_start_time = now_time
                appointment.logical_status = "active"
                appointment.save()
        
                # همه نوبت‌های بعدی را به اندازه طول درمان فعلی جابه‌جا کن
                self.shift_from_now(appointment, int(appointment.predicted_duration))
                return Response({"status": "reactivated_from_skipped"})
        
            # --- اگر از waiting (عادی) وارد in_progress شده ---
            try:
                predicted_dt = datetime.combine(appointment.date, appointment.predicted_start_time)
                actual_dt = datetime.combine(appointment.date, appointment.actual_start_time)
                delay_minutes = int((actual_dt - predicted_dt).total_seconds() / 60)
            except Exception:
                delay_minutes = 0
        
            # ذخیره نهایی
            appointment.save()
        
            # # اگر تأخیر یا زودتر شروع شده، نوبت‌های بعدی را شیفت کن
            # if delay_minutes != 0:
            #     self.shift_later(appointment, delay_minutes)
        
            return Response({"status": "in_progress_started"})
        

        # ========================
        # 2) رفتن به completed
        # ========================
        elif new_status == "completed":
            # باید قبلاً actual_start_time ثبت شده باشد؛ اگر نیست، ثبت کن (fallback)
            if not appointment.actual_start_time:
                appointment.actual_start_time = now_time
        
            start_dt = datetime.combine(appointment.date, appointment.actual_start_time)
            end_dt = now_dt
            actual_duration = max(0, int((end_dt - start_dt).total_seconds() / 60))
            appointment.actual_duration = actual_duration
            appointment.save(update_fields=["actual_duration", "status"])
           
            # 🔹 تفاوت بین زمان واقعی و پیش‌بینی
            diff = int(actual_duration - appointment.predicted_duration)
        
            # اگر بیشتر طول کشیده → عقب بنداز
            if diff > 0:
                self.shift_later(appointment, diff)
            # اگر زودتر تموم شده → به جلو ببر
            elif diff < 0:
                self.pull_earlier(appointment, abs(diff))
        

        # ========================
        # 3) رفتن به cancelled یا absent
        # ========================
        elif new_status in ["cancelled", "absent"]:
            # نوبت را منطقی غیرفعال کن، ولی تایم‌های نوبت‌های دیگر را تغییر نده
            appointment.logical_status = "skipped"
            appointment.skip_reason = "no_show" if new_status == "absent" else "cancelled"
            # **نکته**: اینجا intentionally نه recalculate_schedule و نه shift انجام می‌دهیم
            # تا نوبت‌های دیگر تحت تاثیر عدم حضور قرار نگیرند (طبق خواسته‌ٔ شما).

        appointment.save()
        return Response({"status": "updated"}, status=http_status.HTTP_200_OK)

    # ----- توابع کمکی -----

    def shift_later(self, appointment, minutes):
        """
        شیفت مثبت => نوبت‌های بعدی به جلو (بعدی‌ها دیرتر می‌شوند)
        شیفت منفی => نوبت‌های بعدی به عقب (بعدی‌ها زودتر می‌شوند)
        فقط نوبت‌هایی که logical_status='active' و status='waiting' را در نظر بگیر
        """
        later = Appointment.objects.filter(
            date=appointment.date,
            logical_status='active',
            status__in=['waiting', 'waiting_room'],
            predicted_start_time__gt=appointment.predicted_start_time
        ).order_by('predicted_start_time')

        for a in later:
            try:
                dt = datetime.combine(a.date, a.predicted_start_time)
            except Exception:
                # اگر predicted_start_time خالی بود، از actual_start_time یا skip کن
                continue
            dt += timedelta(minutes=minutes)
            a.predicted_start_time = dt.time().replace(microsecond=0)
            a.save()

    def push_back_after_activation(self, appointment, push_minutes):
        """
        وقتی یک نوبت قبلاً skipped بوده و حالا فعال می‌شود (in_progress)؛
        باید نوبت‌های بعدی را به اندازه predicted_durationِ این نوبت push کنیم
        تا جای کافی برای درمان او ایجاد شود.
        """
        later = Appointment.objects.filter(
            date=appointment.date,
            logical_status='active',
            status='waiting'
        ).exclude(pk=appointment.pk).order_by('predicted_start_time')

        # فقط آنهایی که predicted_start_time >= appointment.predicted_start_time را جابجا کن
        for a in later:
            try:
                dt = datetime.combine(a.date, a.predicted_start_time)
            except Exception:
                continue
            if dt >= datetime.combine(appointment.date, appointment.predicted_start_time):
                dt += timedelta(minutes=push_minutes)
                a.predicted_start_time = dt.time().replace(microsecond=0)
                a.save()
    def pull_earlier(self, appointment, minutes):
        """
        وقتی درمان زودتر از زمان پیش‌بینی‌شده تموم شد،
        نوبت‌های بعدی باید به اندازه `minutes` زودتر شروع بشن.
        """
        ref_dt = datetime.combine(appointment.date, appointment.predicted_start_time)
        later = Appointment.objects.filter(
            date=appointment.date,
            logical_status='active',
            status__in=['waiting', 'waiting_room']
        ).order_by('predicted_start_time')
    
        for a in later:
            try:
                a_dt = datetime.combine(a.date, a.predicted_start_time)
            except Exception:
                continue
    
            # فقط نوبت‌های بعد از این یکی رو جابجا کن
            if a_dt > ref_dt:
                new_dt = a_dt - timedelta(minutes=minutes)
                # جلوگیری از تداخل زمانی (مثلاً نیاد قبل از زمان این درمان)
                if new_dt < ref_dt + timedelta(minutes=1):
                    new_dt = ref_dt + timedelta(minutes=1)
    
                a.predicted_start_time = new_dt.time().replace(microsecond=0)
                a.save(update_fields=["predicted_start_time"])
    def shift_from_now(self, appointment, minutes):
        """
        وقتی یک نوبت از skipped به in_progress برمی‌گردد:
        تمام نوبت‌های 'waiting' در همان روز (بعد از زمان فعلی) یا حتی قبل از آن
        باید طوری شیفت شوند که جا برای درمان جدید باز شود.
        """
        # فقط نوبت‌های waiting فعال در همان روز
        waiting_appointments = Appointment.objects.filter(
            date=appointment.date,
            logical_status='active',
            status__in=['waiting', 'waiting_room']
        ).order_by('predicted_start_time')
    
        now_time = timezone.now().time()
    
        for nxt in waiting_appointments:
            # فقط اونایی که هنوز درمانشون شروع نشده (حتی اگه زمان‌شون گذشته)
            try:
                dt = datetime.combine(nxt.date, nxt.predicted_start_time)
                # فقط نوبت‌هایی که بعد از زمان فعلی هستن یا الان در صفن
                if dt.time() >= now_time or nxt.predicted_start_time <= now_time:
                    dt += timedelta(minutes=minutes)
                    nxt.predicted_start_time = dt.time().replace(microsecond=0)
                    nxt.save()
            except Exception:
                continue



# -------------------------------
# ⏱ افزودن زمان اضافی
# -------------------------------
class AppointmentAddTimeView(APIView):
    permission_classes = [IsAuthenticated]
    def patch(self, request, pk):
        appointment = get_object_or_404(Appointment, pk=pk)
        extra = int(request.data.get("extra_minutes", 0))

        # افزایش زمان پیش‌بینی‌شده برای خود نوبت
        appointment.predicted_duration += extra
        appointment.save()

        # شیفت نوبت‌های بعدی در همان روز
        later = Appointment.objects.filter(
            date=appointment.date,
            logical_status='active',
            status__in=['waiting', 'waiting_room'],
            predicted_start_time__gt=appointment.predicted_start_time
        ).order_by('predicted_start_time')

        for nxt in later:
            if nxt.predicted_start_time:
                dt = datetime.combine(nxt.date, nxt.predicted_start_time)
                dt += timedelta(minutes=extra)
                nxt.predicted_start_time = dt.time().replace(microsecond=0)
                nxt.save()

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
                default=Value(6),
                output_field=IntegerField(),
            )
        ).order_by('status_order', 'predicted_start_time')

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
        # گرفتن تمام نوع درمان‌ها از انکدر
        all_treatments = encoder.classes_
        results = []

        for treatment in all_treatments:
            encoded = encoder.transform([treatment])[0]
            start_minutes = 0  # مدل به این مقدار وابسته نیست
            predicted = model.predict(np.array([[start_minutes, encoded]]))[0]
            results.append({
                "treatment_type": treatment,
                "predicted_duration": round(float(predicted), 2)
            })

        return Response(results)


