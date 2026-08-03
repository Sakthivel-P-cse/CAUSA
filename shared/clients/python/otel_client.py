"""
OpenTelemetry Python Client
Sends metrics, traces, and logs to OTEL Collector
"""

from opentelemetry import trace, metrics, logs
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.logs import LoggerProvider
from opentelemetry.sdk.logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.grpc.log_exporter import OTLPLogExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.sdk.resources import Resource
import os

# ─── Endpoint configuration ─────────────────────────────────────────────────
# Ingestion is three-tier (agent → LB/gateway → backends):
#   app → OTel Agent (node-local DaemonSet, :4317)
#       → traces:       OTel LB (route by trace ID) → gateway pod → Tempo
#       → metrics/logs: OTel Collector gateway → Prometheus / Loki
#   The app still sends ALL signals to the local agent; the agent fans
#   them out to the right tier. Nothing app-side changes.
#
# Send to the NODE-LOCAL agent, not the gateway directly. In Kubernetes,
# inject the node's host IP via the downward API and point the SDK at it:
#   env:
#     - name: HOST_IP
#       valueFrom:
#         fieldRef:
#           fieldPath: status.hostIP
#     - name: OTEL_EXPORTER_OTLP_ENDPOINT
#       value: "http://$(HOST_IP):4317"
# Falls back to localhost:4317 for local dev.
# ─────────────────────────────────────────────────────────────────────────────
OTEL_COLLECTOR_GRPC_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "localhost:4317")
SERVICE_NAME = os.getenv("SERVICE_NAME", "my-app")

def init_otel_tracing():
    """Initialize OpenTelemetry tracing"""
    resource = Resource.create({
        "service.name": SERVICE_NAME,
        "service.version": "1.0.0"
    })
    
    # Traces → OTel Collector :4317 (OTLP gRPC)
    trace_provider = TracerProvider(resource=resource)
    otlp_exporter = OTLPSpanExporter(endpoint=OTEL_COLLECTOR_GRPC_ENDPOINT)
    trace_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
    trace.set_tracer_provider(trace_provider)
    
    # Metrics → OTel Collector :4317 (OTLP gRPC) → remote_write → Prometheus :9090
    otlp_metric_exporter = OTLPMetricExporter(endpoint=OTEL_COLLECTOR_GRPC_ENDPOINT)
    metric_reader = PeriodicExportingMetricReader(otlp_metric_exporter)
    metric_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(metric_provider)
    
    # Logs → OTel Collector :4317 (OTLP gRPC) → HTTP push → Loki :3100
    logger_provider = LoggerProvider(resource=resource)
    otlp_log_exporter = OTLPLogExporter(endpoint=OTEL_COLLECTOR_GRPC_ENDPOINT)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(otlp_log_exporter))
    logs.set_logger_provider(logger_provider)
    
    return trace.get_tracer(__name__)

def init_flask_app(app):
    """Initialize Flask instrumentation"""
    FlaskInstrumentor().instrument_app(app)
    RequestsInstrumentor().instrument()

if __name__ == "__main__":
    init_otel_tracing()
    print(f"OpenTelemetry initialized for {SERVICE_NAME}")
    print(f"Sending telemetry to OTel Collector at {OTEL_COLLECTOR_GRPC_ENDPOINT} (gRPC)")
