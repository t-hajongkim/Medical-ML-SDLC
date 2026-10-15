from ._validation import top_metric


def run(context: dict, parameters: dict | None = None) -> dict:
    return top_metric("precision_at_k", context, parameters, recall=False)
