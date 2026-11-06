import os

# Disable network trace exports during tests while keeping in-memory span tracing active
os.environ["OTEL_TRACES_EXPORTER"] = "none"
