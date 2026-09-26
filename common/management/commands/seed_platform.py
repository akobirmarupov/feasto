
from django.core.management.base import BaseCommand
from django.db import transaction

from common.models import PlatformSettings
from subscriptions.models import SubscriptionPlan

PLAN_PRICES = {
    "restaurant": {1: 250000, 3: 600000},
    "venue": {1: 300000, 3: 800000},
}


class Command(BaseCommand):
    help = "Platforma sozlamalari va tarif rejalarini yaratadi (mavjudlarini buzmaydi)."

    def add_arguments(self, parser):
        parser.add_argument("--telegram", default="akobir_marupov", help="Admin Telegram username (@ siz)")
        parser.add_argument(
            "--reset-prices", action="store_true",
            help="Mavjud rejalarning narxini ham standartga qaytaradi.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        settings_obj, created = PlatformSettings.objects.get_or_create(pk=1)
        if created:
            settings_obj.admin_telegram_username = options["telegram"]
            settings_obj.save()
            self.stdout.write(self.style.SUCCESS("✓ Platforma sozlamalari yaratildi"))
        else:
            self.stdout.write("• Platforma sozlamalari allaqachon mavjud — tegilmadi")

        self.stdout.write("\nTarif rejalari:")
        for business_type, durations in PLAN_PRICES.items():
            for months, price in durations.items():
                plan, created = SubscriptionPlan.objects.get_or_create(
                    business_type=business_type,
                    duration_months=months,
                    defaults={"price": price, "trial_days": settings_obj.trial_days},
                )
                if created:
                    mark = "✓ yaratildi"
                elif options["reset_prices"] and plan.price != price:
                    plan.price = price
                    plan.save(update_fields=["price"])
                    mark = "↻ narx yangilandi"
                else:
                    mark = "• mavjud"

                per_month = f" (oyiga {plan.price_per_month:,.0f})" if months > 1 else ""
                line = (f"  {mark}: {plan.get_business_type_display()} — "
                        f"{plan.duration_label} — {plan.price:,.0f} so'm{per_month}")
                self.stdout.write(line.replace(",", " "))

        self.stdout.write(self.style.SUCCESS("\nTayyor. Endi superuser yarating:"))
        self.stdout.write("  python manage.py createsuperuser")
