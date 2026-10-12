# DBConsole on Oracle Linux 9

Run DBConsole directly on an Oracle Linux 9 host or in Docker containers. A native OCI Compute installation uses systemd and Instance Principal for Object Storage. Installations outside OCI use an OCI config profile and private API signing key.

## Choose a deployment

| Option | Runtime | Browser access | Installation |
| --- | --- | --- | --- |
| Native OL9 host | Gunicorn, Flask, MySQL Shell and local MySQL on the host | HTTPS on port `443` by default | [Existing host](#install-on-an-existing-host) |
| Native OCI Compute host | Same native runtime, with OCI networking and IAM | HTTPS through the instance public IP or hostname | [OCI first boot](#oci-first-boot) |
| Docker on OL9 | Separate DBConsole and local MySQL containers | HTTP on host port `8080` | [Docker installation](#option-2-docker-on-ol9) |

## Prerequisites

- Oracle Linux 9 and a non-root account with `sudo` access (`opc` on a typical OCI image).
- Outbound HTTPS access to GitHub and package repositories; access to OCI endpoints if using Object Storage.
- A network path to each remote MySQL target, with its configured TCP port or SSH forwarding path allowed.
- For a native HTTPS deployment, allow inbound TCP `443` from approved clients in the host firewall and upstream network controls. On OCI, configure the subnet security list or NSG as well; setup cannot change OCI network rules.
- For Object Storage, a bucket and the matching [authentication configuration](#object-storage-authentication).

## Option 1: Native OL9 host

DBConsole runs as a systemd service. Gunicorn serves Flask, MySQL Shell runs background jobs, and a local MySQL service authenticates the local administrator over a Unix socket. Connection profiles can point to separate remote MySQL targets. Application state defaults to `/var/lib/dbconsole`, while runtime files use `/run/dbconsole` under systemd.

### Example architecture on OCI

![Oracle Linux 9 on OCI deployment architecture](../docs/diagrams/architecture-oci.svg)

This example places the DBConsole Compute instance in a public application subnet and a separate, self-managed MySQL target in a private subnet. The local administrator database remains on the DBConsole host. Object Storage is a regional service outside the VCN.

| Component | Example configuration |
| --- | --- |
| Region and VCN | `uk-london-1`, VCN `10.20.0.0/16` |
| Application subnet | Public `10.20.10.0/24`; Compute VNIC with a public IP; HTTPS `443` from approved administrator CIDRs |
| Database subnet | Private `10.20.20.0/24`; target MySQL accepts TCP `3306` from the application NSG |
| Internet Gateway | Application subnet default route for internet access, including installation and updates |
| Service Gateway | Regional Object Storage service route for HTTPS access to the bucket |
| IAM | Compute dynamic group and bucket permissions for Instance Principal |

Region, CIDRs and topology are illustrative. Create OCI networking and IAM separately from application setup. For an OL9 host outside OCI, the same native application processes run on that host; use its existing network and OCI config authentication instead of Instance Principal.

[Full OCI architecture details](../docs/architecture.md#option-3-oci-host) · [Diagram source](../docs/diagrams/architecture-oci.drawio)

### Install on an existing host

Clone the repository as your normal login user:

```bash
sudo dnf install -y git
git clone https://github.com/ivanxma/mysqlconsole.git
cd mysqlconsole
./OL9/setup.sh https --https-port 443
```

Setup provisions the Python environment, MySQL Shell, the local administrator MySQL service and the DBConsole systemd service. Supply `SSL_CERT_FILE` and `SSL_KEY_FILE` during setup to use your certificate; otherwise setup generates a self-signed certificate.

For environment-only preparation, run `./OL9/setup.sh none`. For loopback HTTP access, use `./OL9/setup.sh http --http-port 8080`; remote native access uses HTTPS.

### OCI first boot

Before launching the instance, complete the [OCI network, bucket and IAM prerequisites](../docs/prerequisites-oci.md). While creating an OL9 Compute instance, open **Advanced options → Management → Initialization script** and paste:

```bash
#!/bin/bash
curl -fsSL https://raw.githubusercontent.com/ivanxma/mysqlconsole/main/oci_compute_init.sh | env OS_FAMILY=ol9 bash
```

The initialization script clones DBConsole, runs OL9 setup, installs MySQL Shell, and creates the service. It records status in `/var/log/dbconsole-init.log`. On subsequent runs, it refreshes the checkout with a fast-forward pull.

After boot, connect using `ssh opc@<public-ip>`. The login banner shows installation state. Check progress and service readiness:

```bash
sudo tail -n 100 /var/log/dbconsole-init.log
sudo systemctl status dbconsole-https.service
```

### First login

Open `https://<host-or-public-ip>` and choose `local-admin-profile`. Fresh installations default to username `localadmin` and password `ChangeMe123!`; the application requires an immediate password change. If setup used an explicit credential override, use that value instead.

Add connection profiles for remote databases after signing in. The local administrator database and remote target databases have separate credentials.

## Option 2: Docker on OL9

![Docker deployment architecture on Oracle Linux 9](../docs/diagrams/architecture-docker.svg)

DBConsole and `mysql:innovation` run in separate containers. They share a private Unix socket volume for local administrator login; MySQL exposes no TCP port. Named volumes retain application state and MySQL data. The browser reaches DBConsole through host port `8080`.

From the repository root, install Docker Engine and the Compose plugin:

```bash
./OL9/init_docker.sh
```

Sign out and back in so Docker group membership takes effect, return to the repository root, and start the stack:

```bash
./docker/setup_docker.sh
```

Open `http://127.0.0.1:8080` from a browser on the host. For a remote OL9 host, forward that port to your workstation with `ssh -L 8080:127.0.0.1:8080 <user>@<host>`, then use the same browser address. The supplied Compose mapping publishes port `8080` on the host, so network reachability also depends on its firewall and upstream rules.

Use the same initial local administrator credentials described above. Docker updates use `./docker/setup_docker.sh`; in-application Auto-Update is unavailable for container deployments.

[Docker installation and operations](../docker/README.md) · [Docker architecture details](../docs/architecture.md#option-1-docker-containers) · [Diagram source](../docs/diagrams/architecture-docker.drawio)

## Object Storage authentication

| Environment | Authentication |
| --- | --- |
| Native OCI Compute host | Instance Principal, authorized through a Compute dynamic group and bucket IAM policy |
| Native OL9 host outside OCI | OCI config profile and private API signing key; explicitly enable `oci_config` in the service environment |
| Docker outside OCI | Stored OCI config profile and private key, configured through **Admin > Setup Object Storage > OCI Config** |
| Docker on OCI Compute | Stored config is tried first; if unavailable, the supplied configuration attempts Instance Principal, which requires metadata access and IAM permissions |

For a native OL9 service outside OCI, use `sudo systemctl edit dbconsole-https.service` to set:

```ini
[Service]
Environment=DBCONSOLE_OBJECT_STORAGE_AUTH_MODE=oci_config
Environment=DBCONSOLE_OCI_CONFIG_DIR=/var/lib/dbconsole/oci-config
```

Use the `oci-config` directory under your actual state directory if it differs from `/var/lib/dbconsole`. Apply the configuration:

```bash
sudo systemctl daemon-reload
sudo systemctl restart dbconsole-https.service
```

Then enter the OCI profile and private key under **Admin > Setup Object Storage > OCI Config**, and select that profile for the bucket. For HTTP installations, substitute `dbconsole-http.service`. Object Storage credentials are separate from MySQL login credentials.

For OCI Instance Principal policies and bucket configuration, see [OCI prerequisites](../docs/prerequisites-oci.md). For Docker config storage and profile selection, see [Docker Object Storage authentication](../docker/README.md#object-storage-authentication).

## Operations

For a native HTTPS installation:

```bash
sudo systemctl status dbconsole-https.service
sudo journalctl -u dbconsole-https.service -n 100 --no-pager
sudo systemctl restart dbconsole-https.service
```

Use `dbconsole-http.service` when HTTP was selected. Native application updates are available through **Admin > Auto-Update** while signed in with `local-admin-profile`. Rerun setup for host packages, MySQL Shell upgrades, firewall changes or service provisioning. Restarts clear in-memory login sessions.

See [Operations](../docs/operations.md) for local MySQL control and administrator password reset, or the [Docker guide](../docker/README.md#lifecycle-commands) for container lifecycle commands.

## Files in this directory

| File | Purpose |
| --- | --- |
| [setup.sh](setup.sh) | Calls the shared setup script with Oracle Linux 9 selected |
| [oci_compute_init.sh](oci_compute_init.sh) | OL9 entry point for OCI initialization |
| [init_docker.sh](init_docker.sh) | Installs Docker Engine, Compose, and Git; grants the login user Docker group membership |
| [install_mysql_shell_innovation.sh](install_mysql_shell_innovation.sh) | MySQL Shell Innovation installer |
