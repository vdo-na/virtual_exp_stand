import os
import sys
import random
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.preprocessing import OneHotEncoder

# ==============================================================================
# 1. ДИНАМИЧЕСКАЯ ЗАГРУЗКА И ОБЪЕДИНЕНИЕ ДАННЫХ (БЕЗ ХАРДКОДА)
# ==============================================================================
PRIMARY_CSV = "full_benchmark_dataset.csv"
AB_CSV = "final_ab_comparison.csv"

if not os.path.exists(PRIMARY_CSV):
    print(f"[ERROR] Основной файл {PRIMARY_CSV} не найден!")
    sys.exit(1)

print(f"[INFO] Загрузка основного датасета: {PRIMARY_CSV}")
df = pd.read_csv(PRIMARY_CSV)

# Если есть файл свежего A/B сравнения — объединяем их без дубликатов
if os.path.exists(AB_CSV):
    print(f"[INFO] Обнаружен файл A/B теста: {AB_CSV}. Объединяем...")
    ab_df = pd.read_csv(AB_CSV)
    df = pd.concat([df, ab_df], ignore_index=True)

# Нормализация имен колонок
df = df.rename(columns={
    "_exp_name": "name",
    "p95_latency_ms": "p95_ms",
    "avg_latency_ms": "avg_ms",
    "failed_requests": "fails",
    "total_requests": "requests"
})

# Устраняем дубликаты по имени, оставляя самые свежие строки
df = df.drop_duplicates(subset=["name"], keep="last").copy()

print(f"[INFO] Итого строк для обучения модели: {len(df)}")

GENE_KEYS = [
    "synchronous_commit", "shared_buffers", "wal_buffers", "max_wal_size",
    "work_mem", "random_page_cost", "DISABLE_IP_TRACKING", "WEB_WORKER_NUM", "MEMORY_LIMIT"
]

for k in GENE_KEYS:
    if k not in df.columns:
        raise KeyError(f"Колонка '{k}' отсутствует в данных!")

GENE_SPACE = {k: df[k].astype(str).unique().tolist() for k in GENE_KEYS}

# ==============================================================================
# 2. ОБУЧЕНИЕ СУРРОГАТНЫХ МОДЕЛЕЙ (RPS + CPU БД)
# ==============================================================================
encoder = OneHotEncoder(sparse_output=False, handle_unknown="ignore")
X_encoded = encoder.fit_transform(df[GENE_KEYS].astype(str))

# 1-я модель: прогнозирует пропускную способность (RPS)
model_rps = GradientBoostingRegressor(n_estimators=100, random_state=42)
model_rps.fit(X_encoded, df["rps"])

# 2-я модель: прогнозирует пиковую загрузку процессора базы данных
model_db_cpu = GradientBoostingRegressor(n_estimators=100, random_state=42)
model_db_cpu.fit(X_encoded, df["db_cpu_max_%"])

# ==============================================================================
# 3. ФУНКЦИЯ ПРИСПОСОБЛЕННОСТИ С ЖЕСТКИМ ПОРОГОМ БЕЗОПАСНОСТИ
# ==============================================================================
# Порог красной зоны CPU базы: 191.0% (95.5% от 2 ядер)
# Все, что выше 192% (как у 4 воркеров = 198%), вызывает взаимные блокировки
SAFE_CPU_THRESHOLD = 191.0

def evaluate(ind):
    features = encoder.transform(pd.DataFrame([[str(ind[k]) for k in GENE_KEYS]], columns=GENE_KEYS))
    pred_rps = model_rps.predict(features)[0]
    pred_cpu = model_db_cpu.predict(features)[0]
    
    # ЖЕСТКИЙ БАРЬЕР: если процессор уходит в красную зону — особь дисквалифицируется
    if pred_cpu > SAFE_CPU_THRESHOLD:
        # Тяжелый штраф, исключающий особь из элиты
        fitness = pred_rps - ((pred_cpu - SAFE_CPU_THRESHOLD) * 15.0)
    else:
        fitness = pred_rps
        
    return fitness, pred_rps, pred_cpu

# ==============================================================================
# 4. ГЕНЕТИЧЕСКИЙ АЛГОРИТМ
# ==============================================================================
POP_SIZE = 60
GENS = 50
MUTATION_RATE = 0.15

random.seed(42)
np.random.seed(42)

population = [{k: random.choice(GENE_SPACE[k]) for k in GENE_KEYS} for _ in range(POP_SIZE)]

best_ind = None
best_fitness = -float("inf")
best_rps = 0
best_cpu = 0

print(f"\n=== Запуск оптимизации (Жесткий лимит CPU БД <= {SAFE_CPU_THRESHOLD}%) ===")

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
        
    # Элитизм
    next_gen = [ind for ind, _ in scored[:6]]
    while len(next_gen) < POP_SIZE:
        p1, p2 = random.sample(next_gen[:20], 2)
        child = {k: p1[k] if random.random() < 0.5 else p2[k] for k in GENE_KEYS}
        if random.random() < MUTATION_RATE:
            m = random.choice(GENE_KEYS)
            child[m] = random.choice(GENE_SPACE[m])
        next_gen.append(child)
    population = next_gen

print("\n" + "=" * 65)
print("🏆 ИСПРАВЛЕННЫЙ ЧЕМПИОН С КОНТРОЛЕМ РЕСУРСОВ:")
print("=" * 65)
for k, v in best_ind.items():
    print(f"  {k:22s} : {v}")
print("-" * 65)
print(f"Прогнозируемый RPS     : {best_rps:.2f}")

status = "БЕЗОПАСНО" if best_cpu <= SAFE_CPU_THRESHOLD else "ВНИМАНИЕ: ПРЕВЫШЕНИЕ"
print(f"Прогнозируемый CPU БД  : {best_cpu:.2f}% [{status}, порог: {SAFE_CPU_THRESHOLD}%]")
print("=" * 65)