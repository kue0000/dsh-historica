# 插件索引（可直接托管）

这是 `dsh-historica` 的自建插件索引，格式为 DSH 插件市场 **format 1**。

## 文件

- `index.json` — 索引本体，列出一个 npm 版本（`dsh-historica@1.1.0`）
- `icons/historica.svg` — 插件图标（`index.json` 里的 `icon` 以相对路径引用）

## 托管

把本目录整体上传到任意静态 HTTPS 主机（GitHub Pages / GitLab Pages / 对象存储），要求：

- 返回 2xx，UTF-8 JSON；**不能经过重定向**；
- 索引 ≤ 128 MiB；
- 索引与 `icons/` 保持相对位置不变。

例如 GitHub Pages 后索引 URL 为：

```
https://kue0000.github.io/dsh-historica/index.json
```

在 DSH 中打开 **插件 → 发现 → 添加索引源**，填入该 URL 即可浏览和安装。

## 发布前要改的字段

- `plugins[0].author`：Fagu Yu；
- `plugins[0].repository`：`{ "url": "https://github.com/kue0000/dsh-historica" }`；
- `plugins[0].homepage`：`https://github.com/kue0000/dsh-historica#readme`；
- 新版本发布后追加 `versions[]` 条目（精确 SemVer + Unix 秒发布时间戳），并在 `package.json` 里同步 `engines.dsh`。

## 校验

DSH 会在添加索引时严格校验；格式定义见官方
[插件索引指南](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/guide/plugin-market.zh.md)。
索引是第三方内容：标题、描述、安装来源都由索引声明，DSH 只校验格式，不校验声明真伪。
