import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { api } from "@/lib/api";
import {
  calcExpectedWindows,
  estimateTotalBars,
  formatBarsToApproxDuration,
  parseIntervalToMinutes,
  recommendLabelThreshold,
  recommendWalkForward,
} from "@/lib/recommendations";
import type { JsonValue, RunSummaryResponse, StepStatus } from "@/lib/types";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";

const STEP_LABEL: Record<string, string> = {
  data_download: "数据下载",
  feature_calculation: "特征计算",
  label_calculation: "标签计算",
  model_training: "模型训练",
  model_interpretation: "模型解释（SHAP）",
  model_analysis: "模型分析（规则）",
  backtest_construction: "回测",
  walk_forward_evaluation: "滚动验证（Walk-forward）",
  pipeline: "一键流程",
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

function statusBadge(status: string) {
  const normalized = String(status || "").toLowerCase();
  if (normalized === "succeeded") return <Badge variant="success">已完成</Badge>;
  if (normalized === "failed") return <Badge variant="destructive">失败</Badge>;
  if (normalized === "canceled") return <Badge variant="secondary">已取消</Badge>;
  if (normalized === "running") return <Badge variant="secondary">运行中</Badge>;
  if (normalized === "queued") return <Badge variant="secondary">排队中</Badge>;
  return <Badge variant="secondary">{status}</Badge>;
}

function isTerminalStatus(status: string) {
  const normalized = String(status || "").toLowerCase();
  return normalized === "succeeded" || normalized === "failed" || normalized === "canceled";
}

function stepStatusText(status: StepStatus) {
  const normalized = String(status || "").toLowerCase();
  if (normalized === "pending") return "等待";
  if (normalized === "queued") return "排队中";
  if (normalized === "running") return "运行中";
  if (normalized === "succeeded") return "已完成";
  if (normalized === "failed") return "失败";
  if (normalized === "canceled") return "已取消";
  return String(status);
}

function toNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const n = Number(value);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

function formatPercent(value: number | null, digits = 2) {
  if (value === null) return "-";
  return `${(value * 100).toFixed(digits)}%`;
}

function formatNumber(value: number | null, digits = 2) {
  if (value === null) return "-";
  return value.toFixed(digits);
}

function formatDatetimeCompact(value: unknown) {
  const text = String(value ?? "").replace("T", " ");
  if (text.length >= 16) return text.slice(5, 16);
  return text;
}

function cloneJson<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

const WALK_FORWARD_OVERALL_LABEL: Record<string, string> = {
  windows: "窗口数",
  profitable_windows: "盈利窗口数",
  avg_window_profit_rate: "平均窗口收益率",
  median_window_profit_rate: "中位窗口收益率",
  initial_balance: "初始资金",
  final_balance: "期末资金",
  profit: "总收益",
  profit_rate: "总收益率",
  max_drawdown: "最大回撤",
};

const WALK_FORWARD_OVERALL_ORDER = [
  "windows",
  "profitable_windows",
  "avg_window_profit_rate",
  "median_window_profit_rate",
  "profit_rate",
  "max_drawdown",
  "profit",
  "initial_balance",
  "final_balance",
] as const;

const BACKTEST_STAT_META: Record<string, { label: string; description: string }> = {
  profit_rate: {
    label: "净收益率",
    description: "（期末资金 - 期初资金）/ 期初资金。越高越好，但需要结合最大回撤一起看风险。",
  },
  max_drawdown: {
    label: "最大回撤",
    description: "资金曲线从最高点到最低点的最大跌幅。越小越好（回撤大=坐过山车）。",
  },
  total_trades: {
    label: "交易次数",
    description: "样本量。次数太少结论很不稳定，容易“碰巧”。",
  },
  win_rate: {
    label: "胜率",
    description: "盈利交易占比。胜率高不一定赚钱（还要看每次赚/亏的大小和成本）。",
  },
  winning_trades: {
    label: "盈利交易数",
    description: "盈利的交易次数（用于理解胜率/样本结构）。",
  },
  losing_trades: {
    label: "亏损交易数",
    description: "亏损的交易次数（用于理解胜率/样本结构）。",
  },
  initial_balance: {
    label: "期初资金",
    description: "回测开始时的资金余额。",
  },
  final_balance: {
    label: "期末资金",
    description: "回测结束时的资金余额。",
  },
  profit: {
    label: "净收益",
    description: "期末资金 - 期初资金（单位同资金）。",
  },
  net_pnl: {
    label: "净收益（net_pnl）",
    description: "与净收益含义一致（当前实现中等价于 profit）。",
  },
  gross_pnl: {
    label: "毛收益（未扣成本）",
    description: "按价格变化得到的收益，未扣手续费/滑点等成本（用于对比成本影响）。",
  },
  fees_paid: {
    label: "手续费总额",
    description: "回测期间累计支付的手续费。手续费过高通常意味着交易太频繁或优势太小。",
  },
  avg_net_pnl_per_trade: {
    label: "每笔平均净收益",
    description: "净收益 / 交易次数。用于衡量“单笔优势”是否足够覆盖成本。",
  },
  pnl_mode: {
    label: "收益计算方式",
    description: "price=按价格变化计算（更接近真实）；fixed=固定赢亏（更像示例/教学）。",
  },
  fee_rate: {
    label: "手续费费率",
    description: "按名义仓位双边收取（进出场都收）。例如 0.0004 ≈ 0.04%/边。",
  },
  slippage_bps: {
    label: "滑点（bps）",
    description: "模拟成交价的不利偏移。bps=万分之一，5 bps=0.05%。",
  },
  position_fraction: {
    label: "仓位比例",
    description: "每笔使用当前资金的比例（0~1）。比例越大，收益/风险/成本都会放大。",
  },
  position_notional: {
    label: "固定名义仓位",
    description: "每笔使用固定金额下单（会被余额上限限制）。为空则使用仓位比例。",
  },
};

const BACKTEST_STAT_ORDER = [
  "profit_rate",
  "max_drawdown",
  "total_trades",
  "win_rate",
  "avg_net_pnl_per_trade",
  "profit",
  "initial_balance",
  "final_balance",
  "gross_pnl",
  "fees_paid",
  "fee_rate",
  "slippage_bps",
  "position_fraction",
  "position_notional",
  "pnl_mode",
  "winning_trades",
  "losing_trades",
  "net_pnl",
] as const;

function formatMetricValue(key: string, value: unknown) {
  const n = toNumber(value);
  if (n === null) return String(value ?? "-");

  if (key.includes("drawdown") || key.includes("win_rate") || key.endsWith("_rate") || key.includes("profit_rate")) {
    return formatPercent(n, 2);
  }

  if (key.includes("windows") || key.includes("trades")) {
    return String(Math.round(n));
  }

  if (key.includes("balance") || key === "profit") {
    return formatNumber(n, 2);
  }

  return formatNumber(n, 4);
}

function formatBacktestMetricValue(key: string, value: unknown) {
  const n = toNumber(value);
  if (n === null) return String(value ?? "-");

  if (key === "slippage_bps") return `${formatNumber(n, 2)} bps`;

  if (key === "fee_rate") return formatPercent(n, 3);

  if (key === "position_fraction") return formatPercent(n, 0);

  if (key.includes("drawdown") || key.includes("win_rate") || key.endsWith("_rate") || key.includes("profit_rate")) {
    return formatPercent(n, 2);
  }

  if (key.includes("trades")) return String(Math.round(n));

  if (key.includes("balance") || key === "profit" || key.includes("pnl") || key.includes("fees")) {
    return formatNumber(n, 2);
  }

  return formatNumber(n, 4);
}

export function RunDetailPage() {
  const params = useParams();
  const navigate = useNavigate();
  const runId = params.runId || "";

  const [data, setData] = useState<RunSummaryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isCancelling, setIsCancelling] = useState(false);

  const [isRerunning, setIsRerunning] = useState(false);
  const [rerunError, setRerunError] = useState<string | null>(null);
  const [rerunInitialized, setRerunInitialized] = useState(false);

  const [rerunSymbol, setRerunSymbol] = useState("BTCUSDT");
  const [rerunStartDate, setRerunStartDate] = useState("2025-01-01");
  const [rerunEndDate, setRerunEndDate] = useState("2025-02-01");
  const [rerunInterval, setRerunInterval] = useState("15m");

  const [alphaTypesInput, setAlphaTypesInput] = useState<string[]>(["alpha158"]);
  const [instrumentNameInput, setInstrumentNameInput] = useState("");

  const [labelWindowInput, setLabelWindowInput] = useState("29");
  const [labelLookForwardInput, setLabelLookForwardInput] = useState("10");
  const [labelTypeInput, setLabelTypeInput] = useState<(typeof LABEL_TYPE_OPTIONS)[number]["value"]>("up");
  const [labelFilterTypeInput, setLabelFilterTypeInput] = useState<(typeof FILTER_TYPE_OPTIONS)[number]["value"]>("rsi");
  const [labelThresholdInput, setLabelThresholdInput] = useState("");

  const [numBoostRoundInput, setNumBoostRoundInput] = useState("500");
  const [numThreadsInput, setNumThreadsInput] = useState("4");

  const [shapMaxSamplesInput, setShapMaxSamplesInput] = useState("5000");
  const [shapMaxDisplayInput, setShapMaxDisplayInput] = useState("20");

  const [selectedFeaturesTextInput, setSelectedFeaturesTextInput] = useState("");
  const [maxFeaturesInput, setMaxFeaturesInput] = useState("8");
  const [maxDepthInput, setMaxDepthInput] = useState("3");
  const [minSamplesSplitInput, setMinSamplesSplitInput] = useState("100");
  const [minSamplesLeafInput, setMinSamplesLeafInput] = useState("50");
  const [minRuleSamplesInput, setMinRuleSamplesInput] = useState("50");
  const [analysisLabelThresholdInput, setAnalysisLabelThresholdInput] = useState("");

  const [lookForwardBarsInput, setLookForwardBarsInput] = useState("10");
  const [pnlModeInput, setPnlModeInput] = useState<(typeof PNL_MODE_OPTIONS)[number]["value"]>("price");
  const [backtestTypeInput, setBacktestTypeInput] = useState<(typeof BACKTEST_TYPE_OPTIONS)[number]["value"]>("long");
  const [backtestFilterTypeInput, setBacktestFilterTypeInput] = useState<(typeof FILTER_TYPE_OPTIONS)[number]["value"]>("rsi");
  const [minRuleConfidenceInput, setMinRuleConfidenceInput] = useState("0.0");
  const [winProfitInput, setWinProfitInput] = useState("4.0");
  const [lossCostInput, setLossCostInput] = useState("5.0");
  const [initialBalanceInput, setInitialBalanceInput] = useState("1000.0");

  const [positionFractionInput, setPositionFractionInput] = useState("1.0");
  const [positionNotionalInput, setPositionNotionalInput] = useState("");
  const [orderIntervalMinutesInput, setOrderIntervalMinutesInput] = useState("30");
  const [feeRateInput, setFeeRateInput] = useState("0.0004");
  const [slippageBpsInput, setSlippageBpsInput] = useState("0");

  const [walkForwardEnabledInput, setWalkForwardEnabledInput] = useState(true);
  const [trainBarsInput, setTrainBarsInput] = useState("20000");
  const [testBarsInput, setTestBarsInput] = useState("5000");
  const [stepBarsInput, setStepBarsInput] = useState("5000");
  const [maxWindowsInput, setMaxWindowsInput] = useState("10");

  useEffect(() => {
    if (!runId) return;

    let stopped = false;
    let timer: number | undefined;

    async function tick() {
      try {
        const next = await api.getRunSummary(runId);
        if (stopped) return;
        setData(next);
        setError(null);
        if (!isTerminalStatus(String(next.run.status))) {
          timer = window.setTimeout(tick, 2000);
        }
      } catch (e) {
        if (stopped) return;
        setError(e instanceof Error ? e.message : String(e));
        timer = window.setTimeout(tick, 3000);
      }
    }

    tick();

    return () => {
      stopped = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [runId]);

  const pipeline = (data?.summary as any)?.pipeline ?? null;
  const dataDownload = pipeline?.config?.data_download ?? null;

  const rerunMinutesPerBar = useMemo(() => parseIntervalToMinutes(rerunInterval), [rerunInterval]);
  const rerunEstimatedTotalBars = useMemo(
    () => estimateTotalBars({ startDate: rerunStartDate, endDate: rerunEndDate, interval: rerunInterval }),
    [rerunStartDate, rerunEndDate, rerunInterval],
  );

  const rerunRecommendedThreshold = useMemo(
    () => recommendLabelThreshold({ filterType: labelFilterTypeInput, labelType: labelTypeInput }),
    [labelFilterTypeInput, labelTypeInput],
  );

  const rerunRecommendedWalkForward = useMemo(() => {
    if (rerunEstimatedTotalBars === null) return null;
    if (rerunEstimatedTotalBars <= 0) return null;
    return recommendWalkForward({ totalBars: rerunEstimatedTotalBars });
  }, [rerunEstimatedTotalBars]);

  const rerunExpectedWindows = useMemo(() => {
    const totalBars = rerunEstimatedTotalBars;
    const train = Number(trainBarsInput);
    const test = Number(testBarsInput);
    const step = Number(stepBarsInput);
    if (totalBars === null) return null;
    if (!Number.isFinite(train) || !Number.isFinite(test) || !Number.isFinite(step)) return null;
    return calcExpectedWindows({ totalBars, trainBars: train, testBars: test, stepBars: step });
  }, [rerunEstimatedTotalBars, trainBarsInput, testBarsInput, stepBarsInput]);

  function resetRerunForm() {
    const cfg = pipeline?.config;
    if (!cfg || typeof cfg !== "object") return;

    const dd = (cfg as any)?.data_download ?? null;
    if (dd && typeof dd === "object") {
      if (dd.symbol) setRerunSymbol(String(dd.symbol));
      if (dd.start_date) setRerunStartDate(String(dd.start_date));
      if (dd.end_date) setRerunEndDate(String(dd.end_date));
      if (dd.interval) setRerunInterval(String(dd.interval));
    }

    const fc = (cfg as any)?.feature_calculation ?? null;
    if (fc && typeof fc === "object") {
      const a = Array.isArray(fc.alpha_types) ? fc.alpha_types : ["alpha158"];
      setAlphaTypesInput(a.map((v: unknown) => String(v)).filter(Boolean));
      setInstrumentNameInput(fc.instrument_name ? String(fc.instrument_name) : "");
    }

    const lc = (cfg as any)?.label_calculation ?? null;
    if (lc && typeof lc === "object") {
      if (lc.window !== undefined) setLabelWindowInput(String(lc.window));
      if (lc.look_forward !== undefined) setLabelLookForwardInput(String(lc.look_forward));
      if (lc.label_type) setLabelTypeInput(String(lc.label_type) as any);
      if (lc.filter_type) setLabelFilterTypeInput(String(lc.filter_type) as any);
      setLabelThresholdInput(lc.threshold === null || lc.threshold === undefined ? "" : String(lc.threshold));
    }

    const mt = (cfg as any)?.model_training ?? null;
    if (mt && typeof mt === "object") {
      if (mt.num_boost_round !== undefined) setNumBoostRoundInput(String(mt.num_boost_round));
      if (mt.num_threads !== undefined) setNumThreadsInput(String(mt.num_threads));
    }

    const mi = (cfg as any)?.model_interpretation ?? null;
    if (mi && typeof mi === "object") {
      if (mi.max_samples !== undefined) setShapMaxSamplesInput(String(mi.max_samples));
      if (mi.max_display !== undefined) setShapMaxDisplayInput(String(mi.max_display));
    }

    const ma = (cfg as any)?.model_analysis ?? null;
    if (ma && typeof ma === "object") {
      const selected = Array.isArray(ma.selected_features) ? ma.selected_features : null;
      setSelectedFeaturesTextInput(selected ? selected.map((v: unknown) => String(v)).join("\n") : "");
      if (ma.max_features !== undefined) setMaxFeaturesInput(String(ma.max_features));
      if (ma.max_depth !== undefined) setMaxDepthInput(String(ma.max_depth));
      if (ma.min_samples_split !== undefined) setMinSamplesSplitInput(String(ma.min_samples_split));
      if (ma.min_samples_leaf !== undefined) setMinSamplesLeafInput(String(ma.min_samples_leaf));
      if (ma.min_rule_samples !== undefined) setMinRuleSamplesInput(String(ma.min_rule_samples));
      setAnalysisLabelThresholdInput(ma.label_threshold === null || ma.label_threshold === undefined ? "" : String(ma.label_threshold));
    }

    const bt = (cfg as any)?.backtest_construction ?? null;
    if (bt && typeof bt === "object") {
      if (bt.look_forward_bars !== undefined) setLookForwardBarsInput(String(bt.look_forward_bars));
      if (bt.pnl_mode) setPnlModeInput(String(bt.pnl_mode) as any);
      if (bt.backtest_type) setBacktestTypeInput(String(bt.backtest_type) as any);
      if (bt.filter_type) setBacktestFilterTypeInput(String(bt.filter_type) as any);
      if (bt.min_rule_confidence !== undefined) setMinRuleConfidenceInput(String(bt.min_rule_confidence));
      if (bt.win_profit !== undefined) setWinProfitInput(String(bt.win_profit));
      if (bt.loss_cost !== undefined) setLossCostInput(String(bt.loss_cost));
      if (bt.initial_balance !== undefined) setInitialBalanceInput(String(bt.initial_balance));
      if (bt.position_fraction !== undefined) setPositionFractionInput(String(bt.position_fraction));
      setPositionNotionalInput(bt.position_notional === null || bt.position_notional === undefined ? "" : String(bt.position_notional));
      if (bt.order_interval_minutes !== undefined) setOrderIntervalMinutesInput(String(bt.order_interval_minutes));
      if (bt.fee_rate !== undefined) setFeeRateInput(String(bt.fee_rate));
      if (bt.slippage_bps !== undefined) setSlippageBpsInput(String(bt.slippage_bps));
    }

    const wf = (cfg as any)?.walk_forward_evaluation ?? null;
    if (wf && typeof wf === "object") {
      setWalkForwardEnabledInput(Boolean(wf.enabled !== false));
      if (wf.train_bars !== undefined) setTrainBarsInput(String(wf.train_bars));
      if (wf.test_bars !== undefined) setTestBarsInput(String(wf.test_bars));
      if (wf.step_bars !== undefined) setStepBarsInput(String(wf.step_bars));
      if (wf.max_windows !== undefined) setMaxWindowsInput(String(wf.max_windows));
    }
  }

  useEffect(() => {
    if (rerunInitialized) return;
    if (!pipeline?.config) return;
    resetRerunForm();
    setRerunInitialized(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pipeline, rerunInitialized]);

  const equityPoints = useMemo(() => {
    const points = (data?.summary as any)?.charts?.backtest?.equity_curve?.points;
    return Array.isArray(points) ? points : [];
  }, [data]);

  const backtestStats = useMemo(() => {
    const stats = (data?.summary as any)?.charts?.backtest?.stats?.stats;
    return stats && typeof stats === "object" ? (stats as Record<string, unknown>) : null;
  }, [data]);

  const walkForwardEquityPoints = useMemo(() => {
    const points = (data?.summary as any)?.charts?.walk_forward?.equity_curve?.points;
    return Array.isArray(points) ? points : [];
  }, [data]);

  const walkForwardStats = useMemo(() => {
    const stats = (data?.summary as any)?.charts?.walk_forward?.stats;
    return stats && typeof stats === "object" ? (stats as Record<string, unknown>) : null;
  }, [data]);

  const walkForwardWindows = useMemo(() => {
    const windows = (walkForwardStats as any)?.windows;
    return Array.isArray(windows) ? (windows as any[]) : [];
  }, [walkForwardStats]);

  const walkForwardOverall = (walkForwardStats as any)?.overall ?? null;
  const walkForwardStatus = (walkForwardStats as any)?.status ?? null;
  const walkForwardReason = (walkForwardStats as any)?.reason ?? null;
  const walkForwardAutoAdjusted = Boolean((walkForwardStats as any)?.config?.auto_adjusted);
  const walkForwardEffectiveConfig = (walkForwardStats as any)?.config?.effective ?? null;

  const shap = (data?.summary as any)?.charts?.shap ?? null;
  const shapMeta = (shap as any)?.metadata ?? null;

  const shapTopFeatures = useMemo(() => {
    const top = shapMeta && typeof shapMeta === "object" ? (shapMeta as any)?.top20_importance : null;
    if (!top || typeof top !== "object") return [];
    return Object.entries(top as Record<string, unknown>)
      .map(([k, v]) => [k, toNumber(v)] as const)
      .filter(([, v]) => v !== null)
      .sort((a, b) => (b[1] as number) - (a[1] as number))
      .slice(0, 10);
  }, [shapMeta]);

  const backtestSummary = useMemo(() => {
    if (!backtestStats) return null;

    const profitRate = toNumber((backtestStats as any)?.profit_rate);
    const maxDrawdown = toNumber((backtestStats as any)?.max_drawdown);
    const totalTrades = toNumber((backtestStats as any)?.total_trades);
    const winRate = toNumber((backtestStats as any)?.win_rate);

    const initialBalance = toNumber((backtestStats as any)?.initial_balance);
    const finalBalance = toNumber((backtestStats as any)?.final_balance);
    const feesPaid = toNumber((backtestStats as any)?.fees_paid);

    const feeRate = toNumber((backtestStats as any)?.fee_rate);
    const slippageBps = toNumber((backtestStats as any)?.slippage_bps);
    const pnlMode = String((backtestStats as any)?.pnl_mode ?? "");

    const positionFraction = toNumber((backtestStats as any)?.position_fraction);
    const positionNotional = toNumber((backtestStats as any)?.position_notional);

    const feeRatio =
      initialBalance && feesPaid !== null && Number.isFinite(initialBalance) && initialBalance > 0
        ? feesPaid / initialBalance
        : null;

    const lines: string[] = [];
    lines.push(`收益：净收益率 ${formatPercent(profitRate)} · 最大回撤 ${formatPercent(maxDrawdown)}`);
    lines.push(`样本：交易 ${totalTrades ?? "-"} 笔 · 胜率 ${formatPercent(winRate)}`);
    lines.push(
      `资金：${formatNumber(initialBalance)} → ${formatNumber(finalBalance)} · 手续费 ${formatNumber(feesPaid)}（约占期初 ${formatPercent(
        feeRatio,
      )}）`,
    );

    const modeText = pnlMode === "price" ? "按价格变化计算（更接近真实）" : pnlMode === "fixed" ? "固定赢亏（更像示例）" : "-";
    const feeText = feeRate !== null ? `${formatPercent(feeRate, 3)}/边` : "-";
    const slippageText = slippageBps !== null ? `${formatNumber(slippageBps, 2)} bps` : "-";
    const positionText =
      positionNotional !== null
        ? `每笔约 ${formatNumber(positionNotional)}（固定）`
        : `每笔约 ${formatPercent(positionFraction ?? 1, 0)}（按余额）`;
    lines.push(`回测假设：收益 ${modeText} · 手续费 ${feeText} · 滑点 ${slippageText} · 仓位 ${positionText}`);

    const tips: string[] = [];
    const warnings: string[] = [];

    if (profitRate !== null) {
      tips.push(profitRate > 0 ? "这段时间内回测结果是盈利的（不代表未来）。" : "这段时间内回测结果是亏损的（不代表一定不可用）。");
    }

    if (maxDrawdown !== null) {
      if (maxDrawdown > 0.3) warnings.push("回撤很大：波动/风险偏高。");
      else if (maxDrawdown > 0.2) warnings.push("回撤偏大：风险不低。");
    }

    if (totalTrades !== null) {
      if (totalTrades < 10) warnings.push("交易次数很少：结论很不稳定。");
      else if (totalTrades < 30) warnings.push("交易次数偏少：建议拉长时间再看。");
    }

    if (feeRatio !== null) {
      if (feeRatio > 0.1) warnings.push("手续费占比很高：可能被成本吃掉收益。");
      else if (feeRatio > 0.03) warnings.push("手续费占比偏高：需要关注交易频率与成本假设。");
    }

    if (walkForwardStatus && String(walkForwardStatus) !== "success") {
      warnings.push(`滚动验证未产出：${walkForwardReason ? String(walkForwardReason) : "已跳过"}`);
    } else if (walkForwardOverall) {
      const windows = toNumber((walkForwardOverall as any)?.windows);
      const profitableWindows = toNumber((walkForwardOverall as any)?.profitable_windows);
      if (windows && profitableWindows !== null && windows > 0) {
        const ratio = profitableWindows / windows;
        tips.push(ratio >= 0.7 ? "滚动验证大多数窗口盈利：稳定性相对更好。" : "滚动验证盈利窗口不多：稳定性一般。");
      }
    }

    const score = (() => {
      let s = 0;
      if (profitRate !== null) s += profitRate > 0 ? 2 : -2;
      if (maxDrawdown !== null) {
        if (maxDrawdown < 0.1) s += 2;
        else if (maxDrawdown < 0.2) s += 1;
        else if (maxDrawdown > 0.3) s -= 2;
        else s -= 1;
      }
      if (totalTrades !== null) {
        if (totalTrades >= 100) s += 2;
        else if (totalTrades >= 30) s += 1;
        else if (totalTrades < 10) s -= 2;
        else s -= 1;
      }
      if (feeRatio !== null) {
        if (feeRatio < 0.02) s += 1;
        else if (feeRatio > 0.1) s -= 2;
        else if (feeRatio > 0.03) s -= 1;
      }
      return s;
    })();

    const verdict =
      profitRate === null
        ? { label: "暂无结果", variant: "secondary" as const }
        : score >= 3
          ? { label: "看起来不错", variant: "success" as const }
          : score >= 0
            ? { label: "一般（建议继续验证）", variant: "secondary" as const }
            : { label: "偏弱（建议先优化）", variant: "destructive" as const };

    const headlineParts: string[] = [];
    if (profitRate !== null) headlineParts.push(profitRate > 0 ? "赚钱" : "亏钱");
    if (maxDrawdown !== null) headlineParts.push(maxDrawdown > 0.2 ? "回撤偏大" : "回撤可控");
    if (totalTrades !== null) headlineParts.push(totalTrades < 30 ? "样本偏少" : "样本量还行");
    const headline = headlineParts.length > 0 ? `一句话：这次回测 ${headlineParts.join(" · ")}。` : "一句话：回测已完成。";

    const next: string[] = [];
    if (totalTrades !== null && totalTrades < 30) next.push("把时间范围拉长（例如 3~6 个月）再回测一次，先把样本做大。");
    if (maxDrawdown !== null && maxDrawdown > 0.2) next.push("先把仓位比例调小（例如 50% 或更低），观察回撤是否明显改善。");
    if (feeRatio !== null && feeRatio > 0.03) next.push("如果手续费占比高：尝试减少交易频率（加大 order_interval 或提高过滤阈值）。");
    if (walkForwardStatus && String(walkForwardStatus) !== "success")
      next.push("滚动验证提示数据不足：要么拉长时间范围，要么把窗口参数调小再试。");

    return { lines, tips, warnings, verdict, headline, next };
  }, [backtestStats, walkForwardOverall, walkForwardReason, walkForwardStatus]);

  async function onRerun() {
    setRerunError(null);
    setIsRerunning(true);
    try {
      const cfg = pipeline?.config;
      if (!cfg || typeof cfg !== "object") {
        throw new Error("当前 run 缺少 pipeline.config，无法重跑。");
      }

      const symbol = rerunSymbol.trim();
      const startDate = rerunStartDate.trim();
      const endDate = rerunEndDate.trim();
      const interval = rerunInterval.trim();

      if (!symbol) throw new Error("币种不能为空。");
      if (!startDate) throw new Error("开始日期不能为空。");
      if (!endDate) throw new Error("结束日期不能为空。");
      if (startDate > endDate) throw new Error("开始日期不能晚于结束日期。");
      if (!interval) throw new Error("周期不能为空。");

      const alphaTypes = alphaTypesInput.map((t) => String(t).trim()).filter(Boolean);
      if (alphaTypes.length === 0) throw new Error("alpha_types 至少选择 1 个。");
      const instrumentName = instrumentNameInput.trim() ? instrumentNameInput.trim() : null;

      const labelWindow = Number(labelWindowInput);
      const labelLookForward = Number(labelLookForwardInput);
      if (!Number.isFinite(labelWindow) || labelWindow < 3 || !Number.isInteger(labelWindow))
        throw new Error("标签参数：window 需要是 >= 3 的整数。");
      if (labelWindow % 2 === 0) throw new Error("标签参数：window 建议为奇数（例如 29）。");
      if (!Number.isFinite(labelLookForward) || labelLookForward < 1 || !Number.isInteger(labelLookForward))
        throw new Error("标签参数：look_forward 需要是 >= 1 的整数。");

      const labelType = String(labelTypeInput).trim();
      const labelFilterType = String(labelFilterTypeInput).trim();
      const labelThresholdText = String(labelThresholdInput).trim();
      const labelThreshold = labelThresholdText ? Number(labelThresholdText) : null;
      if (labelThresholdText && (!Number.isFinite(labelThreshold) || labelThreshold === null))
        throw new Error("标签参数：threshold 需要是数字，或留空使用默认值。");

      const numBoostRound = Number(numBoostRoundInput);
      const numThreads = Number(numThreadsInput);
      if (!Number.isFinite(numBoostRound) || numBoostRound < 1 || !Number.isInteger(numBoostRound))
        throw new Error("训练参数：num_boost_round 需要是 >= 1 的整数。");
      if (!Number.isFinite(numThreads) || numThreads < 1 || !Number.isInteger(numThreads))
        throw new Error("训练参数：num_threads 需要是 >= 1 的整数。");

      const shapMaxSamples = Number(shapMaxSamplesInput);
      const shapMaxDisplay = Number(shapMaxDisplayInput);
      if (!Number.isFinite(shapMaxSamples) || shapMaxSamples < 1 || !Number.isInteger(shapMaxSamples))
        throw new Error("解释参数：max_samples 需要是 >= 1 的整数。");
      if (!Number.isFinite(shapMaxDisplay) || shapMaxDisplay < 1 || !Number.isInteger(shapMaxDisplay))
        throw new Error("解释参数：max_display 需要是 >= 1 的整数。");

      const maxFeatures = Number(maxFeaturesInput);
      const maxDepth = Number(maxDepthInput);
      const minSamplesSplit = Number(minSamplesSplitInput);
      const minSamplesLeaf = Number(minSamplesLeafInput);
      const minRuleSamples = Number(minRuleSamplesInput);
      if (!Number.isFinite(maxFeatures) || maxFeatures < 1 || !Number.isInteger(maxFeatures))
        throw new Error("规则参数：max_features 需要是 >= 1 的整数。");
      if (!Number.isFinite(maxDepth) || maxDepth < 1 || !Number.isInteger(maxDepth))
        throw new Error("规则参数：max_depth 需要是 >= 1 的整数。");
      if (!Number.isFinite(minSamplesSplit) || minSamplesSplit < 2 || !Number.isInteger(minSamplesSplit))
        throw new Error("规则参数：min_samples_split 需要是 >= 2 的整数。");
      if (!Number.isFinite(minSamplesLeaf) || minSamplesLeaf < 1 || !Number.isInteger(minSamplesLeaf))
        throw new Error("规则参数：min_samples_leaf 需要是 >= 1 的整数。");
      if (!Number.isFinite(minRuleSamples) || minRuleSamples < 1 || !Number.isInteger(minRuleSamples))
        throw new Error("规则参数：min_rule_samples 需要是 >= 1 的整数。");

      const analysisLabelThresholdText = String(analysisLabelThresholdInput).trim();
      const analysisLabelThreshold = analysisLabelThresholdText ? Number(analysisLabelThresholdText) : null;
      if (analysisLabelThresholdText && (!Number.isFinite(analysisLabelThreshold) || analysisLabelThreshold === null))
        throw new Error("规则参数：label_threshold 需要是数字，或留空使用自动值。");

      const selectedFeatures = selectedFeaturesTextInput
        .split(/\r?\n|,/g)
        .map((s) => s.trim())
        .filter(Boolean);

      const positionFraction = Number(positionFractionInput);
      if (!Number.isFinite(positionFraction) || positionFraction <= 0 || positionFraction > 1) {
        throw new Error("仓位比例需要在 (0, 1] 范围内。");
      }

      const positionNotionalText = String(positionNotionalInput).trim();
      const positionNotional = positionNotionalText ? Number(positionNotionalText) : null;
      if (positionNotionalText && (!Number.isFinite(positionNotional) || positionNotional === null || positionNotional <= 0))
        throw new Error("固定名义仓位需要是 > 0 的数字，或留空。");

      const orderIntervalMinutes = Number(orderIntervalMinutesInput);
      if (
        !Number.isFinite(orderIntervalMinutes) ||
        orderIntervalMinutes < 0 ||
        !Number.isInteger(orderIntervalMinutes)
      ) {
        throw new Error("下单间隔需要是 >= 0 的整数（分钟）。");
      }

      const feeRate = Number(feeRateInput);
      if (!Number.isFinite(feeRate) || feeRate < 0) {
        throw new Error("手续费费率需要是 >= 0 的数字。");
      }

      const slippageBps = Number(slippageBpsInput);
      if (!Number.isFinite(slippageBps) || slippageBps < 0) {
        throw new Error("滑点需要是 >= 0 的数字（bps）。");
      }

      const lookForwardBars = Number(lookForwardBarsInput);
      if (!Number.isFinite(lookForwardBars) || lookForwardBars < 1 || !Number.isInteger(lookForwardBars))
        throw new Error("look_forward_bars 需要是 >= 1 的整数。");

      const minRuleConfidence = Number(minRuleConfidenceInput);
      if (!Number.isFinite(minRuleConfidence) || minRuleConfidence < 0 || minRuleConfidence > 1)
        throw new Error("min_rule_confidence 需要在 [0, 1] 范围内。");

      const winProfit = Number(winProfitInput);
      const lossCost = Number(lossCostInput);
      const initialBalance = Number(initialBalanceInput);
      if (!Number.isFinite(winProfit) || winProfit <= 0) throw new Error("win_profit 需要是 > 0 的数字。");
      if (!Number.isFinite(lossCost) || lossCost <= 0) throw new Error("loss_cost 需要是 > 0 的数字。");
      if (!Number.isFinite(initialBalance) || initialBalance <= 0) throw new Error("initial_balance 需要是 > 0 的数字。");

      const trainBars = Number(trainBarsInput);
      const testBars = Number(testBarsInput);
      const stepBars = Number(stepBarsInput);
      const maxWindows = Number(maxWindowsInput);
      if (!Number.isFinite(trainBars) || trainBars < 1 || !Number.isInteger(trainBars)) {
        throw new Error("train_bars 需要是 >= 1 的整数。");
      }
      if (!Number.isFinite(testBars) || testBars < 1 || !Number.isInteger(testBars)) {
        throw new Error("test_bars 需要是 >= 1 的整数。");
      }
      if (!Number.isFinite(stepBars) || stepBars < 1 || !Number.isInteger(stepBars)) {
        throw new Error("step_bars 需要是 >= 1 的整数。");
      }
      if (!Number.isFinite(maxWindows) || maxWindows < 1 || !Number.isInteger(maxWindows)) {
        throw new Error("max_windows 需要是 >= 1 的整数。");
      }

      const config_overrides = cloneJson(cfg as any) as Record<string, JsonValue>;

      (config_overrides as any).data_download = {
        ...((config_overrides as any).data_download ?? {}),
        symbol,
        start_date: startDate,
        end_date: endDate,
        interval,
      };

      (config_overrides as any).feature_calculation = {
        ...((config_overrides as any).feature_calculation ?? {}),
        alpha_types: alphaTypes,
        instrument_name: instrumentName,
      };

      (config_overrides as any).label_calculation = {
        ...((config_overrides as any).label_calculation ?? {}),
        window: labelWindow,
        look_forward: labelLookForward,
        label_type: labelType,
        filter_type: labelFilterType,
        threshold: labelThresholdText ? (labelThreshold as number) : null,
      };

      (config_overrides as any).model_training = {
        ...((config_overrides as any).model_training ?? {}),
        num_boost_round: numBoostRound,
        num_threads: numThreads,
      };

      (config_overrides as any).model_interpretation = {
        ...((config_overrides as any).model_interpretation ?? {}),
        max_samples: shapMaxSamples,
        max_display: shapMaxDisplay,
      };

      (config_overrides as any).model_analysis = {
        ...((config_overrides as any).model_analysis ?? {}),
        selected_features: selectedFeatures.length > 0 ? selectedFeatures : null,
        max_features: maxFeatures,
        max_depth: maxDepth,
        min_samples_split: minSamplesSplit,
        min_samples_leaf: minSamplesLeaf,
        min_rule_samples: minRuleSamples,
        label_threshold: analysisLabelThresholdText ? (analysisLabelThreshold as number) : null,
      };

      (config_overrides as any).backtest_construction = {
        ...((config_overrides as any).backtest_construction ?? {}),
        look_forward_bars: lookForwardBars,
        pnl_mode: pnlModeInput,
        backtest_type: backtestTypeInput,
        filter_type: backtestFilterTypeInput,
        min_rule_confidence: minRuleConfidence,
        win_profit: winProfit,
        loss_cost: lossCost,
        initial_balance: initialBalance,
        position_fraction: positionFraction,
        position_notional: positionNotionalText ? (positionNotional as number) : null,
        order_interval_minutes: orderIntervalMinutes,
        fee_rate: feeRate,
        slippage_bps: slippageBps,
      };

      (config_overrides as any).walk_forward_evaluation = {
        ...((config_overrides as any).walk_forward_evaluation ?? {}),
        enabled: Boolean(walkForwardEnabledInput),
        train_bars: trainBars,
        test_bars: testBars,
        step_bars: stepBars,
        max_windows: maxWindows,
      };

      const response = await api.runPipeline({
        workflow_name: "default",
        template_id: null,
        symbol,
        start_date: startDate,
        end_date: endDate,
        interval,
        config_overrides,
      });

      navigate(`/runs/${response.run_id}`);
    } catch (e) {
      setRerunError(e instanceof Error ? e.message : String(e));
    } finally {
      setIsRerunning(false);
    }
  }

  async function onCancel() {
    if (!runId) return;
    setIsCancelling(true);
    try {
      await api.cancelRun(runId);
      const next = await api.getRunSummary(runId);
      setData(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setIsCancelling(false);
    }
  }

  return (
    <div className="grid gap-6">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0 md:col-span-1">
              <CardTitle className="text-lg">运行详情</CardTitle>
              <CardDescription className="break-all">{runId}</CardDescription>
            </div>
            <div className="flex items-center gap-2">
              {data ? statusBadge(String(data.run.status)) : null}
              <Button variant="secondary" onClick={() => navigate("/")}>
                返回
              </Button>
              <Button
                variant="destructive"
                onClick={onCancel}
                disabled={isCancelling || !data || isTerminalStatus(String(data.run.status))}
              >
                {isCancelling ? "取消中…" : "取消运行"}
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="grid gap-4">
          {error ? <div className="text-sm text-destructive">错误：{error}</div> : null}

          {dataDownload ? (
            <div className="grid gap-1 text-sm text-muted-foreground">
              <div>
                任务：{String(dataDownload.symbol)} · {String(dataDownload.start_date)} ~ {String(dataDownload.end_date)} ·{" "}
                {String(dataDownload.interval)}
              </div>
            </div>
          ) : null}

          <Separator />

          <div className="grid gap-2">
            <div className="text-sm font-medium">步骤进度</div>
            <div className="grid gap-2">
              {(data?.steps || []).map((s) => (
                <div key={s.step_id} className="rounded-md border p-3">
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0 md:col-span-1">
                      <div className="truncate text-sm font-medium">{STEP_LABEL[s.name] ?? s.name}</div>
                      <div className="truncate text-xs text-muted-foreground">
                        {stepStatusText(s.status)} · {s.message || ""}
                      </div>
                    </div>
                    <div className="shrink-0 text-xs text-muted-foreground">{s.progress}%</div>
                  </div>
                  <div className="mt-2">
                    <Progress value={s.progress} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        </CardContent>
      </Card>

      <details className="rounded-xl border bg-card text-card-foreground shadow-sm">
        <summary className="cursor-pointer list-none select-none p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="grid gap-1">
              <div className="text-lg font-semibold leading-none tracking-tight">快速重跑</div>
              <div className="text-sm text-muted-foreground">基于本次参数快速改一改再跑一遍（会生成新的 run_id）。</div>
            </div>
            <div className="text-xs text-muted-foreground">点击展开/收起</div>
          </div>
        </summary>
        <div className="grid gap-4 px-6 pb-6 pt-0">
          {pipeline?.config ? (
            <div className="rounded-md border px-3 py-2 text-sm text-muted-foreground">
              提示：这里默认复用本次 run 的完整 pipeline 配置，避免模板变化导致“同样参数却跑出不一样结果”。
            </div>
          ) : (
            <div className="rounded-md border px-3 py-2 text-sm text-muted-foreground">
              当前 run 没有 pipeline.config（可能不是“一键流程”创建的），暂时无法从这里重跑。
            </div>
          )}

          {rerunError ? <div className="text-sm text-destructive">重跑失败：{rerunError}</div> : null}

          <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
            <div className="grid gap-2">
              <Label>币种</Label>
              <Input value={rerunSymbol} onChange={(e) => setRerunSymbol(e.target.value)} placeholder="例如 BTCUSDT" />
            </div>
            <div className="grid gap-2">
              <Label>开始日期</Label>
              <Input type="date" value={rerunStartDate} onChange={(e) => setRerunStartDate(e.target.value)} />
            </div>
            <div className="grid gap-2">
              <Label>结束日期</Label>
              <Input type="date" value={rerunEndDate} onChange={(e) => setRerunEndDate(e.target.value)} />
            </div>
            <div className="grid gap-2">
              <Label>周期</Label>
              <Input value={rerunInterval} onChange={(e) => setRerunInterval(e.target.value)} placeholder="例如 15m" />
            </div>
          </div>

          <details className="rounded-md border p-3">
            <summary className="cursor-pointer text-sm text-muted-foreground">特征（alpha_types）</summary>
            <div className="mt-3 grid gap-3">
              <div className="text-xs text-muted-foreground">alpha158/alpha216 等表示“一套技术指标特征”。先用 alpha158 最稳。</div>
              <div className="grid gap-2 rounded-md border p-3">
                {ALPHA_TYPE_OPTIONS.map((opt) => (
                  <label key={opt.value} className="flex items-start gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={alphaTypesInput.includes(opt.value)}
                      onChange={(e) => {
                        const checked = e.target.checked;
                        setAlphaTypesInput((prev) => {
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
              <div className="grid gap-1">
                <Label>instrument_name（可选）</Label>
                <Input value={instrumentNameInput} onChange={(e) => setInstrumentNameInput(e.target.value)} placeholder="留空即可" />
                <div className="text-xs text-muted-foreground">
                  默认（本次）：{formatDefaultValue((pipeline?.config as any)?.feature_calculation?.instrument_name)} · 推荐：留空
                </div>
              </div>
            </div>
          </details>

          <details className="rounded-md border p-3">
            <summary className="cursor-pointer text-sm text-muted-foreground">标签（label_calculation）</summary>
            <div className="mt-3 grid gap-3">
              <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                <div className="grid gap-1">
                  <Label>window（建议奇数）</Label>
                  <Input type="number" min={3} step={2} value={labelWindowInput} onChange={(e) => setLabelWindowInput(e.target.value)} />
                  <div className="text-xs text-muted-foreground">默认（本次）：{formatDefaultValue((pipeline?.config as any)?.label_calculation?.window)} · 推荐：29</div>
                </div>
                <div className="grid gap-1">
                  <Label>look_forward</Label>
                  <Input
                    type="number"
                    min={1}
                    step={1}
                    value={labelLookForwardInput}
                    onChange={(e) => setLabelLookForwardInput(e.target.value)}
                  />
                  <div className="text-xs text-muted-foreground">默认（本次）：{formatDefaultValue((pipeline?.config as any)?.label_calculation?.look_forward)} · 推荐：10</div>
                </div>
                <div className="grid gap-1">
                  <Label>label_type</Label>
                  <Select value={labelTypeInput} onValueChange={(v) => setLabelTypeInput(v as any)}>
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
                </div>
              </div>

              <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                <div className="grid gap-1">
                  <Label>filter_type</Label>
                  <Select value={labelFilterTypeInput} onValueChange={(v) => setLabelFilterTypeInput(v as any)}>
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
                </div>

                <div className="grid gap-1">
                  <Label>threshold（可留空=自动）</Label>
                  <Input
                    type="number"
                    step={0.1}
                    value={labelThresholdInput}
                    onChange={(e) => setLabelThresholdInput(e.target.value)}
                    placeholder="留空=使用默认阈值"
                  />
                  <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                    <span>默认（本次）：{formatDefaultValue((pipeline?.config as any)?.label_calculation?.threshold)}</span>
                    <span>推荐：{rerunRecommendedThreshold === null ? "（无）" : String(rerunRecommendedThreshold)}</span>
                    {rerunRecommendedThreshold !== null ? (
                      <Button
                        type="button"
                        variant="secondary"
                        className="h-7 px-2 text-xs"
                        onClick={() => setLabelThresholdInput(String(rerunRecommendedThreshold))}
                      >
                        应用推荐
                      </Button>
                    ) : null}
                    <Button
                      type="button"
                      variant="secondary"
                      className="h-7 px-2 text-xs"
                      onClick={() => setLabelThresholdInput("")}
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
          </details>

          <details className="rounded-md border p-3">
            <summary className="cursor-pointer text-sm text-muted-foreground">训练（model_training）</summary>
            <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
              <div className="grid gap-1">
                <Label>num_boost_round</Label>
                <Input type="number" min={1} step={50} value={numBoostRoundInput} onChange={(e) => setNumBoostRoundInput(e.target.value)} />
                <div className="text-xs text-muted-foreground">默认（本次）：{formatDefaultValue((pipeline?.config as any)?.model_training?.num_boost_round)} · 推荐：500</div>
              </div>
              <div className="grid gap-1">
                <Label>num_threads</Label>
                <Input type="number" min={1} step={1} value={numThreadsInput} onChange={(e) => setNumThreadsInput(e.target.value)} />
                <div className="text-xs text-muted-foreground">默认（本次）：{formatDefaultValue((pipeline?.config as any)?.model_training?.num_threads)} · 推荐：4</div>
              </div>
            </div>
          </details>

          <details className="rounded-md border p-3">
            <summary className="cursor-pointer text-sm text-muted-foreground">解释（SHAP）</summary>
            <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
              <div className="grid gap-1">
                <Label>max_samples</Label>
                <Input type="number" min={1} step={500} value={shapMaxSamplesInput} onChange={(e) => setShapMaxSamplesInput(e.target.value)} />
                <div className="text-xs text-muted-foreground">默认（本次）：{formatDefaultValue((pipeline?.config as any)?.model_interpretation?.max_samples)} · 推荐：5000</div>
              </div>
              <div className="grid gap-1">
                <Label>max_display</Label>
                <Input type="number" min={1} step={1} value={shapMaxDisplayInput} onChange={(e) => setShapMaxDisplayInput(e.target.value)} />
                <div className="text-xs text-muted-foreground">默认（本次）：{formatDefaultValue((pipeline?.config as any)?.model_interpretation?.max_display)} · 推荐：20</div>
              </div>
            </div>
          </details>

          <details className="rounded-md border p-3">
            <summary className="cursor-pointer text-sm text-muted-foreground">规则（model_analysis）</summary>
            <div className="mt-3 grid gap-3">
              <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                <div className="grid gap-1">
                  <Label>max_features</Label>
                  <Input type="number" min={1} step={1} value={maxFeaturesInput} onChange={(e) => setMaxFeaturesInput(e.target.value)} />
                </div>
                <div className="grid gap-1">
                  <Label>max_depth</Label>
                  <Input type="number" min={1} step={1} value={maxDepthInput} onChange={(e) => setMaxDepthInput(e.target.value)} />
                </div>
                <div className="grid gap-1">
                  <Label>min_rule_samples</Label>
                  <Input type="number" min={1} step={10} value={minRuleSamplesInput} onChange={(e) => setMinRuleSamplesInput(e.target.value)} />
                </div>
              </div>

              <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                <div className="grid gap-1">
                  <Label>min_samples_split</Label>
                  <Input type="number" min={2} step={10} value={minSamplesSplitInput} onChange={(e) => setMinSamplesSplitInput(e.target.value)} />
                </div>
                <div className="grid gap-1">
                  <Label>min_samples_leaf</Label>
                  <Input type="number" min={1} step={10} value={minSamplesLeafInput} onChange={(e) => setMinSamplesLeafInput(e.target.value)} />
                </div>
                <div className="grid gap-1">
                  <Label>label_threshold（可留空=自动）</Label>
                  <Input
                    type="number"
                    step={0.01}
                    value={analysisLabelThresholdInput}
                    onChange={(e) => setAnalysisLabelThresholdInput(e.target.value)}
                    placeholder="留空=使用 label 中位数"
                  />
                </div>
              </div>

              <div className="grid gap-1">
                <Label>selected_features（可选，换行或逗号分隔）</Label>
                <Textarea
                  value={selectedFeaturesTextInput}
                  onChange={(e) => setSelectedFeaturesTextInput(e.target.value)}
                  placeholder="留空=自动从训练得到的 top importance 推导"
                  className="min-h-24 font-mono text-xs"
                />
              </div>
            </div>
          </details>

          <details className="rounded-md border p-3">
            <summary className="cursor-pointer text-sm text-muted-foreground">回测（成本 / 仓位 / 频率）</summary>
            <div className="mt-3 grid gap-3">
              <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                <div className="grid gap-1">
                  <Label>backtest_type</Label>
                  <Select value={backtestTypeInput} onValueChange={(v) => setBacktestTypeInput(v as any)}>
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
                </div>
                <div className="grid gap-1">
                  <Label>pnl_mode</Label>
                  <Select value={pnlModeInput} onValueChange={(v) => setPnlModeInput(v as any)}>
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
                </div>
                <div className="grid gap-1">
                  <Label>look_forward_bars</Label>
                  <Input
                    type="number"
                    min={1}
                    step={1}
                    value={lookForwardBarsInput}
                    onChange={(e) => setLookForwardBarsInput(e.target.value)}
                  />
                </div>
              </div>

              <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
                <div className="grid gap-1">
                  <Label>position_fraction</Label>
                  <Input
                    type="number"
                    min={0}
                    max={1}
                    step={0.05}
                    value={positionFractionInput}
                    onChange={(e) => setPositionFractionInput(e.target.value)}
                  />
                </div>
                <div className="grid gap-1">
                  <Label>position_notional（可空）</Label>
                  <Input
                    type="number"
                    min={0}
                    step={10}
                    value={positionNotionalInput}
                    onChange={(e) => setPositionNotionalInput(e.target.value)}
                    placeholder="留空=使用仓位比例"
                  />
                </div>
                <div className="grid gap-1">
                  <Label>order_interval_minutes</Label>
                  <Input
                    type="number"
                    min={0}
                    step={1}
                    value={orderIntervalMinutesInput}
                    onChange={(e) => setOrderIntervalMinutesInput(e.target.value)}
                  />
                  {rerunMinutesPerBar !== null ? (
                    <div className="mt-1">
                      <Button
                        type="button"
                        variant="secondary"
                        className="h-7 px-2 text-xs"
                        onClick={() => setOrderIntervalMinutesInput(String(rerunMinutesPerBar))}
                      >
                        同步为 1 个 bar（{rerunMinutesPerBar} 分钟）
                      </Button>
                    </div>
                  ) : null}
                </div>
                <div className="grid gap-1">
                  <Label>min_rule_confidence</Label>
                  <Input
                    type="number"
                    min={0}
                    max={1}
                    step={0.05}
                    value={minRuleConfidenceInput}
                    onChange={(e) => setMinRuleConfidenceInput(e.target.value)}
                  />
                </div>
              </div>

              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <div className="grid gap-1">
                  <Label>filter_type</Label>
                  <Select value={backtestFilterTypeInput} onValueChange={(v) => setBacktestFilterTypeInput(v as any)}>
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
                </div>
                <div className="grid gap-1">
                  <Label>fee_rate（每边）</Label>
                  <Input type="number" min={0} step={0.0001} value={feeRateInput} onChange={(e) => setFeeRateInput(e.target.value)} />
                </div>
                <div className="grid gap-1">
                  <Label>slippage_bps</Label>
                  <Input type="number" min={0} step={1} value={slippageBpsInput} onChange={(e) => setSlippageBpsInput(e.target.value)} />
                </div>
                <div className="grid gap-1">
                  <Label>initial_balance</Label>
                  <Input
                    type="number"
                    min={1}
                    step={100}
                    value={initialBalanceInput}
                    onChange={(e) => setInitialBalanceInput(e.target.value)}
                  />
                </div>
              </div>
            </div>
          </details>

          <details className="rounded-md border p-3">
            <summary className="cursor-pointer text-sm text-muted-foreground">滚动验证（Walk-forward）</summary>
            <div className="mt-3 grid gap-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={backtestSummary?.verdict?.variant ?? "secondary"}>
                  {backtestSummary?.verdict?.label ?? "暂无结果"}
                </Badge>
                <div className="text-sm text-muted-foreground">{backtestSummary?.headline ?? "回测已完成"}</div>
              </div>

              <div className="grid gap-1 text-sm">
                {backtestSummary?.lines?.map((t) => (
                  <div key={t} className="text-muted-foreground">
                    {t}
                  </div>
                ))}
              </div>

              {backtestSummary?.warnings?.length > 0 && (
                <div className="rounded-md border px-3 py-2 text-sm text-muted-foreground">
                  <div className="font-medium text-foreground">需要注意</div>
                  <ul className="mt-1 list-disc space-y-1 pl-5">
                    {backtestSummary?.warnings?.map((t) => (
                      <li key={t}>{t}</li>
                    ))}
                  </ul>
                </div>
              )}

              {backtestSummary?.tips?.length > 0 && (
                <div className="rounded-md border px-3 py-2 text-sm text-muted-foreground">
                  <div className="font-medium text-foreground">怎么理解</div>
                  <ul className="mt-1 list-disc space-y-1 pl-5">
                    {backtestSummary?.tips?.map((t) => (
                      <li key={t}>{t}</li>
                    ))}
                  </ul>
                </div>
              )}

              {backtestSummary?.next?.length > 0 && (
                <div className="rounded-md border px-3 py-2 text-sm text-muted-foreground">
                  <div className="font-medium text-foreground">下一步建议</div>
                  <ul className="mt-1 list-disc space-y-1 pl-5">
                    {backtestSummary?.next?.map((t) => (
                      <li key={t}>{t}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </details>

          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={onRerun} disabled={isRerunning || !pipeline?.config}>
              {isRerunning ? "重跑中…" : "用这些参数重跑"}
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                resetRerunForm();
                setRerunError(null);
              }}
              disabled={isRerunning || !pipeline?.config}
            >
              重置为本次参数
            </Button>
          </div>
        </div>
      </details>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">回测总结</CardTitle>
          <CardDescription>把关键数字翻译成“好不好看得懂”的结论（仅作参考）。</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3">
          {!backtestSummary ? (
            <div className="text-sm text-muted-foreground">回测尚未生成</div>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={backtestSummary.verdict.variant}>{backtestSummary.verdict.label}</Badge>
                <div className="text-sm text-muted-foreground">{backtestSummary.headline}</div>
              </div>

              <div className="grid gap-1 text-sm">
                {backtestSummary.lines.map((t) => (
                  <div key={t} className="text-muted-foreground">
                    {t}
                  </div>
                ))}
              </div>

              {backtestSummary.warnings.length > 0 && (
                <div className="rounded-md border px-3 py-2 text-sm text-muted-foreground">
                  <div className="font-medium text-foreground">需要注意</div>
                  <ul className="mt-1 list-disc space-y-1 pl-5">
                    {backtestSummary.warnings.map((t) => (
                      <li key={t}>{t}</li>
                    ))}
                  </ul>
                </div>
              )}

              {backtestSummary.tips.length > 0 && (
                <div className="rounded-md border px-3 py-2 text-sm text-muted-foreground">
                  <div className="font-medium text-foreground">怎么理解</div>
                  <ul className="mt-1 list-disc space-y-1 pl-5">
                    {backtestSummary.tips.map((t) => (
                      <li key={t}>{t}</li>
                    ))}
                  </ul>
                </div>
              )}

              {backtestSummary.next.length > 0 && (
                <div className="rounded-md border px-3 py-2 text-sm text-muted-foreground">
                  <div className="font-medium text-foreground">下一步建议</div>
                  <ul className="mt-1 list-disc space-y-1 pl-5">
                    {backtestSummary.next.map((t) => (
                      <li key={t}>{t}</li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-6 md:grid-cols-2">
        <Card className="md:col-span-2">
          <CardHeader>
            <CardTitle className="text-lg">回测曲线</CardTitle>
            <CardDescription>balance 随时间变化（来自 backtest 产物）。</CardDescription>
          </CardHeader>
          <CardContent>
            {equityPoints.length === 0 ? (
              <div className="text-sm text-muted-foreground">回测曲线尚未生成</div>
            ) : (
              <div className="h-[360px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={equityPoints}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis
                      dataKey="datetime"
                      tickFormatter={(v) => String(v).slice(5, 16).replace("T", " ")}
                      minTickGap={32}
                    />
                    <YAxis />
                    <Tooltip
                      labelFormatter={(v) => String(v)}
                      formatter={(value) => [value, "balance"]}
                    />
                    <Line type="monotone" dataKey="balance" dot={false} stroke="hsl(var(--primary))" strokeWidth={2} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="min-w-0 md:col-span-1">
          <CardHeader>
            <CardTitle className="text-lg">回测统计</CardTitle>
            <CardDescription>核心指标（来自 backtest_stats.json）。</CardDescription>
          </CardHeader>
          <CardContent>
            {!backtestStats ? (
              <div className="text-sm text-muted-foreground">尚未生成</div>
            ) : (
              <div className="grid gap-2 text-sm">
                {[
                  ...BACKTEST_STAT_ORDER.filter((k) => k in (backtestStats as Record<string, unknown>)),
                  ...Object.keys(backtestStats as Record<string, unknown>).filter(
                    (k) => !BACKTEST_STAT_ORDER.includes(k as any),
                  ),
                ].map((k) => {
                  const v = (backtestStats as any)?.[k];
                  const meta = BACKTEST_STAT_META[k] ?? { label: k, description: `字段名：${k}` };
                  return (
                    <div key={k} className="rounded-md border px-3 py-2">
                      <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between sm:gap-3">
                        <div className="min-w-0 md:col-span-1">
                          <div className="text-sm font-medium">{meta.label}</div>
                          <div className="text-xs text-muted-foreground">{meta.description}</div>
                        </div>
                        <div className="font-mono text-sm tabular-nums">{formatBacktestMetricValue(k, v)}</div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="md:col-span-1">
          <CardHeader>
            <CardTitle className="text-lg">滚动验证（Walk-forward）</CardTitle>
            <CardDescription>分窗口训练/测试，更能看出稳定性（来自 walk_forward_stats.json）。</CardDescription>
          </CardHeader>
          <CardContent className="min-w-0 md:col-span-1">
            {!walkForwardStats ? (
              <div className="text-sm text-muted-foreground">尚未生成</div>
            ) : (
              <div className="grid gap-4 min-w-0">
                {walkForwardStatus && String(walkForwardStatus) !== "success" ? (
                  <div className="rounded-md border px-3 py-2 text-sm text-muted-foreground break-words">
                    提示：{walkForwardReason ? String(walkForwardReason) : "滚动验证被跳过"}
                  </div>
                ) : null}
                {walkForwardAutoAdjusted && walkForwardEffectiveConfig ? (
                  <div className="break-words text-xs text-muted-foreground overflow-hidden">
                    已自动缩小窗口：train_bars={String((walkForwardEffectiveConfig as any)?.train_bars)} · test_bars=
                    {String((walkForwardEffectiveConfig as any)?.test_bars)} · step_bars=
                    {String((walkForwardEffectiveConfig as any)?.step_bars)}
                  </div>
                ) : null}
                {walkForwardOverall ? (
                  <div className="grid gap-2 text-xs">
                    {[
                      ...WALK_FORWARD_OVERALL_ORDER.filter((k) => k in (walkForwardOverall as Record<string, unknown>)),
                      ...Object.keys(walkForwardOverall as Record<string, unknown>).filter(
                        (k) => !WALK_FORWARD_OVERALL_ORDER.includes(k as any),
                      ),
                    ].map((k) => {
                      const v = (walkForwardOverall as any)?.[k];
                      return (
                        <div
                          key={k}
                          className="flex flex-col gap-1 rounded-md border px-3 py-2 sm:flex-row sm:items-center sm:justify-between sm:gap-3 overflow-hidden"
                        >
                          <div className="min-w-0 text-muted-foreground truncate">{WALK_FORWARD_OVERALL_LABEL[k] ?? k}</div>
                          <div className="font-mono text-xs tabular-nums truncate">{formatMetricValue(k, v)}</div>
                        </div>
                      );
                    })}
                  </div>
                ) : null}

                {walkForwardWindows.length === 0 ? (
                  <div className="text-sm text-muted-foreground">未生成窗口明细</div>
                ) : (
                  <div className="grid gap-2">
                    <div className="grid gap-2 md:hidden">
                      {walkForwardWindows.map((w) => {
                        const stats = (w as any)?.backtest_stats ?? {};
                        return (
                          <div key={String((w as any)?.window_index)} className="rounded-md border p-3 text-xs overflow-hidden">
                            <div className="flex items-start justify-between gap-3 min-w-0">
                              <div className="font-mono tabular-nums truncate">窗口 {String((w as any)?.window_index)}</div>
                              <div className="font-mono tabular-nums truncate whitespace-nowrap">{formatPercent(toNumber(stats.profit_rate), 2)}</div>
                            </div>
                            <div className="mt-1 text-muted-foreground truncate text-xs">
                              {formatDatetimeCompact((w as any)?.test_start)} → {formatDatetimeCompact((w as any)?.test_end)}
                            </div>
                            <div className="mt-2 grid grid-cols-2 gap-2">
                              <div className="rounded-md border px-2 py-1 min-w-0 overflow-hidden">
                                <div className="text-muted-foreground truncate">回撤</div>
                                <div className="font-mono tabular-nums truncate">{formatPercent(toNumber(stats.max_drawdown), 2)}</div>
                              </div>
                              <div className="rounded-md border px-2 py-1 min-w-0 overflow-hidden">
                                <div className="text-muted-foreground truncate">胜率</div>
                                <div className="font-mono tabular-nums truncate">{formatPercent(toNumber(stats.win_rate), 2)}</div>
                              </div>
                              <div className="rounded-md border px-2 py-1 min-w-0 overflow-hidden">
                                <div className="text-muted-foreground truncate">交易数</div>
                                <div className="font-mono tabular-nums truncate">
                                  {toNumber(stats.total_trades) !== null ? String(Math.round(Number(stats.total_trades))) : "-"}
                                </div>
                              </div>
                              <div className="rounded-md border px-2 py-1 min-w-0 overflow-hidden">
                                <div className="text-muted-foreground truncate">收益率</div>
                                <div className="font-mono tabular-nums truncate">{formatPercent(toNumber(stats.profit_rate), 2)}</div>
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>

                    <div className="hidden md:block min-w-0">
                      <div className="max-w-full overflow-x-auto rounded-md border">
                        <Table className="min-w-[680px] text-xs">
                          <TableHeader>
                            <TableRow>
                              <TableHead className="text-xs">窗口</TableHead>
                              <TableHead className="text-xs">测试区间</TableHead>
                              <TableHead className="text-xs">收益率</TableHead>
                              <TableHead className="text-xs">回撤</TableHead>
                              <TableHead className="text-xs">交易数</TableHead>
                              <TableHead className="text-xs">胜率</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {walkForwardWindows.map((w) => {
                              const stats = (w as any)?.backtest_stats ?? {};
                              return (
                                <TableRow key={String((w as any)?.window_index)}>
                                  <TableCell className="font-mono text-xs tabular-nums truncate">{String((w as any)?.window_index)}</TableCell>
                                  <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
                                    {formatDatetimeCompact((w as any)?.test_start)} → {formatDatetimeCompact((w as any)?.test_end)}
                                  </TableCell>
                                  <TableCell className="font-mono text-xs tabular-nums whitespace-nowrap">
                                    {formatPercent(toNumber(stats.profit_rate), 2)}
                                  </TableCell>
                                  <TableCell className="font-mono text-xs tabular-nums whitespace-nowrap">
                                    {formatPercent(toNumber(stats.max_drawdown), 2)}
                                  </TableCell>
                                  <TableCell className="font-mono text-xs tabular-nums whitespace-nowrap">
                                    {toNumber(stats.total_trades) !== null ? String(Math.round(Number(stats.total_trades))) : "-"}
                                  </TableCell>
                                  <TableCell className="font-mono text-xs tabular-nums whitespace-nowrap">
                                    {formatPercent(toNumber(stats.win_rate), 2)}
                                  </TableCell>
                                </TableRow>
                              );
                            })}
                          </TableBody>
                        </Table>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="md:col-span-2">
          <CardHeader>
            <CardTitle className="text-lg">滚动验证曲线</CardTitle>
            <CardDescription>只在每个 test 窗口上回测后的资金曲线（来自 walk_forward_equity_curve.json）。</CardDescription>
          </CardHeader>
          <CardContent>
            {walkForwardEquityPoints.length === 0 ? (
              <div className="text-sm text-muted-foreground">滚动验证曲线尚未生成</div>
            ) : (
              <div className="h-[360px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={walkForwardEquityPoints}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis
                      dataKey="datetime"
                      tickFormatter={(v) => String(v).slice(5, 16).replace("T", " ")}
                      minTickGap={32}
                    />
                    <YAxis />
                    <Tooltip labelFormatter={(v) => String(v)} formatter={(value) => [value, "balance"]} />
                    <Line type="monotone" dataKey="balance" dot={false} stroke="hsl(var(--primary))" strokeWidth={2} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="min-w-0 md:col-span-2">
          <CardHeader>
            <CardTitle className="text-lg">SHAP 图</CardTitle>
            <CardDescription>模型解释（如依赖不可用则为空）。</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="rounded-md border px-3 py-2 text-sm text-muted-foreground">
              <div className="font-medium text-foreground">怎么看 SHAP？</div>
              <ul className="mt-1 list-disc space-y-1 pl-5">
                <li>Bar 图：越靠上越重要（全局重要度）。</li>
                <li>Dot 图：每个点是一条样本；颜色通常表示特征值高/低；左右表示对模型输出的推动方向。</li>
                <li>注意：SHAP 解释的是“相关性贡献”，不等于因果；建议结合回测与滚动验证一起看。</li>
              </ul>
              {shapMeta && typeof shapMeta === "object" ? (
                <div className="mt-2 text-xs text-muted-foreground">
                  本次解释基于 {(shapMeta as any)?.sampled_rows ?? "-"} / {(shapMeta as any)?.total_rows ?? "-"} 行样本。
                </div>
              ) : null}
              {shapTopFeatures.length > 0 ? (
                <div className="mt-2">
                  <div className="text-xs text-muted-foreground">Top 特征（按重要度）：</div>
                  <ol className="mt-1 list-decimal space-y-0.5 pl-5 text-xs">
                    {shapTopFeatures.map(([name, v]) => (
                      <li key={name} className="flex items-center justify-between gap-3">
                        <span className="min-w-0 break-all">{name}</span>
                        <span className="shrink-0 font-mono tabular-nums">{formatNumber(v, 4)}</span>
                      </li>
                    ))}
                  </ol>
                </div>
              ) : null}
            </div>

            {shap?.summary_bar_artifact_id ? (
              <a
                className="block"
                href={api.artifactDownloadUrl(String(shap.summary_bar_artifact_id))}
                target="_blank"
                rel="noreferrer"
              >
                <img
                  className="mx-auto max-h-[420px] w-full max-w-2xl rounded-md border object-contain"
                  src={api.artifactDownloadUrl(String(shap.summary_bar_artifact_id))}
                  alt="shap summary bar"
                />
              </a>
            ) : (
              <div className="text-sm text-muted-foreground">尚未生成 summary bar</div>
            )}

            {shap?.summary_dot_artifact_id ? (
              <a
                className="block"
                href={api.artifactDownloadUrl(String(shap.summary_dot_artifact_id))}
                target="_blank"
                rel="noreferrer"
              >
                <img
                  className="mx-auto max-h-[420px] w-full max-w-2xl rounded-md border object-contain"
                  src={api.artifactDownloadUrl(String(shap.summary_dot_artifact_id))}
                  alt="shap summary dot"
                />
              </a>
            ) : (
              <div className="text-sm text-muted-foreground">尚未生成 summary dot</div>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Artifacts</CardTitle>
          <CardDescription>产物列表（可直接下载）。</CardDescription>
        </CardHeader>
        <CardContent>
          {!data ? (
            <div className="text-sm text-muted-foreground">加载中…</div>
          ) : data.artifacts.length === 0 ? (
            <div className="text-sm text-muted-foreground">暂无产物</div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>文件</TableHead>
                  <TableHead>kind</TableHead>
                  <TableHead>下载</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.artifacts.map((a) => (
                  <TableRow key={a.artifact_id}>
                    <TableCell className="font-mono text-xs">{String(a.uri).split("/").pop()}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{a.kind}</TableCell>
                    <TableCell>
                      <a
                        className="text-sm text-primary underline underline-offset-4"
                        href={api.artifactDownloadUrl(a.artifact_id)}
                        target="_blank"
                        rel="noreferrer"
                      >
                        下载
                      </a>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
