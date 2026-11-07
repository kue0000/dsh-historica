# dsh-historica · 历史影像下载插件

把 **historica v1.1.0**（历史卫星影像选景 / 下载 / 多期对比出图 CLI）接成 DeepSeek Harness 的常驻插件：
在会话里说人话，或在输入框下方用地图框选 / 输入行政区，即可规划并下载多期 Landsat 影像，生成对比图与技术报告。

- npm 包名：`dsh-historica`
- 类型：Host + Client **bundle**（`dsh.bundle` + `dsh.client`）
- 许可证：MIT
- 源码：<https://github.com/kue0000/dsh-historica>
- 兼容：Package `engines.dsh` 为 `>=0.2.0-rc.1 <0.3.0-0`；需要 Python 3.10+

---

## 1. 安装

### 1.1 安装插件

任选一种：

```powershell
# CLI（profile 名按你的 DSH 实例替换）
dsh plugin --profile desktop add dsh-historica@1.1.0
```

或在 DSH 的 **插件** 页面安装；也可以让 agent 调用 `plugin_manager`，`target` 填 `dsh-historica@1.1.0`。
若你把它发布成了自己的索引，可在 **插件 → 发现 → 添加索引源** 里添加索引 URL 后一键安装。

### 1.2 安装 Python 依赖（首次）

本插件自带 historica 源码（`runtime/historica/`），但不自带 Python 与科学计算依赖：

```powershell
python -m venv "$env:USERPROFILE\.dsh\historica-venv"
& "$env:USERPROFILE\.dsh\historica-venv\Scripts\python.exe" -m pip install numpy shapely pyproj matplotlib rasterio pystac-client pyshp
```

装到别处也可以：在插件配置里指定 `pythonPath`（见第 3 节），或在环境变量 `HISTORICA_PYTHON` 指定解释器。
装好后调用 `historica` 工具的 `{"action":"check"}` 自检；失败时它会列出全部候选路径与安装提示。

### 1.3 数据目录

默认输出到 `$DSH_HOME/historica-runs`（未设置 `DSH_HOME` 时为 `~/.dsh/historica-runs`），
每次运行位于 `historica-runs/estimate/<时间戳>/`。可用 `outDir` 覆盖。

---

## 2. 使用

### 2.1 对话

```
帮我看看南平市延平区 1993 年和 2024 年的卫星影像变化
拉珠三角 1993/2003/2012/2024 四期 Landsat 对比图
贵阳花溪区这些年城市扩张成什么样，要带缺测标注
用 bbox 113.55,22.45,114.55,23.26 出四期影像
先别下载，算一下这个范围四期要多少流量、多久
```

标准流程（agent 自动执行）：

1. `check` 自检解释器 / 依赖 / 输出目录；
2. `plan` 后台选景 + 测速 + 算账（**不下载**），返回 `jobId`；
3. `wait` / `status` 轮询到 `done`，把预算、各期**实际成像日期**与覆盖如实汇报；
4. 确认后 `download`（可带 `runDir` 续跑，也可直接给范围+年份自动先 estimate 再下载）；
5. 用 `read_image` 查看 `out/series.jpg`，用 `present` 交付对比图与技术报告。

### 2.2 地图框选 / 行政区（Web UI）

输入框下方有一个「历史影像」按钮，点开是范围选择对话框：

- **地图框选**：轻量 OSM 地图（自渲染瓦片，不依赖 Leaflet/CDN 脚本）。默认「框选」模式，按住左键拖出矩形得到 `W,S,E,N`；切「移动」拖画布；`+ / − / ⌂` 缩放复位；下方实时显示 bbox 与近似面积。
- **行政区**：输入 `南平市延平区` 这类名称，或点快捷项；边界在服务端由 DataV.GeoAtlas / GADM 解析。
- **年份 / 季节窗口 / 执行方式**：默认 `1993,2004,2014,2024`、同季 ±45 天；可选「先规划（不下载）」或「直接下载并出图」。
- 点「发送给助手」直接提交请求，或「仅插入」写入输入框；执行仍走同一个 `historica` 工具。

> 地图底图来自 `tile.openstreetmap.org`（需外网；瓦片加载失败时仍可按经纬度框选）。行政边界为示意，不能当权属界。

### 2.3 工具动作

| action | 作用 | 后台 |
|---|---|---|
| `check` | Python、依赖、historica 版本、输出目录 | 否 |
| `selftest` | 用随包离线夹具跑一次 estimate（不下载） | 否 |
| `plan` | 选景 + 探针测速 + 预估 | 是，返回 `jobId` |
| `download` | estimate（可选）→ fetch → compose → report；支持 `runDir` 续跑 / `onlyYears` / `steps` | 是 |
| `render` | 对已有 run 只重跑 compose + report | 是 |
| `status` / `wait` | 查任务 / 阻塞等待（默认 120 秒，最大 600 秒） | 否 |
| `cancel` | 取消运行中的任务（缓存保留，可续跑） | 否 |
| `list` | 列最近 run 与产物、本进程任务 | 否 |

参数：`admin` / `bbox` / `vector` / `wkt`（四选一）、`years` 或 `range`+`step`、`window`、`seasonWindow`、`preferDates`、`res`、`provider`、`case`、`noProbe`/`speed`、`runDir`/`planPath`、`steps`/`onlyYears`、`jobId`/`timeoutSec`/`limit`。

---

## 3. 配置

无需配置即可工作。需要覆盖时，在 profile 的 `cordis.patch.yml` 顶层加一条同 `id` 的覆盖行：

```yaml
- id: historica
  name: dsh-historica
  config:
    pythonPath: 'C:/path/to/python.exe'   # 可选
    outDir: 'D:/data/historica-runs'      # 可选
    defaultRes: 30
    seasonWindow: 45
    waitDefaultSec: 120
    waitMaxSec: 600
```

| 键 | 默认 | 说明 |
|---|---|---|
| `pythonPath` | 自动探测 | 显式解释器；相对路径按插件目录解析 |
| `historicaRoot` | `runtime/historica` | historica 包根 |
| `dataHome` | `$DSH_HOME` 或 `~/.dsh` | 数据目录 |
| `outDir` | `<dataHome>/historica-runs` | 运行输出根目录 |
| `defaultRes` | 30 | 输出分辨率（米） |
| `seasonWindow` | 45 | 同季半窗口（显式配置时才传给 CLI） |
| `waitDefaultSec` / `waitMaxSec` | 120 / 600 | `wait` 的默认与最大等待 |
| `selfTestTimeoutSec` | 240 | `selftest` 的同步超时 |
| `logTailLines` / `logTailChars` | 60 / 6000 | 工具结果中的日志尾部 |

**解释器探测顺序**：`config.pythonPath` → `HISTORICA_PYTHON` → `<插件>/runtime/python/...` → `<插件>/runtime/historica/.venv/...` → `<dataHome>/historica-venv/...` → PATH 上的 `python` / `python3`。

> 上游 historica 的吞吐校准表写在 historica 包根下：`runtime/historica/cache/calibration.json`（运行态数据）。`__pycache__` 已通过 `PYTHONDONTWRITEBYTECODE=1` 抑制。

---

## 4. 数据源与能力边界

数据默认来自 Microsoft Planetary Computer 的 **Landsat Collection 2 Level 2**（公有领域，30 m；1984 年后），
可选 Sentinel-2 L2A（2015 年后，默认关闭）。只对目标窗口做 COG HTTP Range GET，不下载整景。

**必须原样写进任何交付物的边界**（插件系统提示词也强制模型遵守）：

- 只承诺 1984 年后的 Landsat TM/ETM+/OLI；更早没有本工具能提供的影像。
- 30 m 分辨率只够看建成区、道路、水库等宏观地物，**不能判读地块/宅基地边界**。
- 跨 WRS-2 轨缝不保证单景完整；「缺角同季」与「满覆盖跨季」不可兼得，缺失区必须如实标注面积。
- 色调统一只用于展示，**不能做变化检测或定量反演**；图上与报告只写实际成像日期，不用目标年替代。
- 预估是 p5–p95 区间，不是承诺；下载耗时受时段带宽影响很大。
- 行政边界为示意（DataV / GADM 百米级），图面红线不能当权属界。

---

## 5. 验证

开发机（DSH `0.2.2-rc.1`、Python 3.12、numpy 2.5.4 / shapely 2.2.0 / pyproj 3.8.0 / matplotlib 3.11.2）上已验证：

- `historica check` 通过；`selftest` 离线 estimate 退出 0；
- 后台 `plan` 与 `download`（estimate → fetch → compose → report）真实联网跑通，产出 `series.jpg`、`single_<年>.jpg`、`metadata.csv`、`technical_report.md`；
- `cancel`、`wait`、`status`、`list` 正常；historica 自带 36 个单元测试全部通过；
- Web UI 入口 `historica-launcher` 在 `conversation.composer.dock` 注册为 active；地图投影往返误差 < 3e-14°。
- 未验证：浏览器自动化点击（本机无浏览器控制）、不同 DSH 版本 / 无外网环境的安装。

---

## 6. 目录结构

```
dsh-historica/
├─ package.json            # bundle 清单：dsh.bundle.patch + dsh.client + locale/icon
├─ cordis.patch.yml        # insert 行：id=historica → dsh-historica
├─ index.js                # Host 入口：注册工具 + 系统提示词
├─ client.js               # Client 入口：composer dock 入口 + 地图/行政区对话框
├─ lib/                    # config / runner / jobs / runtime / summarize / tool
├─ runtime/historica/      # 随包携带的 historica v1.1.0（含离线测试夹具）
├─ locale/en.json zh.json  # 插件标题/描述
├─ icon.svg                # 图标
├─ LICENSE                 # MIT
├─ PUBLISHING.md           # 发布到 npm / 索引 / dsh.so 的步骤
└─ README.md
```

运行期数据在 `<outDir>/` 与 `runtime/historica/cache/calibration.json`，不属于源码。

---

## 7. 数据许可

- Landsat C2 L2：USGS/NASA，公有领域，需署名 USGS；
- Sentinel-2 L2A：Copernicus/ESA，开放许可，需署名 ESA；
- 行政边界：DataV.GeoAtlas（示意）/ GADM 4.1（学术用途）；
- 本插件代码：MIT。
