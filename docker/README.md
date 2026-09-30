# Docker installation

Use this path for a self-contained DBConsole environment on macOS or Linux. It starts DBConsole and a persistent companion MySQL server. The server image uses `mysql:innovation`, so each fresh pull follows the MySQL Innovation channel.

DBConsole and MySQL share a private Docker volume mounted at `/var/run/mysqld`. The generated `local-admin-profile` uses `/var/run/mysqld/mysqld.sock`, not TCP. This gives the profile the same socket-only local-admin behavior as a host installation while keeping the socket unavailable on the Mac host and outside the Compose network.

## Prerequisites

- Docker Desktop (macOS) or Docker Engine plus the Compose plugin (Linux) is installed and running.
- Git is available to clone the repository (the OL9 Docker bootstrap installs it).
- Port `8080` is free for DBConsole.
- You can choose a local MySQL root password. It is saved only in the ignored `.env` file with mode `0600`.

## Install or recreate

From the repository root, run:

```bash
./docker/setup_docker.sh
```

The script prompts for the password only when `docker/.env` does not already exist, pulls the latest MySQL Innovation image, builds DBConsole, waits for the MySQL health check through Compose, and recreates the containers while retaining the `dbconsole-state` and `dbconsole-mysql` volumes. It migrates a legacy root `.env` to `docker/.env` once.

Open [http://127.0.0.1:8080](http://127.0.0.1:8080), select `local-admin-profile`, and sign in as `root` using the password in `docker/.env`.

MySQL has no published TCP port and starts with classic and X Protocol networking disabled. DBConsole is its only client and reaches it through the shared private socket.

## Lifecycle commands

```bash
cd docker
docker-compose ps
docker-compose logs -f dbconsole mysql
docker-compose down
```

Docker Desktop installations that provide the plugin form can use `docker compose` in place of `docker-compose`.

`down` stops containers but preserves both named volumes. Do not run `docker-compose down --volumes` unless you intend to permanently remove the local DBConsole state and MySQL data.

## Object Storage limitation

The application uses OCI Compute Instance Principal authentication only. A Docker container on a Mac is not an OCI Compute instance and therefore cannot configure or use OCI Object Storage credentials. Use the OCI Compute installation when Object Storage features are required.
