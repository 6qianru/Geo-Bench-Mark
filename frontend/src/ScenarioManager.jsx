import { useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || ""; // 空→相对路径（生产 nginx 反代 /api）

async function apiFetch(path, { method = "GET", body } = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    data = text;
  }
  if (!response.ok) {
    const detail = typeof data === "object" && data.detail ? data.detail : text;
    throw new Error(detail);
  }
  return data;
}

export default function ScenarioManager({ scenarios, onClose, onSaved }) {
  const [editing, setEditing] = useState(null); // { id, content }
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function startEdit(scenario) {
    setError("");
    try {
      const data = await apiFetch(`/api/scenarios/${scenario.id}`);
      setEditing({ id: scenario.id, content: data.content });
    } catch (err) {
      setError(err.message);
    }
  }

  async function saveEdit() {
    setBusy(true);
    setError("");
    try {
      await apiFetch(`/api/scenarios/${editing.id}`, { method: "PUT", body: { content: editing.content } });
      setEditing(null);
      onSaved();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function removeScenario(scenario) {
    if (!window.confirm(`删除场景「${scenario.name}」（${scenario.id}）？该操作不可恢复。`)) return;
    setBusy(true);
    setError("");
    try {
      await apiFetch(`/api/scenarios/${scenario.id}`, { method: "DELETE" });
      onSaved();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="scenario-manager">
      <div className="form-head">
        <h4>管理 Scenario</h4>
        <button className="btn-outline" onClick={onClose} disabled={busy}>
          关闭
        </button>
      </div>

      {error ? <pre className="error-box">{error}</pre> : null}

      {editing ? (
        <div className="manager-edit">
          <span className="form-label">
            编辑 {editing.id}（YAML 原文，id 即文件名不可修改）
          </span>
          <textarea
            className="yaml-editor"
            value={editing.content}
            onChange={(event) => setEditing({ ...editing, content: event.target.value })}
            spellCheck={false}
          />
          <div className="form-actions">
            <button className="primary" onClick={saveEdit} disabled={busy}>
              {busy ? "保存中…" : "保存"}
            </button>
            <button className="btn-outline" onClick={() => setEditing(null)} disabled={busy}>
              取消
            </button>
          </div>
        </div>
      ) : scenarios.length === 0 ? (
        <p className="muted">暂无场景。</p>
      ) : (
        <ul className="list compact">
          {scenarios.map((scenario) => (
            <li key={scenario.id} className="manager-row">
              <div className="manager-info">
                <strong>{scenario.name}</strong>
                <span>{scenario.path}</span>
              </div>
              <div className="manager-actions">
                <button className="btn-outline" onClick={() => startEdit(scenario)} disabled={busy}>
                  编辑
                </button>
                <button className="btn-danger" onClick={() => removeScenario(scenario)} disabled={busy}>
                  删除
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
