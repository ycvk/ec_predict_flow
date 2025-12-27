import { useEffect, useMemo, useState } from "react";

import { api } from "@/lib/api";
import type { PipelineTemplateResponse } from "@/lib/types";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

type EditorMode = "create" | "edit" | "copy";

function prettyJson(value: unknown) {
  try {
    return JSON.stringify(value ?? {}, null, 2);
  } catch {
    return "{}";
  }
}

export function TemplatesPage() {
  const [templates, setTemplates] = useState<PipelineTemplateResponse[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<EditorMode>("create");
  const [editing, setEditing] = useState<PipelineTemplateResponse | null>(null);

  const [name, setName] = useState("");
  const [configText, setConfigText] = useState("{}");
  const [isDefault, setIsDefault] = useState(false);

  const defaultTemplate = useMemo(() => templates.find((t) => t.is_default), [templates]);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const tpls = await api.listTemplates();
      setTemplates(tpls);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function openCreate() {
    setMode("create");
    setEditing(null);
    setName("");
    setConfigText(
      prettyJson({
        steps: [
          "data_download",
          "feature_calculation",
          "label_calculation",
          "model_training",
          "model_interpretation",
          "model_analysis",
          "backtest_construction",
        ],
        feature_calculation: { alpha_types: ["alpha158"] },
      }),
    );
    setIsDefault(false);
    setOpen(true);
  }

  function openEdit(tpl: PipelineTemplateResponse) {
    setMode("edit");
    setEditing(tpl);
    setName(tpl.name);
    setConfigText(prettyJson(tpl.config));
    setIsDefault(Boolean(tpl.is_default));
    setOpen(true);
  }

  function openCopy(tpl: PipelineTemplateResponse) {
    setMode("copy");
    setEditing(tpl);
    setName(`${tpl.name} - copy`);
    setConfigText(prettyJson(tpl.config));
    setIsDefault(false);
    setOpen(true);
  }

  async function onSave() {
    setError(null);
    try {
      const parsedConfig = configText.trim() ? (JSON.parse(configText) as Record<string, unknown>) : {};
      if (mode === "create" || mode === "copy") {
        await api.createTemplate({ name, config: parsedConfig as any, is_default: isDefault });
      } else if (mode === "edit" && editing) {
        await api.updateTemplate(editing.template_id, { name, config: parsedConfig as any, is_default: isDefault });
      }
      setOpen(false);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function onDelete(tpl: PipelineTemplateResponse) {
    setError(null);
    try {
      await api.deleteTemplate(tpl.template_id);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function onSetDefault(tpl: PipelineTemplateResponse) {
    setError(null);
    try {
      await api.setDefaultTemplate(tpl.template_id);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="grid gap-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Pipeline 模板</CardTitle>
          <CardDescription>
            保存“一键跑完”的高级配置。默认模板：{defaultTemplate ? defaultTemplate.name : "（无）"}
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={openCreate}>新建模板</Button>
            <Button variant="secondary" onClick={refresh} disabled={loading}>
              刷新
            </Button>
          </div>

          {error ? <div className="text-sm text-destructive">错误：{error}</div> : null}

          {templates.length === 0 ? (
            <div className="text-sm text-muted-foreground">暂无模板</div>
          ) : (
            <div className="grid gap-2">
              {templates.map((t) => (
                <div key={t.template_id} className="flex flex-wrap items-center justify-between gap-3 rounded-md border p-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <div className="truncate text-sm font-medium">{t.name}</div>
                      {t.is_default ? <Badge variant="success">默认</Badge> : null}
                    </div>
                    <div className="truncate font-mono text-xs text-muted-foreground">{t.template_id}</div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Button variant="secondary" onClick={() => openEdit(t)}>
                      编辑
                    </Button>
                    <Button variant="secondary" onClick={() => openCopy(t)}>
                      复制
                    </Button>
                    <Button variant="secondary" onClick={() => onSetDefault(t)} disabled={t.is_default}>
                      设为默认
                    </Button>
                    <Button variant="destructive" onClick={() => onDelete(t)}>
                      删除
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}

          <Dialog open={open} onOpenChange={setOpen}>
            <DialogContent className="max-w-2xl">
              <DialogHeader>
                <DialogTitle>
                  {mode === "create" ? "新建模板" : mode === "copy" ? "复制模板" : "编辑模板"}
                </DialogTitle>
              </DialogHeader>

              <div className="grid gap-4">
                <div className="grid gap-2">
                  <Label>名称</Label>
                  <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：alpha158-default" />
                </div>

                <div className="grid gap-2">
                  <Label>config（JSON）</Label>
                  <Textarea
                    value={configText}
                    onChange={(e) => setConfigText(e.target.value)}
                    className="min-h-64 font-mono text-xs"
                  />
                </div>

                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={isDefault} onChange={(e) => setIsDefault(e.target.checked)} />
                  设为默认模板
                </label>
              </div>

              <DialogFooter>
                <Button variant="secondary" onClick={() => setOpen(false)}>
                  取消
                </Button>
                <Button onClick={onSave} disabled={!name.trim()}>
                  保存
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </CardContent>
      </Card>
    </div>
  );
}
