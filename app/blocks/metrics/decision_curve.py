"""Decision curve analysis requires an explicitly described clinical action."""

from ._validation import counts, cutoffs, inputs, required_parameter, result, text_context


def run(context: dict, parameters: dict | None = None) -> dict:
    block = "decision_curve"
    arrays, parameters, error = inputs(block, context, parameters)
    if error:
        return error
    error = required_parameter(block, parameters, "thresholds")
    if error:
        return error
    thresholds = cutoffs(parameters["thresholds"], "thresholds")
    error = text_context(block, context, "clinical_action")
    if error:
        return error
    labels, probabilities = arrays
    size = len(labels)
    positives = sum(labels)
    rows = []
    for threshold in thresholds:
        cells = counts(labels, probabilities, threshold)
        odds = threshold / (1 - threshold)
        rows.append({
            "threshold": threshold,
            "model_net_benefit": cells["tp"] / size - cells["fp"] / size * odds,
            "treat_all_net_benefit": positives / size - (size - positives) / size * odds,
            "treat_none_net_benefit": 0.0,
        })
    return result(block, rows, {
        "definition": "net benefit = TP/N - FP/N * threshold/(1-threshold)",
        "sample_count": size, "denominator": size,
        "clinical_action": context["clinical_action"],
        "positive_rule": "y_probability >= threshold",
        "interpretation": "Descriptive decision analysis, not proof of clinical benefit or authorization to act.",
    })
