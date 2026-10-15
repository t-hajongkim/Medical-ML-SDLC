"""Binary ROC AUC from average ranks, including tied probability scores."""

from ._validation import inputs, result, unavailable


def run(context: dict, parameters: dict | None = None) -> dict:
    block = "roc_auc"
    arrays, _, error = inputs(block, context, parameters)
    if error:
        return error
    labels, probabilities = arrays
    positives = sum(labels)
    negatives = len(labels) - positives
    details = {
        "definition": "P(score_positive > score_negative) + 0.5 * P(tie)",
        "sample_count": len(labels), "positive_count": positives,
        "negative_count": negatives, "denominator": positives * negatives,
    }
    if not positives or not negatives:
        return unavailable(block, "both_classes_required", **details)
    order = sorted(range(len(labels)), key=lambda index: probabilities[index])
    rank_sum = 0.0
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and probabilities[order[end]] == probabilities[order[start]]:
            end += 1
        average_rank = (start + 1 + end) / 2
        rank_sum += average_rank * sum(labels[index] for index in order[start:end])
        start = end
    numerator = rank_sum - positives * (positives + 1) / 2
    return result(block, numerator / (positives * negatives), details)
