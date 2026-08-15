# Zotero 批注自动同步到 Obsidian Excalidraw

这是一个完全本地运行的 MVP：Python 服务只读 Zotero 本地 HTTP API、维护增量状态并裁剪图片批注；Obsidian 插件读取本地队列，并通过 Excalidraw Automate API 把内容安全写入画布。运行时不调用外部 AI、云端数据库或在线同步服务，也不写 Zotero SQLite。

## 已实现

- 自动轮询 Zotero 的 `highlight` 与 `image` annotation。
- `Zotero parent item key → Excalidraw canvas path` 稳定映射。
- 无映射批注保留为 `pending`；安装配套 Zotero XPI 后，可从 Zotero 右键或条目详情中的 Excalidraw 区块一键创建、打开并绑定画布，也可按 frontmatter `zotero_key` 创建或打开 Literature 文献卡片。
- Zotero 条目详情会显示画布关联状态和路径；新建空白画布会按统一模板生成单行手写标题，以及“主要工作”“解决问题/重要进展”“优化方向”三个彩色整理区，并以 100% 缩放打开。标题可点击回到对应 Zotero 条目；已有非空画布仍只补充缺失的标题，不改动原布局。
- 文字批注拆成“评论/翻译”和“原文”两个独立文本块，两者统一使用支持中英文的手写字体和 Excalidraw M 号（20）字号；默认按“评论/翻译 → 原文”排列，可在插件设置中切换。
- 图片按 Zotero PDF 坐标、本地 PDF crop box 与页面旋转信息，用 PyMuPDF 在本机裁剪。
- 每个文本块（图片批注则为图片本身）都直接链接到 Zotero，不再生成页码或单独按钮；链接格式为 `zotero://open-pdf/library/items/<attachmentKey>?page=<page>&annotation=<annotationKey>`。
- 文字批注保留位于底层的颜色背景，但背景不锁定、也不与文本编组；图片批注只显示图片本身，不生成背景框。
- annotation key 写入 Excalidraw `customData`，并在服务状态中记录 canvas path、element IDs 与首次 source snapshot。
- 已导入内容不会因再次同步而重新生成；正文、位置和缩放不会被重置。
- Zotero 源内容变化时只增加 `source updated` 提示，不覆盖画布内容。
- 源批注删除时不删除画布元素，只在状态中标记 `source_missing`。
- 画布中人工删除的已导入元素默认不会重建；只有执行“明确重新导入一条批注”才会重新加入队列。
- 画布通过 Excalidraw 插件保存；服务不会直接重写压缩 JSON。

## 目录

```text
D:\elpr
├─ backend\                 Python 同步服务
├─ src\                     Obsidian 插件 TypeScript 源码
├─ dist\main.js             已构建插件
├─ dist\*.xpi               已构建 Zotero 插件
├─ zotero-plugin\           Zotero XPI 源码
├─ data\                    本机映射、状态与日志（不纳入版本控制）
├─ fixtures\                可提交的测试与示例数据
├─ scripts\                 安装、启动、暂停与卸载脚本
├─ tests\                   Python 测试
├─ tests-ts\                插件核心测试
├─ config.json              本机 MVP 配置
├─ config.example.json      配置示例
└─ service.py               服务入口
```

## 安装

环境要求：Windows、Obsidian 桌面版、Excalidraw 插件 2.23.2 或更高版本、Python 3.11+、Node.js 18+、Zotero 正在运行。首次安装 Python 依赖：

```powershell
cd D:\elpr
python -m pip install -r requirements.txt
npm install
npm run build
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Enable
```

然后重载 Obsidian，并确认“第三方插件 → Zotero Excalidraw Sync”已启用。插件默认会自动启动本地服务。

构建并安装 Zotero XPI：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-zotero-xpi.ps1
```

在 Zotero 中打开“工具 → 插件”，从文件安装 `D:\elpr\dist\zotero-excalidraw-canvas-0.1.10.xpi`。安装后重启 Zotero；选中文献或其 PDF 附件，右键执行“创建/打开 Excalidraw 画布”，或在条目详情的“Excalidraw 画布”区块点击画布或文献卡片按钮。Windows 下会直接启动 `D:\Program Files\Obsidian\Obsidian.exe`，不经过外部协议确认框；`.excalidraw.md` 仍由 Excalidraw 插件打开，普通文献卡片则通过返回的 vault 相对路径打开。

## 发布 Zotero 插件

发布由 GitHub Actions 手动触发，不会因普通的代码推送自动创建 Release。准备新版时，先将 `zotero-plugin\manifest.json` 中的 `version` 更新为新版本号并推送到 `main`；然后在 GitHub 仓库的 **Actions → 发布 Zotero 插件 → Run workflow** 中选择 `main` 并运行。

该流程会运行测试、构建 XPI、创建或更新同版本的 GitHub Release、上传安装包，并把带 SHA-256 校验值的 `zotero-updates.json` 提交回 `main`。首次使用前，请确认仓库允许 GitHub Actions 对 `main` 分支写入；若 `main` 受保护，需要为 GitHub Actions 开放写入权限或调整分支保护规则。

## 配置与映射

服务配置在 `D:\elpr\config.json`。本机 MVP 验证完成后，运行配置已经把 `annotationAllowlist` 设为 `[]`，会接收之后新增的所有支持类型批注；历史 4298 条不会被整库回灌。

文献卡片目录由 `literatureFolder` 配置，默认是 `20 - 工作学习/文献/Literature`。创建卡片不会修改 `Literature.base`，Base 依靠 `type: literature`、Markdown 扩展名和目录过滤自动收录。

新文献卡片可按 DOI 从 OpenAlex 读取作者所属机构，并写入 `institutions` YAML 列表。该功能只在首次创建时运行；打开已有卡片不会查询网络或覆盖用户内容。复制 `config\institution_translations.example.json` 为 `config\institution_translations.json` 可维护 ROR ID、OpenAlex ID或英文机构名对应的人工中文名。相关配置见 `config.example.json`：

- `institutionMetadataEnabled` 控制是否启用；设为 `false` 时不发送机构请求。
- `institutionTranslationMode` 支持 `manual_only`、`wikidata_only` 和 `wikidata_then_openai`。
- OpenAlex 与翻译服务密钥只从 `openAlexApiKeyEnv`、`institutionTranslationApiKeyEnv` 指定的环境变量读取。
- DOI 查询与中文翻译结果缓存在 `data\institution_cache.json`。接口不可用、无 DOI 或无机构记录时，卡片仍会创建，并写入“待填写”占位。
- OpenAI-compatible 翻译仅在 base URL、模型、密钥环境变量均配置后启用；否则自动退化为人工覆盖表和 Wikidata。

映射文件是 `D:\elpr\data\paper_canvas_map.json`：

```json
{
  "UHM9ZELW": "Excalidraw/3D imaging of the biphoton spatiotemporal wave packet.excalidraw.md"
}
```

当前 `UHM9ZELW` 已连接到对应真实画布；这项映射只影响之后的新批注，不会把 fixture 中的测试卡片复制到真实画布。需要恢复测试模式时，可参考 `fixtures\paper_canvas_map.fixture.example.json`。也可以打开目标 Excalidraw 画布，执行命令“将当前画布绑定到 Zotero 文献”，输入 Zotero 文献的 parent item key。一个画布可以绑定多篇文献；插件不会根据含糊链接擅自推断。

## 使用

- Zotero 一键建画布：选中文献或 PDF 附件，右键执行“创建/打开 Excalidraw 画布”，或使用条目详情中的“打开对应画布/创建并关联画布”按钮。Obsidian 未运行时会自动启动；画布在当前主标签中打开并进入专注模式，已有 Homepage/Dashboard 分区会暂时隐藏而不会被关闭。执行命令“切换 Zotero 画布专注模式”可恢复原布局。默认保存到 `Excalidraw/Literature`；可在 Obsidian 插件设置的“Zotero 文献画布目录”中修改。
- 自动同步：在插件设置中启用“自动同步”，默认每 10 秒处理一次。
- 手动同步：命令面板执行“立即同步 Zotero 批注”，或点击左侧刷新图标。
- 暂停：关闭插件设置中的“自动同步”；如需同时停止服务，运行 `scripts\stop-service.ps1`。
- 恢复：重新启用“自动同步”；或运行 `scripts\start-service.ps1` 后手动同步。
- 明确重导：命令面板执行“明确重新导入一条批注”，输入 annotation key。该命令不会删除用户保留的旧元素，因此可在确认旧卡片已人工删除后使用。

## 验证

```powershell
cd D:\elpr
npm test
npm run build
```

测试覆盖：页码与 sort index 排序、annotation key 去重、首次 source snapshot、源更新不覆盖、源删除只标记、机构提取/去重/翻译/缓存/异常降级、真实 `KZ9MWXAM` PDF 坐标裁剪，以及 TypeScript 类型检查。服务健康检查：

```powershell
curl.exe http://127.0.0.1:27119/health
```

画布级验证应始终在 `Excalidraw/Zotero Sync MVP Fixture.excalidraw.md` 上进行。确认它能在 Excalidraw 视图正常打开、文字修改/移动/缩放在再次同步后仍保留，再把真实文献绑定到真实画布。

### 本机 MVP 验证结果（2026-08-14）

- TypeScript 类型检查通过；插件核心 3 项测试通过；Python 服务 7 项测试通过。
- `QXYCJNWL` 文字高亮与 `KZ9MWXAM` 图片批注均已导入测试画布；服务状态为 `imported: 2`，待处理队列为空。
- 测试画布关闭后重新打开为真实 Excalidraw view，API 可用，压缩场景可重新解析，共 158 个元素，其中 9 个带同步元数据。
- `QXYCJNWL` 回跳链接为 PDF 物理页 5；`KZ9MWXAM` 的显示页签为 8，但精确回跳使用物理页 17。
- 文字改为“【人工修改验证】…”并把 x 坐标移动到 `6291.671889086064` 后再次同步；关闭并重开画布，文字和坐标均保持，独立链接保持。
- 每个 annotation 只有一组同步卡片；再次同步没有新增重复组。
- 真实参考画布与项目内只读 fixture 的 SHA-256 相同，证明原画布未被测试流程修改。
- 源更新、源删除与未映射 pending 使用状态机自动测试验证；为避免修改真实 Zotero 数据，本轮没有人为删除真实 annotation。

## 日志与状态

- `data\service.log`：只记录 annotation key、类型、状态与错误类型，不记录完整批注正文。
- `data\sync_state.json`：保存 source snapshot、状态、画布路径与 element IDs；这是同步状态，不依赖可编辑正文。
- `GET http://127.0.0.1:27119/state`：只返回汇总计数。

## 故障排查

- “Zotero API unavailable”：确认 Zotero 已启动，并用 `curl.exe --noproxy 127.0.0.1 -H "Zotero-API-Version: 3" http://127.0.0.1:23119/api/users/0/items?itemType=annotation&limit=1` 测试。Windows PowerShell 5 的 `Invoke-RestMethod` 可能误判 Zotero 的 HTTP/1.0 正常关闭，请优先用 curl。
- “Excalidraw Automate API unavailable”：确认 Excalidraw 插件已启用且版本不低于 2.23.2，然后重载 Obsidian。
- 开发期间使用 `plugin:reload` 后 Excalidraw 一直显示“仍在初始化”：这是 Excalidraw 2.23.2 的开发态 view 注册限制；关闭临时占位 tab 并完整重启 Obsidian。正常启动不受影响。
- 图片未生成：检查 PDF enclosure 是否为本地文件、`PyMuPDF` 是否安装、图片目录是否可写，并查看脱敏日志。
- 画布不存在：修正 `paper_canvas_map.json` 中相对于 vault 的路径，或重新执行绑定命令。
- 端口 27119 被占用：修改 `config.json` 的 `listenPort`，同时修改插件设置中的服务地址。

## 卸载

先退出或重载 Obsidian，然后运行：

```powershell
powershell -ExecutionPolicy Bypass -File D:\elpr\scripts\uninstall.ps1
```

默认只移除已安装插件并从启用列表删除，保留源代码、状态、映射、图片和测试画布。若也要删除测试画布，可加 `-RemoveFixture`。卸载脚本不会删除真实画布、Zotero 数据或 `D:\elpr`。

## 已知限制与后续扩展

- Zotero 本地 Connector API 9.0.6 没有实现 `/deleted`，因此服务采用“批量端点优先、定期逐条核验已导入 key”的兼容策略；默认最多每 5 分钟确认一次源删除。
- MVP 在需要写入非当前画布时会临时打开一个后台 Excalidraw tab，保存后立即关闭；若 Obsidian/Excalidraw 的未来版本改变 view API，需要适配。
- 当前运行配置已移除验收白名单，服务从已保存的 Zotero library version 开始增量接收新批注，不会回灌全部历史 4298 条。
- 完整工作流可继续增加：映射管理列表、批量 pending 审核、图片 crop 的更多旋转 PDF 回归 fixture、状态冲突面板、Windows 登录自启动，以及只对特定 Zotero collection 同步。
