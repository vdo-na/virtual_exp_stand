import random
from remote_benchmark_runner import apply_remote_config, restart_remote_system, run_locust, RESULTS_CSV
import pandas as pd

HYBRID_SPACE = {
    "synchronous_commit": ["off", "on"],
    "shared_buffers": ["256MB", "384MB"],
    "wal_buffers": ["16MB", "32MB"],
    "max_wal_size": ["1GB", "2GB"],
    "work_mem": ["8MB", "16MB"],
    "random_page_cost": ["4.0"],
    "DISABLE_IP_TRACKING": ["true"],
    "WEB_WORKER_NUM": ["2", "4"],       # 4 воркера показали отличный результат
    "MEMORY_LIMIT": ["256M", "512M"]
}

print("=== Starting 10 Hybrid/Random Combinations ===")
existing_df = pd.read_csv(RESULTS_CSV) if pd.io.common.file_exists(RESULTS_CSV) else pd.DataFrame()
new_rows = []

for i in range(1, 11):
    cfg = {k: random.choice(v) for k, v in HYBRID_SPACE.items()}
    cfg["name"] = f"hybrid_{i:02d}"
    print(f"\n>>> Running Hybrid {i}/10: {cfg['name']}...")
    
    apply_remote_config(cfg)
    restart_remote_system()
    data = run_locust(cfg["name"])
    
    print(f"    RESULT: RPS = {data['rps']} | p95 = {data['p95_ms']}ms | DB CPU = {data['db_cpu_max_%']}%")
    row = {**cfg, **data}
    new_rows.append(row)
    
    # Дописываем результаты в датасет
    combined = pd.concat([existing_df, pd.DataFrame(new_rows)], ignore_index=True)
    combined.to_csv(RESULTS_CSV, index=False)

print("\n[DONE] 10 hybrids completed and appended to dataset!")
