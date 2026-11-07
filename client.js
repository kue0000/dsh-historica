/**
 * Client half of the `dsh-historica` bundle.
 *
 * Adds a compact "历史影像" entry below the composer. It opens a range picker
 * with a slippy map (drag to draw a bbox) or an administrative-area input,
 * then composes a request for the `historica` agent tool and inserts it into
 * the composer. The download itself stays with the Host tool, so the picker
 * holds no privileged state.
 *
 * Plain JavaScript on purpose: no Harness Client package import and no build
 * step. Only React comes from the shared module table.
 */
window.__ModuleLoader__.load({
  id: 'dsh-historica',
  factory(require) {
    const React = require('react')
    let ReactDOM = null
    try {
      ReactDOM = require('react-dom')
    } catch (error) {
      console.warn('[historica] react-dom unavailable; the dialog will render inline', error)
    }
    const h = React.createElement
    const { useEffect, useMemo, useRef, useState, useSyncExternalStore } = React
    const createPortal = ReactDOM && typeof ReactDOM.createPortal === 'function' ? ReactDOM.createPortal : null

    /* ------------------------------------------------------------------ */
    /* Text                                                                */
    /* ------------------------------------------------------------------ */

    const ZH = {
      launcher: '历史影像',
      launcherTitle: '在地图上框选范围，或按行政区选择',
      title: '历史影像 · 选择范围',
      subtitle: '框选地图或输入行政区，生成请求后发送给 historica 工具。',
      tabMap: '地图框选',
      tabAdmin: '行政区',
      panelMap: '地图框选',
      panelAdmin: '指定行政区',
      mapHint: '按住左键拖动绘制矩形；切到“移动”可拖动画布。',
      drawMode: '框选',
      panMode: '移动',
      zoomIn: '放大',
      zoomOut: '缩小',
      resetView: '回到全国',
      presets: '快速定位',
      bboxLabel: '当前范围（W,S,E,N）',
      bboxEmpty: '还没有框选范围',
      areaLabel: '约',
      adminLabel: '行政区名称',
      adminPlaceholder: '如：南平市延平区 / 广东省深圳市',
      adminHint: '名称由 DataV.GeoAtlas 解析，失败时回退 GADM；边界仅供示意。',
      yearsLabel: '目标年份',
      yearsHint: '逗号分隔，如 1993,2004,2014,2024',
      seasonLabel: '同季窗口（±天）',
      seasonHint: '0 = 关闭季节锁，允许跨季',
      modeLabel: '执行方式',
      modePlan: '先规划（不下载）',
      modeDownload: '直接下载并出图',
      promptLabel: '将发送的请求',
      promptHint: '可先修改；发送后 historica 工具会按此执行。',
      copy: '复制请求',
      copied: '已复制到剪贴板。',
      insert: '仅插入',
      inserted: '已插入输入框；关闭窗口后按 Enter 发送。',
      send: '发送给助手',
      sent: '已发送给助手，正在规划/下载；进度会出现在对话里。',
      insertFallback: '无法自动插入，已复制请求，请粘贴到输入框。',
      close: '关闭',
      clear: '清空范围',
      attribution: '© OpenStreetMap contributors',
      tileError: '底图瓦片加载失败，仍可按经纬度框选。',
      invalidYears: '年份格式无效：请用逗号分隔的 4 位年份。',
      invalidAdmin: '请先输入行政区名称。',
      invalidBbox: '请先在地图上框选一个范围。',
      invalidSeason: '同季窗口需为 0–120 的整数。',
      unknown: '未知错误',
    }

    const EN = {
      launcher: 'Historical imagery',
      launcherTitle: 'Draw a bbox on the map or pick an administrative area',
      title: 'Historical imagery · choose extent',
      subtitle: 'Draw on the map or name an administrative area, then send the request to the historica tool.',
      tabMap: 'Map bbox',
      tabAdmin: 'Admin area',
      panelMap: 'Map bbox',
      panelAdmin: 'Administrative area',
      mapHint: 'Drag with the left button to draw a rectangle; switch to Move to pan.',
      drawMode: 'Draw',
      panMode: 'Move',
      zoomIn: 'Zoom in',
      zoomOut: 'Zoom out',
      resetView: 'Reset view',
      presets: 'Jump to',
      bboxLabel: 'Current bbox (W,S,E,N)',
      bboxEmpty: 'No bbox yet',
      areaLabel: 'about',
      adminLabel: 'Administrative area',
      adminPlaceholder: 'e.g. 南平市延平区 / 广东省深圳市',
      adminHint: 'Resolved by DataV.GeoAtlas, falling back to GADM; boundaries are indicative.',
      yearsLabel: 'Target years',
      yearsHint: 'Comma separated, e.g. 1993,2004,2014,2024',
      seasonLabel: 'Same-season window (±days)',
      seasonHint: '0 disables the season lock',
      modeLabel: 'Mode',
      modePlan: 'Plan only (no download)',
      modeDownload: 'Download and render',
      promptLabel: 'Request to send',
      promptHint: 'You may edit it; the historica tool follows this request.',
      copy: 'Copy request',
      copied: 'Copied to the clipboard.',
      insert: 'Insert only',
      inserted: 'Inserted; close this dialog and press Enter to send.',
      send: 'Send to assistant',
      sent: 'Sent to the assistant; planning/download progress appears in the conversation.',
      insertFallback: 'Could not insert automatically; the request was copied — paste it into the composer.',
      close: 'Close',
      clear: 'Clear bbox',
      attribution: '© OpenStreetMap contributors',
      tileError: 'Map tiles failed to load; bbox selection still works.',
      invalidYears: 'Invalid years: use comma-separated 4-digit years.',
      invalidAdmin: 'Enter an administrative area first.',
      invalidBbox: 'Draw a bbox on the map first.',
      invalidSeason: 'The season window must be an integer from 0 to 120.',
      unknown: 'Unknown error',
    }

    /* ------------------------------------------------------------------ */
    /* Geometry                                                            */
    /* ------------------------------------------------------------------ */

    const TILE = 256
    const MIN_Z = 2
    const MAX_Z = 18
    const clamp = (value, min, max) => Math.min(max, Math.max(min, value))
    const worldSize = (z) => TILE * Math.pow(2, z)
    const lonToX = (lon, z) => ((lon + 180) / 360) * worldSize(z)
    const latToY = (lat, z) => {
      const s = Math.sin((lat * Math.PI) / 180)
      return (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * worldSize(z)
    }
    const xToLon = (x, z) => (x / worldSize(z)) * 360 - 180
    const yToLat = (y, z) => {
      const n = Math.PI - (2 * Math.PI * y) / worldSize(z)
      return (180 / Math.PI) * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n)))
    }
    const normalizeBbox = (a, b, c, d) => [Math.min(a, c), Math.min(b, d), Math.max(a, c), Math.max(b, d)]

    const MAP_PRESETS = [
      { id: 'china', label: '全国', lat: 35.0, lon: 105.0, z: 4 },
      { id: 'shenzhen', label: '深圳都市核心', bbox: [113.88, 22.44, 114.18, 22.64], lat: 22.54, lon: 114.03, z: 11 },
      { id: 'prd', label: '珠三角核心', bbox: [113.55, 22.45, 114.55, 23.26], lat: 22.85, lon: 114.05, z: 9 },
      { id: 'beijing', label: '北京', bbox: [116.20, 39.75, 116.55, 40.05], lat: 39.9, lon: 116.37, z: 10 },
      { id: 'shanghai', label: '上海', bbox: [121.30, 31.10, 121.65, 31.40], lat: 31.25, lon: 121.47, z: 10 },
      { id: 'huaxi', label: '贵阳花溪', bbox: [106.45, 26.19, 106.88, 26.57], lat: 26.38, lon: 106.66, z: 10 },
      { id: 'yanping', label: '南平延平', bbox: [117.90, 26.25, 118.50, 27.00], lat: 26.62, lon: 118.2, z: 9 },
    ]

    const ADMIN_PRESETS = ['南平市延平区', '贵阳市花溪区', '广东省深圳市', '北京市', '上海市']

    /** Approximate area of a WGS84 bbox in square kilometres (display only). */
    function bboxAreaKm2(bbox) {
      if (!bbox) return 0
      const midLat = ((bbox[1] + bbox[3]) / 2) * (Math.PI / 180)
      const widthKm = Math.abs(bbox[2] - bbox[0]) * 111.32 * Math.cos(midLat)
      const heightKm = Math.abs(bbox[3] - bbox[1]) * 110.57
      return widthKm * heightKm
    }

    /** Parse a comma/space separated year list, preserving invalid tokens. */
    function parseYears(text) {
      const tokens = String(text).split(/[,，\s]+/).filter(Boolean)
      const years = []
      const invalid = []
      for (const token of tokens) {
        const year = Number(token)
        if (Number.isInteger(year) && year >= 1972 && year <= 2035) years.push(year)
        else invalid.push(token)
      }
      return { years: [...new Set(years)], invalid }
    }

    /* ------------------------------------------------------------------ */
    /* Styles                                                              */
    /* ------------------------------------------------------------------ */

    const CSS = `
.hx-launch{display:inline-flex;align-items:center;gap:6px;height:24px;padding:0 10px;border-radius:999px;border:1px solid var(--dsw-alias-border-l1);background:transparent;color:var(--dsw-alias-label-secondary);font:inherit;font-size:12px;line-height:1;cursor:pointer;transition:background .12s,color .12s}
.hx-launch:hover{background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-primary)}
.hx-launch:focus-visible{outline:2px solid var(--dsw-alias-brand-primary);outline-offset:2px}
.hx-backdrop{position:fixed;inset:0;z-index:1200;display:flex;align-items:center;justify-content:center;padding:16px;background:rgba(0,0,0,.45)}
.hx-panel{width:min(800px,calc(100vw - 32px));max-height:min(90vh,940px);overflow:auto;display:flex;flex-direction:column;gap:14px;padding:18px;border-radius:14px;border:1px solid var(--dsw-alias-border-l2);background:var(--dsw-alias-bg-overlay);color:var(--dsw-alias-label-primary);box-shadow:0 18px 52px rgba(0,0,0,.32);font-size:13px}
.hx-panel *{box-sizing:border-box}
.hx-head{display:flex;align-items:flex-start;gap:12px;justify-content:space-between}
.hx-title{font-size:15px;font-weight:600;line-height:1.3}
.hx-sub{margin-top:4px;font-size:12px;line-height:1.5;color:var(--dsw-alias-label-secondary)}
.hx-close{flex:none;width:28px;height:28px;border-radius:8px;border:1px solid transparent;background:transparent;color:var(--dsw-alias-label-secondary);font-size:16px;line-height:1;cursor:pointer}
.hx-close:hover{background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-primary)}
.hx-tabs{display:flex;gap:4px;padding:3px;border-radius:10px;background:var(--dsw-alias-bg-layer-1);border:1px solid var(--dsw-alias-border-l1);align-self:flex-start}
.hx-tab{height:26px;padding:0 14px;border-radius:7px;border:1px solid transparent;background:transparent;color:var(--dsw-alias-label-secondary);font:inherit;font-size:12px;cursor:pointer}
.hx-tab-active{background:var(--dsw-alias-bg-layer-2);border-color:var(--dsw-alias-border-l2);color:var(--dsw-alias-label-primary)}
.hx-map{position:relative;width:100%;height:340px;overflow:hidden;border-radius:10px;border:1px solid var(--dsw-alias-border-l1);background:var(--dsw-alias-bg-layer-2);user-select:none;touch-action:none;cursor:crosshair}
.hx-map-pan{cursor:grab}
.hx-tile{position:absolute;width:256px;height:256px;pointer-events:none;user-select:none}
.hx-rect{position:absolute;border:2px solid var(--dsw-alias-brand-primary);background:rgba(36,123,191,.16);border-radius:2px;pointer-events:none;box-shadow:inset 0 0 0 1px rgba(255,255,255,.35)}
.hx-mapctrl{position:absolute;top:8px;right:8px;display:flex;flex-direction:column;gap:4px;z-index:3}
.hx-mapbtn{min-width:30px;height:28px;padding:0 8px;border-radius:7px;border:1px solid var(--dsw-alias-border-l2);background:var(--dsw-alias-bg-overlay);color:var(--dsw-alias-label-primary);font-size:12px;line-height:1;cursor:pointer}
.hx-mapbtn:hover{background:var(--dsw-alias-bg-layer-2)}
.hx-maptools{position:absolute;left:8px;top:8px;display:flex;gap:4px;z-index:3}
.hx-mapbtn-active{border-color:var(--dsw-alias-brand-primary);color:var(--dsw-alias-brand-primary)}
.hx-attrib{position:absolute;right:6px;bottom:4px;z-index:3;padding:1px 5px;border-radius:4px;background:var(--dsw-alias-bg-overlay);color:var(--dsw-alias-label-secondary);font-size:10px;opacity:.88}
.hx-attrib a{color:inherit;text-decoration:none}
.hx-maperr{position:absolute;left:8px;bottom:8px;z-index:3;max-width:70%;padding:3px 7px;border-radius:6px;background:var(--dsw-alias-bg-overlay);border:1px solid var(--dsw-alias-state-warn-primary);color:var(--dsw-alias-state-warn-primary);font-size:11px}
.hx-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.hx-label{font-size:12px;color:var(--dsw-alias-label-secondary);margin-bottom:5px}
.hx-input{width:100%;height:32px;padding:0 9px;border-radius:8px;border:1px solid var(--dsw-alias-border-l1);background:var(--dsw-alias-bg-layer-1);color:var(--dsw-alias-label-primary);font:inherit;font-size:13px}
.hx-input:focus{outline:none;border-color:var(--dsw-alias-brand-primary)}
.hx-input-num{width:110px}
.hx-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px}
.hx-box{padding:10px 12px;border-radius:10px;border:1px solid var(--dsw-alias-border-l1);background:var(--dsw-alias-bg-layer-1)}
.hx-mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;line-height:1.5;word-break:break-all;color:var(--dsw-alias-label-primary)}
.hx-muted{color:var(--dsw-alias-label-secondary);font-size:12px}
.hx-chip{height:26px;padding:0 10px;border-radius:999px;border:1px solid var(--dsw-alias-border-l1);background:transparent;color:var(--dsw-alias-label-secondary);font:inherit;font-size:12px;cursor:pointer}
.hx-chip:hover{background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-primary)}
.hx-chip-active{border-color:var(--dsw-alias-brand-primary);color:var(--dsw-alias-brand-primary)}
.hx-textarea{width:100%;min-height:92px;resize:vertical;padding:8px 10px;border-radius:8px;border:1px solid var(--dsw-alias-border-l1);background:var(--dsw-alias-bg-layer-1);color:var(--dsw-alias-label-primary);font:inherit;font-size:12px;line-height:1.6}
.hx-textarea:focus{outline:none;border-color:var(--dsw-alias-brand-primary)}
.hx-seg{display:inline-flex;padding:2px;gap:2px;border-radius:9px;background:var(--dsw-alias-bg-layer-2);border:1px solid var(--dsw-alias-border-l1)}
.hx-seg button{height:26px;padding:0 10px;border-radius:7px;border:1px solid transparent;background:transparent;color:var(--dsw-alias-label-secondary);font:inherit;font-size:12px;cursor:pointer}
.hx-seg button.hx-seg-active{background:var(--dsw-alias-bg-overlay);border-color:var(--dsw-alias-border-l2);color:var(--dsw-alias-label-primary)}
.hx-foot{display:flex;align-items:center;gap:10px;justify-content:space-between;flex-wrap:wrap}
.hx-status{flex:1 1 260px;min-height:18px;font-size:12px;color:var(--dsw-alias-label-secondary)}
.hx-status-ok{color:var(--dsw-alias-state-success-primary)}
.hx-status-err{color:var(--dsw-alias-state-error-primary)}
.hx-btn{height:32px;padding:0 14px;border-radius:9px;border:1px solid var(--dsw-alias-border-l2);background:var(--dsw-alias-bg-layer-1);color:var(--dsw-alias-label-primary);font:inherit;font-size:12px;cursor:pointer}
.hx-btn:hover{background:var(--dsw-alias-bg-layer-2)}
.hx-btn:disabled{opacity:.45;cursor:not-allowed}
.hx-btn-primary{border-color:var(--dsw-alias-brand-primary);color:var(--dsw-alias-brand-primary);background:transparent}
.hx-btn-primary:hover{background:var(--dsw-alias-bg-layer-2)}
.hx-error{color:var(--dsw-alias-state-error-primary);font-size:12px}
@media (max-width:640px){.hx-panel{padding:14px;gap:10px}.hx-map{height:260px}.hx-grid{grid-template-columns:1fr}}
@media (prefers-reduced-motion:no-preference){.hx-backdrop{animation:hx-fade .12s ease-out}.hx-panel{animation:hx-rise .14s ease-out}@keyframes hx-fade{from{opacity:0}to{opacity:1}}@keyframes hx-rise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}}
`

    /* ------------------------------------------------------------------ */
    /* Plugin entry                                                        */
    /* ------------------------------------------------------------------ */

    return {
      inject: ['slots', 'locale'],
      apply(ctx) {
        /* ---- locale ---- */
        const registerLocale = (locale, dict) => {
          try {
            const create = () => ctx.locale.register('historica', locale, dict)
            if (typeof ctx.effect === 'function') return ctx.effect(create, `historica locale ${locale}`)
            return create()
          } catch (error) {
            console.warn('[historica] locale registration failed', error)
            return undefined
          }
        }
        registerLocale('zh', ZH)
        registerLocale('en', EN)
        let bound = null
        try {
          bound = ctx.locale.bind('historica')
        } catch (error) {
          console.warn('[historica] locale bind failed', error)
        }
        const tr = (key) => {
          try {
            const value = bound ? bound(key) : undefined
            if (typeof value === 'string' && value !== key) return value
          } catch {
            /* fall through to Chinese */
          }
          return ZH[key] ?? key
        }

        /** Re-render slot components when the active locale changes. */
        const useLocaleTick = () => {
          return useSyncExternalStore(
            (listener) => ctx.locale.subscribe(listener),
            () => {
              const snapshot = ctx.locale.getSnapshot()
              return snapshot && typeof snapshot === 'object' ? snapshot.revision ?? snapshot.id ?? 0 : 0
            },
          )
        }

        /* ---- remembered selection --------------------------------------- */
        const prefs = {
          tab: 'map',
          bbox: null,
          admin: '',
          years: '1993,2004,2014,2024',
          season: '45',
          mode: 'plan',
          view: { lat: 35, lon: 105, z: 4 },
          drawMode: 'draw',
        }

        /* ---- Launcher --------------------------------------------------- */
        function Launcher(props) {
          useLocaleTick()
          const [open, setOpen] = useState(false)
          const actions = props && props.inputActions
          const close = () => setOpen(false)
          const dialog = open
            ? h(Picker, { actions: actions ?? null, onClose: close })
            : null
          return h(
            React.Fragment,
            null,
            h('style', null, CSS),
            h(
              'button',
              {
                type: 'button',
                className: 'hx-launch',
                title: tr('launcherTitle'),
                onClick: () => setOpen(true),
                'aria-haspopup': 'dialog',
              },
              h(
                'svg',
                { width: 13, height: 13, viewBox: '0 0 16 16', 'aria-hidden': 'true' },
                h('path', {
                  d: 'M1.5 3.5 5.5 2l5 1.5L14.5 2v10.5L10.5 14l-5-1.5L1.5 14V3.5Z',
                  fill: 'none',
                  stroke: 'currentColor',
                  strokeWidth: 1.3,
                  strokeLinejoin: 'round',
                }),
                h('circle', { cx: 8, cy: 7.4, r: 1.7, fill: 'currentColor' }),
              ),
              h('span', null, tr('launcher')),
            ),
            dialog && createPortal ? createPortal(dialog, document.body) : dialog,
          )
        }

        /* ---- Slippy map ------------------------------------------------- */
        function MapPicker({ bbox, onBbox, view, setView, drawMode, setDrawMode }) {
          const ref = useRef(null)
          const [size, setSize] = useState({ w: 0, h: 0 })
          const [drag, setDrag] = useState(null)
          const [pan, setPan] = useState(null)
          const [tileErrors, setTileErrors] = useState(0)

          useEffect(() => {
            const el = ref.current
            if (!el) return undefined
            const update = () => {
              setSize({ w: el.clientWidth || 0, h: el.clientHeight || 0 })
            }
            update()
            if (typeof ResizeObserver === 'function') {
              const observer = new ResizeObserver(update)
              observer.observe(el)
              return () => observer.disconnect()
            }
            window.addEventListener('resize', update)
            return () => window.removeEventListener('resize', update)
          }, [])

          const z = clamp(view.z, MIN_Z, MAX_Z)
          const tiles = useMemo(() => {
            if (!size.w || !size.h) return []
            const n = Math.pow(2, z)
            const left = lonToX(view.lon, z) - size.w / 2
            const top = latToY(view.lat, z) - size.h / 2
            const x0 = Math.floor(left / TILE)
            const x1 = Math.ceil((left + size.w) / TILE)
            const y0 = clamp(Math.floor(top / TILE), 0, n - 1)
            const y1 = clamp(Math.ceil((top + size.h) / TILE), 0, n - 1)
            const out = []
            for (let ty = y0; ty <= y1; ty++) {
              for (let tx = x0; tx <= x1; tx++) {
                const wrapped = ((tx % n) + n) % n
                out.push({
                  key: `${z}/${wrapped}/${ty}`,
                  url: `https://tile.openstreetmap.org/${z}/${wrapped}/${ty}.png`,
                  left: tx * TILE - left,
                  top: ty * TILE - top,
                })
              }
            }
            return out
          }, [size.w, size.h, view.lat, view.lon, z])

          const rectBox = useMemo(() => {
            if (!bbox || !size.w || !size.h) return null
            const left = lonToX(view.lon, z) - size.w / 2
            const top = latToY(view.lat, z) - size.h / 2
            const x1 = lonToX(bbox[0], z) - left
            const x2 = lonToX(bbox[2], z) - left
            const y1 = latToY(bbox[3], z) - top
            const y2 = latToY(bbox[1], z) - top
            return {
              left: Math.min(x1, x2),
              top: Math.min(y1, y2),
              width: Math.abs(x2 - x1),
              height: Math.abs(y2 - y1),
            }
          }, [bbox, size.w, size.h, view.lat, view.lon, z])

          const dragBox = drag
            ? {
                left: Math.min(drag.x0, drag.x1),
                top: Math.min(drag.y0, drag.y1),
                width: Math.abs(drag.x1 - drag.x0),
                height: Math.abs(drag.y1 - drag.y0),
              }
            : null
          const shown = dragBox || rectBox

          const localPoint = (event) => {
            const rect = ref.current.getBoundingClientRect()
            return { x: event.clientX - rect.left, y: event.clientY - rect.top }
          }

          const onPointerDown = (event) => {
            if (event.button !== 0 || !ref.current) return
            event.preventDefault()
            ref.current.setPointerCapture?.(event.pointerId)
            const point = localPoint(event)
            if (drawMode === 'draw') {
              setDrag({ x0: point.x, y0: point.y, x1: point.x, y1: point.y })
            } else {
              setPan({ sx: event.clientX, sy: event.clientY, lat: view.lat, lon: view.lon })
            }
          }

          const onPointerMove = (event) => {
            if (drag) {
              const point = localPoint(event)
              setDrag((current) => (current ? { ...current, x1: point.x, y1: point.y } : current))
              return
            }
            if (pan) {
              const startX = lonToX(pan.lon, z)
              const startY = latToY(pan.lat, z)
              const nx = startX - (event.clientX - pan.sx)
              const ny = clamp(startY - (event.clientY - pan.sy), 0, worldSize(z))
              setView({ lat: yToLat(ny, z), lon: xToLon(nx, z), z })
            }
          }

          const onPointerUp = (event) => {
            if (drag) {
              const point = localPoint(event)
              const left = lonToX(view.lon, z) - size.w / 2
              const top = latToY(view.lat, z) - size.h / 2
              const box = {
                x0: drag.x0,
                y0: drag.y0,
                x1: point.x,
                y1: point.y,
              }
              if (Math.abs(box.x1 - box.x0) >= 4 && Math.abs(box.y1 - box.y0) >= 4) {
                const lonA = xToLon(left + box.x0, z)
                const lonB = xToLon(left + box.x1, z)
                const latA = yToLat(top + box.y0, z)
                const latB = yToLat(top + box.y1, z)
                onBbox(normalizeBbox(lonA, latA, lonB, latB))
              }
              setDrag(null)
            }
            if (pan) setPan(null)
            try {
              ref.current?.releasePointerCapture?.(event.pointerId)
            } catch {
              /* ignore */
            }
          }

          const zoomBy = (delta) => {
            const next = clamp(z + delta, MIN_Z, MAX_Z)
            if (next !== z) setView({ ...view, z: next })
            setTileErrors(0)
          }

          return h(
            'div',
            null,
            h('div', { className: 'hx-row', style: { justifyContent: 'space-between', marginBottom: 8 } }, [
              h(
                'div',
                { className: 'hx-seg', key: 'modes' },
                h(
                  'button',
                  {
                    type: 'button',
                    className: drawMode === 'draw' ? 'hx-seg-active' : '',
                    onClick: () => setDrawMode('draw'),
                  },
                  tr('drawMode'),
                ),
                h(
                  'button',
                  {
                    type: 'button',
                    className: drawMode === 'pan' ? 'hx-seg-active' : '',
                    onClick: () => setDrawMode('pan'),
                  },
                  tr('panMode'),
                ),
              ),
              h('span', { className: 'hx-muted', key: 'hint' }, tr('mapHint')),
            ]),
            h(
              'div',
              {
                ref,
                className: `hx-map${drawMode === 'pan' ? ' hx-map-pan' : ''}`,
                onPointerDown,
                onPointerMove,
                onPointerUp,
                onPointerCancel: onPointerUp,
              },
              tiles.map((tile) =>
                h('img', {
                  key: tile.key,
                  className: 'hx-tile',
                  src: tile.url,
                  alt: '',
                  draggable: false,
                  loading: 'lazy',
                  style: { left: tile.left, top: tile.top },
                  onError: (event) => {
                    event.currentTarget.style.visibility = 'hidden'
                    setTileErrors((count) => Math.min(count + 1, 5))
                  },
                }),
              ),
              shown && shown.width > 0 && shown.height > 0
                ? h('div', {
                    className: 'hx-rect',
                    style: {
                      left: shown.left,
                      top: shown.top,
                      width: shown.width,
                      height: shown.height,
                    },
                  })
                : null,
              h(
                'div',
                { className: 'hx-maptools' },
                MAP_PRESETS.filter((preset) => preset.bbox).map((preset) =>
                  h(
                    'button',
                    {
                      key: preset.id,
                      type: 'button',
                      className: 'hx-mapbtn',
                      title: preset.label,
                      onClick: () => {
                        setView({ lat: preset.lat, lon: preset.lon, z: preset.z })
                        onBbox(preset.bbox.slice())
                        setTileErrors(0)
                      },
                    },
                    preset.label,
                  ),
                ),
              ),
              h(
                'div',
                { className: 'hx-mapctrl' },
                h('button', { type: 'button', className: 'hx-mapbtn', title: tr('zoomIn'), onClick: () => zoomBy(1) }, '+'),
                h('button', { type: 'button', className: 'hx-mapbtn', title: tr('zoomOut'), onClick: () => zoomBy(-1) }, '−'),
                h(
                  'button',
                  {
                    type: 'button',
                    className: 'hx-mapbtn',
                    title: tr('resetView'),
                    onClick: () => {
                      setView({ lat: 35, lon: 105, z: 4 })
                      onBbox(null)
                      setTileErrors(0)
                    },
                  },
                  '⌂',
                ),
              ),
              h(
                'div',
                { className: 'hx-attrib' },
                h('a', { href: 'https://www.openstreetmap.org/copyright', target: '_blank', rel: 'noreferrer' }, tr('attribution')),
              ),
              tileErrors > 1 ? h('div', { className: 'hx-maperr' }, tr('tileError')) : null,
            ),
          )
        }

        /* ---- picker dialog ---------------------------------------------- */
        function Picker({ actions, onClose }) {
          useLocaleTick()

          const [tab, setTab] = useState(prefs.tab)
          const [bbox, setBbox] = useState(prefs.bbox)
          const [admin, setAdmin] = useState(prefs.admin)
          const [yearsText, setYearsText] = useState(prefs.years)
          const [seasonText, setSeasonText] = useState(prefs.season)
          const [mode, setMode] = useState(prefs.mode)
          const [view, setView] = useState(prefs.view)
          const [drawMode, setDrawMode] = useState(prefs.drawMode)
          const [status, setStatus] = useState({ text: '', kind: '' })
          const textareaRef = useRef(null)
          const closeRef = useRef(null)
          const timerRef = useRef(null)

          useEffect(() => {
            prefs.tab = tab
            prefs.bbox = bbox
            prefs.admin = admin
            prefs.years = yearsText
            prefs.season = seasonText
            prefs.mode = mode
            prefs.view = view
            prefs.drawMode = drawMode
          }, [tab, bbox, admin, yearsText, seasonText, mode, view, drawMode])

          useEffect(() => {
            const onKey = (event) => {
              if (event.key === 'Escape') {
                event.stopPropagation()
                onClose()
              }
            }
            document.addEventListener('keydown', onKey, true)
            closeRef.current?.focus?.()
            return () => document.removeEventListener('keydown', onKey, true)
          }, [onClose])

          useEffect(
            () => () => {
              if (timerRef.current) clearTimeout(timerRef.current)
            },
            [],
          )

          const parsed = parseYears(yearsText)
          const seasonValue = Number(seasonText)
          const seasonValid = Number.isInteger(seasonValue) && seasonValue >= 0 && seasonValue <= 120
          const scopeValid = tab === 'map' ? Boolean(bbox) : admin.trim().length > 0
          const yearsValid = parsed.years.length > 0 && parsed.invalid.length === 0
          const valid = scopeValid && yearsValid && seasonValid

          const validation = !scopeValid
            ? tab === 'map' ? tr('invalidBbox') : tr('invalidAdmin')
            : !yearsValid
              ? tr('invalidYears')
              : !seasonValid
                ? tr('invalidSeason')
                : ''

          const prompt = useMemo(() => {
            if (!valid) return ''
            const scope = tab === 'map'
              ? `bbox ${bbox.map((value) => value.toFixed(4)).join(',')}`
              : `行政区 "${admin.trim()}"`
            const years = parsed.years.join('、')
            const season = seasonValue === 0
              ? '关闭季节锁（--season-window 0）'
              : `同季窗口 ±${seasonValue} 天（--season-window ${seasonValue}）`
            if (mode === 'plan') {
              return `请用 historica 工具规划这个范围：${scope}；目标年份 ${years}；${season}。先 action:"plan"（不下载），把各期实际成像日期、覆盖、云量和流量/耗时区间告诉我。`
            }
            return `请用 historica 工具下载并出图：${scope}；目标年份 ${years}；${season}。直接 action:"download"，完成后用 read_image 检查 series.jpg，并用 present 交付对比图、单期图与技术报告。`
          }, [valid, tab, bbox, admin, parsed.years, seasonValue, mode])

          const close = () => onClose()

          const copy = async () => {
            try {
              await navigator.clipboard.writeText(prompt)
              setStatus({ text: tr('copied'), kind: 'ok' })
            } catch (error) {
              setStatus({ text: tr('insertFallback'), kind: 'err' })
              textareaRef.current?.focus?.()
              textareaRef.current?.select?.()
            }
          }

          const copyFallback = async () => {
            try {
              await navigator.clipboard.writeText(prompt)
              setStatus({ text: tr('insertFallback'), kind: 'err' })
            } catch {
              setStatus({ text: tr('insertFallback'), kind: 'err' })
              textareaRef.current?.focus?.()
              textareaRef.current?.select?.()
            }
          }

          const insertIntoComposer = async () => {
            if (!actions) return false
            if (typeof actions.insertText === 'function') {
              try {
                const span = typeof actions.captureInsertion === 'function' ? actions.captureInsertion() : undefined
                const result = actions.insertText(prompt, span)
                const settled = result && typeof result.then === 'function' ? await result : result
                if (settled !== false) return true
              } catch (error) {
                console.warn('[historica] insertText failed', error)
              }
            }
            if (typeof actions.setDraft === 'function') {
              try {
                actions.setDraft(prompt)
                return true
              } catch (error) {
                console.warn('[historica] setDraft failed', error)
              }
            }
            return false
          }

          const insertOnly = async () => {
            if (await insertIntoComposer()) {
              setStatus({ text: tr('inserted'), kind: 'ok' })
              if (timerRef.current) clearTimeout(timerRef.current)
              timerRef.current = setTimeout(() => onClose(), 1400)
              return
            }
            await copyFallback()
          }

          const send = async () => {
            if (!(await insertIntoComposer())) {
              await copyFallback()
              return
            }
            if (actions && typeof actions.submit === 'function') {
              try {
                actions.submit()
                setStatus({ text: tr('sent'), kind: 'ok' })
                onClose()
                return
              } catch (error) {
                console.warn('[historica] submit failed', error)
              }
            }
            setStatus({ text: tr('inserted'), kind: 'ok' })
            if (timerRef.current) clearTimeout(timerRef.current)
            timerRef.current = setTimeout(() => onClose(), 1400)
          }

          const mapSection = h(
            'div',
            { key: 'map' },
            h(MapPicker, {
              bbox,
              onBbox: setBbox,
              view,
              setView,
              drawMode,
              setDrawMode,
            }),
            h(
              'div',
              { className: 'hx-box', style: { marginTop: 8 } },
              h('div', { className: 'hx-label' }, tr('bboxLabel')),
              bbox
                ? h(
                    'div',
                    { className: 'hx-mono' },
                    bbox.map((value) => value.toFixed(4)).join(','),
                    ' ',
                    h('span', { className: 'hx-muted' }, `（${tr('areaLabel')} ${Math.round(bboxAreaKm2(bbox)).toLocaleString()} km²）`),
                  )
                : h('div', { className: 'hx-muted' }, tr('bboxEmpty')),
            ),
          )

          const adminSection = h(
            'div',
            { key: 'admin' },
            h('div', { className: 'hx-label' }, tr('adminLabel')),
            h('input', {
              className: 'hx-input',
              value: admin,
              placeholder: tr('adminPlaceholder'),
              onChange: (event) => setAdmin(event.target.value),
              onKeyDown: (event) => {
                if (event.key === 'Enter' && valid) insert()
              },
              'aria-label': tr('adminLabel'),
            }),
            h('div', { className: 'hx-row', style: { marginTop: 8 } }, ADMIN_PRESETS.map((name) =>
              h(
                'button',
                {
                  key: name,
                  type: 'button',
                  className: `hx-chip${admin === name ? ' hx-chip-active' : ''}`,
                  onClick: () => setAdmin(name),
                },
                name,
              ),
            )),
            h('div', { className: 'hx-muted', style: { marginTop: 8 } }, tr('adminHint')),
          )

          return h(
            React.Fragment,
            null,
            h('style', null, CSS),
            h(
              'div',
              {
                className: 'hx-backdrop',
                onMouseDown: (event) => {
                  if (event.target === event.currentTarget) close()
                },
              },
              h(
                'div',
                {
                  className: 'hx-panel',
                  role: 'dialog',
                  'aria-modal': 'true',
                  'aria-label': tr('title'),
                },
                h(
                  'div',
                  { className: 'hx-head' },
                  h(
                    'div',
                    null,
                    h('div', { className: 'hx-title' }, tr('title')),
                    h('div', { className: 'hx-sub' }, tr('subtitle')),
                  ),
                  h(
                    'button',
                    {
                      ref: closeRef,
                      type: 'button',
                      className: 'hx-close',
                      title: tr('close'),
                      'aria-label': tr('close'),
                      onClick: close,
                    },
                    '✕',
                  ),
                ),
                h(
                  'div',
                  { className: 'hx-tabs', role: 'tablist' },
                  h(
                    'button',
                    {
                      type: 'button',
                      role: 'tab',
                      'aria-selected': tab === 'map',
                      className: `hx-tab${tab === 'map' ? ' hx-tab-active' : ''}`,
                      onClick: () => setTab('map'),
                    },
                    tr('tabMap'),
                  ),
                  h(
                    'button',
                    {
                      type: 'button',
                      role: 'tab',
                      'aria-selected': tab === 'admin',
                      className: `hx-tab${tab === 'admin' ? ' hx-tab-active' : ''}`,
                      onClick: () => setTab('admin'),
                    },
                    tr('tabAdmin'),
                  ),
                ),
                tab === 'map' ? mapSection : adminSection,
                h(
                  'div',
                  { className: 'hx-grid' },
                  h(
                    'div',
                    null,
                    h('div', { className: 'hx-label' }, tr('yearsLabel')),
                    h('input', {
                      className: 'hx-input',
                      value: yearsText,
                      placeholder: '1993,2004,2014,2024',
                      onChange: (event) => setYearsText(event.target.value),
                      'aria-label': tr('yearsLabel'),
                    }),
                    h('div', { className: 'hx-muted' }, tr('yearsHint')),
                  ),
                  h(
                    'div',
                    null,
                    h('div', { className: 'hx-label' }, tr('seasonLabel')),
                    h('input', {
                      className: 'hx-input hx-input-num',
                      type: 'number',
                      min: 0,
                      max: 120,
                      step: 5,
                      value: seasonText,
                      onChange: (event) => setSeasonText(event.target.value),
                      'aria-label': tr('seasonLabel'),
                    }),
                    h('div', { className: 'hx-muted' }, tr('seasonHint')),
                  ),
                ),
                h(
                  'div',
                  { className: 'hx-row' },
                  h('span', { className: 'hx-label', style: { marginBottom: 0 } }, tr('modeLabel')),
                  h(
                    'div',
                    { className: 'hx-seg' },
                    h(
                      'button',
                      {
                        type: 'button',
                        className: mode === 'plan' ? 'hx-seg-active' : '',
                        onClick: () => setMode('plan'),
                      },
                      tr('modePlan'),
                    ),
                    h(
                      'button',
                      {
                        type: 'button',
                        className: mode === 'download' ? 'hx-seg-active' : '',
                        onClick: () => setMode('download'),
                      },
                      tr('modeDownload'),
                    ),
                  ),
                ),
                h(
                  'div',
                  null,
                  h('div', { className: 'hx-label' }, tr('promptLabel')),
                  h('textarea', {
                    ref: textareaRef,
                    className: 'hx-textarea',
                    readOnly: true,
                    rows: 4,
                    value: prompt || validation,
                    onFocus: (event) => event.target.select(),
                    'aria-label': tr('promptLabel'),
                  }),
                  h('div', { className: 'hx-muted' }, tr('promptHint')),
                ),
                h(
                  'div',
                  { className: 'hx-foot' },
                  h(
                    'div',
                    { className: `hx-status${status.kind === 'ok' ? ' hx-status-ok' : status.kind === 'err' ? ' hx-status-err' : ''}` },
                    status.text || (validation ? h('span', { className: 'hx-error' }, validation) : ''),
                  ),
                  h(
                    'div',
                    { className: 'hx-row' },
                    h('button', { type: 'button', className: 'hx-btn', onClick: copy, disabled: !valid }, tr('copy')),
                    h('button', { type: 'button', className: 'hx-btn', onClick: insertOnly, disabled: !valid }, tr('insert')),
                    h(
                      'button',
                      { type: 'button', className: 'hx-btn hx-btn-primary', onClick: send, disabled: !valid },
                      tr('send'),
                    ),
                    h('button', { type: 'button', className: 'hx-btn', onClick: close }, tr('close')),
                  ),
                ),
              ),
            ),
          )
        }

        /* ---- slot registration ------------------------------------------ */
        ctx.slots.inject('conversation.composer.dock', () =>
          ctx.slots.register(
            {
              name: 'conversation.composer.dock',
              id: 'historica-launcher',
              order: 30,
              label: () => tr('launcher'),
            },
            Launcher,
          ),
        )
      },
    }
  },
})
