import os

# Структура папок и файлов для создания проекта
structure = {
    "handlers": [
        "__init__.py",
        "menu_handlers.py",
        "pc_handlers.py",
        "user_handlers.py"
    ],
    "services": [
        "__init__.py",
        "ad_service.py",
        "audit_service.py",
        "db_service.py",
        "logger_service.py",
        "print_service.py"
    ],
    "middlewares": [
        "__init__.py",
        "auth_middleware.py"
    ],
    "logs": []
}

def create_project_structure():
    print("🚀 Создаем структуру проекта...")
    
    # Создаем папки и файлы
    for folder, files in structure.items():
        os.makedirs(folder, exist_ok=True)
        print(f"📁 Папка создана: {folder}/")
        for file in files:
            file_path = os.path.join(folder, file)
            if not os.path.exists(file_path):
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write("")
                print(f"   📄 Файл создан: {file_path}")

    # Корневые файлы
    root_files = [".env", "config.py", "main.py", "requirements.txt"]
    for file in root_files:
        if not os.path.exists(file):
            with open(file, "w", encoding="utf-8") as f:
                f.write("")
            print(f"📄 Корневой файл создан: {file}")

    print("\n✅ Структура успешно создана! Можем наполнять файлы кодом.")

if __name__ == "__main__":
    create_project_structure()