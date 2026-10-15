from ._validation import threshold_metric


def run(context: dict, parameters: dict | None = None) -> dict:
    return threshold_metric(
        "recall", context, parameters, "sensitivity = TP / (TP + FN)",
        lambda c: c["tp"], lambda c: c["tp"] + c["fn"],
    )
