"""Production WSGI entry point used by the DB Console launchers."""
from app import app, ensure_object_storage_store, ensure_profile_store


ensure_profile_store()
ensure_object_storage_store()
application = app
