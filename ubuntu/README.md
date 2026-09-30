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

Use `./ubuntu/oci_compute_init.sh` as the OCI user-data entry point. It defaults to `localadmin` / `ChangeMe123!`; override the environment only when needed and change the password immediately after first login. See [OCI prerequisites](../docs/prerequisites-oci.md) for the test bucket and Instance Principal IAM policy.
