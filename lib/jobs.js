/**
 * Background job manager for long historica pipelines.
 *
 * One job runs an ordered list of CLI steps (estimate -> fetch -> compose ->
 * report). `estimate` is special: its run directory is created by the CLI, so
 * the manager snapshots existing runs before the step and records the new one
 * afterwards. Every transition is persisted to `job.json` so `status` keeps
 * working after a plugin reload.
 */
import { randomBytes } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { appendLog, ensureDir, spawnLogged } from './runner.js'
import { findNewestRun, listRunDirs } from './summarize.js'

/**
 * Build a short unique job id.
 * @returns {string}
 */
function makeJobId() {
  const stamp = new Date().toISOString().replace(/[-:TZ.]/g, '').slice(0, 14)
  return `${stamp}-${randomBytes(3).toString('hex')}`
}

/**
 * Omit internal fields before serializing a job.
 * @param {any} job
 * @returns {object}
 */
function serializeJob(job) {
  return {
    id: job.id,
    label: job.label,
    state: job.state,
    createdAt: job.createdAt,
    updatedAt: job.updatedAt,
    runDir: job.runDir ?? null,
    planPath: job.planPath ?? null,
    error: job.error ?? null,
    pid: job.pid ?? null,
    steps: job.steps.map((step) => ({
      kind: step.kind,
      argv: step.argv,
      state: step.state,
      exitCode: step.exitCode ?? null,
      startedAt: step.startedAt ?? null,
      endedAt: step.endedAt ?? null,
      discover: step.discover ?? null,
    })),
  }
}

/**
 * Whether a recorded pid still belongs to a live process.
 * @param {number} pid
 * @returns {boolean}
 */
function pidAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false
  try {
    process.kill(pid, 0)
    return true
  } catch {
    return false
  }
}

export class JobManager {
  /**
   * @param {ReturnType<import('./config.js').buildConfig>} cfg
   */
  constructor(cfg) {
    this.cfg = cfg
    /** @type {Map<string, any>} */
    this.live = new Map()
  }

  /**
   * Start a job and return its view immediately.
   * @param {{
   *   label: string,
   *   steps: Array<{ kind: string, argv: string[] | null, discover?: string }>,
   *   python: string,
   *   cwd: string,
   *   env?: Record<string, string>,
   *   runDir?: string | null,
   *   planPath?: string | null,
   *   buildArgv?: (kind: string, job: any) => string[],
   * }} spec
   * @returns {object} job view
   */
  start(spec) {
    ensureDir(this.cfg.jobsDir)
    const id = makeJobId()
    const dir = path.join(this.cfg.jobsDir, id)
    ensureDir(dir)
    const job = {
      id,
      label: spec.label,
      dir,
      logPath: path.join(dir, 'job.log'),
      python: spec.python,
      cwd: spec.cwd,
      env: spec.env,
      state: 'running',
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      steps: spec.steps.map((step) => ({
        kind: step.kind,
        argv: step.argv,
        discover: step.discover,
        state: 'pending',
        exitCode: null,
        startedAt: null,
        endedAt: null,
      })),
      runDir: spec.runDir ?? null,
      planPath: spec.planPath ?? null,
      buildArgv: spec.buildArgv,
      error: null,
      child: null,
      pid: null,
      estimateBaseline: [],
      waiters: new Set(),
      cancelled: false,
    }
    this.live.set(id, job)
    this.persist(job)
    // Start after the caller has the job id.
    setImmediate(() => this.advance(job))
    return this.view(job)
  }

  /**
   * Advance to the next pending step or finalize the job.
   * @param {any} job
   */
  advance(job) {
    if (job.state !== 'running') return
    const step = job.steps.find((candidate) => candidate.state === 'pending')
    if (!step) {
      job.state = 'done'
      this.finalize(job)
      return
    }
    if (job.cancelled) {
      job.state = 'cancelled'
      this.finalize(job)
      return
    }
    if (step.discover === 'estimate') {
      job.estimateBaseline = listRunDirs(this.cfg.outDir, 'estimate')
    }
    if (!Array.isArray(step.argv)) {
      if (typeof job.buildArgv !== 'function') {
        step.state = 'failed'
        job.state = 'failed'
        job.error = `内部错误：步骤 ${step.kind} 缺少参数。`
        this.finalize(job)
        return
      }
      try {
        step.argv = job.buildArgv(step.kind, job)
      } catch (error) {
        step.state = 'failed'
        job.state = 'failed'
        job.error = `无法构造步骤 ${step.kind} 的参数：${String(error?.message ?? error)}`
        this.finalize(job)
        return
      }
    }
    step.state = 'running'
    step.startedAt = new Date().toISOString()
    this.persist(job)
    appendLog(job.logPath, `\n$ ${job.python} ${step.argv.join(' ')}\n`)
    let spawned
    try {
      spawned = spawnLogged({
        python: job.python,
        argv: step.argv,
        cwd: job.cwd,
        logPath: job.logPath,
        env: job.env,
      })
    } catch (error) {
      step.state = 'failed'
      step.exitCode = null
      job.state = 'failed'
      job.error = `无法启动 Python：${String(error?.message ?? error)}`
      this.finalize(job)
      return
    }
    job.child = spawned.child
    job.pid = spawned.child.pid ?? null
    this.persist(job)
    spawned.child.once('error', (error) => {
      appendLog(job.logPath, `\n[spawn error] ${String(error?.message ?? error)}\n`)
    })
    spawned.done.then(({ code, signal }) => {
      job.child = null
      job.pid = null
      step.endedAt = new Date().toISOString()
      step.exitCode = code
      if (step.discover === 'estimate' && code === 0) {
        const runDir = findNewestRun(this.cfg.outDir, job.estimateBaseline)
        if (runDir) {
          job.runDir = runDir
          job.planPath = path.join(runDir, 'plan.json')
        }
      }
      if (job.cancelled) {
        step.state = 'cancelled'
        job.state = 'cancelled'
        this.finalize(job)
        return
      }
      if (code === 0) {
        step.state = 'done'
        this.advance(job)
        return
      }
      step.state = 'failed'
      if (code === 3) {
        job.state = 'paused'
        job.error = '运行期看门狗暂停：已落盘，可原样续跑。'
      } else {
        job.state = 'failed'
        job.error = `步骤 ${step.kind} 退出码 ${code ?? 'null'}${signal ? `（信号 ${signal}）` : ''}`
      }
      this.finalize(job)
    })
  }

  /**
   * Persist current state to job.json.
   * @param {any} job
   */
  persist(job) {
    job.updatedAt = new Date().toISOString()
    try {
      fs.writeFileSync(path.join(job.dir, 'job.json'), JSON.stringify(serializeJob(job), null, 2), 'utf8')
    } catch {
      // A read-only runs directory should not crash the job itself.
    }
  }

  /**
   * Mark the job settled and wake every waiter.
   * @param {any} job
   */
  finalize(job) {
    this.persist(job)
    for (const waiter of [...job.waiters]) {
      try {
        waiter()
      } catch {
        // ignore
      }
    }
    job.waiters.clear()
  }

  /**
   * Build the compact live/persisted view of one job.
   * @param {any} job
   * @returns {object}
   */
  view(job) {
    const now = Date.now()
    const steps = job.steps.map((step) => {
      const started = step.startedAt ? Date.parse(step.startedAt) : null
      return {
        kind: step.kind,
        state: step.state,
        exitCode: step.exitCode ?? null,
        elapsedSec: started === null ? null : Math.max(0, Math.round(((step.endedAt ? Date.parse(step.endedAt) : now) - started) / 1000)),
      }
    })
    return {
      id: job.id,
      label: job.label,
      state: job.state,
      createdAt: job.createdAt,
      updatedAt: job.updatedAt,
      runDir: job.runDir ?? null,
      planPath: job.planPath ?? null,
      error: job.error ?? null,
      logPath: job.logPath,
      dir: job.dir,
      steps,
    }
  }

  /**
   * Read a job from memory, or from `job.json` when it is not live.
   * @param {string} jobId
   * @returns {object | undefined}
   */
  status(jobId) {
    const live = this.live.get(jobId)
    if (live) return this.view(live)
    const dir = path.join(this.cfg.jobsDir, jobId)
    let raw
    try {
      raw = JSON.parse(fs.readFileSync(path.join(dir, 'job.json'), 'utf8'))
    } catch {
      return undefined
    }
    const now = Date.now()
    let state = raw.state ?? 'unknown'
    let note = null
    if (state === 'running' && raw.pid !== null && raw.pid !== undefined && !pidAlive(raw.pid)) {
      state = 'interrupted'
      note = '任务进程已不在（插件重载或进程退出）；缓存会保留，可用同一 runDir 续跑。'
    }
    return {
      id: raw.id ?? jobId,
      label: raw.label ?? 'historica',
      state,
      note,
      createdAt: raw.createdAt ?? null,
      updatedAt: raw.updatedAt ?? null,
      runDir: raw.runDir ?? null,
      planPath: raw.planPath ?? null,
      error: raw.error ?? null,
      logPath: path.join(dir, 'job.log'),
      dir,
      steps: Array.isArray(raw.steps)
        ? raw.steps.map((step) => {
            const started = step.startedAt ? Date.parse(step.startedAt) : null
            return {
              kind: step.kind,
              state: step.state,
              exitCode: step.exitCode ?? null,
              elapsedSec: started === null
                ? null
                : Math.max(0, Math.round(((step.endedAt ? Date.parse(step.endedAt) : now) - started) / 1000)),
            }
          })
        : [],
    }
  }

  /**
   * Wait for a job to settle, or return after the timeout.
   * @param {string} jobId
   * @param {number} timeoutMs
   * @param {AbortSignal} [signal]
   * @returns {Promise<object | undefined>}
   */
  wait(jobId, timeoutMs, signal) {
    const live = this.live.get(jobId)
    if (!live || live.state !== 'running') {
      return Promise.resolve(this.status(jobId))
    }
    return new Promise((resolve) => {
      let settled = false
      const finish = () => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        live.waiters.delete(finish)
        signal?.removeEventListener('abort', onAbort)
        resolve(this.status(jobId))
      }
      const timer = setTimeout(finish, Math.max(0, timeoutMs))
      const onAbort = () => finish()
      signal?.addEventListener('abort', onAbort, { once: true })
      live.waiters.add(finish)
    })
  }

  /**
   * Request cancellation of one job.
   * @param {string} jobId
   * @returns {{ ok: boolean, message: string }}
   */
  cancel(jobId) {
    const live = this.live.get(jobId)
    if (live) {
      if (live.state !== 'running') {
        return { ok: false, message: `任务 ${jobId} 已结束（${live.state}），无需取消。` }
      }
      live.cancelled = true
      const child = live.child
      if (child && !child.killed) {
        try {
          child.kill('SIGTERM')
        } catch {
          // ignore
        }
        setTimeout(() => {
          if (live.child && !live.child.killed) {
            try {
              live.child.kill('SIGKILL')
            } catch {
              // ignore
            }
          }
        }, 5000)
      } else {
        live.state = 'cancelled'
        this.finalize(live)
      }
      return { ok: true, message: `已请求取消任务 ${jobId}；缓存会保留，之后可续跑。` }
    }
    const persisted = this.status(jobId)
    if (!persisted) return { ok: false, message: `没有这个任务：${jobId}` }
    let rawPid = null
    try {
      rawPid = JSON.parse(fs.readFileSync(path.join(this.cfg.jobsDir, jobId, 'job.json'), 'utf8')).pid ?? null
    } catch {
      rawPid = null
    }
    if (persisted.state === 'running' && rawPid && pidAlive(rawPid)) {
      try {
        process.kill(rawPid, 'SIGTERM')
        return { ok: true, message: `已向遗留任务进程 ${rawPid} 发送终止信号；缓存保留。` }
      } catch (error) {
        return { ok: false, message: `无法终止遗留进程 ${rawPid}：${String(error?.message ?? error)}` }
      }
    }
    return { ok: false, message: `任务 ${jobId} 不在本进程内（状态 ${persisted.state}），无法取消；如需终止可用任务管理器结束其 Python 进程。` }
  }

  /**
   * Kill every still-running child owned by this job manager.
   */
  disposeAll() {
    for (const job of this.live.values()) {
      job.cancelled = true
      if (job.child) {
        try {
          job.child.kill('SIGTERM')
        } catch {
          // ignore
        }
      }
    }
    this.live.clear()
  }
}
