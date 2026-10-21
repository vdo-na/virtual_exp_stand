import os
import sys
import time
import copy
import subprocess
import time
import requests
import pandas as pd

# ==============================================================================
# 1. КОНФИГУРАЦИЯ БАЗОВЫХ ЗНАЧЕНИЙ (BASELINE) И ВАРИАЦИЙ ДЛЯ ТЕСТОВ
# ==============================================================================

# Дефолтные значения (как они работают сейчас «из коробки»)
HIGH_PRIORITY = 0x00000080 if sys.platform == "win32" else 0


BASELINE_CONFIG = {
    # PostgreSQL
    "synchronous_commit": "on",
    "shared_buffers": "128MB",
    "wal_buffers": "-1",          # авто (~4MB)
    "max_wal_size": "1GB",
    "work_mem": "4MB",
    "random_page_cost": "4.0",    # дефолт под HDD
    # Shlink
    "DISABLE_IP_TRACKING": "false",
    "WEB_WORKER_NUM": "2",        # дефолт под 1 ядро
    "MEMORY_LIMIT": "512M"
}

# Значения, которые нужно протестировать (ровно по 1 эксперименту на каждое значение)
PARAM_VARIATIONS = {
    "synchronous_commit": ["off"],
    "shared_buffers": ["256MB", "384MB", "512MB"],
    "wal_buffers": ["16MB", "32MB"],
    "max_wal_size": ["2GB", "4GB"],
    "work_mem": ["8MB", "16MB", "32MB"],
    "random_page_cost": ["1.1"],                   # Оптимизация под SSD Docker
    "DISABLE_IP_TRACKING": ["true"],
    "WEB_WORKER_NUM": ["4", "8", "16"],
    "MEMORY_LIMIT": ["256M", "768M", "-1"]         # -1 отключает лимит PHP
}

# Параметры нагрузки
LOCUST_FILE = "locustfile.py"
HOST = "http://localhost:80"

TEST_DURATION = "5m"   # Длительность одного замера (60 сек достаточно для фиксации RPS)
USERS = 50              # Количество одновременных виртуальных пользователей
SPAWN_RATE = 25         # Скорость набора пользователей
DATASET_FILE = "benchmark_dataset.csv"
REPORTS_DIR = "benchmark_reports"


# ==============================================================================
# 2. ГЕНЕРАТОР ЭКСПЕРИМЕНТОВ
# ==============================================================================

def generate_experiments():
    """Формирует список тестов: 1 baseline + по одному тесту на каждое значение"""
    experiments = []

    # 0. Базовый эксперимент
    base_exp = copy.deepcopy(BASELINE_CONFIG)
    base_exp["_exp_name"] = "00_baseline"
    base_exp["_param_changed"] = "none"
    base_exp["_value_changed"] = "default"
    experiments.append(base_exp)

    # 1. Эксперименты с единичными отклонениями
    idx = 1
    for param_name, values in PARAM_VARIATIONS.items():
        for val in values:
            exp = copy.deepcopy(BASELINE_CONFIG)
            exp[param_name] = str(val)
            exp["_exp_name"] = f"{idx:02d}_{param_name}_{str(val).replace('-', 'unlimited')}"
            exp["_param_changed"] = param_name
            exp["_value_changed"] = str(val)
            experiments.append(exp)
            idx += 1

    return experiments


# ==============================================================================
# 3. УПРАВЛЕНИЕ ОКРУЖЕНИЕМ DOCKER
# ==============================================================================

COMPOSE_FILE = "benchmark-compose.yml"
OVERRIDE_FILE = "benchmark-compose.override.yml"
DOCKER_CMD = ["docker", "compose", "-f", COMPOSE_FILE, "-f", OVERRIDE_FILE]

def apply_docker_override(cfg):
    """Генерирует benchmark-compose.override.yml со строгим списком аргументов для Postgres"""
    override_content = f"""
services:
  db:
    command:
      - postgres
      - -c
      - synchronous_commit={cfg['synchronous_commit']}
      - -c
      - shared_buffers={cfg['shared_buffers']}
      - -c
      - wal_buffers={cfg['wal_buffers']}
      - -c
      - max_wal_size={cfg['max_wal_size']}
      - -c
      - work_mem={cfg['work_mem']}
      - -c
      - random_page_cost={cfg['random_page_cost']}

  backend:
    environment:
      DISABLE_IP_TRACKING: '{cfg['DISABLE_IP_TRACKING']}'
      WEB_WORKER_NUM: {cfg['WEB_WORKER_NUM']}
      MEMORY_LIMIT: '{cfg['MEMORY_LIMIT']}'
"""
    with open(OVERRIDE_FILE, "w", encoding="utf-8") as f:
        f.write(override_content.strip())


def reset_and_wait():
    """
    Быстрый и стабильный перезапуск:
    Применяет новые параметры через restart БЕЗ удаления томов (down -v),
    затем очищает таблицы визитов мгновенным SQL TRUNCATE.
    """
    print("   [DOCKER] Applying configs and restarting containers...")
    
    # 1. Поднимаем/перезапускаем с новыми флагами (без уничтожения виртуального диска WSL2)
    subprocess.run(
        DOCKER_CMD + ["up", "-d", "--force-recreate"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True
    )

    print("   [DOCKER] Waiting for API and warming up...")
    headers = {
        "Host": "api.localhost",
        "X-Api-Key": "00000000-0000-0000-0000-000000000000"
    }

    # Ждем готовности
    for attempt in range(40):
        try:
            r = requests.get(f"{HOST}/rest/v3/short-urls", headers=headers, timeout=2)
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(1)

    # 2. Мгновенно очищаем накопленные визиты напрямую в Postgres, не ломая таблицы
    # Это исключает I/O зависания WSL2 и оставляет базу девственно чистой перед тестом
    truncate_cmd = [
        "docker", "compose", "-f", COMPOSE_FILE, "exec", "-T", "db",
        "psql", "-U", "shlink", "-d", "shlink", "-c",
        "TRUNCATE visits, visit_locations CASCADE;"
    ]
    subprocess.run(truncate_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 3. Даем 5 секунд на прогрев Opcache и стабилизацию воркеров
    time.sleep(15)
    print("   [DOCKER] Ready with warm caches.")


def run_test(exp_name):
    """Запуск Locust в изолированном контейнере внутри сети Docker"""
    # Путь к отчету внутри контейнера
    container_csv_prefix = f"/mnt/locust/reports/{exp_name}"
    # Путь к тому же отчету на хосте (Windows)
    host_stats_file = os.path.join(REPORTS_DIR, f"{exp_name}_stats.csv")

    cmd = DOCKER_CMD + [
        "run", "--rm",
        "locust",
        "-f", "/mnt/locust/locustfile.py",
        "--host", "http://traefik:80",    # Внутри сети Docker стучимся напрямую в traefik!
        "--headless",
        "-u", str(USERS),
        "-r", str(SPAWN_RATE),
        "--run-time", TEST_DURATION,
        f"--csv={container_csv_prefix}",
        "--only-summary",
        "--exit-code-on-error=0"
    ]

    print(f"   [LOCUST-DOCKER] Running stress test inside Docker network ({TEST_DURATION})...")
    
    # Запуск контейнера Locust
    result = subprocess.run(cmd, capture_output=True, text=True)

    if not os.path.exists(host_stats_file):
        print(f"\n[ERROR] Locust container failed! Details:\n{result.stderr}\n{result.stdout}")
        raise RuntimeError(f"Locust did not generate {host_stats_file}")

    # Читаем результат, который контейнер записал в смонтированную папку
    df = pd.read_csv(host_stats_file)
    aggregated = df[df["Name"] == "Aggregated"].iloc[0]

    req_count = int(aggregated["Request Count"])
    fail_count = int(aggregated["Failure Count"])

    return {
        "total_requests": req_count,
        "failed_requests": fail_count,
        "fail_ratio_%": round((fail_count / req_count * 100), 2) if req_count > 0 else 0,
        "rps": round(float(aggregated["Requests/s"]), 2),
        "median_latency_ms": float(aggregated["Median Response Time"]),
        "avg_latency_ms": float(aggregated["Average Response Time"]),
        "p95_latency_ms": float(aggregated["95%"]),
        "p99_latency_ms": float(aggregated["99%"])
    }

# ==============================================================================
# 5. ГЛАВНЫЙ ЦИКЛ
# ==============================================================================

def main():
    os.makedirs(REPORTS_DIR, exist_ok=True)
    experiments = generate_experiments()
    total_tests = len(experiments)

    print("=" * 80)
    print(f"BENCHMARK RUNNER: Prepared {total_tests} experiments (1 Baseline + {total_tests - 1} variations)")
    print("=" * 80)

    dataset_rows = []

    try:
        for idx, exp in enumerate(experiments, 1):
            exp_name = exp["_exp_name"]
            param = exp["_param_changed"]
            val = exp["_value_changed"]

            print(f"\n>>> [{idx}/{total_tests}] Running: {exp_name}")
            if param == "none":
                print("    Target: Baseline (all defaults)")
            else:
                print(f"    Target: Parameter '{param}' = '{val}' (others at default)")

            # 1. Генерируем override
            apply_docker_override(exp)

            # 2. Поднимаем с нуля
            reset_and_wait()

            # 3. Прогоняем нагрузку
            metrics = run_test(exp_name)

            print(f"    --> Results: RPS = {metrics['rps']} | p95 = {metrics['p95_latency_ms']} ms | Fails = {metrics['failed_requests']}")

            # 4. Формируем запись датасета
            record = copy.deepcopy(exp)
            record.update(metrics)
            dataset_rows.append(record)

            # Сохраняем датасет после каждого шага (защита от сбоя)
            pd.DataFrame(dataset_rows).to_csv(DATASET_FILE, index=False)

    except KeyboardInterrupt:
        print("\n[ABORT] Testing stopped by user!")
    finally:
        # Убираем за собой временный override
        if os.path.exists(OVERRIDE_FILE):
            os.remove(OVERRIDE_FILE)
            print(f"\n[CLEANUP] Removed temporary {OVERRIDE_FILE}")
            
    print("\n" + "=" * 80)
    print(f"SUCCESS: Completed {len(dataset_rows)} experiments.")
    print(f"Full dataset saved to: {os.path.abspath(DATASET_FILE)}")
    print(f"Locust CSV reports saved to: {os.path.abspath(REPORTS_DIR)}/")
    print("=" * 80)


if __name__ == "__main__":
    main()