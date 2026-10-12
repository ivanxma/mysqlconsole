# MySQL DBConsole

DBConsole is a Flask web console for MySQL administration, SQL workspaces, monitoring, MySQL Shell dump/load operations, HeatWave workflows, and OCI Object Storage integration.

Current version: `1.1.20`

## Deployment options

Choose the deployment that matches where DBConsole will run. Each option has a separate diagram and [architecture details](docs/architecture.md).

### Docker containers on macOS or Linux

DBConsole and its local administrator MySQL database run in separate containers, connected by a private Unix socket. Open `http://127.0.0.1:8080`. Persistent Docker volumes hold application state and MySQL data. Outside OCI, use **OCI config** for Object Storage.

![Docker deployment](docs/diagrams/architecture-docker.svg)

[Docker installation](docker/README.md) · [Architecture details](docs/architecture.md#docker-containers) · [Diagram source](docs/diagrams/architecture-docker.drawio)

### Native installation on a Mac

DBConsole, MySQL Shell and the local administrator MySQL process run directly on the Mac. Files stay on the Mac, and remote databases connect through TCP/TLS or SSH. Use **OCI config** for Object Storage by setting `DBCONSOLE_OBJECT_STORAGE_AUTH_MODE=oci_config` when starting DBConsole, pointing the runtime at your OCI config location, and selecting that profile. The native setup instructions below include the required settings.

![Native macOS deployment](docs/diagrams/architecture-macos.svg)

[Native Mac setup and architecture](docs/architecture.md#native-macos) · [Diagram source](docs/diagrams/architecture-macos.drawio)

### Hosted on OCI Compute

DBConsole runs on an Oracle Linux 9 or Ubuntu Compute instance. This example places the application in a public subnet, a separate target MySQL host in a private subnet, and Object Storage outside the VCN. Use **Instance Principal** with a dynamic group and IAM policy for Object Storage. Region, CIDRs and network rules shown below are illustrative.

![OCI host deployment](docs/diagrams/architecture-oci.svg)

[OCI prerequisites](docs/prerequisites-oci.md) · [Architecture details](docs/architecture.md#oci-host) · [Diagram source](docs/diagrams/architecture-oci.drawio)

## Choose an installation

| Environment | Guide |
| --- | --- |
| macOS or Linux local Docker stack with MySQL Innovation | [Docker installation](docker/README.md) |
| Native macOS host | [Mac setup](docs/architecture.md#native-macos) |
| Oracle Linux 9 host | [OL9 installation](OL9/README.md) |
| Ubuntu host | [Ubuntu installation](ubuntu/README.md) |
| OCI Compute with Object Storage | [OCI one-step prerequisites and setup](docs/prerequisites-oci.md) |

Oracle Linux 8 is no longer supported.

## Before you begin

- Docker users need Docker Desktop. Fresh Docker setup uses `localadmin` / `ChangeMe123!` and requires an immediate password change at first sign-in.
- Linux users need Oracle Linux 9 or Ubuntu and `sudo`. Fresh host setup uses `localadmin` / `ChangeMe123!` and requires an immediate password change at first sign-in.
- Object Storage users outside OCI need an OCI config profile and private API signing key with bucket access. On OCI Compute, use Instance Principal with dynamic-group IAM permissions. See the [authentication and deployment details](docs/architecture.md).

## Daily operations

Service control, updates, and local-password guidance are in [Operations](docs/operations.md).

## Security model

Database passwords are never stored in `profiles.json`. DBConsole keeps login credentials in server-side memory. For Object Storage, use OCI config outside OCI and Instance Principal on OCI Compute. Docker selects a stored config first, with an Instance Principal fallback for OCI environments; native installations outside OCI must explicitly set `DBCONSOLE_OBJECT_STORAGE_AUTH_MODE=oci_config`. Private config and key files remain in application state and are used for Object Storage, independently of MySQL login.

## Development

Install Python dependencies for source development:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/flask --app app run --port 5001
```

Run the test suite with:

```bash
.venv/bin/python -m unittest discover -s tests
```

Release notes are maintained in [version_history.md](version_history.md).
