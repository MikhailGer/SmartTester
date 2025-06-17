import json

# Путь к исходному лог-файлу
INPUT_FILE = "log_examples/user_session_1746808617612.json"
OUTPUT_FILE = "sorted_log6.json"

# Загрузка логов
with open(INPUT_FILE, "r", encoding="utf-8") as f:
    events = json.load(f)

# Сортировка по timestamp
sorted_events = sorted(events, key=lambda e: e.get("timestamp", 0))

# Сохранение отсортированного лога
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(sorted_events, f, ensure_ascii=False, indent=2)

print(f"✔️ Лог отсортирован по timestamp и сохранён в {OUTPUT_FILE}")
