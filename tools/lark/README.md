# lark-cli — 独立的飞书知识库读取脚本

这是一个**独立的小工具**，专门用于让 Claude / Cowork 读取飞书（Lark）知识库的内容。它不挂在 `dimos` 主依赖里，零外部依赖（仅 Python 标准库），可以单独使用。

目录结构：

    tools/lark/
    ├── lark_cli.py     # 主脚本（urllib 实现）
    ├── .env.example    # 凭证模板，复制为 .env 并填入
    └── README.md       # 你正在看的这份

---

## 1. 在飞书开放平台新建一个独立的「自建应用」

> 这一步必须人工去飞书后台点。完成后你会拿到 **App ID** + **App Secret** 两段字符串。

1. 打开 <https://open.feishu.cn/app>，点 **创建企业自建应用**。
2. 应用名随便填，比如 `dimos-wiki-reader`。
3. 进入应用 → **凭证与基础信息**，复制 `App ID` 和 `App Secret`。
4. 进入 **权限管理**，按需勾选：

   **只读用法**（够用 `wiki-get` / `wiki-tree`）：
   - `wiki:wiki:readonly`        — 读取知识空间
   - `wiki:node:read`            — 读取知识节点（部分租户名字略不同）
   - `docx:document:readonly`    — 读取新版文档（docx）
   - `docs:doc:readonly`         — 读取老版文档（可选）
   - `drive:drive:readonly`      — 读取云空间文件元数据（可选）

   **读写用法**（要跑 `wiki-rw-test`，或后续写入/新建节点）：
   - `wiki:wiki`                 — ⚠ 非 readonly 版本，覆盖创建/删除/移动节点
   - `docx:document`             — 编辑新版文档内容
   - 同时在飞书 wiki 的「成员管理」里，把应用权限从「可阅读」改成「可编辑」或「管理员」
5. 进入 **版本管理与发布** → 创建版本并申请发布。管理员审批通过后权限才生效。
6. **把应用加入目标知识库的成员**：
   - 打开目标 wiki，例如 <https://ycn9htpxsusl.feishu.cn/wiki/YmjJw5BJAi2E3Tkv1INcbAtrnFb>
   - 右上 ⚙️ → **知识库设置 → 成员管理 → 添加成员 → 应用** → 选你刚才那个 app，权限给 **可阅读** 即可。
   - ⚠️ 漏了这一步的话，API 会返回 `code 99991672` 或 `permission denied`。

---

## 2. 把凭证写进 `.env`

```bash
cp tools/lark/.env.example tools/lark/.env
$EDITOR tools/lark/.env
```

填好 `LARK_APP_ID` / `LARK_APP_SECRET`。`tools/lark/.env` 已经默认被 `.gitignore` 忽略（见仓库根 `.gitignore`），不会被提交。

---

## 3. 验证配置

```bash
# (a) 拿到 tenant_access_token，证明 App ID / Secret 正确
python tools/lark/lark_cli.py verify

# (b) 读取你给的那篇 wiki
python tools/lark/lark_cli.py wiki-get \
    https://ycn9htpxsusl.feishu.cn/wiki/YmjJw5BJAi2E3Tkv1INcbAtrnFb

# 只看元信息，不下正文
python tools/lark/lark_cli.py wiki-get YmjJw5BJAi2E3Tkv1INcbAtrnFb --meta

# 列出某个 space 下的子节点
python tools/lark/lark_cli.py wiki-tree <space_id> [parent_node_token]
```

`verify` 成功会打印：

    OK: obtained tenant_access_token (len=44, host=https://open.feishu.cn)

`wiki-get` 成功会先打印节点元信息（title、obj_type、space_id 等），再打印文档正文（仅当 `obj_type` 是 `docx` 或 `doc` 时）。

---

## 4. 常见报错速查

| 现象 | 原因 / 修法 |
|------|-------------|
| `code 99991663` `app ticket invalid` | App ID / Secret 输错了 |
| `code 99991672` `permission denied`  | 没把应用加进 wiki 成员；或没申请对应权限 |
| `code 1254301`  `wiki not found`    | node_token 错了，或不在你 app 可访问的租户 |
| `obj_type=sheet/bitable` 不下正文 | 当前脚本只下 `docx` / `doc`，电子表格 / 多维表格留 TODO |

---

## 5. 给 Claude / Cowork 用

让 Claude 调用这个脚本就行，例如：

```bash
python tools/lark/lark_cli.py wiki-get <url_or_token> > /tmp/wiki.txt
```

随后让模型从 `/tmp/wiki.txt` 读取做翻译、摘要、检索（RAG）都可以。
