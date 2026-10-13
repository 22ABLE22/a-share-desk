# 待办 · todo.md

> 更新时间：2026-09-14 开市实测后 + 严格信号/操作卡
> 已完成明细见 [done.md](./done.md)
> 远程仓库：https://github.com/22ABLE22/gupiao （private）
> 本地路径：`E:\Users\Admin\github\gupiao`

---

## 当前基线

| 层 | 状态 |
|---|---|
| 数据/分时/价格线 | 开市实测通过 |
| **严格信号** | 日+周多周期共振；置信&lt;68 不推送 |
| **今日操作卡** | 白话建议 + 情景 if/then，少看K线也能用 |
| **阈值回测** | `/api/backtest` + 界面一键回测 |
| 本地模型 | 可选 Ollama `qwen2.5:3b`；未安装自动规则白话 |
| 前端 | 新增「今日操作」页 |

---

## P0

- [ ] 轮换 GitHub PAT（令牌曾出现在对话中）
- [ ] 浏览器「实时提醒」挂机 + 系统通知授权（见页内说明）
- [ ] 改代码后重启 8765（`start.bat`）

---

## P1 剩余

- [ ] 提醒等级 / 音效
- [ ] 分时 1/5/15 分钟 UI
- [ ] 交易流水、CSV 导入导出
- [ ] 东财恢复后回验 BJ
- [ ] 2027 休市日历
- [ ] 可选：安装 Ollama + `ollama pull qwen2.5:3b` 后勾选「本地模型润色」
- [ ] 日线 120/250 最后一根日期偶发不一致，需再核对源对齐

---

## P2

- [ ] 组合级风控与仓位优化
- [ ] 主题切换、桌面壳

---

## 关键路径

1. 虚拟环境 `.venv\Scripts\python.exe`
2. 服务 `http://127.0.0.1:8765`，改后端必须重启
3. 持仓仅本地 `data/portfolio.json`
4. 今日操作 `/api/advice`；回测 `/api/backtest?symbol=...`
5. Watt Toolkit → 仓库 `http.sslBackend=schannel`
