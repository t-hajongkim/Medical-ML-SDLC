"""Shared validation and arithmetic for evaluation-only blocks.

No model objects, training data, or EDA context are consumed. Missing prerequisites
and mathematically undefined results are not applicable; malformed inputs raise
ValueError. All returned values use finite, JSON-native types.
"""

from collections.abc import Mapping
import math
from numbers import Integral, Real


def result(block, value, details):
    return {"block": block, "status": "completed", "value": value, "details": details}


def unavailable(block, reason, **details):
    return {
        "block": block, "status": "not_applicable", "value": None,
        "details": {"reason": reason, **details},
    }


def arguments(context, parameters):
    if not isinstance(context, dict):
        raise ValueError("context must be a dict")
    if parameters is None:
        parameters = {}
    if not isinstance(parameters, dict):
        raise ValueError("parameters must be a dict or None")
    return parameters


def sequence(value, name):
    if isinstance(value, (str, bytes, Mapping, set, frozenset)):
        raise ValueError(f"{name} must be an ordered one-dimensional sequence")
    try:
        return list(value)
    except TypeError as error:
        raise ValueError(f"{name} must be an ordered one-dimensional sequence") from error


def number(value, name):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite real number")
    try:
        converted = float(value)
    except (OverflowError, ValueError) as error:
        raise ValueError(f"{name} must be a finite real number") from error
    if not math.isfinite(converted):
        raise ValueError(f"{name} must be finite (no NaN or Infinity)")
    return converted


def probability(value, name):
    value = number(value, name)
    if not 0 <= value <= 1:
        raise ValueError(f"{name} must be between 0 and 1 inclusive")
    return value


def inputs(block, context, parameters=None, *, reference=False):
    parameters = arguments(context, parameters)
    keys = ["y_true", "y_probability"]
    if reference:
        keys.append("reference_probabilities")
    missing = [key for key in keys if key not in context or context[key] is None]
    if missing:
        return None, parameters, unavailable(block, "missing_inputs", missing_inputs=missing)
    y_true = sequence(context["y_true"], "y_true")
    labels = []
    for value in y_true:
        if isinstance(value, bool):
            labels.append(int(value))
        else:
            label = number(value, "y_true")
            if label not in (0, 1):
                raise ValueError("y_true must contain only binary 0/1 labels")
            labels.append(int(label))
    arrays = [labels]
    for key in keys[1:]:
        values = sequence(context[key], key)
        arrays.append([probability(value, key) for value in values])
    if any(len(array) != len(labels) for array in arrays[1:]):
        raise ValueError("y_true and probability arrays must have equal row counts")
    if not labels:
        return None, parameters, unavailable(block, "empty_input", sample_count=0)
    return arrays, parameters, None


def required_parameter(block, parameters, name):
    if name not in parameters or parameters[name] is None:
        return unavailable(block, "missing_parameters", missing_parameters=[name])
    return None


def text_context(block, context, name):
    value = context.get(name)
    if value is None or (isinstance(value, str) and not value.strip()):
        return unavailable(block, "missing_context", missing_context=[name])
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a nonempty description string")
    return None


def cutoffs(value, name):
    values = [number(item, name) for item in sequence(value, name)]
    if not values:
        raise ValueError(f"{name} must contain at least one threshold")
    if any(not 0 < item < 1 for item in values):
        raise ValueError(f"{name} must satisfy 0 < threshold < 1")
    if any(left >= right for left, right in zip(values, values[1:])):
        raise ValueError(f"{name} must be strictly increasing without duplicates")
    return values


def counts(labels, probabilities, threshold):
    tp = sum(label == 1 and score >= threshold for label, score in zip(labels, probabilities))
    fp = sum(label == 0 and score >= threshold for label, score in zip(labels, probabilities))
    fn = sum(labels) - tp
    tn = len(labels) - tp - fp - fn
    return {"tn": tn, "fp": fp, "fn": fn, "tp": tp}


def threshold_metric(block, context, parameters, definition, numerator=None, denominator=None):
    arrays, parameters, error = inputs(block, context, parameters)
    if error:
        return error
    error = required_parameter(block, parameters, "threshold")
    if error:
        return error
    threshold = probability(parameters["threshold"], "threshold")
    labels, probabilities = arrays
    cells = counts(labels, probabilities, threshold)
    details = {
        "definition": definition, "sample_count": len(labels),
        "threshold": threshold, "positive_rule": "y_probability >= threshold",
        "counts": cells,
    }
    if numerator is None:
        return result(block, {
            **cells, "labels": [0, 1],
            "matrix": [[cells["tn"], cells["fp"]], [cells["fn"], cells["tp"]]],
        }, {**details, "matrix_axes": "rows=y_true, columns=predicted", "denominator": len(labels)})
    top = numerator(cells)
    bottom = denominator(cells)
    details.update(numerator=top, denominator=bottom)
    if bottom == 0:
        return unavailable(block, "zero_denominator", **details)
    return result(block, top / bottom, details)


def top_metric(block, context, parameters, *, recall):
    arrays, parameters, error = inputs(block, context, parameters)
    if error:
        return error
    error = required_parameter(block, parameters, "top_fraction")
    if error:
        return error
    fraction = number(parameters["top_fraction"], "top_fraction")
    if not 0 < fraction <= 1:
        raise ValueError("top_fraction must satisfy 0 < top_fraction <= 1")
    labels, probabilities = arrays
    size = len(labels)
    k = math.ceil(size * fraction)
    order = sorted(range(size), key=lambda index: -probabilities[index])
    selected = order[:k]
    captured = sum(labels[index] for index in selected)
    denominator = sum(labels) if recall else k
    details = {
        "definition": "captured positives / all positives" if recall else "captured positives / alerts",
        "sample_count": size, "top_fraction": fraction,
        "rounding": "ceil(sample_count * top_fraction)",
        "alert_count": k, "captured_positives": captured, "positive_count": sum(labels),
        "selected_indices": selected, "denominator": denominator,
        "tie_policy": "descending score, stable original row order; never expand alert budget",
        "boundary_tie": k < size and probabilities[order[k - 1]] == probabilities[order[k]],
    }
    if denominator == 0:
        return unavailable(block, "zero_denominator", **details)
    return result(block, captured / denominator, details)


def paired_ids(context, size):
    keys = ("sample_ids", "reference_sample_ids")
    missing = [key for key in keys if key not in context or context[key] is None]
    if missing:
        return unavailable("categorical_nri", "missing_inputs", missing_inputs=missing)
    arrays = []
    for key in keys:
        values = sequence(context[key], key)
        if len(values) != size:
            raise ValueError(f"{key} must match the probability row count")
        if any(isinstance(value, bool) or not isinstance(value, (str, Integral)) for value in values):
            raise ValueError(f"{key} must contain nonempty string or integer sample identifiers")
        normalized = [int(value) if isinstance(value, Integral) else value for value in values]
        if any(isinstance(value, str) and not value.strip() for value in normalized):
            raise ValueError(f"{key} must contain nonempty sample identifiers")
        if len(set(normalized)) != size:
            raise ValueError(f"{key} must contain unique sample identifiers")
        arrays.append(normalized)
    if arrays[0] != arrays[1]:
        raise ValueError("sample_ids and reference_sample_ids must match in the same row order")
    return None
