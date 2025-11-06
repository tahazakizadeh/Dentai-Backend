
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

            sms_sent = send_appointment_sms(patient, date.strftime('%Y-%m-%d'), current_time.time())
            time.sleep(2)
            if not sms_sent:
                logger.warning(f"SMS not sent for {appt.id}")

            current_time += timedelta(minutes=duration)
            scheduled.append(appt)

        output = AppointmentOutputSerializer(scheduled, many=True)
        return Response({"status": "generated", "scheduled": output.data})


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
            # اگر این نوبت قبلاً skipped (غایب/کنسل) بود -> بازگشت از غیبت
            if original_logical == "skipped":
                # ثبت زمان واقعی شروع
                appointment.actual_start_time = now_time
                # گذاشتن predicted_start_time به زمان فعلی (slot now)
                appointment.predicted_start_time = now_time
                # فعال‌سازی منطقی نوبت
                appointment.logical_status = "active"
                appointment.save()
                # اینجا باید برای جا گرفتن نوبت، بقیه را به اندازه predicted_duration جابجا کنیم
                self.push_back_after_activation(appointment, int(appointment.predicted_duration))
            else:
                # حالت معمول: waiting -> in_progress (تاخیر یا زود رسیدن)
                if not appointment.actual_start_time:
                    appointment.actual_start_time = now_time

                # اگر predicted_start_time موجود باشه، محاسبه تاخیر
                try:
                    predicted_dt = datetime.combine(appointment.date, appointment.predicted_start_time)
                    actual_dt = datetime.combine(appointment.date, appointment.actual_start_time)
                    delay_minutes = int((actual_dt - predicted_dt).total_seconds() / 60)
                except Exception:
                    delay_minutes = 0

                if delay_minutes != 0:
                    # فقط نوبت‌های waiting و active (نه skipped/completed) رو shift کن
                    self.shift_later(appointment, delay_minutes)

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
            status='waiting',
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
            status='waiting'
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



# -------------------------------
# ⏱ افزودن زمان اضافی
# -------------------------------
class AppointmentAddTimeView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        appointment = get_object_or_404(Appointment, pk=pk)
        extra = int(request.data.get("extra_minutes", 0))
        appointment.predicted_duration += extra
        appointment.save()
        recalculate_schedule(appointment.date)
        return Response({"status": "extra_time_added"})


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
                When(status='waiting', then=Value(2)),
                When(status='absent', then=Value(3)),
                When(status='completed', then=Value(4)),
                default=Value(5),
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


