# DBConsole Version History

Version summary from `1.0.2a` to `1.1.0`.

## 1.1.0 Summary

Version `1.1.0` adds a hardened MySQL Shell dump/load workflow using OCI Compute Instance Principal authentication.

- Added MySQL Shell Dump/Load, reusable Option Profiles, PAR Setup, validation, typed dump/load controls, include/exclude object selectors, and durable job history.
- Uses Instance Principal exclusively for Object Storage. Legacy OCI API-key configuration aliases are rejected, and DBConsole does not consume OCI private keys or global OCI configuration.
- Opens the complete MySQL Shell menu only to `local-admin-profile` or authenticated MySQL accounts with `SYSTEM_USER`; all other MySQL Shell routes fail closed.
- Added `Delete after used` PAR cleanup, private runtime/state directories, redacted job logs, Gunicorn production serving, and systemd hardening.
- Added live job percentage progress from redacted MySQL Shell output; completed jobs show `100%`.
- Added dump-only Lakehouse exclusion: Instance/Schema Dump resolve visible `ENGINE=LAKEHOUSE` tables and construct qualified MySQL Shell `excludeTables` options at preview and submission time.
- Improved DB Admin primary-key workflows, status/variable handling, UI consistency, browser security headers, update safety, and resource bounds.

## 1.0.4d Summary

Version `1.0.4d` completes the Instance Principal, runtime-state, update authorization, Object Storage validation, and shared destructive-action hardening work. It adds a 2 GiB default Object Storage validation limit, session-bound import plans, secure HTTPS cookies, private systemd runtime state, release metadata assertions, and regression coverage for the security boundaries.

## 1.0.4c Summary

Version `1.0.4c` serializes each authenticated session's cached MySQL connection.

- Added a per-session re-entrant connection lock, so concurrent requests from the same session cannot use Connector/Python concurrently.
- Added registry synchronization for server-session creation, lookup, expiry, and logout.
- Makes connection cleanup wait for an in-progress request before closing its cached connection or SSH tunnel.
- Added regression tests proving same-session serialization, different-session independence, and safe close-after-use.
- Aligned package metadata with the application version.

## 1.0.4b Summary

Version `1.0.4b` reorganizes the downloaded Admin Dashboard report into compact, collapsed detail groups.

- Made Installed Components and Password Policy individually collapsible and collapsed by default.
- Grouped audit variables/status, applied users, and filters under MySQL Audit.
- Grouped firewall variables/status, users, and rules under MySQL Firewall.
- Grouped replica and Group Replication tables under Replication Details.
- Grouped database inventory, InnoDB tables, views, routines, and events under DB Inventory Details.

## 1.0.4a Summary

Version `1.0.4a` improves the downloaded Admin Dashboard report and Object Storage folder selection.

- Made Global Variables, Global Status, and Error Log report sections collapsible and collapsed by default.
- Expanded report error-log collection to 2,000 rows and added a client-side `Exclude Note and System` / `ALL` priority view.
- Added database event inventory at the end of the report and populated InnoDB row/data/index/total size fields.
- Added an explicit Object Storage `Populate Folders` action with bounded recursive folder discovery under the configured profile prefix.

## 1.0.4 Summary

Version `1.0.4` fixes DB Admin schema-guard dependency wiring for primary-key repair and other schema-changing actions.

- Injected the shared system-schema guard into `modules/db_admin_queries.py` so primary-key repair no longer fails with an undefined helper.
- Added regression coverage proving system schemas are rejected before SQL execution and application schemas generate the expected primary-key statement.

## 1.0.3z Summary

Version `1.0.3z` replaces OCI API-key/config-file authentication with OCI Compute Instance Principal authentication for Object Storage.

- Removed OCI user, tenancy, fingerprint, private-key upload, and config-file/profile runtime settings and UI.
- Added a cached Instance Principal signer and explicit per-profile Object Storage region client configuration.
- Added deployment-region seeding through `DBCONSOLE_OBJECT_STORAGE_REGION` and OCI IMDSv2 without overriding saved cross-region targets.
- Revalidated profile, folder, and file dropdown population against the selected target's region, namespace, bucket, and prefix.
- Added server-side upload size, filename, extension, prefix, CSV/JSON text, Parquet/Avro signature, and post-upload object-size validation.

## 1.0.3y Summary

Version `1.0.3y` tightens DBConsole modularity and makes Monitoring Locks refresh behavior consistent across all lock views.

- Refactored app-level session, profile/config, update, and generic query helpers out of `app.py` into reusable service modules.
- Added `modules/session_services.py`, `modules/update_service.py`, `modules/config_services.py`, and `modules/query_service.py` so `app.py` focuses on Flask setup, service instantiation, dependency wiring, and route registration.
- Revalidated all local modules with direct imports and confirmed the Flask app still registers the expected routes after the service extraction.
- Updated `Monitoring > Locks > Connection` to reuse the shared Auto Refresh toolbar used by Row Locks and Meta Locks.
- Removed the Connection tab's separate `connection_refresh` URL parameter and per-tab reload script so refresh period behavior is controlled consistently from the shared Monitoring auto-refresh UI.

## 1.0.3x Summary

Version `1.0.3x` adds the OCI/Lakehouse administration workflow, stored routine export improvements, and Object Storage file selection for HeatWave Load.

- Reworked `Admin > Setup OCI Config` with a one-row overview, source selection for app-local user config or `~/.oci/config`, profile inspection, app-local git-ignored config storage, and OCI config testing.
- Added `HeatWave > External Table/Lakehouse` upload support for CSV, JSON, Parquet, Delta, and Avro files, with Object Storage folder listing and optional folder creation.
- Added `HeatWave Load` Object Storage folder and file selectors that fill the editable `oci://bucket@namespace/path/file` URI and infer the file format from the selected file extension.
- Extended `HW Admin` with a `Lakehouse` tab that lists Lakehouse tables and shows load state, progress, load status, recovery source, and errors.
- Enhanced `MySQL > DB Admin > SP/Function` with checkbox selection, individual and bulk `.sql` export, row-style routine overview, and charset/collation metadata.

## 1.0.3v Summary

Version `1.0.3v` makes the SQL Workspace `NL_SQL` generated-SQL workaround explicit and opt-in.

- Added a `Generated SQL for NL_SQL` checkbox to the SQL Workspace toolbar.
- Applies the `execute=true` to `execute=false` generated-SQL workaround only when that checkbox is enabled and the submitted `sys.NL_SQL` options explicitly request `execute=true`.
- Leaves `sys.NL_SQL` calls with `execute=false`, unchecked calls, and non-`NL_SQL` statements on the normal SQL Workspace execution path.

## 1.0.3u Summary

Version `1.0.3u` fixes generated SQL extraction from HeatWave `sys.NL_SQL` output.

- Reads the generated statement from the `sql_query` field returned in the `NL_SQL` output JSON.
- Keeps the existing verbose-log fallback for outputs that include `Generated SQL statement:`.
- Includes a short `NL_SQL` output excerpt in the SQL Workspace error when no executable generated `SELECT` can be found.

## 1.0.3t Summary

Version `1.0.3t` fixes SQL Workspace rendering for HeatWave `sys.NL_SQL` result sets with non-ASCII generated column aliases.

- Treats `CALL sys.NL_SQL(...)` as SQL generation first by forcing the options payload to `execute=false` when possible.
- Reads the generated SQL from the submitted output variable and executes that generated `SELECT` directly in SQL Workspace.
- Avoids the `NL_SQL execute=true` procedure result metadata path that can return replacement headers such as `??` for Japanese aliases.

## 1.0.3s Summary

Version `1.0.3s` fixes SQL Workspace failures caused by HeatWave secondary-engine session state leaking into DBConsole metadata queries.

- Forced DBConsole database inventory queries against `information_schema` to run with `use_secondary_engine=OFF`.
- Reset SQL Workspace secondary-engine session state after executing user statements so cached MySQL connections do not carry `ON` or `FORCED` into later application queries.
- Prevents `3889 (HY000): Secondary engine operation failed` errors when SQL Workspace refreshes the page after HeatWave-enabled execution.

## 1.0.3r Summary

Version `1.0.3r` syncs the validated modular DBConsole refactor and deployment fixes from the test environment into the main source.

- Extracted dashboard, DB Admin, monitoring, and session logic into focused modules so `app.py` is limited to app setup, dependency wiring, and route registration.
- Fixed post-login dashboard regressions caused by missing extracted helpers and missing session-profile dependency injection.
- Injected MySQL connection access into extracted DB Admin and monitoring query modules instead of relying on removed `app.py` globals.
- Moved Monitoring Locks request parsing into the route layer and made monitoring report downloads return an error CSV row when optional HeatWave `performance_schema.rpd_*` tables are unavailable.
- Fixed macOS embedded MySQL Server archive detection and completed validation on macOS plus fresh OCI Compute OL8, OL9, and Ubuntu 24.04 deployments with authenticated page/download sweeps and cleanup.

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

## Viewing Version History

Use this Markdown file for formatted version-history viewing inside GitHub.
