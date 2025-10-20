# # appointments/urls.py

# appointments/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    AppointmentViewSet,
    BatchAppointmentAddView,
    AppointmentStatusUpdateView,
    AppointmentAddTimeView,
    AppointmentSearchByDateView,
    AppointmentDoctorNoteUpdateView,
    TreatmentDurationListView

)

router = DefaultRouter()
router.register(r'appointments', AppointmentViewSet)

urlpatterns = [
    path('appointments/batch-add/', BatchAppointmentAddView.as_view(), name='batch-add-appointments'),
    path('appointments/<int:pk>/update-status/', AppointmentStatusUpdateView.as_view(), name='update-appointment-status'),
    path('appointments/<int:pk>/add-time/', AppointmentAddTimeView.as_view(), name='add-time-to-appointment'),
    path('appointments/search-by-date/', AppointmentSearchByDateView.as_view(), name='appointment-search-by-date'),
    path('appointments/<int:pk>/update-note/', AppointmentDoctorNoteUpdateView.as_view(), name='update-appointment-note'),
    path('appointments/treatments-durations/', TreatmentDurationListView.as_view(), name='treatment-durations'),
    path('', include(router.urls)),
]
