"""Entry point executed inside mysqlsh with --pym."""
import json
import os
import sys

from modules.mysqlsh_runner import ALLOWED_FUNCTIONS, RESULT_END, RESULT_START


def _emit(payload):
    print(RESULT_START)
    print(json.dumps(payload, default=str))
    print(RESULT_END)


def main():
    if len(sys.argv) != 2:
        raise ValueError("MySQL Shell request file is required.")
    request_path = sys.argv[1]
    try:
        with open(request_path, encoding="utf-8") as handle:
            request = json.load(handle)
    finally:
        try:
            os.unlink(request_path)
        except OSError:
            pass
    function_name = request.get("function_name")
    if function_name not in ALLOWED_FUNCTIONS:
        raise ValueError("MySQL Shell function is not allowed.")
    session = None
    try:
        session = shell.connect(request["connection_options"])  # noqa: F821 - injected by mysqlsh
        shell.options.useWizards = False  # noqa: F821 - injected by mysqlsh
        active_session = shell.get_session()  # noqa: F821 - injected by mysqlsh
        if active_session is None or not active_session.is_open():
            raise RuntimeError("MySQL Shell did not establish an active session.")
        function = {  # noqa: F821 - util is injected by mysqlsh
            "dump_instance": util.dump_instance,  # noqa: F821
            "dump_schemas": util.dump_schemas,  # noqa: F821
            "load_dump": util.load_dump,  # noqa: F821
        }[function_name]
        result = function(*request.get("args", []), **request.get("kwargs", {}))
        _emit({"status": "ok", "result": result})
    except Exception as error:
        _emit({"status": "error", "error": str(error), "error_type": type(error).__name__})
        raise
    finally:
        try:
            if session is not None and session.is_open():
                session.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
