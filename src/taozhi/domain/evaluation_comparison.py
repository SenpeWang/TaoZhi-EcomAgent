"""按用例编号、定义摘要和分组归类两个评测批次的变化。"""


def pair_case_results(records: list[dict], baseline_rows: list[dict]) -> dict:
    """双方使用相同模式和裁判时，按可判定的同版本样本配对。"""
    previous = {row["case_id"]: row for row in baseline_rows}
    changes = {group: {"stable_passed": [], "stable_failed": [], "fixed": [], "regressed": []}
               for group in ("standard", "challenge")}
    excluded = {"new": [], "baseline_only": [], "definition_changed": [], "unassessable": []}
    for row in records:
        case_id = row["case_id"]
        other = previous.pop(case_id, None)
        if other is None:
            excluded["new"].append(case_id)
        elif other["case_sha256"] != row["case_sha256"]:
            excluded["definition_changed"].append(case_id)
        elif "unassessable" in (other["status"], row["status"]):
            excluded["unassessable"].append(case_id)
        else:
            if row["status"] == other["status"]:
                change = "stable_" + row["status"]
            else:
                change = "fixed" if row["status"] == "passed" else "regressed"
            group = "challenge" if row["suite"] == "challenge" else "standard"
            changes[group][change].append(case_id)
    excluded["baseline_only"] = sorted(previous)
    count = {group: sum(len(ids) for ids in results.values()) for group, results in changes.items()}
    comparable = count["standard"] > 0 if any(row["suite"] != "challenge" for row in records) else count["challenge"] > 0
    return {
        "paired_counts": count, "changes": changes,
        "excluded_count": sum(len(ids) for ids in excluded.values()), "excluded": excluded,
        "comparable": comparable, "passed": comparable and not changes["standard"]["regressed"],
    }
