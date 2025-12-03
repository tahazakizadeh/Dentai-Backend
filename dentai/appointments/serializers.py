# appointments/serializers.py

from rest_framework import serializers
from .models import Appointment
from accounts.models import Patient



class AppointmentSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source='patient.user.username', read_only=True)
    

    class Meta:
        model = Appointment
        fields = ['id', 'patient', 'patient_name', 'treatment_type', 'predicted_start_time', 'predicted_duration', 'actual_start_time', 'actual_duration', 'status', 'date', 'doctor_note']



class BatchAppointmentInputSerializer(serializers.Serializer):
    date = serializers.DateField()
    start_time = serializers.TimeField(format='%H:%M', input_formats=['%H:%M'])
    appointments = serializers.ListField(
        child=serializers.DictField(
            child=serializers.CharField()
        )
    )

# appointments/serializers.py

from rest_framework import serializers
from .models import Appointment
from accounts.models import Patient



class AppointmentSerializer(serializers.ModelSerializer):
    patient_name = serializers.SerializerMethodField()
    predicted_start_time = serializers.TimeField(format='%H:%M:%S')
    actual_start_time = serializers.TimeField(format='%H:%M:%S', required=False)

    class Meta:
        model = Appointment
        fields = ['id', 'patient', 'patient_name', 'treatment_type', 'predicted_start_time', 'predicted_duration', 'actual_start_time', 'actual_duration', 'status', 'date', 'doctor_note']

    def get_patient_name(self, obj):
        user=obj.patient.user
        full_name=f"{user.first_name} {user.last_name}".strip()
        return full_name if full_name else user.username


class BatchAppointmentInputSerializer(serializers.Serializer):
    date = serializers.DateField()
    start_time = serializers.TimeField(format='%H:%M', input_formats=['%H:%M'])
    appointments = serializers.ListField(
        child=serializers.DictField(
            child=serializers.CharField()
        )
    )

class AppointmentOutputSerializer(serializers.ModelSerializer):
    patient_name = serializers.SerializerMethodField()
    predicted_start_time = serializers.TimeField(format='%H:%M:%S')
    actual_start_time = serializers.TimeField(format='%H:%M:%S', required=False)
    
    class Meta:
        model = Appointment
        fields = [
            "id", "patient", "patient_name", "treatment_type",
            "predicted_start_time", "predicted_duration",
            "actual_start_time", "actual_duration",
            "status", "date", 'doctor_note'
        ]

    def get_patient_name(self, obj):
        user=obj.patient.user
        full_name = f"{user.first_name} {user.last_name}".strip()
        return full_name if full_name else user.username

    class AppointmentStatusUpdateSerializer(serializers.ModelSerializer):
        class Meta:
            model = Appointment
            fields = ['status']
class AppointmentDoctorNoteUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Appointment
        fields = ['doctor_note']
