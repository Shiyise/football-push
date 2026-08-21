# 球讯哨 · GitHub 定时推送版

这个目录专门用来部署到 GitHub：不用自己的电脑常开，GitHub Actions 每 30 分钟自动拉一次英超和德甲赛程，把你有变化的比赛推送到手机通知栏，同时生成一个手机能看的静态赛程页。

## 用到的东西

- GitHub Actions：定时拉取 ESPN 免费接口，对比上一次快照
- ntfy（安卓 / iPhone）或 Bark（iPhone）：把通知推到手机，不需要 Telegram
- PushPlus：走微信接收提醒，国内网络下比 ntfy 更稳
- GitHub Pages：展示赛程、关注球队、最近推送记录
- 数据源是 ESPN 公开接口，不需要 API Key

## 一次部署步骤

1. 在 GitHub 新建一个空仓库（不要勾选自动生成 README）
2. 在电脑上把这个目录推上去：

```bash
cd D:\cdx\football-push-gh
git init
git add .
git commit -m "first commit"
git branch -M main
git remote add origin https://github.com/你的名字/仓库名.git
git push -u origin main
```

3. 打开仓库 Settings → Secrets and variables → Actions，添加以下 Secret（不用 Telegram）：

| Secret 名 | 填什么 |
| --- | --- |
| `NTFY_TOPICS` | ntfy 主题名，例如 `my-epl-2026`；多个主题用逗号或空格分隔 |
| `BARK_KEYS` | Bark 设备密钥（Bark App 里复制），多个用逗号或空格分隔 |
| `WEBHOOK_URLS` | Server酱 / PushPlus 等推送 URL，多个用逗号或空格分隔 |
| `PUSHPLUS_TOKEN` | PushPlus 的 token，从 pushplus.plus 微信登录后复制 |

4. 打开 Settings → Pages，Source 选 “Deploy from a branch”，Branch 选 `main`，目录选 `/ (root)`，Save
5. 打开 Settings → Actions → General，把 Workflow permissions 改成 “Read and write permissions”
6. 到 Actions 页面点 “盯比赛并推送”，再点 “Run workflow”，数据源选 `espn`，第一次运行可以选 `true` 的 “只建快照不发通知”，跑完就能看到页面
7. 手机安装 [ntfy](https://ntfy.sh) 或 Bark，订阅/填入你在 Secret 里写的主题，等下一次定时运行

部署完成后，赛程页地址一般是：

```text
https://你的名字.github.io/仓库名/
```

## 关注哪些球队

编辑仓库根目录的 `config.json`，`followed_teams` 里改成你关注的球队，推送到 GitHub 后下一次自动运行生效：

```json
{
  "followed_teams": [
    {"id": "359", "name": "Arsenal", "league": "EPL"},
    {"id": "364", "name": "Liverpool", "league": "EPL"},
    {"id": "132", "name": "Bayern Munich", "league": "BL1"}
  ]
}
```

球队 id 是 ESPN 的数字 id，不要动 name 也可以；常用参考：

- 英超：Arsenal `359`、Liverpool `364`、Manchester City `382`、Manchester United `360`、Chelsea `363`、Tottenham `367`、Newcastle `361`
- 德甲：Bayern Munich `132`、Borussia Dortmund `124`、Bayer Leverkusen `131`、RB Leipzig `133`、Union Berlin `598`、VfB Stuttgart `167`

## 推送什么，多久一次

- 只在你关注球队的比赛进入开赛前 12 小时窗口时提醒一次
- 同一场比赛不会重复提醒，避免每隔半小时打扰
- 只推送你关注球队的比赛
- 定时每 30 分钟检查一次，想改频率改 `.github/workflows/check-and-push.yml` 里的 `cron`

## 本地调试

```bash
python -m unittest test_github_checker -v
```

想看演示数据生成的页面：

```bash
set SOURCE=mock
set SEND_PUSH=false
python checker.py
```

## 和本地 WebUI 的关系

`D:\cdx\football-push` 是电脑上常开的 WebUI 版本；这个目录是 GitHub 自动运行版。两者用同一份 ESPN 数据，可以同时用，也可以只用 GitHub 版。
