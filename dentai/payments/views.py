# payments/views.py

from rest_framework import generics, status
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from accounts.models import Patient

from .models import Payment, PatientBalance
from .serializers import PaymentSerializer, PatientBalanceSerializer
from django.shortcuts import get_object_or_404

class PaymentsByPatientView(generics.ListAPIView):
    """
    لیست همه پرداخت‌های یک بیمار (نوبتی + آزاد)
    """
    serializer_class = PaymentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        patient_id = self.kwargs['patient_id']
        return Payment.objects.filter(patient_id=patient_id).order_by('created_at')


class PaymentDetailView(generics.RetrieveAPIView):
    """
    جزئیات یک پرداخت با id
    """
    queryset = Payment.objects.all().order_by("id")
    serializer_class = PaymentSerializer
    permission_classes = [IsAuthenticated]


class PaymentUpdateView(generics.UpdateAPIView):
    """
    ویرایش مبلغ / روش پرداخت / کد رهگیری یک پرداخت
    """
    queryset = Payment.objects.all().order_by("id")
    serializer_class = PaymentSerializer
    permission_classes = [IsAuthenticated]


class ManualPaymentCreateView(APIView):
    """
    ساخت پرداخت دستی (بدون وابستگی به نوبت)
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, patient_id):
        data = request.data.copy()
        
        serializer = PaymentSerializer(data=data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        patient = get_object_or_404(Patient, pk=patient_id)

        payment = serializer.save(patient=patient)
        return Response(PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)


class PatientBalanceView(APIView):
    """
    گرفتن / تنظیم هزینه کل درمان و خلاصه مالی بیمار
    GET → summary
    PATCH → تغییر total_debt
    """
    permission_classes = [IsAuthenticated]

    def get_object(self, patient_id):
        balance, _ = PatientBalance.objects.get_or_create(patient_id=patient_id)
        return balance

    def get(self, request, patient_id):
        balance = self.get_object(patient_id)
        serializer = PatientBalanceSerializer(balance)
        return Response(serializer.data)

    def patch(self, request, patient_id):
        balance = self.get_object(patient_id)
        serializer = PatientBalanceSerializer(balance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
        
class LatestPaymentView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, patient_id):
        latest = (
            Payment.objects
            .filter(patient_id=patient_id)
            .order_by('-created_at', '-id')
            .first()
        )

        if not latest:
            return Response(
                {"detail": "هیچ پرداختی برای این بیمار ثبت نشده است."},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = PaymentSerializer(latest)
        return Response(serializer.data, status=status.HTTP_200_OK)
