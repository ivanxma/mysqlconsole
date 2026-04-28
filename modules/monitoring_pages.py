def build_monitoring_dashboard_page_context(*, build_monitoring_dashboard_context):
    return build_monitoring_dashboard_context()


def build_monitoring_charts_page_context(*, build_monitoring_chart_snapshot, charts_data_url):
    return {
        "chart_snapshot": build_monitoring_chart_snapshot(),
        "charts_data_url": charts_data_url,
    }


def build_monitoring_charts_data(*, build_monitoring_chart_snapshot):
    return build_monitoring_chart_snapshot()


def build_monitoring_locks_page_context(*, build_monitoring_locks_context):
    return build_monitoring_locks_context()


def build_monitoring_report_page(
    fetcher,
    *,
    page_title,
    report_title,
    report_description,
    download_endpoint,
    fetch_kwargs=None,
    extra_context=None,
):
    error_message = ""
    report = {"columns": [], "rows": []}
    try:
        report = fetcher(**(fetch_kwargs or {}))
    except Exception as error:
        error_message = str(error)
    context = {
        "page_title": page_title,
        "report_title": report_title,
        "report_description": report_description,
        "report": report,
        "error_message": error_message,
        "download_endpoint": download_endpoint,
    }
    if extra_context:
        context.update(extra_context)
    return context


def build_monitoring_report_download(fetcher, filename, *, fetch_kwargs=None):
    report = fetcher(**(fetch_kwargs or {}))
    return {
        "filename": filename,
        "columns": report["columns"],
        "rows": report["rows"],
    }
