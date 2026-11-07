/**
 * Runtime configuration for the historica bundle.
 *
 * The bundle ships the historica Python package under `runtime/historica`, so
 * every path here is derived from the installed package directory unless the
 * profile patch overrides it. No absolute machine path is hard-coded.
 *
 * Supported `config` keys (all optional):
 *   historicaRoot  path to the historica package root (relative to this plugin)
 *   pythonPath     Python interpreter to use (relative to this plugin or absolute)
 *   outDir         where runs are written; default `<DSH_HOME>/historica-runs`
 *   dataHome       data directory; default `$DSH_HOME` or `~/.dsh`
 *   defaultRes     default output resolution in metres (30)
 *   seasonWindow   default same-season half-window in days (45)
 *   waitDefaultSec default `wait` timeout (120)
 *   waitMaxSec     maximum `wait` timeout (600)
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const DEFAULTS = {
  defaultRes: 30,
  seasonWindow: 45,
  waitDefaultSec: 120,
  waitMaxSec: 600,
  selfTestTimeoutSec: 240,
  logTailLines: 60,
  logTailChars: 6000,
}

/**
 * Resolve one path-like config value against the package directory.
 * @param {string} pluginDir absolute package directory.
 * @param {string | undefined} value configured value.
 * @param {string} fallback fallback relative path.
 * @returns {string} absolute path.
 */
function resolvePath(pluginDir, value, fallback) {
  const chosen = typeof value === 'string' && value.trim() !== '' ? value.trim() : fallback
  return path.isAbsolute(chosen) ? path.normalize(chosen) : path.resolve(pluginDir, chosen)
}

/**
 * Build the effective runtime configuration for this plugin instance.
 * @param {{ pluginDir: string, config?: Record<string, unknown> }} options
 * @returns {ReturnType<typeof buildConfig>}
 */
export function buildConfig(options) {
  const { pluginDir } = options
  const raw = options.config && typeof options.config === 'object' ? options.config : {}
  const configuredDataHome = typeof raw.dataHome === 'string' && raw.dataHome.trim() !== ''
    ? resolvePath(pluginDir, raw.dataHome, '')
    : ''
  const dataHome = configuredDataHome
    || (typeof process.env.DSH_HOME === 'string' && process.env.DSH_HOME.trim() !== ''
      ? path.resolve(process.env.DSH_HOME.trim())
      : path.join(os.homedir(), '.dsh'))
  const historicaRoot = resolvePath(pluginDir, raw.historicaRoot, 'runtime/historica')
  const outDir = resolvePath(pluginDir, raw.outDir, path.join(dataHome, 'historica-runs'))
  const entry = path.join(historicaRoot, 'historica.py')

  const configuredPython = typeof raw.pythonPath === 'string' && raw.pythonPath.trim() !== ''
    ? resolvePath(pluginDir, raw.pythonPath, '')
    : ''

  /** Candidate interpreters, most specific first. */
  const pythonCandidates = []
  if (configuredPython) pythonCandidates.push(configuredPython)
  if (typeof process.env.HISTORICA_PYTHON === 'string' && process.env.HISTORICA_PYTHON.trim() !== '') {
    pythonCandidates.push(process.env.HISTORICA_PYTHON.trim())
  }
  pythonCandidates.push(
    path.join(pluginDir, 'runtime', 'python', 'python.exe'),
    path.join(pluginDir, 'runtime', 'python', 'bin', 'python3'),
    path.join(historicaRoot, '.venv', 'Scripts', 'python.exe'),
    path.join(historicaRoot, '.venv', 'bin', 'python'),
    path.join(dataHome, 'historica-venv', 'Scripts', 'python.exe'),
    path.join(dataHome, 'historica-venv', 'bin', 'python'),
    path.join(pluginDir, '..', 'historica-venv', 'Scripts', 'python.exe'),
    path.join(pluginDir, '..', 'historica-venv', 'bin', 'python'),
  )

  const number = (key, fallback) => {
    const value = raw[key]
    return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : fallback
  }

  return {
    pluginDir,
    historicaRoot,
    dataHome,
    entry,
    outDir,
    jobsDir: path.join(outDir, '_jobs'),
    configuredPython,
    pythonCandidates,
    cwd: historicaRoot,
    defaultRes: number('defaultRes', DEFAULTS.defaultRes),
    seasonWindow: number('seasonWindow', DEFAULTS.seasonWindow),
    seasonWindowExplicit: typeof raw.seasonWindow === 'number' && Number.isFinite(raw.seasonWindow) && raw.seasonWindow >= 0,
    resExplicit: typeof raw.defaultRes === 'number' && Number.isFinite(raw.defaultRes),
    waitDefaultSec: number('waitDefaultSec', DEFAULTS.waitDefaultSec),
    waitMaxSec: number('waitMaxSec', DEFAULTS.waitMaxSec),
    selfTestTimeoutSec: number('selfTestTimeoutSec', DEFAULTS.selfTestTimeoutSec),
    logTailLines: number('logTailLines', DEFAULTS.logTailLines),
    logTailChars: number('logTailChars', DEFAULTS.logTailChars),
  }
}

/**
 * Pick the first candidate interpreter that exists. Falls back to the platform
 * `python` command so a PATH-installed interpreter still produces a useful
 * diagnostic instead of a missing-executable crash.
 * @param {ReturnType<typeof buildConfig>} cfg
 * @returns {string} interpreter path or command name.
 */
export function resolvePython(cfg) {
  for (const candidate of cfg.pythonCandidates) {
    try {
      if (fs.existsSync(candidate)) return candidate
    } catch {
      // ignore unreadable candidates
    }
  }
  return process.platform === 'win32' ? 'python' : 'python3'
}

/**
 * Describe every candidate interpreter with an existence flag for diagnostics.
 * @param {ReturnType<typeof buildConfig>} cfg
 * @returns {Array<{ path: string, exists: boolean }>}
 */
export function describePythonCandidates(cfg) {
  return cfg.pythonCandidates.map((candidate) => {
    let exists = false
    try {
      exists = fs.existsSync(candidate)
    } catch {
      exists = false
    }
    return { path: candidate, exists }
  })
}
