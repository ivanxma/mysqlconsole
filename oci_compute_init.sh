#!/bin/bash
set -euo pipefail

APP_TITLE="${APP_TITLE:-MySQL DBConsole}"
APP_REPO="${APP_REPO:-https://github.com/ivanxma/mysqlconsole.git}"
APP_USER="${APP_USER:-opc}"
APP_GROUP="${APP_GROUP:-$APP_USER}"
APP_DIR="${APP_DIR:-/home/$APP_USER/mysqlconsole}"
OS_FAMILY="${OS_FAMILY:-ol9}"
DEPLOY_MODE="${DEPLOY_MODE:-https}"
HTTP_PORT="${HTTP_PORT:-}"
HTTPS_PORT="${HTTPS_PORT:-443}"
SERVICE_NAME="${SERVICE_NAME:-dbconsole-https.service}"
HOST="${HOST:-0.0.0.0}"
LOCAL_MYSQL_PROFILE_NAME="${LOCAL_MYSQL_PROFILE_NAME:-local-admin-profile}"
LOCAL_MYSQL_ADMIN_USER="${LOCAL_MYSQL_ADMIN_USER:-localadmin}"
LOCAL_MYSQL_ADMIN_PASSWORD="${LOCAL_MYSQL_ADMIN_PASSWORD:-}"
LOCAL_MYSQL_DATABASE="${LOCAL_MYSQL_DATABASE:-mysql}"
LOCAL_MYSQL_SOCKET="${LOCAL_MYSQL_SOCKET:-}"
LOCAL_MYSQL_INIT_FILE_PROVISIONING="${LOCAL_MYSQL_INIT_FILE_PROVISIONING:-${LOCAL_MYSQL_RESET_UNKNOWN_ROOT:-1}}"
DBCONSOLE_PYTHON_BIN="${DBCONSOLE_PYTHON_BIN:-}"
DBCONSOLE_PYTHON_MIN_VERSION="${DBCONSOLE_PYTHON_MIN_VERSION:-}"
DBCONSOLE_DEPENDENCY_AUDIT="${DBCONSOLE_DEPENDENCY_AUDIT:-}"
DBCONSOLE_DEPENDENCY_AUDIT_STRICT="${DBCONSOLE_DEPENDENCY_AUDIT_STRICT:-}"
DBCONSOLE_UPDATE_ALLOWED_REMOTE_URL="${DBCONSOLE_UPDATE_ALLOWED_REMOTE_URL:-}"
DBCONSOLE_UPDATE_ALLOWED_BRANCH="${DBCONSOLE_UPDATE_ALLOWED_BRANCH:-}"
DBCONSOLE_MYSQLSH="${DBCONSOLE_MYSQLSH:-}"
EMBEDDED_MYSQL_SHELL_DIR="${EMBEDDED_MYSQL_SHELL_DIR:-}"
EMBEDDED_MYSQL_SERVER_DIR="${EMBEDDED_MYSQL_SERVER_DIR:-}"
MYSQL_SHELL_MIN_VERSION="${MYSQL_SHELL_MIN_VERSION:-}"
MYSQL_SHELL_EMBEDDED_URL="${MYSQL_SHELL_EMBEDDED_URL:-}"
MYSQL_SHELL_EMBEDDED_PACKAGE="${MYSQL_SHELL_EMBEDDED_PACKAGE:-}"
MYSQL_SHELL_MACOS_PACKAGE_TAG="${MYSQL_SHELL_MACOS_PACKAGE_TAG:-}"
MYSQL_SERVER_VERSION="${MYSQL_SERVER_VERSION:-}"
MYSQL_SERVER_EMBEDDED_URL="${MYSQL_SERVER_EMBEDDED_URL:-}"
MYSQL_SERVER_EMBEDDED_PACKAGE="${MYSQL_SERVER_EMBEDDED_PACKAGE:-}"
MYSQL_SERVER_MACOS_PACKAGE_TAG="${MYSQL_SERVER_MACOS_PACKAGE_TAG:-}"

STATE_DIR="/var/lib/dbconsole-init"
INSTALLING_FLAG="$STATE_DIR/installing"
INSTALLED_FLAG="$STATE_DIR/installed"
FAILED_FLAG="$STATE_DIR/failed"
SERVICE_FILE="$STATE_DIR/service-name"
LOG_FILE="/var/log/dbconsole-init.log"
PROFILE_BANNER="/etc/profile.d/dbconsole-login-banner.sh"

mkdir -p "$STATE_DIR"
chmod 0755 "$STATE_DIR"
: > "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1

touch "$INSTALLING_FLAG"
rm -f "$INSTALLED_FLAG" "$FAILED_FLAG"
printf '%s\n' "$SERVICE_NAME" > "$SERVICE_FILE"
chmod 0644 "$SERVICE_FILE"

cat > "$PROFILE_BANNER" <<BANNER
#!/bin/bash
STATE_DIR="/var/lib/dbconsole-init"
INSTALLING_FLAG="\$STATE_DIR/installing"
INSTALLED_FLAG="\$STATE_DIR/installed"
FAILED_FLAG="\$STATE_DIR/failed"
SERVICE_FILE="\$STATE_DIR/service-name"
LOG_FILE="/var/log/dbconsole-init.log"

case \$- in
  *i*) ;;
  *) return 0 ;;
esac

[ "\${USER:-}" = "$APP_USER" ] || return 0

SERVICE_NAME=""
if [ -r "\$SERVICE_FILE" ]; then
  SERVICE_NAME="\$(head -n 1 "\$SERVICE_FILE")"
fi

show_service_status() {
  [ -n "\$SERVICE_NAME" ] || return 0
  if systemctl list-unit-files "\$SERVICE_NAME" --no-legend 2>/dev/null | grep -Fq "\$SERVICE_NAME"; then
    systemctl --no-pager --full --lines=12 status "\$SERVICE_NAME" || true
  else
    printf '%s\\n' "DBConsole service unit has not been created yet."
  fi
}

printf '\\n'
if [ -f "\$INSTALLING_FLAG" ]; then
  printf '%s\\n' "Please wait until installation to be completed."
elif [ -f "\$INSTALLED_FLAG" ]; then
  printf '%s\\n' "$APP_TITLE setup has been completed"
  show_service_status
elif [ -f "\$FAILED_FLAG" ]; then
  printf '%s\\n' "The installation finished with errors. Recent setup log:"
  tail -n 30 "\$LOG_FILE" 2>/dev/null || true
  show_service_status
fi
printf '\\n'
BANNER
chmod 0755 "$PROFILE_BANNER"

finish_install() {
  local exit_code="$1"
  rm -f "$INSTALLING_FLAG"
  if [ "$exit_code" -eq 0 ]; then
    touch "$INSTALLED_FLAG"
    rm -f "$FAILED_FLAG"
  else
    touch "$FAILED_FLAG"
    rm -f "$INSTALLED_FLAG"
  fi
}

trap 'finish_install $?' EXIT

install_package_prereqs() {
  if command -v dnf >/dev/null 2>&1; then
    dnf install -y curl git cpio rpm
  elif command -v yum >/dev/null 2>&1; then
    yum install -y curl git cpio rpm
  elif command -v apt-get >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y curl git dpkg
  else
    echo "Unable to install curl and git automatically." >&2
    return 1
  fi
}

run_as_app_user() {
  if [ "$(id -u "$APP_USER")" = "$(id -u)" ]; then
    "$@"
  else
    sudo -u "$APP_USER" "$@"
  fi
}

install_package_prereqs

mkdir -p "$(dirname "$APP_DIR")"
chown "$APP_USER:$APP_GROUP" "$(dirname "$APP_DIR")"

if [ -d "$APP_DIR" ]; then
  mv "$APP_DIR" "${APP_DIR}.$(date +%Y%m%d%H%M%S)"
fi

run_as_app_user git clone "$APP_REPO" "$APP_DIR"
cd "$APP_DIR"

if [ -z "$LOCAL_MYSQL_ADMIN_PASSWORD" ]; then
  echo "LOCAL_MYSQL_ADMIN_PASSWORD must be provided for first-boot local-admin-profile bootstrap. Refusing to generate or log a password automatically." >&2
  exit 1
fi

SETUP_ARGS=( "$OS_FAMILY" "$DEPLOY_MODE" )
if [ -n "$HTTP_PORT" ]; then
  SETUP_ARGS+=( "--http-port" "$HTTP_PORT" )
fi
if [ -n "$HTTPS_PORT" ]; then
  SETUP_ARGS+=( "--https-port" "$HTTPS_PORT" )
fi

run_as_app_user env \
  HOST="$HOST" \
  SERVICE_USER="$APP_USER" \
  SERVICE_GROUP="$APP_GROUP" \
  LOCAL_MYSQL_PROFILE_NAME="$LOCAL_MYSQL_PROFILE_NAME" \
  LOCAL_MYSQL_ADMIN_USER="$LOCAL_MYSQL_ADMIN_USER" \
  LOCAL_MYSQL_ADMIN_PASSWORD="$LOCAL_MYSQL_ADMIN_PASSWORD" \
  LOCAL_MYSQL_DATABASE="$LOCAL_MYSQL_DATABASE" \
  LOCAL_MYSQL_SOCKET="$LOCAL_MYSQL_SOCKET" \
  LOCAL_MYSQL_INIT_FILE_PROVISIONING="$LOCAL_MYSQL_INIT_FILE_PROVISIONING" \
  DBCONSOLE_PYTHON_BIN="$DBCONSOLE_PYTHON_BIN" \
  DBCONSOLE_PYTHON_MIN_VERSION="$DBCONSOLE_PYTHON_MIN_VERSION" \
  DBCONSOLE_DEPENDENCY_AUDIT="$DBCONSOLE_DEPENDENCY_AUDIT" \
  DBCONSOLE_DEPENDENCY_AUDIT_STRICT="$DBCONSOLE_DEPENDENCY_AUDIT_STRICT" \
  DBCONSOLE_UPDATE_ALLOWED_REMOTE_URL="$DBCONSOLE_UPDATE_ALLOWED_REMOTE_URL" \
  DBCONSOLE_UPDATE_ALLOWED_BRANCH="$DBCONSOLE_UPDATE_ALLOWED_BRANCH" \
  DBCONSOLE_MYSQLSH="$DBCONSOLE_MYSQLSH" \
  EMBEDDED_MYSQL_SHELL_DIR="$EMBEDDED_MYSQL_SHELL_DIR" \
  EMBEDDED_MYSQL_SERVER_DIR="$EMBEDDED_MYSQL_SERVER_DIR" \
  MYSQL_SHELL_MIN_VERSION="$MYSQL_SHELL_MIN_VERSION" \
  MYSQL_SHELL_EMBEDDED_URL="$MYSQL_SHELL_EMBEDDED_URL" \
  MYSQL_SHELL_EMBEDDED_PACKAGE="$MYSQL_SHELL_EMBEDDED_PACKAGE" \
  MYSQL_SHELL_MACOS_PACKAGE_TAG="$MYSQL_SHELL_MACOS_PACKAGE_TAG" \
  MYSQL_SERVER_VERSION="$MYSQL_SERVER_VERSION" \
  MYSQL_SERVER_EMBEDDED_URL="$MYSQL_SERVER_EMBEDDED_URL" \
  MYSQL_SERVER_EMBEDDED_PACKAGE="$MYSQL_SERVER_EMBEDDED_PACKAGE" \
  MYSQL_SERVER_MACOS_PACKAGE_TAG="$MYSQL_SERVER_MACOS_PACKAGE_TAG" \
  bash ./setup.sh "${SETUP_ARGS[@]}"

systemctl --no-pager --full --lines=12 status "$SERVICE_NAME" || true
