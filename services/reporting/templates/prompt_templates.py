"""Prompt engineering and formatting templates for Claude API price reporting."""

SYSTEM_PROMPT = """Bạn là trợ lý AI chuyên gia phân tích thị trường giá bán lẻ (MarketData Intelligence Assistant).
Nhiệm vụ của bạn là xem xét dữ liệu giá đã được tổng hợp trong 24 giờ qua và viết bản tóm lược phân tích điều hành ngắn gọn, súc tích cho doanh nghiệp.

Nguyên tắc bắt buộc:
1. AI chỉ hỗ trợ xem xét và đưa ra nhận định, TUYỆT ĐỐI KHÔNG tự động thay đổi giá bán.
2. Mọi nhận định phải có số liệu dẫn chứng cụ thể từ dữ liệu được cung cấp (tên SKU/sản phẩm, nguồn, giá, tỷ lệ % chênh lệch).
3. Chỉ dùng dữ liệu được cung cấp. Không suy đoán xu hướng theo thời gian, đối thủ, giá nội bộ, biên lợi nhuận hay thị phần nếu dữ liệu không có; khi thiếu, hãy nói rõ là không đủ dữ liệu.
4. Luôn công bố rõ ràng các nguồn dữ liệu bị thiếu hoặc dữ liệu cũ/quá hạn (nếu có).
5. Giữ giọng văn khách quan, chuyên nghiệp.
6. Giới hạn độ dài tối đa 3-4 đoạn văn (khoảng 300-400 từ), sử dụng tiếng Việt chuẩn.
7. Trình bày bằng Markdown đơn giản: đoạn văn, danh sách gạch đầu dòng, chữ đậm. Không dùng bảng, khối mã hay dấu ` quanh tên sản phẩm và nguồn.
"""


def price_lines(prices: list[dict]) -> list[str]:
    """One line per product, with the spread computed here rather than left to the model."""
    by_product = {}
    for row in prices:
        by_product.setdefault(row["product_id"], []).append(row)
    lines = []
    for product_id, rows in by_product.items():
        quotes = ", ".join(f"{r['source_id']} {r['price']:,} VND" for r in rows)
        values = [r["price"] for r in rows]
        spread = ""
        if len(values) > 1 and min(values) > 0:
            spread = f"; chênh lệch cao nhất so với thấp nhất: {(max(values) - min(values)) / min(values) * 100:.2f} %"
        lines.append(f"- Sản phẩm {product_id}: {quotes}{spread}")
    return lines


def build_claude_prompt(summary_data: dict) -> str:
    """Format structured numeric matrix and source metrics into the report prompt."""
    tenant_id = summary_data.get("tenant_id", "Unknown")
    period = summary_data.get("period", "Unknown")
    window_start = summary_data.get("window_start", "")
    window_end = summary_data.get("window_end", "")
    stats = summary_data.get("stats", {})
    anomalies = summary_data.get("anomalies", [])
    stale_sources = summary_data.get("stale_sources", [])
    top_changes = summary_data.get("top_changes", [])
    prices = summary_data.get("prices", [])
    active_sources = sorted({row["source_id"] for row in prices})
    reference = stats.get("reference_source")
    no_reference = "Không có; chỉ so sánh giá giữa các nguồn, không kết luận so với giá tham chiếu hay giá nội bộ"

    prompt = f"""Dữ liệu quan sát giá ngày {period} (Cửa sổ 24 giờ: {window_start} đến {window_end}) cho Tenant: {tenant_id}

1. Thống kê tổng quan:
- Tổng số sản phẩm theo dõi: {stats.get('total_products', 0)}
- Tổng số quan sát giá hợp lệ: {stats.get('total_observations', 0)}
- Nguồn có dữ liệu: {', '.join(active_sources) if active_sources else 'Không có'}
- Nguồn tham chiếu: {reference or no_reference}

2. Cảnh báo dữ liệu nguồn:
- Nguồn bị thiếu hoặc dữ liệu cũ quá 24h: {', '.join(stale_sources) if stale_sources else 'Không có (đủ nguồn)'}

3. Giá mới nhất theo từng nguồn (không có dữ liệu lịch sử để kết luận về xu hướng):
"""
    lines = price_lines(prices)
    prompt += "\n".join(lines) + "\n" if lines else "- Không có quan sát giá hợp lệ trong cửa sổ này.\n"

    if top_changes:
        prompt += "\n4. Chênh lệch so với giá tham chiếu:\n"
        for item in top_changes:
            prompt += (
                f"- Sản phẩm {item['product_id']}: nguồn {item['source_id']} có giá {item['price']:,} VND "
                f"(Chênh lệch: {item['change_percent']:+} %)\n"
            )

    if anomalies:
        prompt += "\n5. Các trường hợp bất thường cần lưu ý (chênh lệch > 15%):\n"
        for a in anomalies:
            prompt += (
                f"- Cảnh báo bất thường ở SKU {a['product_id']} từ nguồn {a['source_id']}: "
                f"chênh lệch {a['change_percent']}% so với nguồn tham chiếu.\n"
            )

    prompt += "\nHãy phân tích và đưa ra 3 khuyến nghị theo dõi giá cho ngày hôm nay, chỉ dựa trên dữ liệu trên."
    return prompt
