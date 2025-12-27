import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "@/lib/api";
import {
  calcExpectedWindows,
  estimateTotalBars,
  formatBarsToApproxDuration,
  parseIntervalToMinutes,
  recommendLabelThreshold,
  recommendWalkForward,
} from "@/lib/recommendations";
import type { JsonValue, PipelineTemplateResponse, RunResponse } from "@/lib/types";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

const SYMBOL_OPTIONS = ["BTCUSDT", "ETHUSDT"] as const;
const INTERVAL_OPTIONS = ["1m", "5m", "15m", "1h", "4h", "1d"] as const;
const TEMPLATE_AUTO = "__auto__";
const TEMPLATE_NONE = "__none__";

const DEFAULT_PIPELINE_CONFIG: Record<string, JsonValue> = {
  steps: [
    "data_download",
    "feature_calculation",
    "label_calculation",
    "model_training",
    "model_interpretation",
    "model_analysis",
    "backtest_construction",
    "walk_forward_evaluation",
  ],
  data_download: {
    symbol: "",
    start_date: "",
    end_date: "",
    interval: "1m",
    proxy: null,
  },
  feature_calculation: {
    alpha_types: ["alpha158"],
    instrument_name: null,
  },
  label_calculation: {
    window: 29,
    look_forward: 10,
    label_type: "up",
    filter_type: "rsi",
    threshold: null,
  },
  model_training: {
    num_boost_round: 500,
    num_threads: 4,
  },
  model_interpretation: {
    max_samples: 5000,
    max_display: 20,
  },
  model_analysis: {
    selected_features: null,
    max_features: 8,
    max_depth: 3,
    min_samples_split: 100,
    min_samples_leaf: 50,
    min_rule_samples: 50,
    label_threshold: null,
  },
  backtest_construction: {
    look_forward_bars: 10,
    win_profit: 4.0,
    loss_cost: 5.0,
    initial_balance: 1000.0,
    pnl_mode: "price",
    fee_rate: 0.0004,
    slippage_bps: 0.0,
    position_fraction: 1.0,
    position_notional: null,
    backtest_type: "long",
    filter_type: "rsi",
    order_interval_minutes: 30,
    min_rule_confidence: 0.0,
  },
  walk_forward_evaluation: {
    enabled: true,
    train_bars: 20000,
    test_bars: 5000,
    step_bars: 5000,
    max_windows: 10,
  },
};

const ALPHA_TYPE_OPTIONS = [
  {
    value: "alpha158",
    label: "alpha158（新手推荐）",
    description: "158 个常用技术指标因子，速度快、最容易先跑通。",
  },
  {
    value: "alpha216",
    label: "alpha216",
    description: "更丰富的技术指标特征，可能更慢，也更容易过拟合。",
  },
  {
    value: "alpha101",
    label: "alpha101",
    description: "经典的 101 因子集合（偏学术/研究向）。",
  },
  {
    value: "alpha191",
    label: "alpha191",
    description: "更大的因子集合（更慢，更像“研究模式”）。",
  },
  {
    value: "alpha_ch",
    label: "alpha_ch",
    description: "历史兼容/实验用因子集合（不确定性更高）。",
  },
] as const;

const LABEL_TYPE_OPTIONS = [
  { value: "up", label: "上涨（预测未来涨的概率）" },
  { value: "down", label: "下跌（预测未来跌的概率）" },
] as const;

const FILTER_TYPE_OPTIONS = [
  { value: "rsi", label: "RSI（更常见）" },
  { value: "cti", label: "CTI（更激进）" },
] as const;

const BACKTEST_TYPE_OPTIONS = [
  { value: "long", label: "做多（涨了赚）" },
  { value: "short", label: "做空（跌了赚）" },
] as const;

const PNL_MODE_OPTIONS = [
  { value: "price", label: "按价格变化计算（推荐）" },
  { value: "fixed", label: "固定赢亏（示例/教学用）" },
] as const;

function formatDefaultValue(value: unknown): string {
  if (value === null || value === undefined) return "（空）";
  if (Array.isArray(value)) return value.map((v) => String(v)).join(", ");
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

function isPlainObject(value: unknown): value is Record<string, JsonValue> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function deepMerge(base: Record<string, JsonValue>, patch: Record<string, JsonValue>): Record<string, JsonValue> {
  const merged: Record<string, JsonValue> = { ...(base || {}) };
  for (const [k, v] of Object.entries(patch || {})) {
    const prev = merged[k];
    if (isPlainObject(prev) && isPlainObject(v)) {
      merged[k] = deepMerge(prev, v);
    } else {
      merged[k] = v;
    }
  }
  return merged;
}

function formatStatusBadge(status: string) {
  const normalized = String(status || "").toLowerCase();
  if (normalized === "succeeded") return <Badge variant="success">已完成</Badge>;
  if (normalized === "failed") return <Badge variant="destructive">失败</Badge>;
  if (normalized === "canceled") return <Badge variant="secondary">已取消</Badge>;
  if (normalized === "running") return <Badge variant="secondary">运行中</Badge>;
  if (normalized === "queued") return <Badge variant="secondary">排队中</Badge>;
  return <Badge variant="secondary">{status}</Badge>;
}

export function HomePage() {
  const navigate = useNavigate();

  const [symbol, setSymbol] = useState<(typeof SYMBOL_OPTIONS)[number]>("BTCUSDT");
  const [startDate, setStartDate] = useState("2025-01-01");
  const [endDate, setEndDate] = useState("2025-02-01");
  const [interval, setInterval] = useState<(typeof INTERVAL_OPTIONS)[number]>("1m");

  const [templates, setTemplates] = useState<PipelineTemplateResponse[]>([]);
  const [templateChoice, setTemplateChoice] = useState<string>(TEMPLATE_AUTO);

  const [overridesText, setOverridesText] = useState("");
  const [runs, setRuns] = useState<RunResponse[]>([]);

  const [useFeatureOverrides, setUseFeatureOverrides] = useState(false);
  const [alphaTypes, setAlphaTypes] = useState<string[]>(["alpha158"]);
  const [instrumentName, setInstrumentName] = useState("");

  const [useLabelOverrides, setUseLabelOverrides] = useState(false);
  const [labelWindow, setLabelWindow] = useState("29");
  const [labelLookForward, setLabelLookForward] = useState("10");
  const [labelType, setLabelType] = useState<(typeof LABEL_TYPE_OPTIONS)[number]["value"]>("up");
  const [labelFilterType, setLabelFilterType] = useState<(typeof FILTER_TYPE_OPTIONS)[number]["value"]>("rsi");
  const [labelThreshold, setLabelThreshold] = useState("");

  const [useTrainingOverrides, setUseTrainingOverrides] = useState(false);
  const [numBoostRound, setNumBoostRound] = useState("500");
  const [numThreads, setNumThreads] = useState("4");

  const [useInterpretationOverrides, setUseInterpretationOverrides] = useState(false);
  const [shapMaxSamples, setShapMaxSamples] = useState("5000");
  const [shapMaxDisplay, setShapMaxDisplay] = useState("20");

  const [useAnalysisOverrides, setUseAnalysisOverrides] = useState(false);
  const [selectedFeaturesText, setSelectedFeaturesText] = useState("");
  const [maxFeatures, setMaxFeatures] = useState("8");
  const [maxDepth, setMaxDepth] = useState("3");
  const [minSamplesSplit, setMinSamplesSplit] = useState("100");
  const [minSamplesLeaf, setMinSamplesLeaf] = useState("50");
  const [minRuleSamples, setMinRuleSamples] = useState("50");
  const [analysisLabelThreshold, setAnalysisLabelThreshold] = useState("");

  const [useBacktestOverrides, setUseBacktestOverrides] = useState(false);
  const [positionFraction, setPositionFraction] = useState("1.0");
  const [positionNotional, setPositionNotional] = useState("");
  const [orderIntervalMinutes, setOrderIntervalMinutes] = useState("30");
  const [lookForwardBars, setLookForwardBars] = useState("10");
  const [pnlMode, setPnlMode] = useState<(typeof PNL_MODE_OPTIONS)[number]["value"]>("price");
  const [backtestType, setBacktestType] = useState<(typeof BACKTEST_TYPE_OPTIONS)[number]["value"]>("long");
  const [backtestFilterType, setBacktestFilterType] = useState<(typeof FILTER_TYPE_OPTIONS)[number]["value"]>("rsi");
  const [minRuleConfidence, setMinRuleConfidence] = useState("0.0");
  const [winProfit, setWinProfit] = useState("4.0");
  const [lossCost, setLossCost] = useState("5.0");
  const [initialBalance, setInitialBalance] = useState("1000.0");
  const [feeRate, setFeeRate] = useState("0.0004");
  const [slippageBps, setSlippageBps] = useState("0");

  const [useWalkForwardOverrides, setUseWalkForwardOverrides] = useState(false);
  const [walkForwardEnabled, setWalkForwardEnabled] = useState(true);
  const [trainBars, setTrainBars] = useState("20000");
  const [testBars, setTestBars] = useState("5000");
  const [stepBars, setStepBars] = useState("5000");
  const [maxWindows, setMaxWindows] = useState("10");

  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const defaultTemplate = useMemo(() => templates.find((t) => t.is_default), [templates]);

  const selectedTemplate = useMemo(() => {
    if (templateChoice === TEMPLATE_NONE) return null;
    if (templateChoice === TEMPLATE_AUTO) return defaultTemplate ?? null;
    return templates.find((t) => t.template_id === templateChoice) ?? null;
  }, [templateChoice, templates, defaultTemplate]);

  const basePipelineConfig = useMemo(() => {
    const merged = deepMerge(DEFAULT_PIPELINE_CONFIG, (selectedTemplate?.config ?? {}) as Record<string, JsonValue>);
    return deepMerge(merged, {
      data_download: {
        ...(merged as any).data_download,
        symbol,
        start_date: startDate,
        end_date: endDate,
        interval,
      },
    });
  }, [selectedTemplate, symbol, startDate, endDate, interval]);

  const baseFeatureCfg = (basePipelineConfig as any)?.feature_calculation ?? {};
  const baseLabelCfg = (basePipelineConfig as any)?.label_calculation ?? {};
  const baseTrainCfg = (basePipelineConfig as any)?.model_training ?? {};
  const baseInterpCfg = (basePipelineConfig as any)?.model_interpretation ?? {};
  const baseAnalysisCfg = (basePipelineConfig as any)?.model_analysis ?? {};
  const baseBacktestCfg = (basePipelineConfig as any)?.backtest_construction ?? {};
  const baseWfCfg = (basePipelineConfig as any)?.walk_forward_evaluation ?? {};

  const minutesPerBar = useMemo(() => parseIntervalToMinutes(interval), [interval]);
  const estimatedTotalBars = useMemo(
    () => estimateTotalBars({ startDate, endDate, interval }),
    [startDate, endDate, interval],
  );

  const effectiveLabelType = (useLabelOverrides ? labelType : (baseLabelCfg.label_type ?? "up")) as string;
  const effectiveLabelFilterType = (useLabelOverrides ? labelFilterType : (baseLabelCfg.filter_type ?? "rsi")) as string;
  const recommendedThreshold = useMemo(
    () => recommendLabelThreshold({ filterType: effectiveLabelFilterType, labelType: effectiveLabelType }),
    [effectiveLabelFilterType, effectiveLabelType],
  );

  const recommendedWalkForward = useMemo(() => {
    if (estimatedTotalBars === null) return null;
    if (estimatedTotalBars <= 0) return null;
    return recommendWalkForward({ totalBars: estimatedTotalBars });
  }, [estimatedTotalBars]);

  const expectedWindows = useMemo(() => {
    const totalBars = estimatedTotalBars;
    const train = Number(trainBars);
    const test = Number(testBars);
    const step = Number(stepBars);
    if (totalBars === null) return null;
    if (!Number.isFinite(train) || !Number.isFinite(test) || !Number.isFinite(step)) return null;
    return calcExpectedWindows({ totalBars, trainBars: train, testBars: test, stepBars: step });
  }, [estimatedTotalBars, trainBars, testBars, stepBars]);

  useEffect(() => {
    if (!useFeatureOverrides) {
      const a = Array.isArray(baseFeatureCfg.alpha_types) ? baseFeatureCfg.alpha_types : ["alpha158"];
      setAlphaTypes(a.map((t: unknown) => String(t)).filter(Boolean));
      setInstrumentName(baseFeatureCfg.instrument_name ? String(baseFeatureCfg.instrument_name) : "");
    }
  }, [useFeatureOverrides, baseFeatureCfg.alpha_types, baseFeatureCfg.instrument_name]);

  useEffect(() => {
    if (!useLabelOverrides) {
      setLabelWindow(String(baseLabelCfg.window ?? 29));
      setLabelLookForward(String(baseLabelCfg.look_forward ?? 10));
      setLabelType((baseLabelCfg.label_type ?? "up") as any);
      setLabelFilterType((baseLabelCfg.filter_type ?? "rsi") as any);
      setLabelThreshold(baseLabelCfg.threshold === null || baseLabelCfg.threshold === undefined ? "" : String(baseLabelCfg.threshold));
    }
  }, [useLabelOverrides, baseLabelCfg.window, baseLabelCfg.look_forward, baseLabelCfg.label_type, baseLabelCfg.filter_type, baseLabelCfg.threshold]);

  useEffect(() => {
    if (!useTrainingOverrides) {
      setNumBoostRound(String(baseTrainCfg.num_boost_round ?? 500));
      setNumThreads(String(baseTrainCfg.num_threads ?? 4));
    }
  }, [useTrainingOverrides, baseTrainCfg.num_boost_round, baseTrainCfg.num_threads]);

  useEffect(() => {
    if (!useInterpretationOverrides) {
      setShapMaxSamples(String(baseInterpCfg.max_samples ?? 5000));
      setShapMaxDisplay(String(baseInterpCfg.max_display ?? 20));
    }
  }, [useInterpretationOverrides, baseInterpCfg.max_samples, baseInterpCfg.max_display]);

  useEffect(() => {
    if (!useAnalysisOverrides) {
      const selected = Array.isArray(baseAnalysisCfg.selected_features) ? baseAnalysisCfg.selected_features : null;
      setSelectedFeaturesText(selected ? selected.map((v: unknown) => String(v)).join("\n") : "");
      setMaxFeatures(String(baseAnalysisCfg.max_features ?? 8));
      setMaxDepth(String(baseAnalysisCfg.max_depth ?? 3));
      setMinSamplesSplit(String(baseAnalysisCfg.min_samples_split ?? 100));
      setMinSamplesLeaf(String(baseAnalysisCfg.min_samples_leaf ?? 50));
      setMinRuleSamples(String(baseAnalysisCfg.min_rule_samples ?? 50));
      setAnalysisLabelThreshold(
        baseAnalysisCfg.label_threshold === null || baseAnalysisCfg.label_threshold === undefined ? "" : String(baseAnalysisCfg.label_threshold),
      );
    }
  }, [
    useAnalysisOverrides,
    baseAnalysisCfg.selected_features,
    baseAnalysisCfg.max_features,
    baseAnalysisCfg.max_depth,
    baseAnalysisCfg.min_samples_split,
    baseAnalysisCfg.min_samples_leaf,
    baseAnalysisCfg.min_rule_samples,
    baseAnalysisCfg.label_threshold,
  ]);

  useEffect(() => {
    if (!useBacktestOverrides) {
      setLookForwardBars(String(baseBacktestCfg.look_forward_bars ?? 10));
      setPnlMode((baseBacktestCfg.pnl_mode ?? "price") as any);
      setBacktestType((baseBacktestCfg.backtest_type ?? "long") as any);
      setBacktestFilterType((baseBacktestCfg.filter_type ?? "rsi") as any);
      setFeeRate(String(baseBacktestCfg.fee_rate ?? 0.0004));
      setSlippageBps(String(baseBacktestCfg.slippage_bps ?? 0.0));
      setPositionFraction(String(baseBacktestCfg.position_fraction ?? 1.0));
      setPositionNotional(baseBacktestCfg.position_notional === null || baseBacktestCfg.position_notional === undefined ? "" : String(baseBacktestCfg.position_notional));
      setOrderIntervalMinutes(String(baseBacktestCfg.order_interval_minutes ?? 30));
      setMinRuleConfidence(String(baseBacktestCfg.min_rule_confidence ?? 0.0));
      setWinProfit(String(baseBacktestCfg.win_profit ?? 4.0));
      setLossCost(String(baseBacktestCfg.loss_cost ?? 5.0));
      setInitialBalance(String(baseBacktestCfg.initial_balance ?? 1000.0));
    }
  }, [
    useBacktestOverrides,
    baseBacktestCfg.look_forward_bars,
    baseBacktestCfg.pnl_mode,
    baseBacktestCfg.backtest_type,
    baseBacktestCfg.filter_type,
    baseBacktestCfg.fee_rate,
    baseBacktestCfg.slippage_bps,
    baseBacktestCfg.position_fraction,
    baseBacktestCfg.position_notional,
    baseBacktestCfg.order_interval_minutes,
    baseBacktestCfg.min_rule_confidence,
    baseBacktestCfg.win_profit,
    baseBacktestCfg.loss_cost,
    baseBacktestCfg.initial_balance,
  ]);

  useEffect(() => {
    if (!useWalkForwardOverrides) {
      setWalkForwardEnabled(Boolean(baseWfCfg.enabled ?? true));
      setTrainBars(String(baseWfCfg.train_bars ?? 20000));
      setTestBars(String(baseWfCfg.test_bars ?? 5000));
      setStepBars(String(baseWfCfg.step_bars ?? 5000));
      setMaxWindows(String(baseWfCfg.max_windows ?? 10));
    }
  }, [useWalkForwardOverrides, baseWfCfg.enabled, baseWfCfg.train_bars, baseWfCfg.test_bars, baseWfCfg.step_bars, baseWfCfg.max_windows]);

  async function refresh() {
    const [tpls, latestRuns] = await Promise.all([api.listTemplates(), api.listRuns(20, 0)]);
    setTemplates(tpls);
    setRuns(latestRuns);
  }

  useEffect(() => {
    refresh().catch((e) => setError(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function onRun() {
    setError(null);
    setIsSubmitting(true);
    try {
      let config_overrides: Record<string, JsonValue> = {};
      if (overridesText.trim()) {
        config_overrides = JSON.parse(overridesText) as Record<string, JsonValue>;
      }

      const form_overrides: Record<string, JsonValue> = {};

      if (useFeatureOverrides) {
        const cleaned = alphaTypes.map((t) => String(t).trim()).filter(Boolean);
        if (cleaned.length === 0) throw new Error("特征参数：alpha_types 至少选择 1 个。");
        form_overrides.feature_calculation = {
          alpha_types: cleaned,
          instrument_name: instrumentName.trim() ? instrumentName.trim() : null,
        };
      }

      if (useLabelOverrides) {
        const window = Number(labelWindow);
        const lookForward = Number(labelLookForward);
        if (!Number.isFinite(window) || window < 3 || !Number.isInteger(window))
          throw new Error("标签参数：window 需要是 >= 3 的整数。");
        if (window % 2 === 0) throw new Error("标签参数：window 建议为奇数（例如 29）。");
        if (!Number.isFinite(lookForward) || lookForward < 1 || !Number.isInteger(lookForward))
          throw new Error("标签参数：look_forward 需要是 >= 1 的整数。");

        const t = String(labelType).trim();
        const f = String(labelFilterType).trim();
        const thText = String(labelThreshold).trim();
        const th = thText ? Number(thText) : null;
        if (thText && (!Number.isFinite(th) || th === null)) throw new Error("标签参数：threshold 需要是数字，或留空使用默认值。");

        form_overrides.label_calculation = {
          window,
          look_forward: lookForward,
          label_type: t,
          filter_type: f,
          threshold: thText ? (th as number) : null,
        };
      }

      if (useTrainingOverrides) {
        const rounds = Number(numBoostRound);
        const threads = Number(numThreads);
        if (!Number.isFinite(rounds) || rounds < 1 || !Number.isInteger(rounds))
          throw new Error("训练参数：num_boost_round 需要是 >= 1 的整数。");
        if (!Number.isFinite(threads) || threads < 1 || !Number.isInteger(threads))
          throw new Error("训练参数：num_threads 需要是 >= 1 的整数。");
        form_overrides.model_training = { num_boost_round: rounds, num_threads: threads };
      }

      if (useInterpretationOverrides) {
        const ms = Number(shapMaxSamples);
        const md = Number(shapMaxDisplay);
        if (!Number.isFinite(ms) || ms < 1 || !Number.isInteger(ms)) throw new Error("解释参数：max_samples 需要是 >= 1 的整数。");
        if (!Number.isFinite(md) || md < 1 || !Number.isInteger(md)) throw new Error("解释参数：max_display 需要是 >= 1 的整数。");
        form_overrides.model_interpretation = { max_samples: ms, max_display: md };
      }

      if (useAnalysisOverrides) {
        const mf = Number(maxFeatures);
        const md = Number(maxDepth);
        const mss = Number(minSamplesSplit);
        const msl = Number(minSamplesLeaf);
        const mrs = Number(minRuleSamples);
        if (!Number.isFinite(mf) || mf < 1 || !Number.isInteger(mf)) throw new Error("规则参数：max_features 需要是 >= 1 的整数。");
        if (!Number.isFinite(md) || md < 1 || !Number.isInteger(md)) throw new Error("规则参数：max_depth 需要是 >= 1 的整数。");
        if (!Number.isFinite(mss) || mss < 2 || !Number.isInteger(mss)) throw new Error("规则参数：min_samples_split 需要是 >= 2 的整数。");
        if (!Number.isFinite(msl) || msl < 1 || !Number.isInteger(msl)) throw new Error("规则参数：min_samples_leaf 需要是 >= 1 的整数。");
        if (!Number.isFinite(mrs) || mrs < 1 || !Number.isInteger(mrs)) throw new Error("规则参数：min_rule_samples 需要是 >= 1 的整数。");

        const thText = String(analysisLabelThreshold).trim();
        const th = thText ? Number(thText) : null;
        if (thText && (!Number.isFinite(th) || th === null)) throw new Error("规则参数：label_threshold 需要是数字，或留空使用自动值。");

        const selected = selectedFeaturesText
          .split(/\r?\n|,/g)
          .map((s) => s.trim())
          .filter(Boolean);

        form_overrides.model_analysis = {
          selected_features: selected.length > 0 ? selected : null,
          max_features: mf,
          max_depth: md,
          min_samples_split: mss,
          min_samples_leaf: msl,
          min_rule_samples: mrs,
          label_threshold: thText ? (th as number) : null,
        };
      }

      if (useBacktestOverrides) {
        const pf = Number(positionFraction);
        if (!Number.isFinite(pf) || pf <= 0 || pf > 1) throw new Error("回测参数：仓位比例需要在 (0, 1] 范围内。");

        const pnText = String(positionNotional).trim();
        const pn = pnText ? Number(pnText) : null;
        if (pnText && (!Number.isFinite(pn) || pn === null || pn <= 0))
          throw new Error("回测参数：position_notional 需要是 > 0 的数字，或留空。");

        const oi = Number(orderIntervalMinutes);
        if (!Number.isFinite(oi) || oi < 0 || !Number.isInteger(oi))
          throw new Error("回测参数：下单间隔需要是 >= 0 的整数（分钟）。");

        const fr = Number(feeRate);
        if (!Number.isFinite(fr) || fr < 0) throw new Error("回测参数：手续费费率需要是 >= 0 的数字。");

        const sb = Number(slippageBps);
        if (!Number.isFinite(sb) || sb < 0) throw new Error("回测参数：滑点需要是 >= 0 的数字（bps）。");

        const lf = Number(lookForwardBars);
        if (!Number.isFinite(lf) || lf < 1 || !Number.isInteger(lf))
          throw new Error("回测参数：look_forward_bars 需要是 >= 1 的整数。");

        const mrc = Number(minRuleConfidence);
        if (!Number.isFinite(mrc) || mrc < 0 || mrc > 1) throw new Error("回测参数：min_rule_confidence 需要在 [0, 1] 范围内。");

        const wp = Number(winProfit);
        const lc = Number(lossCost);
        const ib = Number(initialBalance);
        if (!Number.isFinite(wp) || wp <= 0) throw new Error("回测参数：win_profit 需要是 > 0 的数字。");
        if (!Number.isFinite(lc) || lc <= 0) throw new Error("回测参数：loss_cost 需要是 > 0 的数字。");
        if (!Number.isFinite(ib) || ib <= 0) throw new Error("回测参数：initial_balance 需要是 > 0 的数字。");

        form_overrides.backtest_construction = {
          look_forward_bars: lf,
          pnl_mode: pnlMode,
          backtest_type: backtestType,
          filter_type: backtestFilterType,
          position_fraction: pf,
          position_notional: pnText ? (pn as number) : null,
          order_interval_minutes: oi,
          fee_rate: fr,
          slippage_bps: sb,
          min_rule_confidence: mrc,
          win_profit: wp,
          loss_cost: lc,
          initial_balance: ib,
        };
      }

      if (useWalkForwardOverrides) {
        const train = Number(trainBars);
        const test = Number(testBars);
        const step = Number(stepBars);
        const maxW = Number(maxWindows);

        if (!Number.isFinite(train) || train < 1 || !Number.isInteger(train))
          throw new Error("滚动验证参数：train_bars 需要是 >= 1 的整数。");
        if (!Number.isFinite(test) || test < 1 || !Number.isInteger(test))
          throw new Error("滚动验证参数：test_bars 需要是 >= 1 的整数。");
        if (!Number.isFinite(step) || step < 1 || !Number.isInteger(step))
          throw new Error("滚动验证参数：step_bars 需要是 >= 1 的整数。");
        if (!Number.isFinite(maxW) || maxW < 1 || !Number.isInteger(maxW))
          throw new Error("滚动验证参数：max_windows 需要是 >= 1 的整数。");

        form_overrides.walk_forward_evaluation = {
          enabled: walkForwardEnabled,
          train_bars: train,
          test_bars: test,
          step_bars: step,
          max_windows: maxW,
        };
      }

      config_overrides = deepMerge(config_overrides, form_overrides);

      const response = await api.runPipeline({
        workflow_name: "default",
        template_id:
          templateChoice === TEMPLATE_NONE
            ? null
            : templateChoice === TEMPLATE_AUTO
              ? defaultTemplate?.template_id || null
              : templateChoice,
        symbol,
        start_date: startDate,
        end_date: endDate,
        interval,
        config_overrides,
      });

      await refresh();
      navigate(`/runs/${response.run_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="grid gap-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">一键跑完拿到结果</CardTitle>
          <CardDescription>先用默认值跑通：然后在结果页看图表（回测曲线 / SHAP）。</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
            <div className="grid gap-2">
              <Label>币种</Label>
              <Select value={symbol} onValueChange={(v) => setSymbol(v as any)}>
                <SelectTrigger>
                  <SelectValue placeholder="选择币种" />
                </SelectTrigger>
                <SelectContent>
                  {SYMBOL_OPTIONS.map((s) => (
                    <SelectItem key={s} value={s}>
                      {s}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="grid gap-2">
              <Label>开始日期</Label>
              <Input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
            </div>

            <div className="grid gap-2">
              <Label>结束日期</Label>
              <Input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
            </div>

            <div className="grid gap-2">
              <Label>周期</Label>
              <Select value={interval} onValueChange={(v) => setInterval(v as any)}>
                <SelectTrigger>
                  <SelectValue placeholder="选择周期" />
                </SelectTrigger>
                <SelectContent>
                  {INTERVAL_OPTIONS.map((it) => (
                    <SelectItem key={it} value={it}>
                      {it}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <details className="rounded-lg border bg-card p-4">
            <summary className="cursor-pointer text-sm text-muted-foreground">高级设置（可选）</summary>
            <div className="mt-4 grid gap-4">
              <div className="grid gap-2 md:max-w-md">
                <Label>模板（自动/不使用/指定）</Label>
                <Select value={templateChoice} onValueChange={setTemplateChoice}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={TEMPLATE_AUTO}>
                      自动（默认：{defaultTemplate ? defaultTemplate.name : "无"}）
                    </SelectItem>
                    <SelectItem value={TEMPLATE_NONE}>不使用模板</SelectItem>
                    {templates.map((t) => (
                      <SelectItem key={t.template_id} value={t.template_id}>
                        {t.name}
                        {t.is_default ? "（默认）" : ""}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <div className="text-xs text-muted-foreground">
                  模板就是“一套参数预设”。你可以先用默认模板跑通，再按需要覆盖其中一部分参数。
                </div>
              </div>

              <details className="rounded-md border p-3">
                <summary className="cursor-pointer text-sm text-muted-foreground">特征（alpha_types）</summary>
                <div className="mt-3 grid gap-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="grid gap-1">
                      <div className="text-sm font-medium">alpha_types（特征因子集合）</div>
                      <div className="text-xs text-muted-foreground">
                        alpha158/alpha216 等表示“一套技术指标特征”。先用 alpha158 跑通最稳。
                      </div>
                    </div>
                    <label className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={useFeatureOverrides}
                        onChange={(e) => setUseFeatureOverrides(e.target.checked)}
                      />
                      覆盖默认/模板
                    </label>
                  </div>

                  {useFeatureOverrides ? (
                    <div className="grid gap-3">
                      <div className="grid gap-2">
                        <Label>alpha_types（可多选）</Label>
                        <div className="grid gap-2 rounded-md border p-3">
                          {ALPHA_TYPE_OPTIONS.map((opt) => (
                            <label key={opt.value} className="flex items-start gap-2 text-sm">
                              <input
                                type="checkbox"
                                checked={alphaTypes.includes(opt.value)}
                                onChange={(e) => {
                                  const checked = e.target.checked;
                                  setAlphaTypes((prev) => {
                                    const next = new Set(prev);
                                    if (checked) next.add(opt.value);
                                    else next.delete(opt.value);
                                    return Array.from(next);
                                  });
                                }}
                              />
                              <span className="grid gap-0.5">
                                <span className="font-medium">{opt.label}</span>
                                <span className="text-xs text-muted-foreground">{opt.description}</span>
                              </span>
                            </label>
                          ))}
                        </div>
                        <div className="text-xs text-muted-foreground">
                          默认：{formatDefaultValue(baseFeatureCfg.alpha_types)} · 推荐：alpha158
                        </div>
                      </div>

                      <div className="grid gap-1">
                        <Label>instrument_name（可选）</Label>
                        <Input value={instrumentName} onChange={(e) => setInstrumentName(e.target.value)} placeholder="留空即可" />
                        <div className="text-xs text-muted-foreground">
                          默认：{formatDefaultValue(baseFeatureCfg.instrument_name)} · 推荐：留空
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="text-xs text-muted-foreground">
                      未覆盖：使用模板/默认值（alpha_types={formatDefaultValue(baseFeatureCfg.alpha_types)}）。
                    </div>
                  )}
                </div>
              </details>

              <details className="rounded-md border p-3">
                <summary className="cursor-pointer text-sm text-muted-foreground">标签（label）</summary>
                <div className="mt-3 grid gap-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="grid gap-1">
                      <div className="text-sm font-medium">标签怎么来的？</div>
                      <div className="text-xs text-muted-foreground">
                        标签=“未来 look_forward 根K线涨/跌的比例”，只在满足过滤条件（RSI/CTI）时才赋值。
                      </div>
                    </div>
                    <label className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={useLabelOverrides}
                        onChange={(e) => setUseLabelOverrides(e.target.checked)}
                      />
                      覆盖默认/模板
                    </label>
                  </div>

                  {useLabelOverrides ? (
                    <div className="grid gap-3">
                      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                        <div className="grid gap-1">
                          <Label>window（窗口，建议奇数）</Label>
                          <Input type="number" min={3} step={2} value={labelWindow} onChange={(e) => setLabelWindow(e.target.value)} />
                          <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseLabelCfg.window)} · 推荐：29</div>
                        </div>
                        <div className="grid gap-1">
                          <Label>look_forward（预测步长）</Label>
                          <Input
                            type="number"
                            min={1}
                            step={1}
                            value={labelLookForward}
                            onChange={(e) => setLabelLookForward(e.target.value)}
                          />
                          <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseLabelCfg.look_forward)} · 推荐：10</div>
                        </div>
                        <div className="grid gap-1">
                          <Label>label_type（方向）</Label>
                          <Select value={labelType} onValueChange={(v) => setLabelType(v as any)}>
                            <SelectTrigger>
                              <SelectValue placeholder="选择方向" />
                            </SelectTrigger>
                            <SelectContent>
                              {LABEL_TYPE_OPTIONS.map((o) => (
                                <SelectItem key={o.value} value={o.value}>
                                  {o.label}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseLabelCfg.label_type)} · 推荐：up</div>
                        </div>
                      </div>

                      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                        <div className="grid gap-1">
                          <Label>filter_type（过滤指标）</Label>
                          <Select value={labelFilterType} onValueChange={(v) => setLabelFilterType(v as any)}>
                            <SelectTrigger>
                              <SelectValue placeholder="选择过滤指标" />
                            </SelectTrigger>
                            <SelectContent>
                              {FILTER_TYPE_OPTIONS.map((o) => (
                                <SelectItem key={o.value} value={o.value}>
                                  {o.label}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseLabelCfg.filter_type)} · 推荐：rsi</div>
                        </div>

                        <div className="grid gap-1">
                          <Label>threshold（阈值，可留空=自动）</Label>
                          <Input
                            type="number"
                            step={0.1}
                            value={labelThreshold}
                            onChange={(e) => setLabelThreshold(e.target.value)}
                            placeholder="留空=使用默认阈值"
                          />
                          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                            <span>默认：{formatDefaultValue(baseLabelCfg.threshold)}</span>
                            <span>推荐：{recommendedThreshold === null ? "（无）" : String(recommendedThreshold)}</span>
                            {recommendedThreshold !== null ? (
                              <Button
                                type="button"
                                variant="secondary"
                                className="h-7 px-2 text-xs"
                                onClick={() => setLabelThreshold(String(recommendedThreshold))}
                              >
                                应用推荐
                              </Button>
                            ) : null}
                            <Button
                              type="button"
                              variant="secondary"
                              className="h-7 px-2 text-xs"
                              onClick={() => setLabelThreshold("")}
                            >
                              清空=自动
                            </Button>
                          </div>
                        </div>

                        <div className="grid gap-1">
                          <Label>提示</Label>
                          <div className="rounded-md border px-3 py-2 text-xs text-muted-foreground">
                            RSI：up 默认 30 / down 默认 70；CTI：up 默认 -0.5 / down 默认 0.5。
                          </div>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="text-xs text-muted-foreground">
                      未覆盖：使用模板/默认值（window={formatDefaultValue(baseLabelCfg.window)} · look_forward={formatDefaultValue(baseLabelCfg.look_forward)} · label_type={formatDefaultValue(baseLabelCfg.label_type)} · filter_type={formatDefaultValue(baseLabelCfg.filter_type)}）。
                    </div>
                  )}
                </div>
              </details>

              <details className="rounded-md border p-3">
                <summary className="cursor-pointer text-sm text-muted-foreground">训练（model_training）</summary>
                <div className="mt-3 grid gap-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="grid gap-1">
                      <div className="text-sm font-medium">模型训练参数</div>
                      <div className="text-xs text-muted-foreground">影响训练速度与模型容量（先用默认值即可）。</div>
                    </div>
                    <label className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={useTrainingOverrides}
                        onChange={(e) => setUseTrainingOverrides(e.target.checked)}
                      />
                      覆盖默认/模板
                    </label>
                  </div>

                  {useTrainingOverrides ? (
                    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                      <div className="grid gap-1">
                        <Label>num_boost_round（迭代轮数）</Label>
                        <Input type="number" min={1} step={50} value={numBoostRound} onChange={(e) => setNumBoostRound(e.target.value)} />
                        <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseTrainCfg.num_boost_round)} · 推荐：500</div>
                      </div>
                      <div className="grid gap-1">
                        <Label>num_threads（线程数）</Label>
                        <Input type="number" min={1} step={1} value={numThreads} onChange={(e) => setNumThreads(e.target.value)} />
                        <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseTrainCfg.num_threads)} · 推荐：4</div>
                      </div>
                    </div>
                  ) : (
                    <div className="text-xs text-muted-foreground">
                      未覆盖：使用模板/默认值（num_boost_round={formatDefaultValue(baseTrainCfg.num_boost_round)} · num_threads={formatDefaultValue(baseTrainCfg.num_threads)}）。
                    </div>
                  )}
                </div>
              </details>

              <details className="rounded-md border p-3">
                <summary className="cursor-pointer text-sm text-muted-foreground">解释（SHAP）</summary>
                <div className="mt-3 grid gap-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="grid gap-1">
                      <div className="text-sm font-medium">SHAP 解释参数</div>
                      <div className="text-xs text-muted-foreground">用于控制解释时抽样的数量与显示 Top 特征数。</div>
                    </div>
                    <label className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={useInterpretationOverrides}
                        onChange={(e) => setUseInterpretationOverrides(e.target.checked)}
                      />
                      覆盖默认/模板
                    </label>
                  </div>

                  {useInterpretationOverrides ? (
                    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                      <div className="grid gap-1">
                        <Label>max_samples（最多抽样行数）</Label>
                        <Input type="number" min={1} step={500} value={shapMaxSamples} onChange={(e) => setShapMaxSamples(e.target.value)} />
                        <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseInterpCfg.max_samples)} · 推荐：5000</div>
                      </div>
                      <div className="grid gap-1">
                        <Label>max_display（Top 特征数）</Label>
                        <Input type="number" min={1} step={1} value={shapMaxDisplay} onChange={(e) => setShapMaxDisplay(e.target.value)} />
                        <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseInterpCfg.max_display)} · 推荐：20</div>
                      </div>
                    </div>
                  ) : (
                    <div className="text-xs text-muted-foreground">
                      未覆盖：使用模板/默认值（max_samples={formatDefaultValue(baseInterpCfg.max_samples)} · max_display={formatDefaultValue(baseInterpCfg.max_display)}）。
                    </div>
                  )}
                </div>
              </details>

              <details className="rounded-md border p-3">
                <summary className="cursor-pointer text-sm text-muted-foreground">规则（model_analysis）</summary>
                <div className="mt-3 grid gap-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="grid gap-1">
                      <div className="text-sm font-medium">把模型“翻译成规则”</div>
                      <div className="text-xs text-muted-foreground">
                        会训练一个浅层决策树来提取规则用于回测；参数越激进越容易过拟合。
                      </div>
                    </div>
                    <label className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={useAnalysisOverrides}
                        onChange={(e) => setUseAnalysisOverrides(e.target.checked)}
                      />
                      覆盖默认/模板
                    </label>
                  </div>

                  {useAnalysisOverrides ? (
                    <div className="grid gap-3">
                      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                        <div className="grid gap-1">
                          <Label>max_features（最多使用多少特征）</Label>
                          <Input type="number" min={1} step={1} value={maxFeatures} onChange={(e) => setMaxFeatures(e.target.value)} />
                          <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseAnalysisCfg.max_features)} · 推荐：8</div>
                        </div>
                        <div className="grid gap-1">
                          <Label>max_depth（树深度）</Label>
                          <Input type="number" min={1} step={1} value={maxDepth} onChange={(e) => setMaxDepth(e.target.value)} />
                          <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseAnalysisCfg.max_depth)} · 推荐：3</div>
                        </div>
                        <div className="grid gap-1">
                          <Label>min_rule_samples（规则最少样本数）</Label>
                          <Input type="number" min={1} step={10} value={minRuleSamples} onChange={(e) => setMinRuleSamples(e.target.value)} />
                          <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseAnalysisCfg.min_rule_samples)} · 推荐：50</div>
                        </div>
                      </div>

                      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                        <div className="grid gap-1">
                          <Label>min_samples_split</Label>
                          <Input type="number" min={2} step={10} value={minSamplesSplit} onChange={(e) => setMinSamplesSplit(e.target.value)} />
                          <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseAnalysisCfg.min_samples_split)} · 推荐：100</div>
                        </div>
                        <div className="grid gap-1">
                          <Label>min_samples_leaf</Label>
                          <Input type="number" min={1} step={10} value={minSamplesLeaf} onChange={(e) => setMinSamplesLeaf(e.target.value)} />
                          <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseAnalysisCfg.min_samples_leaf)} · 推荐：50</div>
                        </div>
                        <div className="grid gap-1">
                          <Label>label_threshold（可留空=自动）</Label>
                          <Input
                            type="number"
                            step={0.01}
                            value={analysisLabelThreshold}
                            onChange={(e) => setAnalysisLabelThreshold(e.target.value)}
                            placeholder="留空=使用 label 中位数"
                          />
                          <div className="text-xs text-muted-foreground">
                            默认：{formatDefaultValue(baseAnalysisCfg.label_threshold)} · 推荐：留空
                          </div>
                        </div>
                      </div>

                      <div className="grid gap-1">
                        <Label>selected_features（可选，换行或逗号分隔）</Label>
                        <Textarea
                          value={selectedFeaturesText}
                          onChange={(e) => setSelectedFeaturesText(e.target.value)}
                          placeholder="留空=自动从训练得到的 top importance 推导"
                          className="min-h-24 font-mono text-xs"
                        />
                        <div className="text-xs text-muted-foreground">
                          默认：{formatDefaultValue(baseAnalysisCfg.selected_features)} · 推荐：留空
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="text-xs text-muted-foreground">
                      未覆盖：使用模板/默认值（max_depth={formatDefaultValue(baseAnalysisCfg.max_depth)} · max_features={formatDefaultValue(baseAnalysisCfg.max_features)}）。
                    </div>
                  )}
                </div>
              </details>

              <details className="rounded-md border p-3">
                <summary className="cursor-pointer text-sm text-muted-foreground">回测（成本 / 仓位 / 频率）</summary>
                <div className="mt-3 grid gap-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="grid gap-1">
                      <div className="text-sm font-medium">回测参数</div>
                      <div className="text-xs text-muted-foreground">影响“回测统计”的成本假设、风险大小与交易频率。</div>
                    </div>
                    <label className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={useBacktestOverrides}
                        onChange={(e) => setUseBacktestOverrides(e.target.checked)}
                      />
                      覆盖默认/模板
                    </label>
                  </div>

                  {useBacktestOverrides ? (
                    <div className="grid gap-3">
                      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                        <div className="grid gap-1">
                          <Label>backtest_type（方向）</Label>
                          <Select value={backtestType} onValueChange={(v) => setBacktestType(v as any)}>
                            <SelectTrigger>
                              <SelectValue placeholder="选择方向" />
                            </SelectTrigger>
                            <SelectContent>
                              {BACKTEST_TYPE_OPTIONS.map((o) => (
                                <SelectItem key={o.value} value={o.value}>
                                  {o.label}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseBacktestCfg.backtest_type)} · 推荐：long</div>
                        </div>

                        <div className="grid gap-1">
                          <Label>pnl_mode（收益计算方式）</Label>
                          <Select value={pnlMode} onValueChange={(v) => setPnlMode(v as any)}>
                            <SelectTrigger>
                              <SelectValue placeholder="选择方式" />
                            </SelectTrigger>
                            <SelectContent>
                              {PNL_MODE_OPTIONS.map((o) => (
                                <SelectItem key={o.value} value={o.value}>
                                  {o.label}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseBacktestCfg.pnl_mode)} · 推荐：price</div>
                        </div>

                        <div className="grid gap-1">
                          <Label>look_forward_bars（持有K线数）</Label>
                          <Input
                            type="number"
                            min={1}
                            step={1}
                            value={lookForwardBars}
                            onChange={(e) => setLookForwardBars(e.target.value)}
                          />
                          <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseBacktestCfg.look_forward_bars)} · 推荐：10</div>
                        </div>
                      </div>

                      <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
                        <div className="grid gap-1">
                          <Label>仓位比例（position_fraction）</Label>
                          <Input
                            type="number"
                            min={0}
                            max={1}
                            step={0.05}
                            value={positionFraction}
                            onChange={(e) => setPositionFraction(e.target.value)}
                          />
                          <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseBacktestCfg.position_fraction)} · 推荐：0.3</div>
                        </div>

                        <div className="grid gap-1">
                          <Label>固定名义仓位（position_notional，可空）</Label>
                          <Input
                            type="number"
                            min={0}
                            step={10}
                            value={positionNotional}
                            onChange={(e) => setPositionNotional(e.target.value)}
                            placeholder="留空=使用仓位比例"
                          />
                          <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseBacktestCfg.position_notional)} · 推荐：留空</div>
                        </div>

                        <div className="grid gap-1">
                          <Label>下单间隔（order_interval_minutes）</Label>
                          <Input
                            type="number"
                            min={0}
                            step={1}
                            value={orderIntervalMinutes}
                            onChange={(e) => setOrderIntervalMinutes(e.target.value)}
                          />
                          <div className="text-xs text-muted-foreground">
                            默认：{formatDefaultValue(baseBacktestCfg.order_interval_minutes)} · 推荐：30
                            {minutesPerBar !== null ? `（≥ ${minutesPerBar} 更直观）` : ""}
                          </div>
                          {minutesPerBar !== null ? (
                            <div className="mt-1">
                              <Button
                                type="button"
                                variant="secondary"
                                className="h-7 px-2 text-xs"
                                onClick={() => setOrderIntervalMinutes(String(minutesPerBar))}
                              >
                                同步为 1 个 bar（{minutesPerBar} 分钟）
                              </Button>
                            </div>
                          ) : null}
                        </div>

                        <div className="grid gap-1">
                          <Label>min_rule_confidence（规则置信度阈值）</Label>
                          <Input
                            type="number"
                            min={0}
                            max={1}
                            step={0.05}
                            value={minRuleConfidence}
                            onChange={(e) => setMinRuleConfidence(e.target.value)}
                          />
                          <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseBacktestCfg.min_rule_confidence)} · 推荐：0.0~0.6</div>
                        </div>
                      </div>

                      <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
                        <div className="grid gap-1">
                          <Label>filter_type（回测过滤指标）</Label>
                          <Select value={backtestFilterType} onValueChange={(v) => setBacktestFilterType(v as any)}>
                            <SelectTrigger>
                              <SelectValue placeholder="选择过滤指标" />
                            </SelectTrigger>
                            <SelectContent>
                              {FILTER_TYPE_OPTIONS.map((o) => (
                                <SelectItem key={o.value} value={o.value}>
                                  {o.label}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseBacktestCfg.filter_type)} · 推荐：rsi</div>
                        </div>

                        <div className="grid gap-1">
                          <Label>手续费费率（fee_rate，每边）</Label>
                          <Input type="number" min={0} step={0.0001} value={feeRate} onChange={(e) => setFeeRate(e.target.value)} />
                          <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseBacktestCfg.fee_rate)} · 推荐：0.0004</div>
                        </div>

                        <div className="grid gap-1">
                          <Label>滑点（slippage_bps）</Label>
                          <Input
                            type="number"
                            min={0}
                            step={1}
                            value={slippageBps}
                            onChange={(e) => setSlippageBps(e.target.value)}
                          />
                          <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseBacktestCfg.slippage_bps)} · 推荐：0~10</div>
                        </div>

                        <div className="grid gap-1">
                          <Label>initial_balance（初始资金）</Label>
                          <Input
                            type="number"
                            min={1}
                            step={100}
                            value={initialBalance}
                            onChange={(e) => setInitialBalance(e.target.value)}
                          />
                          <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseBacktestCfg.initial_balance)} · 推荐：1000</div>
                        </div>
                      </div>

                      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                        <div className="grid gap-1">
                          <Label>win_profit（仅 fixed 模式使用）</Label>
                          <Input type="number" min={0.1} step={0.1} value={winProfit} onChange={(e) => setWinProfit(e.target.value)} />
                          <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseBacktestCfg.win_profit)} · 推荐：4.0</div>
                        </div>
                        <div className="grid gap-1">
                          <Label>loss_cost（仅 fixed 模式使用）</Label>
                          <Input type="number" min={0.1} step={0.1} value={lossCost} onChange={(e) => setLossCost(e.target.value)} />
                          <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseBacktestCfg.loss_cost)} · 推荐：5.0</div>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="text-xs text-muted-foreground">未覆盖：使用模板/默认值（通常足够先跑通）。</div>
                  )}
                </div>
              </details>

              <details className="rounded-md border p-3">
                <summary className="cursor-pointer text-sm text-muted-foreground">滚动验证（Walk-forward）</summary>
                <div className="mt-3 grid gap-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="grid gap-1">
                      <div className="text-sm font-medium">滚动验证（更像训练/测试）</div>
                      <div className="text-xs text-muted-foreground">用多个时间窗口重复训练/回测，更能看出稳定性。</div>
                    </div>
                    <label className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={useWalkForwardOverrides}
                        onChange={(e) => setUseWalkForwardOverrides(e.target.checked)}
                      />
                      覆盖默认/模板
                    </label>
                  </div>

                  {useWalkForwardOverrides ? (
                    <div className="grid gap-3">
                      <label className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={walkForwardEnabled}
                          onChange={(e) => setWalkForwardEnabled(e.target.checked)}
                        />
                        启用滚动验证
                      </label>

                      {estimatedTotalBars !== null ? (
                        <div className="rounded-md border px-3 py-2 text-xs text-muted-foreground">
                          当前时间范围可用 bars≈{estimatedTotalBars}（{formatBarsToApproxDuration(estimatedTotalBars, interval)}）
                        </div>
                      ) : (
                        <div className="rounded-md border px-3 py-2 text-xs text-muted-foreground">当前时间范围暂无法估算 bars。</div>
                      )}

                      {recommendedWalkForward ? (
                        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                          <span>
                            推荐：train={recommendedWalkForward.train_bars}（{formatBarsToApproxDuration(recommendedWalkForward.train_bars, interval)}） · test=
                            {recommendedWalkForward.test_bars}（{formatBarsToApproxDuration(recommendedWalkForward.test_bars, interval)}） · step=
                            {recommendedWalkForward.step_bars} · 预计窗口数≈{recommendedWalkForward.expected_windows ?? "-"}
                          </span>
                          <Button
                            type="button"
                            variant="secondary"
                            className="h-7 px-2 text-xs"
                            onClick={() => {
                              setTrainBars(String(recommendedWalkForward.train_bars));
                              setTestBars(String(recommendedWalkForward.test_bars));
                              setStepBars(String(recommendedWalkForward.step_bars));
                              setMaxWindows(String(recommendedWalkForward.max_windows));
                            }}
                          >
                            应用推荐
                          </Button>
                        </div>
                      ) : null}

                      <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
                        <div className="grid gap-1">
                          <Label>训练窗口（train_bars）</Label>
                          <Input type="number" min={1} step={100} value={trainBars} onChange={(e) => setTrainBars(e.target.value)} />
                          <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseWfCfg.train_bars)} · 推荐：按上面“应用推荐”</div>
                        </div>

                        <div className="grid gap-1">
                          <Label>测试窗口（test_bars）</Label>
                          <Input type="number" min={1} step={100} value={testBars} onChange={(e) => setTestBars(e.target.value)} />
                          <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseWfCfg.test_bars)} · 推荐：按上面“应用推荐”</div>
                        </div>

                        <div className="grid gap-1">
                          <Label>步长（step_bars）</Label>
                          <Input type="number" min={1} step={100} value={stepBars} onChange={(e) => setStepBars(e.target.value)} />
                          <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseWfCfg.step_bars)} · 推荐：通常= test_bars</div>
                        </div>

                        <div className="grid gap-1">
                          <Label>最多窗口（max_windows）</Label>
                          <Input type="number" min={1} step={1} value={maxWindows} onChange={(e) => setMaxWindows(e.target.value)} />
                          <div className="text-xs text-muted-foreground">默认：{formatDefaultValue(baseWfCfg.max_windows)} · 推荐：10</div>
                        </div>
                      </div>

                      <div className="text-xs text-muted-foreground">
                        预计窗口数：{expectedWindows === null ? "-" : String(expectedWindows)}（如果为 0 或很小：会提示“数据不足/已跳过”）。
                      </div>
                    </div>
                  ) : (
                    <div className="text-xs text-muted-foreground">未覆盖：使用模板/默认值。</div>
                  )}
                </div>
              </details>

              <div className="grid gap-2">
                <Label>config_overrides（JSON，可为空）</Label>
                <div className="text-xs text-muted-foreground">
                  可留空：上面的表单会自动生效。两者同时使用时，同名字段以表单为准。
                </div>
                <Textarea
                  value={overridesText}
                  onChange={(e) => setOverridesText(e.target.value)}
                  placeholder='例如：{"backtest_construction":{"position_fraction":0.3},"walk_forward_evaluation":{"train_bars":10000,"test_bars":2000,"step_bars":2000}}'
                  className="min-h-28 font-mono text-xs"
                />
              </div>
            </div>
          </details>

          {error ? <div className="text-sm text-destructive">错误：{error}</div> : null}

          <div className="flex items-center gap-3">
            <Button onClick={onRun} disabled={isSubmitting}>
              {isSubmitting ? "运行中…" : "一键运行"}
            </Button>
            <Button variant="secondary" onClick={() => refresh().catch((e) => setError(String(e)))} disabled={isSubmitting}>
              刷新
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">最近运行</CardTitle>
          <CardDescription>点击某条 run 进入结果页（会自动刷新进度）。</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-2">
          {runs.length === 0 ? <div className="text-sm text-muted-foreground">暂无运行记录</div> : null}
          {runs.slice(0, 10).map((r) => (
            <button
              key={r.run_id}
              className="flex w-full items-center justify-between rounded-md border px-3 py-2 text-left hover:bg-muted/40"
              onClick={() => navigate(`/runs/${r.run_id}`)}
            >
              <div className="min-w-0">
                <div className="truncate text-sm font-medium">{r.run_id}</div>
                <div className="text-xs text-muted-foreground">
                  {r.step_name} · {new Date(r.created_at).toLocaleString()}
                </div>
              </div>
              <div className="ml-3 shrink-0">{formatStatusBadge(String(r.status))}</div>
            </button>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
