"""Точка входа backend-сервиса MiroFish."""

import os
import sys

# На Windows заранее включаем UTF-8, чтобы избежать проблем с консольным выводом
if sys.platform == 'win32':
    # Просим Python использовать UTF-8
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    # Перенастраиваем stdout/stderr на UTF-8
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Добавляем корень проекта в sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from app.config import Config


def main():
    """Основная функция запуска."""
    # Проверяем конфигурацию
    errors = Config.validate()
    if errors:
        print("Configuration errors:")
        for err in errors:
            print(f"  - {err}")
        print("\nCheck the .env configuration")
        sys.exit(1)
    
    # Создаем приложение
    app = create_app()
    
    # Получаем параметры запуска
    host = os.environ.get('FLASK_HOST', '0.0.0.0')
    port = int(os.environ.get('FLASK_PORT', 5001))
    debug = Config.DEBUG
    
    # Запускаем сервис
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == '__main__':
    main()
