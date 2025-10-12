
from rest_framework import viewsets
from rest_framework.views import APIView,status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import Appointment
from accounts.models import Patient
from .serializers import BatchAppointmentInputSerializer, AppointmentOutputSerializer, AppointmentSerializer, AppointmentDoctorNoteUpdateSerializer
from datetime import datetime, timedelta
import pickle
from rest_framework import status as http_status
from rest_framework.generics import get_object_or_404
from datetime import datetime
from django.utils.timezone import now
from fuzzywuzzy import process
import time
import logging
from kavenegar import KavenegarAPI, APIException, HTTPException
from django.conf import settings
import jdatetime

def to_persian_date_str(date_str):
    # تبدیل رشته به datetime
    gregorian_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    # تبدیل به شمسی
    persian_date = jdatetime.date.fromgregorian(date=gregorian_date)
    # فرمت خروجی: روز-ماه-سال
    return persian_date.strftime("%Y-%m-%d")

logger = logging.getLogger(__name__)

def send_appointment_sms(patient, date, time):
    try:
        api = KavenegarAPI(settings.KAVENEGAR_API_KEY)
        phone=patient.user.phone_number
        phone = "+98" + phone[1:]             
        name="مراجع"
        date_str = to_persian_date_str(str(date))
        time_str = str(time)
        phone_str=str(phone)
        params = {
            'sender' : '2000660110',
            'receptor':phone_str,
            'token':name,
            'token2':date_str,
            'token3':time_str,
            'template': 'SMS1',
            'type': 'sms'  # یا voice برای تماس صوتی
        }
        api.verify_lookup(params)
        return True
    except Exception as e:
        print("Unexpected error:", str(e))
        return False




class AppointmentViewSet(viewsets.ModelViewSet):
    queryset = Appointment.objects.all().order_by("id")
    serializer_class = AppointmentSerializer
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response({"status": "deleted"}, status=status.HTTP_200_OK)

# مدل ML و انکودر
with open('ai/duration_predictor_model_v2.pkl', 'rb') as f:
    model = pickle.load(f)

with open('ai/treatment_encoder_v2.pkl', 'rb') as f:
    encoder = pickle.load(f)

def predict_duration(treatment_type):
    treatment_type = treatment_type.strip()

    if treatment_type not in encoder.classes_:
        # پیدا کردن نزدیک‌ترین match
        best_match, score = process.extractOne(treatment_type, encoder.classes_)
        if score > 80:  # اگر شباهت خوب بود، ادامه بده
            treatment_type = best_match
        else:
            raise ValueError(f"نوع درمان '{treatment_type}' شناخته نشد و مقدار مشابه مناسبی یافت نشد.")

    encoded = encoder.transform([treatment_type])[0]
    return round(model.predict([[840, encoded]])[0], 2)


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
            predicted_start_str = current_time.time().strftime('%H:%M')

            appt = Appointment.objects.create(
                patient=patient,
                treatment_type=treatment_type,
                predicted_start_time=current_time.time(),
                predicted_duration=duration,
                status='waiting',
                date=date
            )
 
            sms_sent = send_appointment_sms(patient, date.strftime('%Y-%m-%d'), predicted_start_str)
            time.sleep(5)
            if not sms_sent:
                logger.warning(f"Failed to send SMS for appointment {appt.id}")

            current_time += timedelta(minutes=duration)
            scheduled.append(appt)

        output_serializer = AppointmentOutputSerializer(scheduled, many=True)
        return Response({
            "status": "generated",
            "scheduled": output_serializer.data
        })


class AppointmentStatusUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        appointment = get_object_or_404(Appointment, pk=pk)
        new_status = request.data.get("status")

        original_status = appointment.status
        appointment.status = new_status

        # 1. ثبت زمان واقعی شروع درمان
        if new_status == "in_progress" and appointment.actual_start_time is None:
            appointment.actual_start_time = now().time()
            predicted_dt = datetime.combine(appointment.date, appointment.predicted_start_time)
            actual_dt = datetime.combine(appointment.date, appointment.actual_start_time)
            delay_minutes = int((actual_dt - predicted_dt).total_seconds() / 60)

            if delay_minutes != 0:
                self.shift_later_appointments(appointment, delay_minutes)

        # 2. ثبت زمان واقعی پایان درمان
        if new_status == "completed" and appointment.actual_start_time:
            start_dt = datetime.combine(appointment.date, appointment.actual_start_time)
            end_dt = datetime.now()
            actual_duration = int((end_dt - start_dt).total_seconds() / 60)
            appointment.actual_duration = actual_duration

            delay_minutes = actual_duration - appointment.predicted_duration
            if delay_minutes != 0:
                self.shift_later_appointments(appointment, delay_minutes)

        # 3. اگر لغو شد یا نیومد → نوبت‌های بعدی جلو بیان
        if new_status in ["cancelled", "absent"] and original_status == "waiting":
            self.shift_later_appointments(appointment, -appointment.predicted_duration)

        appointment.save()
        return Response({"status": "updated"}, status=http_status.HTTP_200_OK)

    def shift_later_appointments(self, appointment, shift_minutes):
        later_appointments = Appointment.objects.filter(
            date=appointment.date,
            predicted_start_time__gt=appointment.predicted_start_time
        ).order_by('predicted_start_time')

        for a in later_appointments:
            dt = datetime.combine(appointment.date, a.predicted_start_time)
            dt += timedelta(minutes=shift_minutes)
            a.predicted_start_time = dt.time().replace(microsecond=0)
            a.save()


class AppointmentAddTimeView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        appointment = get_object_or_404(Appointment, pk=pk)
        extra = int(request.data.get("extra_minutes", 0))
        appointment.predicted_duration += extra
        appointment.save()
        
        # نوبت‌های بعدی رو shift بده
        later_appointments = Appointment.objects.filter(
            date=appointment.date,
            predicted_start_time__gt=appointment.predicted_start_time
        ).order_by('predicted_start_time')

        for a in later_appointments:
            dt = datetime.combine(appointment.date, a.predicted_start_time)
            dt += timedelta(minutes=extra)
            a.predicted_start_time = dt.time() 
            a.save()

        return Response({"status": "extra_time_added"}, status=http_status.HTTP_200_OK)


class AppointmentSearchByDateView(APIView):
    
    def get(self, request):
        date_str = request.query_params.get("date")
        if not date_str:
            return Response({"error": "پارامتر date الزامی است."}, status=400)

        try:
            date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return Response({"error": "فرمت تاریخ معتبر نیست. از YYYY-MM-DD استفاده کنید."}, status=400)

        appointments = Appointment.objects.filter(date=date).select_related('patient__user').order_by('id')
        serializer = AppointmentOutputSerializer(appointments, many=True)
        return Response(serializer.data)
    

    
class AppointmentDoctorNoteUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        appointment = get_object_or_404(Appointment, pk=pk)
        serializer = AppointmentDoctorNoteUpdateSerializer(appointment, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response({"status": "doctor_note_updated"})
        return Response(serializer.errors, status=400)
 
