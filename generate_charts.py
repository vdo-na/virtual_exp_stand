import io
import os
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np

# 1. Встраиваем ваши данные напрямую
df = pd.read_csv('benchmark_dataset_notebook.csv')
os.makedirs("report_plots", exist_ok=True)

# Стилизация графиков под академический стандарт
sns.set_theme(style="whitegrid", font="sans-serif")
plt.rcParams.update({
    "font.size": 10,
    "axes.titlesize": 12,
    "axes.labelsize": 10,
    "figure.titlesize": 15,
    "figure.dpi": 300
})

base_rps = df[df["_exp_name"] == "00_baseline"]["rps"].values[0]
df["delta_rps_%"] = ((df["rps"] - base_rps) / base_rps) * 100

# Цветовая кодировка
def get_color(name):
    if name == "99_best":
        return "#27ae60"  # Изумрудный для Чемпиона
    elif name == "13_DISABLE_IP_TRACKING_true":
        return "#2980b9"  # Синий для ключевого прорыва
    elif "WEB_WORKER" in name:
        return "#e74c3c"  # Красный для аномалии деградации
    elif name == "00_baseline":
        return "#7f8c8d"  # Серый для базы
    return "#34495e"      # Темно-синий для остальных

df["color"] = df["_exp_name"].apply(get_color)

# ==============================================================================
# СБОРНЫЙ ДАШБОРД (4 В 1)
# ==============================================================================
fig, axes = plt.subplots(2, 2, figsize=(18, 14))
fig.suptitle("Комплексный анализ оптимизации производительности Shlink Cluster", fontsize=16, fontweight="bold", y=0.99)

# 1. Рейтинг Throughput (RPS)
ax1 = axes[0, 0]
df_sorted_rps = df.sort_values(by="rps", ascending=True)
bars1 = ax1.barh(df_sorted_rps["_exp_name"], df_sorted_rps["rps"], color=df_sorted_rps["color"], height=0.7)
ax1.axvline(base_rps, color="#e67e22", linestyle="--", linewidth=1.5, label=f"Baseline ({base_rps:.1f} RPS)")
ax1.set_title("1. Рейтинг пропускной способности (Requests / Second)", fontweight="bold")
ax1.set_xlabel("RPS (Больше = Лучше)")
ax1.set_xlim(0, 90)
ax1.legend(loc="lower right")
for bar in bars1:
    w = bar.get_width()
    ax1.text(w + 1, bar.get_y() + bar.get_height()/2, f"{w:.1f}", va="center", fontsize=8, fontweight="bold")

# 2. Карта компромисса (Парето: RPS vs Latency)
ax2 = axes[0, 1]
scatter = ax2.scatter(
    df["avg_latency_ms"], df["rps"],
    c=df["color"], s=100, edgecolors="black", linewidth=1, zorder=3
)
ax2.set_title("2. Карта Парето: Задержка vs Пропускная способность", fontweight="bold")
ax2.set_xlabel("Средняя задержка, мс (Меньше = Лучше)")
ax2.set_ylabel("RPS (Больше = Лучше)")

# Аннотации ключевых точек
for _, row in df.iterrows():
    name = row["_exp_name"]
    if name in ["00_baseline", "99_best", "13_DISABLE_IP_TRACKING_true", "15_WEB_WORKER_NUM_8"]:
        ax2.annotate(
            name, (row["avg_latency_ms"], row["rps"]),
            xytext=(10, 5), textcoords="offset points", fontsize=9, fontweight="bold"
        )
# Выделение квадранта максимальной эффективности
ax2.axvspan(500, 700, ymin=0.6, ymax=1.0, color="#27ae60", alpha=0.1, label="Оптимальная зона (Высокий RPS / Низкая задержка)")
ax2.legend(loc="upper right")

# 3. Дельта изменений относительно Baseline (%)
ax3 = axes[1, 0]
df_delta = df[df["_exp_name"] != "00_baseline"].sort_values(by="delta_rps_%", ascending=True)
delta_colors = ["#27ae60" if x > 0 else "#e74c3c" for x in df_delta["delta_rps_%"]]
bars3 = ax3.barh(df_delta["_exp_name"], df_delta["delta_rps_%"], color=delta_colors, height=0.7)
ax3.axvline(0, color="black", linewidth=1)
ax3.set_title("3. Относительное отклонение RPS от Baseline (%)", fontweight="bold")
ax3.set_xlabel("Изменение производительности (%)")
ax3.set_xlim(-40, 45)
for bar in bars3:
    w = bar.get_width()
    pos_x = w + (1 if w >= 0 else -4.5)
    ax3.text(pos_x, bar.get_y() + bar.get_height()/2, f"{w:+.1f}%", va="center", fontsize=8, fontweight="bold")

# 4. Профиль задержек (Median vs Average vs p95 для топ-конфигураций)
ax4 = axes[1, 1]
key_configs = ["00_baseline", "13_DISABLE_IP_TRACKING_true", "99_best", "15_WEB_WORKER_NUM_8"]
df_sub = df[df["_exp_name"].isin(key_configs)].set_index("_exp_name").loc[key_configs].reset_index()

x = np.arange(len(key_configs))
width = 0.25

ax4.bar(x - width, df_sub["median_latency_ms"], width, label="Медиана (p50)", color="#3498db")
ax4.bar(x, df_sub["avg_latency_ms"], width, label="Средняя (Avg)", color="#9b59b6")
ax4.bar(x + width, df_sub["p95_latency_ms"], width, label="95-й процентиль (p95)", color="#e67e22")

ax4.set_title("4. Спектр задержек для ключевых архитектур (мс)", fontweight="bold")
ax4.set_xticks(x)
ax4.set_xticklabels(["Baseline", "No IP Tracking", "Champion (99_best)", "16 Workers (Троттлинг)"], rotation=15, ha="right")
ax4.set_ylabel("Задержка, миллисекунды (Меньше = Лучше)")
ax4.legend(loc="upper left")

plt.tight_layout()
dashboard_path = "report_plots/00_master_dashboard.png"
plt.savefig(dashboard_path, bbox_inches="tight")
print(f"[УСПЕХ] Главный дашборд сохранен: {dashboard_path}")
plt.close()