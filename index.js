/**
 * Host half of the `dsh-historica` bundle.
 *
 * Registers one `historica` agent tool backed by the bundled historica Python
 * CLI (Landsat C2 L2 planning, windowed download, figure composition, technical
 * report) and a short system-prompt section that teaches the model the
 * capability boundaries that must not be misrepresented in deliverables.
 */
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { buildConfig } from './lib/config.js'
import { HistoricaService } from './lib/runtime.js'
import { createToolDefinition } from './lib/tool.js'

export const inject = ['tools', 'systemPrompt']

const PROMPT = `## 历史卫星影像（historica 工具）

用户要"历史影像 / 卫星影像对比 / 城市扩张 / 老影像下载 / 某地这些年变成什么样"时，调用 \`historica\` 工具，不要自己拼 STAC 链接或手写下载脚本。
用户也可以在输入框下方点「历史影像」：用地图框选 bbox 或输入行政区生成请求（对话框里的「发送给助手」会直接提交请求，执行仍走本工具）。
数据默认是 Microsoft Planetary Computer 上的 Landsat Collection 2 Level 2（公有领域，30 m，1984 年后），可选 Sentinel-2。

标准流程：
1. \`{"action":"check"}\` 先确认 Python 与依赖；失败时把 message 里的候选解释器路径反馈给用户（可用 config.pythonPath 指定）。
2. \`{"action":"plan","admin":"...","years":[1993,2024],"seasonWindow":45}\` 规划（选景 + 测速 + 算账，不下载）。返回 jobId；随后 \`{"action":"wait","jobId":"...","timeoutSec":120}\`，必要时重复 wait，直到 state 为 done / failed / paused。把预算、各期实际日期与覆盖如实汇报给用户。
3. 用户确认后 \`{"action":"download","runDir":"<plan 返回的 run 目录>"}\`（也可直接给范围与年份，让工具先 estimate 再下载）。下载耗时可能接近预估区间的上限，用 wait/status 跟进即可。
4. 完成后用 \`read_image\` 查看 \`out/series.jpg\`，用 \`present\` 交付 series.jpg、single_<年>.jpg 与 technical_report.md。

续跑：fetch 因看门狗暂停（state=paused，CLI 退出码 3）时，用同一 runDir 再 \`download\`，已下载波段命中缓存不会重下。

能力边界（必须写进交付物，不得删改）：
- 只承诺 1984 年后的 Landsat TM/ETM+/OLI；更早没有本工具能提供的影像。
- 30 m 分辨率只够看建成区、道路、水库等宏观地物，不能判读地块/宅基地边界。
- 跨 WRS-2 轨缝不保证单景完整；"缺角同季"与"满覆盖跨季"不可兼得，缺失区必须如实标注面积。
- 色调统一只用于展示，不能做变化检测或定量反演；图上与报告只写实际成像日期。`

/**
 * Plugin entry.
 * @param {import('@deepseek-ai/cordis').Context} ctx
 * @param {Record<string, unknown>} [config]
 */
export function apply(ctx, config = {}) {
  const pluginDir = path.dirname(fileURLToPath(import.meta.url))
  const service = new HistoricaService(buildConfig({ pluginDir, config: config ?? {} }))

  ctx.effect(() => {
    const unregisterTool = ctx.tools.register(createToolDefinition(service))
    const removePrompt = ctx.systemPrompt.section({
      name: 'historica',
      order: 450,
      text: PROMPT,
    })
    return () => {
      removePrompt()
      unregisterTool()
    }
  })

  ctx.effect(() => () => {
    service.dispose()
  })
}
