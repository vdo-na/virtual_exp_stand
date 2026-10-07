import random
from datetime import datetime, timedelta, timezone
from faker import Faker
from locust import HttpUser, task, between, events

fake = Faker()

API_KEY = "00000000-0000-0000-0000-000000000000"
HOST_HEADER = "api.localhost"

# Глобальные пулы для обмена данными между тасками
SHORT_CODES_POOL = []
KNOWN_TAGS = ["load-test", "promo", "marketing", "social", "internal"]

GEO_IPS = [
    "188.40.142.1",    # DE
    "8.8.8.8",         # US
    "194.95.249.20",   # DE
    "151.101.1.140",   # US
    "133.242.18.35",   # JP
    "177.18.200.12",   # BR
    "51.15.22.4",      # FR
    "212.58.244.70",   # GB
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) Mobile/15E148",
    "Mozilla/5.0 (Linux; Android 14; SM-S918B) Chrome/121.0.6167.143 Mobile Safari/537.36",
]


class BaseShlinkUser(HttpUser):
    abstract = True

    def on_start(self):
        self.client.headers.update({
            "Host": HOST_HEADER,
            "X-Api-Key": API_KEY,
            "Accept": "application/json",
        })


# ==============================================================================
# 1. ОБЫЧНЫЙ ПОЛЬЗОВАТЕЛЬ (Посетитель)
# ==============================================================================
class RegularVisitorUser(BaseShlinkUser):
    weight = 7
    wait_time = between(0.1, 0.4)

    @task(9)
    def visit_valid_short_url(self):
        """Переход по существующей ссылке (302 Redirect + GeoIP запись)"""
        if not SHORT_CODES_POOL:
            return

        short_code = random.choice(SHORT_CODES_POOL)
        headers = {
            "Host": HOST_HEADER,
            "X-Forwarded-For": random.choice(GEO_IPS),
            "User-Agent": random.choice(USER_AGENTS),
            "Referer": fake.uri(),
        }

        with self.client.get(
            f"/{short_code}",
            headers=headers,
            allow_redirects=False,
            name="/{shortCode} [Redirect Check]",
            catch_response=True
        ) as response:
            if response.status_code in (301, 302, 307, 308):
                response.success()
            else:
                response.failure(f"Status {response.status_code}")

    @task(1)
    def visit_invalid_url(self):
        """Генерация 404 визитов для проверки orphan-аналитики"""
        fake_code = f"missing_{random.randint(1000, 9999)}"
        headers = {
            "Host": HOST_HEADER,
            "X-Forwarded-For": random.choice(GEO_IPS),
            "User-Agent": random.choice(USER_AGENTS),
        }
        with self.client.get(
            f"/{fake_code}",
            headers=headers,
            allow_redirects=False,
            name="/{invalidCode} [404 Orphan Visit]",
            catch_response=True
        ) as response:
            if response.status_code == 404:
                response.success()
            else:
                response.failure(f"Expected 404, got {response.status_code}")


# ==============================================================================
# 2. АДМИНИСТРАТОР (CRUD, Фильтры, Управление тегами)
# ==============================================================================
class AdminUser(BaseShlinkUser):
    weight = 1
    wait_time = between(1, 3)

    @task(4)
    def create_short_url(self):
        """[Админ 1] Создание ссылки"""
        tag = random.choice(KNOWN_TAGS)
        payload = {
            "longUrl": f"https://{fake.domain_name()}/{fake.uri_path()}",
            "tags": [tag, "auto-test"],
            "title": fake.catch_phrase(),
            "forwardQuery": True,
            "findIfExists": False
        }

        with self.client.post(
            "/rest/v3/short-urls",
            json=payload,
            name="/rest/v3/short-urls [POST Create]",
            catch_response=True
        ) as response:
            if response.status_code in (200, 201):
                code = response.json().get("shortCode")
                if code and code not in SHORT_CODES_POOL:
                    SHORT_CODES_POOL.append(code)
                response.success()
            else:
                response.failure(f"Create failed: {response.status_code}")

    @task(2)
    def update_short_url(self):
        """[Админ 2 - НОВЫЙ] Редактирование существующей ссылки (PATCH)"""
        if not SHORT_CODES_POOL:
            return

        short_code = random.choice(SHORT_CODES_POOL)
        payload = {
            "title": f"Updated: {fake.word()}",
            "tags": [random.choice(KNOWN_TAGS), "updated"],
            "maxVisits": random.randint(500, 5000)
        }

        with self.client.patch(
            f"/rest/v3/short-urls/{short_code}",
            json=payload,
            name="/rest/v3/short-urls/{shortCode} [PATCH Update]",
            catch_response=True
        ) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code == 404:
                # Если ссылка была удалена параллельным потоком
                if short_code in SHORT_CODES_POOL:
                    SHORT_CODES_POOL.remove(short_code)
                response.success()
            else:
                response.failure(f"Update failed: {response.status_code}")

    @task(2)
    def search_and_filter_urls(self):
        """[Админ 3 - НОВЫЙ] Сложный поиск по тегам, строке и сортировке по просмотрам"""
        tag = random.choice(KNOWN_TAGS)
        self.client.get(
            f"/rest/v3/short-urls?page=1&itemsPerPage=20&tags[]={tag}&orderBy[visits]=DESC",
            name="/rest/v3/short-urls [GET Filtered & Sorted]"
        )

    @task(1)
    def list_tags(self):
        """[Админ 4 - НОВЫЙ] Получение полного списка тегов системы"""
        self.client.get(
            "/rest/v3/tags",
            name="/rest/v3/tags [GET List]"
        )

    @task(1)
    def delete_short_url(self):
        """[Админ 5 - НОВЫЙ] Удаление ссылки (контролируемо, если пул > 40)"""
        if len(SHORT_CODES_POOL) <= 40:
            return

        short_code = SHORT_CODES_POOL.pop(random.randrange(len(SHORT_CODES_POOL)))
        with self.client.delete(
            f"/rest/v3/short-urls/{short_code}",
            name="/rest/v3/short-urls/{shortCode} [DELETE]",
            catch_response=True
        ) as response:
            if response.status_code in (204, 200):
                response.success()
            else:
                response.failure(f"Delete failed: {response.status_code}")


# ==============================================================================
# 3. СТАТИСТИК (Аналитика, временные срезы, Orphan, Теги)
# ==============================================================================
class StatisticianUser(BaseShlinkUser):
    weight = 2
    wait_time = between(0.8, 2)

    @task(3)
    def get_url_visits(self):
        """[Статистик 1] Просмотр визитов конкретной ссылки с пагинацией"""
        if not SHORT_CODES_POOL:
            return

        short_code = random.choice(SHORT_CODES_POOL)
        with self.client.get(
            f"/rest/v3/short-urls/{short_code}/visits?page=1&itemsPerPage=20",
            name="/rest/v3/short-urls/{shortCode}/visits [GET Visits]",
            catch_response=True
        ) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code == 404:
                if short_code in SHORT_CODES_POOL:
                    SHORT_CODES_POOL.remove(short_code)
                response.success()
            else:
                response.failure(f"Visits failed: {response.status_code}")

    @task(2)
    def get_tag_visits(self):
        """[Статистик 2 - НОВЫЙ] Аналитика визитов по конкретному тегу/кампании"""
        tag = random.choice(KNOWN_TAGS)
        self.client.get(
            f"/rest/v3/tags/{tag}/visits?page=1&itemsPerPage=20",
            name="/rest/v3/tags/{tag}/visits [GET Tag Visits]"
        )

    @task(2)
    def get_orphan_visits(self):
        """[Статистик 3 - НОВЫЙ] Выгрузка статистики ошибочных (404) переходов"""
        self.client.get(
            "/rest/v3/visits/orphan?page=1&itemsPerPage=20",
            name="/rest/v3/visits/orphan [GET Orphan Visits]"
        )

    @task(2)
    def get_filtered_time_visits(self):
        """[Статистик 4 - НОВЫЙ] Анализ визитов за последние 24 часа без учета ботов"""
        start_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
        self.client.get(
            f"/rest/v3/visits/non-orphan?startDate={start_date}&excludeBots=true&page=1&itemsPerPage=20",
            name="/rest/v3/visits/non-orphan [GET Filtered Time & Bots]"
        )

    @task(1)
    def get_tag_stats_summary(self):
        """[Статистик 5] Общая сводная статистика по тегам"""
        self.client.get(
            "/rest/v3/tags/stats",
            name="/rest/v3/tags/stats [GET Tags Summary]"
        )


# ==============================================================================
# PRE-SEEDING
# ==============================================================================
@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    import requests

    base_url = environment.host or "http://localhost:80"
    headers = {
        "Host": HOST_HEADER,
        "X-Api-Key": API_KEY,
        "Content-Type": "application/json"
    }

    print("\n[INFO] Starting DB pre-seeding...")
    try:
        res = requests.get(f"{base_url}/rest/v3/short-urls?itemsPerPage=50", headers=headers, timeout=5)
        if res.status_code == 200:
            for item in res.json().get("shortUrls", {}).get("data", []):
                SHORT_CODES_POOL.append(item["shortCode"])
            print(f"[INFO] Loaded {len(SHORT_CODES_POOL)} existing short codes.")
    except Exception as e:
        print(f"[WARN] Fetch existing links failed: {e}")

    needed = max(0, 50 - len(SHORT_CODES_POOL))
    for _ in range(needed):
        try:
            payload = {
                "longUrl": f"https://{fake.domain_name()}/{fake.uri_path()}",
                "tags": [random.choice(KNOWN_TAGS), "seed"],
                "findIfExists": False
            }
            res = requests.post(f"{base_url}/rest/v3/short-urls", json=payload, headers=headers, timeout=5)
            if res.status_code in (200, 201):
                SHORT_CODES_POOL.append(res.json().get("shortCode"))
        except Exception:
            break

    print(f"[INFO] Pre-seeding completed. Active pool: {len(SHORT_CODES_POOL)} URLs.\n")