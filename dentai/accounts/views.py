from rest_framework import viewsets, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.decorators import action
from .models import Patient, User
from .serializers import PatientSerializer, UserSerializer, PatientSearchSerializer,PatientMiniSerializer
from django.contrib.auth import authenticate
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q
from django.http import HttpResponse
from django.contrib.auth import get_user_model
User = get_user_model()  # ✅ درست

from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from rest_framework.views import APIView
from rest_framework.response import Response
from django.contrib.auth import get_user_model

import random, string
from kavenegar import KavenegarAPI, APIException, HTTPException
from django.conf import settings
import pandas as pd
from io import BytesIO
import openpyxl

# لیست و پروفایل بیماران
class PatientViewSet(viewsets.ModelViewSet):
    queryset = Patient.objects.all().select_related('user')
    serializer_class = PatientSerializer
    permission_classes = [permissions.IsAuthenticated]

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response({"status": "deleted"}, status=status.HTTP_200_OK)

    def perform_destroy(self, instance):
        from payments.models import PatientBalance
        PatientBalance.objects.filter(patient=instance).delete()
        
        user = instance.user
        instance.delete()
        user.delete()

# ورود منشی با username/password
class StaffLoginView(APIView):
    def post(self, request):
        username = request.data.get('username')
        password = request.data.get('password')
        user = authenticate(username=username, password=password)
        if user and user.is_receptionist:
            refresh = RefreshToken.for_user(user)
            return Response({
                "token": str(refresh.access_token),
                "role": "receptionist",
                "isAuthenticated": True
            })
        return Response({"error": "اطلاعات ورود نامعتبر است."}, status=400)



from .models import OTP
from django.utils import timezone
import random

class RequestOTPView(APIView):
    def post(self, request):
        phone = request.data.get('phone_number')
        if not phone:
            return Response({"error": "شماره موبایل وارد نشده."}, status=400)

        try:
            user = User.objects.get(phone_number=phone, is_patient=True)
        except User.DoesNotExist:
            return Response({"error": "کاربر یافت نشد."}, status=404)

        otp_code =''.join(random.choices(string.digits, k=4))
        print(otp_code)

        # حذف کدهای قدیمی برای این شماره
        OTP.objects.filter(phone_number=phone).delete()

        # ذخیره کد جدید
        OTP.objects.create(phone_number=phone, code=otp_code)


        try:
            api = KavenegarAPI(settings.KAVENEGAR_API_KEY)
            params = {
                'sender' : '2000660110',
                'receptor': phone,
                'token': otp_code,
                'template': 'OTP',
                'type': 'sms'  # یا voice برای تماس صوتی
            }
            api.verify_lookup(params)
        except (APIException, HTTPException) as e:
            print("SMS Error:", str(e))
            return Response({"error": "خطا در ارسال پیامک"}, status=500)

        return Response({"message": "کد با موفقیت ارسال شد."})
    
class VerifyOTPView(APIView):
    def post(self, request):
        phone = request.data.get('phone_number')
        code = request.data.get('code')

        if not code or not phone:
            return Response({"error": "شماره موبایل یا کد وارد نشده."}, status=400)

        try:
            otp_record = OTP.objects.get(phone_number=phone, code=code)
        except OTP.DoesNotExist:
            return Response({"error": "کد اشتباه است یا منقضی شده."}, status=400)

        if otp_record.is_expired():
            otp_record.delete()
            return Response({"error": "کد اشتباه است یا منقضی شده."}, status=400)

        # اگر کد درست و معتبر است، کد رو حذف می‌کنیم تا یکبار مصرف باشد
        otp_record.delete()

        try:
            user = User.objects.get(phone_number=phone, is_patient=True)
        except User.DoesNotExist:
            return Response({"error": "کاربر یافت نشد."}, status=404)

        refresh = RefreshToken.for_user(user)
        return Response({
            "token": str(refresh.access_token),
            "role": "patient",
            "isAuthenticated": True
        })

    
class PatientSearchView(APIView):

    def get(self, request):
        query = request.query_params.get('query', '')
        patients = Patient.objects.filter(
            Q(user__first_name__icontains=query) |
            Q(user__last_name__icontains=query) |
            Q(user__username__icontains=query) |
            Q(user__phone_number__icontains=query)
        )
        serializer = PatientSearchSerializer(patients, many=True)
        return Response(serializer.data)
class PhoneNumberExistsView(APIView):
    def get(self, request):
        phone = request.query_params.get('phone_number')
        if not phone:
            return Response({"error": "پارامتر phone_number الزامی است."}, status=400)
        
        exists = User.objects.filter(phone_number=phone).exists()
        return Response({
            "phone_number": phone,
            "exists": exists
        })
class PatientMiniDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, patient_id):
        try:
            patient = Patient.objects.select_related("user").get(id=patient_id)
            serializer = PatientMiniSerializer(patient)
            return Response(serializer.data)
        except Patient.DoesNotExist:
            return Response({"error": "بیمار یافت نشد."}, status=404)

class ExportPatientsExcelView(APIView):
    def get(self, request):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Patients"

        # ────── هدرها ──────
        headers = [
            # User
            "PatientID", "UserID", "Username", "First Name", "Last Name",
            "Phone Number", "National ID",

            # Personal Info
            "Birth Date", "Gender", "Occupation", "Landline", "Mobile", "Address",
            "Relative Job", "Record Completion Date", "Record Number",
            "Has Diabetes", "Has Heart Disease", "Has Tonsil Issue", "Has Blood Disease",
            "Has Allergy", "Has Lung Disease", "Has History of Hospitalization",
            "Other Diseases", "Current Medications",

            # Treatment Consent
            "Relationship With Patient", "Treatment Consent", "Consent Date",

            # Clinical Evaluation
            "Problem List", "Treatment Plan", "Retention Plan", "System Used",
            "Crowding", "Protrusion Lips", "Molars", "Bite", "Midline",
            "Gingival Show", "Shift", "Dual Bite"
        ]
        ws.append(headers)

        # ────── دیتا ──────
        patients = Patient.objects.select_related("user").all()
        for p in patients:
            ws.append([
                # User
                p.id,
                p.user.id,
                p.user.username,
                p.user.first_name,
                p.user.last_name,
                p.user.phone_number,
                p.user.national_id,

                # Personal Info
                p.birth_date.strftime("%Y-%m-%d") if p.birth_date else "",
                p.gender,
                p.occupation,
                p.landline,
                p.phone,
                p.address,
                p.relative_job,
                p.record_completion_date.strftime("%Y-%m-%d") if p.record_completion_date else "",
                p.record_number,
                "Yes" if p.has_diabetes else "No",
                "Yes" if p.has_heart_disease else "No",
                "Yes" if p.has_tonsil_issue else "No",
                "Yes" if p.has_blood_disease else "No",
                "Yes" if p.has_allergy else "No",
                "Yes" if p.has_lung_disease else "No",
                "Yes" if p.has_history_of_hospitalization else "No",
                p.other_diseases,
                p.current_medications,

                # Treatment Consent
                p.relationship_with_patient,
                "Yes" if p.treatment_consent else "No",
                p.consent_date.strftime("%Y-%m-%d") if p.consent_date else "",

                # Clinical Evaluation
                p.problem_list,
                p.treatment_plan,
                p.retention_plan,
                p.system_used,
                p.crowding,
                p.protrusion_lips,
                p.molars,
                p.bite,
                p.midline,
                p.gingival_show,
                p.shift,
                p.dual_bite,
            ])

        # ────── خروجی اکسل ──────
        response = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = 'attachment; filename="patients.xlsx"'
        wb.save(response)
        return response


class CreateAdminUserView(APIView):
    def post(self, request):
        key = request.data.get("key")
        if key != "dsf54gd45sgd1gtrh4fg1b":
            return Response({"error": "Unauthorized"}, status=403)

        User = get_user_model()
        username = "Drmousavi"
        password = "456"
        email = "Drmousavi@gmail.com"

        user = User.objects.filter(username=username).first()

        if user:
            if not user.is_superuser:
                return Response({"error": "User exists but is not superuser."}, status=400)

            user.set_password(password)
            user.email = email
            user.is_receptionist = True  # تنظیم نقش منشی
            user.save()
            return Response({"message": "Superuser updated with receptionist role."})
        else:
            user = User.objects.create_superuser(username=username, password=password, email=email)
            user.is_receptionist = True  # تنظیم نقش منشی هنگام ساخت
            user.save()
            return Response({"message": "Superuser with receptionist role created successfully."})

