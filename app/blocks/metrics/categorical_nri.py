"""Paired categorical NRI with pre-specified risk category cutoffs."""

from bisect import bisect_right

from ._validation import (
    cutoffs, inputs, paired_ids, required_parameter, result, text_context, unavailable,
)


def run(context: dict, parameters: dict | None = None) -> dict:
    block = "categorical_nri"
    arrays, parameters, error = inputs(block, context, parameters, reference=True)
    if error:
        return error
    error = required_parameter(block, parameters, "risk_thresholds")
    if error:
        return error
    thresholds = cutoffs(parameters["risk_thresholds"], "risk_thresholds")
    labels, probabilities, reference = arrays
    error = paired_ids(context, len(labels))
    if error:
        return error
    error = text_context(block, context, "comparison_model")
    if error:
        return error
    positives = sum(labels)
    negatives = len(labels) - positives
    details = {
        "definition": "event(up-down)/events + nonevent(down-up)/nonevents, new versus reference",
        "sample_count": len(labels), "positive_count": positives, "negative_count": negatives,
        "denominator": {"event": positives, "nonevent": negatives},
        "risk_thresholds": thresholds, "category_rule": "cutoff equality enters the higher risk category",
        "paired_sample_ids_verified": True, "comparison_model": context["comparison_model"],
        "interpretation": "Categorical NRI is not proof of incremental clinical benefit.",
        "precondition": "Risk thresholds must be pre-specified before evaluation; caller attests this.",
    }
    if not positives or not negatives:
        return unavailable(block, "both_classes_required", **details)
    moves = {"event_up": 0, "event_down": 0, "nonevent_up": 0, "nonevent_down": 0}
    for label, score, previous in zip(labels, probabilities, reference):
        direction = bisect_right(thresholds, score) - bisect_right(thresholds, previous)
        if direction:
            group = "event" if label else "nonevent"
            moves[f"{group}_{'up' if direction > 0 else 'down'}"] += 1
    event = (moves["event_up"] - moves["event_down"]) / positives
    nonevent = (moves["nonevent_down"] - moves["nonevent_up"]) / negatives
    return result(block, {
        "event_component": event, "nonevent_component": nonevent, "total": event + nonevent,
    }, {**details, "movements": moves})
