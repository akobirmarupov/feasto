import logging

from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from businesses.filters import BusinessApplicationFilter
from businesses.models import BusinessApplication
from businesses.routes.serializers import (
    BusinessApplicationCreateSerializer,
    BusinessApplicationSerializer,
)
from businesses.services import (
    BusinessLimitReached,
    TrialNotAvailable,
    approve_application,
    reject_application,
    submit_application,
)
from common.exceptions import BadRequest
from common.models import PlatformSettings
from common.pagination import StandardResultsPagination
from common.permissions import HasContactPhone, IsSuperAdmin
from common.throttles import BusinessApplicationThrottle

logger = logging.getLogger("businesses")


class BusinessApplicationCreateAPIView(APIView):
    permission_classes = [IsAuthenticated, HasContactPhone]
    phone_message = (
        "Ariza yuborish uchun aloqa raqamingizni kiriting — administrator siz bilan bog'lanadi."
    )
    throttle_classes = [BusinessApplicationThrottle]

    @extend_schema(request=BusinessApplicationCreateSerializer, responses={201: BusinessApplicationSerializer})
    def post(self, request):
        serializer = BusinessApplicationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        pending = any(
            hasattr(application, "business")
            for application in BusinessApplication.objects.filter(
                applicant=request.user, status=BusinessApplication.STATUS_PENDING
            )
        )
        if pending:
            raise BadRequest("Sizda ko'rib chiqilayotgan ariza allaqachon bor.", code="application_pending")

        plan = serializer.validated_data.get("plan")
        try:
            application, business, _ = submit_application(
                applicant=request.user,
                business_type=serializer.validated_data["business_type"],
                business_name=serializer.validated_data["business_name"],
                plan=plan,
            )
        except BusinessLimitReached as error:
            raise BadRequest(str(error), code="business_limit")
        except TrialNotAvailable as error:
            raise BadRequest(str(error), code="trial_used")
        except ValueError as error:
            raise BadRequest(str(error))

        platform = PlatformSettings.get_solo()
        admin_telegram = platform.admin_telegram_username

        if plan is None:
            message = (
                "Arizangiz qabul qilindi! Administrator uni tekshiradi va "
                f"tasdiqlagach sizga {platform.trial_days} kunlik BEPUL sinov "
                "ochiladi — shu muddat ichida platformaning barcha imkoniyatlaridan "
                "foydalanasiz. Tasdiqni tezlashtirish uchun Telegram orqali "
                f"administrator bilan bog'laning: @{admin_telegram}"
            )
        else:
            message = (
                f"Arizangiz qabul qilindi! Tanlangan tarif — {plan.duration_label}, "
                f"{plan.price:,.0f} so'm. To'lovni Telegram orqali amalga oshiring: "
                f"@{admin_telegram}. Administrator tasdiqlagach obunangiz o'sha "
                f"kundan boshlab {plan.duration_label}ga faollashadi."
            ).replace(",", " ")

        return Response({
            "application": BusinessApplicationSerializer(application).data,
            "business_id": str(business.id),
            "is_trial": plan is None,
            "trial_days": platform.trial_days if plan is None else None,
            "message": message,
            "admin_telegram": f"@{admin_telegram}",
        }, status=status.HTTP_201_CREATED)


class MyBusinessApplicationAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=BusinessApplicationSerializer(many=True))
    def get(self, request):
        queryset = (
            BusinessApplication.objects.filter(applicant=request.user)
            .select_related("applicant", "business", "plan")
            .order_by("-created_at")
        )
        return Response(BusinessApplicationSerializer(queryset, many=True).data)


class AdminApplicationListAPIView(APIView):
    permission_classes = [IsSuperAdmin]
    filter_backends = [DjangoFilterBackend]
    filterset_class = BusinessApplicationFilter
    queryset = BusinessApplication.objects.none()
    pagination_class = StandardResultsPagination

    @extend_schema(responses=BusinessApplicationSerializer(many=True))
    def get(self, request):
        queryset = (
            BusinessApplication.objects.select_related("applicant", "approved_by", "business", "plan")
            .order_by("-created_at")
        )
        queryset = BusinessApplicationFilter(request.GET, queryset=queryset).qs

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(BusinessApplicationSerializer(page, many=True).data)


def _get_application(pk):
    try:
        return BusinessApplication.objects.select_related("applicant", "business", "plan").get(pk=pk)
    except BusinessApplication.DoesNotExist:
        raise NotFound("Ariza topilmadi.")


class AdminApplicationApproveAPIView(APIView):
    permission_classes = [IsSuperAdmin]

    @extend_schema(request=None, responses=BusinessApplicationSerializer)
    def post(self, request, pk):
        application = _get_application(pk)
        if application.status == BusinessApplication.STATUS_APPROVED:
            raise BadRequest("Bu ariza allaqachon tasdiqlangan.")

        try:
            application = approve_application(application=application, approved_by=request.user)
        except TrialNotAvailable as error:
            raise BadRequest(str(error), code="trial_used")

        return Response(BusinessApplicationSerializer(application).data, status=status.HTTP_200_OK)


class AdminApplicationRejectAPIView(APIView):
    permission_classes = [IsSuperAdmin]

    @extend_schema(request=None, responses=BusinessApplicationSerializer)
    def post(self, request, pk):
        application = _get_application(pk)
        if application.status == BusinessApplication.STATUS_REJECTED:
            raise BadRequest("Bu ariza allaqachon rad etilgan.")

        application = reject_application(application=application, rejected_by=request.user)
        return Response(BusinessApplicationSerializer(application).data, status=status.HTTP_200_OK)
