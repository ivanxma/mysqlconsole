# DBConsole Version History

Version summary from `1.0.2a` to `1.0.3b`.

## 1.0.3b Summary

Version `1.0.3b` improves local MySQL bootstrap recovery for hosts where MySQL Server is already installed and `root@localhost` has an existing password.

- Added transient `LOCAL_MYSQL_ROOT_PASSWORD` support for setup and Auto-Update runs that need to create or repair `local-admin-profile` on an already-initialized MySQL server.
- Added an optional Auto-Update root password field for one-time bootstrap repair; supplied root credentials are passed only to the update worker environment and are not written to runtime files, update status, or logs.
- Updated setup recovery order to try the requested local admin account, socket-root authentication, supplied root credentials, supplied admin password as root credentials for compatibility, and then the MySQL temporary root password log.
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
- Added Auto-Update bootstrap prompts for missing or non-socket `local-admin-profile`, including old-version compatibility where the first update refreshes code and the second update collects the temporary local admin password.
- Expanded git ignore coverage for runtime, embedded, temporary, and security-sensitive local files.

## Upgrade Behavior

| Area | Behavior in 1.0.3 |
| --- | --- |
| Existing Auto-Update pages | Old pages can complete a code-refresh update first. After restart, the refreshed Auto-Update page prompts for `localadmin` and a temporary password when local admin bootstrap is still required. |
| Local admin profile | `local-admin-profile` is created as a socket-only profile and marked for first-login password change. |
| Credential handling | Temporary local admin passwords are passed only to setup/update worker process environments and are not stored in profile files, runtime env files, update status, or logs. |
| Local runtime files | Generated runtime files such as profiles, object storage settings, uploaded SSH keys, TLS files, and embedded runtimes are ignored by git and preserved during safe update flows. |

## Operational Notes

- For new OCI Compute deployments, set `LOCAL_MYSQL_ADMIN_PASSWORD` explicitly in the initialization script before creating the instance.
- For existing deployments without `local-admin-profile`, run Auto-Update once to refresh code, then rerun Auto-Update from the refreshed page with the temporary local admin password.
- After bootstrap, sign in with `local-admin-profile`, username `localadmin`, and the temporary password; DBConsole requires an immediate password change and then logs out.

## HTML Version

The standalone HTML version remains available in `version_history.html` for local browser viewing. GitHub repository `/blob/` pages display HTML files as source code, so use this Markdown file for formatted viewing inside GitHub.
