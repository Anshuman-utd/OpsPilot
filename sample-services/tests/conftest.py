import os

# Disable background OTLP network exporter during automated pytest runs
os.environ["OTEL_SDK_DISABLED"] = "true"
