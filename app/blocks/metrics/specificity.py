from ._validation import threshold_metric


def run(context: dict, parameters: dict | None = None) -> dict:
    return threshold_metric(
        "specificity", context, parameters, "TN / (TN + FP)",
        lambda c: c["tn"], lambda c: c["tn"] + c["fp"],
    )
