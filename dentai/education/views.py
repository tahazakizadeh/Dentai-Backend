# education/views.py
from rest_framework import viewsets , status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.http import FileResponse, Http404
from django.conf import settings
from .models import EducationItem
from .serializers import EducationItemSerializer
import os
from django.core.exceptions import RequestDataTooBig

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
        
    # ✅ Override create
    def create(self, request, *args, **kwargs):
        try:
            file_obj = request.FILES.get('file')
            max_upload_size = 100 * 1024 * 1024  # 50 MB
            if file_obj and file_obj.size > max_upload_size:
                return Response(
                    {"error": f"حجم فایل بیش از حد مجاز است. حداکثر حجم مجاز {max_upload_size / (1024*1024)} مگابایت است."},
                    status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
                )

            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            self.perform_create(serializer)
            return Response({
                "message": "فایل آموزشی با موفقیت اضافه شد.",
                "data": serializer.data
            }, status=status.HTTP_201_CREATED)

        except RequestDataTooBig:
            return Response(
                {"error": f"حجم فایل بیش از حد مجاز است. حداکثر حجم مجاز {max_upload_size / (1024*1024)} مگابایت است."},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            )
        except Exception as e:
            return Response({"error": f"خطا در آپلود فایل: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # ✅ Override update
    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response({
            "message": "فایل آموزشی با موفقیت ویرایش شد.",
            "data": serializer.data
        }, status=status.HTTP_200_OK)

    # ✅ Override destroy
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        title = instance.title
        self.perform_destroy(instance)
        return Response({
            "message": f"فایل '{title}' با موفقیت حذف شد."
        }, status=status.HTTP_200_OK)

    # ✅ Custom error handling for retrieve
    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance)
            return Response(serializer.data)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_404_NOT_FOUND)
