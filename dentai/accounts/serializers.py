from rest_framework import serializers
from .models import User, Patient

# ------------------ User ------------------
class UserSerializer(serializers.ModelSerializer):
    username = serializers.CharField(read_only=True)

    class Meta:
        model  = User
        fields = ['id', 'username', 'first_name', 'last_name', 'phone_number', 'national_id']
        extra_kwargs = {
            'phone_number': {'validators': []},  # ✅ جلوی ارور تکراری رو می‌گیره
        }

# ------------------ Patient ------------------
class PatientSerializer(serializers.ModelSerializer):
    user = UserSerializer()

    # ✅ این متد جدید رو اینجا قرار بده
    def to_internal_value(self, data):
        data = data.copy()

        def unpack(section_name):
            section = data.pop(section_name, None)
            if isinstance(section, dict):
                for k, v in section.items():
                    data[k] = v

        unpack("personal_info")
        unpack("treatment_consent")
        unpack("clinical_evaluation")

        return super().to_internal_value(data)

    class Meta:
        model  = Patient
        fields = [
            "id", "user",

            # personal_info
            "birth_date", "gender", "occupation", "landline", "phone", "address",
            "relative_job", "record_completion_date", "record_number",
            "has_diabetes", "has_heart_disease", "has_tonsil_issue",
            "has_blood_disease", "has_allergy", "has_lung_disease",
            "has_history_of_hospitalization", "other_diseases", "current_medications",

            # treatment_consent
            "relationship_with_patient", "treatment_consent", "consent_date",

            # clinical_evaluation
            "problem_list", "treatment_plan", "retention_plan", "system_used",
            "crowding", "protrusion_lips", "molars", "bite", "midline",
            "gingival_show", "shift", "dual_bite",
        ]


    # ——> فقط برای پاسخ GET خروجی را nested می‌کنیم:
    def to_representation(self, instance):
        data = super().to_representation(instance)

        return {
            "id": data["id"],
            "user": data["user"],
            "personal_info": {
                **{k: data[k] for k in [
                    "birth_date", "gender", "occupation", "landline", "phone", "address",
                    "relative_job", "record_completion_date", "record_number",
                    "has_diabetes", "has_heart_disease", "has_tonsil_issue",
                    "has_blood_disease", "has_allergy", "has_lung_disease",
                    "has_history_of_hospitalization", "other_diseases", "current_medications"
                ]}
            },
            "treatment_consent": {
                **{k: data[k] for k in [
                    "relationship_with_patient", "treatment_consent", "consent_date"
                ]}
            },
            "clinical_evaluation": {
                **{k: data[k] for k in [
                    "problem_list", "treatment_plan", "retention_plan", "system_used",
                    "crowding", "protrusion_lips", "molars", "bite", "midline",
                    "gingival_show", "shift", "dual_bite"
                ]}
            }
        }

    # ——> از default ModelSerializer.create/update استفاده می‌کنیم (flat data)
    def create(self, validated_data):
        user_data = validated_data.pop("user")

        # ساخت username از first_name + last_name
        first_name = user_data.get("first_name", "").strip()
        last_name = user_data.get("last_name", "").strip()
        base_username = f"{first_name}_{last_name}".replace(" ", "").lower()

        # اطمینان از یکتا بودن username
        username = base_username
        counter = 1
        while User.objects.filter(username=username).exists():
            username = f"{base_username}{counter}"
            counter += 1

        user_data["username"] = username

        user = User.objects.create_user(**user_data, is_patient=True)
        return Patient.objects.create(user=user, **validated_data)


    def update(self, instance, validated_data):
        user_data = validated_data.pop("user", None)
        print("RAW REQUEST DATA:", self.context["request"].data)
        print("VALIDATED DATA:", validated_data)

        if user_data:

            first_name = user_data.get("first_name", instance.user.first_name).strip()
            last_name = user_data.get("last_name", instance.user.last_name).strip()
            base_username = f"{first_name}_{last_name}".replace(" ", "").lower()

            username = base_username
            counter = 1
            while User.objects.filter(username=username).exclude(pk=instance.user.pk).exists():
                username = f"{base_username}{counter}"
                counter += 1

            user_data["username"] = username


            for attr, val in user_data.items():
                setattr(instance.user, attr, val)
            instance.user.save()
        # ---- NESTED GROUPS ----
        request = self.context.get("request")
    
        if request:
            data = request.data
    
            for group in ["personal_info", "treatment_consent", "clinical_evaluation"]:
                section = data.get(group)
                if isinstance(section, dict):
                    for key, value in section.items():
                        if hasattr(instance, key):
                            setattr(instance, key, value)
    
        # ---- FLAT FIELDS (اگر همزمان Flat هم فرستاده شده باشد) ----
        for attr, val in validated_data.items():
            setattr(instance, attr, val)
    
        instance.save()
        return instance


class PatientMiniSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()

    class Meta:
        model = Patient
        fields = ['id', 'user', 'birth_date', 'has_allergy']

    def get_user(self, obj):
        user = obj.user
        return {
            "first_name": user.first_name,
            "last_name": user.last_name,
            "phone_number": user.phone_number,
            "national_id": user.national_id,
        }


# ------------------ Search ------------------
class PatientSearchSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()

    class Meta:
        model  = Patient
        fields = ['id', 'full_name']

    def get_full_name(self, obj):
        return f"{obj.user.first_name} {obj.user.last_name}"
