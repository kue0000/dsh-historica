/**
 * Business operations behind the `historica` agent tool.
 *
 * One implementation serves every action; there is no second code path. All
 * long work is delegated to the JobManager so a tool call returns promptly and
 * the model polls with `status` / `wait`.
 */
import fs from 'node:fs'
import path from 'node:path'
import { appendLog, ensureDir, readJson, readTail, runCaptured } from './runner.js'
import { describePythonCandidates, resolvePython } from './config.js'
import { JobManager } from './jobs.js'
import {
  collectArtifacts,
  findNewestRun,
  listRuns,
  readProgress,
  runDirOfPlan,
  summarizeActual,
  summarizeAlign,
  summarizeEstimate,
  summarizePlan,
} from './summarize.js'

const DEP_PROBE = 'import numpy, shapely, pyproj; print("numpy", numpy.__version__, "shapely", shapely.__version__, "pyproj", pyproj.__version__)'
const MATPLOTLIB_PROBE = 'import matplotlib; print("matplotlib", matplotlib.__version__)'

/** Actions that only read state and never spawn a download. */
const READ_ONLY_ACTIONS = new Set(['check', 'status', 'wait', 'cancel', 'list'])
/** Canonical pipeline order. */
const STEP_ORDER = ['estimate', 'fetch', 'compose', 'report']

/**
 * Whether a resolved interpreter is usable.
 * @param {string} python
 * @returns {boolean}
 */
function interpreterExists(python) {
  if (!path.isAbsolute(python)) return true
  try {
    return fs.existsSync(python)
  } catch {
    return false
  }
}

/**
 * Format an interpreter-not-found error with actionable candidates.
 * @param {ReturnType<import('./config.js').buildConfig>} cfg
 * @returns {string}
 */
function missingPythonMessage(cfg) {
  const candidates = describePythonCandidates(cfg)
    .map((item) => `  - ${item.path}${item.exists ? '' : '（不存在）'}`)
    .join('\n')
  return [
    '找不到可用的 Python 解释器（historica 需要 numpy / shapely / pyproj，出图还需要 matplotlib）。',
    '可在插件 config.pythonPath 指定解释器，或设置环境变量 HISTORICA_PYTHON。候选：',
    candidates,
    '参考创建方式：',
    '  python -m venv .venv && .venv\\Scripts\\python -m pip install numpy shapely pyproj matplotlib rasterio pystac-client pyshp',
  ].join('\n')
}

export class HistoricaService {
  /**
   * @param {ReturnType<import('./config.js').buildConfig>} cfg
   */
  constructor(cfg) {
    this.cfg = cfg
    this.jobs = new JobManager(cfg)
  }

  /**
   * Release every running child process.
   */
  dispose() {
    this.jobs.disposeAll()
  }

  /**
   * Resolve the interpreter or throw a diagnostic.
   * @returns {string}
   */
  requirePython() {
    const python = resolvePython(this.cfg)
    if (!interpreterExists(python)) throw new Error(missingPythonMessage(this.cfg))
    return python
  }

  /**
   * Resolve a path-like tool argument against the plugin directory.
   * @param {string | undefined} value
   * @returns {string | undefined}
   */
  resolveUserPath(value) {
    if (typeof value !== 'string' || value.trim() === '') return undefined
    const chosen = value.trim()
    return path.isAbsolute(chosen) ? path.normalize(chosen) : path.resolve(this.cfg.pluginDir, chosen)
  }

  /**
   * Common CLI arguments shared by init/estimate/fetch/compose/report.
   * @param {Record<string, any>} args
   * @returns {string[]}
   */
  commonArgs(args) {
    const argv = ['--out', this.cfg.outDir]
    const res = typeof args.res === 'number' && Number.isFinite(args.res) && args.res > 0
      ? args.res
      : this.cfg.resExplicit ? this.cfg.defaultRes : undefined
    if (res !== undefined) argv.push('--res', String(res))
    if (typeof args.provider === 'string' && args.provider.trim() !== '') argv.push('--provider', args.provider.trim())
    return argv
  }

  /**
   * Build the scope argument group (exactly one of admin/bbox/vector/wkt).
   * @param {Record<string, any>} args
   * @returns {string[]}
   */
  scopeArgs(args) {
    const scopes = ['admin', 'bbox', 'vector', 'wkt'].filter(
      (key) => typeof args[key] === 'string' && String(args[key]).trim() !== '',
    )
    if (scopes.length !== 1) {
      throw new Error('范围参数必须且只能给一个：admin（行政区名）、bbox（W,S,E,N）、vector（矢量文件）、wkt（WKT 面）。')
    }
    const key = scopes[0]
    if (key === 'bbox') {
      const parts = String(args.bbox).split(',').map((part) => Number(part.trim()))
      if (parts.length !== 4 || parts.some((value) => !Number.isFinite(value))) {
        throw new Error('bbox 必须是 4 个数字：W,S,E,N（例：113.88,22.44,114.18,22.64）。')
      }
      const [west, south, east, north] = parts
      if (!(west < east) || !(south < north)) throw new Error('bbox 要求 W<E 且 S<N。')
      return ['--bbox', parts.join(',')]
    }
    if (key === 'vector') {
      const vector = this.resolveUserPath(args.vector)
      if (!vector) throw new Error('--vector 需要文件路径。')
      if (!fs.existsSync(vector)) throw new Error(`矢量文件不存在：${vector}`)
      return ['--vector', vector]
    }
    return [`--${key}`, String(args[key]).trim()]
  }

  /**
   * Build the year argument group.
   * @param {Record<string, any>} args
   * @param {{ required?: boolean }} [options]
   * @returns {string[]}
   */
  yearArgs(args, options = {}) {
    const required = options.required !== false
    const argv = []
    const years = Array.isArray(args.years) ? args.years.filter((year) => Number.isInteger(year)) : []
    if (years.length > 0) {
      argv.push('--year', years.join(','))
    } else if (typeof args.range === 'string' && args.range.trim() !== '') {
      argv.push('--range', args.range.trim())
      if (Number.isInteger(args.step)) argv.push('--step', String(args.step))
    } else if (required) {
      throw new Error('需要年份：years（如 [1993,2003,2012,2024]）或 range（如 "1990-2025" + step）。')
    }
    if (Number.isInteger(args.window) && args.window >= 0) argv.push('--window', String(args.window))
    const seasonWindow = Number.isInteger(args.seasonWindow)
      ? args.seasonWindow
      : this.cfg.seasonWindowExplicit ? this.cfg.seasonWindow : undefined
    if (seasonWindow !== undefined && seasonWindow >= 0) argv.push('--season-window', String(seasonWindow))
    if (Array.isArray(args.preferDates)) {
      for (const date of args.preferDates) {
        if (typeof date === 'string' && date.trim() !== '') argv.push('--prefer-date', date.trim())
      }
    }
    return argv
  }

  /**
   * Build a full `estimate` argv.
   * @param {Record<string, any>} args
   * @returns {string[]}
   */
  estimateArgv(args) {
    const argv = []
    const hasCase = typeof args.case === 'string' && args.case.trim() !== ''
    if (hasCase) argv.push('--case', args.case.trim())
    const hasScope = ['admin', 'bbox', 'vector', 'wkt'].some(
      (key) => typeof args[key] === 'string' && String(args[key]).trim() !== '',
    )
    if (!hasScope && !hasCase) {
      throw new Error('需要范围：admin / bbox / vector / wkt（或给出 case，用内置案例默认值）。')
    }
    const hasYears = (Array.isArray(args.years) && args.years.some((year) => Number.isInteger(year)))
      || (typeof args.range === 'string' && args.range.trim() !== '')
    if (!hasYears && !hasCase) {
      throw new Error('需要年份：years 或 range（或给出 case，用内置案例默认值）。')
    }
    argv.push('estimate')
    if (hasScope) argv.push(...this.scopeArgs(args))
    argv.push(...this.yearArgs(args, { required: !hasCase }), ...this.commonArgs(args))
    if (args.noProbe === true) argv.push('--no-probe')
    if (typeof args.speed === 'number' && Number.isFinite(args.speed) && args.speed > 0) argv.push('--speed', String(args.speed))
    return [this.cfg.entry, ...argv]
  }

  /**
   * Build a `fetch` argv.
   * @param {string} planPath
   * @param {Record<string, any>} args
   * @returns {string[]}
   */
  fetchArgv(planPath, args) {
    const argv = [this.cfg.entry, 'fetch', '--plan', planPath, ...this.commonArgs(args)]
    if (Array.isArray(args.onlyYears) && args.onlyYears.length > 0) {
      argv.push('--only-year', args.onlyYears.filter((year) => Number.isInteger(year)).join(','))
    }
    return argv
  }

  /**
   * Start one background job.
   * @param {{
   *   label: string,
   *   steps: Array<{ kind: string, argv?: string[] | null, discover?: string }>,
   *   runDir?: string | null,
   *   planPath?: string | null,
   *   buildArgv?: (kind: string, job: any) => string[],
   * }} spec
   * @returns {object}
   */
  startJob(spec) {
    const python = this.requirePython()
    const view = this.jobs.start({
      label: spec.label,
      steps: spec.steps.map((step) => ({ kind: step.kind, argv: step.argv ?? null, discover: step.discover })),
      python,
      cwd: this.cfg.cwd,
      env: {},
      runDir: spec.runDir ?? null,
      planPath: spec.planPath ?? null,
      buildArgv: spec.buildArgv,
    })
    return {
      ok: true,
      action: spec.label,
      jobId: view.id,
      state: view.state,
      runDir: view.runDir,
      message: `已启动后台任务 ${view.id}（${view.steps.map((step) => step.kind).join(' → ')}）。用 action:"wait" 或 "status" 查看进度。`,
      steps: view.steps,
      jobDir: view.dir,
    }
  }

  /**
   * Enrich a job view with plan/estimate/progress/artifacts/log tail.
   * @param {object} view
   * @returns {object}
   */
  enrich(view) {
    const enriched = { ...view }
    const runDir = view.runDir ?? null
    if (runDir) {
      const plan = readJson(path.join(runDir, 'plan.json'))
      const estimate = readJson(path.join(runDir, 'estimate.json'))
      enriched.plan = summarizePlan(plan)
      enriched.estimate = summarizeEstimate(estimate)
      const progress = readProgress(runDir)
      if (progress) enriched.progress = progress
      const actual = summarizeActual(runDir)
      if (actual) enriched.actual = actual
      const align = summarizeAlign(runDir)
      if (align) enriched.align = align
      const artifacts = collectArtifacts(runDir)
      if (artifacts.files.length > 0 || artifacts.epochCount > 0) {
        enriched.artifacts = artifacts.files
        enriched.epochCount = artifacts.epochCount
      }
    }
    enriched.logTail = readTail(view.logPath, {
      maxLines: this.cfg.logTailLines,
      maxChars: this.cfg.logTailChars,
    })
    enriched.message = this.describeState(enriched)
    return enriched
  }

  /**
   * Short Chinese status sentence.
   * @param {any} view
   * @returns {string}
   */
  describeState(view) {
    const current = view.steps?.find((step) => step.state === 'running')
    if (view.state === 'running') {
      return current ? `正在运行 ${current.kind}（已 ${current.elapsedSec} 秒）。` : '任务已排队。'
    }
    if (view.state === 'done') {
      const years = view.progress?.completedYears?.length ? `，已完成 ${view.progress.completedYears.join('、')}` : ''
      return `任务完成${years}。`
    }
    if (view.state === 'paused') {
      return `${view.error ?? '看门狗暂停。'} 可用同一 runDir 继续：action:"download" + runDir。`
    }
    if (view.state === 'failed') return `任务失败：${view.error ?? '未知错误'}`
    if (view.state === 'cancelled') return '任务已取消；已下载的缓存保留。'
    if (view.state === 'interrupted') return view.note ?? '任务进程已不在；缓存会保留，可用同一 runDir 续跑。'
    return `任务状态：${view.state}`
  }

  /**
   * `check`: report interpreter, dependencies and CLI readiness.
   * @returns {object}
   */
  check() {
    const python = resolvePython(this.cfg)
    const candidates = describePythonCandidates(this.cfg)
    const base = {
      ok: false,
      action: 'check',
      python,
      historicaRoot: this.cfg.historicaRoot,
      entry: this.cfg.entry,
      outDir: this.cfg.outDir,
      candidates,
      version: null,
      deps: null,
      matplotlib: null,
      writable: false,
    }
    if (!fs.existsSync(this.cfg.entry)) {
      return { ...base, message: `historica 入口不存在：${this.cfg.entry}` }
    }
    ensureDir(this.cfg.outDir)
    const probeFile = path.join(this.cfg.outDir, '.write-test')
    try {
      fs.writeFileSync(probeFile, 'ok', 'utf8')
      fs.unlinkSync(probeFile)
      base.writable = true
    } catch (error) {
      base.writable = false
      base.writeError = String(error?.message ?? error)
    }
    const version = runCaptured({ python, argv: [this.cfg.entry, '--version'], cwd: this.cfg.cwd, timeoutMs: 30000 })
    base.version = (version.stdout || version.stderr || '').trim() || null
    const deps = runCaptured({ python, argv: ['-c', DEP_PROBE], cwd: this.cfg.cwd, timeoutMs: 120000 })
    base.deps = deps.code === 0 ? (deps.stdout || '').trim() : {
      error: (deps.stderr || deps.stdout || '').trim() || 'numpy/shapely/pyproj 不可用',
    }
    const matplotlib = runCaptured({ python, argv: ['-c', MATPLOTLIB_PROBE], cwd: this.cfg.cwd, timeoutMs: 120000 })
    base.matplotlib = matplotlib.code === 0 ? (matplotlib.stdout || '').trim() : false
    base.ok = version.code === 0 && deps.code === 0 && base.writable
    base.message = base.ok
      ? `环境就绪：${base.version}；${base.matplotlib ? base.matplotlib : '缺少 matplotlib，compose 出图会失败'}。输出目录 ${this.cfg.outDir}`
      : '环境未就绪，请按 message 中的候选路径配置 config.pythonPath。'
    return base
  }

  /**
   * `plan`: background estimate (selection + speed probe + cost, no download).
   * @param {Record<string, any>} args
   * @returns {object}
   */
  plan(args) {
    const argv = this.estimateArgv(args)
    return this.startJob({
      label: 'plan',
      steps: [{ kind: 'estimate', argv, discover: 'estimate' }],
    })
  }

  /**
   * `download`: estimate (optional) -> fetch -> compose -> report.
   * @param {Record<string, any>} args
   * @returns {object}
   */
  download(args) {
    let runDir = this.resolveUserPath(args.runDir)
    const planPath = this.resolveUserPath(args.planPath)
    if (planPath) {
      if (!fs.existsSync(planPath)) throw new Error(`plan 文件不存在：${planPath}`)
      runDir = runDirOfPlan(planPath)
    }
    if (runDir && !fs.existsSync(path.join(runDir, 'plan.json'))) {
      throw new Error(`run 目录下没有 plan.json：${runDir}`)
    }

    let requested = Array.isArray(args.steps) && args.steps.length > 0 ? args.steps : null
    if (requested) {
      const unknown = requested.filter((step) => !STEP_ORDER.includes(step))
      if (unknown.length > 0) throw new Error(`未知步骤：${unknown.join(', ')}；可用：${STEP_ORDER.join(', ')}`)
      requested = STEP_ORDER.filter((step) => requested.includes(step))
    }
    if (runDir && requested?.includes('estimate')) {
      throw new Error('已有 runDir 时不能再 estimate；如需重新规划，省略 runDir/planPath。')
    }
    if (runDir && requested && requested.length === 0) throw new Error('steps 为空。')

    const specs = []
    if (!runDir) {
      if (!requested || requested.includes('estimate')) {
        specs.push({ kind: 'estimate', argv: null, discover: 'estimate' })
      } else {
        throw new Error('没有现成 run 时，steps 必须包含 estimate，或先调用 action:"plan" 再用返回的 runDir。')
      }
    }
    for (const kind of STEP_ORDER.slice(1)) {
      if (!requested || requested.includes(kind)) specs.push({ kind, argv: null })
    }
    if (specs.length === 0) throw new Error('没有要运行的步骤；请检查 steps 参数。')

    const buildArgv = (kind, job) => {
      const activeRun = job.runDir ?? runDir
      const activePlan = job.planPath ?? (activeRun ? path.join(activeRun, 'plan.json') : null)
      if (kind === 'estimate') return this.estimateArgv(args)
      if (kind === 'fetch') {
        if (!activePlan) throw new Error('还没有 plan.json，无法 fetch。')
        return this.fetchArgv(activePlan, args)
      }
      if (!activeRun) throw new Error(`还没有 run 目录，无法 ${kind}。`)
      return [this.cfg.entry, kind, '--run', activeRun, ...this.commonArgs(args)]
    }

    const view = this.startJob({
      label: runDir ? 'download(resume)' : 'download',
      steps: specs,
      runDir: runDir ?? null,
      planPath: runDir ? path.join(runDir, 'plan.json') : null,
      buildArgv,
    })
    return view
  }

  /**
   * `render`: compose + report for an existing run.
   * @param {Record<string, any>} args
   * @returns {object}
   */
  render(args) {
    const runDir = this.requireRunDir(args)
    const steps = [{
      kind: 'compose',
      argv: [this.cfg.entry, 'compose', '--run', runDir, ...this.commonArgs(args)],
    }, {
      kind: 'report',
      argv: [this.cfg.entry, 'report', '--run', runDir, ...this.commonArgs(args)],
    }]
    const view = this.startJob({
      label: 'render',
      steps,
      runDir,
      planPath: path.join(runDir, 'plan.json'),
    })
    return view
  }

  /**
   * Resolve a run directory argument and verify it looks like a run.
   * @param {Record<string, any>} args
   * @returns {string}
   */
  requireRunDir(args) {
    let runDir = this.resolveUserPath(args.runDir)
    const planPath = this.resolveUserPath(args.planPath)
    if (!runDir && planPath) runDir = runDirOfPlan(planPath)
    if (!runDir) throw new Error('需要 runDir 或 planPath。')
    if (!fs.existsSync(path.join(runDir, 'plan.json'))) throw new Error(`run 目录下没有 plan.json：${runDir}`)
    return runDir
  }

  /**
   * `status`: read one job.
   * @param {Record<string, any>} args
   * @returns {object}
   */
  status(args) {
    const jobId = this.requireJobId(args)
    const view = this.jobs.status(jobId)
    if (!view) return { ok: false, action: 'status', jobId, message: `没有这个任务：${jobId}` }
    return { ok: true, action: 'status', ...this.enrich(view) }
  }

  /**
   * `wait`: block until the job settles or the timeout expires.
   * @param {Record<string, any>} args
   * @param {AbortSignal} [signal]
   * @returns {Promise<object>}
   */
  async wait(args, signal) {
    const jobId = this.requireJobId(args)
    const requested = Number.isFinite(args.timeoutSec) ? Math.max(1, Math.floor(args.timeoutSec)) : this.cfg.waitDefaultSec
    const timeoutSec = Math.min(requested, this.cfg.waitMaxSec)
    const view = await this.jobs.wait(jobId, timeoutSec * 1000, signal)
    if (!view) return { ok: false, action: 'wait', jobId, message: `没有这个任务：${jobId}` }
    const enriched = this.enrich(view)
    if (enriched.state === 'running') {
      enriched.message = `仍在运行（已等待 ${timeoutSec} 秒）。可再次 action:"wait"，或稍后用 action:"status"。`
    }
    return { ok: true, action: 'wait', ...enriched }
  }

  /**
   * `cancel`: stop one running job.
   * @param {Record<string, any>} args
   * @returns {object}
   */
  cancel(args) {
    const jobId = this.requireJobId(args)
    const result = this.jobs.cancel(jobId)
    return { ok: result.ok, action: 'cancel', jobId, message: result.message }
  }

  /**
   * `list`: recent runs plus live jobs.
   * @param {Record<string, any>} args
   * @returns {object}
   */
  list(args) {
    const limit = Number.isInteger(args.limit) && args.limit > 0 ? Math.min(args.limit, 50) : 10
    const runs = listRuns(this.cfg.outDir, limit)
    const jobs = [...this.jobs.live.values()].map((job) => this.enrich(this.jobs.view(job)))
    return {
      ok: true,
      action: 'list',
      outDir: this.cfg.outDir,
      runs,
      jobs,
      message: `共 ${runs.length} 个最近运行，${jobs.length} 个本进程任务。`,
    }
  }

  /**
   * `selftest`: offline estimate against the bundled Yanping plan fixture.
   * @returns {object}
   */
  selftest() {
    const python = this.requirePython()
    const fixture = path.join(this.cfg.historicaRoot, 'tests', 'data', 'plan_yanping.json')
    if (!fs.existsSync(fixture)) return { ok: false, action: 'selftest', message: `缺少离线测试夹具：${fixture}` }
    const outDir = path.join(this.cfg.outDir, '_selftest')
    ensureDir(outDir)
    const logPath = path.join(outDir, 'selftest.log')
    const argv = [
      this.cfg.entry, 'estimate', '--plan', fixture, '--no-probe', '--speed', '0.2', '--out', outDir,
    ]
    fs.writeFileSync(logPath, '')
    appendLog(logPath, `\n$ ${python} ${argv.join(' ')}\n`)
    const result = runCaptured({ python, argv, cwd: this.cfg.cwd, timeoutMs: this.cfg.selfTestTimeoutSec * 1000 })
    appendLog(logPath, `${result.stdout}${result.stderr}`)
    const runDir = findNewestRun(outDir, [])
    const plan = runDir ? readJson(path.join(runDir, 'plan.json')) : undefined
    const estimate = runDir ? readJson(path.join(runDir, 'estimate.json')) : undefined
    const ok = result.code === 0
    return {
      ok,
      action: 'selftest',
      runDir: runDir ?? null,
      plan: summarizePlan(plan),
      estimate: summarizeEstimate(estimate),
      logTail: readTail(logPath, { maxLines: 40, maxChars: 3000 }),
      message: ok
        ? `离线自检通过（estimate 退出 0，未下载影像）。run: ${runDir}`
        : `离线自检失败（退出码 ${result.code}${result.timedOut ? '，超时' : ''}）。见日志 ${logPath}`,
    }
  }

  /**
   * Validate and return the jobId argument.
   * @param {Record<string, any>} args
   * @returns {string}
   */
  requireJobId(args) {
    if (typeof args.jobId !== 'string' || args.jobId.trim() === '') {
      throw new Error('需要 jobId（由 action:"plan"/"download"/"render" 返回）。')
    }
    return args.jobId.trim()
  }
}

export { READ_ONLY_ACTIONS }
