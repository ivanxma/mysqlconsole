# Oracle Linux 9 deployment

This directory contains the OL9 MySQL Shell Innovation installer and the OL9 deployment entry points.

## Prerequisites

- Oracle Linux 9 with a non-root account that can run `sudo`.
- Outbound HTTPS access to GitHub, Oracle package repositories, and OCI services when applicable.
- TCP `443` ingress for HTTPS (or `80` for HTTP) in both the OCI NSG/security list and host firewall.
- An explicit local MySQL administrator password. Setup does not generate or log one.

## Install

From the repository root:

```bash
LOCAL_MYSQL_ADMIN_USER=localadmin LOCAL_MYSQL_ADMIN_PASSWORD='choose-a-password' ./OL9/setup.sh https --https-port 443
```

For environment-only preparation, use `./OL9/setup.sh none`.

## OCI first boot

Use `./OL9/oci_compute_init.sh` as the OCI user-data entry point. Supply `LOCAL_MYSQL_ADMIN_PASSWORD` through the instance initialization environment. See [OCI prerequisites](../docs/prerequisites-oci.md) for the test bucket and Instance Principal IAM policy.
