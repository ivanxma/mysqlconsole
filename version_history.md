# DBConsole Version History

Version summary from `1.0.2a` to `1.0.3q`.

## 1.0.3q Summary

Version `1.0.3q` syncs the validated SQL Workspace result-table layout and Oracle Linux first-boot RPM hardening into the main source.

- Updated SQL Workspace result-set tables to use flexible widths, horizontal scrolling, sortable headers, drag-to-reorder columns, resize handles, saved layout reset, and a single result download action.
- Extended the shared table enhancer with opt-in column reordering, stable per-table layout keys, and per-table suppression of the generic CSV download control.
- Hardened OL8 and OL9 MySQL Shell and local MySQL package setup by waiting for transient RPM database locks and importing the current MySQL 2025 RPM GPG key when present.
- Kept the production OCI init script pointed at the production repository; the test-repo-only init default was not synced.

## 1.0.3p Summary

Version `1.0.3p` updates OCI Compute deployment documentation to match the validated OL8, OL9, and Ubuntu 24.04 first-boot behavior.

- Corrected the README OCI init-script platform matrix to document Ubuntu 24.04, `opc` for Oracle Linux, and `ubuntu` for Ubuntu.
- Clarified that OCI subnet security-list or NSG ingress must allow the selected listener port in addition to the instance-local firewall updates done by `setup.sh`.
- Added OL8/OL9 verification checks for firewalld/nft listener rules and Ubuntu verification checks for iptables ordering and AppArmor mysqld profile state.
- Recorded the validated firewalld runtime/nft fallback, Ubuntu iptables-before-reject rule, AppArmor allowances for `etc/my.cnf`, `.embedded/mysql-server/`, and `.data/`, threaded Flask listeners, and external HTTPS `200` checks.

## 1.0.3o Summary

Version `1.0.3o` moves DBConsole's MySQL driver boundary to Oracle MySQL Connector/Python and centralizes connection behavior in `modules/mysql_util.py`.

- Replaced the active MySQL Python driver dependency with `mysql-connector-python>=9.5,<10.0`.
- Added `modules/mysql_util.py` for profile normalization, TLS mode handling, Connector/Python cursor adaptation, cached connection borrowing, `SELECT 1` health checks, transaction cleanup, SSH tunnel cleanup, and SQL literal escaping.
- Updated TCP profiles to support `SSL Mode = Required`, `VERIFY_CA`, `VERIFY_IDENTITY`, and `DISABLED` through Connector/Python arguments, including explicit SSL client flag handling for servers with `require_secure_transport=ON`.
- Kept server-side cached connections per active profile/session while validating every borrowed connection with `SELECT 1` before use.
- Updated DB Admin helpers to consume MySQL utility exception aliases and SQL literal escaping instead of importing a connector directly.
- Added `mysql_util_refactor_plan.html` to document the refactor, validation matrix, platform checks, OCI setup verification, rollback path, and `myapp` skill synchronization.
- Fixed macOS `setup.sh` HTTPS setup so Linux-only `service_user` and `service_group` values are initialized before TLS asset handling.
- Updated the local `myapp` skill and the `codexSKILL` mirror so future MySQL apps default to Connector/Python and the `modules/mysql_util.py` pattern.

## 1.0.3n Summary

Version `1.0.3n` fixes Ubuntu and Oracle Linux 8 OCI Compute first-boot setup for the app-managed local MySQL deployment path.

- Ubuntu setup now retries virtualenv creation after installing the matching `python3.12-venv` package when Python 3.12 exists but `ensurepip` support is missing.
- Ubuntu MySQL Shell setup now removes a stale MySQL APT source list before the first `apt-get update` and uses the current MySQL 2025 signing key before recreating the repository file.
- Ubuntu app-managed local MySQL setup now writes a local AppArmor allowance for DBConsole's generated `etc/my.cnf` and `.data/` tree before running `mysqld --initialize`.
- Oracle Linux 8 setup now disables the platform MySQL module before installing Oracle MySQL community server/client packages, avoiding DNF modular filtering.
- Live OCI Compute validation completed on Ubuntu and OL8 with active `dbconsole-https.service`, app-local socket-only MySQL, `localadmin@localhost`, and HTTPS `200` responses.

## 1.0.3m Summary

Version `1.0.3m` tightens generated MySQL artifact ignore and auto-update validation behavior.

- Validated that `.data/` is ignored for the DBConsole-managed MySQL datadir, socket, PID, temporary files, and error log.
- Confirmed that only generated `etc/my.cnf` is ignored; the `etc/` directory remains available for future tracked templates.
- Tightened Auto-Update clean-worktree allowances so `etc/my.cnf` is treated as an exact generated local file, not a path prefix.

## 1.0.3l Summary

Version `1.0.3l` documents and validates the platform setup and OCI init-script behavior after the app-managed MySQL bootstrap change.

- Added README validation status for Oracle Linux 9, Oracle Linux 8, Ubuntu, and macOS.
- Recorded that OL9 was live-validated on OCI Compute with app-local MySQL, `localadmin@localhost` socket login, active HTTPS service, and HTTPS `200` response.
- Clarified that OL8 and Ubuntu use the same shared app-managed MySQL bootstrap path with static validation, while macOS remains a local-hosting target outside the OCI Linux init script.

## 1.0.3k Summary

Version `1.0.3k` changes Linux local MySQL bootstrap to an app-managed initialization model for OCI Compute and other fresh hosts.

- Linux setup now installs MySQL Server binaries from the platform package manager but writes DBConsole's own `etc/my.cnf`.
- The generated MySQL config uses the installed MySQL `basedir`, stores the datadir under `.data/mysql`, writes the error log under `.data/log`, uses an app-local socket under `.data/run`, and keeps MySQL socket-only with `skip-networking` and MySQL X Plugin disabled.
- Setup runs `mysqld --initialize`, reads the generated temporary root password from the app-local error log, renames `root@localhost` to the submitted local admin account, and sets the submitted password.
- Runtime start and stop scripts now manage the DBConsole-owned Linux MySQL process from the saved app-local config instead of relying on the package-created system MySQL datadir.

## 1.0.3j Summary

Version `1.0.3j` fixes OL9 OCI Compute first-boot provisioning when MySQL 9.7 installs with an unknown package-generated root password and the init-file path does not create `localadmin`.

- Added a one-time local MySQL grant-table bypass fallback that runs with `skip-networking`, creates or resets only `localadmin@localhost`, removes the temporary config, restarts MySQL normally, and verifies the supplied localadmin password.
- Kept the recovery path root-safe: setup still does not create a MySQL `root` user and does not reset `root@localhost`.
- Updated OCI first-boot guidance so the fallback behavior is documented for DBConsole-managed local MySQL installs.

## 1.0.3i Summary

Version `1.0.3i` applies the dependency security fixes from the latest vulnerability report.

- Raised the Paramiko dependency range to the fixed 5.x release line for SSH tunnel handling.
- Updated setup-created and auto-updated virtual environments to refresh `setuptools` with `pip` and `wheel`.
- Keeps dependency audit automation in the deployment path so future setup and auto-update runs continue to report package vulnerabilities.

## 1.0.3h Summary

Version `1.0.3h` fixes fresh OCI Compute setup when Python 3.12 must be installed during `setup.sh`.

- Redirected Python package-manager install output away from the interpreter path capture so `setup.sh` records only the resolved Python command.
- Prevents fresh Oracle Linux installs from treating DNF progress output as part of the Python executable path.

## 1.0.3g Summary

Version `1.0.3g` tightens Auto-Update credential handling after local admin bootstrap.

- Auto-Update collects a temporary `localadmin` password only when `local-admin-profile` is missing or not socket-only.
- Existing `local-admin-profile` sessions no longer see or submit localadmin password setup fields on Auto-Update.
- Existing localadmin password changes remain available only through the local-admin password change flow.

## 1.0.3f Summary

Version `1.0.3f` makes the local-admin trust boundary explicit while preserving a first-time bootstrap path for older deployments.

- Auto-Update is normally available only after signing in through `local-admin-profile`.
- Older deployments where `local-admin-profile` is missing or not socket-only can use a first-time authenticated Auto-Update bootstrap that requires a temporary `localadmin` password, password confirmation, and explicit reset confirmation.
- Added `reset_localadmin_password.sh` as a support utility for creating or resetting only `localadmin@localhost`.
- Local MySQL provisioning creates or repairs only `localadmin@localhost`; it does not create a MySQL `root` user and does not reset `root@localhost`.
- Renamed the setup recovery switch to `LOCAL_MYSQL_INIT_FILE_PROVISIONING`, keeping `LOCAL_MYSQL_RESET_UNKNOWN_ROOT` only as a compatibility alias.

## 1.0.3e Summary

Version `1.0.3e` fixes the local MySQL recovery path on MySQL packages that do not read `/etc/my.cnf.d` unless `/etc/my.cnf` explicitly includes it.

- Setup now ensures the DBConsole MySQL config include directory is loaded before writing socket-only and temporary init-file recovery configs.
- Recovery config files are written with readable root-owned permissions and SELinux contexts are restored when `restorecon` is available.
- The one-time localadmin init-file provisioning path can now apply on Oracle Linux MySQL packages that only read `/etc/my.cnf` by default.

## 1.0.3d Summary

Version `1.0.3d` removes the need to know MySQL's generated `root@localhost` password during DBConsole-managed local MySQL bootstrap.

- Added a one-time localadmin init-file provisioning path for OL and Ubuntu deployments when direct localadmin or root-authenticated setup is unavailable.
- The recovery path uses sudo, a temporary MySQL init file, and the existing socket-only local MySQL configuration to create or repair `localadmin`.
- The recovery path creates or repairs only `localadmin@localhost`; it does not create a MySQL `root` user and does not reset `root@localhost`.
- Added a setup switch to disable init-file localadmin provisioning on hosts that should never use a MySQL init file.

## 1.0.3c Summary

Version `1.0.3c` repairs Python runtime migration for deployments that already had a `.venv` created by Python 3.9 before the Python 3.12 policy was introduced.

- Rebuilds an existing `.venv` when its interpreter is older than the configured `DBCONSOLE_PYTHON_MIN_VERSION`.
- Installs dependencies through `.venv/bin/python -m pip` so pip, packages, and the service interpreter stay aligned.
- Fails setup with a clear message if the rebuilt virtual environment still does not satisfy the Python 3.12+ policy.

## 1.0.3b Summary

Version `1.0.3b` improves local MySQL bootstrap recovery for hosts where MySQL Server is already installed and `root@localhost` has an existing password.

- Added transient `LOCAL_MYSQL_ROOT_PASSWORD` support for setup runs that need to create or repair `local-admin-profile` on an already-initialized MySQL server with known root credentials.
- Added an optional Auto-Update root password field for one-time bootstrap repair in this release; later releases removed root-password handling from Auto-Update and kept only a localadmin password reset bootstrap.
- Updated setup recovery order to try the requested local admin account, socket-root authentication, supplied root credentials, and supplied admin password as root credentials for compatibility; later releases removed root-password reset behavior.
- Updated deployment documentation to explain when `LOCAL_MYSQL_ROOT_PASSWORD` is needed.

## 1.0.3a Summary

Version `1.0.3a` extends the deployment and Auto-Update hardening work with Python 3.12+ runtime policy, dependency audit automation, stricter update trust checks, and local file permission repair for existing installations.

- Raised setup-created deployments to Python 3.12 or newer, with platform-specific package installation and `pyproject.toml` metadata.
- Added pinned Python dependency ranges for Flask, PyMySQL, SSH tunneling, Paramiko, and certificate handling.
- Added dependency audit automation through `pip-audit`, with warn-by-default behavior and an optional strict mode for deployments that should fail on unresolved vulnerabilities.
- Updated Auto-Update to pass Python, audit, and trust-boundary settings through to setup so existing deployments can rebuild the virtual environment during patching.
- Added git remote and branch trust checks before Auto-Update fetches or pulls source changes.
- Hardened runtime file permissions before and after setup and Auto-Update, including profile stores, object storage settings, Flask secret material, update state, logs, and TLS secrets.
- Persisted secure cookie defaults for HTTPS deployments.
- Tightened git ignore coverage for embedded downloads, runtime caches, security reports, secrets, TLS material, and generated local deployment artifacts.

## 1.0.3a Upgrade Behavior

| Area | Behavior in 1.0.3a |
| --- | --- |
| Python runtime | Setup selects Python 3.12 or newer and can install the required interpreter packages on supported platforms. |
| Existing Auto-Update deployments | Auto-Update forwards the Python runtime policy to setup, then repairs permissions after git state restoration, dependency installation, and setup completion. |
| Update trust boundary | The update worker verifies the configured git remote and branch before fetching or pulling. |
| Dependency audit | Setup installs and runs `pip-audit` by default in warn mode, with strict mode available through deployment environment settings. |
| Local runtime files | Generated local files remain ignored by git, are preserved during safe update flows, and are permission-hardened after patching. |

## 1.0.3 Summary

Version `1.0.3` focuses on deployment hardening, secured connection profile management, local socket-only MySQL administration, and safer auto-update behavior for existing installations.

- Added socket-only local MySQL provisioning for the bootstrap `local-admin-profile`.
- Restricted profile management to authenticated sessions using `local-admin-profile`.
- Added first-login local admin password rotation and logout after the password change.
- Added SSH private key upload handling with app-owned storage and restrictive file permissions.
- Hardened the login screen so it displays profile names only, without internal hosts, sockets, SSH keys, or jump-server details.
- Added embedded MySQL Shell fallback when the platform package manager does not provide the required Innovation version.
- Added macOS local MySQL server support through public Oracle tar installation under the application runtime directory.
- Added explicit `start_mysql.sh` and `stop_mysql.sh` operations for local MySQL startup and shutdown.
- Updated OCI Compute initialization to require an explicit local admin password and pass through embedded runtime settings.
- Updated Auto-Update to preserve allowed local runtime files across git pulls that remove those files from source control.
- Added Auto-Update bootstrap prompts for missing or non-socket `local-admin-profile`; later releases narrowed this behavior so the refreshed page prompts only for a localadmin password reset and never for a root password.
- Expanded git ignore coverage for runtime, embedded, temporary, and security-sensitive local files.

## Upgrade Behavior

| Area | Behavior in 1.0.3 |
| --- | --- |
| Existing Auto-Update pages | Old pages can complete a code-refresh update first. The refreshed page then requires a temporary localadmin password and confirmation when `local-admin-profile` is missing or not socket-only. |
| Local admin profile | `local-admin-profile` is created as a socket-only profile and marked for first-login password change. |
| Credential handling | Temporary local admin passwords are passed only to setup/update worker process environments and are not stored in profile files, runtime env files, update status, or logs. |
| Local runtime files | Generated runtime files such as profiles, object storage settings, uploaded SSH keys, TLS files, and embedded runtimes are ignored by git and preserved during safe update flows. |

## Operational Notes

- For new OCI Compute deployments, set `LOCAL_MYSQL_ADMIN_PASSWORD` explicitly in the initialization script before creating the instance.
- For existing deployments without `local-admin-profile`, run Auto-Update once to refresh code, then rerun Auto-Update from the refreshed page with the temporary local admin password.
- After bootstrap, sign in with `local-admin-profile`, username `localadmin`, and the temporary password; DBConsole requires an immediate password change and then logs out.

## HTML Version

The standalone HTML version remains available in `version_history.html` for local browser viewing. GitHub repository `/blob/` pages display HTML files as source code, so use this Markdown file for formatted viewing inside GitHub.
