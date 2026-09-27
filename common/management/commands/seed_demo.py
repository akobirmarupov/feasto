import datetime
import random
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from businesses.models import Business, Hall, Room, VenuePricing
from businesses.services import approve_application, submit_application
from catalog.models import RestaurantMenuItem, VenueMenuItem
from content.models import Banner, News
from reservations.models import Availability, Reservation
from reviews.models import Review

User = get_user_model()

PASSWORD = "Demo12345!"

RESTAURANTS = [
    ("Shoxona", "Yunusobod", "Amir Temur ko'chasi 12", 41.3300, 69.2850, "milliy"),
    ("Bella Italia", "Mirobod", "Shota Rustaveli 45", 41.2980, 69.2740, "yevropa"),
    ("Sakura", "Yakkasaroy", "Bobur ko'chasi 8", 41.2900, 69.2500, "sharqona"),
]
VENUES = [
    ("Navro'z saroyi", "Chilonzor", "Bunyodkor shoh ko'chasi 3", 41.2750, 69.2050),
    ("Oq saroy", "Sergeli", "Yangi Sergeli 21", 41.2250, 69.2200),
]
RESTAURANT_MENU = [
    ("Osh", "main", 45000), ("Shashlik", "main", 25000), ("Lag'mon", "soup", 35000),
    ("Achichuk", "salad", 15000), ("Somsa", "starter", 12000), ("Choy", "drink", 5000),
    ("Medovik", "dessert", 22000),
]
VENUE_MENU = [
    ("To'y oshi", "main"), ("Kabob", "main"), ("Shurva", "soup"), ("Olivye", "salad"),
    ("Mevalar", "dessert"), ("Sharbat", "drink"),
]


class Command(BaseCommand):
    help = "Frontend va sinov uchun demo ma'lumot yaratadi (faqat DEBUG yoki --force)."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="DEBUG=False bo'lsa ham yaratish.")

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError("Demo ma'lumot faqat DEBUG=True da yaratiladi (yoki --force).")
        if User.objects.filter(username="demo_owner1").exists():
            self.stdout.write("Demo ma'lumot allaqachon bor. Avval `reset_data --yes` qiling.")
            return

        random.seed(7)
        call_command("seed_platform", verbosity=0)

        with transaction.atomic():
            admin = self._admin()
            customers = [self._user(f"demo_user{i}", f"Mijoz {i}", f"+99890000{i:04d}") for i in range(1, 6)]
            businesses = self._businesses(admin)
            self._schedules(businesses)
            self._reservations(businesses, customers)
            self._content()

        self.stdout.write(self.style.SUCCESS(
            f"\nTayyor. Parol hamma demo hisoblar uchun: {PASSWORD}\n"
            "  admin:      demo_admin (is_staff)\n"
            "  egalari:    demo_owner1..5\n"
            "  mijozlar:   demo_user1..5\n"
        ))

    def _admin(self):
        admin, created = User.objects.get_or_create(
            username="demo_admin",
            defaults={"full_name": "Demo Admin", "is_staff": True, "is_superuser": True,
                      "phone_number": "+998900000001"},
        )
        if created:
            admin.set_password(PASSWORD)
            admin.save()
        return admin

    def _user(self, username, full_name, phone):
        user = User.objects.create_user(
            username=username, password=PASSWORD, full_name=full_name,
            phone_number=phone, is_phone_verified=True, is_confirmed=True,
        )
        return user

    def _businesses(self, admin):
        result = []
        index = 1
        for name, district, address, lat, lng, cuisine in RESTAURANTS:
            owner = self._user(f"demo_owner{index}", f"{name} egasi", f"+99891000{index:04d}")
            application, business, _ = submit_application(
                applicant=owner, business_type=Business.TYPE_RESTAURANT, business_name=name,
            )
            approve_application(application=application, approved_by=admin)
            business.district, business.address = district, address
            business.latitude, business.longitude = lat, lng
            business.cuisine = cuisine
            business.description = f"{name} — {district} tumanidagi qulay restoran."
            business.open_time, business.close_time = datetime.time(10, 0), datetime.time(23, 0)
            business.telegram_username = f"{owner.username}"
            business.phone_number = owner.phone_number
            business.save()

            Room.objects.create(business=business, name="VIP xona", room_type="vip", capacity=8, deposit_tier="premium")
            Room.objects.create(business=business, name="Oilaviy xona", room_type="standard", capacity=12, deposit_tier="pro")
            Room.objects.create(business=business, name="Terrasa", room_type="outdoor", capacity=20, deposit_tier="pro", deposit_price=Decimal(30000))
            for dish, category, price in RESTAURANT_MENU:
                RestaurantMenuItem.objects.create(business=business, name=dish, category=category, price=Decimal(price))
            result.append(business)
            index += 1

        for name, district, address, lat, lng in VENUES:
            owner = self._user(f"demo_owner{index}", f"{name} egasi", f"+99891000{index:04d}")
            application, business, _ = submit_application(
                applicant=owner, business_type=Business.TYPE_VENUE, business_name=name,
            )
            approve_application(application=application, approved_by=admin)
            business.district, business.address = district, address
            business.latitude, business.longitude = lat, lng
            business.description = f"{name} — {district} tumanidagi zamonaviy to'yxona."
            business.telegram_username = f"{owner.username}"
            business.phone_number = owner.phone_number
            business.save()

            Hall.objects.create(business=business, name="Katta zal", people=500, all_price=Decimal(25000000))
            Hall.objects.create(business=business, name="Kichik zal", people=200, all_price=Decimal(12000000))
            for dish_count, price in ((1, 90000), (2, 130000), (3, 170000)):
                VenuePricing.objects.create(business=business, dish_count=dish_count, price_per_person=Decimal(price))
            for dish, category in VENUE_MENU:
                VenueMenuItem.objects.create(business=business, name=dish, category=category)
            result.append(business)
            index += 1
        return result

    def _schedules(self, businesses):
        today = timezone.localdate()
        months = sorted({
            datetime.date(d.year, d.month, 1)
            for d in (today - datetime.timedelta(days=20), today, today + datetime.timedelta(days=40))
        })
        for business in businesses:
            if business.business_type == Business.TYPE_RESTAURANT:
                for room in business.rooms.all():
                    Availability.generate_for_months(
                        business=business, room=room,
                        start_time=datetime.time(10, 0), end_time=datetime.time(23, 0), months=months,
                    )
            else:
                Availability.generate_for_months(
                    business=business, start_time=datetime.time(8, 0), end_time=datetime.time(0, 0), months=months,
                )

    def _reservations(self, businesses, customers):
        today = timezone.localdate()
        for business in businesses:
            place = business.rooms.first() if business.business_type == Business.TYPE_RESTAURANT else business.halls.first()
            for offset, status in ((-10, "completed"), (-5, "completed"), (-2, "cancelled"), (2, "confirmed"), (5, "pending")):
                day = today + datetime.timedelta(days=offset)
                customer = random.choice(customers)
                availability = Availability.objects.filter(
                    business=business, date=day,
                    room=place if business.business_type == Business.TYPE_RESTAURANT else None,
                ).first()
                if availability is None:
                    continue
                kwargs = {"user": customer, "business": business, "availability": availability, "status": status}
                if business.business_type == Business.TYPE_RESTAURANT:
                    kwargs.update(room=place, start_time=datetime.time(18, 0), end_time=datetime.time(20, 0), guests_count=4)
                else:
                    if Reservation.objects.filter(availability=availability).exclude(status="cancelled").exists():
                        continue
                    pricing = business.pricings.filter(dish_count=2).first()
                    kwargs.update(
                        hall=place, guests_count=150, dish_count=2,
                        price_per_person=pricing.price_per_person, day_rent_price=place.all_price,
                        total_price=place.all_price + pricing.price_per_person * 150,
                    )
                reservation = Reservation(**kwargs)
                reservation.deposit_amount = reservation.resolve_deposit_amount()
                reservation.save()
                if status == "completed":
                    Review.objects.create(
                        user=customer, business=business, reservation=reservation,
                        rating=random.choice((4, 5, 5, 3)), comment="Xizmat yaxshi, tavsiya qilaman.",
                    )

    def _content(self):
        Banner.objects.create(
            title_uz="Feasto — restoran va to'yxonalarni bron qiling",
            title_ru="Feasto — бронируйте рестораны и залы",
            title_en="Feasto — book restaurants and venues",
            subtitle_uz="Yaqin atrofdagi joylarni toping va bir necha soniyada bron qiling.",
            cta_label_uz="Boshlash", cta_url="/", placement=Banner.PLACEMENT_HERO,
        )
        News.objects.create(
            title_uz="Platforma ishga tushdi", title_ru="Платформа запущена", title_en="Platform launched",
            excerpt_uz="Endi Toshkentdagi restoran va to'yxonalarni onlayn bron qilish mumkin.",
            category=News.CATEGORY_UPDATE, is_pinned=True,
        )
