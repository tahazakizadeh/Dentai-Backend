# payments/serializers.py

from rest_framework import serializers
from .models import Payment, PatientBalance


class PaymentSerializer(serializers.ModelSerializer):
    patient_name = serializers.SerializerMethodField()
    treatment_type = serializers.SerializerMethodField()
    date = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = [
            'id',
            'patient',
            'appointment',
            'patient_name',
            'treatment_type',
            'date',
            'amount',
            'payment_method',
            'tracking_code',
            'created_at',
        ]

        read_only_fields = ['patient', 'created_at', 'patient_name', 'treatment_type', 'date']

    def get_patient_name(self, obj):
        return obj.patient.user.get_full_name()

    def get_treatment_type(self, obj):
        if obj.appointment_id:
            return obj.appointment.treatment_type
        return None

    def get_date(self, obj):
        # اگر appointment هست تاریخ نوبت، وگرنه تاریخ خود payment
        if obj.appointment_id:
            return obj.appointment.date
        return obj.created_at.date()


class PatientBalanceSerializer(serializers.ModelSerializer):
    remaining = serializers.SerializerMethodField()
    percentage_paid = serializers.SerializerMethodField()

    class Meta:
        model = PatientBalance
        fields = ['patient', 'total_debt', 'total_paid', 'remaining', 'percentage_paid']

    def get_remaining(self, obj):
        return obj.remaining

    def get_percentage_paid(self, obj):
        if obj.total_debt in (None, 0):
            return 0
        return int((obj.total_paid / obj.total_debt) * 100)
