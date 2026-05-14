# DBConsole Version History

Version summary from `1.0.2a` to `1.0.3`.

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
