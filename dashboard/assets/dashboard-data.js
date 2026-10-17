// Empty initial shell. The publisher replaces this with actual aggregate outputs.
window.DASHBOARD_DATA = {
  schema: 1,
  purpose: "Progressive read-only dashboard; awaiting actual pipeline outputs.",
  generated_at: null,
  remote_pr1: null,
  target: null,
  dataset: {},
  eda: { results: [], assessment_status: "pending", executed_ids: [], reused_ids: [] },
  evaluation_plan: {},
  local_smoke: null,
  remote_model_evaluation: null,
  remote_pr2: null,
  pipeline: { repository: null, issue_number: null, stage: "idle", source_run: null, status: "pending" }
};
