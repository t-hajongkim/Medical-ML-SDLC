from ._validation import threshold_metric


def run(context: dict, parameters: dict | None = None) -> dict:
    return threshold_metric(
        "precision", context, parameters, "positive predictive value (PPV) = TP / (TP + FP)",
        lambda c: c["tp"], lambda c: c["tp"] + c["fp"],
    )
