"""Conservative offline report over every imported text section."""
from collections import Counter
import re


DATE = re.compile(r"(?<!\d)(20\d{2})[-/.年](0?[1-9]|1[0-2])(?:[-/.月]\d{1,2})?")
TOPICS = {
    "预算与价格": re.compile(r"预算|报价|价格|折扣|优惠|成本"),
    "库存与交期": re.compile(r"库存|现货|发货|交期|送达|到货|交付"),
    "产品需求": re.compile(r"需求|规格|型号|设备|功能|尺寸"),
    "售后与风险": re.compile(r"售后|保修|退货|退款|故障|投诉"),
}


def build_local_report(store, person_id):
    """Count all rows; report literal evidence without invented traits or sentiment."""
    totals = store.stats(person_id)
    if not totals["section_count"]:
        return "尚未导入文字记录。"
    months = Counter()
    topics = Counter()
    speakers = Counter()
    evidence = []
    undated = 0
    processed = 0
    offset = 0
    while True:
        page = store.list_messages(person_id, offset=offset, limit=500)
        if not page:
            break
        for row in page:
            text = row["text"]
            processed += 1
            dates = DATE.findall(text)
            if dates:
                months.update(f"{year}-{int(month):02d}" for year, month in dates)
            else:
                undated += 1
            if re.search(r"(^|\s)(我|自己|本人)\s*[：:]", text):
                speakers["我"] += 1
            elif re.search(r"(^|\s)(客户|对方|买家)\s*[：:]", text):
                speakers["对方"] += 1
            else:
                speakers["未识别"] += 1
            found = [name for name, pattern in TOPICS.items() if pattern.search(text)]
            topics.update(found)
            if found and len(evidence) < 30:
                evidence.append(f"- {row['source_name']} / {row['label']} [{', '.join(found)}]：{text[:180]}")
        offset += len(page)
    result = ["本地历史整理报告", "",
              f"范围：{totals['source_count']} 个导入文件，{processed} 个文字片段，约 {totals['char_count']} 字。",
              "该报告覆盖全部已导入文字片段；缺失记录与扫描 PDF 不在范围内。", "",
              "客户画像线索（仅列明确文字证据，不推断性格或真实意图）"]
    result.extend(evidence or ["未发现预算、交期、产品或售后等明确线索。"])
    result.extend(["", "关系与互动趋势（仅按可识别的时间和文本数量）"])
    result.extend(f"- {month}：{count} 个带日期片段" for month, count in sorted(months.items()))
    result.append(f"- {undated} 个片段未识别到日期；不据此推断关系好坏。")
    result.extend(["", "聊天统计（文本片段，不等于微信原始消息数）",
                   f"- 发送人线索：我 {speakers['我']}，对方 {speakers['对方']}，未识别 {speakers['未识别']}。"])
    result.extend(f"- {topic}：{topics[topic]} 个相关片段" for topic in TOPICS)
    return "\n".join(result)
