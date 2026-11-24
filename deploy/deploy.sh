#!/usr/bin/env sh
# AION 一键部署
#   用法1（本机/内网）: ./deploy/deploy.sh
#   用法2（域名+HTTPS）: ./deploy/deploy.sh trade.example.com
#
# 做的事情：生成数据库密码与 JWT 密钥 -> 构建并启动 db/redis/backend(可选 caddy)
#          -> 等待健康检查 -> 自动迁移 -> 创建管理员并打印账号密码
set -eu

DOMAIN="${1:-${AION_DOMAIN:-}}"
ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT_DIR"

say() { printf '\033[1;36m[aion]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[aion]\033[0m %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "未安装 Docker，请先安装 Docker Engine（含 compose 插件）"
docker compose version >/dev/null 2>&1 || die "缺少 docker compose 插件（需要 Compose v2）"

random_hex() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 24
    else
        head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
    fi
}

sed_inplace() {
    if sed --version >/dev/null 2>&1; then
        sed -i "$1" "$2"
    else
        sed -i '' "$1" "$2"
    fi
}

# ---------- 1. 数据库密码 ----------
if [ ! -f .env ]; then
    say "生成数据库密码 -> .env"
    printf 'POSTGRES_PASSWORD=%s\n' "$(random_hex)" > .env
fi

POSTGRES_PASSWORD=$(grep -E '^POSTGRES_PASSWORD=' .env | tail -1 | cut -d= -f2-)
[ -n "$POSTGRES_PASSWORD" ] || die ".env 缺少 POSTGRES_PASSWORD"
export POSTGRES_PASSWORD

# ---------- 2. 后端配置 ----------
if [ ! -f backend/.env ]; then
    cp backend/.env.example backend/.env
    sed_inplace "s|^AION_SECRET_KEY=.*|AION_SECRET_KEY=$(random_hex)|" backend/.env
    say "已生成 backend/.env（含随机 AION_SECRET_KEY）"
fi

if ! grep -qE '^AION_SECRET_KEY=.+' backend/.env; then
    sed_inplace "s|^AION_SECRET_KEY=.*|AION_SECRET_KEY=$(random_hex)|" backend/.env
    say "AION_SECRET_KEY 为空，已自动补一个随机值"
fi

# ---------- 3. 启动 ----------
if [ -n "$DOMAIN" ]; then
    export AION_DOMAIN="$DOMAIN"
    export ACME_EMAIL="${ACME_EMAIL:-}"
    COMPOSE="docker compose -f compose.yaml"
    SERVICES="db redis backend caddy"
    say "域名模式：$DOMAIN，Caddy 会自动申请 HTTPS 证书（需 80/443 可访问）"
else
    COMPOSE="docker compose -f compose.yaml -f deploy/compose.local.yaml"
    SERVICES="db redis backend"
    say "本机模式：不启用 HTTPS，后端映射到 8080"
fi

say "开始构建并启动：$SERVICES"
# shellcheck disable=SC2086
$COMPOSE up -d --build $SERVICES

# ---------- 4. 健康检查 ----------
say "等待后端就绪（迁移在容器启动时自动执行）"
i=0
until $COMPOSE exec -T backend curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -ge 60 ]; then
        # shellcheck disable=SC2086
        $COMPOSE logs --tail=60 backend
        die "后端在 120 秒内未通过健康检查，请查看上面的日志"
    fi
    sleep 2
done
say "后端已就绪"

# ---------- 5. 管理员账号 ----------
ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
if [ -n "${ADMIN_PASSWORD:-}" ]; then
    $COMPOSE exec -T -e ADMIN_USERNAME="$ADMIN_USERNAME" -e ADMIN_PASSWORD="$ADMIN_PASSWORD" \
        backend python bootstrap_admin.py
else
    $COMPOSE exec -T -e ADMIN_USERNAME="$ADMIN_USERNAME" backend python bootstrap_admin.py
fi

# ---------- 6. 结果 ----------
if [ -n "$DOMAIN" ]; then
    BASE="https://$DOMAIN"
else
    BASE="http://<本机IP>:8080"
fi

cat <<EOF

[aion] 部署完成

  控制台   $BASE/ui/
  官网     $BASE/site/
  接口文档 $BASE/docs

  管理员   $ADMIN_USERNAME
  重置密码 $ADMIN_USERNAME=admin ADMIN_PASSWORD='新密码' ./deploy/deploy.sh ${DOMAIN}

  查看日志 docker compose logs -f backend
  停止服务 docker compose down

  安卓端记得把 app/build.gradle.kts 里的 AION_BASE_URL 改成 $BASE
EOF
