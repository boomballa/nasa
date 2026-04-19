# Claude 协作指引

## 交互语言

- 默认使用**中文**交流
- 代码、命令、文件名保持英文

## 代码风格

- 优先修改现有文件，不随意新建
- 不添加多余注释、类型注解、docstring（除非逻辑不自明）
- 不做超出需求范围的重构或"顺手优化"

## 每次修改后

提供一条符合 [Conventional Commits](https://www.conventionalcommits.org/) 规范的 git 提交信息，**必须使用英文**，格式：

```
<type>(<scope>): <short description in English>
```

常用 type：`feat` / `fix` / `refactor` / `docs` / `chore`

示例：
```
feat(gallery): add --gallery command to generate static HTML gallery
docs(readme): expand installation and usage instructions
chore(git): add .gitignore to exclude data directory
```

## 项目背景

NASA APOD 批量下载器，抓取每日天文图及元数据，支持生成静态 HTML 图库浏览。
