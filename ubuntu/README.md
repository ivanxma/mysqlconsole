# Ubuntu deployment

This directory contains the Ubuntu MySQL Shell Innovation installer and Ubuntu deployment entry points.

## Prerequisites

- A supported Ubuntu host with a non-root account that can run `sudo`.
- Outbound HTTPS access to GitHub, package repositories, and OCI services when applicable.
- TCP `443` ingress for HTTPS (or `80` for HTTP) in both the OCI NSG/security list and host firewall.
- Fresh setup defaults to `localadmin` / `ChangeMe123!` and forces an immediate password change at first sign-in. Override the environment only when needed.

## Install

From the repository root:

```bash
./ubuntu/setup.sh https --https-port 443
```

For environment-only preparation, use `./ubuntu/setup.sh none`. Both commands default to `localadmin` / `ChangeMe123!`; change the password immediately after first login.

## OCI first boot

Create the Compute instance, then open **Advanced options → Management → Initialization script** and paste this one-step installer. It clones DBConsole, runs the Ubuntu setup, installs MySQL Shell Innovation, creates the service, and leaves status in `/var/log/dbconsole-init.log`.

```bash
#!/bin/bash
curl -fsSL https://raw.githubusercontent.com/ivanxma/mysqlconsole/main/oci_compute_init.sh | env OS_FAMILY=ubuntu bash
```

After boot, connect with `ssh ubuntu@&lt;public-ip&gt;`. The login banner shows installation state; verify with `sudo systemctl status dbconsole-https.service` and open `https://&lt;public-ip&gt;`. The init script is rerunnable: it refreshes its Git checkout with a fast-forward pull instead of replacing it. See [OCI prerequisites](../docs/prerequisites-oci.md) for ingress, the test bucket, and Instance Principal IAM policy.
