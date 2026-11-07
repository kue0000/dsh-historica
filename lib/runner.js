/**
 * Process and log helpers for running the historica CLI.
 *
 * Child processes run with UTF-8 stdio forced on, so Chinese CLI output is
 * readable on Windows consoles whose default code page is not UTF-8.
 */
import { spawn, spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

/** Environment additions applied to every Python invocation. */
export function pythonEnv(extra) {
  return {
    ...process.env,
    PYTHONIOENCODING: 'utf-8',
    PYTHONUTF8: '1',
    PYTHONUNBUFFERED: '1',
    PYTHONDONTWRITEBYTECODE: '1',
    ...(extra ?? {}),
  }
}

/**
 * Create a directory (recursively) when it does not exist.
 * @param {string} dir
 */
export function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true })
}

/**
 * Start one logged child process. stdout and stderr both go to `logPath`.
 * @param {{ python: string, argv: string[], cwd: string, logPath: string, env?: Record<string, string> }} spec
 * @returns {{ child: import('node:child_process').ChildProcess, done: Promise<{ code: number | null, signal: string | null }> }}
 */
export function spawnLogged(spec) {
  ensureDir(path.dirname(spec.logPath))
  const fd = fs.openSync(spec.logPath, 'a')
  let child
  try {
    child = spawn(spec.python, spec.argv, {
      cwd: spec.cwd,
      env: pythonEnv(spec.env),
      windowsHide: true,
      stdio: ['ignore', fd, fd],
    })
  } catch (error) {
    try {
      fs.closeSync(fd)
    } catch {
      // ignore
    }
    throw error
  }
  const done = new Promise((resolve) => {
    child.once('error', () => {
      try {
        fs.closeSync(fd)
      } catch {
        // ignore
      }
      resolve({ code: null, signal: 'spawn-error' })
    })
    child.once('close', (code, signal) => {
      try {
        fs.closeSync(fd)
      } catch {
        // ignore
      }
      resolve({ code, signal })
    })
  })
  return { child, done }
}

/**
 * Run one child process synchronously and capture its output.
 * @param {{ python: string, argv: string[], cwd: string, timeoutMs?: number, env?: Record<string, string> }} spec
 * @returns {{ code: number | null, signal: string | null, stdout: string, stderr: string, timedOut: boolean, spawnError?: string }}
 */
export function runCaptured(spec) {
  const result = spawnSync(spec.python, spec.argv, {
    cwd: spec.cwd,
    env: pythonEnv(spec.env),
    timeout: spec.timeoutMs,
    windowsHide: true,
    encoding: 'utf8',
    maxBuffer: 16 * 1024 * 1024,
  })
  const timedOut = result.error?.code === 'ETIMEDOUT'
  return {
    code: result.status ?? null,
    signal: result.signal ?? null,
    stdout: result.stdout ?? '',
    stderr: result.stderr ?? '',
    timedOut,
    ...(result.error && !timedOut ? { spawnError: String(result.error.message ?? result.error) } : {}),
  }
}

/**
 * Append captured output to a log file.
 * @param {string} logPath
 * @param {string} text
 */
export function appendLog(logPath, text) {
  ensureDir(path.dirname(logPath))
  fs.appendFileSync(logPath, text, 'utf8')
}

/**
 * Read the newest bytes of a log file and return the last lines within a
 * character budget.
 * @param {string} logPath
 * @param {{ maxLines?: number, maxChars?: number }} [options]
 * @returns {string}
 */
export function readTail(logPath, options = {}) {
  const maxLines = options.maxLines ?? 60
  const maxChars = options.maxChars ?? 6000
  let size
  try {
    size = fs.statSync(logPath).size
  } catch {
    return ''
  }
  if (size === 0) return ''
  const windowBytes = Math.min(size, 256 * 1024)
  const buffer = Buffer.alloc(windowBytes)
  let fd
  try {
    fd = fs.openSync(logPath, 'r')
    fs.readSync(fd, buffer, 0, windowBytes, size - windowBytes)
  } finally {
    if (fd !== undefined) {
      try {
        fs.closeSync(fd)
      } catch {
        // ignore
      }
    }
  }
  const text = buffer.toString('utf8')
  const lines = text.split(/\r?\n/)
  const tail = lines.slice(Math.max(0, lines.length - maxLines)).join('\n').trim()
  return tail.length > maxChars ? tail.slice(tail.length - maxChars) : tail
}

/**
 * Read a JSON file, returning undefined when it is missing or malformed.
 * @param {string} filePath
 * @returns {any}
 */
export function readJson(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'))
  } catch {
    return undefined
  }
}

/**
 * Return the last path segment, which is the run timestamp directory.
 * @param {string} value
 * @returns {string}
 */
export function baseName(value) {
  return path.basename(value)
}
