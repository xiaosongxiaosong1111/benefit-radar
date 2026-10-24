<p align="center">
  <img src="docs/assets/cover.svg" alt="权益雷达 Benefit Radar：与你有关的，替你留意着。" width="100%">
</p>

<h1 align="center">权益雷达 <sub>Benefit Radar</sub></h1>

<p align="center">
  从官方政策里，找到与你有关的支持。<br>
  看懂条件，准备材料，把值得办的事一件件办好。
</p>

<p align="center">
  <img alt="Python 3.11 及以上" src="https://img.shields.io/badge/Python-3.11%2B-184e43?style=flat-square&amp;logo=python&amp;logoColor=white">
  <img alt="无生产第三方依赖" src="https://img.shields.io/badge/Runtime_dependencies-0-526c55?style=flat-square">
  <img alt="本地优先" src="https://img.shields.io/badge/Local_first-SQLite-687268?style=flat-square">
  <img alt="当前为天津试点" src="https://img.shields.io/badge/Stage-Tianjin_Pilot-8b6129?style=flat-square">
</p>

<p align="center">
  <a href="#体验流程">体验流程</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="#元宝搜索参与在哪里">元宝搜索</a> ·
  <a href="#验证记录">验证记录</a>
</p>

---

政策公告告诉你发生了什么，申请时还需要知道：**与我有没有关系，要准备什么，接下来去哪办。**

权益雷达把这些步骤放在同一个工作区：先根据已核验的官方资料核对条件，再把材料、办理入口、申请进度和后续变化留在一起。当前提供 **天津试点 · 本机单用户原型**，内置三项政策；尚未实现全国查询或公网多用户服务。

<p align="center">
  <img src="docs/screenshots/01-home.png" alt="实际运行首页：暖白与墨绿界面，展示政策支持内容、条件入口和申请节点" width="100%">
</p>

<p align="center"><sub>实际应用界面 · 桌面与手机共用一套流程</sub></p>

## 体验流程

**了解情况 → 核对条件 → 准备材料 → 跟进申请 → 留意变化。** 每一步都有可查看的官方依据，暂不确定的回答可以保留，填错的条件可以修改。

<table>
  <tr>
    <td width="50%" valign="top">
      <strong>01 / 简单了解你的情况</strong><br>
      <sub>选择当前阶段、毕业年份与院校情况，无需姓名、证件或联系方式。</sub><br><br>
      <img src="docs/screenshots/02-profile.png" alt="过程截图：填写个人阶段等初步情况" width="100%">
    </td>
    <td width="50%" valign="top">
      <strong>02 / 逐项核对，保留不确定</strong><br>
      <sub>进入一项政策后再确认具体条件，已有回答随时可以纠正。</sub><br><br>
      <img src="docs/screenshots/03-condition-check.png" alt="过程截图：在政策详情中回答条件核对问题" width="100%">
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <strong>03 / 看清与你的关系</strong><br>
      <sub>区分可能适用、待补信息、当前不适用与已截止，并说明判断依据。</sub><br><br>
      <img src="docs/screenshots/04-results.png" alt="过程截图：结合已填情况展示政策初步匹配结果" width="100%">
    </td>
    <td width="50%" valign="top">
      <strong>04 / 把材料准备到位</strong><br>
      <sub>基础必备与按情形补充分开，进度只统计基础材料，支持导出 Markdown 清单。</sub><br><br>
      <img src="docs/screenshots/05-materials.png" alt="过程截图：勾选基础材料并查看材料准备进度" width="100%">
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <strong>05 / 下一次，从这里继续</strong><br>
      <sub>关注事项、材料和申请状态持久保存；申请状态与材料准备进度独立展示。</sub><br><br>
      <img src="docs/screenshots/06-follow.png" alt="过程截图：我的跟进工作区，左侧申请清单，右侧政策与期限提醒" width="100%">
    </td>
    <td width="50%" valign="top">
      <strong>06 / 变化出现时，有迹可查</strong><br>
      <sub>检查关注政策的官方原文，展示检查状态；发现变化后提示重新核对。</sub><br><br>
      <img src="docs/screenshots/07-source-check.png" alt="过程截图：手动检查官方原文后的来源检查记录" width="100%">
    </td>
  </tr>
</table>

<details>
<summary><strong>查看手机界面</strong></summary>
<br>
<p align="center"><img src="docs/screenshots/mobile.png" alt="实际手机视口：政策详情与底部办理操作" width="340"></p>

手机采用单列布局，详情保留底部办理操作；选项、关闭与主要操作支持触控和键盘焦点反馈。浏览器视口验证不等同于物理手机实测。

</details>

截图使用虚构示例情况，来自独立运行的真实本地应用，不代表任何真实个人的资格或获批结果。示例中的“可能适用”是申请前核对结果；截图不作为真实元宝调用的证明。

## 快速开始

需要 **Python 3.11+**。应用运行使用 Python 标准库，无需安装前端框架、数据库服务或生产依赖。

```bash
git clone https://github.com/xiaosongxiaosong1111/benefit-radar.git
cd benefit-radar
```

**Windows / PowerShell**

```powershell
.\start_app.ps1
```

**macOS / Linux**

```bash
python3 -B policy_server.py --port 4173
```

打开 **[http://127.0.0.1:4173/](http://127.0.0.1:4173/)**。Windows 可用 `.\start_app.ps1 -Port 4174` 更换端口。

默认未配置搜索密钥时，仍可体验官方资料预览、条件核对、材料清单、申请进度和原文检查。页面会明确标注 **“联网检索未启用”**。

> 当前服务仅监听本机回环地址，适合本地体验与演示。Python 后端需要运行中的进程，仓库上传到 GitHub 或启用静态页面托管不会自动启动后端。

## 元宝搜索参与在哪里

元宝搜索负责 **检索天津官方公开来源、发现相关资料线索**。资格初筛使用已整理并核验的三项政策规则；新检索结果先作为“待核对”资料，不会直接改写申请条件或认定申请资格。

<p align="center">
  <img src="docs/assets/workflow.svg" alt="元宝搜索与本地规则链路：公开政策关键词进入 SearchPro，官方来源进入原文核对与资料摘录，本地规则生成条件与材料清单" width="100%">
</p>

| 环节 | 当前实现 |
| --- | --- |
| 搜索输入 | 使用公开政策关键词；不把个人问答发送给搜索服务。当前查询以天津毕业生支持政策为主。 |
| 官方检索 | 调用 SearchPro，限定天津政府官方来源；重复查询使用 5 分钟缓存。 |
| 运行证据 | 界面区分实时、缓存、未配置与失败状态，展示服务返回的检索时间与 `RequestId`。 |
| 原文核对 | 对关注政策保存正文与效力标注的对照记录，变化后显示复核提醒。 |
| 新资料处理 | 按对象、条件、材料、时间和行动摘录原文；缺少依据的字段保持为空。 |
| 最终成果 | 可追溯的条件核对结果、材料清单、办理入口与申请跟进记录。 |

### 启用联网搜索

准备腾讯云元宝搜索服务的有效 API Key 后，在 PowerShell 中运行：

```powershell
.\start_app.ps1 -WithSearch
```

按提示输入密钥。输入内容被遮罩，只注入本次服务进程，不写入源码、浏览器或数据库。也可通过当前进程的 `TENCENTCLOUD_WSA_APIKEY` 环境变量配置。

接口实现对应 [腾讯云 SearchPro 官方文档](https://cloud.tencent.com/document/product/1806/130615)。实际授权、套餐权限和召回效果需要在你自己的账户下验证；本仓库的无密钥演示和自动化测试不代表真实 SearchPro 调用已经通过。

## 天津试点资料

以下是 **2026-10-24 的资料快照**。政策正文、受理时间与院校要求可能变化，申请前应查看最新原文并向经办机构核实。

| 事项 | 资料中的申请或效力节点 | 官方依据 |
| --- | --- | --- |
| 2027 届高校毕业生一次性求职补贴 | 通知申请节点为 2026-10-31；院校可另有具体要求 | [津人社办发〔2026〕37号](https://www.tj.gov.cn/zwgk/zfxxgkzl/fdzdgknr/zdmsxx220316/jy_157359/zcwj_157391/202610/t20261009_7390207.html) |
| 高校毕业生灵活就业社会保险补贴 | 每月 20 日为办理节点；当前依据有效至 2027-12-31 | [津人社办发〔2024〕50号](https://hrss.tj.gov.cn/zhengwugongkai/zhengcezhinan/zxwjnew/202412/t20241224_6813668.html) |
| 天津居住证首次申领 | 当前管理办法于 2026-12-31 废止，后续应核对新依据 | [津政规〔2021〕6号](https://www.tj.gov.cn/zwgk/szfwj/tjsrmzf/202112/t20211231_5768456.html) |

## 提醒、数据与运行范围

| 你关心的事 | 实际行为 |
| --- | --- |
| 提醒怎样产生 | 本地服务每 6 小时检查关注政策，也可手动检查；临近申请截止会生成站内提醒。 |
| 关闭网页之后 | 服务保持运行时仍可积累站内通知；关闭服务后停止监测。页面可见时每 60 秒同步本机状态。 |
| 原文发生变化 | 展示正文差异或效力标注，要求重新核对；不把正文变化直接解释为已确认的资格变化。 |
| 回答存在哪里 | 问答偏好保存在当前浏览器；本机接口临时处理匹配。 |
| 清单存在哪里 | `data/policy.sqlite3` 保存关注、材料、申请状态、站内提醒、原文快照和检索元数据。 |
| 当前边界 | 天津试点、本机单用户；没有全国规则库、微信或邮件推送、公网账户及代提交申请能力。 |

基础材料准备完成与申请已提交、已获批是不同状态。已完成或取消关注的事项不再产生新提醒；来源读取失败会保留上次记录并显示失败状态。

## 验证记录

当前本机验证记录见 [QA.md](QA.md)。

| 验证层 | 已有记录 | 覆盖重点 |
| --- | ---: | --- |
| Python 单元、集成与安全回归 | 65 项通过 | 条件匹配、持久化、来源检查、请求校验与提醒去重 |
| 前端纯逻辑 | 41 项通过 | 材料分组、准备进度、导出与政策阅读顺序 |
| Chrome 浏览器流程 | 102 项断言通过 | 填写、纠错、跟进、保存、导出、异常恢复、键盘焦点与响应式布局 |

Python 当前测得合并语句与分支覆盖率 **89%**。浏览器覆盖 320、360、390、768、1024 和 1440 像素宽度；自动化使用随机端口和临时 SQLite，并从测试子进程环境中剔除搜索密钥，不修改实际用户清单。

<details>
<summary><strong>开发验证命令与目录</strong></summary>

测试依赖与生产运行分开。在 Windows / PowerShell 中：

```powershell
py -m pip install -r requirements-dev.txt
py -B -m coverage run --branch --source=policy_core,policy_catalog,policy_service,policy_server,policy_sources -m unittest discover -s tests -p "test_*.py" -v
py -m coverage report --fail-under=80
node tests/test_frontend_model.cjs
```

浏览器回归另需 Node.js、Playwright 与可用浏览器：

```powershell
node tests/e2e.cjs
```

可用 `PLAYWRIGHT_MODULE` 指向现有 Playwright 模块目录，或用 `CHROME_PATH` 指定 Chrome 可执行文件。Windows 默认使用标准安装路径的 Chrome；其他平台需准备 Playwright 可用的 Chromium。

```text
benefit-radar/
├── policy_catalog.py     # 官方资料目录与申请规则
├── policy_core.py        # 来源校验、指纹与提醒规则
├── policy_sources.py     # SearchPro、官方正文提取与原文摘录
├── policy_service.py     # SQLite、监测、搜索缓存与站内提醒
├── policy_server.py      # 本机 HTTP API 与静态资源
├── static/               # HTML / CSS / JavaScript 交互界面
├── tests/                # 单元、集成与浏览器流程验证
├── docs/                 # README 视觉资产与运行截图
├── start_app.ps1         # Windows 启动与遮罩密钥输入
└── QA.md                 # 验证记录与尚未验证项
```

</details>

---

<p align="center">
  <strong>与你有关的，替你留意着。</strong><br>
  <sub>依据来自官方，核对留在眼前，下一步由你决定。</sub>
</p>

本项目开发使用 AI 辅助。工具提供申请前核对和准备支持，最终解释、受理和资格审核以官方经办机构及最新通知为准。
