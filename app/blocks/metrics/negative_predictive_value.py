from ._validation import threshold_metric


def run(context: dict, parameters: dict | None = None) -> dict:
    return threshold_metric(
        "negative_predictive_value", context, parameters, "NPV = TN / (TN + FN)",
        lambda c: c["tn"], lambda c: c["tn"] + c["fn"],
    )
