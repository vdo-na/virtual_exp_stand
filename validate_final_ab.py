import os
import sys
import time
import subprocess
import requests
import pandas as pd

# ==============================================================================
# НАСТРОЙКИ СВЯЗИ С СЕРВЕРОМ 1
# ==============================================================================
REMOTE_IP = "186.246.29.52"       # Вставьте ваш IP Сервера 1
REMOTE_USER = "root"
REMOTE_DIR = "/root/docker"    # Папка на Сервере 1

PROMETHEUS_URL = f"http://{REMOTE_IP}:9090"
APP_URL = f"http://{REMOTE_IP}:80"

USERS = 100
SPAWN_RATE = 20
TEST_DURATION = "1500s"           # 25 минут на тест (как в основном датасете)
OUTPUT_CSV = "final_ab_comparison.csv"

# ==============================================================================
# ДВЕ КОНФИГУРАЦИИ ДЛЯ ЧЕСТНОГО A/B ТЕСТИРОВАНИЯ
# ==============================================================================
EXPERIMENTS = [
    {
        "name": "00_baseline",
        "synchronous_commit": "on",
        "shared_buffers": "128MB",
        "wal_buffers": "-1",
        "max_wal_size": "1GB",
        "work_mem": "4MB",
        "random_page_cost": "4.0",
        "DISABLE_IP_TRACKING": "false",
        "WEB_WORKER_NUM": "2",
        "MEMORY_LIMIT": "512M"
    },
    {
        "name": "99_champion_ga",
        "synchronous_commit": "on",
        "shared_buffers": "512MB",     # Лучший буфер из 25-мин теста
        "wal_buffers": "-1",
        "max_wal_size": "4GB",         # Топ по сбросу чекпоинтов
        "work_mem": "8MB",             # Топ по сортировкам
        "random_page_cost": "1.1",     # Топ по индексам
        "DISABLE_IP_TRACKING": "true",
        "WEB_WORKER_NUM": "4",         # Абсолютный лидер по параллелизму
        "MEMORY_LIMIT": "256M"         # Выбор алгоритма
    }
]


def ssh_exec(cmd):
    full_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", f"{REMOTE_USER}@{REMOTE_IP}", cmd]
    res = subprocess.run(full_cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"SSH Error: {res.stderr}")
    return res.stdout


def apply_remote_config(cfg):
    override_content = f"""services:
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
    cmd = f"cat << 'EOF' > {REMOTE_DIR}/docker-compose.override.yml\n{override_content}\nEOF"
    ssh_exec(cmd)


def restart_remote_system():
    print("   [REMOTE] Recreating containers with new override...")
    ssh_exec(f"cd {REMOTE_DIR} && docker compose up -d --force-recreate")

    headers = {"Host": "api.localhost", "X-Api-Key": "00000000-0000-0000-0000-000000000000"}
    for _ in range(40):
        try:
            r = requests.get(f"{APP_URL}/rest/v3/short-urls", headers=headers, timeout=2)
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(1)

    # Очистка накопленных визитов
    ssh_exec(f"cd {REMOTE_DIR} && docker compose exec -T db psql -U shlink -d shlink -c 'TRUNCATE visits, visit_locations CASCADE;'")
    time.sleep(5)  # Прогрев кэшей


def get_prometheus_metrics(start_ts, end_ts):
    def query_prom(query):
        try:
            url = f"{PROMETHEUS_URL}/api/v1/query_range"
            params = {"query": query, "start": start_ts, "end": end_ts, "step": "10s"}
            res = requests.get(url, params=params, timeout=5).json()
            values = [float(v[1]) for series in res["data"]["result"] for v in series["values"]]
            return round(max(values), 2), round(sum(values) / len(values), 2) if values else (0, 0)
        except Exception:
            return 0, 0

    backend_cpu_max, backend_cpu_avg = query_prom('sum(rate(container_cpu_usage_seconds_total{name=~".*backend.*"}[30s])) * 100')
    db_cpu_max, db_cpu_avg = query_prom('sum(rate(container_cpu_usage_seconds_total{name=~".*db.*"}[30s])) * 100')
    backend_ram_max, _ = query_prom('sum(container_memory_working_set_bytes{name=~".*backend.*"}) / 1024 / 1024')
    db_ram_max, _ = query_prom('sum(container_memory_working_set_bytes{name=~".*db.*"}) / 1024 / 1024')

    return {
        "backend_cpu_max_%": backend_cpu_max,
        "backend_cpu_avg_%": backend_cpu_avg,
        "db_cpu_max_%": db_cpu_max,
        "db_cpu_avg_%": db_cpu_avg,
        "backend_ram_max_mb": backend_ram_max,
        "db_ram_max_mb": db_ram_max
    }


def run_locust(exp_name):
    csv_prefix = f"reports_ab/{exp_name}"
    os.makedirs("reports_ab", exist_ok=True)

    start_ts = time.time()
    cmd = [
        "locust",
        "-f", "locustfile.py",
        "--host", APP_URL,
        "--headless",
        "-u", str(USERS),
        "-r", str(SPAWN_RATE),
        "--run-time", TEST_DURATION,
        f"--csv={csv_prefix}",
        "--only-summary",
        "--exit-code-on-error=0"
    ]
    print(f"   [LOCUST] Running stress test ({TEST_DURATION})...")
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    end_ts = time.time()

    df = pd.read_csv(f"{csv_prefix}_stats.csv")
    agg = df[df["Name"] == "Aggregated"].iloc[0]

    prom_metrics = get_prometheus_metrics(start_ts, end_ts)

    locust_metrics = {
        "requests": int(agg["Request Count"]),
        "fails": int(agg["Failure Count"]),
        "rps": round(float(agg["Requests/s"]), 2),
        "avg_ms": round(float(agg["Average Response Time"]), 1),
        "p95_ms": round(float(agg["95%"]), 1)
    }

    return {**locust_metrics, **prom_metrics}


def main():
    print("=" * 70)
    print("ЗАПУСК ЧИСТОГО A/B ТЕСТИРОВАНИЯ (25 МИНУТ НА ТЕСТ)")
    print("=" * 70)
    results = []

    for i, exp in enumerate(EXPERIMENTS):
        name = exp["name"]
        print(f"\n>>> [{i+1}/2] Тестирование: {name}...")
        apply_remote_config(exp)
        restart_remote_system()
        data = run_locust(name)

        print(f"    ИТОГ: RPS = {data['rps']} | p95 = {data['p95_ms']}ms | DB CPU = {data['db_cpu_max_%']}%")
        results.append({**exp, **data})
        pd.DataFrame(results).to_csv(OUTPUT_CSV, index=False)

    print("\n" + "=" * 70)
    print("РЕЗУЛЬТАТЫ ФИНАЛЬНОГО СРАВНЕНИЯ:")
    print("=" * 70)
    res_df = pd.DataFrame(results)[["name", "rps", "avg_ms", "p95_ms", "fails", "db_cpu_max_%"]]
    print(res_df.to_string(index=False))


if __name__ == "__main__":
    main()
