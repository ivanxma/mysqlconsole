FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DBCONSOLE_RUNTIME_DIR=/run/dbconsole \
    DBCONSOLE_STATE_DIR=/var/lib/dbconsole \
    DBCONSOLE_LISTENER_SCHEME=http \
    DBCONSOLE_SESSION_COOKIE_SECURE=0

WORKDIR /opt/dbconsole

RUN groupadd --system dbconsole \
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
