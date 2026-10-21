"""Prompt engineering and formatting templates for Claude API price reporting."""

SYSTEM_PROMPT = """Bạn là trợ lý AI chuyên gia phân tích thị trường giá bán lẻ (MarketData Intelligence Assistant).
Nhiệm vụ của bạn là xem xét dữ liệu giá đã được tổng hợp trong 24 giờ qua và viết bản tóm lược phân tích điều hành ngắn gọn, súc tích cho doanh nghiệp.

Nguyên tắc bắt buộc:
1. AI chỉ hỗ trợ xem xét và đưa ra nhận định, TUYỆT ĐỐI KHÔNG tự động thay đổi giá bán.
2. Mọi nhận định phải có số liệu dẫn chứng cụ thể từ dữ liệu được cung cấp (tên SKU/sản phẩm, giá gốc, giá đối thủ, tỷ lệ % chênh lệch).
3. Luôn công bố rõ ràng các nguồn dữ liệu bị thiếu hoặc dữ liệu cũ/quá hạn (nếu có).
4. Giữ giọng văn khách quan, chuyên nghiệp, cảnh báo rủi ro về biên lợi nhuận hoặc mất thị phần.
5. Giới hạn độ dài tối đa 3-4 đoạn văn (khoảng 300-400 từ), sử dụng tiếng Việt chuẩn.
"""


def build_claude_prompt(summary_data: dict) -> str:
    """Format structured numeric matrix and source metrics into Claude prompt."""
    tenant_id = summary_data.get("tenant_id", "Unknown")
    period = summary_data.get("period", "Unknown")
    window_start = summary_data.get("window_start", "")
    window_end = summary_data.get("window_end", "")
    stats = summary_data.get("stats", {})
    anomalies = summary_data.get("anomalies", [])
    stale_sources = summary_data.get("stale_sources", [])
    top_changes = summary_data.get("top_changes", [])

    prompt = f"""Dữ liệu quan sát giá ngày {period} (Cửa sổ 24 giờ: {window_start} đến {window_end}) cho Tenant: {tenant_id}

1. Thống kê tổng quan:
- Tổng số sản phẩm theo dõi: {stats.get('total_products', 0)}
- Tổng số quan sát giá hợp lệ: {stats.get('total_observations', 0)}
- Nguồn tham chiếu: {stats.get('reference_source', 'internal')}

2. Cảnh báo dữ liệu nguồn:
- Nguồn bị thiếu hoặc dữ liệu cũ quá 24h: {', '.join(stale_sources) if stale_sources else 'Không có (đủ nguồn)'}

3. Các biến động giá đáng chú ý so với giá tham chiếu:
"""
    if top_changes:
        for item in top_changes:
            prompt += (
                f"- Sản phẩm {item['product_id']}: nguồn {item['source_id']} có giá {item['price']:,} VND "
                f"(Chênh lệch: {item['change_percent']:+} %)\n"
            )
    else:
        prompt += "- Giá thị trường ổn định, không có chênh lệch lớn giữa các nguồn.\n"

    if anomalies:
        prompt += "\n4. Các trường hợp bất thường cần lưu ý (chênh lệch > 15%):\n"
        for a in anomalies:
            prompt += (
                f"- Cảnh báo bất thường ở SKU {a['product_id']} từ nguồn {a['source_id']}: "
                f"chênh lệch {a['change_percent']}% so với chuẩn nội bộ.\n"
            )

    prompt += "\nHãy phân tích và đưa ra 3 khuyến nghị theo dõi giá cho ngày hôm nay."
    return prompt
