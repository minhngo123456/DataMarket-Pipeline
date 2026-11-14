"""Safe, stable collection error codes; never expose source URLs or credentials."""

import re

ERROR_CODES = {
    "api_response_exceeds_1_mib", "invalid_json_payload", "api_payload_missing_items_list",
    "api_payload_must_be_list_or_dict", "empty_api_response", "api_exceeds_1000_rows",
    "invalid_item_format", "missing_required_api_fields", "tenant_not_allowed_in_record",
    "empty_required_api_field", "missing_api_endpoint", "row_exceeds_32_kib",
    "html_response_exceeds_1_mib", "invalid_html_encoding", "invalid_html_syntax",
    "empty_or_unparseable_html_table", "html_exceeds_1000_rows", "missing_required_html_fields",
    "invalid_html_observed_at", "invalid_html_price", "missing_web_url", "ambiguous_html_product",
    "source_snapshot_invalid_row_count", "source_destination_blocked", "source_url_scheme_not_allowed",
    "csv_exceeds_1_mib", "invalid_csv_headers", "invalid_csv_columns", "csv_exceeds_1000_rows",
    "empty_csv", "invalid_utf8_csv", "source_disabled_or_missing",
    "source_generation_changed", "source_https_downgrade_blocked",
}


def collection_error_code(error, kind):
    code = str(error)
    prefix = "retry_limit_exceeded_"
    if code.startswith(prefix):
        # Strip at most one prefix, so arbitrary exception text cannot recurse.
        reason = code[len(prefix):]
        if not reason.startswith(prefix):
            return prefix + collection_error_code(reason, kind)
        return kind + "_worker_error"
    if code in ERROR_CODES or re.fullmatch(
        rf"{kind}_(http_retryable_(429|408|5\d\d)|access_denied_40[13]|http_error_4\d\d|worker_error|invalid_job_message)",
        code,
    ):
        return code
    if code.startswith(kind + "_connection_error"):
        return kind + "_connection_error"
    return kind + "_worker_error"
