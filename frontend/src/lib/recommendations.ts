export function parseIntervalToMinutes(interval: string): number | null {
  const text = String(interval || "").trim();
  const match = text.match(/^(\d+)?(m|h|d)$/i);
  if (!match) return null;

  const n = Number(match[1] ?? "1");
  if (!Number.isFinite(n) || n <= 0) return null;

  const unit = String(match[2] ?? "").toLowerCase();
  if (unit === "m") return n;
  if (unit === "h") return n * 60;
  if (unit === "d") return n * 24 * 60;
  return null;
}

function parseDateYmdUtc(dateStr: string): number | null {
  const m = String(dateStr || "").trim().match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return null;
  const year = Number(m[1]);
  const month = Number(m[2]);
  const day = Number(m[3]);
  if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) return null;
  if (month < 1 || month > 12) return null;
  if (day < 1 || day > 31) return null;
  return Date.UTC(year, month - 1, day);
}

export function estimateTotalBars(params: { startDate: string; endDate: string; interval: string }): number | null {
  const { startDate, endDate, interval } = params;
  const start = parseDateYmdUtc(startDate);
  const end = parseDateYmdUtc(endDate);
  const minutesPerBar = parseIntervalToMinutes(interval);
  if (start === null || end === null || minutesPerBar === null) return null;
  const diffMinutes = Math.max(0, Math.floor((end - start) / 60000));
  return Math.max(0, Math.floor(diffMinutes / minutesPerBar));
}

export function formatBarsToApproxDuration(bars: number, interval: string): string {
  const minutesPerBar = parseIntervalToMinutes(interval);
  if (minutesPerBar === null) return `${Math.round(bars)} bars`;

  const totalMinutes = Math.max(0, bars) * minutesPerBar;
  const days = totalMinutes / (24 * 60);
  if (days >= 2) return `≈ ${Math.round(days)} 天`;

  const hours = totalMinutes / 60;
  if (hours >= 2) return `≈ ${Math.round(hours)} 小时`;

  return `≈ ${Math.round(totalMinutes)} 分钟`;
}

export function recommendLabelThreshold(params: { filterType: string; labelType: string }): number | null {
  const { filterType, labelType } = params;
  const f = String(filterType || "").toLowerCase();
  const t = String(labelType || "").toLowerCase();
  if (f === "rsi") return t === "down" ? 70 : 30;
  if (f === "cti") return t === "down" ? 0.5 : -0.5;
  return null;
}

export function calcExpectedWindows(params: {
  totalBars: number;
  trainBars: number;
  testBars: number;
  stepBars: number;
}): number | null {
  const { totalBars, trainBars, testBars, stepBars } = params;
  const n = Math.max(0, Math.floor(totalBars));
  const train = Math.max(0, Math.floor(trainBars));
  const test = Math.max(0, Math.floor(testBars));
  const step = Math.max(0, Math.floor(stepBars));
  if (train <= 0 || test <= 0 || step <= 0) return null;
  if (n < train + test) return 0;
  return Math.floor((n - train - test) / step) + 1;
}

export function recommendWalkForward(params: { totalBars: number }): {
  train_bars: number;
  test_bars: number;
  step_bars: number;
  max_windows: number;
  expected_windows: number | null;
} {
  const { totalBars } = params;
  const n = Math.max(0, Math.floor(totalBars));

  const trainRatio = 4; // train:test ≈ 4:1（更稳）
  const targetWindows = 6; // 目标：大约 6 个窗口（可读性更好）
  const maxWindows = 10;
  const minTest = 200;

  let test = Math.floor(n / (trainRatio + targetWindows));
  if (!Number.isFinite(test) || test <= 0) test = minTest;

  const roundTo = test >= 2000 ? 100 : test >= 500 ? 50 : 10;
  test = Math.max(minTest, Math.floor(test / roundTo) * roundTo);

  let train = trainRatio * test;
  train = Math.max(200, train);

  // 确保至少有 2 个窗口的机会（否则用户会很困惑）
  if (n > 0 && n < train + 2 * test) {
    test = Math.max(minTest, Math.floor(n / (trainRatio + 2)));
    const roundTo2 = test >= 2000 ? 100 : test >= 500 ? 50 : 10;
    test = Math.max(minTest, Math.floor(test / roundTo2) * roundTo2);
    train = Math.max(200, trainRatio * test);
  }

  const step = test;
  const expected = calcExpectedWindows({ totalBars: n, trainBars: train, testBars: test, stepBars: step });

  return {
    train_bars: train,
    test_bars: test,
    step_bars: step,
    max_windows: maxWindows,
    expected_windows: expected,
  };
}
