/**
 * Read and condense historica run artifacts into model-facing summaries.
 *
 * historica writes plan.json / estimate.json / actual.json / align.json plus
 * out/*.jpg, metadata.csv and technical_report.md. This module never rewrites
 * those files; it only reads the fields needed to describe a run.
 */
import fs from 'node:fs'
import path from 'node:path'
import { readJson } from './runner.js'

/**
 * Round for display while keeping the value numeric.
 * @param {number} value
 * @param {number} digits
 * @returns {number}
 */
function round(value, digits = 1) {
  const factor = 10 ** digits
  return Math.round(value * factor) / factor
}

/**
 * List timestamp directories under one run family (`estimate` or `fetch`).
 * @param {string} outDir
 * @param {string} family
 * @returns {string[]} absolute run directories, newest first by mtime.
 */
export function listRunDirs(outDir, family) {
  const root = path.join(outDir, family)
  let entries
  try {
    entries = fs.readdirSync(root, { withFileTypes: true })
  } catch {
    return []
  }
  return entries
    .filter((entry) => entry.isDirectory())
    .map((entry) => {
      const dir = path.join(root, entry.name)
      let mtime = 0
      try {
        mtime = fs.statSync(dir).mtimeMs
      } catch {
        mtime = 0
      }
      return { dir, mtime }
    })
    .sort((a, b) => b.mtime - a.mtime)
    .map((item) => item.dir)
}

/**
 * Find the newest estimate run that is not in the baseline snapshot.
 * @param {string} outDir
 * @param {string[]} baseline absolute directories present before the job started.
 * @returns {string | undefined}
 */
export function findNewestRun(outDir, baseline = []) {
  const known = new Set(baseline)
  const fresh = listRunDirs(outDir, 'estimate').filter((dir) => !known.has(dir))
  if (fresh.length > 0) return fresh[0]
  return listRunDirs(outDir, 'estimate')[0]
}

/**
 * Resolve the run directory that owns a plan file.
 * @param {string} planPath absolute plan.json path.
 * @returns {string}
 */
export function runDirOfPlan(planPath) {
  return path.dirname(path.resolve(planPath))
}

/**
 * Condense plan.json.
 * @param {any} plan
 * @returns {object | undefined}
 */
export function summarizePlan(plan) {
  if (!plan || typeof plan !== 'object') return undefined
  const aoi = plan.aoi ?? {}
  const grid = aoi.grid ?? {}
  const season = plan.season
  const epochs = Array.isArray(plan.epochs)
    ? plan.epochs.map((epoch) => {
        const chosen = epoch.chosen ?? {}
        return {
          targetYear: epoch.target_year,
          status: epoch.status,
          date: chosen.date ?? null,
          coveragePct: typeof chosen.coverage === 'number' ? round(chosen.coverage * 100, 1) : null,
          cloudPct: typeof chosen.cloud === 'number' ? round(chosen.cloud, 1) : null,
          nScenes: chosen.n_scenes ?? (Array.isArray(chosen.scenes) ? chosen.scenes.length : 0),
          sceneIds: Array.isArray(chosen.scenes) ? chosen.scenes : [],
          crossSeason: Boolean(epoch.season?.cross_season),
          seasonOffsetDays: epoch.season?.offset_days ?? null,
        }
      })
    : []
  return {
    range: aoi.name || aoi.name_matched || aoi.source || '(未命名范围)',
    source: aoi.source ?? null,
    areaKm2: typeof aoi.area_km2 === 'number' ? round(aoi.area_km2, 1) : null,
    crs: grid.crs ?? null,
    res: grid.res ?? null,
    grid: grid.ncols && grid.nrows ? `${grid.ncols}×${grid.nrows}` : null,
    season: season
      ? {
          enabled: Boolean(season.enabled),
          windowDays: season.window_days ?? null,
          centerLabel: season.center_label ?? null,
          crossSeasonEpochs: Array.isArray(season.cross_season_epochs) ? season.cross_season_epochs : [],
          postlockStdDays: season.postlock?.std_days ?? null,
          postlockMaxDiffDays: season.postlock?.max_diff_days ?? null,
          prelockStdDays: season.prelock?.std_days ?? null,
        }
      : null,
    epochs,
  }
}

/**
 * Condense estimate.json.
 * @param {any} estimate
 * @returns {object | undefined}
 */
export function summarizeEstimate(estimate) {
  if (!estimate || typeof estimate !== 'object') return undefined
  const total = estimate.total ?? {}
  return {
    scenes: total.scenes ?? null,
    payloadMb: typeof total.payload_mb === 'number' ? round(total.payload_mb, 1) : null,
    transferMb: typeof total.transfer_mb === 'number' ? round(total.transfer_mb, 1) : null,
    pointS: typeof total.total_s === 'number' ? round(total.total_s, 1) : null,
    loS: typeof total.total_lo_s === 'number' ? round(total.total_lo_s, 1) : null,
    hiS: typeof total.total_hi_s === 'number' ? round(total.total_hi_s, 1) : null,
    speedMbps: typeof estimate.speed_mbps === 'number' ? round(estimate.speed_mbps, 3) : null,
    speedBasis: estimate.speed_basis?.source ?? null,
    advisory: typeof estimate.advisory === 'string' ? estimate.advisory : null,
    perEpoch: Array.isArray(estimate.per_epoch)
      ? estimate.per_epoch.map((epoch) => ({
          targetYear: epoch.target_year,
          date: epoch.date ?? null,
          status: epoch.status ?? null,
          transferMb: typeof epoch.transfer_mb === 'number' ? round(epoch.transfer_mb, 1) : null,
          pointS: typeof epoch.total_s === 'number' ? round(epoch.total_s, 1) : null,
        }))
      : [],
  }
}

/**
 * Read actual.json when present.
 * @param {string} runDir
 * @returns {object | undefined}
 */
export function summarizeActual(runDir) {
  const actual = readJson(path.join(runDir, 'actual.json'))
  if (!actual) return undefined
  return {
    totalS: typeof actual.total_s === 'number' ? round(actual.total_s, 1) : null,
    paused: Boolean(actual.paused),
    pauseReason: actual.pause_reason ?? null,
    errorRatio: typeof actual.error_ratio === 'number' ? round(actual.error_ratio * 100, 1) : null,
    perEpoch: Array.isArray(actual.per_epoch) ? actual.per_epoch.length : 0,
  }
}

/**
 * Read fetch_progress.json when present.
 * @param {string} runDir
 * @returns {object | undefined}
 */
export function readProgress(runDir) {
  const progress = readJson(path.join(runDir, 'fetch_progress.json'))
  if (!progress) return undefined
  return {
    updatedAt: progress.updated_at ?? null,
    completedYears: Array.isArray(progress.completed_years) ? progress.completed_years : [],
    paused: Boolean(progress.paused),
    reason: progress.reason ?? null,
  }
}

/**
 * Read align.json when present.
 * @param {string} runDir
 * @returns {object | undefined}
 */
export function summarizeAlign(runDir) {
  const align = readJson(path.join(runDir, 'align.json'))
  if (!align) return undefined
  return {
    pairs: align.pairs ?? null,
    locked: align.locked ?? null,
    unlocked: align.unlocked ?? null,
    zeroShift: align.zero_shift ?? null,
    minConf: typeof align.min_conf === 'number' ? round(align.min_conf, 3) : null,
  }
}

/**
 * List existing output artifacts in a run directory.
 * @param {string} runDir
 * @returns {{ files: string[], epochCount: number, report?: string }}
 */
export function collectArtifacts(runDir) {
  if (!runDir) return { files: [], epochCount: 0 }
  const outDir = path.join(runDir, 'out')
  const dataDir = path.join(runDir, 'data')
  const files = []
  const pushIfFile = (file) => {
    try {
      if (fs.statSync(file).isFile()) files.push(file)
    } catch {
      // missing
    }
  }
  pushIfFile(path.join(outDir, 'series.jpg'))
  let singles = []
  try {
    singles = fs
      .readdirSync(outDir)
      .filter((name) => /^single_\d{4}\.jpg$/.test(name))
      .sort()
  } catch {
    singles = []
  }
  for (const name of singles) pushIfFile(path.join(outDir, name))
  pushIfFile(path.join(outDir, 'metadata.csv'))
  pushIfFile(path.join(outDir, 'technical_report.md'))
  pushIfFile(path.join(runDir, 'align.json'))
  pushIfFile(path.join(runDir, 'actual.json'))
  pushIfFile(path.join(runDir, 'fetch_progress.json'))

  let epochCount = 0
  try {
    epochCount = fs.readdirSync(dataDir).filter((name) => /^epoch_\d{4}\.npz$/.test(name)).length
  } catch {
    epochCount = 0
  }
  return {
    files,
    epochCount,
    report: files.find((file) => file.endsWith('technical_report.md')),
  }
}

/**
 * Summarize recent runs for the `list` action.
 * @param {string} outDir
 * @param {number} [limit]
 * @returns {Array<object>}
 */
export function listRuns(outDir, limit = 10) {
  const seen = new Set()
  const runs = []
  for (const family of ['estimate', 'fetch']) {
    for (const dir of listRunDirs(outDir, family)) {
      if (seen.has(dir)) continue
      seen.add(dir)
      const plan = readJson(path.join(dir, 'plan.json'))
      if (!plan) continue
      let mtime = null
      try {
        mtime = fs.statSync(dir).mtime.toISOString()
      } catch {
        mtime = null
      }
      const planSummary = summarizePlan(plan)
      const artifacts = collectArtifacts(dir)
      runs.push({
        runDir: dir,
        family,
        updatedAt: mtime,
        range: planSummary?.range ?? null,
        epochs: planSummary?.epochs ?? [],
        hasSeries: artifacts.files.some((file) => file.endsWith('series.jpg')),
        report: artifacts.report ?? null,
      })
    }
  }
  runs.sort((a, b) => String(b.updatedAt).localeCompare(String(a.updatedAt)))
  return runs.slice(0, limit)
}
