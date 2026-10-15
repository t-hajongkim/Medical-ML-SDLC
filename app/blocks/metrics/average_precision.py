"""Average precision (AP), not trapezoidal precision-recall area."""

import math

from ._validation import inputs, result, unavailable


def run(context: dict, parameters: dict | None = None) -> dict:
    block = "average_precision"
    arrays, _, error = inputs(block, context, parameters)
    if error:
        return error
    labels, probabilities = arrays
    positives = sum(labels)
    details = {
        "definition": "AP = sum((recall_i - recall_previous) * precision_i) at distinct scores",
        "not_trapezoidal_pr_area": True, "sample_count": len(labels),
        "positive_count": positives, "denominator": positives,
    }
    if positives == 0:
        return unavailable(block, "positive_class_required", **details)
    order = sorted(range(len(labels)), key=lambda index: -probabilities[index])
    terms = []
    true_positives = 0
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and probabilities[order[end]] == probabilities[order[start]]:
            end += 1
        new_positives = sum(labels[index] for index in order[start:end])
        true_positives += new_positives
        terms.append((new_positives / positives) * (true_positives / end))
        start = end
    return result(block, math.fsum(terms), details)
