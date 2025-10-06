# education/serializers.py
from rest_framework import serializers
from .models import EducationItem

class EducationItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = EducationItem
        fields = ['id', 'title', 'file_type', 'file', 'url', 'description', 'created_at']
