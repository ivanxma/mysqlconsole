# MySQL DBConsole

`dbconsole` is a Flask-based MySQL and HeatWave administration console.

Current version: `1.0.3q`

Version history: `version_history.md` for GitHub viewing, or `version_history.html` for standalone browser viewing.

It provides:

- login/profile-based MySQL access with optional SSH tunnel settings
- `Admin > Status and Variables` with grouped status and variable views
- `Admin > Dashboard` for server, object, security, diagnostics, and HeatWave summary views
- `MySQL > DB Admin` for schema/table browsing, event management, DDL preview, indexes, partitions, row preview, and column-definition changes
- `MySQL > SQL Workspace` with Execute and Explain actions, `use_secondary_engine` selection, tabbed result output, session history, and flexible result tables with sortable, resizable, and reorderable columns
- `MySQL > Import` for CSV and JSON uploads into MySQL tables
- `HeatWave` pages for HW table inventory and `HW Admin` management actions
- HW Table reports use horizontally scrollable, flexible-width tables so wide HeatWave metadata such as `rpd_nodes` and table inventory does not collapse into unreadable columns
- `Monitoring` dashboards, locks, report pages, and live charts with refresh, reorder, hide, popup, download, browser-local time labels on the chart axis, and tabbed chart groups
- authenticated top-right user icon with app version, update status, user, profile, connection summary, and logout
- shared interactive table styling with sortable headers, resizable columns, saved column widths and column order where enabled, reset-layout controls, and compact download/action icons

## Login, Sessions, and Updates

DBConsole keeps database credentials out of browser-visible session state. The Flask cookie uses the app-specific `dbconsole_session` name and stores only non-secret profile data plus an opaque server-side session id. Live MySQL username/password values are held in server-owned memory for the active process and are cleared on logout, connection loss, or session reset.

After a successful login, DBConsole reads local `appver.json` and compares it with the repository version file. If the repository version string differs, the user is redirected to `Admin > Auto-Update`; otherwise the normal MySQL dashboard opens. The user icon in the top-right corner shows the current app version and update availability, and clicking it opens the profile/connection details and logout actions.

The `Admin > Auto-Update` status page uses a job-scoped polling token for status reads so progress can keep refreshing during a service restart even when server-side MySQL credential state is rebuilt.

## Layout

Key files:

- `app.py`: Flask app creation, shared session handling, profile persistence, route registration
- `modules/`: feature modules for page orchestration and extracted logic
- `templates/`: Jinja templates
- `static/style.css`: shared styling
- `setup.sh`: environment setup and MySQL Shell Innovation install
- `oci_compute_init.sh`: reusable OCI Compute first-boot installer with login-banner status
- `reset_localadmin_password.sh`: support utility to create or reset only the local `localadmin@localhost` password when recovery is needed
- `start_http.sh`: start on the saved HTTP default port, `80` unless changed by `setup.sh`
- `start_https.sh`: start on the saved HTTPS default port, `443` unless changed by `setup.sh`

Current feature modules:

- `modules/mysql_import.py`
- `modules/mysql_util.py`: MySQL Connector/Python boundary for profile normalization, TLS options, cached connections, health checks, transactions, and SQL literal escaping
- `modules/status_variables.py`
- `modules/mysql_pages.py`
- `modules/heatwave_pages.py`
- `modules/monitoring_pages.py`

## Requirements

- Python 3.12 or newer for setup-created deployments
- MySQL access credentials
- optional SSH access if tunneling is enabled in a profile

Python dependencies are installed from `requirements.txt`. `pyproject.toml` also declares `requires-python = ">=3.12"` for tooling that reads Python project metadata.

- `Flask`
- `mysql-connector-python`
- `sshtunnel`

## Local Run

For a simple local dev run:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

That starts the app on `127.0.0.1:5001` in debug mode.

## Deployment Scripts

`setup.sh` is the only deployment entry point. It selects or installs Python 3.12 or newer, creates the Python virtual environment from that interpreter, installs requirements, installs MySQL Shell Innovation for the target platform, writes `.runtime.env`, and optionally configures Linux systemd services and firewall ports.

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

When `./setup.sh` is run interactively without parameters, it also prompts for the default local MySQL admin username and password. Providing those values makes setup install MySQL Server binaries where supported, write an app-local socket-only MySQL config at `etc/my.cnf`, initialize the DBConsole-managed datadir under `.data/mysql`, rename the initialized `root@localhost` account to the submitted `user@localhost`, set the submitted password, and generate the first `local-admin-profile`.

On Linux, package managers such as `dnf` or `apt` provide the MySQL binaries, but DBConsole does not use the package-created `/var/lib/mysql` datadir for local admin bootstrap. The generated `etc/my.cnf` records the installed MySQL `basedir` such as `/usr`, `.data/mysql` as `datadir`, `.data/log/mysqld.err` as `log-error`, `.data/run/mysql.sock` as the socket, and `skip-networking` plus disabled MySQL X Plugin settings. Setup runs `mysqld --initialize`, reads the temporary password from the app-local error log, then renames `root@localhost` to the submitted local admin account. It does not leave a usable MySQL `root` account in the DBConsole-managed local instance.

Interactive setup also checks whether `local-admin-profile` is missing or not configured as the expected socket-only profile. If it needs repair and admin credentials were not supplied, setup prompts for the local MySQL admin username and password, then patches `profiles.json` after provisioning the socket-only local MySQL account.

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

Use the reusable `oci_compute_init.sh` for first-boot installs on OCI Compute Linux images. The script installs prerequisites, clones the repository, runs `setup.sh`, records install state under `/var/lib/dbconsole-init`, writes `/var/log/dbconsole-init.log`, and installs a login banner that shows setup progress, failure details, or service status.

OCI Compute platform choices:

| Platform | OCI image family | Login user | `OS_FAMILY` | Local MySQL socket |
| --- | --- | --- | --- | --- |
| Oracle Linux 8 | Oracle Linux 8 | `opc` | `ol8` | `/home/opc/mysqlconsole/.data/run/mysql.sock` |
| Oracle Linux 9 | Oracle Linux 9 | `opc` | `ol9` | `/home/opc/mysqlconsole/.data/run/mysql.sock` |
| Ubuntu | Ubuntu 24.04 | `ubuntu` | `ubuntu` | `/home/ubuntu/mysqlconsole/.data/run/mysql.sock` |
| macOS | Not an OCI Compute Linux target | n/a | `macos` | n/a |

Instance values to choose in OCI:

- Compartment: `<compartment>`
- Image: Oracle Linux 8, Oracle Linux 9, or Ubuntu 24.04
- Shape: `<shape>`
- VCN/Subnet: `<vcn>` / `<subnet>`
- Public IPv4: enabled when you want direct browser/SSH access
- SSH public key: your SSH public key
- Deploy mode: `https`, `http`, or `both`

Open OCI subnet security-list or NSG ingress only for the selected deploy mode. `setup.sh` also updates the instance-local host firewall, but OCI network ingress must allow the same port before external browser checks can pass:

- SSH: TCP `22` from your admin CIDR
- HTTPS: TCP `443` when deploy mode is `https` or `both`
- HTTP: TCP `80` when deploy mode is `http` or `both`
- Custom listener ports: open only the ports you set with `HTTP_PORT` or `HTTPS_PORT`

In the OCI Console:

1. Create a Compute instance with the chosen platform image.
2. Select the VCN/subnet and public IP setting.
3. Add your SSH public key.
4. Open `Advanced options` > `Management`.
5. Paste the matching initialization script below.
6. Replace `<replace-with-explicit-strong-password>` with the actual temporary password string for the first `local-admin-profile` login before you create the instance. Do not leave it blank, do not leave the placeholder text unchanged, and do not omit `LOCAL_MYSQL_ADMIN_PASSWORD`.
7. Create the instance and wait for first boot to finish.
8. SSH to the instance and check the login banner.

Oracle Linux 8 images use the `opc` login user:

```bash
#!/bin/bash
set -euo pipefail
dnf install -y curl
curl -fsSL https://raw.githubusercontent.com/ivanxma/mysqlconsole/main/oci_compute_init.sh -o /tmp/oci_compute_init.sh
chmod 0755 /tmp/oci_compute_init.sh

APP_USER=opc \
APP_GROUP=opc \
APP_DIR=/home/opc/mysqlconsole \
OS_FAMILY=ol8 \
DEPLOY_MODE=https \
HTTPS_PORT=443 \
LOCAL_MYSQL_ADMIN_USER=localadmin \
LOCAL_MYSQL_ADMIN_PASSWORD='<replace-with-explicit-strong-password>' \
SERVICE_NAME=dbconsole-https.service \
bash /tmp/oci_compute_init.sh
```

Oracle Linux 9 images use the `opc` login user:

```bash
#!/bin/bash
set -euo pipefail
dnf install -y curl
curl -fsSL https://raw.githubusercontent.com/ivanxma/mysqlconsole/main/oci_compute_init.sh -o /tmp/oci_compute_init.sh
chmod 0755 /tmp/oci_compute_init.sh

APP_USER=opc \
APP_GROUP=opc \
APP_DIR=/home/opc/mysqlconsole \
OS_FAMILY=ol9 \
DEPLOY_MODE=https \
HTTPS_PORT=443 \
LOCAL_MYSQL_ADMIN_USER=localadmin \
LOCAL_MYSQL_ADMIN_PASSWORD='<replace-with-explicit-strong-password>' \
SERVICE_NAME=dbconsole-https.service \
bash /tmp/oci_compute_init.sh
```

Ubuntu images usually use the `ubuntu` login user:

```bash
#!/bin/bash
set -euo pipefail
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y curl
curl -fsSL https://raw.githubusercontent.com/ivanxma/mysqlconsole/main/oci_compute_init.sh -o /tmp/oci_compute_init.sh
chmod 0755 /tmp/oci_compute_init.sh

APP_USER=ubuntu \
APP_GROUP=ubuntu \
APP_DIR=/home/ubuntu/mysqlconsole \
OS_FAMILY=ubuntu \
DEPLOY_MODE=https \
HTTPS_PORT=443 \
LOCAL_MYSQL_ADMIN_USER=localadmin \
LOCAL_MYSQL_ADMIN_PASSWORD='<replace-with-explicit-strong-password>' \
SERVICE_NAME=dbconsole-https.service \
bash /tmp/oci_compute_init.sh
```

For HTTP only, set `DEPLOY_MODE=http`, `HTTP_PORT=80`, `HTTPS_PORT=`, and `SERVICE_NAME=dbconsole-http.service` in the wrapper. For both listeners, set `DEPLOY_MODE=both`, `HTTP_PORT=80`, `HTTPS_PORT=443`, and choose the service you want the banner to show. When you use a non-default listener port such as `8443`, set the matching `HTTPS_PORT` or `HTTP_PORT`, update `SERVICE_NAME` if needed, and open the same TCP port in the subnet security rules or NSG.

Oracle Linux verification:

```bash
PUBLIC_IP='<public-ip>'
ssh opc@"$PUBLIC_IP"
sudo tail -n 120 /var/log/dbconsole-init.log
sudo ls -l /var/lib/dbconsole-init
systemctl --no-pager status dbconsole-https.service
sudo firewall-cmd --zone=public --list-ports 2>/dev/null || true
sudo nft list ruleset 2>/dev/null | grep -n 'dport 443' || true
curl -kI "https://$PUBLIC_IP/"
```

Ubuntu verification:

```bash
PUBLIC_IP='<public-ip>'
ssh ubuntu@"$PUBLIC_IP"
sudo tail -n 120 /var/log/dbconsole-init.log
sudo ls -l /var/lib/dbconsole-init
systemctl --no-pager status dbconsole-https.service
sudo iptables -S INPUT
sudo aa-status 2>/dev/null || true
sudo cat /etc/apparmor.d/local/usr.sbin.mysqld 2>/dev/null || true
sudo apparmor_parser -T -r /etc/apparmor.d/usr.sbin.mysqld 2>/dev/null || true
curl -kI "https://$PUBLIC_IP/"
```

macOS is supported by `setup.sh macos ...` for local development or local hosting, but it is not an OCI Compute Linux image target. Do not use `oci_compute_init.sh` for macOS.

Platform validation for this deployment path:

| Platform | Validation status |
| --- | --- |
| Oracle Linux 9 OCI Compute | Live first-boot validation completed with DBConsole-managed `.data/mysql`, app-local `etc/my.cnf`, `localadmin@localhost` socket login, active `dbconsole-https.service`, firewalld runtime port opening, MySQL RPM lock/GPG-key hardening, and external HTTPS `200` response. |
| Oracle Linux 8 | Live OCI Compute validation completed with DBConsole-managed `.data/mysql`, app-local `etc/my.cnf`, `localadmin@localhost` socket login, active `dbconsole-https.service`, firewalld/nft runtime fallback when `firewall-cmd` stalls, MySQL RPM lock/GPG-key hardening, and external HTTPS `200` response. Setup disables the OL8 AppStream MySQL module before installing Oracle MySQL community server/client packages so package filtering does not block first boot. |
| Ubuntu 24.04 | Live OCI Compute validation completed with Python 3.12 venv repair, refreshed MySQL APT repository signing key, DBConsole-managed `.data/mysql`, app-local `etc/my.cnf`, `localadmin@localhost` socket login, active `dbconsole-https.service`, host `iptables` 443 rule before the terminal reject rule, and external HTTPS `200` response. Setup writes a local AppArmor allowance for DBConsole's app-local MySQL config, `.embedded/mysql-server/`, and `.data/` paths before initializing the socket-only local MySQL instance. |
| macOS | Static validation covers `setup.sh macos`, start/stop scripts, and the macOS MySQL Shell installer. macOS remains a local-hosting target that uses `.embedded/mysql-server`, not the OCI Linux init script. |

The login banner is installed at `/etc/profile.d/dbconsole-login-banner.sh`. During first boot, a new SSH login shows `Please wait until installation to be completed.` If setup fails, it shows the recent setup log and service status. After success, it shows `MySQL DBConsole setup has been completed` and the current `systemctl status` for the configured service.

The OCI init script requires `LOCAL_MYSQL_ADMIN_PASSWORD` to be set to a real password value in the pasted initialization script. It refuses to generate or log a password automatically, and first boot fails if the variable is empty or omitted. Setup uses `LOCAL_MYSQL_ADMIN_USER` and `LOCAL_MYSQL_ADMIN_PASSWORD` to install MySQL Server binaries where supported, write DBConsole's app-local socket-only MySQL config, initialize `.data/mysql`, rename the initialized `root@localhost` account to the submitted local admin account, set the submitted password, and write the first non-secret login profile named `local-admin-profile` into `profiles.json`. `localadmin` is the DBConsole-managed local administrator account; the DBConsole-managed local instance does not keep a usable `root@localhost` account after initialization. Do not enable shell xtrace (`set -x`) in wrappers that pass these passwords, because cloud-init and console logs can retain command traces.

OCI first boot defaults the local admin username to `localadmin` and passes through MySQL Shell embedded fallback settings such as `MYSQL_SHELL_MIN_VERSION`, `MYSQL_SHELL_EMBEDDED_URL`, `MYSQL_SHELL_EMBEDDED_PACKAGE`, and `EMBEDDED_MYSQL_SHELL_DIR` when they are set. Linux OCI deployments use platform packages for MySQL Server binaries but run a DBConsole-managed socket-only MySQL instance from `etc/my.cnf` and `.data/`; the macOS embedded MySQL Server tar flow is for local macOS installs, not OCI Compute. The HTTP and HTTPS start scripts run Flask with threaded request handling so repeated health checks do not fill a single-threaded listener backlog.

On the first DBConsole login with `local-admin-profile`, use the `LOCAL_MYSQL_ADMIN_USER` and `LOCAL_MYSQL_ADMIN_PASSWORD` values supplied to setup. DBConsole sends that session directly to the local-admin password-change screen. After the password is changed, DBConsole logs the user out so the old setup password is no longer active in the browser session. Sign in again with `local-admin-profile` and the new password to manage profiles. For OCI Compute instances that already have `local-admin-profile`, change the existing `localadmin` password from the local-admin password change page; Auto-Update does not collect localadmin password fields after bootstrap is complete.

### What `setup.sh` Does

`setup.sh` will:

- select or install Python 3.12 or newer, then create `.venv` from that interpreter; if an existing `.venv` was created with an older Python, setup rebuilds it before installing dependencies
- on Ubuntu, retry virtualenv creation after installing the matching `python3.12-venv` support package when the interpreter exists but `ensurepip` is unavailable
- install Python dependencies
- run the platform-specific MySQL Shell Innovation installer
  - `ol8` and `ol9`: configure the MySQL community repositories, disable the `8.4 LTS` repos, enable the innovation repos, refresh package metadata, and ask DNF for the best available vendor `mysql-shell` package
  - `ol8` and `ol9`: wait for transient RPM database locks and import the current MySQL 2025 RPM GPG key when present before repository refreshes and package installs
  - `ubuntu`: remove a stale MySQL APT source list if present, write the current MySQL signing key and APT source for `mysql-innovation` and `mysql-tools`, refresh package metadata, then install or upgrade the vendor `mysql-shell` package
  - `macos`: refresh Homebrew metadata, install or upgrade `mysql-shell`, and fall back to the formula path if needed
  - all platforms discover the latest MySQL Shell version from the vendor download page and verify that `mysqlsh` meets that version; set `MYSQL_SHELL_MIN_VERSION` only when you intentionally need to pin a specific minimum
  - if the platform package manager leaves no usable `mysqlsh` at the required version, setup installs an app-local embedded MySQL Shell under `.embedded/mysql-shell` and writes `DBCONSOLE_MYSQLSH` to `.runtime.env`
  - Linux installers try the configured vendor package repository first; if the repository has not published the required MySQL Shell version yet, setup computes a MySQL vendor package URL from the discovered or pinned version, platform, and CPU architecture and installs that package
- save default HTTP and HTTPS ports in `.runtime.env`
- when `LOCAL_MYSQL_ADMIN_USER` and `LOCAL_MYSQL_ADMIN_PASSWORD` are provided, install MySQL Server binaries, write app-local `etc/my.cnf`, initialize `.data/mysql` with `mysqld --initialize`, rename the initialized `root@localhost` account to the submitted `user@localhost`, set the submitted password, and create the first `local-admin-profile` entry in `profiles.json`; setup does not create application tables or default schemas on connected databases
- on Ubuntu, add a local AppArmor allowance for the generated `etc/my.cnf` file, `.embedded/mysql-server/`, and `.data/` tree before running the app-managed `mysqld --initialize`; AppArmor does not control HTTPS port `443` ingress, so external HTTPS failures should also check OCI ingress and host firewall rules
- on OL8, disable the platform MySQL module before installing Oracle MySQL community server and client packages
- mark the generated `local-admin-profile` for first-login password rotation; DBConsole requires the password change before profile management and logs out after the change
- when run interactively, prompt for omitted setup values and offer current/default values for OS family, deploy mode, host, the listener port for the selected deploy mode, TLS paths, and service user/group when applicable
- when deploy mode is `https` or `both` and no TLS paths are supplied, generate a default self-signed certificate and key under `tls/`
- synchronize the selected HTTP/HTTPS TCP ports with the host firewall, including removing stale DBConsole ports that are no longer selected; on OL8/OL9 setup opens the firewalld runtime zone port before attempting permanent persistence and can fall back to an `nft` firewalld input allow rule when `firewall-cmd` stalls, while Ubuntu uses `ufw` when present or an `iptables` rule inserted before any terminal reject/drop rule
- it does not stop or disable the firewall service globally; it only updates the DBConsole listener ports
- on `ol8`, `ol9`, and `ubuntu`, install `dbconsole-http.service` and `dbconsole-https.service`
- when a Linux systemd service is configured to use a port below `1024`, grant `CAP_NET_BIND_SERVICE` so `80` and `443` do not require running the service as `root`, without clamping the rest of the service capability set
- enable and start the systemd service that matches the selected deploy mode
- leave the HTTPS systemd service installed but disabled only when user-supplied TLS files are missing or invalid

Start scripts:

```bash
./start_mysql.sh
./start_http.sh
SSL_CERT_FILE=/path/to/cert.pem SSL_KEY_FILE=/path/to/key.pem ./start_https.sh
./stop_mysql.sh
```

The start scripts read saved defaults from `.runtime.env`. You can still override either port for a single launch with `PORT=<port>`.

When setup provisions the local socket-only MySQL profile, `.runtime.env` records `LOCAL_MYSQL_AUTOSTART=1`, the socket path, `LOCAL_MYSQL_BASEDIR`, `LOCAL_MYSQL_DATADIR`, `LOCAL_MYSQL_CONFIG_FILE`, `LOCAL_MYSQL_ERROR_LOG`, and `LOCAL_MYSQL_PID_FILE`. `start_http.sh` and `start_https.sh` check that socket before starting Flask. `start_mysql.sh` and `stop_mysql.sh` provide explicit local MySQL operations. On Linux they start/stop the DBConsole-managed MySQL process from the saved app-local config; on macOS setup installs MySQL Server from the public Oracle tar archive under `.embedded/mysql-server` and the scripts start/stop that embedded server directly.

When you run the start scripts directly outside systemd, privileged ports below `1024` can still require `sudo` or a higher port such as `8443`.

If `setup.sh` generated the default TLS assets, they are stored at `tls/dbconsole-selfsigned.crt` and `tls/dbconsole-selfsigned.key`.

On Linux systemd hosts, `setup.sh` writes unit files to `/etc/systemd/system/` and uses the same `.runtime.env` values for host, ports, and optional TLS paths.

The `Admin > Auto-Update` page works best when the DBConsole service user can run `sudo` non-interactively for the privileged steps in `setup.sh` and for service restarts. When passwordless `sudo` is unavailable from the running service, the updater falls back to:

- `git fetch` and `git pull`
- selecting or installing Python 3.12 or newer and reinstalling Python packages inside `.venv`
- refreshing `.runtime.env`
- restarting the current DBConsole systemd service by letting systemd recover after the running service process exits

In that fallback mode, privileged changes such as MySQL Shell package installation, firewall updates, TLS ownership fixes, and systemd unit rewrites are skipped. Re-run `./setup.sh` from an SSH shell with sudo access when those changes are needed.

When passwordless `sudo` is available, auto-update reruns the full `setup.sh` path, upgrades the app virtual environment to Python 3.12 or newer when needed, and upgrades MySQL Shell Innovation to the latest package available for the platform before restarting DBConsole.

Auto-update is available from a session logged in through `local-admin-profile`. A first-time bootstrap exception is also available for older DBConsole deployments where `local-admin-profile` is missing or not socket-only: an authenticated session can open `Admin > Auto-Update`, but the start action requires a new localadmin password, password confirmation, and an explicit setup confirmation. That bootstrap path creates or resets only `localadmin@localhost`; it does not create a MySQL `root` user and does not reset `root@localhost`. Once `local-admin-profile` exists and is in use, Auto-Update no longer displays or accepts localadmin password setup fields; use the local-admin password change page to change the existing password. The update worker also verifies the configured git `origin` and branch before it fetches or pulls. By default it expects `https://github.com/ivanxma/mysqlconsole.git` on `main`; set `DBCONSOLE_UPDATE_ALLOWED_REMOTE_URL` and `DBCONSOLE_UPDATE_ALLOWED_BRANCH` only after verifying the intended deployment source.

Before repository validation, after any preserved local files are restored, and after rerunning setup, Auto-Update repairs local deployment permissions for `.runtime.env`, `.flask_secret_key`, `profiles.json`, `object_storage.json`, `profile_ssh_keys/`, and `tls/`. It also treats generated security/vulnerability reports and `pip-audit` reports as local deployment artifacts during the worktree check so existing hardening outputs do not block an update.

If an existing deployment does not yet have the socket-only local MySQL admin server or `local-admin-profile`, use Auto-Update from any authenticated session to refresh the code and complete first-time bootstrap. Older already-rendered update pages may need one code-refresh run first; after DBConsole restarts, rerun Auto-Update from the refreshed page and enter the temporary `localadmin` password with confirmation. After setup completes, choose `local-admin-profile` on the login screen, sign in as `localadmin` with the temporary password, and DBConsole will require an immediate password change. After the password is changed, DBConsole logs out; sign in again with the new password to manage profiles and Auto-Update.

For deployments where in-app bootstrap cannot be completed because the running service lacks sudo privileges, rerun `./setup.sh ...` from SSH with `LOCAL_MYSQL_ADMIN_USER` and `LOCAL_MYSQL_ADMIN_PASSWORD`, or use the support reset script below, then sign in with `local-admin-profile` before starting future normal Auto-Update jobs.

Support reset for the local DBConsole MySQL admin account:

```bash
LOCAL_MYSQL_ADMIN_PASSWORD='<new-temporary-password>' ./reset_localadmin_password.sh
```

The reset script reads `.runtime.env` when present, accepts `--user`, `--socket`, `--database`, and `--service` overrides, and prompts interactively when `LOCAL_MYSQL_ADMIN_PASSWORD` is omitted. It creates or resets only `localadmin@localhost` through socket-root access or one-time MySQL init-file provisioning. It does not create a MySQL `root` user, does not reset `root@localhost`, and does not save the password.

DBConsole stores the local application version in `appver.json`. On successful login it checks the repository copy of that file with a short timeout and redirects to `Admin > Auto-Update` when the repository version string differs and the session can use Auto-Update through `local-admin-profile` or first-time bootstrap. Set `DBCONSOLE_VERSION_URL` when the raw `appver.json` URL cannot be inferred from the configured git origin and branch. HTTPS version checks use `certifi` by default; set `DBCONSOLE_VERSION_CA_BUNDLE` to a specific CA bundle path if your environment requires one.

`setup.sh` runs a dependency vulnerability audit with `pip-audit` after installing Python dependencies. The default mode is warn-only so setup can continue if the advisory service is unavailable or an issue is reported. Set `DBCONSOLE_DEPENDENCY_AUDIT=off` to skip the audit, or set `DBCONSOLE_DEPENDENCY_AUDIT_STRICT=1` to fail setup on audit setup errors or reported vulnerabilities.

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
- `DBCONSOLE_PYTHON_BIN`
- `DBCONSOLE_PYTHON_MIN_VERSION`
- `MYSQL_SHELL_MIN_VERSION`
- `MYSQL_SHELL_PACKAGE`
- `MYSQL_SHELL_DOWNLOAD_PAGE`
- `MYSQL_SHELL_VENDOR_DOWNLOAD_BASE`
- `MYSQL_SHELL_EMBEDDED_URL`
- `MYSQL_SHELL_EMBEDDED_PACKAGE`
- `MYSQL_SHELL_MACOS_PACKAGE_TAG`
- `EMBEDDED_MYSQL_SHELL_DIR`
- `MYSQL_SERVER_VERSION`
- `MYSQL_SERVER_DOWNLOAD_PAGE`
- `MYSQL_SERVER_VENDOR_DOWNLOAD_BASE`
- `MYSQL_SERVER_EMBEDDED_URL`
- `MYSQL_SERVER_EMBEDDED_PACKAGE`
- `MYSQL_SERVER_MACOS_PACKAGE_TAG`
- `EMBEDDED_MYSQL_SERVER_DIR`
- `BOOTSTRAP_REPO_URL`
- `BOOTSTRAP_CLONE_DIR`
- `BOOTSTRAP_PARENT_DIR`
- `DBCONSOLE_VERSION_URL`
- `DBCONSOLE_VERSION_CA_BUNDLE`
- `DBCONSOLE_DEPENDENCY_AUDIT`
- `DBCONSOLE_DEPENDENCY_AUDIT_STRICT`
- `DBCONSOLE_UPDATE_ALLOWED_REMOTE_URL`
- `DBCONSOLE_UPDATE_ALLOWED_BRANCH`
- `LOCAL_MYSQL_PROFILE_NAME`
- `LOCAL_MYSQL_ADMIN_USER`
- `LOCAL_MYSQL_ADMIN_PASSWORD`
- `LOCAL_MYSQL_ROOT_PASSWORD`
- `LOCAL_MYSQL_INIT_FILE_PROVISIONING`
- `LOCAL_MYSQL_RESET_UNKNOWN_ROOT`
- `LOCAL_MYSQL_SOCKET`
- `LOCAL_MYSQL_DATABASE`

For `start_http.sh` and `start_https.sh`:

- `PYTHON_BIN`
- `PORT`
- `RUNTIME_ENV_FILE`
- `HOST`
- `DBCONSOLE_MYSQLSH`
- `DBCONSOLE_PYTHON_BIN`
- `DBCONSOLE_PYTHON_MIN_VERSION`
- `DBCONSOLE_SESSION_COOKIE_SECURE`
- `DBCONSOLE_UPDATE_ALLOWED_REMOTE_URL`
- `DBCONSOLE_UPDATE_ALLOWED_BRANCH`
- `LOCAL_MYSQL_AUTOSTART`
- `LOCAL_MYSQL_SOCKET`
- `LOCAL_MYSQL_SERVICE`
- `LOCAL_MYSQL_BASEDIR`
- `LOCAL_MYSQL_DATADIR`
- `LOCAL_MYSQL_CONFIG_FILE`
- `LOCAL_MYSQL_ERROR_LOG`
- `LOCAL_MYSQL_PID_FILE`
- `SSL_CERT_FILE`
- `SSL_KEY_FILE`

## Default Config Files

- `.runtime.env`: saved host, port, and TLS defaults written by `setup.sh`
- `.flask_secret_key`: generated Flask session signing key used when `FLASK_SECRET_KEY` is not set
- `.embedded/mysql-shell/`: app-local embedded MySQL Shell fallback used when the platform `mysqlsh` is missing or below the required version
- `.embedded/mysql-server/`: app-local MySQL Server installed from the public Oracle macOS tar archive for socket-only local admin use
- `.data/`: DBConsole-managed local MySQL datadir, socket, PID, temporary files, and error log
- `etc/my.cnf`: generated app-local MySQL config for the DBConsole-managed local MySQL instance; only this file is ignored, not the whole `etc/` directory
- `profiles.json`: non-secret saved connection defaults created locally by the app
- `profile_ssh_keys/`: uploaded SSH private keys for SSH tunnel profiles; paths are stored server-side only and keys are written with restrictive file permissions
- `tls/`: default self-signed TLS assets generated by `setup.sh` when you do not supply your own certificate and key
- `object_storage.json`: object storage settings used by HeatWave-related screens

`.runtime.env`, `.flask_secret_key`, `.data/`, `.embedded/`, `etc/my.cnf`, `profiles.json`, `object_storage.json`, `profile_ssh_keys/`, and `tls/` are git-ignored local state. `etc/` itself is not ignored, so future checked-in configuration templates can be added there without changing `.gitignore`. `setup.sh` repairs local sensitive file permissions on every run: runtime config, Flask secret key, profiles, object storage config, and generated MySQL config are written as owner-readable files, uploaded SSH-key directories are owner-only, and TLS private key material is owner-readable only. The auto-update worker allows local changes to these files during the repository clean-check.

`profiles.json` must not contain database passwords. The generated `local-admin-profile` stores only the local socket path, default database, default username, and first-login password-change marker. SSH private keys are also not stored in `profiles.json`; uploaded key files are kept under `profile_ssh_keys/` with restrictive permissions and only the server-side path is retained.

## Main Screens

### Admin

- `Dashboard`
- `Profile` (available only after signing in with the socket-only `local-admin-profile`)
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

- tabbed create-database, select-database/table, event, charset/collation, and tables-without-primary-key views
- tabbed report for tables without a primary key
- create and drop database
- select database and table from dropdowns or table list
- delete selected tables from the selected database with checkbox selection, a Select all control, and confirmation
- list user-schema events with checkbox selection
- enable, disable, or delete selected events
- create events with database selection, event name, schedule selection, and event body SQL
- refresh the event list after create or bulk actions and surface event action output in the page
- inspect table charset/collation defaults and character columns that differ from the table default
- inspect outgoing and referenced-by foreign key definitions in the charset/collation table list
- bulk-change selected table charset/collation with `ALTER TABLE ... CONVERT TO CHARACTER SET`
- change selected character-column charset/collation with generated `MODIFY COLUMN` clauses
- preview charset/collation change SQL before execution, including generated foreign key drop and recreate statements
- download the generated charset/collation SQL plan as a `.sql` file
- optionally run charset/collation changes with `FOREIGN_KEY_CHECKS=0` and drop/recreate outgoing foreign keys touching selected changes
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
- result-set tables use flexible column widths, horizontal scrolling, sortable headers, drag-to-reorder headers, resize handles, and saved layout reset without duplicating the result download action
- session-local execution history with execution time, status, database, and `use_secondary_engine`

## HW Admin

`HW Admin` supports:

- tabbed `DB` and `Table` actions
- database-level HeatWave load and unload actions
- table-level full load and unload actions
- consistent red-gradient enabled action buttons across DB and Table actions
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
