#!/bin/sh
# 生产 compose 统一入口：固定文件顺序，可选叠加文件由 deployment.env 的
# SAGE_COMPOSE_OVERLAYS 声明（空格分隔，取值 auth / alipay / sales）。
# 用法: sage-compose.sh <docker compose 子命令与参数...>
set -eu
if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <compose subcommand> [args...]" >&2
    exit 2
fi
DIR=$(cd "$(dirname "$0")/.." && pwd)
set -a; . "${SAGE_DEPLOYMENT_ENV:-/opt/sage-docker-preview/deployment.env}"; set +a
OVERLAYS=" ${SAGE_COMPOSE_OVERLAYS:-} "
for o in $OVERLAYS; do
    case "$o" in
        auth|alipay|sales) ;;
        *) echo "Unknown compose overlay: $o" >&2; exit 2 ;;
    esac
done
has() { case "$OVERLAYS" in *" $1 "*) return 0 ;; esac; return 1; }
# 逆序前插，最终顺序：compose → offline → [auth] → egress → [alipay] → [sales]
if has sales; then set -- -f "$DIR/compose.sales.yaml" "$@"; fi
if has alipay; then set -- -f "$DIR/compose.alipay.yaml" "$@"; fi
set -- -f "$DIR/compose.egress.yaml" "$@"
if has auth; then set -- -f "$DIR/compose.auth.yaml" "$@"; fi
set -- -f "$DIR/compose.yaml" -f "$DIR/compose.offline.yaml" "$@"
exec docker compose --env-file /dev/null -p sage-preview "$@"
