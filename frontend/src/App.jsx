import { useEffect, useRef, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

async function fetchJson(path, options) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed: ${response.status}`);
  }
  return response.json();
}

function ResultDetail({ result }) {
  const fr = result.final_output?.final_response || "";
  const toolCalls = result.tool_calls || [];
  const conversation = result.conversation || [];
  const assertions = result.assertions || [];
  const errors = result.errors || [];
  const externalInteractions = result.final_output?.external_interactions || [];
  const judge = result.judge || {};
  const hasJudge = typeof judge === "object" && Object.keys(judge).length > 0;
  const scorePct = judge.score != null ? Math.round(judge.score * 100) : null;
  // judge_mode 为迭代 2（LLM judge）新增字段；老数据没有时按 reason 反推状态标签
  const modeLabel = hasJudge
    ? {
        llm: "LLM 判定",
        "rule-skill": "规则判定·skill契约",
        "rule-agent": "规则判定·宽松",
        disabled: "已禁用",
        error: "判定错误",
      }[judge.judge_mode] ||
      (typeof judge.reason === "string" && judge.reason.includes("Judge disabled")
        ? "已禁用"
        : "规则判定")
    : "";

  const jsonBlock = (obj) =>
    obj && typeof obj === "object" ? JSON.stringify(obj, null, 2) : String(obj ?? "");

  return (
    <div className="result-detail">
      <div className="result-section">
        <h4>工具调用</h4>
        {toolCalls.length === 0 ? (
          <p className="muted">(无工具调用)</p>
        ) : (
          <ul className="list compact">
            {toolCalls.map((call, index) => (
              <li key={index}>
                <details className="tool-item">
                  <summary>
                    <span className="tool-arrow">▸</span>
                    <strong>{call.tool_name}</strong>
                    <span className={call.status === "success" ? "pill ok" : "pill bad"}>{call.status}</span>
                  </summary>
                  <div className="json-label">入参</div>
                  <pre>{jsonBlock(call.arguments)}</pre>
                  {call.result ? (
                    <>
                      <div className="json-label">出参</div>
                      <pre>{jsonBlock(call.result)}</pre>
                    </>
                  ) : null}
                </details>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="result-section">
        <h4>最终回答</h4>
        <pre className="result-text">{fr || "(空)"}</pre>
      </div>
      <div className="result-section">
        <h4>判定结果</h4>
        {!hasJudge ? (
          <p className="muted">(无 judge 结果)</p>
        ) : (
          <>
            <div className="judge-header">
              <span className={judge.passed ? "pill ok" : "pill bad"}>
                {judge.passed ? "passed" : "failed"}
              </span>
              {scorePct != null && <span className="judge-score">{scorePct}%</span>}
              <span className="pill mode">{modeLabel}</span>
              {judge.model ? <span className="muted">模型: {judge.model}</span> : null}
            </div>
            <div className="json-label">原因</div>
            <pre className="result-text">{judge.reason || ""}</pre>
            {judge.issues?.length ? (
              <>
                <div className="json-label">问题</div>
                <ul className="list compact">
                  {judge.issues.map((item, index) => (
                    <li key={index} className="assertion bad">
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
            {judge.suggestions?.length ? (
              <>
                <div className="json-label">建议</div>
                <ul className="list compact">
                  {judge.suggestions.map((item, index) => (
                    <li key={index}>
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
          </>
        )}
      </div>
      <div className="result-section">
        <h4>完整对话</h4>
        {conversation.length === 0 ? (
          <p className="muted">(无对话记录)</p>
        ) : (
          <ul className="list compact">
            {conversation.map((message, index) => (
              <li key={index}>
                <strong className={message.role === "assistant" ? "role-assistant" : "role-user"}>
                  {message.role}
                </strong>
                <pre className="msg-content">{jsonBlock(message.content)}</pre>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="result-section">
        <h4>外部智能体交互</h4>
        {externalInteractions.length === 0 ? (
          <p className="muted">(无外部交互记录)</p>
        ) : (
          <ul className="list compact">
            {externalInteractions.map((interaction, index) => (
              <li key={index}>
                <strong className="role-user">指令 {interaction.turn}</strong>
                <pre className="msg-content">{interaction.instruction || ""}</pre>
                <strong className="role-assistant">外部回答</strong>
                <pre className="msg-content">{interaction.response || ""}</pre>
                {interaction.tool_calls?.length ? (
                  <p className="muted">
                    外部工具调用: {interaction.tool_calls.map((call) => call.tool_name).join(", ")}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="result-section">
        <h4>断言</h4>
        {assertions.length === 0 ? (
          <p className="muted">(无断言)</p>
        ) : (
          <ul className="list compact">
            {assertions.map((item, index) => (
              <li key={index} className={item.passed ? "assertion ok" : "assertion bad"}>
                <span className="pill">{item.passed ? "passed" : "failed"}</span>
                <span>
                  {item.type}: {item.message}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="result-section">
        <h4>错误</h4>
        {errors.length === 0 ? (
          <p className="muted">(无错误)</p>
        ) : (
          errors.map((error, index) => <pre key={index} className="error-box">{error}</pre>)
        )}
      </div>
      <details className="result-section">
        <summary>原始 JSON</summary>
        <pre>{JSON.stringify(result, null, 2)}</pre>
      </details>
    </div>
  );
}

export default function App() {
  const [scenarios, setScenarios] = useState([]);
  const [skills, setSkills] = useState([]);
  const [reports, setReports] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [runHistory, setRunHistory] = useState([]); // 来自 DB 的持久化历史（/api/runs）
  const [historyDbError, setHistoryDbError] = useState("");
  const [selectedPath, setSelectedPath] = useState("");
  const [memoryEnabled, setMemoryEnabled] = useState(false);
  const [validation, setValidation] = useState(null);
  const [tools, setTools] = useState([]);
  const [currentTask, setCurrentTask] = useState(null);
  const [runResult, setRunResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const eventSourceRef = useRef(null);

  useEffect(() => {
    loadInitial();
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, []);

  async function loadInitial() {
    try {
      const [scenarioData, skillData, reportData, taskData] = await Promise.all([
        fetchJson("/api/scenarios"),
        fetchJson("/api/skills"),
        fetchJson("/api/reports"),
        fetchJson("/api/tasks"),
      ]);
      setScenarios(scenarioData);
      setSkills(skillData);
      setReports(reportData);
      setTasks(taskData);
      if (scenarioData.length > 0) {
        setSelectedPath(scenarioData[0].path);
      }
      await loadRunHistory();
    } catch (err) {
      setError(err.message);
    }
  }

  async function loadRunHistory() {
    try {
      const data = await fetchJson("/api/runs");
      if (data.available) {
        setRunHistory(data.runs);
        setHistoryDbError("");
      } else {
        setRunHistory([]);
        setHistoryDbError(data.error || "数据库不可用");
      }
    } catch (err) {
      setHistoryDbError(err.message);
    }
  }

  async function loadRunDetail(runId) {
    try {
      const data = await fetchJson(`/api/runs/${runId}`);
      // 后端返回 { run_id, scenario_id, json, md, ... }，json 是报告全文 JSON 字符串
      if (data.json) {
        setRunResult(JSON.parse(data.json));
      }
    } catch (err) {
      setError(err.message);
    }
  }

  async function validateScenario() {
    if (!selectedPath) return;
    setLoading(true);
    setError("");
    try {
      const data = await fetchJson("/api/validate", {
        method: "POST",
        body: JSON.stringify({ path: selectedPath }),
      });
      setValidation(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function listTools() {
    if (!selectedPath) return;
    setLoading(true);
    setError("");
    try {
      const data = await fetchJson("/api/list-tools", {
        method: "POST",
        body: JSON.stringify({ path: selectedPath }),
      });
      setTools(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function runScenario() {
    if (!selectedPath) return;
    setLoading(true);
    setError("");
    setRunResult(null);
    try {
      const task = await fetchJson("/api/tasks", {
        method: "POST",
        body: JSON.stringify({
          path: selectedPath,
          output_dir: "reports",
          memory_enabled: memoryEnabled,
        }),
      });
      setCurrentTask(task);
      setTasks((previous) => [task, ...previous.filter((item) => item.task_id !== task.task_id)]);
      subscribeToTask(task.task_id);
    } catch (err) {
      setError(err.message);
      setLoading(false);
    }
  }

  function subscribeToTask(taskId) {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }
    const source = new EventSource(`${API_BASE}/api/tasks/${taskId}/events`);
    eventSourceRef.current = source;

    const handleEvent = (event) => {
      const payload = JSON.parse(event.data);
      const task = payload.task;
      if (task) {
        setCurrentTask(task);
        setTasks((previous) => [task, ...previous.filter((item) => item.task_id !== task.task_id)]);
      }
      if (payload.payload?.result) {
        setRunResult(payload.payload.result);
      }
      if (payload.type === "task_finished") {
        if (task?.result) {
          setRunResult(task.result);
        }
        loadReportsOnly();
        loadRunHistory();
        setLoading(false);
        source.close();
      }
      if (payload.type === "error") {
        setError(payload.payload?.message || "Task failed.");
      }
    };

    source.addEventListener("task_created", handleEvent);
    source.addEventListener("task_started", handleEvent);
    source.addEventListener("executor_session", handleEvent);
    source.addEventListener("stage", handleEvent);
    source.addEventListener("executor_step", handleEvent);
    source.addEventListener("actor_reply", handleEvent);
    source.addEventListener("agent_result", handleEvent);
    source.addEventListener("assertions", handleEvent);
    source.addEventListener("judge", handleEvent);
    source.addEventListener("error", handleEvent);
    source.addEventListener("result", handleEvent);
    source.addEventListener("task_finished", handleEvent);
    source.onerror = () => {
      source.close();
      setLoading(false);
    };
  }

  async function loadReportsOnly() {
    try {
      const reportData = await fetchJson("/api/reports");
      setReports(reportData);
    } catch (err) {
      setError(err.message);
    }
  }

  const currentScenario = scenarios.find((scenario) => scenario.path === selectedPath);
  const stageResults = currentTask?.stage_results || {};

  return (
    <div className="app-shell">
      <header className="hero">
        <div>
          <p className="eyebrow">GeoSkillBench</p>
          <h1>GIS Agent Skill Evaluation Console</h1>
          <p className="lede">
            Task-based evaluation UI with a pluggable executor layer and live SSE progress.
          </p>
        </div>
        <div className="hero-card">
          <span>Backend</span>
          <strong>{API_BASE}</strong>
          <span className="status-pill">{currentTask?.status || "idle"}</span>
        </div>
      </header>

      <main className="layout">
        <section className="panel">
          <h2>Scenario</h2>
          <label className="field">
            <span>Select scenario</span>
            <select value={selectedPath} onChange={(event) => setSelectedPath(event.target.value)}>
              {scenarios.map((scenario) => (
                <option key={scenario.path} value={scenario.path}>
                  {scenario.name}
                </option>
              ))}
            </select>
          </label>
          <div className="config-grid">
            <label className="field">
              <span>Executor runtime</span>
              <div className="readonly-value">{currentScenario?.executor || "—"}</div>
            </label>
            <label className="checkbox-field">
              <input
                type="checkbox"
                checked={memoryEnabled}
                onChange={(event) => setMemoryEnabled(event.target.checked)}
              />
              <span>Memory enabled</span>
            </label>
          </div>
          <div className="button-row">
            <button onClick={validateScenario} disabled={loading || !selectedPath}>
              Validate
            </button>
            <button onClick={listTools} disabled={loading || !selectedPath}>
              List Tools
            </button>
            <button className="primary" onClick={runScenario} disabled={loading || !selectedPath}>
              Create Task
            </button>
          </div>
          {error ? <pre className="error-box">{error}</pre> : null}
          <div className="meta-grid">
            <div>
              <h3>Available scenarios</h3>
              <ul className="list">
                {scenarios.map((scenario) => (
                  <li key={scenario.path}>
                    <strong>{scenario.name}</strong>
                    <span>{scenario.path}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <h3>Available skills</h3>
              <ul className="list">
                {skills.map((skill) => (
                  <li key={skill.path}>
                    <strong>{skill.name}</strong>
                    <span>{skill.id}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        <section className="panel">
          <h2>Task Progress</h2>
          <div className="result-grid">
            <article>
              <h3>Current task</h3>
              <pre className="scroll-box">{currentTask ? JSON.stringify(currentTask, null, 2) : "No active task."}</pre>
            </article>
            <article>
              <h3>Stage state</h3>
              <ul className="list compact">
                {Object.entries(stageResults).length ? (
                  Object.entries(stageResults).map(([stage, status]) => (
                    <li key={stage} className={`stage-${status.toLowerCase()}`}>
                      <strong>
                        {stage}
                        <span className="stage-dot" />
                      </strong>
                      <span>{status}</span>
                    </li>
                  ))
                ) : (
                  <li className="stage-pending">
                    <strong>No stages yet</strong>
                    <span>Create a task to stream progress.</span>
                  </li>
                )}
              </ul>
            </article>
          </div>
        </section>

        <section className="panel">
          <h2>Inspector</h2>
          <div className="result-grid">
            <article>
              <h3>Validation</h3>
              <pre>{validation ? JSON.stringify(validation, null, 2) : "No validation yet."}</pre>
            </article>
            <article>
              <h3>Tools</h3>
              <pre>{tools.length ? JSON.stringify(tools, null, 2) : "No tool listing yet."}</pre>
            </article>
          </div>
        </section>

        <section className="panel">
          <h2>Run Result</h2>
          {runResult ? <ResultDetail result={runResult} /> : "No run completed yet."}
        </section>

        <section className="panel">
          <h2>Task History</h2>
          {historyDbError ? (
            <pre className="error-box">{historyDbError}</pre>
          ) : runHistory.length === 0 ? (
            <p className="muted">No runs recorded yet.</p>
          ) : (
            <ul className="list">
              {runHistory.map((run) => (
                <li key={run.run_id} onClick={() => loadRunDetail(run.run_id)} style={{ cursor: "pointer" }}>
                  <strong>{run.scenario_name || run.scenario_id}</strong>
                  <span>
                    {run.run_id.slice(0, 8)} · <span className={run.status === "passed" ? "pill ok" : "pill bad"}>{run.status}</span> ·{" "}
                    {run.executor || "—"} · {run.created_at?.slice(0, 19)?.replace("T", " ")}
                  </span>
                  <span className="muted">点击查看该次运行报告</span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="panel">
          <h2>Reports</h2>
          <ul className="list">
            {reports.map((report) => (
              <li key={report.scenario_id}>
                <strong>{report.scenario_id}</strong>
                <span>{report.json_path}</span>
              </li>
            ))}
          </ul>
        </section>
      </main>
    </div>
  );
}
