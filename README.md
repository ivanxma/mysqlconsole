# MySQL DBConsole

DBConsole is a Flask web console for MySQL administration, SQL workspaces, monitoring, MySQL Shell dump/load operations, HeatWave workflows, and OCI Object Storage integration.

Current version: `1.1.18`

## Choose an installation

| Environment | Guide |
| --- | --- |
| macOS or Linux local Docker stack with MySQL Innovation | [Docker installation](docker/README.md) |
| Oracle Linux 9 host | [OL9 installation](OL9/README.md) |
| Ubuntu host | [Ubuntu installation](ubuntu/README.md) |
| OCI Compute with Object Storage | [OCI one-step prerequisites and setup](docs/prerequisites-oci.md) |

Oracle Linux 8 is no longer supported.

## Before you begin

- Docker users need Docker Desktop. Fresh Docker setup uses `localadmin` / `ChangeMe123!` and requires an immediate password change at first sign-in.
- Linux users need Oracle Linux 9 or Ubuntu and `sudo`. Fresh host setup uses `localadmin` / `ChangeMe123!` and requires an immediate password change at first sign-in.
- OCI Object Storage users need a dedicated test bucket plus Compute Instance Principal IAM permissions. See the [specific bucket and IAM requirements](docs/prerequisites-oci.md).

## Daily operations

Service control, updates, and local-password guidance are in [Operations](docs/operations.md).

## Security model

Database passwords are never stored in `profiles.json`. DBConsole uses server-side session state. Host and OCI Compute deployments use Instance Principal for Object Storage. Docker uses the selected private OCI config profile first, then attempts Instance Principal only when no usable stored config is available; Docker credentials remain in the DBConsole state volume and are used for Object Storage only.

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
