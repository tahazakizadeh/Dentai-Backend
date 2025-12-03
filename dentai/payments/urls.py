# payments/urls.py

from django.urls import path
from .views import (
    PaymentDetailView,
    PaymentUpdateView,
    PaymentsByPatientView,
    ManualPaymentCreateView,
    PatientBalanceView,
)

urlpatterns = [
    # خلاصه مالی + بدهی کل
    path('patient/<int:patient_id>/balance/', PatientBalanceView.as_view(), name='patient-balance'),

    # لیست همه پرداخت‌های بیمار
    path('patient/<int:patient_id>/payments/', PaymentsByPatientView.as_view(), name='payments-by-patient'),

    # ساخت پرداخت دستی
    path('patient/<int:patient_id>/payments/manual/', ManualPaymentCreateView.as_view(), name='payment-manual'),

    # جزئیات / ویرایش تک پرداخت (بر اساس id خودش)
    path('payments/<int:pk>/', PaymentDetailView.as_view(), name='payment-detail'),
    path('payments/<int:pk>/edit/', PaymentUpdateView.as_view(), name='payment-edit'),
]
