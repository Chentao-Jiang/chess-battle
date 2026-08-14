# 中国象棋 AI 对战 — 设计文档

## 概述

一个 Web 应用，让两个 AI 大语言模型分别控制红棋和黑棋进行中国象棋比赛。用户可独立配置双方的 Base URL、模型名称和 API Key。

## 技术栈

- **后端**: Python + FastAPI
- **前端**: 单 HTML 页面 + 原生 JS (SVG 棋盘)
- **AI 通信**: OpenAI 兼容 API 格式

## 架构

```
chess-battle/
├── backend/
│   ├── main.py              # FastAPI 入口 (REST + WebSocket)
│   ├── engine/
│   │   ├── board.py         # 棋盘状态 (9×10 坐标矩阵)
│   │   ├── rules.py         # 规则引擎 (合法性校验)
│   │   └── notation.py      # 中文记谱 ↔ 坐标 转换
│   ├── ai/
│   │   ├── client.py        # 通用 LLM API 客户端
│   │   └── prompt.py        # Prompt 构建器
│   └── game/
│       ├── state.py         # 对局状态机
│       └── controller.py    # 比赛调度器
├── frontend/
│   └── index.html           # 单页面 (棋盘 SVG + 控制面板)
├── requirements.txt
└── README.md
```

## 核心设计决策

### 棋盘表示
- 内部用 JSON 9×10 矩阵，每个格子有棋子标识或空
- 坐标: (col, row) = (0-8, 0-9)
- 红方: row=5-9, 黑方: row=0-4
- 前端展示中文记谱，内部使用坐标计算

### 走法验证
- 内置完整中国象棋规则引擎
- 校验非法走法时，自动把当前合法走法列表发给 AI 要求重选
- 避免无限循环

### AI Prompt
- JSON 结构化棋盘矩阵
- 附带当前所有合法走法列表
- 要求 AI 返回坐标格式 {"from": [c,r], "to": [c,r]}

### 对战控制
- 开始 / 停止按钮
- 速度滑块 (每步等待时间 1s~10s)
- 古典木质棋盘风格

### 数据流
1. 后端获取当前棋盘 JSON
2. 发送给当前轮次的 AI
3. 等待 AI 返回走法
4. 校验合法性 → 非法则重选
5. 更新棋盘 → WebSocket 推送前端 → 切换轮次

## 棋子标识
- 内部统一用单字符: r/R=車/俥, n/N=馬/傌, b/B=象/相, a/A=士/仕, k/K=將/帥, c/C=砲/炮, p/P=卒/兵
- 小写=黑方, 大写=红方
