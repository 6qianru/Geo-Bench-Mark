import { useEffect, useMemo, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

async function getJson(path) {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) throw new Error(`请求失败: ${response.status}`);
  return response.json();
}

async function postJson(path, body) {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
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

function fieldDefault(field) {
  if (field.type === "switch") return Boolean(field.default);
  if (field.type === "number") return field.default ?? "";
  return field.default ?? "";
}

// 值等于默认值（含空串/空数字）→ 灰字标记"未自定义"
function isDefaultValue(field, value) {
  if (field.type === "switch") return value === fieldDefault(field);
  if (field.type === "number") return value === "" || value === field.default;
  return value === fieldDefault(field);
}

function FieldInput({ field, value, onChange }) {
  const isDefault = isDefaultValue(field, value);
  const className = `form-input${isDefault ? " is-default" : ""}`;

  if (field.type === "select") {
    return (
      <select className={className} value={value} onChange={(event) => onChange(event.target.value)}>
        {field.options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    );
  }

  if (field.type === "textarea") {
    return (
      <textarea
        className={className}
        rows={field.rows || 3}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    );
  }

  if (field.type === "switch") {
    return (
      <label className="switch-label">
        <input type="checkbox" checked={Boolean(value)} onChange={(event) => onChange(event.target.checked)} />
        <span className="switch-text">{value ? "开" : "关"}</span>
      </label>
    );
  }

  if (field.type === "number") {
    return (
      <input
        type="number"
        className={className}
        value={value}
        onChange={(event) => onChange(event.target.value === "" ? "" : Number(event.target.value))}
      />
    );
  }

  return <input type="text" className={className} value={value} onChange={(event) => onChange(event.target.value)} />;
}

function FieldRow({ field, value, onChange }) {
  return (
    <label className="field-row">
      <span className="form-label">
        {field.label}
        {field.required && <span className="required-star"> *</span>}
        {field.help && <span className="field-help">{field.help}</span>}
      </span>
      <FieldInput field={field} value={value} onChange={onChange} />
    </label>
  );
}

function FixtureEditor({ listDef, fixtures, onChange }) {
  const fields = listDef.fields;
  const updateRow = (index, key, value) => {
    onChange(fixtures.map((row, i) => (i === index ? { ...row, [key]: value } : row)));
  };
  const addRow = () => {
    const row = {};
    for (const field of fields) row[field.key] = fieldDefault(field);
    onChange([...fixtures, row]);
  };
  const removeRow = (index) => onChange(fixtures.filter((_, i) => i !== index));

  return (
    <div className="fixture-editor">
      <span className="form-label">{listDef.label}</span>
      <table className="fixture-table">
        <thead>
          <tr>
            {fields.map((field) => (
              <th key={field.key}>
                {field.label}
                {field.required && <span className="required-star"> *</span>}
              </th>
            ))}
            <th />
          </tr>
        </thead>
        <tbody>
          {fixtures.length === 0 ? (
            <tr>
              <td className="muted" colSpan={fields.length + 1}>
                暂无数据集，点击下方"添加"新增
              </td>
            </tr>
          ) : (
            fixtures.map((row, index) => (
              <tr key={index}>
                {fields.map((field) => (
                  <td key={field.key}>
                    <FieldInput
                      field={field}
                      value={row[field.key]}
                      onChange={(value) => updateRow(index, field.key, value)}
                    />
                  </td>
                ))}
                <td className="fixture-remove">
                  <button className="btn-danger" onClick={() => removeRow(index)} title="删除该行">
                    删除
                  </button>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
      <button className="btn-outline" onClick={addRow}>
        + 添加 {listDef.row_label || "数据"}
      </button>
    </div>
  );
}

export default function ScenarioForm({ onClose, onSaved }) {
  const [schema, setSchema] = useState([]);
  const [schemaError, setSchemaError] = useState("");
  const [type, setType] = useState("agent_skill_test");
  const [values, setValues] = useState({});
  const [fixtures, setFixtures] = useState([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [overwritePending, setOverwritePending] = useState(null);

  useEffect(() => {
    getJson("/api/scenarios/schema")
      .then((data) => setSchema(data))
      .catch((err) => setSchemaError(err.message));
  }, []);

  // 对指定模式的字段补默认值（不覆盖用户已填的）
  function ensureDefaults(mode) {
    setValues((prev) => {
      const next = { ...prev };
      for (const group of schema) {
        if (!group.modes.includes(mode)) continue;
        for (const field of group.fields || []) {
          if (!(field.key in next)) next[field.key] = fieldDefault(field);
        }
      }
      // 执行器跟随模式：skill 模式→skill，agent 模式→orchestrator（模式的核心配置，切模式时同步）
      next["runtime.executor"] = mode === "agent_test" ? "orchestrator" : "skill";
      return next;
    });
  }

  // schema 加载完成后初始化默认值
  useEffect(() => {
    if (schema.length > 0) ensureDefaults(type);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [schema]);

  // 提交时可见字段的必填预检
  const missingRequired = useMemo(() => {
    const missing = [];
    for (const group of schema) {
      if (!group.modes.includes(type)) continue;
      for (const field of group.fields || []) {
        if (!field.required) continue;
        const value = values[field.key];
        const empty = value === undefined || value === null || value === "";
        if (empty) missing.push(field.label);
      }
    }
    if (type === "agent_skill_test") {
      const emptyRow = fixtures.some((row) => !row.id?.trim() || !row.path?.trim());
      if (fixtures.length > 0 && emptyRow) missing.push("数据集（每行 ID 与路径必填）");
    }
    return missing;
  }, [schema, type, values, fixtures]);

  function buildPayload() {
    const payload = {};
    const blocks = {};
    for (const [key, value] of Object.entries(values)) {
      if (value === undefined || value === null) continue;
      const field = allFields[key];
      let out = value;
      if (field?.type === "number") {
        if (value === "") out = fieldDefault(field);
      } else if ((field?.type === "text" || field?.type === "textarea") && value === "") {
        continue; // 空文本不提交，后端用模型默认值兜底
      }
      if (key.includes(".")) {
        const [block, fieldKey] = key.split(".");
        blocks[block] = blocks[block] || {};
        blocks[block][fieldKey] = out;
      } else {
        payload[key] = out;
      }
    }
    // fixtures 行：只提交填了 ID 与路径的行
    if (type === "agent_skill_test") {
      const rows = fixtures
        .filter((row) => row.id?.trim() && row.path?.trim())
        .map((row) => {
          const clean = {};
          for (const [k, v] of Object.entries(row)) {
            if (v !== undefined && v !== null && v !== "") clean[k] = v;
          }
          return clean;
        });
      if (rows.length > 0) blocks.data = { fixtures: rows };
    }
    return { ...payload, ...blocks };
  }

  const allFields = useMemo(() => {
    const map = {};
    for (const group of schema) {
      for (const field of group.fields || []) map[field.key] = field;
    }
    return map;
  }, [schema]);

  async function handleSubmit(overwrite) {
    setError("");
    if (missingRequired.length > 0) {
      setError(`必填项未填写：${missingRequired.join("、")}`);
      return;
    }
    setSaving(true);
    try {
      const payload = buildPayload();
      const result = await postJson("/api/scenarios", { scenario: payload, overwrite: Boolean(overwrite) });
      onSaved(result);
      onClose();
    } catch (err) {
      if (String(err.message).includes("已存在")) {
        setOverwritePending(buildPayload().id || "");
      }
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  if (schemaError) {
    return <pre className="error-box">加载表单定义失败：{schemaError}</pre>;
  }

  const visibleGroups = schema.filter((group) => group.modes.includes(type));

  return (
    <div className="scenario-form">
      <div className="form-head">
        <h3>新建 Scenario</h3>
        <button className="btn-outline" onClick={onClose} disabled={saving}>
          关闭
        </button>
      </div>

      {visibleGroups.map((group) => (
        <section className="form-group" key={group.key}>
          <h4>{group.label}</h4>
          {group.fields?.map((field) => (
            <FieldRow
              key={field.key}
              field={field}
              value={field.key in values ? values[field.key] : fieldDefault(field)}
              onChange={(value) => {
                if (field.key === "type") {
                  setValues((prev) => ({ ...prev, [field.key]: value }));
                  setType(value);
                  ensureDefaults(value);
                } else {
                  setValues((prev) => ({ ...prev, [field.key]: value }));
                }
              }}
            />
          ))}
          {group.list && (
            <FixtureEditor listDef={group.list} fixtures={fixtures} onChange={setFixtures} />
          )}
        </section>
      ))}

      {overwritePending && (
        <div className="overwrite-bar">
          <span>场景 <code>{overwritePending}</code> 已存在。</span>
          <button className="btn-danger" disabled={saving} onClick={() => handleSubmit(true)}>
            覆盖保存
          </button>
          <button className="btn-outline" disabled={saving} onClick={() => setOverwritePending(null)}>
            取消
          </button>
        </div>
      )}

      {error ? <pre className="error-box">{error}</pre> : null}

      <div className="form-actions">
        <button className="primary" onClick={() => handleSubmit(false)} disabled={saving}>
          {saving ? "保存中…" : "保存场景"}
        </button>
      </div>
    </div>
  );
}
