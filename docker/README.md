# Docker installation

Use this path for a self-contained DBConsole environment on macOS or Linux. It starts DBConsole and a persistent companion MySQL server. The server image uses `mysql:innovation`, so each fresh pull follows the MySQL Innovation channel. DBConsole includes MySQL Shell Innovation, so Dump/Load preview and submission run inside the application container.

DBConsole and MySQL share a private Docker volume mounted at `/var/run/mysqld`. The generated `local-admin-profile` uses `/var/run/mysqld/mysqld.sock`, not TCP. This gives the profile the same socket-only local-admin behavior as a host installation while keeping the socket unavailable on the host and outside the Compose network.

The DBConsole image includes the generated `en_US.UTF-8` locale. MySQL Shell therefore runs without the missing-`LC_ALL` locale warning that can occur in minimal Debian images.

## Prerequisites

- Docker Desktop (macOS) or Docker Engine plus the Compose plugin (Linux) is installed and running.
- Git is available to clone the repository (the OL9 Docker bootstrap installs it).
- Port `8080` is free for DBConsole.
- Fresh setup uses `localadmin` / `ChangeMe123!` and saves the password only in ignored `docker/.env` with mode `0600`.

## Install or recreate

From the repository root, run:

```bash
./docker/setup_docker.sh
```

The script requires no parameters: it creates `docker/.env` with the default `ChangeMe123!` password when needed, pulls the latest MySQL Innovation image, builds DBConsole, waits for the MySQL health check through Compose, and recreates the containers while retaining the `dbconsole-state` and `dbconsole-mysql` volumes. It migrates a legacy root `.env` to `docker/.env` once.

Open [http://127.0.0.1:8080](http://127.0.0.1:8080), select `local-admin-profile`, and sign in as `localadmin` with `ChangeMe123!` (or the password in `docker/.env`). DBConsole requires an immediate password change.

MySQL has no published TCP port and starts with classic and X Protocol networking disabled. DBConsole is its only client and reaches it through the shared private socket.

## Lifecycle commands

```bash
cd docker
docker-compose ps
docker-compose logs -f dbconsole mysql
docker-compose down
```

Use `docker compose` when the Compose plugin is available (the normal Docker Desktop and current Docker Engine form); `docker-compose` remains supported for older Linux installations.

## Persistent volumes and image refresh

The DBConsole image is replaceable; `./docker/setup_docker.sh` may rebuild it and recreate both containers without removing Docker volumes. The following named volumes persist on the Docker host across image and container refreshes:

- `dbconsole-state`: DBConsole profiles, settings, Object Storage targets, uploaded OCI config/key, and application state.
- `dbconsole-mysql`: MySQL database files.
- `dbconsole-mysql-socket`: the private Unix socket path shared by DBConsole and MySQL; it contains no durable database data.

`docker-compose down` stops containers but keeps all three volumes. Do not run `docker-compose down --volumes` unless you intend to permanently remove DBConsole state and MySQL data.

## Update

The in-application **Admin > Auto-Update** control is intentionally unavailable in Docker because a container cannot safely rebuild or replace its own image. From the repository checkout on the Docker host, run the same idempotent setup command:

```bash
./docker/setup_docker.sh
```

It pulls the MySQL Innovation image, rebuilds DBConsole, and recreates the stack without removing its named volumes.

## Object Storage authentication

For Object Storage only, Docker uses the selected OCI config profile first. OCI config remains optional: if the selected profile is not stored, DBConsole attempts OCI Compute Instance Principal instead. In **Admin > Setup Object Storage > OCI Config**, enter the standard config fields (`user`, `fingerprint`, `tenancy`, `region`, and optional `compartment`) and upload its private key. The application writes a complete OCI config file for that named profile, including its private `key_file` path. Both files are stored mode `0600` under the private `dbconsole-state` Docker volume and are never rendered or included in the image or source repository.

Set **OCI Config Profile** to the named profile used for that Object Storage target. Each uploaded OCI config is stored separately with its own private key; switching the Object Storage profile switches to its selected OCI config. The OCI Config tab lists saved profiles and safely reloads their non-secret fields; the private key is never shown and is retained unless explicitly replaced. The credentials are used only for Object Storage calls: folder browsing, file upload, PAR setup, MySQL Shell dump/load, and Lakehouse workflows.

## MySQL Shell CA-bundle compatibility

The MySQL Shell Innovation generic Linux archive uses the Oracle Linux-style trust-anchor path `/etc/pki/tls/certs/ca-bundle.crt`. DBConsole's Docker image is Debian-based, where the system CA bundle is `/etc/ssl/certs/ca-certificates.crt`. Without the compatibility path, an Object Storage Dump/Load job can fail before contacting OCI with:

```text
error adding trust anchors from file: /etc/pki/tls/certs/ca-bundle.crt (CURLcode = 77)
```

This is a MySQL Shell generic-Linux packaging/path compatibility issue, not an OCI Config, PAR, bucket, or IAM failure. The DBConsole image creates `/etc/pki/tls/certs/ca-bundle.crt` as a symlink to Debian's managed CA bundle during its build. Verify it after an update with:

```bash
cd docker
docker-compose exec dbconsole readlink /etc/pki/tls/certs/ca-bundle.crt
```

It should print `/etc/ssl/certs/ca-certificates.crt`. If an older image reports the error, rebuild it with `./docker/setup_docker.sh` and resubmit the job; the failed job record is retained as history.
