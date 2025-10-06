# education/views.py
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.http import FileResponse, Http404
from django.conf import settings
from .models import EducationItem
from .serializers import EducationItemSerializer
import os


class EducationItemViewSet(viewsets.ModelViewSet):
    queryset = EducationItem.objects.all().order_by('-created_at')
    serializer_class = EducationItemSerializer
    permission_classes = [IsAuthenticated]

    @action(detail=True, methods=['get'], url_path='download')
    def download_file(self, request, pk=None):
        """دانلود امن فایل آموزشی با JWT"""
        try:
            item = self.get_object()
            if not item.file:
                return Response({'error': 'فایلی برای این آیتم وجود ندارد.'}, status=404)

            file_path = item.file.path
            if not os.path.exists(file_path):
                raise Http404

            file_name = os.path.basename(file_path)
            response = FileResponse(open(file_path, 'rb'), as_attachment=True, filename=file_name)
            return response

        except Exception as e:
            return Response({'error': str(e)}, status=500)
