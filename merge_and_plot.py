import pandas as pd
import subprocess

# 1. Читаем оба датасета
main_df = pd.read_csv("full_benchmark_dataset.csv")
ab_df = pd.read_csv("final_ab_comparison.csv")

# 2. Убираем старую строку 99_champion из основного датасета
main_df = main_df[main_df["name"] != "99_champion"].copy()

# 3. Берем новую строку чемпиона из ab_df (называем ее красиво для графика)
champion_row = ab_df[ab_df["name"] == "99_champion_ga"].copy()
champion_row["name"] = "99_champion_ga"

# 4. Объединяем и сохраняем
updated_df = pd.concat([main_df, champion_row], ignore_index=True)
updated_df.to_csv("full_benchmark_dataset.csv", index=False)
print("[OK] Датасет full_benchmark_dataset.csv успешно обновлен результатами Чемпиона!")

# 5. Автоматически перерисовываем графики
subprocess.run(["python", "generate_charts.py"])