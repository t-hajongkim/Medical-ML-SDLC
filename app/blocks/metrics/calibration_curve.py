"""Uniform-width calibration groups; empty groups contain null means, not zero."""

import math
from numbers import Integral

from ._validation import inputs, result


def run(context: dict, parameters: dict | None = None) -> dict:
    block = "calibration_curve"
    arrays, parameters, error = inputs(block, context, parameters)
    if error:
        return error
    n_bins = parameters.get("n_bins", 10)
    if isinstance(n_bins, bool) or not isinstance(n_bins, Integral) or n_bins < 1:
        raise ValueError("n_bins must be a positive integer")
    n_bins = int(n_bins)
    labels, probabilities = arrays
    groups = [[] for _ in range(n_bins)]
    for label, score in zip(labels, probabilities):
        groups[min(int(score * n_bins), n_bins - 1)].append((label, score))
    bins = []
    for index, group in enumerate(groups):
        count = len(group)
        bins.append({
            "bin": index, "lower": index / n_bins, "upper": (index + 1) / n_bins,
            "upper_inclusive": index == n_bins - 1, "count": count,
            "mean_predicted_probability": math.fsum(score for _, score in group) / count if count else None,
            "observed_event_rate": sum(label for label, _ in group) / count if count else None,
        })
    return result(block, bins, {
        "definition": "per-bin mean predicted probability, observed positive fraction, and count",
        "strategy": "uniform", "n_bins": n_bins, "sample_count": len(labels),
        "bin_rule": "[lower, upper), except final bin includes 1",
        "denominator": "count in each bin; empty bin means are null",
    })
