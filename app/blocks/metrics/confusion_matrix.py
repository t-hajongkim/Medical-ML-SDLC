from ._validation import threshold_metric


def run(context: dict, parameters: dict | None = None) -> dict:
    return threshold_metric("confusion_matrix", context, parameters, "counts of true/predicted binary labels")
