# Docker installation

Use this path for a self-contained DBConsole environment on macOS or Linux. It starts DBConsole and a persistent companion MySQL server. The server image uses `mysql:innovation`, so each fresh pull follows the MySQL Innovation channel.

DBConsole and MySQL share a private Docker volume mounted at `/var/run/mysqld`. The generated `local-admin-profile` uses `/var/run/mysqld/mysqld.sock`, not TCP. This gives the profile the same socket-only local-admin behavior as a host installation while keeping the socket unavailable on the host and outside the Compose network.

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

`down` stops containers but preserves both named volumes. Do not run `docker-compose down --volumes` unless you intend to permanently remove the local DBConsole state and MySQL data.

## Update

The in-application **Admin > Auto-Update** control is intentionally unavailable in Docker because a container cannot safely rebuild or replace its own image. From the repository checkout on the Docker host, run the same idempotent setup command:

```bash
./docker/setup_docker.sh
```

It pulls the MySQL Innovation image, rebuilds DBConsole, and recreates the stack without removing its named volumes.

## Object Storage authentication

For Object Storage only, Docker first uses OCI Compute Instance Principal when it is available (for example, when Docker runs on OCI Compute). If metadata authentication is unavailable, it falls back to a read-only OCI config directory mounted at `/home/dbconsole/.oci`.

Create a container-compatible config directory on the Docker host, or use the ignored default `docker/oci/`. The config's `key_file` must use the container path `/home/dbconsole/.oci/<key-file-name>`:

```ini
[DEFAULT]
user=ocid1.user.oc1..example
fingerprint=aa:bb:cc:dd
tenancy=ocid1.tenancy.oc1..example
region=uk-london-1
key_file=/home/dbconsole/.oci/oci_api_key.pem
```

Set `OCI_CONFIG_DIR` in ignored `docker/.env` to use another directory, and optionally set `OCI_CONFIG_PROFILE`. Keep the directory and key private on the Docker host; the Compose mount is read-only. The credentials are used only for Object Storage calls: folder browsing, file upload, PAR setup, MySQL Shell dump/load, and Lakehouse workflows.
