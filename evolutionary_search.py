import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.preprocessing import OneHotEncoder
import random

# ==============================================================================
# 1. ЗАГРУЗКА И ФОРМИРОВАНИЕ ОБУЧАЮЩИХ ДАННЫХ ИЗ БЕНЧМАРКА
# ==============================================================================
data = pd.read_csv('benchmark_dataset_notebook.csv')

df = data

# Пространство генов (допустимые аллели каждого параметра)
GENE_SPACE = {
    "synchronous_commit": ["on", "off"],
    "shared_buffers": ["128MB", "256MB", "384MB", "512MB"],
    "wal_buffers": ["-1", "16MB", "32MB"],
    "max_wal_size": ["1GB", "2GB", "4GB"],
    "work_mem": ["4MB", "8MB", "16MB", "32MB"],
    "random_page_cost": ["1.1", "4.0"],
    "DISABLE_IP_TRACKING": ["false", "true"],
    "WEB_WORKER_NUM": ["2", "4", "8", "16"],
    "MEMORY_LIMIT": ["256M", "512M", "768M", "-1"]
}

GENE_KEYS = list(GENE_SPACE.keys())

# Целевая метрика приспособленности (Fitness Score):
# Максимизируем RPS, одновременно штрафуя за высокую задержку p95
df["fitness"] = df["rps"] / (np.log1p(df["p95_latency_ms"]) / np.log(10))

# Обучаем суррогатную модель градиентного бустинга для аппроксимации фитнеса
encoder = OneHotEncoder(sparse_output=False, handle_unknown="ignore")
X_encoded = encoder.fit_transform(df[GENE_KEYS].astype(str))
surrogate_model = GradientBoostingRegressor(n_estimators=100, random_state=42)
surrogate_model.fit(X_encoded, df["fitness"])


def predict_fitness(individual):
    """Оценка приспособленности особи суррогатной моделью"""
    ind_df = pd.DataFrame([[str(individual[k]) for k in GENE_KEYS]], columns=GENE_KEYS)
    features = encoder.transform(ind_df)
    return surrogate_model.predict(features)[0]


# ==============================================================================
# 2. ГЕНЕТИЧЕСКИЙ АЛГОРИТМ (GENETIC ALGORITHM)
# ==============================================================================
POPULATION_SIZE = 40
GENERATIONS = 50
MUTATION_RATE = 0.15
ELITISM_COUNT = 4


def create_random_individual():
    """Создание случайной особи"""
    return {k: random.choice(GENE_SPACE[k]) for k in GENE_KEYS}


def crossover(parent1, parent2):
    """Равномерный кроссовер (Uniform Crossover)"""
    child = {}
    for k in GENE_KEYS:
        child[k] = parent1[k] if random.random() < 0.5 else parent2[k]
    return child


def mutate(individual):
    """Мутация гена со случайной вероятностью"""
    mutated = individual.copy()
    for k in GENE_KEYS:
        if random.random() < MUTATION_RATE:
            mutated[k] = random.choice(GENE_SPACE[k])
    return mutated


def run_genetic_algorithm():
    random.seed(42)
    np.random.seed(42)

    # Инициализация популяции
    population = [create_random_individual() for _ in range(POPULATION_SIZE)]

    best_individual = None
    best_fitness = -float("inf")

    print(f"=== Запуск генетического поиска ({GENERATIONS} поколений, популяция {POPULATION_SIZE}) ===")

    for gen in range(GENERATIONS):
        # Оценка фитнеса каждого кандидата
        scored_population = [(ind, predict_fitness(ind)) for ind in population]
        scored_population.sort(key=lambda x: x[1], reverse=True)

        current_best, current_fitness = scored_population[0]
        if current_fitness > best_fitness:
            best_fitness = current_fitness
            best_individual = current_best

        if (gen + 1) % 10 == 0 or gen == 0:
            print(f"Поколение {gen+1:02d} | Лучший Fitness: {best_fitness:.2f} | RPS_est: {best_fitness * 4:.1f}")

        # Элитизм: сохраняем лучших без изменений
        next_population = [ind for ind, _ in scored_population[:ELITISM_COUNT]]

        # Турнирный отбор и скрещивание
        while len(next_population) < POPULATION_SIZE:
            # Турнир из 3 особей
            t1 = random.sample(scored_population[:20], 3)
            p1 = max(t1, key=lambda x: x[1])[0]

            t2 = random.sample(scored_population[:20], 3)
            p2 = max(t2, key=lambda x: x[1])[0]

            child = crossover(p1, p2)
            child = mutate(child)
            next_population.append(child)

        population = next_population

    return best_individual, best_fitness


# ==============================================================================
# 3. РЕЗУЛЬТАТ ПОИСКА
# ==============================================================================
champion, score = run_genetic_algorithm()

print("\n" + "=" * 70)
print("🏆 ОПТИМАЛЬНАЯ КОНФИГУРАЦИЯ (ЧЕМПИОН ГЕНЕТИЧЕСКОГО СКРЕЩИВАНИЯ):")
print("=" * 70)
for k, v in champion.items():
    print(f"  {k:22s} : {v}")
print("-" * 70)
print(f"Итоговый оценочный Fitness: {score:.2f}")
print("=" * 70)