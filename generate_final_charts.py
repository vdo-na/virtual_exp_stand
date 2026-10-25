import os
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np

CSV_FILE = "full_benchmark_dataset.csv"

if not os.path.exists(CSV_FILE):
    print(f"[ERROR] Файл {CSV_FILE} не найден!")
    exit(1)

df = pd.read_csv(CSV_FILE)

# 1. Отфильтровываем крашнутые замеры с таймаутом 60 секунд (99_champion и active_iter_03)
# Они искажают масштаб осей задержки
df_clean = df[df["p95_ms"] < 30000].copy()

# Находим реальный базовый RPS (первая строчка 00_baseline)
base_row = df_clean[df_clean["name"] == "00_baseline"]
base_rps = float(base_row["rps"].values[0]) if not base_row.empty else 72.12
base_p95 = float(base_row["p95_ms"].values[0]) if not base_row.empty else 2500.0

# Считаем относительное отклонение от базы в процентах
df_clean["delta_rps_%"] = ((df_clean["rps"] - base_rps) / base_rps) * 100

# Цветовая маркировка точек
def assign_color(row):
    name = str(row["name"])
    rps = float(row["rps"])
    if name == "opt_loop_iter_03":
        return "#2ecc71"  # Ярко-зеленый (Абсолютный Чемпион 86.76 RPS)
    elif name == "00_baseline":
        return "#7f8c8d"  # Серый (База 72.12 RPS)
    elif rps < 65.0:
        return "#e74c3c"  # Красный (Неудачные мутации / деградация)
    elif rps >= 80.0:
        return "#3498db"  # Синий (Высокоэффективная группа 80+ RPS)
    else:
        return "#34495e"  # Темный сланец (Стандартные замеры 70-79 RPS)

df_clean["color"] = df_clean.apply(assign_color, axis=1)

os.makedirs("report_plots", exist_ok=True)
sns.set_theme(style="whitegrid", font="sans-serif")
plt.rcParams.update({
    "font.size": 9,
    "axes.titlesize": 11,
    "axes.labelsize": 10,
    "figure.titlesize": 14,
    "figure.dpi": 300
})

# ==============================================================================
# СБОРНЫЙ МАСТЕР-ДАШБОРД (4 В 1)
# ==============================================================================
fig, axes = plt.subplots(2, 2, figsize=(18, 14))
fig.suptitle("Итоговый отчет оптимизации Shlink Cluster (Cloud Linux Environment)", fontsize=16, fontweight="bold", y=0.99)

# ------------------------------------------------------------------------------
# График 1: Хронология поиска и рост RPS (Iteration Progress)
# ------------------------------------------------------------------------------
ax1 = axes[0, 0]
x_indices = range(len(df_clean))
bars1 = ax1.bar(x_indices, df_clean["rps"], color=df_clean["color"], width=0.7)
ax1.axhline(base_rps, color="#e67e22", linestyle="--", linewidth=1.5, label=f"Baseline ({base_rps:.2f} RPS)")
ax1.axhline(86.76, color="#2ecc71", linestyle=":", linewidth=1.5, label="Champion: opt_loop_iter_03 (86.76 RPS)")

ax1.set_title("1. Динамика пропускной способности по всем прогонам (RPS)", fontweight="bold")
ax1.set_xlabel("Порядковый номер эксперимента")
ax1.set_ylabel("RPS (Больше = Лучше)")
ax1.set_ylim(50, 95)
ax1.legend(loc="upper left")

# Подписываем чемпиона и базу
for i, (name, rps) in enumerate(zip(df_clean["name"], df_clean["rps"])):
    if name in ["00_baseline", "opt_loop_iter_03"]:
        ax1.text(i, rps + 1.2, f"{rps:.1f}", ha="center", va="bottom", fontsize=8, fontweight="bold")

# ------------------------------------------------------------------------------
# График 2: Карта Парето (Задержка vs RPS)
# ------------------------------------------------------------------------------
ax2 = axes[0, 1]
ax2.scatter(df_clean["avg_ms"], df_clean["rps"], c=df_clean["color"], s=90, edgecolors="black", linewidth=0.8, zorder=3)

# Аннотации ключевых точек
for _, r in df_clean.iterrows():
    if r["name"] in ["00_baseline", "opt_loop_iter_03", "active_iter_03", "WEB_WORKER_NUM_4"]:
        ax2.annotate(
            r["name"],
            (r["avg_ms"], r["rps"]),
            xytext=(8, 4), textcoords="offset points",
            fontsize=8, fontweight="bold"
        )

ax2.set_title("2. Карта Парето: Средняя задержка vs Пропускная способность", fontweight="bold")
ax2.set_xlabel("Средняя задержка ответа, мс (Меньше = Лучше)")
ax2.set_ylabel("RPS (Больше = Лучше)")
# Квадрант лучшей эффективности
ax2.axvspan(1000, 1200, ymin=0.65, ymax=1.0, color="#2ecc71", alpha=0.1, label="Зона максимума (Низкая задержка + Высокий RPS)")
ax2.legend(loc="lower left")

# ------------------------------------------------------------------------------
# График 3: ТОП-10 Лучших конфигураций (Рейтинг победителей)
# ------------------------------------------------------------------------------
ax3 = axes[1, 0]
top10 = df_clean.sort_values(by="rps", ascending=True).tail(10)
bars3 = ax3.barh(top10["name"], top10["rps"], color=top10["color"], height=0.65)
ax3.axvline(base_rps, color="#e67e22", linestyle="--", linewidth=1.2, label=f"Baseline ({base_rps:.1f})")

for b in bars3:
    w = b.get_width()
    ax3.text(w + 0.4, b.get_y() + b.get_height()/2, f"{w:.2f}", va="center", fontsize=8, fontweight="bold")

ax3.set_title("3. Топ-10 наиболее производительных конфигураций (RPS)", fontweight="bold")
ax3.set_xlabel("RPS")
ax3.set_xlim(65, 92)
ax3.legend(loc="lower right")

# ------------------------------------------------------------------------------
# График 4: Разгрузка задержек (Сравнение спектра задержек Базы и Чемпиона)
# ------------------------------------------------------------------------------
ax4 = axes[1, 1]
compare_names = ["00_baseline", "opt_loop_iter_03"]
compare_df = df_clean[df_clean["name"].isin(compare_names)].set_index("name").loc[compare_names].reset_index()

x = np.arange(len(compare_names))
w = 0.35

bars_avg = ax4.bar(x - w/2, compare_df["avg_ms"], width=w, label="Средняя задержка (Avg, ms)", color="#3498db")
bars_p95 = ax4.bar(x + w/2, compare_df["p95_ms"], width=w, label="Худшая задержка (p95, ms)", color="#e67e22")

for b in bars_avg:
    h = b.get_height()
    ax4.text(b.get_x() + b.get_width()/2, h + 30, f"{h:.0f} ms", ha="center", fontsize=8, fontweight="bold")
for b in bars_p95:
    h = b.get_height()
    ax4.text(b.get_x() + b.get_width()/2, h + 30, f"{h:.0f} ms", ha="center", fontsize=8, fontweight="bold")

ax4.set_title("4. Снижение задержек: Базовый профиль vs Чемпион", fontweight="bold")
ax4.set_xticks(x)
ax4.set_xticklabels(["00_baseline (Исходная)", "opt_loop_iter_03 (Чемпион)"], fontweight="bold")
ax4.set_ylabel("Задержка, миллисекунды (Меньше = Лучше)")
ax4.set_ylim(0, 3000)
ax4.legend(loc="upper right")

plt.tight_layout()
output_img = "report_plots/00_master_final_report.png"
plt.savefig(output_img, bbox_inches="tight")
print(f"[УСПЕХ] Главный итоговый дашборд сохранен: {output_img}")
plt.close()