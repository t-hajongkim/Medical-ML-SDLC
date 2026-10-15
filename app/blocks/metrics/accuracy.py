from ._validation import threshold_metric


def run(context: dict, parameters: dict | None = None) -> dict:
    return threshold_metric(
        "accuracy", context, parameters, "(TP + TN) / (TP + TN + FP + FN)",
        lambda c: c["tp"] + c["tn"], lambda c: sum(c.values()),
    )
