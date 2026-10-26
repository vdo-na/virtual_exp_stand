import os
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np

CSV_FILE = "full_benchmark_dataset.csv"
if not os.path.exists(CSV_FILE):
    print(f"Файл {CSV_FILE} не найден!")
    exit(1)

df = pd.read_csv(CSV_FILE)

# Исключаем аварийные строки (>35 сек задержки)
df_clean = df[df["p95_ms"] < 35000].copy()

# Расчет значения целевой функции (Fitness)
df_clean["fitness"] = df_clean["rps"] / (np.log10(df_clean["p95_ms"]) + 0.1)

os.makedirs("report_plots", exist_ok=True)
sns.set_theme(style="whitegrid", font="sans-serif")
plt.rcParams.update({"figure.dpi": 300, "font.size": 10})

fig, axes = plt.subplots(2, 2, figsize=(16, 12))
fig.suptitle("Зависимость целевой функции и производительности от ключевых параметров", fontsize=15, fontweight="bold", y=0.98)

# 1. Зависимость RPS от числа воркеров (WEB_WORKER_NUM)
ax1 = axes[0, 0]
sns.boxplot(data=df_clean, x="WEB_WORKER_NUM", y="rps", palette="Blues", ax=ax1, width=0.5)
sns.stripplot(data=df_clean, x="WEB_WORKER_NUM", y="rps", color="black", alpha=0.6, jitter=0.2, size=7, ax=ax1)
ax1.set_title("А. Влияние пула воркеров (WEB_WORKER_NUM) на Throughput", fontweight="bold")
ax1.set_xlabel("Количество воркеров RoadRunner")
ax1.set_ylabel("RPS (Запросов в секунду)")

# 2. Влияние work_mem на целевую функцию (Fitness)
ax2 = axes[0, 1]
# Сортируем порядок категорий work_mem
order_wm = ["4MB", "8MB", "16MB", "32MB"]
wm_data = df_clean[df_clean["work_mem"].isin(order_wm)]
sns.barplot(data=wm_data, x="work_mem", y="fitness", order=order_wm, palette="crest", errorbar=None, ax=ax2, width=0.6)
ax2.set_title("Б. Зависимость интегральной целевой функции от work_mem", fontweight="bold")
ax2.set_xlabel("Объем памяти сортировок (work_mem)")
ax2.set_ylabel("Значение фитнес-функции (Score)")

# 3. Влияние max_wal_size на пиковую задержку p95
ax3 = axes[1, 0]
order_wal = ["1GB", "2GB", "4GB"]
wal_data = df_clean[df_clean["max_wal_size"].isin(order_wal)]
sns.barplot(data=wal_data, x="max_wal_size", y="p95_ms", order=order_wal, palette="Reds_r", errorbar=None, ax=ax3, width=0.6)
ax3.set_title("В. Зависимость задержки p95 от размера WAL (max_wal_size)", fontweight="bold")
ax3.set_xlabel("Размер журнала WAL")
ax3.set_ylabel("Задержка p95, мс (Меньше = Лучше)")

# 4. Траектория сходимости целевой функции в ходе итераций поиска (Convergence Curve)
ax4 = axes[1, 1]
# Берем непрерывную траекторию поиска
iterations = range(1, len(df_clean) + 1)
running_max_rps = df_clean["rps"].cummax()

ax4.plot(iterations, df_clean["rps"], marker="o", markersize=5, linestyle="-", color="#7f8c8d", alpha=0.5, label="Текущий замер итерации")
ax4.plot(iterations, running_max_rps, linestyle="-", linewidth=2.5, color="#27ae60", label="Текущий рекорд (Best Found)")
ax4.axhline(72.12, color="#e67e22", linestyle="--", label="Исходный Baseline (72.12 RPS)")
ax4.axhline(86.76, color="#2ecc71", linestyle=":", label="Глобальный максимум (86.76 RPS)")

ax4.set_title("Г. Сходимость оптимизационного процесса к глобальному оптимуму", fontweight="bold")
ax4.set_xlabel("Номер экспериментального прогона")
ax4.set_ylabel("RPS")
ax4.legend(loc="lower right")

plt.tight_layout()
plot_path = "report_plots/parameter_dependencies.png"
plt.savefig(plot_path, bbox_inches="tight")
print(f"[УСПЕХ] Графики зависимостей сохранены в: {plot_path}")