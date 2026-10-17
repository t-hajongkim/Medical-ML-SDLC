/* Read-only public snapshot transport and aggregate/provenance validation. */
(() => {
  "use strict";

  const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
  const forbidden = new Set(["y_true", "y_probability", "patient_key", "row_key", "sample_ids", "selected_indices"]);
  const repoPattern = /^[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9_.-]+$/;
  const issue = value => Number.isSafeInteger(value) && value > 0;
  const fail = message => { throw new Error(message); };
  const canonical = value => JSON.stringify(value, (_key, child) => object(child) ? Object.fromEntries(Object.keys(child).sort().map(key => [key, child[key]])) : child);
  const issueOf = value => value.pipeline?.issue_number ?? value.target?.issue_number ?? value.remote_pr1?.issue_number ?? value.remote_pr1?.issue ?? null;
  const repositoryOf = value => value.pipeline?.repository || value.target?.repository || value.remote_pr1?.repository || value.remote_model_evaluation?.provenance?.repository || null;
  const identity = value => `${String(repositoryOf(value) || "").toLowerCase()}#${issueOf(value) || ""}`;
  const hasData = value => !!(value.target || value.remote_pr1 || value.remote_model_evaluation || value.local_smoke || value.eda?.results?.length || Object.keys(value.dataset || {}).length || Object.keys(value.evaluation_plan || {}).length);
  const count = value => Number.isSafeInteger(value) && value >= 0;
  const probability = value => typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1;
  const dataID = value => typeof value === "string" && /^[a-f0-9]{64}$/i.test(value);
  const metricIDs = new Set(["roc_auc", "average_precision", "brier_score", "calibration_curve", "f1", "recall", "precision"]);
  const empty = () => ({
    schema: 1, purpose: "Awaiting actual pipeline outputs.", generated_at: null,
    remote_pr1: null, target: null, dataset: {},
    eda: { results: [], assessment_status: "pending", executed_ids: [], reused_ids: [] },
    evaluation_plan: {}, local_smoke: null, remote_model_evaluation: null, remote_pr2: null,
    pipeline: { repository: null, issue_number: null, stage: "idle", source_run: null, status: "pending" }
  });

  function inspect(value, depth = 0) {
    if (depth > 40) fail("집계 JSON의 중첩 깊이가 너무 큽니다.");
    if (typeof value === "number" && !Number.isFinite(value)) fail("유효하지 않은 수치가 있습니다.");
    if (Array.isArray(value)) value.forEach(child => inspect(child, depth + 1));
    else if (object(value)) Object.entries(value).forEach(([key, child]) => {
      if (forbidden.has(key)) fail(`개인별 데이터 필드는 허용되지 않습니다: ${key}`);
      inspect(child, depth + 1);
    });
  }

  function githubLink(value, repository, prefix) {
    if (value == null) return;
    let url;
    try { url = new URL(value); } catch { fail("GitHub 출처 URL 형식이 잘못되었습니다."); }
    const expected = `/${repository.toLowerCase()}/${prefix}`;
    const matches = prefix.endsWith("/") ? url.pathname.toLowerCase().startsWith(expected) : url.pathname.toLowerCase() === expected;
    if (url.origin !== "https://github.com" || !matches || url.username || url.password) {
      fail("GitHub 출처 URL의 저장소가 일치하지 않습니다.");
    }
  }

  function validate(value, expectedRepository = null) {
    if (!object(value) || value.schema !== 1) fail("지원하지 않는 스냅샷 schema입니다.");
    for (const key of ["dataset", "eda", "evaluation_plan", "pipeline"]) if (!object(value[key])) fail(`${key} 집계 객체가 필요합니다.`);
    for (const key of ["target", "remote_pr1", "local_smoke", "remote_model_evaluation", "remote_pr2"]) {
      if (!(key in value) || (value[key] !== null && !object(value[key]))) fail(`${key} 형식이 잘못되었습니다.`);
    }
    for (const key of ["results", "executed_ids", "reused_ids"]) if (!Array.isArray(value.eda[key])) fail(`eda.${key} 배열이 필요합니다.`);
    if (value.eda.results.some(item => !object(item) || typeof item.block !== "string" || !object(item.details))) fail("EDA 블록 형식이 잘못되었습니다.");
    if (value.evaluation_plan.selected_ids != null && (!Array.isArray(value.evaluation_plan.selected_ids) || value.evaluation_plan.selected_ids.some(id => typeof id !== "string"))) fail("선택 지표 목록이 잘못되었습니다.");
    inspect(value);
    const repository = repositoryOf(value);
    const activeIssue = issueOf(value);
    const sources = [
      value.pipeline.repository, value.target?.repository, value.remote_pr1?.repository,
      value.remote_model_evaluation?.provenance?.repository
    ].filter(x => x != null);
    if (sources.some(x => typeof x !== "string" || !repoPattern.test(x) || x.toLowerCase() !== String(repository).toLowerCase())) fail("스냅샷의 저장소 출처가 서로 다릅니다.");
    if (expectedRepository && repository && repository.toLowerCase() !== expectedRepository.toLowerCase()) fail("설정된 공개 저장소와 다른 스냅샷입니다.");
    const issues = [
      value.pipeline.issue_number, value.target?.issue_number, value.remote_pr1?.issue_number, value.remote_pr1?.issue,
      value.remote_model_evaluation?.provenance?.issue_number, value.remote_pr2?.issue_number, value.remote_pr2?.source_issue_number
    ].filter(x => x != null);
    if (issues.some(x => !issue(x) || x !== activeIssue)) fail("Issue 출처가 섞인 스냅샷입니다.");
    if (hasData(value) && (!repository || !issue(activeIssue))) fail("실제 산출물의 저장소·Issue 출처가 필요합니다.");
    if (value.generated_at !== null && (typeof value.generated_at !== "string" || !/^\d{4}-\d\d-\d\dT/.test(value.generated_at) || !Number.isFinite(Date.parse(value.generated_at)))) fail("스냅샷 생성 시각이 잘못되었습니다.");
    if (hasData(value) && value.generated_at === null) fail("실제 산출물의 생성 시각이 필요합니다.");
    if (value.target) {
      if (!issue(value.target.issue_number) || !value.target.repository) fail("연구 요청의 저장소·Issue가 필요합니다.");
      githubLink(value.target.issue_url, repository, `issues/${activeIssue}`);
    }
    if (value.remote_pr1) {
      if (!issue(value.remote_pr1.issue_number ?? value.remote_pr1.issue)) fail("PR1의 원본 Issue가 필요합니다.");
      githubLink(value.remote_pr1.url, repository, "pull/");
    }
    const rows = value.dataset.split_rows;
    if (rows != null && (!object(rows) || Object.values(rows).some(count => !Number.isSafeInteger(count) || count < 0))) fail("Split 건수가 잘못되었습니다.");
    const datasetID = value.dataset.data_id;
    if (datasetID != null && !dataID(datasetID)) fail("데이터셋 식별자가 잘못되었습니다.");
    if (value.dataset.feature_count != null && !count(value.dataset.feature_count)) fail("Feature 개수가 잘못되었습니다.");
    if (value.dataset.feature_columns != null && (!Array.isArray(value.dataset.feature_columns) || value.dataset.feature_columns.some(column => typeof column !== "string") || (value.dataset.feature_count != null && value.dataset.feature_columns.length !== value.dataset.feature_count))) fail("Feature 목록이 입력 계약과 다릅니다.");
    const plan = value.evaluation_plan;
    if (plan.selected_ids && (plan.selected_ids.some(id => !metricIDs.has(id)) || new Set(plan.selected_ids).size !== plan.selected_ids.length)) fail("선택 지표가 지원 목록과 다릅니다.");
    if (plan.parameters != null && !object(plan.parameters)) fail("계획 parameters 형식이 잘못되었습니다.");
    if (plan.primary_metric != null && !plan.selected_ids?.includes(plan.primary_metric)) fail("주지표가 선택 목록에 없습니다.");
    const threshold = plan.threshold ?? plan.parameters?.threshold ?? null;
    if (threshold !== null && !probability(threshold)) fail("Threshold 값이 잘못되었습니다.");
    for (const planID of [plan.data_id, plan.context?.data_id, plan.inputs?.data_id]) {
      if (planID != null && (!datasetID || planID !== datasetID)) fail("평가 계획의 data_id가 데이터셋과 다릅니다.");
    }
    const evaluation = value.remote_model_evaluation;
    if (evaluation) {
      if (!object(evaluation.provenance) || !evaluation.provenance.repository || !issue(evaluation.provenance.issue_number)) fail("원격 평가의 Issue·저장소 provenance가 필요합니다.");
      if (!datasetID || evaluation.data_id !== datasetID) fail("원격 평가의 data_id가 현재 데이터셋과 다릅니다.");
      if (!Array.isArray(evaluation.metrics) || !object(evaluation.inference)) fail("원격 평가 집계 형식이 잘못되었습니다.");
      const inference = evaluation.inference;
      if (!count(inference.rows) || !count(inference.feature_count) || !["train", "validation", "test"].includes(inference.split)) fail("추론 입력 계약이 잘못되었습니다.");
      if (rows?.[inference.split] != null && inference.rows !== rows[inference.split]) fail("추론 입원 수가 현재 split과 다릅니다.");
      if (value.dataset.feature_count != null && inference.feature_count !== value.dataset.feature_count) fail("추론 feature 수가 현재 데이터셋과 다릅니다.");
      githubLink(evaluation.provenance.workflow_run_url, repository, "actions/runs/");
      const selected = plan.selected_ids;
      if (!Array.isArray(selected) || !Array.isArray(evaluation.selected_ids) || canonical([...selected].sort()) !== canonical([...evaluation.selected_ids].sort())) fail("원격 평가와 현재 계획의 선택 지표가 다릅니다.");
      if ((evaluation.primary_metric ?? null) !== (plan.primary_metric ?? null)) fail("원격 평가와 현재 계획의 주지표가 다릅니다.");
      const seen = new Set();
      for (const metric of evaluation.metrics) {
        if (!object(metric) || !metricIDs.has(metric.block) || seen.has(metric.block) || !selected.includes(metric.block) || (metric.details != null && !object(metric.details))) fail("평가 지표 형식이 잘못되었습니다.");
        seen.add(metric.block);
        if (metric.details?.sample_count != null && metric.details.sample_count !== inference.rows) fail("평가 지표의 표본 수가 현재 추론과 다릅니다.");
        if (metric.details?.threshold != null && metric.details.threshold !== threshold) fail("원격 평가와 현재 계획의 threshold가 다릅니다.");
        if (metric.block === "calibration_curve" && metric.status === "completed") {
          if (!Array.isArray(metric.value) || !metric.value.length) fail("Calibration 구간 집계가 필요합니다.");
          let sum = 0;
          for (const bin of metric.value) {
            if (!object(bin) || !count(bin.count) || !probability(bin.lower) || !probability(bin.upper) || bin.lower >= bin.upper) fail("Calibration 구간 형식이 잘못되었습니다.");
            if (bin.count > 0 && (!probability(bin.mean_predicted_probability) || !probability(bin.observed_event_rate))) fail("Calibration 관찰값이 잘못되었습니다.");
            if (bin.count === 0 && (bin.mean_predicted_probability !== null || bin.observed_event_rate !== null)) fail("빈 calibration 구간의 값은 null이어야 합니다.");
            sum += bin.count;
          }
          if (sum !== inference.rows) fail("Calibration 구간 입원 합계가 현재 추론과 다릅니다.");
        } else if (metric.status === "completed" && !probability(metric.value)) fail("완료 지표의 값이 잘못되었습니다.");
        if (metric.details?.counts) {
          const cells = ["tn", "fp", "fn", "tp"].map(key => metric.details.counts[key]);
          if (cells.some(cell => !count(cell)) || cells.reduce((sum, cell) => sum + cell, 0) !== inference.rows) fail("분류 집계의 입원 합계가 현재 추론과 다릅니다.");
        }
      }
      const planHash = plan.plan_hash || value.plan_hash;
      if (planHash && evaluation.plan_hash !== planHash) fail("원격 평가와 현재 계획의 hash가 다릅니다.");
    }
    if (value.local_smoke && (!datasetID || value.local_smoke.data_id !== datasetID)) fail("로컬 검증의 data_id가 현재 데이터셋과 다릅니다.");
    if (value.remote_pr2) {
      if (!evaluation) fail("현재 Issue의 원격 평가 없이 PR2를 표시할 수 없습니다.");
      const pr2 = value.remote_pr2;
      const branchIssue = String(pr2.headRefName || "").match(/(?:^|[-/])issue-(\d+)(?:-|$)/);
      const pr2Issue = pr2.issue_number ?? pr2.source_issue_number ?? (branchIssue ? Number(branchIssue[1]) : null);
      if (pr2Issue != null && pr2Issue !== activeIssue) fail("PR2의 원본 Issue를 현재 평가와 연결할 수 없습니다.");
      if (pr2Issue == null && value.pipeline.issue_number !== evaluation.provenance.issue_number) fail("PR2의 게시 케이스와 원격 평가 Issue가 다릅니다.");
      githubLink(pr2.url, repository, "pull/");
    }
    return value;
  }

  function configure(source) {
    if (source == null || source.repository == null) return { enabled: false, reason: source?.public === false ? "비공개 저장소는 자동 조회하지 않습니다. 포함된 스냅샷만 표시합니다." : "자동 조회가 설정되지 않았습니다. 포함된 스냅샷만 표시합니다." };
    if (typeof source.repository !== "string" || !repoPattern.test(source.repository) || source.repository.split("/").includes("..")) fail("공개 저장소 설정이 잘못되었습니다.");
    if (source.public !== true) return { enabled: false, repository: source.repository, reason: source.public === false ? "비공개 저장소는 자동 조회하지 않습니다. 포함된 스냅샷만 표시합니다." : "공개 저장소로 명시되지 않아 자동 조회하지 않습니다. 포함된 스냅샷만 표시합니다." };
    const branch = source.branch || "dashboard-data";
    const path = source.path || "dashboard/assets/dashboard-data.json";
    const segments = [branch, ...path.split("/")];
    if (segments.some(part => !part || part === "." || part === ".." || !/^[A-Za-z0-9_.-]+$/.test(part)) || !path.endsWith(".json")) fail("스냅샷 branch/path 설정이 잘못되었습니다.");
    return { enabled: true, repository: source.repository, branch, path, pollSeconds: Math.max(60, Number(source.poll_seconds) || 60) };
  }

  function create({ initial, source, onSnapshot, onStatus }) {
    let config;
    let configError = null;
    try { config = configure(source); } catch (error) { config = { enabled: false }; configError = error; }
    let current = empty();
    let status = { kind: "pending", message: "실제 파이프라인 결과를 기다립니다.", stale: false };
    let initialError = null;
    try { current = validate(initial, config.repository); } catch (error) { initialError = error; }
    let serialized = canonical(current);
    let inFlight = null;
    let timer = null;
    let disposed = false;
    const emit = (kind, message) => {
      status = { kind, message, stale: ["error", "invalid", "stale", "awaiting"].includes(kind) && hasData(current), checked_at: new Date().toISOString() };
      onStatus?.(status);
    };

    async function refresh() {
      if (!config.enabled || disposed) return false;
      if (inFlight) return inFlight;
      emit("loading", "공개 저장소의 게시 스냅샷을 확인합니다.");
      inFlight = (async () => {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 15000);
        try {
          const url = `https://raw.githubusercontent.com/${config.repository}/${encodeURIComponent(config.branch)}/${config.path.split("/").map(encodeURIComponent).join("/")}?snapshot=${Math.floor(Date.now() / 60000)}`;
          const response = await fetch(url, { method: "GET", credentials: "omit", cache: "no-store", redirect: "error", referrerPolicy: "no-referrer", signal: controller.signal });
          if (response.status === 404) {
            emit("awaiting", "게시된 스냅샷이 아직 없습니다 (404). 파이프라인 게시를 기다립니다.");
            return false;
          }
          if (!response.ok) throw new Error(`공개 스냅샷 조회 실패 (HTTP ${response.status}).`);
          const text = await response.text();
          if (text.length > 2 * 1024 * 1024) throw new Error("스냅샷 파일이 집계 용량 제한을 초과했습니다.");
          let incoming;
          try { incoming = JSON.parse(text); } catch { throw new Error("스냅샷이 유효한 JSON이 아닙니다."); }
          try { validate(incoming, config.repository); } catch (error) { emit("invalid", `스냅샷 검증 실패: ${error.message}`); return false; }
          const next = canonical(incoming);
          if (next === serialized) {
            emit("current", hasData(current) ? "게시된 최신 스냅샷과 같습니다." : "아직 실제 파이프라인 결과가 없습니다.");
            return false;
          }
          const before = current.generated_at === null ? -Infinity : Date.parse(current.generated_at);
          const after = incoming.generated_at === null ? -Infinity : Date.parse(incoming.generated_at);
          const previousIssue = issueOf(current), nextIssue = issueOf(incoming);
          if (after <= before || (previousIssue && nextIssue && nextIssue < previousIssue)) {
            emit("stale", "이전 시각·Issue 또는 같은 시각의 다른 스냅샷을 거부했습니다.");
            return false;
          }
          const changedIssue = identity(current) !== identity(incoming);
          current = incoming;
          serialized = next;
          onSnapshot?.(current, { changedIssue });
          emit("current", "실제 게시 스냅샷을 반영했습니다.");
          return true;
        } catch (error) {
          emit("error", error.name === "AbortError" ? "공개 스냅샷 조회 시간이 초과됐습니다." : `스냅샷 조회 오류: ${error.message}`);
          return false;
        } finally {
          clearTimeout(timeout);
          inFlight = null;
        }
      })();
      return inFlight;
    }

    function visibleRefresh() {
      if (document.visibilityState === "visible") void refresh();
    }
    function start() {
      if (initialError || configError) emit("invalid", `초기 설정·스냅샷 검증 실패: ${(configError || initialError).message}`);
      else if (!config.enabled) emit("offline", config.reason);
      if (config.enabled) {
        if (!initialError) emit("pending", "공개 게시 스냅샷을 기다립니다. 보이는 탭에서만 자동 조회합니다.");
        document.addEventListener("visibilitychange", visibleRefresh);
        timer = setInterval(visibleRefresh, config.pollSeconds * 1000);
        visibleRefresh();
      }
    }
    function dispose() {
      disposed = true;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", visibleRefresh);
    }
    return Object.freeze({ getSnapshot: () => current, getStatus: () => ({ ...status }), enabled: config.enabled, refresh, start, dispose });
  }

  window.DashboardSnapshots = Object.freeze({ create, validate, empty, identity, issueOf, repositoryOf });
})();
