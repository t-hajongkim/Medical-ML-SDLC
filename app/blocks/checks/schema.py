def run(context: dict) -> dict:
    actual = context.get("feature_columns")
    expected = context.get("expected_feature_columns")
    if not isinstance(actual, list) or not isinstance(expected, list) or not actual or not expected:
        return {"block": "schema", "status": "unverifiable", "details": {"reason": "Both feature schemas are required."}}
    valid = actual == expected and len(actual) == len(set(actual))
    return {"block": "schema", "status": "passed" if valid else "failed", "details": {"actual_count": len(actual), "expected_count": len(expected)}}
