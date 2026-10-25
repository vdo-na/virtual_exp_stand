import os
import random
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.preprocessing import OneHotEncoder

# ==============================================================================
# 1. ЗАГРУЗКА И НОРМАЛИЗАЦИЯ ВСЕХ СЕРВЕРНЫХ ДАТАСЕТОВ
# ==============================================================================
DATASET_CONFIGS = [
    {"file": "full_benchmark_dataset_run2.csv", "duration_s": 120,  "weight": 1.0},
    {"file": "full_benchmark_dataset_run1.csv", "duration_s": 300,  "weight": 1.5},
    {"file": "full_benchmark_dataset.csv",      "duration_s": 1500, "weight": 3.0},
    {"file": "final_ab_comparison.csv",         "duration_s": 1500, "weight": 3.0}
]

dfs = []
for item in DATASET_CONFIGS:
    fname = item["file"]
    if os.path.exists(fname):
        temp_df = pd.read_csv(fname)
        # Нормализуем названия колонок
        temp_df = temp_df.rename(columns={
            "_exp_name": "name",
            "p95_latency_ms": "p95_ms",
            "avg_latency_ms": "avg_ms",
            "failed_requests": "fails",
            "total_requests": "requests"
        })
        temp_df["duration_s"] = item["duration_s"]
        temp_df["sample_weight"] = item["weight"]
        dfs.append(temp_df)
        print(f"[OK] Подключен датасет: {fname} ({len(temp_df)} строк, длительность: {item['duration_s']}s)")

if not dfs:
    print("[ERROR] Не найдено ни одного CSV-файла!")
    exit(1)

full_df = pd.concat(dfs, ignore_index=True)

# Исключаем аномальный 60-секундный OOM краш
full_df = full_df[full_df["p95_ms"] < 40000].copy()

print(f"\n[INFO] Итого объединенная обучающая выборка: {len(full_df)} замеров!")

GENE_KEYS = [
    "synchronous_commit", "shared_buffers", "wal_buffers", "max_wal_size",
    "work_mem", "random_page_cost", "DISABLE_IP_TRACKING", "WEB_WORKER_NUM", "MEMORY_LIMIT"
]

GENE_SPACE = {k: full_df[k].astype(str).unique().tolist() for k in GENE_KEYS}

# ==============================================================================
# 2. ОБУЧЕНИЕ СУРРОГАТНОЙ МОДЕЛИ С ВЕСАМИ ВЫБОРКИ
# ==============================================================================
encoder = OneHotEncoder(sparse_output=False, handle_unknown="ignore")
X_encoded = encoder.fit_transform(full_df[GENE_KEYS].astype(str))

# Модель RPS
model_rps = GradientBoostingRegressor(n_estimators=150, max_depth=4, random_state=42)
model_rps.fit(X_encoded, full_df["rps"], sample_weight=full_df["sample_weight"])

# Модель CPU базы данных
model_db_cpu = GradientBoostingRegressor(n_estimators=150, max_depth=4, random_state=42)
model_db_cpu.fit(X_encoded, full_df["db_cpu_max_%"], sample_weight=full_df["sample_weight"])

# ==============================================================================
# 3. ФИТНЕС С ЖЕСТКИМ КОНТРОЛЕМ ПЕРЕГРУЗКИ CPU БД (<= 191.0%)
# ==============================================================================
SAFE_CPU_LIMIT = 191.0

def evaluate(ind):
    features = encoder.transform(pd.DataFrame([[str(ind[k]) for k in GENE_KEYS]], columns=GENE_KEYS))
    pred_rps = model_rps.predict(features)[0]
    pred_cpu = model_db_cpu.predict(features)[0]
    
    # Жесткий барьер безопасности
    if pred_cpu > SAFE_CPU_LIMIT:
        fitness = pred_rps - ((pred_cpu - SAFE_CPU_LIMIT) * 20.0)
    else:
        fitness = pred_rps
        
    return fitness, pred_rps, pred_cpu

# ==============================================================================
# 4. ГЕНЕТИЧЕСКИЙ АЛГОРИТМ
# ==============================================================================
POP_SIZE = 80
GENS = 60
MUTATION_RATE = 0.15

random.seed(42)
np.random.seed(42)

population = [{k: random.choice(GENE_SPACE[k]) for k in GENE_KEYS} for _ in range(POP_SIZE)]

best_ind = None
best_fitness = -float("inf")
best_rps = 0
best_cpu = 0

print(f"\n=== Генетический поиск на расширенном датасете (Популяция {POP_SIZE}, Поколений {GENS}) ===")

for gen in range(GENS):
    evals = [evaluate(ind) for ind in population]
    scored = list(zip(population, evals))
    scored.sort(key=lambda x: x[1][0], reverse=True)
    
    cur_ind, (cur_fit, cur_rps, cur_cpu) = scored[0]
    if cur_fit > best_fitness:
        best_fitness = cur_fit
        best_ind = cur_ind
        best_rps = cur_rps
        best_cpu = cur_cpu
        
    # Элитизм (сохраняем топ-8)
    next_gen = [ind for ind, _ in scored[:8]]
    while len(next_gen) < POP_SIZE:
        p1, p2 = random.sample(next_gen[:25], 2)
        child = {k: p1[k] if random.random() < 0.5 else p2[k] for k in GENE_KEYS}
        if random.random() < MUTATION_RATE:
            m = random.choice(GENE_KEYS)
            child[m] = random.choice(GENE_SPACE[m])
        next_gen.append(child)
    population = next_gen

print("\n" + "=" * 70)
print("🏆 ИСТИННЫЙ ЧЕМПИОН, НАЙДЕННЫЙ НА ОБЪЕДИНЕННОМ ДАТАСЕТЕ (50+ ЗАМЕРОВ):")
print("=" * 70)
for k, v in best_ind.items():
    print(f"  {k:22s} : {v}")
print("-" * 70)
print(f"Прогнозируемый RPS     : {best_rps:.2f}")
status = "БЕЗОПАСНО" if best_cpu <= SAFE_CPU_LIMIT else "ВНИМАНИЕ: ПРЕВЫШЕНИЕ"
print(f"Прогнозируемый CPU БД  : {best_cpu:.2f}% [{status}, порог: {SAFE_CPU_LIMIT}%]")
print("=" * 70)