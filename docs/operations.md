# Operations

## Service status on Linux

```bash
sudo systemctl status dbconsole-https.service
sudo journalctl -u dbconsole-https.service -n 100 --no-pager
```

Use `dbconsole-http.service` when HTTP was selected during setup.

## Update

Use **Admin > Auto-Update** while signed in through `local-admin-profile` for application updates. When the hardened service cannot use passwordless sudo, Auto-Update refreshes application code and Python dependencies, leaves the running socket-only local MySQL service and credentials unchanged, and restarts DBConsole. Rerun `./setup.sh` from SSH when package installation, MySQL Shell upgrades, local-MySQL provisioning, firewall changes, or systemd configuration must be refreshed.

## Local MySQL

Use `start_mysql.sh` and `stop_mysql.sh` only for the app-managed MySQL installation on Linux or macOS. Docker installations are managed with Compose; see [Docker installation](../docker/README.md).

## Reset the local administrator

Use `reset_localadmin_password.sh` on a host installation when the local administrator password must be reset. It never saves the password. Docker uses `localadmin`; its initial password is held in ignored `docker/.env`. After MySQL initialization, change the account password through DBConsole or MySQL rather than editing `.env` alone.
