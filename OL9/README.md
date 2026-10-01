# Oracle Linux 9 deployment

This directory contains the OL9 MySQL Shell Innovation installer, Docker bootstrap, and OL9 deployment entry points.

## Prerequisites

- Oracle Linux 9 with a non-root account that can run `sudo`. The Docker bootstrap installs Git for the repository clone.
- Outbound HTTPS access to GitHub, Oracle package repositories, and OCI services when applicable.
- TCP `443` ingress for HTTPS (or `80` for HTTP) in both the OCI NSG/security list and host firewall.
- Fresh setup defaults to `localadmin` / `ChangeMe123!` and forces an immediate password change at first sign-in. Override the environment only when needed.

## Install

From the repository root:

```bash
./OL9/setup.sh https --https-port 443
```

For environment-only preparation, use `./OL9/setup.sh none`. Both commands default to `localadmin` / `ChangeMe123!`; change the password immediately after first login.

## Docker Engine

For the socket-only DBConsole Docker deployment, install Docker Engine and the Compose plugin first:

```bash
./OL9/init_docker.sh
```

Sign out and back in so the `docker` group membership takes effect, then clone DBConsole and run `./docker/setup_docker.sh`. See the [Docker deployment guide](../docker/README.md).

## OCI first boot

Create the Compute instance, then open **Advanced options → Management → Initialization script** and paste this one-step installer. It clones DBConsole, runs the OL9 setup, installs MySQL Shell Innovation, creates the service, and leaves status in `/var/log/dbconsole-init.log`.

```bash
#!/bin/bash
curl -fsSL https://raw.githubusercontent.com/ivanxma/mysqlconsole/main/oci_compute_init.sh | env OS_FAMILY=ol9 bash
```

After boot, connect with `ssh opc@&lt;public-ip&gt;`. The login banner shows installation state; verify with `sudo systemctl status dbconsole-https.service` and open `https://&lt;public-ip&gt;`. The init script is rerunnable: it refreshes its Git checkout with a fast-forward pull instead of replacing it. See [OCI prerequisites](../docs/prerequisites-oci.md) for ingress, the test bucket, and Instance Principal IAM policy.
