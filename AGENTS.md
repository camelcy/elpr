# 项目规则

## 协作与提交

- 完成任何项目改动后，说明修改内容和验证结果，并提供一条可直接使用的中文 Git 提交消息。
- 除非用户明确要求，否则不要暂存、提交或推送改动；由用户在 VS Code 中自行完成这些操作。

## 开发日记

- 新增开发日记使用 `docs/devlog/YYYY-MM-DD-<topic>.md`，同步到 Obsidian 时保持相同文件名。
- 开发日记改动完成后，提供的中文 Git 提交消息使用“第 N 日开发日记”格式，按项目开发天数填写。

## 测试与打包

- 插件代码修改后如需测试，在测试通过后直接生成对应的可安装或发布产物，不只停留在测试命令：Zotero 插件运行 `scripts/build-zotero-xpi.ps1` 生成 XPI，Obsidian 插件运行 `npm run build` 生成构建产物。
- Obsidian 插件构建成功后，默认运行 `scripts/install.ps1 -VaultPath 'D:\Obsidian\Steins Gate' -SkipBuild -Enable`，安装并启用到 Steins Gate；用户明确要求不安装时除外。
- 打包完成后报告产物路径、版本、验证和安装结果。Zotero XPI 不自动安装；不要自动暂存、提交或推送，除非用户明确要求。
