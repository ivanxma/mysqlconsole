# MySQL DBConsole

`dbconsole` is a Flask-based MySQL and HeatWave administration console.

It provides:

- login/profile-based MySQL access with optional SSH tunnel settings
- `Admin > Status and Variables` with grouped status and variable views
- `Admin > Dashboard` for server, object, security, diagnostics, and HeatWave summary views
- `MySQL > DB Admin` for schema/table browsing, event management, DDL preview, indexes, partitions, row preview, and column-definition changes
- `MySQL > SQL Workspace` with Execute and Explain actions, `use_secondary_engine` selection, tabbed result output, and session history
- `MySQL > Import` for CSV and JSON uploads into MySQL tables
- `HeatWave` pages for HW table inventory and `HW Admin` management actions
- `Monitoring` dashboards, locks, report pages, and live charts with refresh, reorder, hide, popup, download, browser-local time labels on the chart axis, and tabbed chart groups
- authenticated top-right user icon with app version, update status, user, profile, connection summary, and logout
- shared interactive table styling with sortable headers, resizable columns, saved column widths, reset-layout controls, and compact download/action icons

## Login, Sessions, and Updates

DBConsole keeps database credentials out of browser-visible session state. The Flask cookie uses the app-specific `dbconsole_session` name and stores only non-secret profile data plus an opaque server-side session id. Live MySQL username/password values are held in server-owned memory for the active process and are cleared on logout, connection loss, or session reset.

After a successful login, DBConsole reads local `appver.json` and compares it with the repository version file. If the repository version string differs, the user is redirected to `Admin > Auto-Update`; otherwise the normal MySQL dashboard opens. The user icon in the top-right corner shows the current app version and update availability, and clicking it opens the profile/connection details and logout actions.

## Layout

Key files:

- `app.py`: Flask app creation, shared session handling, shared DB helpers, route registration
- `modules/`: feature modules for page orchestration and extracted logic
- `templates/`: Jinja templates
- `static/style.css`: shared styling
- `setup.sh`: environment setup and MySQL Shell Innovation install
- `start_http.sh`: start on the saved HTTP default port, `80` unless changed by `setup.sh`
- `start_https.sh`: start on the saved HTTPS default port, `443` unless changed by `setup.sh`

Current feature modules:

- `modules/mysql_import.py`
- `modules/status_variables.py`
- `modules/mysql_pages.py`
- `modules/heatwave_pages.py`
- `modules/monitoring_pages.py`

## Requirements

- Python 3
- MySQL access credentials
- optional SSH access if tunneling is enabled in a profile

Python dependencies are defined in `requirements.txt`:

- `Flask`
- `PyMySQL`
- `sshtunnel`

## Local Run

For a simple local dev run:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
python3 app.py
```

That starts the app on `127.0.0.1:5001` in debug mode.

## Deployment Scripts

`setup.sh` is the only deployment entry point. It creates the Python virtual environment, installs requirements, installs MySQL Shell Innovation for the target platform, writes `.runtime.env`, and optionally configures Linux systemd services and firewall ports.

Supported OS families:

- `ol8`
- `ol9`
- `ubuntu`
- `macos`

Supported deploy modes:

- `http`: install/start `dbconsole-http.service`
- `https`: install/start `dbconsole-https.service`
- `both`: install/start both services
- `none`: prepare the local environment only

### Existing Clone Setup

Usage:

```bash
./setup.sh [ol8|ol9|ubuntu|macos] [http|https|both|none] [http_port] [https_port]
./setup.sh [ol8|ol9|ubuntu|macos] [http|https|both|none] --http-port 8080 --https-port 8443
```

Examples:

```bash
./setup.sh macos none
./setup.sh ubuntu http
./setup.sh ol9 both
./setup.sh ol9 both 8080 8443
./setup.sh ubuntu https --https-port 8443
```

Interactive runs prompt for omitted values. Non-interactive runs should pass the OS family, deploy mode, and listener ports explicitly or set the matching environment variables.

### Fresh Host Bootstrap

On a fresh host, stream `setup.sh` once. The bootstrap path installs `git` if needed, clones the repository, then re-executes the cloned `setup.sh`.

```bash
curl -fsSL https://raw.githubusercontent.com/ivanxma/mysqlconsole/main/setup.sh | sh -s -- ol9 https --https-port 443
```

The bootstrap flow:

- installs `git` when it is missing
- clones `https://github.com/ivanxma/mysqlconsole.git`
- renames an existing target directory to `<dir>.<timestamp>`
- re-executes the cloned `setup.sh` with `bash`

Optional bootstrap overrides:

```bash
BOOTSTRAP_REPO_URL=https://github.com/ivanxma/mysqlconsole.git
BOOTSTRAP_CLONE_DIR=mysqlconsole
BOOTSTRAP_PARENT_DIR=/opt
```

Example:

```bash
BOOTSTRAP_PARENT_DIR=/opt \
BOOTSTRAP_CLONE_DIR=dbconsole \
curl -fsSL https://raw.githubusercontent.com/ivanxma/mysqlconsole/main/setup.sh | sh -s -- ubuntu both --http-port 80 --https-port 443
```

### OCI Compute Quick Start

Create a normal OCI Compute instance, then paste one of these snippets into `Advanced options` > `Management` > `Initialization script`. Keep the network rules aligned with the deploy mode:

- choose your compartment, availability domain, shape, boot volume, VCN/subnet, public IP, and SSH public key as usual
- use an Oracle Linux 9 or Ubuntu image
- HTTPS only: allow TCP `443`
- HTTP only: allow TCP `80`
- both: allow TCP `80` and `443`

Oracle Linux 9 images use the `opc` login user:

```bash
#!/bin/bash
set -euxo pipefail
APP_USER=opc
APP_GROUP=opc
OS_FAMILY=ol9
SERVICE_NAME=dbconsole-https.service
STATE_DIR=/var/lib/dbconsole-init
LOG_FILE=/var/log/dbconsole-init.log
BANNER_FILE=/etc/profile.d/dbconsole-setup-status.sh

mkdir -p "$STATE_DIR"
printf '%s\n' installing > "$STATE_DIR/status"
printf '%s\n' "$SERVICE_NAME" > "$STATE_DIR/service"
exec > >(tee -a "$LOG_FILE") 2>&1

cat > "$BANNER_FILE" <<'BANNER'
#!/bin/bash
case $- in *i*) ;; *) return 0 ;; esac
STATE_DIR=/var/lib/dbconsole-init
LOG_FILE=/var/log/dbconsole-init.log
STATUS="$(cat "$STATE_DIR/status" 2>/dev/null || true)"
SERVICE_NAME="$(cat "$STATE_DIR/service" 2>/dev/null || true)"

printf '\nDBConsole setup status: %s\n' "${STATUS:-unknown}"
if [ "$STATUS" = "installing" ]; then
  printf '%s\n' "Please wait until DBConsole setup has completed."
elif [ "$STATUS" = "failed" ]; then
  printf '%s\n' "DBConsole setup failed. Recent setup log:"
  tail -n 30 "$LOG_FILE" 2>/dev/null || true
elif [ "$STATUS" = "installed" ]; then
  printf '%s\n' "DBConsole setup has completed."
  if [ -n "$SERVICE_NAME" ]; then
    systemctl --no-pager --full --lines=12 status "$SERVICE_NAME" 2>/dev/null || true
  fi
fi
printf '\n'
BANNER
chmod 0755 "$BANNER_FILE"

finish_setup() {
  local exit_code="$1"
  if [ "$exit_code" -eq 0 ]; then
    printf '%s\n' installed > "$STATE_DIR/status"
  else
    printf '%s\n' failed > "$STATE_DIR/status"
  fi
  systemctl --no-pager --full --lines=20 status "$SERVICE_NAME" || true
}
trap 'finish_setup $?' EXIT

dnf install -y curl git
cd "/home/$APP_USER"
sudo -u "$APP_USER" env \
  BOOTSTRAP_PARENT_DIR="/home/$APP_USER" \
  BOOTSTRAP_CLONE_DIR=mysqlconsole \
  HOST=0.0.0.0 \
  SERVICE_USER="$APP_USER" \
  SERVICE_GROUP="$APP_GROUP" \
  bash -lc "curl -fsSL https://raw.githubusercontent.com/ivanxma/mysqlconsole/main/setup.sh | sh -s -- $OS_FAMILY https --https-port 443"

systemctl --no-pager --full --lines=20 status "$SERVICE_NAME" || true
```

Ubuntu images usually use the `ubuntu` login user:

```bash
#!/bin/bash
set -euxo pipefail
APP_USER=ubuntu
APP_GROUP=ubuntu
OS_FAMILY=ubuntu
SERVICE_NAME=dbconsole-https.service
STATE_DIR=/var/lib/dbconsole-init
LOG_FILE=/var/log/dbconsole-init.log
BANNER_FILE=/etc/profile.d/dbconsole-setup-status.sh

mkdir -p "$STATE_DIR"
printf '%s\n' installing > "$STATE_DIR/status"
printf '%s\n' "$SERVICE_NAME" > "$STATE_DIR/service"
exec > >(tee -a "$LOG_FILE") 2>&1

cat > "$BANNER_FILE" <<'BANNER'
#!/bin/bash
case $- in *i*) ;; *) return 0 ;; esac
STATE_DIR=/var/lib/dbconsole-init
LOG_FILE=/var/log/dbconsole-init.log
STATUS="$(cat "$STATE_DIR/status" 2>/dev/null || true)"
SERVICE_NAME="$(cat "$STATE_DIR/service" 2>/dev/null || true)"

printf '\nDBConsole setup status: %s\n' "${STATUS:-unknown}"
if [ "$STATUS" = "installing" ]; then
  printf '%s\n' "Please wait until DBConsole setup has completed."
elif [ "$STATUS" = "failed" ]; then
  printf '%s\n' "DBConsole setup failed. Recent setup log:"
  tail -n 30 "$LOG_FILE" 2>/dev/null || true
elif [ "$STATUS" = "installed" ]; then
  printf '%s\n' "DBConsole setup has completed."
  if [ -n "$SERVICE_NAME" ]; then
    systemctl --no-pager --full --lines=12 status "$SERVICE_NAME" 2>/dev/null || true
  fi
fi
printf '\n'
BANNER
chmod 0755 "$BANNER_FILE"

finish_setup() {
  local exit_code="$1"
  if [ "$exit_code" -eq 0 ]; then
    printf '%s\n' installed > "$STATE_DIR/status"
  else
    printf '%s\n' failed > "$STATE_DIR/status"
  fi
  systemctl --no-pager --full --lines=20 status "$SERVICE_NAME" || true
}
trap 'finish_setup $?' EXIT

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y curl git
cd "/home/$APP_USER"
sudo -u "$APP_USER" env \
  BOOTSTRAP_PARENT_DIR="/home/$APP_USER" \
  BOOTSTRAP_CLONE_DIR=mysqlconsole \
  HOST=0.0.0.0 \
  SERVICE_USER="$APP_USER" \
  SERVICE_GROUP="$APP_GROUP" \
  bash -lc "curl -fsSL https://raw.githubusercontent.com/ivanxma/mysqlconsole/main/setup.sh | sh -s -- $OS_FAMILY https --https-port 443"

systemctl --no-pager --full --lines=20 status "$SERVICE_NAME" || true
```

OCI verification:

```bash
ssh opc@<public-ip>              # Oracle Linux
ssh ubuntu@<public-ip>           # Ubuntu
sudo tail -n 120 /var/log/dbconsole-init.log
cat /var/lib/dbconsole-init/status
systemctl --no-pager status dbconsole-https.service
curl -kI https://<public-ip>/
```

Use `http --http-port 80` and `dbconsole-http.service` instead of the HTTPS values when deploying HTTP only. Use `both --http-port 80 --https-port 443` when both listeners are required.

The login banner is installed at `/etc/profile.d/dbconsole-setup-status.sh`. During first boot, a new SSH login shows `installing`; if setup fails, it shows the last 30 lines of `/var/log/dbconsole-init.log`; after success, it shows the current `systemctl status` for the configured DBConsole service.

### What `setup.sh` Does

`setup.sh` will:

- create `.venv`
- install Python dependencies
- run the platform-specific MySQL Shell Innovation installer
  - `ol8` and `ol9`: configure the MySQL community repositories, disable the `8.4 LTS` repos, enable the innovation repos, and install `mysql-shell`
  - `ubuntu`: write a MySQL APT source for `mysql-innovation` and `mysql-tools`, then install `mysql-shell`
  - `macos`: install or upgrade `mysql-shell` with Homebrew and fall back to the formula path if needed
- save default HTTP and HTTPS ports in `.runtime.env`
- when run interactively, prompt for omitted setup values and offer current/default values for OS family, deploy mode, host, the listener port for the selected deploy mode, TLS paths, and service user/group when applicable
- when deploy mode is `https` or `both` and no TLS paths are supplied, generate a default self-signed certificate and key under `tls/`
- synchronize the selected HTTP/HTTPS TCP ports with the host firewall when `firewall-cmd` or `ufw` is available, including removing stale DBConsole ports that are no longer selected
- it does not stop or disable the firewall service globally; it only updates the DBConsole listener ports
- on `ol8`, `ol9`, and `ubuntu`, install `dbconsole-http.service` and `dbconsole-https.service`
- when a Linux systemd service is configured to use a port below `1024`, grant `CAP_NET_BIND_SERVICE` so `80` and `443` do not require running the service as `root`, without clamping the rest of the service capability set
- enable and start the systemd service that matches the selected deploy mode
- leave the HTTPS systemd service installed but disabled only when user-supplied TLS files are missing or invalid

Start scripts:

```bash
./start_http.sh
SSL_CERT_FILE=/path/to/cert.pem SSL_KEY_FILE=/path/to/key.pem ./start_https.sh
```

The start scripts read saved defaults from `.runtime.env`. You can still override either port for a single launch with `PORT=<port>`.

When you run the start scripts directly outside systemd, privileged ports below `1024` can still require `sudo` or a higher port such as `8443`.

If `setup.sh` generated the default TLS assets, they are stored at `tls/dbconsole-selfsigned.crt` and `tls/dbconsole-selfsigned.key`.

On Linux systemd hosts, `setup.sh` writes unit files to `/etc/systemd/system/` and uses the same `.runtime.env` values for host, ports, and optional TLS paths.

The `Admin > Auto-Update` page works best when the DBConsole service user can run `sudo` non-interactively for the privileged steps in `setup.sh` and for service restarts. When passwordless `sudo` is unavailable from the running service, the updater falls back to:

- `git fetch` and `git pull`
- reinstalling Python packages inside `.venv`
- refreshing `.runtime.env`
- restarting the current DBConsole systemd service by letting systemd recover after the running service process exits

In that fallback mode, privileged changes such as MySQL Shell package installation, firewall updates, TLS ownership fixes, and systemd unit rewrites are skipped. Re-run `./setup.sh` from an SSH shell with sudo access when those changes are needed.

DBConsole stores the local application version in `appver.json`. On successful login it checks the repository copy of that file with a short timeout and redirects to `Admin > Auto-Update` when the repository version string differs from the local version. Set `DBCONSOLE_VERSION_URL` when the raw `appver.json` URL cannot be inferred from the configured git origin and branch.

If your Linux service was installed by an older `setup.sh` that wrote `CapabilityBoundingSet=CAP_NET_BIND_SERVICE`, run `git pull --ff-only` and `./setup.sh ...` once from an SSH shell to rewrite the unit files. After that one-time refresh, `Admin > Auto-Update` can use the new updater behavior on later releases.

### Environment Overrides

For `setup.sh`:

- `OS_FAMILY`
- `DEPLOY_MODE`
- `HOST`
- `HTTP_PORT`
- `HTTPS_PORT`
- `RUNTIME_ENV_FILE`
- `SSL_CERT_FILE`
- `SSL_KEY_FILE`
- `SKIP_PRIVILEGED_SETUP`
- `SERVICE_USER`
- `SERVICE_GROUP`
- `VENV_DIR`
- `BOOTSTRAP_REPO_URL`
- `BOOTSTRAP_CLONE_DIR`
- `BOOTSTRAP_PARENT_DIR`

For `start_http.sh` and `start_https.sh`:

- `PYTHON_BIN`
- `PORT`
- `RUNTIME_ENV_FILE`
- `HOST`
- `SSL_CERT_FILE`
- `SSL_KEY_FILE`

## Default Config Files

- `.runtime.env`: saved host, port, and TLS defaults written by `setup.sh`
- `profiles.json`: non-secret saved connection defaults created locally by the app
- `tls/`: default self-signed TLS assets generated by `setup.sh` when you do not supply your own certificate and key
- `object_storage.json`: object storage settings used by HeatWave-related screens

`.runtime.env`, `profiles.json`, and `tls/` are git-ignored local state.

## Main Screens

### Admin

- `Dashboard`
- `Profile`
- `Status and Variables`
- `Setup Object Storage`
- `Auto-Update`

### MySQL

- `DB Admin`
- `SQL Workspace`
- `Import`

### HeatWave

- `HW Table`
- `HW Admin`
- `Performance Query`
- `ML Query`
- `Table Load Recovery`

### Monitoring

- `Dashboard`
- `Charts`
- `Locks`

## Admin Dashboard

`Admin Dashboard` provides:

- server connection, timezone, SQL mode, charset, collation, and connection-limit details
- clickable object summary cards for InnoDB tables, views, and stored procedures/functions
- HeatWave summary counts where HeatWave tables are defined by `secondary_engine=rapid`
- Lakehouse summary counts where Lakehouse tables are defined by `engine=lakehouse`
- security and diagnostics tabs for security features, installed components, and `performance_schema.error_log`

## DB Admin

`DB Admin` supports:

- tabbed create-database, select-database/table, event, and tables-without-primary-key views
- tabbed report for tables without a primary key
- create and drop database
- select database and table from dropdowns or table list
- list user-schema events with checkbox selection
- enable, disable, or delete selected events
- create events with database selection, event name, schedule selection, and event body SQL
- refresh the event list after create or bulk actions and surface event action output in the page
- view column metadata
- view `CREATE TABLE`
- view index metadata
- view partition metadata for partitioned tables
- modify column definitions including rename and full type/length parameter edits
- add a primary key for tables that already have an `AUTO_INCREMENT` column
- bulk-fix or single-fix tables without a primary key by adding invisible `my_row_id` when needed
- page through preview rows

## SQL Workspace

`SQL Workspace` supports:

- toolbar controls for `USE_SECONDARY_ENGINE`, database selection, Execute, and Explain
- Execute output rendered in one TabView with `Execution Result`, each result set, and `History`
- Explain output rendered as `Text`, `JSON`, and `Visual` execution-plan tabs
- multi-result-set SQL handling in the output area
- session-local execution history with execution time, status, database, and `use_secondary_engine`

## HW Admin

`HW Admin` supports:

- tabbed `DB` and `Table` actions
- database-level HeatWave load and unload actions
- table-level full load and unload actions
- database status popup with HeatWave load details
- exclude-column popup with selectable and de-selectable exclusion state
- multi-result-set procedure output displayed in popup tabs

## Import

`MySQL > Import` supports:

- CSV and JSON upload
- choose existing database or create a new one
- default table name from the file name
- lowercase table and generated column names
- editable target column names and SQL types
- sample-data preview before import
- replace-table confirmation

## Monitoring Charts

Charts support:

- tabbed chart groups for `General`, `HeatWave`, and `Replication`
- refresh button
- refresh period selection: `5s`, `15s`, `30s`, `60s`
- close and restore
- drag to reorder
- download CSV
- popup view
- 50% width card layout on desktop
- browser-local time labels on the visible chart axis
- exact time values rendered on the chart axis

## Verification

Useful verification command:

```bash
python3 -m py_compile app.py modules/__init__.py modules/mysql_import.py modules/status_variables.py modules/mysql_pages.py modules/heatwave_pages.py modules/monitoring_pages.py
```
