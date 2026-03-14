"""Flask application factory for the MiroFish backend."""

import os
import importlib
import warnings

# 抑制 multiprocessing resource_tracker 的警告（来自第三方库如 transformers）
# 需要在所有其他导入之前设置
warnings.filterwarnings("ignore", message=".*resource_tracker.*")

from flask import Flask, request
from flask_cors import CORS

from .config import Config
from .utils.logger import setup_logger, get_logger


def create_app(config_class=Config):
    """Создает и настраивает Flask-приложение."""
    app = Flask(__name__)
    app.config.from_object(config_class)
    
    # Отключаем ASCII-экранирование в JSON
    if hasattr(app, 'json') and hasattr(app.json, 'ensure_ascii'):
        app.json.ensure_ascii = False
    
    # 设置日志
    logger = setup_logger('mirofish')
    
    # 只在 reloader 子进程中打印启动信息（避免 debug 模式下打印两次）
    is_reloader_process = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    debug_mode = app.config.get('DEBUG', False)
    should_log_startup = not debug_mode or is_reloader_process
    
    if should_log_startup:
        logger.info("=" * 50)
        logger.info("Starting MiroFish backend...")
        logger.info("=" * 50)
    
    # 启用CORS
    CORS(app, resources={r"/api/*": {"origins": "*"}})
    
    # 请求日志中间件
    @app.before_request
    def log_request():
        logger = get_logger('mirofish.request')
        logger.debug(f"Request: {request.method} {request.path}")
        if request.content_type and 'json' in request.content_type:
            logger.debug(f"Payload: {request.get_json(silent=True)}")
    
    @app.after_request
    def log_response(response):
        logger = get_logger('mirofish.request')
        logger.debug(f"Response: {response.status_code}")
        return response
    
    startup_errors = []

    # Регистрируем blueprints по одному, чтобы единичный импорт не валил весь backend.
    from .api import graph_bp, simulation_bp, report_bp
    blueprint_modules = [
        (".api.graph", graph_bp, "/api/graph"),
        (".api.simulation", simulation_bp, "/api/simulation"),
        (".api.report", report_bp, "/api/report"),
    ]

    for module_name, blueprint, url_prefix in blueprint_modules:
        try:
            importlib.import_module(module_name, __name__)
            app.register_blueprint(blueprint, url_prefix=url_prefix)
        except Exception as exc:
            error_info = {
                "module": module_name,
                "error": str(exc),
            }
            startup_errors.append(error_info)
            logger.exception(f"Не удалось зарегистрировать {module_name}: {exc}")
    
    # 健康检查
    @app.route('/')
    def root():
        status = 'ok' if not startup_errors else 'degraded'
        response = {'status': status, 'service': 'MiroFish Backend'}
        if startup_errors:
            response['startup_errors'] = startup_errors
        return response

    @app.route('/health')
    def health():
        status = 'ok' if not startup_errors else 'degraded'
        response = {'status': status, 'service': 'MiroFish Backend'}
        if startup_errors:
            response['startup_errors'] = startup_errors
        return response
    
    if should_log_startup:
        logger.info("MiroFish backend started")
    
    return app
