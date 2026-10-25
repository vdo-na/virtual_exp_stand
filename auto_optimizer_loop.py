import os
import sys
import time
import copy
import json
import random
import subprocess
import requests
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.preprocessing import OneHotEncoder

# ==============================================================================
# 1. НАСТРОЙКИ
# ==============================================================================
REMOTE_IP = "186.246.29.52"       # Вставьте ваш IP Сервера 1
REMOTE_USER = "root"
REMOTE_DIR = "/root/docker"

PROMETHEUS_URL = f"http://{REMOTE_IP}:9090"
APP_URL = f"http://{REMOTE_IP}:80"

USERS = 100
SPAWN_RATE = 20
TEST_DURATION = "600s"           # 10 минут
DATASET_FILE = "full_benchmark_dataset.csv"
SESSION_RESULTS_FILE = "session_active_learning_results.csv"
CHAMPION_EXPORT_JSON = "FINAL_CHAMPION.json"
CHAMPION_EXPORT_TXT = "FINAL_CHAMPION.txt"

MAX_ALLOWED_P95_MS = 4000.0      # Естественный барьер стабильности (задержка не выше 4 сек)
PATIENCE_LIMIT = 4               # 4 попытки подряд без улучшений до остановки

GENE_KEYS = [
    "synchronous_commit", "shared_buffers", "wal_buffers", "max_wal_size",
    "work_mem", "random_page_cost", "DISABLE_IP_TRACKING", "WEB_WORKER_NUM", "MEMORY_LIMIT"
]

GENE_SPACE = {
    "synchronous_commit": ["on", "off"],
    "shared_buffers": ["128MB", "256MB", "384MB", "512MB"],
    "wal_buffers": ["-1", "16MB", "32MB"],
    "max_wal_size": ["1GB", "2GB", "4GB"],
    "work_mem": ["4MB", "8MB", "16MB", "32MB"],
    "random_page_cost": ["1.1", "4.0"],
    "DISABLE_IP_TRACKING": ["true", "false"],
    "WEB_WORKER_NUM": ["2", "4"],
    "MEMORY_LIMIT": ["512M", "768M"]
}


# ==============================================================================
# 2. УПРАВЛЕНИЕ СЕРВЕРОМ 1 (SSH / DOCKER / PROMETHEUS)
# ==============================================================================
def ssh_exec(cmd):
    res = subprocess.run(["ssh", "-o", "StrictHostKeyChecking=no", f"{REMOTE_USER}@{REMOTE_IP}", cmd], capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"SSH Error: {res.stderr}")
    return res.stdout


def apply_remote_config(cfg):
    override = f"""services:
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
    ssh_exec(f"cat << 'EOF' > {REMOTE_DIR}/docker-compose.override.yml\n{override}\nEOF")


def restart_remote_system():
    print("   [REMOTE] Recreating containers...")
    ssh_exec(f"cd {REMOTE_DIR} && docker compose up -d --force-recreate")

    headers = {"Host": "api.localhost", "X-Api-Key": "00000000-0000-0000-0000-000000000000"}
    for _ in range(40):
        try:
            r = requests.get(f"{APP_URL}/rest/v3/short-urls", headers=headers, timeout=2)
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(1)

    # ПОЛНАЯ ЧЕСТНАЯ ОЧИСТКА ТАБЛИЦ (БЕЗ УДАЛЕНИЯ API КЛЮЧЕЙ)
    clean_cmd = "TRUNCATE visits, visit_locations, short_urls, tags CASCADE;"
    ssh_exec(f"cd {REMOTE_DIR} && docker compose exec -T db psql -U shlink -d shlink -c '{clean_cmd}'")
    time.sleep(5)


def get_prometheus_metrics(start_ts, end_ts):
    def query_prom(query):
        try:
            url = f"{PROMETHEUS_URL}/api/v1/query_range"
            res = requests.get(url, params={"query": query, "start": start_ts, "end": end_ts, "step": "10s"}, timeout=5).json()
            values = [float(v[1]) for s in res["data"]["result"] for v in s["values"]]
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
    csv_prefix = f"reports_honest/{exp_name}"
    os.makedirs("reports_honest", exist_ok=True)

    start_ts = time.time()
    cmd = [
        "locust", "-f", "locustfile.py", "--host", APP_URL, "--headless",
        "-u", str(USERS), "-r", str(SPAWN_RATE), "--run-time", TEST_DURATION,
        f"--csv={csv_prefix}", "--only-summary", "--exit-code-on-error=0"
    ]
    print(f"   [LOCUST] Running test ({TEST_DURATION})...")
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    end_ts = time.time()

    df = pd.read_csv(f"{csv_prefix}_stats.csv")
    agg = df[df["Name"] == "Aggregated"].iloc[0]
    prom = get_prometheus_metrics(start_ts, end_ts)

    return {
        "requests": int(agg["Request Count"]),
        "fails": int(agg["Failure Count"]),
        "rps": round(float(agg["Requests/s"]), 2),
        "avg_ms": round(float(agg["Average Response Time"]), 1),
        "p95_ms": round(float(agg["95%"]), 1),
        **prom
    }


# ==============================================================================
# 3. МАШИННОЕ ОБУЧЕНИЕ И ГЕНЕТИЧЕСКИЙ АЛГОРИТМ
# ==============================================================================
def suggest_next_candidate(history_df, tested_set):
    # Очищаем историю от грубых зависаний (>40 сек)
    clean_df = history_df[history_df["p95_ms"] < 40000].copy()
    
    # Агрегация дубликатов по среднему
    grouped = clean_df.groupby(GENE_KEYS, as_index=False).agg({
        "rps": "mean",
        "p95_ms": "mean"
    })

    encoder = OneHotEncoder(sparse_output=False, handle_unknown="ignore")
    X = encoder.fit_transform(grouped[GENE_KEYS].astype(str))

    model_rps = GradientBoostingRegressor(n_estimators=100, max_depth=3, random_state=42).fit(X, grouped["rps"])
    model_p95 = GradientBoostingRegressor(n_estimators=100, max_depth=3, random_state=42).fit(X, grouped["p95_ms"])

    # Целевая функция эффективности: максимизируем RPS, штрафуя за рост задержки p95
    def fitness_fn(ind):
        feat = encoder.transform(pd.DataFrame([[str(ind[k]) for k in GENE_KEYS]], columns=GENE_KEYS))
        pred_rps = model_rps.predict(feat)[0]
        pred_p95 = model_p95.predict(feat)[0]
        
        # Штраф начисляется только если задержка выходит за комфортные 4000 мс
        if pred_p95 > MAX_ALLOWED_P95_MS:
            penalty = ((pred_p95 - MAX_ALLOWED_P95_MS) / 500.0) * 10.0
            score = pred_rps - penalty
        else:
            score = pred_rps
            
        return score, pred_rps, pred_p95

    # Генетический поиск
    pop = [{k: random.choice(GENE_SPACE[k]) for k in GENE_KEYS} for _ in range(80)]
    for _ in range(50):
        scored = [(ind, fitness_fn(ind)) for ind in pop]
        scored.sort(key=lambda x: x[1][0], reverse=True)
        survivors = [x[0] for x in scored[:10]]
        next_pop = survivors.copy()
        while len(next_pop) < 80:
            p1, p2 = random.sample(survivors, 2)
            child = {k: p1[k] if random.random() < 0.5 else p2[k] for k in GENE_KEYS}
            if random.random() < 0.2:
                m = random.choice(GENE_KEYS)
                child[m] = random.choice(GENE_SPACE[m])
            next_pop.append(child)
        pop = next_pop

    scored = [(ind, fitness_fn(ind)) for ind in pop]
    scored.sort(key=lambda x: x[1][0], reverse=True)

    # Ищем лучшего среди тех, кто еще не был запущен
    for ind, (fit, rps_est, p95_est) in scored:
        ind_tuple = tuple(str(ind[k]) for k in GENE_KEYS)
        if ind_tuple not in tested_set:
            return ind, rps_est, p95_est

    # Если топ изучен, делаем разведочную мутацию
    fallback = scored[0][0].copy()
    m = random.choice(GENE_KEYS)
    fallback[m] = random.choice(GENE_SPACE[m])
    _, rps_est, p95_est = fitness_fn(fallback)
    return fallback, rps_est, p95_est


# ==============================================================================
# 4. ГЛАВНЫЙ ЦИКЛ ОПТИМИЗАЦИИ
# ==============================================================================
def main():
    print("=" * 75)
    print("🚀 СТАРТ АВТОНОМНОГО ОПТИМИЗАТОРА (С ПАМЯТЬЮ И ЭКСПОРТОМ ЧЕМПИОНА)")
    print(f"   Длительность шага : {TEST_DURATION} (10 минут)")
    print(f"   Лимит терпения    : {PATIENCE_LIMIT} попытки без улучшений")
    print("=" * 75)

    if not os.path.exists(DATASET_FILE):
        print(f"[ERROR] Файл {DATASET_FILE} не найден!")
        sys.exit(1)

    df_history = pd.read_csv(DATASET_FILE)
    df_history = df_history.rename(columns={"_exp_name": "name", "p95_latency_ms": "p95_ms", "avg_latency_ms": "avg_ms"})

    session_results = []
    tested_set = set()
    for _, r in df_history.iterrows():
        tested_set.add(tuple(str(r[k]) for k in GENE_KEYS))

    # Находим абсолютный рекорд во всей истории (с учетом стабильности p95 < 4000)
    valid_history = df_history[df_history["p95_ms"] < MAX_ALLOWED_P95_MS]
    if not valid_history.empty:
        best_row = valid_history.sort_values(by="rps", ascending=False).iloc[0]
        current_best_rps = float(best_row["rps"])
        best_candidate = {k: str(best_row[k]) for k in GENE_KEYS}
        best_candidate["name"] = str(best_row["name"])
        print(f"[INIT] Текущий рекорд из истории: {current_best_rps:.2f} RPS (Тест: {best_row['name']})")
    else:
        current_best_rps = 0.0
        best_candidate = {}

    patience_counter = 0
    iteration = 1

    try:
        while patience_counter < PATIENCE_LIMIT:
            print(f"\n" + "-" * 75)
            print(f"🌀 ИТЕРАЦИЯ #{iteration} (Неудач подряд: {patience_counter}/{PATIENCE_LIMIT} | Рекорд для победы: {current_best_rps:.2f} RPS)")
            print("-" * 75)

            candidate, rps_est, p95_est = suggest_next_candidate(df_history, tested_set)
            exp_name = f"opt_loop_iter_{iteration:02d}"
            candidate["name"] = exp_name

            print(f"[ML] Модель предложила гипотезу (Прогноз: RPS={rps_est:.1f}, p95={p95_est:.0f}ms):")
            for k in GENE_KEYS:
                print(f"     {k:22s} : {candidate[k]}")

            apply_remote_config(candidate)
            restart_remote_system()
            test_res = run_locust(exp_name)

            real_rps = test_res["rps"]
            real_p95 = test_res["p95_ms"]
            print(f"   [ФАКТ] RPS: {real_rps:.2f} | p95: {real_p95}ms | CPU БД: {test_res['db_cpu_max_%']}%")

            # Записываем в датасет и историю
            new_row = {**candidate, **test_res}
            session_results.append(new_row)
            df_history = pd.concat([df_history, pd.DataFrame([new_row])], ignore_index=True)
            tested_set.add(tuple(str(candidate[k]) for k in GENE_KEYS))

            # Сохраняем на диск сразу после каждого шага
            df_history.to_csv(DATASET_FILE, index=False)
            pd.DataFrame(session_results).to_csv(SESSION_RESULTS_FILE, index=False)

            # Проверка улучшения: смотрим на RPS при условии нормальной задержки
            if real_p95 <= MAX_ALLOWED_P95_MS and real_rps > (current_best_rps + 0.3):
                gain = real_rps - current_best_rps
                print(f"   🎉 НОВЫЙ РЕКОРД! RPS вырос с {current_best_rps:.2f} до {real_rps:.2f} (+{gain:.2f} RPS)!")
                current_best_rps = real_rps
                best_candidate = candidate.copy()
                patience_counter = 0
            else:
                patience_counter += 1
                print(f"   ⚠️ Кандидат не побил текущий рекорд ({current_best_rps:.2f} RPS). Счетчик: {patience_counter}/{PATIENCE_LIMIT}")

            iteration += 1

    finally:
        # ==============================================================================
        # АВТОМАТИЧЕСКИЙ ЭКСПОРТ ЛУЧШЕЙ КОНФИГУРАЦИИ
        # ==============================================================================
        print("\n" + "=" * 75)
        print("🏁 ОПТИМИЗАЦИЯ ЗАВЕРШЕНА!")
        print(f"   Абсолютный чемпион показал: {current_best_rps:.2f} RPS")
        print("=" * 75)

        # Сохраняем в JSON (удобно для скриптов)
        champion_data = {
            "record_rps": current_best_rps,
            "configuration": {k: best_candidate.get(k, "") for k in GENE_KEYS}
        }
        with open(CHAMPION_EXPORT_JSON, "w", encoding="utf-8") as f:
            json.dump(champion_data, f, indent=4)

        # Сохраняем в читаемый TXT (для отчета)
        with open(CHAMPION_EXPORT_TXT, "w", encoding="utf-8") as f:
            f.write("=" * 60 + "\n")
            f.write(f"ЛУЧШАЯ НАЙДЕННАЯ КОНФИГУРАЦИЯ (РЕКОРД: {current_best_rps:.2f} RPS)\n")
            f.write("=" * 60 + "\n\n")
            for k in GENE_KEYS:
                f.write(f"{k:22s} : {best_candidate.get(k, '')}\n")

        print(f"[ВЫГРУЗКА] Конфигурация победителя сохранена в:")
        print(f"   • {CHAMPION_EXPORT_JSON}")
        print(f"   • {CHAMPION_EXPORT_TXT}")
        print("=" * 75)


if __name__ == "__main__":
    main()
