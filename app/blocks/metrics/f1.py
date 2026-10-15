from ._validation import threshold_metric


def run(context: dict, parameters: dict | None = None) -> dict:
    return threshold_metric(
        "f1", context, parameters, "2 * TP / (2 * TP + FP + FN)",
        lambda c: 2 * c["tp"], lambda c: 2 * c["tp"] + c["fp"] + c["fn"],
    )
