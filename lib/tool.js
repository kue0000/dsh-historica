/**
 * The model-facing `historica` tool definition.
 *
 * The parameter object is a raw JSON Schema (the registry projects it as-is).
 * Long actions return a job id; `wait` / `status` report progress.
 */

/** Actions accepted by the tool. */
export const ACTIONS = ['check', 'plan', 'download', 'render', 'status', 'wait', 'cancel', 'list', 'selftest']

const PARAMETERS = {
  type: 'object',
  additionalProperties: false,
  properties: {
    action: {
      type: 'string',
      enum: ACTIONS,
      description: [
        'check=自检 Python/依赖/目录；',
        'plan=后台选景+测速+算账（不下载，返回 jobId）；',
        'download=后台下载并出图（无 runDir 时自动先 estimate；可续跑）；',
        'render=只对已有 run 重跑 compose+report；',
        'status=查任务；wait=阻塞等待任务；cancel=取消任务；list=列最近运行；selftest=离线自检。',
      ].join(''),
    },
    admin: { type: 'string', description: '行政区名，如 "南平市延平区" 或 "广东省深圳市"。与 bbox/vector/wkt 四选一。' },
    bbox: { type: 'string', description: '经纬度范围 W,S,E,N（度），如 "113.88,22.44,114.18,22.64"。' },
    vector: { type: 'string', description: '矢量文件路径（GeoJSON / .shp / 内含 shp 的 .zip / KML）。' },
    wkt: { type: 'string', description: 'WKT 面字符串，如 POLYGON((...))。' },
    years: {
      type: 'array',
      items: { type: 'integer' },
      description: '目标年份，如 [1993,2003,2012,2024]。与 range 二选一。',
    },
    range: { type: 'string', description: '年份区间，如 "1990-2025"，配合 step 使用。' },
    step: { type: 'integer', description: '区间步长（默认 5）。' },
    window: { type: 'integer', description: '目标年 ±N 内择优（默认 1）。' },
    seasonWindow: {
      type: 'integer',
      description: '同季窗口 ±N 天（默认 45；0=关闭季节锁，允许跨季）。',
    },
    preferDates: {
      type: 'array',
      items: { type: 'string' },
      description: '强制某些年份的日期，如 ["1993-12-26"]。',
    },
    res: { type: 'number', description: '输出分辨率（米，默认 30）。' },
    provider: { type: 'string', enum: ['landsat-c2-l2', 'sentinel-2-l2a'], description: '数据源，默认 landsat-c2-l2。' },
    case: { type: 'string', description: '内置案例名（yanping / prd_core / prd_full / guizhou_huaxi），其默认范围与年份可省略。' },
    noProbe: { type: 'boolean', description: 'plan 时跳过带宽探针（更快，但预估更粗）。' },
    speed: { type: 'number', description: 'plan 时手动指定吞吐 MB/s，跳过探针。' },
    runDir: { type: 'string', description: '已有 run 目录（含 plan.json）；download 用它续跑，render 用它重出图。' },
    planPath: { type: 'string', description: '已有 plan.json 的路径（等价于给出 runDir）。' },
    steps: {
      type: 'array',
      items: { type: 'string', enum: ['estimate', 'fetch', 'compose', 'report'] },
      description: 'download 时只跑指定步骤（默认全部）。续跑通常只给 ["fetch","compose","report"]。',
    },
    onlyYears: {
      type: 'array',
      items: { type: 'integer' },
      description: '只重建指定年份（其余期命中缓存）。',
    },
    jobId: { type: 'string', description: 'status/wait/cancel 需要的任务 id。' },
    timeoutSec: { type: 'integer', description: 'wait 的等待秒数（默认 120，最大 600）。' },
    limit: { type: 'integer', description: 'list 返回的运行条数（默认 10）。' },
  },
  required: ['action'],
}

/**
 * Format seconds for humans.
 * @param {number | null | undefined} seconds
 * @returns {string}
 */
function duration(seconds) {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return '未知'
  if (seconds < 90) return `${Math.round(seconds)} 秒`
  const minutes = seconds / 60
  if (minutes < 90) return `${minutes.toFixed(1)} 分钟`
  return `${(minutes / 60).toFixed(1)} 小时`
}

/**
 * Render the plan summary fragment.
 * @param {any} plan
 * @returns {string[]}
 */
function planLines(plan) {
  const lines = []
  lines.push(`范围: ${plan.range}${plan.source ? `（边界来源 ${plan.source}）` : ''}`)
  if (plan.areaKm2 !== null || plan.grid) {
    lines.push(`格网: ${plan.crs ?? '-'} ${plan.grid ?? '-'} @ ${plan.res ?? '-'} m；面积 ${plan.areaKm2 ?? '-'} km²`)
  }
  if (plan.season) {
    const season = plan.season
    const cross = season.crossSeasonEpochs?.length ? `；跨季期：${season.crossSeasonEpochs.join('、')}` : ''
    lines.push(
      `季节: ${season.enabled ? `同季 ±${season.windowDays} 天，中心 ${season.centerLabel}` : '季节锁关闭'}；` +
        `日序标准差 ${season.postlockStdDays ?? '-'} 天，最大差 ${season.postlockMaxDiffDays ?? '-'} 天${cross}`,
    )
  }
  if (Array.isArray(plan.epochs) && plan.epochs.length > 0) {
    lines.push('各期:')
    for (const epoch of plan.epochs) {
      const date = epoch.date ?? '（无可用景）'
      const coverage = epoch.coveragePct === null ? '-' : `${epoch.coveragePct}%`
      const cloud = epoch.cloudPct === null ? '-' : `${epoch.cloudPct}%`
      lines.push(`  - ${epoch.targetYear} → ${date}，覆盖 ${coverage}，云 ${cloud}，${epoch.nScenes} 景${epoch.crossSeason ? '（跨季）' : ''}`)
    }
  }
  return lines
}

/**
 * Render the estimate summary fragment.
 * @param {any} estimate
 * @returns {string[]}
 */
function estimateLines(estimate) {
  const lines = []
  lines.push(
    `预估: ${estimate.scenes ?? '-'} 景，原始 ${estimate.payloadMb ?? '-'} MB，传输 ${estimate.transferMb ?? '-'} MB；` +
      `点估计 ${duration(estimate.pointS)}，区间 ${duration(estimate.loS)}–${duration(estimate.hiS)}` +
      (estimate.speedMbps ? `（${estimate.speedMbps} MB/s）` : ''),
  )
  if (estimate.speedBasis) lines.push(`测速依据: ${estimate.speedBasis}`)
  if (estimate.advisory) lines.push(`提示: ${estimate.advisory}`)
  return lines
}

/**
 * Render one job/run value into model content.
 * @param {Record<string, any>} args
 * @param {any} value
 * @returns {Array<{ type: 'text', text: string }>}
 */
export function renderValue(args, value) {
  const lines = []
  if (!value || typeof value !== 'object') {
    return [{ type: 'text', text: `[historica] ${args?.action ?? '调用'}：没有返回结果。` }]
  }
  const state = value.state ? ` · ${value.state}` : ''
  const failed = value.ok === false ? ' · 失败' : ''
  lines.push(`[historica] ${value.action ?? args?.action ?? '调用'}${state}${failed}`)
  if (value.message) lines.push(value.message)

  if (value.python) lines.push(`Python: ${value.python}`)
  if (value.version) lines.push(`historica: ${value.version}`)
  if (value.deps) {
    lines.push(`依赖: ${typeof value.deps === 'string' ? value.deps : value.deps.error ?? '不可用'}`)
  }
  if (value.matplotlib) {
    lines.push(`出图依赖: ${typeof value.matplotlib === 'string' ? value.matplotlib : '缺少 matplotlib（compose 会失败）'}`)
  }
  if (value.historicaRoot) lines.push(`historica 根目录: ${value.historicaRoot}`)
  if (value.outDir) lines.push(`输出目录: ${value.outDir}`)

  if (value.jobId) lines.push(`任务: ${value.jobId}`)
  if (Array.isArray(value.steps) && value.action !== 'list') {
    lines.push(
      `步骤: ${value.steps
        .map((step) => `${step.kind}(${step.state}${step.exitCode !== null ? `, exit ${step.exitCode}` : ''}${step.elapsedSec !== null ? `, ${step.elapsedSec}s` : ''})`)
        .join(' → ')}`,
    )
  }
  if (value.runDir) lines.push(`run 目录: ${value.runDir}`)
  if (value.plan) lines.push(...planLines(value.plan))
  if (value.estimate) lines.push(...estimateLines(value.estimate))
  if (value.progress) {
    const completed = value.progress.completedYears?.length ? value.progress.completedYears.join('、') : '无'
    lines.push(`下载进度: 已完成 ${completed}${value.progress.paused ? `；暂停：${value.progress.reason ?? '看门狗'}` : ''}`)
  }
  if (value.align) {
    lines.push(`配准: ${value.align.pairs ?? '-'} 个窗口，锁定 ${value.align.locked ?? '-'}，未锁定 ${value.align.unlocked ?? '-'}，最小置信比 ${value.align.minConf ?? '-'}`)
  }
  if (Array.isArray(value.artifacts) && value.artifacts.length > 0) {
    lines.push('产物:')
    for (const artifact of value.artifacts.slice(0, 12)) lines.push(`  - ${artifact}`)
    if (typeof value.epochCount === 'number' && value.epochCount > 0) {
      lines.push(`  - data/epoch_*.npz × ${value.epochCount}`)
    }
    lines.push('（用 read_image 查看 series.jpg / single_<年>.jpg，用 present 交付产物。）')
  }
  if (Array.isArray(value.runs)) {
    if (value.runs.length === 0) lines.push('最近没有运行记录。')
    for (const run of value.runs) {
      const epochs = Array.isArray(run.epochs)
        ? run.epochs.map((epoch) => `${epoch.targetYear}:${epoch.date ?? '无景'}`).join('，')
        : ''
      lines.push(
        `- ${run.runDir}\n  范围 ${run.range ?? '-'}；${epochs || '无期次'}；` +
          `${run.hasSeries ? '已出图' : '未出图'}${run.report ? `；报告 ${run.report}` : ''}`,
      )
    }
  }
  if (Array.isArray(value.jobs) && value.jobs.length > 0) {
    lines.push('本进程任务:')
    for (const job of value.jobs) lines.push(`- ${job.id} ${job.state} ${job.label ?? ''}`)
  }
  if (value.logTail) lines.push(`日志尾部:\n${value.logTail}`)
  const text = lines.join('\n')
  return [{ type: 'text', text: text.length > 16000 ? `${text.slice(0, 16000)}\n…（截断）` : text }]
}

/**
 * Build the registry-ready tool definition.
 * @param {import('./runtime.js').HistoricaService} service
 * @returns {object}
 */
export function createToolDefinition(service) {
  return {
    name: 'historica',
    description:
      '历史卫星影像（Landsat Collection 2 Level 2，1984 年后；可选 Sentinel-2）规划、下载与出图。' +
      '给定行政区名 / bbox / 矢量 / WKT 与目标年份，自动选景、按实测带宽预估流量与耗时（plan，不下载），' +
      '再窗口化下载并拼成多期对比图（series.jpg、single_<年>.jpg）与技术报告（technical_report.md）。' +
      '动作：check 自检；plan 后台规划；download 后台下载并出图（可续跑）；render 重出图；wait/status 看进度；' +
      'cancel 取消；list 列最近运行；selftest 离线自检。长任务返回 jobId，用 wait（每次≤600 秒）或 status 轮询。',
    parameters: PARAMETERS,
    output: {
      schema: { type: 'object', additionalProperties: true },
      render: renderValue,
    },
    async execute(args, exec) {
      switch (args.action) {
        case 'check':
          return service.check()
        case 'selftest':
          return service.selftest()
        case 'plan':
          return service.plan(args)
        case 'download':
          return service.download(args)
        case 'render':
          return service.render(args)
        case 'status':
          return service.status(args)
        case 'wait':
          return service.wait(args, exec?.signal)
        case 'cancel':
          return service.cancel(args)
        case 'list':
          return service.list(args)
        default:
          throw new Error(`未知 action：${String(args.action)}；可用：${ACTIONS.join(', ')}`)
      }
    },
  }
}
