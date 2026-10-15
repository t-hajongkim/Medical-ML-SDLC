from collections import Counter


def run(context: dict) -> dict:
    records = context.get("metadata", [])
    columns = ("race", "gender", "age_group")
    if not records or any(any(column not in row for column in columns) for row in records):
        return {"block": "demographics", "status": "unverifiable", "details": {"reason": "Race, gender and age-group metadata are required."}}
    distributions = {}
    for split in ("train", "valid", "test"):
        subset = [row for row in records if row.get("split") == split]
        distributions[split] = {
            column: dict(Counter(row[column] or "MISSING" for row in subset))
            for column in columns
        }
    return {"block": "demographics", "status": "completed", "details": {"distributions": distributions}}
