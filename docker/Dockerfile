FROM python:3.12-slim

ARG MYSQL_SHELL_VERSION=26.7.1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DBCONSOLE_RUNTIME_DIR=/run/dbconsole \
    DBCONSOLE_STATE_DIR=/var/lib/dbconsole \
    DBCONSOLE_LISTENER_SCHEME=http \
    DBCONSOLE_SESSION_COOKIE_SECURE=0

WORKDIR /opt/dbconsole

RUN set -eux; \
    case "$(dpkg --print-architecture)" in \
      amd64) mysqlsh_arch=x86-64bit ;; \
      arm64) mysqlsh_arch=arm-64bit ;; \
      *) echo "Unsupported MySQL Shell architecture: $(dpkg --print-architecture)" >&2; exit 1 ;; \
    esac; \
    apt-get update; \
    apt-get install --yes --no-install-recommends ca-certificates curl; \
    curl --fail --location --silent --show-error \
      "https://dev.mysql.com/get/Downloads/MySQL-Shell/mysql-shell-${MYSQL_SHELL_VERSION}-linux-glibc2.28-${mysqlsh_arch}.tar.gz" \
      | tar --extract --gzip --directory /opt; \
    mysqlsh_dir="$(find /opt -maxdepth 1 -type d -name 'mysql-shell-*' -print -quit)"; \
    test -n "$mysqlsh_dir"; \
    ln -s "$mysqlsh_dir/bin/mysqlsh" /usr/local/bin/mysqlsh; \
    mysqlsh --version; \
    rm -rf /var/lib/apt/lists/*; \
    groupadd --system dbconsole \
    && useradd --system --gid dbconsole --create-home --home-dir /home/dbconsole dbconsole \
    && mkdir -p /run/dbconsole /var/lib/dbconsole \
    && chown -R dbconsole:dbconsole /run/dbconsole /var/lib/dbconsole

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=dbconsole:dbconsole . ./
RUN chmod 0755 /opt/dbconsole/docker/docker-entrypoint.sh

USER dbconsole

EXPOSE 8080

ENTRYPOINT ["/opt/dbconsole/docker/docker-entrypoint.sh"]
CMD ["gunicorn", "--workers", "1", "--threads", "8", "--timeout", "120", "--graceful-timeout", "30", "--bind", "0.0.0.0:8080", "--access-logfile", "-", "--error-logfile", "-", "wsgi:application"]
