#!/bin/sh
set -eu
mode=${1:-local}
case "$mode" in
  local) destination=.env.preview ;;
  production)
    case "${2:-}" in https://?*) ;; *) echo "Usage: $0 production https://ops.example.com" >&2; exit 2;; esac
    destination=.env.release
    ;;
  *) echo "Usage: $0 [local | production https://ops.example.com]" >&2; exit 2 ;;
esac

if [ -e "$destination" ]; then
    echo "$destination already exists. Use its existing settings."
    exit 0
fi

umask 077
set -C
{
  if [ "$mode" = local ]; then
    printf '%s\n' 'OPSPILOT_ENV=development' 'OPSPILOT_ORIGIN=http://localhost:8088' \
      'EDGE_SITE_ADDRESS=http://localhost' 'EDGE_HTTP_BIND=127.0.0.1:8088' \
      'EDGE_HTTPS_BIND=127.0.0.1:8448' 'FAULT_INJECTION_ENABLED=true' \
      'DEMO_ORDERS_ENABLED=true' 'DEMO_LAB_ENABLED=true'
  else
    printf 'OPSPILOT_ORIGIN=%s\n' "$2"
    printf '%s\n' 'FAULT_INJECTION_ENABLED=false' 'DEMO_ORDERS_ENABLED=false' 'DEMO_LAB_ENABLED=false'
  fi
  for variable in POSTGRES_PASSWORD DEMO_POSTGRES_PASSWORD GRAFANA_PASSWORD; do
    printf '%s=%s\n' "$variable" "$(openssl rand -hex 24)"
  done
} > "$destination"
printf 'Created %s\n' "$destination"
