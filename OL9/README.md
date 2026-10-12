# Install DBConsole on Oracle Linux 9

DBConsole can run directly on an Oracle Linux 9 server or in Docker containers on that server. Both installations provide the web console, MySQL Shell jobs, and a local MySQL database for administrator login. You can then add connection profiles for the MySQL databases you want to manage.

Choose how to run the application first. If your server is an OCI Compute instance, the native installation also supports automated first-boot setup and Instance Principal access to Object Storage.

| Installation | What runs on the server | Start here |
| --- | --- | --- |
| Native host | DBConsole and local MySQL run as host processes; systemd manages the web service | [Install directly on OL9](#install-directly-on-ol9) |
| Docker | DBConsole and local MySQL run in separate containers with persistent volumes | [Install with Docker](#install-with-docker) |

## Before you start

Use an OL9 server with a non-root account that has `sudo` access. The account is normally `opc` on OCI. The server needs outbound HTTPS access to GitHub and package repositories, plus network access to any remote MySQL targets.

For native remote access, allow HTTPS on TCP `443` from your administrator clients. Configure both the host firewall and the surrounding network controls. On OCI, this includes the subnet security list or NSG; the installer does not create OCI network rules.

Object Storage is optional. Its authentication depends on where the application runs: **Instance Principal on a native OCI host**, or **OCI config outside OCI**.

## Install directly on OL9

The native installer creates a Python environment, installs MySQL Shell and the local MySQL service, and configures DBConsole as a systemd service. The browser connects to Gunicorn over HTTPS. Local administrator login uses a Unix socket; profiles for remote databases use their configured network connections.

Clone the repository and run setup as your normal login user:

```bash
sudo dnf install -y git
git clone https://github.com/ivanxma/mysqlconsole.git
cd mysqlconsole
./OL9/setup.sh https --https-port 443
```

To use your own TLS certificate, supply `SSL_CERT_FILE` and `SSL_KEY_FILE` during setup. Otherwise, setup generates a self-signed certificate. Application state defaults to `/var/lib/dbconsole`; the systemd service uses `/run/dbconsole` for runtime files.

Open `https://<host-or-public-ip>`. Select `local-admin-profile` and sign in with `localadmin` / `ChangeMe123!` on a fresh installation. DBConsole requires an immediate password change. If you supplied different credentials during setup, use those instead.

For preparation without starting a web service, use `./OL9/setup.sh none`. For local HTTP access, use `./OL9/setup.sh http --http-port 8080`; native HTTP binds to loopback by default.

### When the host is on OCI

The same native application can run on an OL9 Compute instance. The diagram below shows one possible network layout for that installation.

![Example native OL9 deployment on OCI](../docs/diagrams/architecture-oci.svg)

In this example, the DBConsole host has a public IP in an application subnet. Administrators reach it over HTTPS. A separate target MySQL server sits in a private subnet and accepts database connections from DBConsole. The small local MySQL database used for administrator login remains on the DBConsole host.

The application subnet uses an Internet Gateway for internet access and a Service Gateway for regional Object Storage access. A Compute dynamic group and IAM policy authorize bucket operations through Instance Principal. Object Storage remains outside the VCN.

The region, CIDRs and target database placement are examples. Provision the network and IAM to suit your environment before installing DBConsole. The application setup scripts do not create these OCI resources.

[OCI network and IAM prerequisites](../docs/prerequisites-oci.md) · [Detailed OCI architecture](../docs/architecture.md#oci-host) · [Diagram source](../docs/diagrams/architecture-oci.drawio)

### OCI first boot

For a new OCI instance, you can automate the native installation instead of running the manual commands above. While creating the OL9 instance, open **Advanced options → Management → Initialization script** and paste:

```bash
#!/bin/bash
curl -fsSL https://raw.githubusercontent.com/ivanxma/mysqlconsole/main/oci_compute_init.sh | env OS_FAMILY=ol9 bash
```

After boot, connect with `ssh opc@<public-ip>` and check installation progress:

```bash
sudo tail -n 100 /var/log/dbconsole-init.log
sudo systemctl status dbconsole-https.service
```

When the service is ready, open `https://<public-ip>` and use the first-login instructions above. The initialization script can be rerun; it updates the repository with a fast-forward pull.

### Object Storage for a native installation

On **OCI Compute**, use Instance Principal. Configure the Compute dynamic group and bucket IAM policy described in [OCI prerequisites](../docs/prerequisites-oci.md), then enter the region, namespace and bucket in **Admin > Setup Object Storage**.

On an **OL9 host outside OCI**, use an OCI config profile and private API signing key. Run `sudo systemctl edit dbconsole-https.service` and add:

```ini
[Service]
Environment=DBCONSOLE_OBJECT_STORAGE_AUTH_MODE=oci_config
Environment=DBCONSOLE_OCI_CONFIG_DIR=/var/lib/dbconsole/oci-config
```

If you changed the application state directory, use its `oci-config` subdirectory instead. Apply the service settings:

```bash
sudo systemctl daemon-reload
sudo systemctl restart dbconsole-https.service
```

Sign in again, enter the profile and private key under **Admin > Setup Object Storage > OCI Config**, and select that profile for the bucket. For an HTTP service, substitute `dbconsole-http.service` in the commands. Object Storage credentials are separate from MySQL login credentials.

### Manage the native service

```bash
sudo systemctl status dbconsole-https.service
sudo journalctl -u dbconsole-https.service -n 100 --no-pager
sudo systemctl restart dbconsole-https.service
```

Use **Admin > Auto-Update** for application updates while signed in with `local-admin-profile`. Rerun setup when host packages, MySQL Shell, firewall settings or service provisioning need to change. A service restart clears active login sessions.

See [Operations](../docs/operations.md) for local MySQL control and administrator password reset.

## Install with Docker

Docker runs the application and the local administrator database in separate containers. The application container includes Gunicorn, Flask and MySQL Shell. The MySQL container exposes only a shared Unix socket to DBConsole. Named volumes preserve application state and database files.

![DBConsole Docker deployment on OL9](../docs/diagrams/architecture-docker.svg)

The browser connects to host port `8080`. Remote MySQL targets and OCI Object Storage are optional connections from the application container.

[Detailed Docker architecture](../docs/architecture.md#docker-containers) · [Diagram source](../docs/diagrams/architecture-docker.drawio)

### Prepare Docker and start DBConsole

If you have not cloned the repository, do that first:

```bash
sudo dnf install -y git
git clone https://github.com/ivanxma/mysqlconsole.git
cd mysqlconsole
```

From the repository root, install Docker Engine and the Compose plugin:

```bash
./OL9/init_docker.sh
```

Sign out and back in to activate Docker group membership. Return to the repository root and start the stack:

```bash
./docker/setup_docker.sh
```

Open `http://127.0.0.1:8080` from a browser on the server. To use a browser on your workstation, open an SSH tunnel to the server:

```bash
ssh -L 8080:127.0.0.1:8080 <user>@<host>
```

Then open `http://127.0.0.1:8080` on the workstation. The supplied Compose mapping also publishes port `8080` on the server; direct network access depends on its firewall and upstream rules.

Select `local-admin-profile`. Fresh setup uses `localadmin` / `ChangeMe123!` and requires an immediate password change.

### Object Storage and updates in Docker

Outside OCI, enter your OCI profile and private key under **Admin > Setup Object Storage > OCI Config**, then select that profile for the bucket. Docker stores these files in the private application state volume.

The supplied Docker configuration tries stored OCI config first. If no usable config is available, it attempts Instance Principal; that fallback requires an OCI Compute host, access to instance metadata, and the required IAM permissions.

To update the containers, rerun `./docker/setup_docker.sh`. The named volumes are retained. In-application Auto-Update is unavailable in Docker.

See the [Docker guide](../docker/README.md) for lifecycle commands, volume details and Object Storage configuration.

## Installer reference

| Script | Purpose |
| --- | --- |
| [setup.sh](setup.sh) | Native Oracle Linux 9 setup |
| [oci_compute_init.sh](oci_compute_init.sh) | OL9 entry point for OCI first-boot initialization |
| [init_docker.sh](init_docker.sh) | Docker Engine, Compose and Git installation |
| [install_mysql_shell_innovation.sh](install_mysql_shell_innovation.sh) | MySQL Shell Innovation installation |
