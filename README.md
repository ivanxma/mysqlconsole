# MySQL DBConsole

`dbconsole` is a Flask-based MySQL and HeatWave administration console.

It provides:

- login/profile-based MySQL access with optional SSH tunnel settings
- `Admin > Status and Variables` with grouped status and variable views
- `MySQL > DB Admin` for schema/table browsing, DDL preview, indexes, partitions, and row preview
- `MySQL > Import` for CSV and JSON uploads into MySQL tables
- `HeatWave` pages for HW table inventory and management actions
- `Monitoring` dashboards, locks, report pages, and live charts with refresh, reorder, hide, popup, download, and browser-local time labels on the chart axis

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

`setup.sh` supports:

- `ol8`
- `ol9`
- `ubuntu`
- `macos`

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

`setup.sh` will:

- create `.venv`
- install Python dependencies
- run the platform-specific MySQL Shell Innovation installer
- save default HTTP and HTTPS ports in `.runtime.env`
- when run interactively without explicit port arguments, prompt for HTTP and HTTPS port changes
- open the selected HTTP/HTTPS TCP ports when the platform tooling supports it
- on `ol8`, `ol9`, and `ubuntu`, install `dbconsole-http.service` and `dbconsole-https.service`
- enable and start the systemd service that matches the selected deploy mode
- leave the HTTPS systemd service installed but disabled when TLS files are not configured yet

Start scripts:

```bash
./start_http.sh
SSL_CERT_FILE=/path/to/cert.pem SSL_KEY_FILE=/path/to/key.pem ./start_https.sh
```

The start scripts read saved defaults from `.runtime.env`. You can still override either port for a single launch with `PORT=<port>`.

On Linux systemd hosts, `setup.sh` writes unit files to `/etc/systemd/system/` and uses the same `.runtime.env` values for host, ports, and optional TLS paths.

Environment overrides:

- `PYTHON_BIN`
- `HOST`
- `PORT`
- `HTTP_PORT`
- `HTTPS_PORT`
- `RUNTIME_ENV_FILE`
- `SSL_CERT_FILE`
- `SSL_KEY_FILE`
- `SERVICE_USER`
- `SERVICE_GROUP`

## Default Config Files

- `profiles.json`: non-secret saved connection defaults
- `object_storage.json`: object storage settings used by HeatWave-related screens

The current default profile points at `127.0.0.1:3310` and does not store passwords.

## Main Screens

### Admin

- `Profile`
- `Status and Variables`
- `Setup Object Storage`

### MySQL

- `Admin Dashboard`
- `DB Admin`
- `Import`

### HeatWave

- `HW Table`
- `Management`
- `Performance Query`
- `ML Query`
- `Table Load Recovery`

### Monitoring

- `Dashboard`
- `Charts`
- `Locks`

## DB Admin

`DB Admin` supports:

- create and drop database
- select database and table from dropdowns or table list
- view column metadata
- view `CREATE TABLE`
- view index metadata
- view partition metadata for partitioned tables
- page through preview rows

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
