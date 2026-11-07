# 发布指南（dsh-historica）

本目录已经是可发布的 npm 包形态；`../market` 是可以直接上传到静态主机的插件索引。
下面 3 项需要在发布前由你补上：

1. **npm 包名**：默认用 `dsh-historica`（已确认 npm 上未被占用；无 scope，便于通过 dsh.so 的 id 规则）。
2. **GitHub 仓库**：`https://github.com/kue0000/dsh-historica`（已写入 `package.json` 与 `../market/index.json`）。
3. **作者名**：已设为 `Fagu Yu`（`kue0000`）。

## 1. 发布到 npm

```powershell
cd <本目录>
npm login
npm pack --dry-run          # 检查将要发布的文件（应包含 client.js、runtime/historica、LICENSE，不含 runs/cache）
npm publish --access public
npm view dsh-historica version
```

发布后更新版本用 `npm version patch|minor` + 重新 `npm publish`。

## 2. 建 GitHub 仓库并打上 dsh-plugin topic

```powershell
git init
git add .
git commit -m "feat: dsh-historica 1.1.0"
git remote add origin https://github.com/kue0000/dsh-historica.git
git push -u origin master
```

在 GitHub 仓库设置里加 topic **`dsh-plugin`**（dsh.so 会自动索引带该 topic 的仓库）。
仓库创建后补上：

- `package.json` 的 `repository` / `homepage`；
- `../market/index.json` 的 `repository` / `homepage` / `author`。

## 3. 先按“可安装”验证一次

用一个**没有**安装本地开发版（link 安装的旧包名）的 DSH profile 测试，避免 `historica` 工具重复注册：

```powershell
dsh plugin --profile <测试profile> add dsh-historica@1.1.0
```

装好后在会话里让 agent 调 `{"action":"check"}`；若 Python 依赖未装，按插件 `check` 输出的候选路径配置 `pythonPath`。
本地开发版与 npm 版同时存在会冲突（同一个 `historica` 工具只能注册一次），二选一。

## 4. 自建插件索引（可选，官方支持）

`../market/index.json` 已按 format 1 写好，把整个 `market/` 目录发布到任意静态 HTTPS 主机即可，例如 GitHub Pages：

- `index.json` 与 `icons/` 放在同一目录；
- 要求 2xx、UTF-8、不经过重定向、≤128 MiB；
- 用户在 **插件 → 发现 → 添加索引源** 填入 `https://kue0000.github.io/dsh-historica/index.json`。

索引里的 `versions[].source` 是 `{"type":"npm"}`，DSH 会用 `dsh-historica@1.1.0` 安装；也可以用 `{"type":"git","url":"https://github.com/kue0000/dsh-historica","commit":"<40位SHA>","path":"."}`。
可用官方参考生成器从包名列表生成索引，或先把索引加进 DSH，按「管理索引源…」的报错逐条修。

## 5. 提交到 dsh.so（应用内默认市场索引）

打开 <https://www.dsh.so/zh/submit/>，粘贴 GitHub 仓库地址或 npm 包名。检查器会拉取、校验、安全扫描，通过后生成市场条目。

接受标准（本包已满足大部分）：

- DSH 兼容信号：`package.json` 有 `dsh` 字段 ✅
- README 带安装说明 ✅
- SPDX 可识别许可证：MIT ✅（需提交 `LICENSE`）
- 插件 id 符合 `^[a-z0-9]+(-[a-z0-9]+)*$`：仓库名/纯 npm 包名用 `dsh-historica` ✅
- 源码可扫描（JS/TS 等）✅
- 安全扫描无阻断项（注意：插件会 spawn Python 下载数据，确保没有硬编码密钥/混淆/可疑外联）

> dsh.so 是独立社区项目，不是 DeepSeek 官方商店；收录后先标 **Declared**，通过真实兼容性测试后才会标 Verified。
> 官方 DSH 的索引机制见 [插件索引指南](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/guide/plugin-market.zh.md)。

## 6. 发布前检查清单

- [ ] `package.json`：无 `private`，`name` 为 `dsh-historica`，`files` 覆盖运行时文件
- [ ] `cordis.patch.yml` 的 `name` 与包名一致
- [ ] `client.js` 的 `__ModuleLoader__.load({ id: 'dsh-historica' })` 与包名一致
- [ ] `runs/`、`runtime/historica/cache/`、`__pycache__/`、`*.tgz` 不提交
- [ ] `node --check index.js client.js lib/*.js` 全部通过
- [ ] `npm pack --dry-run` 文件清单符合预期
- [ ] 在一个干净 DSH profile 里安装并 `check` 通过
