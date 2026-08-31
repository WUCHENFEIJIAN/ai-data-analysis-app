"use client";

import React, { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { Check, KeyRound, LoaderCircle, Monitor, Moon, PlugZap, Save, Settings2, Sun, X } from "lucide-react";

import { getModelConfiguration, listModelPresets, testModelConnection, updateModelConfiguration } from "../../lib/api";
import type { ModelConfiguration, ModelPreset, ThemeMode } from "../../types/api";
import { useTheme } from "./theme-provider";

const themeOptions: Array<{ id: ThemeMode; label: string; hint: string; icon: typeof Sun }> = [
  { id: "light", label: "浅色", hint: "清晰明亮", icon: Sun },
  { id: "dark", label: "深色", hint: "低亮度工作", icon: Moon },
  { id: "system", label: "跟随系统", hint: "自动适配", icon: Monitor },
];

export function SettingsButton() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className="settings-button" title="打开界面主题与模型设置" aria-label="打开界面主题与模型设置" data-tooltip="主题、模型与中转站配置" onClick={() => setOpen(true)}>
        <Settings2 size={15} /><span>设置</span>
      </button>
      {open && typeof document !== "undefined" && createPortal(<SettingsDialog onClose={() => setOpen(false)} />, document.body)}
    </>
  );
}

export function SettingsDialog({ onClose }: { onClose: () => void }) {
  const { mode, setMode } = useTheme();
  const [presets, setPresets] = useState<ModelPreset[]>([]);
  const [current, setCurrent] = useState<ModelConfiguration | null>(null);
  const [presetId, setPresetId] = useState("deepseek");
  const [apiBase, setApiBase] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [clearApiKey, setClearApiKey] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  useEffect(() => {
    let active = true;
    Promise.all([listModelPresets(), getModelConfiguration()])
      .then(([presetResponse, configuration]) => {
        if (!active) return;
        setPresets(presetResponse.items);
        setCurrent(configuration);
        const initialId = configuration?.preset_id ?? presetResponse.items[0]?.id ?? "custom";
        const initialPreset = presetResponse.items.find((item) => item.id === initialId);
        setPresetId(initialId);
        setApiBase(configuration?.api_base ?? initialPreset?.api_base ?? "");
        setModel(configuration?.model ?? initialPreset?.model ?? "");
      })
      .catch((caught) => setError(caught instanceof Error ? caught.message : "无法加载模型设置"))
      .finally(() => setLoading(false));
    return () => { active = false; };
  }, []);

  const selectedPreset = useMemo(() => presets.find((preset) => preset.id === presetId), [presets, presetId]);

  function selectPreset(nextId: string) {
    const nextPreset = presets.find((preset) => preset.id === nextId);
    setPresetId(nextId);
    if (!nextPreset) return;
    setApiBase(nextPreset.api_base);
    setModel(nextPreset.model);
    setNotice("");
    setError("");
  }

  async function saveModel() {
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const updated = await updateModelConfiguration({
        preset_id: presetId,
        api_base: apiBase.trim(),
        model: model.trim(),
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
        ...(clearApiKey ? { clear_api_key: true } : {}),
      });
      setCurrent(updated);
      setApiKey("");
      setClearApiKey(false);
      setNotice("模型配置已保存，下一次分析会立即使用新模型。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "模型配置保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function testConnection() {
    setTesting(true);
    setError("");
    setNotice("");
    try {
      const result = await testModelConnection({
        preset_id: presetId,
        api_base: apiBase.trim(),
        model: model.trim(),
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
      });
      setNotice(result.message);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "模型连接测试失败");
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="settings-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="settings-dialog" role="dialog" aria-modal="true" aria-labelledby="settings-title">
        <header className="settings-dialog__header">
          <div><div className="settings-dialog__eyebrow">Workspace preferences</div><h2 id="settings-title">设置</h2><p>控制界面氛围，并管理当前使用的模型服务。</p></div>
          <button type="button" className="tool-button" aria-label="关闭设置" onClick={onClose}><X size={17} /></button>
        </header>
        <div className="settings-dialog__body">
          <section className="settings-section">
            <div className="settings-section__heading"><div><h3>界面主题</h3><p>只影响当前浏览器，不会改变分析结果。</p></div><div className="settings-current-theme"><span>当前</span><strong>{themeOptions.find((item) => item.id === mode)?.label}</strong></div></div>
            <div className="theme-choice-grid" role="group" aria-label="选择界面主题">
              {themeOptions.map(({ id, label, hint, icon: Icon }) => (
                <button type="button" key={id} title={`切换为${label}主题`} className={`theme-choice ${mode === id ? "is-active" : ""}`} onClick={() => setMode(id)} aria-pressed={mode === id}>
                  <Icon size={16} /><span><strong>{label}</strong><small>{hint}</small></span>{mode === id && <Check className="theme-choice__check" size={15} />}
                </button>
              ))}
            </div>
          </section>
          <section className="settings-section">
            <div className="settings-section__heading"><div><h3>模型服务</h3><p>预设会填入官方地址；中转站请选择自定义并填写地址。</p></div><KeyRound size={17} /></div>
            {loading ? <div className="settings-loading">正在读取模型配置…</div> : (
              <>
                <div className="model-preset-grid">
                  {presets.map((preset) => <button type="button" key={preset.id} onClick={() => selectPreset(preset.id)} className={`model-preset ${preset.id === presetId ? "is-active" : ""}`}><span>{preset.label}</span><small>{preset.description}</small></button>)}
                </div>
                <div className="settings-form-grid">
                  <label>API 地址<input value={apiBase} onChange={(event) => setApiBase(event.target.value)} placeholder={selectedPreset?.requires_api_base ? "https://your-relay.example/v1" : "默认地址"} /></label>
                  <label>模型名称<input value={model} onChange={(event) => setModel(event.target.value)} placeholder="例如：deepseek-chat" /></label>
                  <label className="settings-form-grid__wide">API Key<input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={current?.api_key_hint ? `已配置 ${current.api_key_hint}，留空保持不变` : "仅保存在后端"} autoComplete="off" /></label>
                </div>
                {current?.api_key_configured && <label className="settings-clear-key"><input type="checkbox" checked={clearApiKey} onChange={(event) => setClearApiKey(event.target.checked)} />清除已保存的 API Key</label>}
                <div className="settings-dialog__note">当前协议：{selectedPreset?.provider === "anthropic" ? "Anthropic Messages" : "OpenAI-compatible"}。密钥不会回传到前端。</div>
              </>
            )}
          </section>
        </div>
        <footer className="settings-dialog__footer">
          <div className={error ? "settings-feedback is-error" : "settings-feedback"}>{error || notice || "设置会在本机保存并立即生效。"}</div>
          <div className="settings-dialog__actions"><button type="button" className="secondary-button" onClick={onClose}>取消</button><button type="button" className="secondary-button settings-test-button" disabled={loading || saving || testing} onClick={() => void testConnection()} title="只测试当前表单，不保存配置"><PlugZap size={14} />{testing ? <><LoaderCircle className="loading-spinner" size={14} />测试中…</> : "测试连接"}</button><button type="button" className="primary-button" disabled={loading || saving || testing} onClick={() => void saveModel()}><Save size={14} />{saving ? "保存中…" : "保存模型配置"}</button></div>
        </footer>
      </section>
    </div>
  );
}
