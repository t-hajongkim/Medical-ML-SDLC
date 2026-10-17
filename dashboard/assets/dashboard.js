/* Read-only aggregate views. Public snapshot transport is isolated in dashboard-snapshot.js. */
(() => {
  "use strict";

  const view = document.getElementById("view");
  if (!window.DashboardSnapshots || !window.echarts) {
    view.innerHTML = '<div class="empty-state"><h1>대시보드 파일을 확인하세요</h1><p>assets/dashboard-snapshot.js와 vendor/echarts-6.1.0.min.js가 필요합니다. 전체 폴더를 함께 열어주세요.</p></div>';
    return;
  }
  const snapshots = window.DashboardSnapshots.create({
    initial: window.DASHBOARD_DATA,
    source: window.DASHBOARD_SOURCE,
    onSnapshot: applySnapshot,
    onStatus: showSnapshotStatus
  });
  let raw = snapshots.getSnapshot();
  window.DASHBOARD_DATA = raw;

  const stages = [
    { id: "target", name: "Target", icon: '<circle cx="10" cy="10" r="7"/><circle cx="10" cy="10" r="3"/>' },
    { id: "eda", name: "EDA", icon: '<path d="M3 17V9h3v8M9 17V3h3v14M15 17v-6h3v6"/>' },
    { id: "evaluation-plan", name: "Evaluation Plan", icon: '<rect x="4" y="3" width="13" height="15" rx="2"/><path d="m7 8 1 1 2-2m2 1h2M7 13h7"/>' },
    { id: "model", name: "Model", icon: '<path d="m10 2 8 4v9l-8 4-8-4V6l8-4Zm0 9 8-5M2 6l8 5v8"/>' },
    { id: "evaluation", name: "Evaluation", icon: '<path d="M3 17h15M5 14V7m5 7V3m5 11V9M3 7h4m1-4h4m1 6h4"/>' },
    { id: "result", name: "Result", icon: '<path d="M4 2h8l4 4v12H4V2Zm8 0v5h4M7 11h6m-6 3h6"/>' }
  ];
  const catalog = [
    { id: "roc_auc", name: "ROC AUC", description: "전체 순위 판별력", direction: "높을수록", parameter: "없음" },
    { id: "average_precision", name: "Average precision", short: "AP", description: "양성 중심 순위 성능", direction: "높을수록", parameter: "없음" },
    { id: "brier_score", name: "Brier score", description: "확률의 평균제곱오차", direction: "낮을수록", parameter: "없음" },
    { id: "calibration_curve", name: "Calibration", description: "예측확률과 관찰률의 일치", direction: "대각선 근접", parameter: "구간 수" },
    { id: "f1", name: "F1", description: "Precision·recall의 조화평균", direction: "높을수록", parameter: "Threshold" },
    { id: "recall", name: "Recall", description: "양성 중 탐지한 비율", direction: "높을수록", parameter: "Threshold" },
    { id: "precision", name: "Precision", description: "양성 예측 중 실제 양성 비율", direction: "높을수록", parameter: "Threshold" }
  ];
  const blockNames = {
    schema: "Feature 스키마",
    patient_overlap: "환자 중복",
    demographics: "인구통계",
    cohort: "코호트",
    label_observability: "라벨 관찰가능성",
    preprocessing_lineage: "전처리 계보",
    prediction_timing: "예측 시점"
  };
  const stateLabels = {
    completed: "완료", passed: "통과", failed: "진단 실패",
    diagnostic_failed: "진단 실패", execution_failed: "실행 실패",
    unverifiable: "확인 불가", pending: "대기", blocked: "차단",
    empty: "산출물 없음", selected: "선택", not_selected: "미선택",
    provided: "제공됨", local: "Local smoke", remote: "원격 추론",
    eligible_planned_inputs: "입력 생성 계획", view_error: "조회 오류"
  };
  let ds, blocks, counts, splitRows, totalRows, plan, remotePR1, selected, primary, planThreshold;
  const details = id => blocks.get(id)?.details || {};
  const splitKeys = ["train", "validation", "test"];
  const state = { stage: "target", edaTab: "cohort", demographic: "age_group", split: "validation", source: "remote" };
  let charts = [];
  let scheduled = [];

  const e = value => String(value ?? "—").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const n = value => Number.isFinite(Number(value)) && value !== null && value !== "" ? Number(value).toLocaleString("en-US") : "—";
  const decimal = (value, places = 4) => typeof value === "number" && Number.isFinite(value) ? value.toFixed(places) : "—";
  const percent = value => typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : "—";
  const short = value => typeof value === "string" ? `${value.slice(0, 12)}…` : "미제공";
  const badge = (status, label) => `<span class="badge" data-state="${e(status)}">${e(label || stateLabels[status] || status)}</span>`;
  const digest = value => `<span class="digest" title="${e(value)}">${e(value || "미제공")}</span>`;
  const metricID = value => ({ ap: "average_precision", brier: "brier_score", calibration: "calibration_curve", f1_score: "f1", rocauc: "roc_auc" })[String(value).toLowerCase()] || value;
  const metricInfo = id => catalog.find(x => x.id === metricID(id)) || { id, name: id, description: "", direction: "", parameter: "" };
  const sourceForStage = () => ["model", "evaluation", "result"].includes(state.stage) ? state.source : "pr1";
  const pr2Number = () => raw.remote_pr2?.number || raw.remote_pr2?.pr || null;
  const pr2Review = () => raw.remote_pr2?.isDraft ? "Draft / 사람 리뷰 대기" : raw.remote_pr2?.review_status || raw.remote_pr2?.status || "PR2 산출물 대기";
  const remoteRunURL = () => raw.remote_model_evaluation?.provenance?.workflow_run_url || raw.remote_model_evaluation?.workflow_run_url || null;
  const capturedAt = () => raw.generated_at ? new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(raw.generated_at)) : "미제공";
  const currentIssue = () => window.DashboardSnapshots.issueOf(raw);
  const hasDataset = () => totalRows !== null;
  const pipelineFailed = () => ["failure", "failed", "execution_failed", "error", "cancelled", "timed_out"].includes(raw.pipeline?.status);

  function hydrate(value) {
    raw = value;
    ds = raw.dataset || {};
    blocks = new Map((raw.eda?.results || []).map(x => [x.block || x.id, x]));
    counts = details("cohort").counts || {};
    splitRows = ds.split_rows || {};
    totalRows = splitKeys.every(key => Number.isSafeInteger(splitRows[key])) ? splitKeys.reduce((sum, key) => sum + splitRows[key], 0) : null;
    plan = raw.evaluation_plan || {};
    remotePR1 = raw.remote_pr1 || {};
    selected = new Set((plan.selected_ids || []).map(metricID));
    primary = metricID(plan.primary_metric);
    planThreshold = plan.threshold ?? plan.parameters?.threshold ?? null;
  }

  function applySnapshot(value, { changedIssue }) {
    hydrate(value);
    window.DASHBOARD_DATA = value;
    if (changedIssue) Object.assign(state, { edaTab: "cohort", demographic: "age_group", split: "validation", source: "remote" });
    if (!raw.local_smoke) state.source = "remote";
    render();
  }

  function showSnapshotStatus(status) {
    const element = document.getElementById("snapshot-status");
    element.dataset.kind = status.kind;
    const pipeline = raw.pipeline || {};
    const progress = pipeline.stage && pipeline.stage !== "idle" ? ` 파이프라인: ${pipeline.stage} / ${pipeline.status || "pending"}.` : "";
    element.textContent = `${pipelineFailed() ? `파이프라인 실행 실패 (${pipeline.stage}). ` : ""}${status.message}${status.stale ? ` 마지막 정상 스냅샷을 유지합니다 (캡처 ${capturedAt()} KST).` : ""}${progress}`;
    element.dataset.execution = pipeline.status || "pending";
    const refresh = document.getElementById("snapshot-refresh");
    refresh.disabled = !snapshots.enabled || status.kind === "loading";
    refresh.setAttribute("aria-busy", String(status.kind === "loading"));
  }

  hydrate(raw);

  function table(headers, rows, options = {}) {
    const headings = headers.map(h => typeof h === "string" ? { text: h } : h);
    return `<div class="table-responsive${options.scroll ? " table-scroll" : ""}"${options.label ? ` tabindex="0" role="region" aria-label="${e(options.label)}"` : ""}><table class="table table-vcenter${options.nowrap ? " table-nowrap" : ""}"${options.id ? ` id="${e(options.id)}"` : ""}><thead><tr>${headings.map(h => `<th scope="col"${h.numeric ? ' class="numeric"' : ""}>${e(h.text)}</th>`).join("")}</tr></thead><tbody>${rows.map(row => `<tr>${row.map((cell, i) => `<td${headings[i]?.numeric ? ' class="numeric"' : ""}>${cell}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  }

  function kpis(items) {
    return `<div class="kpi-strip">${items.map(item => `<div class="kpi"><p class="kpi-label">${e(item.label)}</p><div class="kpi-value${item.word ? " word" : ""}"${item.raw != null ? ` title="${e(item.raw)}"` : ""}>${e(item.value)}</div><p class="kpi-note">${e(item.note || " ")}</p></div>`).join("")}</div>`;
  }

  function dataKPIs() {
    return kpis([
      { label: "전체 입원", value: n(totalRows), note: "Admission / encounter" },
      { label: "Train", value: n(splitRows.train), note: "공개 split 유지" },
      { label: "Validation", value: n(splitRows.validation), note: "평가용 split" },
      { label: "Test", value: n(splitRows.test), note: "이번 평가에 사용하지 않음" }
    ]);
  }

  function sourcePayload() {
    return state.source === "remote" ? raw.remote_model_evaluation : raw.local_smoke;
  }

  function sourceRoot(payload = sourcePayload()) {
    return payload?.report || payload?.result || payload || {};
  }

  function metricsOf(payload = sourcePayload(), depth = 0) {
    if (!payload || depth > 5) return [];
    if (Array.isArray(payload)) {
      return payload.filter(x => x && typeof x === "object" && catalog.some(c => c.id === metricID(x.block || x.id || x.metric))).map(x => ({ ...x, block: metricID(x.block || x.id || x.metric) }));
    }
    for (const key of ["metrics", "evaluation_results", "results", "evaluation", "report", "result", "model_evaluation"]) {
      const part = payload[key];
      const found = metricsOf(part, depth + 1);
      if (found.length) return found;
      if (key === "metrics" && part && typeof part === "object" && !Array.isArray(part)) {
        const mapped = Object.entries(part).filter(([id]) => catalog.some(c => c.id === metricID(id))).map(([id, value]) => typeof value === "number" || Array.isArray(value) ? { block: metricID(id), status: "completed", value, details: {} } : { ...value, block: metricID(id) });
        if (mapped.length) return mapped;
      }
    }
    return [];
  }

  function inferenceOf(payload = sourcePayload()) {
    const root = sourceRoot(payload);
    return root.inference || payload?.inference || root.model?.inference || {};
  }

  function valueOf(id, metrics = metricsOf()) {
    return metrics.find(x => x.block === id)?.value;
  }

  function thresholdOf(payload = sourcePayload()) {
    const root = sourceRoot(payload);
    return root.threshold ?? payload?.threshold ?? root.parameters?.threshold ?? root.evaluation_plan?.threshold ?? metricsOf(payload).find(x => ["f1", "recall", "precision"].includes(x.block))?.details?.threshold ?? null;
  }

  function calibrationOf(metrics = metricsOf()) {
    const metric = metrics.find(x => x.block === "calibration_curve");
    const points = metric?.value || metric?.details?.bins || metric?.details?.points;
    return Array.isArray(points) ? points.map(x => ({
      ...x,
      mean_predicted_probability: x.mean_predicted_probability ?? x.mean_predicted ?? null,
      observed_event_rate: x.observed_event_rate ?? x.observed_rate ?? null
    })) : [];
  }

  function executionState(payload = sourcePayload()) {
    if (!payload) return "pending";
    const root = sourceRoot(payload);
    const status = root.execution_status || payload.execution_status || root.inference?.status || root.status;
    if (["failed", "error", "execution_failed"].includes(status)) return "execution_failed";
    if (["success", "completed", "passed"].includes(status) || inferenceOf(payload).inference_performed === true) return "completed";
    return "unverifiable";
  }

  function provenance() {
    const source = sourceForStage();
    let title, context, label, link = "";
    if (source === "pr1") {
      title = raw.remote_pr1 ? `원격 PR1 #${remotePR1.pr || remotePR1.number || "미제공"} 스냅샷` : raw.target ? `연구 요청 / Issue #${currentIssue()}` : "파이프라인 결과 대기";
      label = raw.remote_pr1 ? "PR1" : raw.target ? "Issue" : "대기";
      context = raw.remote_pr1 ? `head ${String(remotePR1.head || "미제공").slice(0, 12)} / run ${remotePR1.source_run || "미제공"}` : raw.target ? "실제 연구 요청을 받았습니다. 분석 산출물은 아직 없습니다." : "등록된 연구 요청·분석 산출물이 없습니다.";
      if (remotePR1.url) link = `<a href="${e(remotePR1.url)}" target="_blank" rel="noopener">원본 PR</a>${remotePR1.merge_commit ? ` ${badge("completed", "Merge 완료")}` : ""}`;
      else if (raw.target?.issue_url) link = `<a href="${e(raw.target.issue_url)}" target="_blank" rel="noopener">원본 Issue</a>`;
    } else if (source === "local") {
      title = "Local smoke";
      label = "로컬 검증";
      context = "보존된 fixture 기반 로컬 추론. 원격 최신 실행 결과가 아닙니다.";
    } else {
      title = "원격 모델 평가 스냅샷";
      label = raw.remote_model_evaluation ? "원격 추론" : "대기";
      const payload = raw.remote_model_evaluation;
      const pr = raw.remote_pr2;
      const run = payload?.source_run || payload?.run_id || sourceRoot(payload).source_run || remoteRunURL()?.split("/").pop() || "미제공";
      context = payload ? `run ${run} / ${pr2Review()}` : "원격 추론·PR2 산출물은 이 파일에 아직 없습니다.";
      if (pr?.url) link = `<a href="${e(pr.url)}" target="_blank" rel="noopener">PR2 #${e(pr2Number())}${pr.isDraft ? " / Draft" : ""}</a>`;
    }
    const dataID = source === "pr1" ? ds.data_id : sourceRoot().data_id || sourcePayload()?.data_id;
    const pending = (source === "remote" && !raw.remote_model_evaluation) || (source === "pr1" && !raw.remote_pr1 && !raw.target);
    return `<div class="provenance" data-source="${e(source)}"><div class="provenance-main">${badge(pending ? "pending" : source, label)}<strong>${e(title)}</strong>${link}</div><div class="provenance-meta">${e(context)}<br>data ${e(short(dataID))}${raw.generated_at ? ` / 캡처 ${e(capturedAt())} KST` : " / 캡처 시각 미제공"}</div></div>`;
  }

  function sourceSelector() {
    if (!raw.local_smoke) return "";
    return `<div class="heading-controls"><label><span class="control-label">결과 출처</span><select id="result-source" aria-label="결과 출처"><option value="remote"${state.source === "remote" ? " selected" : ""}>원격 추론${raw.remote_model_evaluation ? " 스냅샷" : " / 산출물 대기"}</option><option value="local"${state.source === "local" ? " selected" : ""}>Local smoke / 원격 결과 아님</option></select></label></div>`;
  }

  function heading(description) {
    const stage = stages.find(x => x.id === state.stage);
    return `<div class="page-heading"><div><h1 id="stage-heading" tabindex="-1">${e(stage.name)}</h1><p class="page-description">${e(description)}</p></div>${sourceForStage() === "pr1" ? "" : sourceSelector()}</div>${provenance()}`;
  }

  function pendingKPIs() {
    const labels = {
      target: ["전체 입원", "Train", "Validation", "Test"],
      eda: ["전체 입원", "Train", "Validation", "Test"],
      "evaluation-plan": ["선택 지표", "주지표", "Threshold", "평가 입원"],
      model: ["모델 입력", "Runtime", "추론 입원", "새 학습"],
      evaluation: ["평가 입원", "Average precision / AP", "ROC AUC", "Brier score"],
      result: ["Average precision / AP", "ROC AUC", "Brier score", "F1"]
    };
    return kpis(labels[state.stage].map(label => ({ label, value: "—", note: "실제 산출물 대기" })));
  }

  function awaitingContent(message) {
    return `<div class="empty-state">${badge("pending", "실제 산출물 대기")}<h2>아직 표시할 분석 결과가 없습니다.</h2><p>${e(message)}</p><p>파이프라인이 게시한 실제 값만 표시합니다. 대기 값을 0으로 채우거나 이전 Issue 결과를 가져오지 않습니다.</p></div>`;
  }

  function stageReady(stage) {
    if (stage === "target") return hasDataset();
    if (stage === "eda") return blocks.size > 0;
    if (stage === "evaluation-plan") return Array.isArray(plan.selected_ids);
    return !!sourcePayload();
  }

  function pendingView() {
    const descriptions = {
      target: "연구 요청을 확인하고 실제 데이터 집계를 기다립니다.",
      eda: "EDA 실행 결과가 게시되면 집계 차트와 진단을 표시합니다.",
      "evaluation-plan": "실제 PR1 평가 계획이 게시되면 지표와 설정을 표시합니다.",
      model: "고정 모델의 실제 추론 근거를 기다립니다.",
      evaluation: "실제 평가가 완료되면 계산된 지표와 평가 근거를 표시합니다.",
      result: "실제 평가 지표와 결과 PR의 게시를 기다립니다."
    };
    const request = state.stage === "target" && raw.target ? `<section class="section request-section"><div class="section-head"><div><h2>${e(raw.target.title || `연구 요청 #${currentIssue()}`)}</h2><p class="panel-subtitle">Issue #${e(currentIssue())} / 실제 연구 요청</p></div>${badge("provided", "요청 수신")}</div><p class="text-body">${e(raw.target.research_goal || "연구 목적 미제공")}</p>${raw.target.research_question ? `<details class="details"><summary>연구 질문과 추가 요청</summary><p class="text-body">${e(raw.target.research_question)}</p><p class="text-body">${e(raw.target.cohort_requirements || "")}</p><p class="text-body">${e(raw.target.additional_requests || "")}</p></details>` : ""}</section>` : "";
    const failureStages = {
      target: ["issue", "target"], eda: ["eda", "pr1"],
      "evaluation-plan": ["evaluation-plan", "evaluation_plan", "pr1", "pr1_feedback"],
      model: ["model", "inference", "model_evaluation", "model-evaluation"],
      evaluation: ["evaluation", "model", "inference", "model_evaluation", "model-evaluation"],
      result: ["result", "pr2", "publish", "publication"]
    };
    const failed = pipelineFailed() && failureStages[state.stage].includes(raw.pipeline?.stage);
    const content = failed ? `<div class="empty-state">${badge("execution_failed")}<h2>파이프라인 실행에 실패했습니다.</h2><p>${e(raw.pipeline.message || raw.pipeline.error || "현재 단계의 실제 실행 산출물이 없습니다. 원본 Actions 실행을 확인하세요.")}</p><p>데이터 점검의 진단 실패와 구분합니다. 이전 Issue의 값으로 채우지 않습니다.</p></div>` : awaitingContent(descriptions[state.stage]);
    return `${heading(descriptions[state.stage])}${pendingKPIs()}${request}${content}`;
  }

  function plotPanel(id, title, subtitle, options = {}) {
    return `<section class="panel"><div class="panel-head"><div><h2>${e(title)}</h2>${subtitle ? `<p class="panel-subtitle">${e(subtitle)}</p>` : ""}</div>${options.badge || ""}</div><div class="chart-frame"><div class="chart${options.tall ? " tall" : ""}${options.short ? " short" : ""}" id="${e(id)}" role="img" aria-label="${e(title)}"></div></div>${options.foot ? `<p class="chart-foot">${e(options.foot)}</p>` : ""}${options.legend || ""}</section>`;
  }

  function legend() {
    return '<div class="legend-inline" aria-label="범례"><span><i class="legend-mark light" aria-hidden="true"></i>Target 0</span><span><i class="legend-mark" aria-hidden="true"></i>Target 1 / 30일 이내</span></div>';
  }

  function queue(id, option, label) {
    scheduled.push(() => {
      const el = document.getElementById(id);
      if (!el) return;
      const chart = echarts.init(el, null, { renderer: "svg" });
      chart.setOption({
        animation: false,
        color: ["#000000", "#aaaaaa", "#666666", "#dddddd", "#333333", "#eeeeee"],
        textStyle: { fontFamily: '"Segoe UI","Malgun Gothic",sans-serif', fontSize: 11, color: "#000000" },
        backgroundColor: "#ffffff",
        aria: { enabled: true, label: { description: label } },
        tooltip: {
          trigger: "axis", confine: true, backgroundColor: "#ffffff", borderColor: "#bbbbbb", borderWidth: 1,
          textStyle: { color: "#000000", fontSize: 11 },
          extraCssText: "box-shadow:0 2px 8px rgba(0,0,0,.12);border-radius:3px",
          axisPointer: { type: "shadow", shadowStyle: { color: "rgba(0,0,0,.04)" }, lineStyle: { color: "#777777" }, label: { color: "#ffffff", backgroundColor: "#000000" } },
          formatter: values => {
            const rows = Array.isArray(values) ? values : [values];
            return `<strong>${e(rows[0]?.axisValueLabel || rows[0]?.name || "")}</strong>${rows.map(x => `<br>${e(x.seriesName)}: ${e(Array.isArray(x.value) ? x.value.map(v => decimal(v, 4)).join(", ") : n(x.value))}`).join("")}`;
          }
        },
        ...option
      });
      el.dataset.source = sourceForStage();
      el.setAttribute("aria-label", label);
      charts.push(chart);
    });
  }

  function valueAxis(extra = {}) {
    return { type: "value", axisLabel: { color: "#666666", fontSize: 10 }, axisLine: { show: false, lineStyle: { color: "#aaaaaa" } }, axisTick: { show: false }, splitLine: { lineStyle: { color: "#eeeeee" } }, ...extra };
  }

  function categoryAxis(data, extra = {}) {
    return { type: "category", data, axisLabel: { color: "#555555", fontSize: 11 }, axisLine: { show: false, lineStyle: { color: "#aaaaaa" } }, axisTick: { show: false }, ...extra };
  }

  const hatch = { symbol: "rect", symbolSize: 1, color: "rgba(0,0,0,.22)", dashArrayX: [1, 0], dashArrayY: [2, 5], rotation: Math.PI / 4 };

  function splitPlot(id, stacked = false) {
    const order = splitKeys.filter(k => splitRows[k] != null);
    const series = stacked ? [
      { name: "Target 0", type: "bar", stack: "cohort", barWidth: 25, data: order.map(k => counts[k]?.target_0 ?? null), itemStyle: { color: "#dddddd", decal: hatch }, emphasis: { disabled: true } },
      { name: "Target 1", type: "bar", stack: "cohort", barWidth: 25, data: order.map(k => counts[k]?.target_1 ?? null), itemStyle: { color: "#000000" }, label: { show: true, position: "right", color: "#000000", fontSize: 11, formatter: p => n(counts[order[p.dataIndex]]?.admissions) }, emphasis: { disabled: true } }
    ] : [
      { name: "입원 건수", type: "bar", barWidth: 27, data: order.map((k, i) => ({ value: splitRows[k], itemStyle: { color: i === 0 ? "#000000" : i === 1 ? "#aaaaaa" : "#dddddd", decal: i === 0 ? null : hatch } })), label: { show: true, position: "right", color: "#000000", fontSize: 12, formatter: p => n(p.value) }, emphasis: { disabled: true } }
    ];
    queue(id, { grid: { left: 78, right: 66, top: 25, bottom: 32 }, xAxis: valueAxis({ axisLabel: { color: "#666666", fontSize: 10, formatter: v => v ? `${v / 1000}k` : "0" } }), yAxis: categoryAxis(order.map(x => x[0].toUpperCase() + x.slice(1)), { inverse: true }), series }, `공개 split의 입원 건수. Train ${n(splitRows.train)}, validation ${n(splitRows.validation)}, test ${n(splitRows.test)}. ${stacked ? "회색은 target 0, 검정은 30일 이내 재입원 라벨 target 1." : ""}`);
  }

  function cohortTable() {
    return table(["Split", { text: "입원", numeric: true }, { text: "고유 환자", numeric: true }, { text: "Target 0", numeric: true }, { text: "Target 1", numeric: true }, { text: "양성률", numeric: true }], splitKeys.map(k => [
      e(k), n(counts[k]?.admissions ?? splitRows[k]), n(counts[k]?.patients), n(counts[k]?.target_0), n(counts[k]?.target_1), percent(counts[k]?.event_rate)
    ]), { label: "Split별 입원·환자·라벨 집계", nowrap: true });
  }

  function targetView() {
    splitPlot("target-split");
    const mapping = ds.target_mapping || {};
    return `${heading("30일 이내 재입원을 평가합니다. 준비된 데이터·split·모델을 변경하지 않습니다.")}${dataKPIs()}
      <div class="analysis-grid">
        ${plotPanel("target-split", "데이터 분할", "공개 데이터의 admission / encounter 기준", { foot: "행을 재분할하거나 첫 입원만 선택하지 않습니다. 반복 입원을 유지합니다." })}
        <section class="panel"><div class="panel-head"><div><h2>예측 대상</h2><p class="panel-subtitle">제공된 target mapping</p></div></div>
          <div class="mapping-table">${table(["원본 라벨", "인코딩"], Object.entries(mapping).map(([key, value]) => [e(key), `${n(value)} ${value === 1 ? '<small>30일 이내</small>' : '<small>그 외 인코딩</small>'}`]))}</div>
          <dl class="definition-list"><div class="definition-row"><dt>Feature</dt><dd>${n(ds.feature_count)}개 / 고정 입력</dd></div><div class="definition-row"><dt>예측 시점</dt><dd>${raw.target?.prediction_time ? e(raw.target.prediction_time) : badge("pending", "미선언")}</dd></div><div class="definition-row"><dt>라벨 해석</dt><dd>0은 완전한 30일 무사건 추적을 보장하지 않습니다.</dd></div></dl>
        </section>
      </div>
      <section class="section"><div class="section-head"><div><h2>코호트 구성</h2><p class="panel-subtitle">양성률 = target 1 / 해당 split의 입원 건수</p></div>${badge("unverifiable", "원자료 선정 기준 확인 불가")}</div>${cohortTable()}<p class="source-caption">전체 고유 환자 ${n(details("cohort").unique_patients)}명. split 간 중복이 있으므로 split별 고유 환자 수를 더하지 않습니다.</p></section>
      <details class="details"><summary>연구 요청과 데이터 식별자</summary><p class="text-body">${e(raw.target?.research_goal || "연구 목적 미제공")}</p><p>Data digest</p>${digest(ds.data_id)}<p><a href="${e(raw.target?.issue_url || remotePR1.url || "#target")}" target="_blank" rel="noopener">원본 연구 요청</a></p></details>`;
  }

  function edaTabs() {
    const tabs = [["cohort", "코호트"], ["demographics", "인구통계"], ["schema", "스키마"], ["evidence", "진단 근거"]];
    return `<div class="tabs" role="tablist" aria-label="EDA 분석 보기">${tabs.map(([id, name]) => `<button class="tab" type="button" role="tab" id="tab-${id}" data-tab="${id}" aria-selected="${state.edaTab === id}" aria-controls="eda-content" tabindex="${state.edaTab === id ? 0 : -1}">${e(name)}</button>`).join("")}</div>`;
  }

  function blockSummary(id) {
    const d = details(id);
    switch (id) {
      case "schema": return `${n(d.actual_count)} / ${n(d.expected_count)} feature 일치. 수치 계약은 ${d.numeric_contract === "passed" ? "통과" : "미확정"}, 임상적 의미는 별도 검증 필요`;
      case "patient_overlap": return `Train–validation ${n(d.overlaps?.train_validation)}명. 공개 split 간 환자 중복`;
      case "demographics": return "연령·성별·인종의 입원 및 라벨 분포. 원본 race 결측 별도 보존";
      case "cohort": return `입원 ${n(d.total_admissions)}건, 고유 환자 ${n(d.unique_patients)}명. 연구 단위는 미선언`;
      case "label_observability": return "인코딩 검증 통과. 원본 라벨 정합·30일 추적 완전성은 확인 불가";
      case "preprocessing_lineage": return "산출물 해시 검증과 과거 전처리 fit 범위·누수 검증을 구분";
      case "prediction_timing": return `승인된 예측 시점 없음. ${n(d.undocumented_features?.length)}개 feature 가용시점 근거 부족`;
      default: return "원본 보고서를 확인하세요.";
    }
  }

  function blockTable() {
    const ids = ["schema", "patient_overlap", "demographics", "cohort", "label_observability", "preprocessing_lineage", "prediction_timing"];
    return table(["분석 블록", "진단", "주요 근거", "이번 PR1"], ids.filter(id => blocks.has(id)).map(id => [
      e(blockNames[id]), badge(blocks.get(id).status),
      `<span class="text-cell">${e(blockSummary(id))}</span>`,
      `<small>${(raw.eda?.executed_ids || []).includes(id) ? "실행" : (raw.eda?.reused_ids || []).includes(id) ? "기존 집계 재사용" : "제공된 집계"}</small>`
    ]), { label: "EDA 7개 블록 진단 결과" });
  }

  function overlapPlot() {
    const overlaps = details("patient_overlap").overlaps || ds.patient_overlap || {};
    const pairs = [["train_validation", "Train / val."], ["train_test", "Train / test"], ["validation_test", "Val. / test"]];
    queue("eda-overlap", {
      grid: { left: 85, right: 42, top: 25, bottom: 32 },
      xAxis: valueAxis({ axisLabel: { color: "#666666", fontSize: 10, formatter: v => v ? `${v / 1000}k` : "0" } }),
      yAxis: categoryAxis(pairs.map(x => x[1]), { inverse: true, axisLabel: { color: "#555555", fontSize: 10 } }),
      series: [{ name: "중복 고유 환자", type: "bar", barWidth: 23, data: pairs.map(x => overlaps[x[0]] ?? null), itemStyle: { color: "#000000" }, label: { show: true, position: "right", fontSize: 11, color: "#000000", formatter: p => n(p.value) }, emphasis: { disabled: true } }]
    }, `split 쌍별 중복 고유 환자. Train-validation ${n(overlaps.train_validation)}명, train-test ${n(overlaps.train_test)}명, validation-test ${n(overlaps.validation_test)}명. 중복 입원 행의 수가 아닙니다.`);
  }

  function edaCohort() {
    splitPlot("eda-labels", true);
    overlapPlot();
    return `<div class="analysis-grid">
      ${plotPanel("eda-labels", "Split별 라벨 분포", "막대 길이: 입원 건수 / 검정: 30일 이내 재입원 라벨", { legend: legend(), foot: "양성률은 모든 split에서 약 11%. 모델의 recall이나 precision이 아닙니다." })}
      ${plotPanel("eda-overlap", "Split 간 환자 중복", "단위: 각 split 쌍의 중복 고유 환자", { badge: badge(blocks.get("patient_overlap")?.status || "unverifiable") })}
    </div>
    <section class="section"><div class="section-head"><div><h2>코호트 상세</h2><p class="panel-subtitle">양성률 분모는 입원 건수입니다. 환자 수는 split 간 합산하지 않습니다.</p></div></div>${cohortTable()}</section>
    <section class="section"><div class="section-head"><div><h2>EDA 진단</h2><p class="panel-subtitle">블록 점검 결과와 workflow 실행 성공은 서로 다른 상태입니다.</p></div>${badge(raw.eda?.assessment_status || "unverifiable")}</div>${blockTable()}</section>`;
  }

  function demographicRows() {
    const distribution = details("demographics").distributions?.[state.split]?.[state.demographic] || {};
    const rows = Object.entries(distribution).map(([group, values]) => ({ group, ...values }));
    if (state.demographic === "age_group") rows.sort((a, b) => (parseInt(a.group.replace("[", ""), 10) || 0) - (parseInt(b.group.replace("[", ""), 10) || 0));
    return rows;
  }

  function demographicPlot(rows) {
    const labels = rows.map(x => x.group === "MISSING_ORIGINAL" ? "원본 결측" : x.group);
    queue("demographic-counts", {
      grid: { left: state.demographic.startsWith("race") ? 114 : 90, right: 34, top: 14, bottom: 30 },
      xAxis: valueAxis({ axisLabel: { color: "#666666", fontSize: 10, formatter: v => v >= 1000 ? `${v / 1000}k` : n(v) } }),
      yAxis: categoryAxis(labels, { inverse: true, axisLabel: { color: "#555555", fontSize: 10 } }),
      series: [
        { name: "Target 0", type: "bar", stack: "demographics", barMaxWidth: 20, data: rows.map(x => x.target_0), itemStyle: { color: "#dddddd", decal: hatch }, emphasis: { disabled: true } },
        { name: "Target 1", type: "bar", stack: "demographics", barMaxWidth: 20, data: rows.map(x => x.target_1), itemStyle: { color: "#000000" }, emphasis: { disabled: true } }
      ]
    }, `${state.split} ${state.demographic} 그룹별 target 0과 target 1 입원 건수. 원본 집계 기반이며 모델 하위집단 성능이 아닙니다.`);
    queue("demographic-rate", {
      grid: { left: state.demographic.startsWith("race") ? 114 : 90, right: 32, top: 14, bottom: 30 },
      tooltip: { trigger: "item", confine: true, backgroundColor: "#ffffff", borderColor: "#bbbbbb", textStyle: { color: "#000000", fontSize: 11 }, formatter: p => `${e(labels[p.dataIndex])}<br>관찰 양성률: ${percent(rows[p.dataIndex].event_rate)}<br>${n(rows[p.dataIndex].target_1)} / ${n(rows[p.dataIndex].admissions)} 입원` },
      xAxis: valueAxis({ min: 0, max: Math.max(.2, Math.ceil(Math.max(...rows.map(x => x.event_rate || 0)) * 10) / 10), axisLabel: { color: "#666666", fontSize: 10, formatter: v => `${Math.round(v * 100)}%` } }),
      yAxis: categoryAxis(labels, { inverse: true, axisLabel: { show: false } }),
      series: [{ name: "관찰 양성률", type: "scatter", symbol: "circle", symbolSize: 7, data: rows.map((x, i) => [x.event_rate, i]), itemStyle: { color: "#000000", opacity: 1 }, emphasis: { disabled: true }, label: { show: true, position: "right", fontSize: 10, color: "#000000", formatter: p => percent(rows[p.dataIndex].event_rate) } }]
    }, `${state.split} ${state.demographic}별 관찰 양성률. 각 그룹의 target 1 입원 수를 해당 그룹 전체 입원 수로 나눈 값입니다.`);
  }

  function edaDemographics() {
    const rows = demographicRows();
    demographicPlot(rows);
    const splitOptions = splitKeys.map(x => `<option value="${x}"${state.split === x ? " selected" : ""}>${e(x)}</option>`).join("");
    const groups = [["age_group", "연령"], ["gender", "성별"], ["race_original", "원본 race / 결측 보존"], ["race", "공개 race / 보정 포함"]];
    const groupOptions = groups.map(([id, name]) => `<option value="${id}"${state.demographic === id ? " selected" : ""}>${e(name)}</option>`).join("");
    const missing = ds.race_missing?.[state.split] ?? details("demographics").original_race_missing?.[state.split];
    return `<div class="filters"><label>Split <select id="demographic-split" aria-label="인구통계 split">${splitOptions}</select></label><label>항목 <select id="demographic-group" aria-label="인구통계 항목">${groupOptions}</select></label></div>
      <div class="analysis-grid">
        ${plotPanel("demographic-counts", "그룹별 라벨 분포", `${state.split} / admission 기준`, { tall: true, legend: legend() })}
        ${plotPanel("demographic-rate", "관찰 양성률", "Target 1 / 해당 그룹 입원 건수", { tall: true })}
      </div>
      <div class="notice compact"><strong>원본 race 결측 ${n(missing)}건 / ${n(splitRows[state.split])} 입원</strong><p>공개 race에는 Caucasian 보정이 포함됩니다. 원본 race 보기에서는 결측을 별도 범주로 유지합니다. 이 차트는 하위집단 모델 성능 비교가 아닙니다.</p></div>
      <section class="section"><div class="section-head"><h2>그룹별 집계<span class="inline-count">${rows.length}개 그룹</span></h2></div>${table(["그룹", { text: "입원", numeric: true }, { text: "Target 0", numeric: true }, { text: "Target 1", numeric: true }, { text: "양성률", numeric: true }, { text: "고유 환자", numeric: true }], rows.map(x => [e(x.group === "MISSING_ORIGINAL" ? "원본 결측" : x.group), n(x.admissions), n(x.target_0), n(x.target_1), percent(x.event_rate), n(x.patients)]), { label: "인구통계별 실제 집계", nowrap: true })}<p class="source-caption">동일 환자가 여러 입원·그룹에 나타날 수 있습니다. 그룹별 고유 환자를 더해 전체 환자 수를 계산하지 않습니다.</p></section>`;
  }

  function edaSchema() {
    const schema = details("schema");
    const quality = Object.entries(schema.quality || {});
    const constant = quality.filter(([, x]) => x.constant).length;
    queue("schema-structure", {
      grid: { left: 96, right: 35, top: 20, bottom: 35 },
      xAxis: valueAxis({ minInterval: 1 }),
      yAxis: categoryAxis(["변동 feature", "상수 feature"], { inverse: true }),
      series: [{ type: "bar", name: "Feature 수", barWidth: 28, data: [{ value: quality.length - constant, itemStyle: { color: "#000000" } }, { value: constant, itemStyle: { color: "#aaaaaa", decal: hatch } }], label: { show: true, position: "right", color: "#000000", formatter: p => n(p.value) }, emphasis: { disabled: true } }]
    }, `제공된 ${quality.length}개 feature의 전체 데이터 범위에서 변동 feature ${quality.length - constant}개, 상수 feature ${constant}개. 상수 여부는 자동 제거 기준이 아닙니다.`);
    const missing = quality.reduce((sum, [, x]) => sum + (x.missing || 0), 0);
    const invalid = quality.reduce((sum, [, x]) => sum + (x.non_numeric || 0) + (x.non_finite || 0), 0);
    return `<div class="analysis-grid">
      ${plotPanel("schema-structure", "Feature 입력 구조", "제공된 표준화 입력의 전체 범위", { short: true, foot: "상수 feature를 임의로 제거하지 않습니다. 모델의 고정 113개 입력 순서를 유지합니다." })}
      <section class="panel"><div class="panel-head"><div><h2>수치 계약 점검</h2><p class="panel-subtitle">임상 범위 적절성과 별개입니다.</p></div>${badge(schema.numeric_contract || "unverifiable")}</div><dl class="definition-list"><div class="definition-row"><dt>Feature 일치</dt><dd>${n(schema.actual_count)} / ${n(schema.expected_count)}</dd></div><div class="definition-row"><dt>결측 셀</dt><dd>${n(missing)}</dd></div><div class="definition-row"><dt>비수치·비유한 셀</dt><dd>${n(invalid)}</dd></div><div class="definition-row"><dt>행 정렬 문제</dt><dd>${n(schema.row_issues?.alignment_or_order)}</dd></div></dl></section>
      </div>
      <section class="section"><div class="section-head"><div><h2>Feature 상세<span class="inline-count" id="feature-match-count">${quality.length}개</span></h2><p class="panel-subtitle">Min·max는 준비된 표준화 값입니다. 원래 임상 단위가 아닙니다.</p></div><label class="search-field"><span class="sr-only">Feature 검색</span><input class="text-input" id="feature-search" type="search" placeholder="Feature 이름 검색" autocomplete="off"></label></div>
      ${table(["Feature", { text: "결측", numeric: true }, { text: "유한 값", numeric: true }, { text: "Min", numeric: true }, { text: "Max", numeric: true }, "상수"], quality.map(([name, q]) => [e(name), n(q.missing), n(q.finite), decimal(q.min, 3), decimal(q.max, 3), q.constant ? "예" : "아니오"]), { scroll: true, label: "Feature 스키마 113개 상세", id: "feature-table", nowrap: true })}
      <p class="source-caption" id="feature-search-status" aria-live="polite">전체 ${quality.length}개 feature를 표시합니다.</p></section>`;
  }

  function edaEvidence() {
    const timing = details("prediction_timing");
    const lineage = details("preprocessing_lineage");
    const label = details("label_observability");
    const rows = [
      ["라벨 매핑", label.target_definition?.status || "unverifiable", "제공된 <30 / >30 / NO 매핑과 인코딩을 검증합니다."],
      ["원본 라벨 정합", label.raw_label_alignment?.status || "unverifiable", label.raw_label_alignment?.reason || "원본 readmitted 값 미제공"],
      ["30일 추적 완전성", label.followup_completeness?.status || "unverifiable", label.followup_completeness?.reason || "개별 추적 종료일 미제공"],
      ["타 병원 재입원 포착", label.outside_hospital_capture?.status || "unverifiable", label.outside_hospital_capture?.reason || "참여 병원 밖의 사건 포착 근거 없음"],
      ["산출물 무결성", lineage.asset_integrity?.status || "unverifiable", "파일 해시·불변 manifest 검증입니다. 누수 없음 인증이 아닙니다."],
      ["과거 전처리 fit 범위", lineage.historical_113_pipeline?.status || "unverifiable", "원래 113-feature 파이프라인의 독립 재현 및 fit 범위 근거 부족"],
      ["예측 시점", blocks.get("prediction_timing")?.status || "unverifiable", timing.prediction_time || "승인된 선언 없음"],
      ["Feature 가용시점", "unverifiable", `${n(timing.undocumented_features?.length)}개 feature의 검증된 가용시점 사전이 없습니다.`]
    ];
    return `<div class="analysis-grid">
      <section class="panel"><div class="panel-head"><div><h2>관찰가능성과 재현성</h2><p class="panel-subtitle">측정된 사실과 확인할 수 없는 항목을 분리합니다.</p></div></div>${table(["점검", "진단", "근거"], rows.map(([name, status, text]) => [e(name), badge(status), `<span class="text-cell">${e(text)}</span>`]), { label: "관찰가능성과 재현성 진단" })}</section>
      <section class="panel"><div class="panel-head"><h2>해석 범위</h2></div><div class="notice compact"><strong>독립 holdout 성능이 아닙니다.</strong><p>환자 중복, 개발 중 validation 재사용, 과거 target encoding·전처리의 불확실성이 있습니다.</p></div><dl class="definition-list"><div class="definition-row"><dt>새 학습</dt><dd>수행하지 않음</dd></div><div class="definition-row"><dt>새 전처리</dt><dd>수행하지 않음</dd></div><div class="definition-row"><dt>시점 후보 점검</dt><dd>${n(timing.completion_information_candidates?.length)}개 이름 기반 후보<br><small>실제 누수 확정이 아닙니다.</small></dd></div></dl><p class="chart-foot">보고서의 진단 실패·확인 불가는 workflow 자체가 실행되지 않았다는 뜻이 아닙니다.</p></section>
      </div>
      <section class="section"><div class="section-head"><h2>7개 EDA 블록의 원본 상태</h2></div>${blockTable()}</section>`;
  }

  function edaView() {
    const available = {
      cohort: Object.keys(counts).length > 0 && Object.keys(details("patient_overlap").overlaps || {}).length > 0,
      demographics: Object.keys(details("demographics").distributions || {}).length > 0,
      schema: Object.keys(details("schema").quality || {}).length > 0,
      evidence: ["label_observability", "preprocessing_lineage", "prediction_timing"].every(id => blocks.has(id))
    };
    const content = available[state.edaTab] ? { cohort: edaCohort, demographics: edaDemographics, schema: edaSchema, evidence: edaEvidence }[state.edaTab]() : `${awaitingContent("이 보기에 필요한 EDA 집계가 아직 게시되지 않았습니다.")}<section class="section">${blockTable()}</section>`;
    return `${heading("코호트·입력 품질·관찰가능성을 확인합니다. 집계 분석과 검증의 한계를 함께 봅니다.")}${dataKPIs()}${edaTabs()}<div id="eda-content" role="tabpanel" aria-labelledby="tab-${state.edaTab}">${content}</div>`;
  }

  function planView() {
    const metricRows = catalog.map(item => {
      const chosen = selected.has(item.id);
      const planned = (plan.items || []).find(x => metricID(x.id || x.block) === item.id);
      const thresholdText = ["f1", "recall", "precision"].includes(item.id) ? planThreshold == null ? badge(chosen ? "pending" : "not_selected", chosen ? "Threshold 대기" : "미선택") : e(String(planThreshold)) : item.id === "calibration_curve" ? e(plan.parameters?.n_bins ?? "기본 설정") : "불필요";
      return [
        `<span class="metric-name">${e(item.name)}${primary === item.id ? `<span class="metric-primary">${badge("selected", "주지표")}</span>` : ""}<small>${e(item.description)}</small></span>`,
        chosen ? badge("selected", "선택됨") : badge("not_selected", "미선택"), e(item.direction), thresholdText,
        `<span class="text-cell"><small>${e(chosen ? planned?.state === "eligible_planned_inputs" ? "필요 입력 생성 후 실행. 아직 성능값이 아닙니다." : planned?.reason || "원본 계획에 포함된 지표입니다." : "이 스냅샷의 선택 목록에 없습니다.")}</small></span>`
      ];
    });
    return `${heading("선택한 지표와 사람이 확정한 설정을 확인합니다. 계획과 계산된 성능은 구분합니다.")}
      ${kpis([
        { label: "선택 지표", value: n(selected.size), note: "현재 PR1 계획 기준" },
        { label: "주지표", value: primary ? metricInfo(primary).short || metricInfo(primary).name : "대기", note: primary ? "원본 계획의 확정값" : "사람의 확정 필요", word: true },
        { label: "Threshold", value: planThreshold == null ? "대기" : String(planThreshold), note: planThreshold == null ? "임의 기본값을 넣지 않음" : "데모 설정 / 임상 기준 아님", word: planThreshold == null },
        { label: "평가 입원", value: n(splitRows.validation), note: "Validation / test 미사용" }
      ])}
      <div class="analysis-grid">
        <section class="panel"><div class="panel-head"><div><h2>평가 지표</h2><p class="panel-subtitle">지원 지표 7종 중 이 스냅샷에서 ${selected.size}종 선택</p></div></div>${table(["지표", "선택", "방향", "설정", "실행 조건"], metricRows, { label: "평가 지표별 선택과 설정" })}</section>
        <section class="panel"><div class="panel-head"><h2>평가 범위</h2></div><dl class="definition-list"><div class="definition-row"><dt>분석 단위</dt><dd>Admission / encounter</dd></div><div class="definition-row"><dt>평가 split</dt><dd>Validation</dd></div><div class="definition-row"><dt>Test</dt><dd>이번 평가에 사용하지 않음</dd></div><div class="definition-row"><dt>Threshold</dt><dd>${planThreshold == null ? badge("pending", "미확정") : `${e(planThreshold)} / demo`}</dd></div><div class="definition-row"><dt>임상 배포</dt><dd>평가 범위 아님</dd></div></dl><div class="notice compact"><strong>해석 제한</strong><p>환자 중복·개발 재사용·추적 불완전성을 포함한 준비 데이터 평가입니다. 지표 선택이 독립 검증을 보장하지 않습니다.</p></div></section>
      </div>
      <section class="section"><div class="section-head"><h2>Threshold가 필요한 지표</h2></div><p class="text-body">F1·recall·precision은 적용한 threshold와 함께 해석합니다. 계획이 바뀌어도 이전 Local smoke 결과를 새 계획으로 다시 계산한 것처럼 표시하지 않습니다.</p></section>`;
  }

  function sourceUnavailable(description) {
    return `<div class="empty-state">${badge("pending", "원격 산출물 대기")}<h2>이 스냅샷에는 원격 결과가 없습니다.</h2><p>${e(description)}</p><p>위의 결과 출처에서 Local smoke를 선택하면 별도로 보존된 로컬 검증값을 볼 수 있습니다. 자동 조회·실행·승인을 요청하지 않습니다.</p></div>`;
  }

  function inferenceData() {
    const payload = sourcePayload();
    const root = sourceRoot(payload);
    const inf = inferenceOf(payload);
    const metrics = metricsOf(payload);
    const detail = metrics.find(x => x.details?.sample_count)?.details || {};
    return {
      payload, root, inf, metrics,
      rows: inf.rows ?? inf.sample_count ?? detail.sample_count ?? null,
      positive: detail.positive_count ?? null,
      negative: detail.negative_count ?? (detail.sample_count != null && detail.positive_count != null ? detail.sample_count - detail.positive_count : null),
      modelID: root.model_id || payload?.model_id || root.model_digest || root.model?.digest,
      dataID: root.data_id || payload?.data_id || root.data_digest,
      runtime: inf.runtime_scikit_learn || root.runtime_scikit_learn || root.runtime?.scikit_learn || null,
      featureCount: inf.feature_count ?? root.feature_count ?? null,
      modelClass: inf.model_class || root.model_class || root.model?.class || null,
      split: inf.split || detail.split || null
    };
  }

  function artifactTable(data) {
    const { inf } = data;
    const bool = value => value === true ? "수행" : value === false ? "수행하지 않음" : "미제공";
    const rows = [
      ["모델", e(data.modelClass || "미제공")],
      ["입력 feature", `${n(data.featureCount)}개`],
      ["평가 split / 입원", `${e(data.split || "미제공")} / ${n(data.rows)}`],
      ["Runtime / scikit-learn", e(data.runtime || "미제공")],
      ["Data digest", digest(data.dataID)],
      ["Model digest", digest(data.modelID)],
      ["추론", e(bool(inf.inference_performed))],
      ["새 학습", e(bool(inf.training_performed))],
      ["새 전처리", e(bool(inf.preprocessing_performed))],
      ...(state.source === "remote" && remoteRunURL() ? [
        ["원격 실행", `<a href="${e(remoteRunURL())}" target="_blank" rel="noopener">Actions run ${e(remoteRunURL().split("/").pop())}</a>`],
        ["계획 hash", digest(data.root.plan_hash)],
        ["Data image", digest(data.root.data_image)],
        ["Model image", digest(data.root.model_image)],
        ["PR2 리뷰", `${raw.remote_pr2?.url ? `<a href="${e(raw.remote_pr2.url)}" target="_blank" rel="noopener">PR2 #${e(pr2Number())}</a> / ` : ""}${e(pr2Review())}`],
        ["임상 사용 승인", data.root.clinical_use_approved === false ? "승인되지 않음" : "승인 근거 미제공"]
      ] : [])
    ];
    return `<div class="artifact-table">${table(["항목", "산출물 근거"], rows, { label: "모델·데이터 및 추론 재현성 근거" })}</div>`;
  }

  function modelView() {
    const title = heading("고정 Logistic Regression 모델과 입력 계약·런타임·불변 산출물을 확인합니다.");
    if (!sourcePayload()) return `${title}${sourceUnavailable("모델의 원격 추론 근거가 제공되기 전에는 로컬 검증을 원격 실행으로 표시하지 않습니다.")}`;
    const data = inferenceData();
    const exec = executionState();
    return `${title}${kpis([
      { label: "모델 입력", value: n(data.featureCount), note: "Feature / 고정 순서" },
      { label: "Runtime", value: data.runtime || "미제공", note: "scikit-learn", word: true },
      { label: "추론 입원", value: n(data.rows), note: data.split || "Split 미제공" },
      { label: "새 학습", value: data.inf.training_performed === false ? "없음" : data.inf.training_performed === true ? "수행" : "미제공", note: "보존 모델의 추론 검증", word: true }
    ])}
    <div class="analysis-grid">
      <section class="panel"><div class="panel-head"><div><h2>모델 산출물</h2><p class="panel-subtitle">현재 선택한 출처의 실제 기록만 표시합니다.</p></div>${badge(exec, exec === "completed" ? "추론 근거 있음" : undefined)}</div>${artifactTable(data)}</section>
      <section class="panel"><div class="panel-head"><h2>입력 계약과 해석</h2></div><dl class="definition-list"><div class="definition-row"><dt>고정 입력</dt><dd>${n(data.featureCount)}개 feature</dd></div><div class="definition-row"><dt>모델 변경</dt><dd>이 프리뷰에서 수행하지 않음</dd></div><div class="definition-row"><dt>계수·중요도</dt><dd>${badge("empty", "산출물 없음")}</dd></div><div class="definition-row"><dt>학습 곡선</dt><dd>${badge("empty", "새 학습 없음")}</dd></div></dl><div class="notice compact"><strong>${state.source === "local" ? "로컬 추론 근거입니다." : "선택한 원격 스냅샷입니다."}</strong><p>${state.source === "local" ? "원격 PR1의 현재 계획이 이 실행에 적용됐다는 의미가 아닙니다." : "PR2 생성·실행 성공과 사람의 리뷰·승인은 구분됩니다."}</p></div><p class="chart-foot">원본 모델 계수가 없으므로 feature importance 차트를 생성하지 않습니다.</p></section>
    </div>`;
  }

  function evaluationView() {
    const title = heading("평가 입력·표본 구성·실행 근거를 확인합니다. 데이터 진단과 실행 상태를 분리합니다.");
    if (!sourcePayload()) return `${title}${sourceUnavailable("원격 모델 실행과 PR2 산출물이 포함된 스냅샷을 받아야 평가 근거를 표시할 수 있습니다.")}`;
    const data = inferenceData();
    const threshold = thresholdOf();
    const exec = executionState();
    const classification = data.metrics.find(x => x.details?.counts?.tp != null)?.details?.counts;
    if (data.positive != null && data.negative != null) {
      queue("evaluation-composition", {
        grid: { left: 78, right: 60, top: 30, bottom: 35 },
        xAxis: valueAxis(),
        yAxis: categoryAxis(["Target 0", "Target 1"], { inverse: true }),
        series: [{ name: "입원 건수", type: "bar", barWidth: 32, data: [{ value: data.negative, itemStyle: { color: "#dddddd", decal: hatch } }, { value: data.positive, itemStyle: { color: "#000000" } }], label: { show: true, position: "right", color: "#000000", formatter: p => n(p.value) }, emphasis: { disabled: true } }]
      }, `선택한 ${state.source === "local" ? "로컬" : "원격"} 평가의 표본 구성. Target 0 ${n(data.negative)}건, target 1 ${n(data.positive)}건, 전체 ${n(data.rows)}건.`);
    }
    return `${title}${kpis([
      { label: "평가 입원", value: n(data.rows), note: data.split || "Split 미제공" },
      { label: "양성", value: n(data.positive), note: "현재 출처의 metric 집계" },
      { label: "완료 지표", value: data.metrics.length ? n(data.metrics.filter(x => x.status === "completed").length) : "—", note: "Calibration 포함" },
      { label: "Threshold", value: threshold == null ? "미제공" : String(threshold), note: state.source === "local" ? "Local smoke 설정" : "현재 원격 실행 설정", word: threshold == null }
    ])}
      ${data.metrics.length ? `<section class="section evaluation-metrics"><div class="section-head"><div><h2>계산된 평가지표</h2><p class="panel-subtitle">현재 선택한 출처의 실제 계산값입니다. 계획 지표와 구분합니다.</p></div>${badge(exec)}</div>${metricTable(data.metrics, threshold)}</section>` : ""}
      <div class="analysis-grid">
        ${data.positive != null && data.negative != null ? plotPanel("evaluation-composition", "평가 표본 구성", `${data.split || "Split 미제공"} / admission 기준`, { foot: "라벨 비율은 모델 성능이 아닙니다. 개인별 라벨·예측값은 이 프리뷰에 없습니다." }) : `<section class="panel"><div class="empty-state">${badge("empty")}<h2>평가 표본별 집계가 없습니다.</h2><p>제공된 입원·양성·음성 집계가 있을 때만 차트를 표시합니다.</p></div></section>`}
        <section class="panel"><div class="panel-head"><h2>실행과 진단</h2></div><dl class="definition-list"><div class="definition-row"><dt>선택 출처 실행</dt><dd>${badge(exec)}</dd></div><div class="definition-row"><dt>PR1 EDA 진단</dt><dd>${badge(raw.eda?.assessment_status || "unverifiable")}<br><small>별도 PR1 스냅샷의 점검 결과</small></dd></div><div class="definition-row"><dt>Test 평가</dt><dd>수행하지 않음</dd></div><div class="definition-row"><dt>PR2 리뷰</dt><dd>${state.source === "remote" && raw.remote_pr2 ? e(pr2Review()) : "로컬 검증과 별개"}</dd></div></dl><div class="notice compact"><strong>진단 실패 ≠ 실행 실패</strong><p>환자 중복 등 점검 기준을 충족하지 않아도 추론은 완료될 수 있습니다. 실행 완료가 임상적 유효성을 보장하지 않습니다.</p></div></section>
      </div>
      ${classification && threshold != null ? `<section class="section"><div class="section-head"><div><h2>Threshold ${e(threshold)} 분류 집계</h2><p class="panel-subtitle">양성 예측: p ≥ ${e(threshold)} / 교육용 데모 설정, 임상 기준 아님</p></div></div>${table(["실제 라벨", { text: "예측 0", numeric: true }, { text: "예측 1", numeric: true }], [["Target 0", `${n(classification.tn)} <small>TN</small>`, `${n(classification.fp)} <small>FP</small>`], ["Target 1", `${n(classification.fn)} <small>FN</small>`, `${n(classification.tp)} <small>TP</small>`]], { label: "실제 threshold의 혼동행렬 집계" })}</section>` : ""}
      <section class="section"><div class="section-head"><h2>평가 산출물과 재현성</h2></div>${artifactTable(data)}</section>`;
  }

  function calibrationPlots(points, data) {
    const usable = points.filter(x => x.count > 0 && x.mean_predicted_probability != null && x.observed_event_rate != null);
    queue("result-calibration", {
      grid: { left: 48, right: 24, top: 22, bottom: 52 },
      xAxis: valueAxis({ min: 0, max: 1, interval: .2, name: "평균 예측확률", nameLocation: "middle", nameGap: 32, nameTextStyle: { color: "#666666", fontSize: 10 }, axisLabel: { color: "#666666", fontSize: 10, formatter: v => Number(v).toFixed(1) } }),
      yAxis: valueAxis({ min: 0, max: 1, interval: .2, name: "관찰 양성률", nameTextStyle: { color: "#666666", fontSize: 10 }, axisLabel: { color: "#666666", fontSize: 10, formatter: v => Number(v).toFixed(1) } }),
      tooltip: { trigger: "item", confine: true, backgroundColor: "#ffffff", borderColor: "#bbbbbb", textStyle: { color: "#000000", fontSize: 11 }, formatter: p => p.seriesIndex === 0 ? "기준선 y = x" : `예측확률 ${decimal(p.value[0], 4)}<br>관찰 양성률 ${decimal(p.value[1], 4)}<br>입원 ${n(usable[p.dataIndex]?.count)}건` },
      series: [
        { name: "y = x 기준선", type: "line", data: [[0, 0], [1, 1]], symbol: "none", lineStyle: { type: "dashed", color: "#aaaaaa", width: 1.25 }, silent: true },
        { name: "관찰 calibration", type: "line", data: usable.map(x => [x.mean_predicted_probability, x.observed_event_rate]), symbol: "circle", symbolSize: 6, lineStyle: { color: "#000000", width: 2 }, itemStyle: { color: "#000000", borderColor: "#ffffff", borderWidth: 1 }, emphasis: { disabled: true } }
      ]
    }, `${state.source === "local" ? "Local smoke" : "원격 추론"} calibration. 가로축 평균 예측확률, 세로축 관찰 양성률, 두 축 0부터 1. ${points.length}개 bin, 각 bin 입원 수를 별도 표시합니다. Scalar AUC로 생성한 곡선이 아닙니다.`);
    queue("result-bins", {
      grid: { left: 58, right: 42, top: 14, bottom: 35 },
      xAxis: valueAxis({ axisLabel: { color: "#666666", fontSize: 10, formatter: v => v >= 1000 ? `${v / 1000}k` : n(v) } }),
      yAxis: categoryAxis(points.map(x => `${decimal(x.lower, 1)}–${decimal(x.upper, 1)}`), { inverse: true, axisLabel: { color: "#666666", fontSize: 10 } }),
      series: [{ type: "bar", name: "Bin 입원 수", barMaxWidth: 16, data: points.map(x => x.count), itemStyle: { color: "#aaaaaa", decal: hatch }, label: { show: true, position: "right", color: "#000000", fontSize: 10, formatter: p => n(p.value) }, emphasis: { disabled: true } }]
    }, `같은 calibration bin의 입원 건수. 총합 ${n(points.reduce((sum, x) => sum + x.count, 0))}, 선택 출처의 평가 입원 ${n(data.rows)}건. 빈 bin은 관찰률 0으로 대체하지 않습니다.`);
  }

  function metricTable(metrics, threshold) {
    const rows = catalog.map(info => {
      const metric = metrics.find(x => x.block === info.id);
      const isCurve = info.id === "calibration_curve";
      const isThreshold = ["f1", "recall", "precision"].includes(info.id);
      let value = "—";
      if (metric) value = isCurve ? `${calibrationOf(metrics).length} bins` : decimal(metric.value, 6);
      const denom = metric?.details?.denominator;
      const sample = metric?.details?.sample_count;
      const denomText = typeof denom === "number" ? n(denom) : isCurve && metric ? "Bin별 입원 수" : metric ? "미제공" : "—";
      return [
        `<span class="metric-name">${e(info.name)}<small>${e(info.description)}</small></span>`,
        `<span class="metric-number" title="${e(metric?.value)}">${e(value)}</span>`,
        metric ? badge(metric.status || "completed") : badge("empty"),
        isThreshold ? threshold == null ? badge("unverifiable", "Threshold 미제공") : e(threshold) : "불필요",
        e(denomText), sample != null ? n(sample) : "—"
      ];
    });
    return table(["지표", { text: "값", numeric: true }, "상태", "Threshold", { text: "계산 분모", numeric: true }, { text: "평가 입원", numeric: true }], rows, { label: "현재 출처의 지표 값과 계산 분모", nowrap: true });
  }

  function resultView() {
    const title = heading("계산된 지표와 calibration 집계를 확인합니다. 제공되지 않은 곡선은 생성하지 않습니다.");
    if (!sourcePayload()) return `${title}${sourceUnavailable("원격 평가 지표가 아직 제공되지 않았습니다. Scalar metric이나 곡선을 로컬 결과로 대신 채우지 않습니다.")}`;
    const data = inferenceData();
    const metrics = data.metrics;
    const points = calibrationOf(metrics);
    const threshold = thresholdOf();
    if (points.length) calibrationPlots(points, data);
    const f1 = valueOf("f1", metrics);
    const last = typeof f1 === "number" ? { label: "F1", value: decimal(f1), raw: f1, note: threshold == null ? "Threshold 미제공" : `Threshold ${threshold} / demo` } : { label: "Calibration", value: points.length ? `${points.length} bins` : "미제공", note: `${n(data.rows)} 입원의 집계`, word: true };
    const binSum = points.reduce((sum, p) => sum + (p.count || 0), 0);
    const totalMatch = points.length && data.rows != null && binSum === data.rows;
    return `${title}${kpis([
      { label: "Average precision / AP", value: decimal(valueOf("average_precision", metrics)), raw: valueOf("average_precision", metrics), note: state.source === "local" ? "별도 보존된 로컬 집계" : metricID(data.root.primary_metric) === "average_precision" ? "주지표 / 원격 스냅샷" : "원격 스냅샷" },
      { label: "ROC AUC", value: decimal(valueOf("roc_auc", metrics)), raw: valueOf("roc_auc", metrics), note: "Scalar / ROC 곡선 없음" },
      { label: "Brier score", value: decimal(valueOf("brier_score", metrics)), raw: valueOf("brier_score", metrics), note: "낮을수록 작은 확률 오차" }, last
    ])}
      ${state.source === "local" ? '<div class="notice compact"><strong>Local smoke 결과입니다.</strong><p>원격 최신 실행·최신 PR1 계획의 결과가 아닙니다. 새 계획으로 F1·recall·precision을 다시 계산한 값도 아닙니다.</p></div>' : `<div class="notice compact"><strong>원격 추론 산출물 / ${pr2Number() ? `PR2 #${e(pr2Number())}` : "PR2 생성 대기"} ${e(pr2Review())}</strong><p>Threshold ${threshold == null ? "미제공" : e(threshold)}는 교육용 데모 설정이며 임상 기준이 아닙니다. ${data.root.clinical_use_approved === false ? "임상 사용은 승인되지 않았습니다." : "임상 승인 근거는 별도 확인이 필요합니다."}</p></div>`}
      ${points.length ? `<div class="analysis-grid">
        ${plotPanel("result-calibration", "Calibration", `${data.split || "Split 미제공"} / ${n(data.rows)} 입원`, { tall: true, legend: '<div class="legend-inline"><span><i class="legend-mark" aria-hidden="true"></i>Bin별 관찰값</span><span><i class="legend-mark line" aria-hidden="true"></i>기준선 y = x</span></div>' })}
        ${plotPanel("result-bins", "Bin별 표본 수", `${points.length}개 구간 / 같은 calibration 집계`, { tall: true, foot: `${totalMatch ? "합계 일치" : "합계 확인 필요"}: ${n(binSum)} / ${n(data.rows)} 입원. 마지막 구간만 1.0을 포함합니다.` })}
      </div>` : `<div class="empty-state">${badge("empty")}<h2>Calibration 집계가 없습니다.</h2><p>실제 bin별 예측확률 평균·관찰률·표본 수를 받아야 그릴 수 있습니다.</p></div>`}
      <section class="section"><div class="section-head"><div><h2>평가 지표 상세</h2><p class="panel-subtitle">값·분모·적용 threshold는 현재 선택한 출처만 기준으로 합니다.</p></div>${badge(executionState())}</div>${metricTable(metrics, threshold)}</section>
      ${points.length ? `<details class="details"><summary>Calibration ${points.length}개 bin의 원자료</summary>${table(["구간", { text: "입원", numeric: true }, { text: "예측확률 평균", numeric: true }, { text: "관찰 양성률", numeric: true }], points.map(p => [`${decimal(p.lower, 1)} ≤ p ${p.upper_inclusive ? "≤" : "<"} ${decimal(p.upper, 1)}`, n(p.count), decimal(p.mean_predicted_probability, 6), decimal(p.observed_event_rate, 6)]), { label: "Calibration bin 원자료", nowrap: true })}<p>빈 bin의 평균과 관찰률은 null을 유지합니다.</p></details>` : ""}
      <section class="section"><div class="section-head"><h2>아직 제공되지 않은 분석</h2></div><ul class="unavailable-list"><li><strong>ROC / PR 곡선</strong><span>Threshold별 FPR·TPR·precision·recall 집계 필요. Scalar AUC·AP만으로 복원하지 않습니다.</span></li><li><strong>Threshold sweep / 하위집단 성능</strong><span>새 threshold·그룹별 metric 집계가 필요합니다. 인구통계 라벨 비율로 대체하지 않습니다.</span></li></ul></section>`;
  }

  const renderers = { target: targetView, eda: edaView, "evaluation-plan": planView, model: modelView, evaluation: evaluationView, result: resultView };

  function render({ focusHeading = false, focusControl = null } = {}) {
    charts.forEach(chart => chart.dispose());
    charts = [];
    scheduled = [];
    try {
      view.innerHTML = stageReady(state.stage) ? renderers[state.stage]() : pendingView();
      document.getElementById("topbar-stage").textContent = stages.find(x => x.id === state.stage).name;
      document.title = `${stages.find(x => x.id === state.stage).name} | Medical ML 연구 분석`;
      document.querySelectorAll("#stage-nav a").forEach(link => {
        const active = link.dataset.stage === state.stage;
        link.classList.toggle("active", active);
        if (active) link.setAttribute("aria-current", "page");
        else link.removeAttribute("aria-current");
        const ready = stageReady(link.dataset.stage);
        link.querySelector(".stage-status").textContent = ready ? "제공" : "대기";
      });
      const issueNumber = currentIssue();
      document.getElementById("workspace-name").textContent = issueNumber ? `Issue #${issueNumber}` : "연구 대기";
      document.getElementById("workspace-context").textContent = window.DashboardSnapshots.repositoryOf(raw)?.split("/").pop() || "등록된 Issue 없음";
      document.getElementById("topbar-project").textContent = issueNumber ? `Issue #${issueNumber}` : "Medical ML";
      view.dataset.stage = state.stage;
      view.dataset.source = sourceForStage();
      scheduled.forEach(fn => fn());
      bindView();
      if (focusHeading) {
        document.getElementById("stage-heading")?.focus({ preventScroll: true });
        window.scrollTo({ top: 0, left: 0, behavior: "instant" });
      }
      if (focusControl) document.getElementById(focusControl)?.focus({ preventScroll: true });
    } catch (error) {
      console.error("Dashboard view error", error);
      view.innerHTML = `<div class="empty-state">${badge("view_error")}<h1>집계 형식을 확인하세요.</h1><p>현재 파일을 표시하는 중 오류가 발생했습니다. Workflow 실행 실패와는 다릅니다.</p><p>${e(error.message)}</p></div>`;
    }
  }

  function bindView() {
    document.getElementById("result-source")?.addEventListener("change", event => {
      state.source = event.target.value;
      render({ focusControl: "result-source" });
    });
    document.querySelectorAll(".tab").forEach(tab => {
      tab.addEventListener("click", () => {
        state.edaTab = tab.dataset.tab;
        render({ focusControl: tab.id });
      });
      tab.addEventListener("keydown", event => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const options = ["cohort", "demographics", "schema", "evidence"];
        const current = options.indexOf(state.edaTab);
        const next = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1 : (current + (event.key === "ArrowRight" ? 1 : -1) + options.length) % options.length;
        state.edaTab = options[next];
        render({ focusControl: `tab-${state.edaTab}` });
      });
    });
    document.getElementById("demographic-split")?.addEventListener("change", event => {
      state.split = event.target.value;
      render({ focusControl: "demographic-split" });
    });
    document.getElementById("demographic-group")?.addEventListener("change", event => {
      state.demographic = event.target.value;
      render({ focusControl: "demographic-group" });
    });
    document.getElementById("feature-search")?.addEventListener("input", event => {
      const query = event.target.value.trim().toLowerCase();
      let shown = 0;
      document.querySelectorAll("#feature-table tbody tr").forEach(row => {
        row.hidden = !row.cells[0].textContent.toLowerCase().includes(query);
        if (!row.hidden) shown++;
      });
      document.getElementById("feature-match-count").textContent = `${shown}개`;
      document.getElementById("feature-search-status").textContent = `전체 ${Object.keys(details("schema").quality || {}).length}개 중 ${shown}개 feature를 표시합니다.`;
    });
  }

  const nav = document.getElementById("stage-nav");
  nav.innerHTML = stages.map(stage => `<a class="nav-link" href="#${stage.id}" data-stage="${stage.id}" aria-label="${e(stage.name)}"><svg class="stage-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round" stroke-linecap="round" aria-hidden="true">${stage.icon}</svg><span>${e(stage.name)}</span><span class="stage-status" aria-hidden="true">대기</span></a>`).join("");

  const sidebar = document.getElementById("sidebar");
  const scrim = document.getElementById("sidebar-scrim");
  const toggle = document.getElementById("sidebar-toggle");
  const mobile = matchMedia("(max-width:900px)");
  let menuOpen = false;
  function menu(open, restoreFocus = true) {
    menuOpen = open && mobile.matches;
    document.body.classList.toggle("menu-open", menuOpen);
    scrim.hidden = !menuOpen;
    toggle.setAttribute("aria-expanded", String(menuOpen));
    sidebar.inert = mobile.matches && !menuOpen;
    if (menuOpen) {
      sidebar.setAttribute("role", "dialog");
      sidebar.setAttribute("aria-modal", "true");
      sidebar.querySelector("#sidebar-close").focus();
    } else {
      sidebar.removeAttribute("role");
      sidebar.removeAttribute("aria-modal");
      if (restoreFocus && mobile.matches) toggle.focus();
    }
  }
  toggle.addEventListener("click", () => menu(!menuOpen));
  document.getElementById("sidebar-close").addEventListener("click", () => menu(false));
  scrim.addEventListener("click", () => menu(false));
  mobile.addEventListener("change", () => { menu(false, false); charts.forEach(chart => chart.resize()); });
  document.addEventListener("keydown", event => {
    if (!menuOpen) return;
    if (event.key === "Escape") { event.preventDefault(); menu(false); }
    if (event.key === "Tab") {
      const nodes = [...sidebar.querySelectorAll('a[href],button,summary,[tabindex="0"]')].filter(el => el.getClientRects().length && !el.disabled);
      const first = nodes[0], last = nodes[nodes.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  nav.addEventListener("click", event => {
    const link = event.target.closest("a[data-stage]");
    if (!link) return;
    if (menuOpen) menu(false, false);
    if (location.hash === link.hash) {
      state.stage = link.dataset.stage;
      render({ focusHeading: true });
    }
  });
  document.querySelector(".skip-link").addEventListener("click", event => {
    event.preventDefault();
    document.getElementById("main").focus();
  });
  function route(focusHeading) {
    const candidate = location.hash.slice(1);
    state.stage = stages.some(stage => stage.id === candidate) ? candidate : "target";
    if (candidate !== state.stage) history.replaceState(null, "", `#${state.stage}`);
    render({ focusHeading });
  }
  window.addEventListener("hashchange", () => route(true));
  window.addEventListener("resize", () => charts.forEach(chart => chart.resize()));
  menu(false, false);
  route(false);
  document.getElementById("snapshot-refresh").addEventListener("click", () => { void snapshots.refresh(); });
  snapshots.start();
  window.dashboardPreview = Object.freeze({
    getState: () => ({ ...state, chartCount: charts.length, source: sourceForStage(), dataID: ds.data_id, issue: currentIssue(), generatedAt: raw.generated_at, remoteAvailable: !!raw.remote_model_evaluation, metricCount: metricsOf().length, snapshotStatus: snapshots.getStatus() }),
    version: "2.0.0"
  });
})();
