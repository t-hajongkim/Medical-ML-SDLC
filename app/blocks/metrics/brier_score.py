"""Mean squared binary probability error."""

import math

from ._validation import inputs, result


def run(context: dict, parameters: dict | None = None) -> dict:
    block = "brier_score"
    arrays, _, error = inputs(block, context, parameters)
    if error:
        return error
    labels, probabilities = arrays
    value = math.fsum((score - label) ** 2 for label, score in zip(labels, probabilities)) / len(labels)
    return result(block, value, {
        "definition": "mean((y_probability - y_true) ** 2)",
        "sample_count": len(labels), "denominator": len(labels),
    })
