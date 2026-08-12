import os
import time
import random
import json
import logging
from datetime import datetime
from flask import Flask, request, jsonify
import requests

# ============================================
# Настройка OpenTelemetry
# ============================================
from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor

# Инициализация ресурса
resource = Resource.create({
    SERVICE_NAME: os.getenv("OTEL_SERVICE_NAME", "app-service"),
    "service.namespace": "demo",
    "service.version": "1.0.0",
    "deployment.environment": "development"
})

# Настройка трейсинга
tracer_provider = TracerProvider(resource=resource)
trace.set_tracer_provider(tracer_provider)
span_exporter = OTLPSpanExporter(
    endpoint=f"{os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://otel-collector:4318')}/v1/traces"
)
tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))

# Настройка метрик
metric_exporter = OTLPMetricExporter(
    endpoint=f"{os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://otel-collector:4318')}/v1/metrics"
)
metric_reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=10000)
meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
metrics.set_meter_provider(meter_provider)

# Настройка логирования
log_exporter = OTLPLogExporter(
    endpoint=f"{os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://otel-collector:4318')}/v1/logs"
)
log_provider = LoggerProvider(resource=resource)
log_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))

# Инструментация логирования Python
LoggingInstrumentor().instrument(set_logging_format=True)

# Настройка стандартного логгера
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ============================================
# Создание метрик
# ============================================
tracer = trace.get_tracer(__name__)
meter = metrics.get_meter(__name__)

# Счётчик запросов
request_counter = meter.create_counter(
    name="http.server.requests",
    description="Number of HTTP requests",
    unit="1"
)

# Гистограмма длительности запросов
request_duration = meter.create_histogram(
    name="http.server.duration",
    description="HTTP request duration",
    unit="ms"
)

# Счётчик ошибок
error_counter = meter.create_counter(
    name="http.server.errors",
    description="Number of HTTP errors",
    unit="1"
)

# ============================================
# Создание Flask приложения
# ============================================
app = Flask(__name__)

# Инструментируем Flask
FlaskInstrumentor().instrument_app(app)
# Инструментируем requests (для внешних вызовов)
RequestsInstrumentor().instrument()

@app.before_request
def before_request():
    """Логируем каждый запрос"""
    request.start_time = time.time()

@app.after_request
def after_request(response):
    """Записываем метрики после запроса"""
    duration = (time.time() - request.start_time) * 1000  # в миллисекундах

    request_counter.add(1, {
        "method": request.method,
        "endpoint": request.path
    })

    request_duration.record(duration, {
        "method": request.method,
        "endpoint": request.path
    })

    if response.status_code >= 400:
        error_counter.add(1, {
            "method": request.method,
            "endpoint": request.path,
            "status_code": str(response.status_code)
        })

    return response

# ============================================
# Эндпоинты
# ============================================

@app.route("/")
def home():
    """Главная страница"""
    with tracer.start_as_current_span("home-page") as span:
        span.set_attribute("user.agent", request.headers.get("User-Agent", "unknown"))

        logger.info("Home page accessed")

        return jsonify({
            "service": "app-service",
            "version": "1.0.0",
            "message": "Microservice is running",
            "endpoints": ["/", "/users", "/products", "/slow", "/error"]
        })

@app.route("/users")
def get_users():
    """Получение списка пользователей"""
    with tracer.start_as_current_span("get-users") as span:
        # Симулируем задержку БД
        time.sleep(random.uniform(0.05, 0.2))

        users = [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
            {"id": 3, "name": "Charlie"}
        ]

        span.set_attribute("users.count", len(users))
        logger.info(f"Returned {len(users)} users")

        return jsonify(users)

@app.route("/products")
def get_products():
    """Получение списка продуктов"""
    with tracer.start_as_current_span("get-products") as span:
        # Симулируем запрос к другому сервису
        time.sleep(random.uniform(0.1, 0.3))

        products = [
            {"id": 1, "name": "Laptop", "price": 999.99},
            {"id": 2, "name": "Mouse", "price": 29.99},
            {"id": 3, "name": "Keyboard", "price": 79.99}
        ]

        span.set_attribute("products.count", len(products))
        logger.info(f"Returned {len(products)} products")

        return jsonify(products)

@app.route("/slow")
def slow_endpoint():
    """Медленный эндпоинт (для демонстрации проблем с производительностью)"""
    with tracer.start_as_current_span("slow-operation") as span:
        # Имитация медленной операции
        delay = random.uniform(1.0, 3.0)
        time.sleep(delay)

        span.set_attribute("operation.delay_seconds", delay)
        span.set_attribute("performance", "critical")

        logger.warning(f"Slow operation completed in {delay:.2f}s")

        return jsonify({
            "message": "Slow operation completed",
            "delay_seconds": round(delay, 2)
        })

@app.route("/error")
def error_endpoint():
    """Эндпоинт с ошибкой (для демонстрации поиска неисправностей)"""
    with tracer.start_as_current_span("error-operation") as span:
        try:
            # Симулируем ошибку в 80% случаев
            if random.random() < 0.8:
                error_msg = "Database connection timeout"
                span.set_attribute("error", True)
                span.set_attribute("error.type", "TimeoutError")

                logger.error(f"Error occurred: {error_msg}")

                return jsonify({
                    "error": error_msg,
                    "status": "error"
                }), 500
            else:
                logger.info("Error endpoint called - no error this time")
                return jsonify({"message": "OK"})

        except Exception as e:
            span.record_exception(e)
            span.set_status(trace.Status(trace.StatusCode.ERROR))
            logger.error(f"Unexpected error: {str(e)}")
            return jsonify({"error": str(e)}), 500

@app.route("/health")
def health():
    """Проверка здоровья"""
    return jsonify({"status": "healthy", "timestamp": datetime.utcnow().isoformat()})

@app.route("/chain")
def chain_call():
    """Цепочка вызовов (демонстрация распределённой трассировки)"""
    with tracer.start_as_current_span("chain-call") as span:
        logger.info("Starting chain call")

        # Вызываем себя же (симуляция межсервисного взаимодействия)
        time.sleep(0.1)

        # Вызываем users
        resp1 = requests.get("http://localhost:8080/users", timeout=5)
        span.set_attribute("users.status_code", resp1.status_code)

        # Вызываем products
        resp2 = requests.get("http://localhost:8080/products", timeout=5)
        span.set_attribute("products.status_code", resp2.status_code)

        span.set_attribute("chain.completed", True)
        logger.info(f"Chain call completed. Users: {resp1.status_code}, Products: {resp2.status_code}")

        return jsonify({
            "chain": "completed",
            "users_status": resp1.status_code,
            "products_status": resp2.status_code
        })

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    logger.info(f"Starting application on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)