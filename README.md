# MiniMax H3 Colab Skill (跨平台 Ref2VA 影片生成技能)

[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-blue.svg)](#)
[![Python](https://img.shields.io/badge/python-3.11+-brightgreen.svg)](#)
[![License](https://img.shields.io/badge/license-Apache%202.0-yellow.svg)](LICENSE)

本儲存庫是專為 **Antigravity**、**Claude Code** 與 **Codex** 等 AI Agent 設計的獨立擴充技能（Skill）。  
使用者無需具備本地高階顯卡，直接將本機的參照圖片（1～9 張）與分鏡提示詞交由 **Google Colab 雲端 A100 GPU** 執行 **MiniMax H3 Ref2VA**（Reference-to-Video with Audio）模型，生成帶有對齊聲音與對話的 4~15 秒短影音，完成後自動下載回本機。

已完整優化並驗證 **Windows (PowerShell)** 與 **Linux / macOS (Bash)** 雙平台原生相容性！

---

## 🌟 核心特點 (Key Features)

1. **多圖參照（1～9 張圖片）**：
   - 順序嚴格對應提示詞中的 `<Picture 1>` 至 `<Picture 9>`，精確鎖定人物、服裝與場景風格。
2. **Ref2VA 影音一體化**：
   - 同時產生電影級畫面、環境音場（Soundscape）、背景音樂與角色對白語音（支援 `<d>[Chinese] 台詞內容...</d>`）。
3. **雲端 Session 複用與批次隊列**：
   - 支援 `jobs.json` 批次清單。同一批次共用同一個 Colab Session 與已載入的模型權重，大幅省去重複載入時間與運算額度。
4. **全自動生命週期管理**：
   - 內建進度追蹤（`progress.json`），完成後自動停止 Colab Session；遇逾時或異常時亦會安全終止遠端運算，防額度浪費。
5. **雙平台原生腳本支援**：
   - Windows 提供 `install.ps1` 與 `run_colab_inference.ps1`。
   - Linux / macOS 提供 `install.sh` 與 `run_colab_inference.sh`。

---

## 🚀 跨電腦快速安裝 (Quick Installation)

在任何一台新電腦上，只需以下指令即可一鍵安裝技能至 Antigravity 與 Codex 目錄：

### Windows (PowerShell)
```powershell
git clone https://github.com/JeffreyHo0520/minimax-h3-colab-skill.git
cd minimax-h3-colab-skill
.\install.ps1
```

### Linux / macOS (Bash)
```bash
git clone https://github.com/JeffreyHo0520/minimax-h3-colab-skill.git
cd minimax-h3-colab-skill
chmod +x install.sh run_colab_inference.sh
./install.sh
```

> **預設安裝目標路徑**：
> - Antigravity: `~/.gemini/config/skills/minimax-h3-colab`
> - Codex: `$CODEX_HOME/skills/minimax-h3-colab` 或 `~/.codex/skills/minimax-h3-colab`

---

## 🔑 前置設定：安裝與授權 Colab CLI

本技能使用 Google 官方 `google-colab-cli`。建議使用 `uv` 安裝：

```bash
uv python install 3.12
uv tool install --python 3.12 google-colab-cli
```

首次使用需完成一次 Google OAuth2 授權並確認帳號額度：

```bash
colab --auth=oauth2 usage
```
終端機會顯示授權網址與驗證碼，依提示在瀏覽器登入具有 Colab Compute Units 的 Google 帳號授權即可。

---

## 🎬 使用方式

### 1. 單支影片生成

#### Windows (PowerShell)
```powershell
.\run_colab_inference.ps1 `
  -Image .\reference1.png,.\reference2.jpg `
  -Prompt .\prompt.txt `
  -Output .\output.mp4
```

#### Linux / macOS (Bash)
```bash
./run_colab_inference.sh \
  --image /path/to/reference1.png \
  --image /path/to/reference2.jpg \
  --prompt /path/to/prompt.txt \
  --output /path/to/output.mp4
```

---

### 2. 批次多支影片生成 (Batch Manifest)

撰寫 `jobs.json` 清單（範例）：

```json
{
  "jobs": [
    {
      "id": "intro",
      "title": "Presenter introduction",
      "reference_images": [
        "C:/assets/host_front.png",
        "C:/assets/host_side.png"
      ],
      "prompt_file": "C:/assets/intro_prompt.txt",
      "duration_seconds": 8,
      "output_name": "host_intro"
    },
    {
      "id": "demo",
      "title": "Action demonstration",
      "reference_images": ["C:/assets/host_front.png"],
      "prompt": "A continuous close-up shot of <Picture 1> demonstrating the UI. <d>[Chinese] 歡迎來到全自動影片創作工作流！</d>",
      "duration_seconds": 12,
      "output_name": "host_demo"
    }
  ]
}
```

執行批次佇列：
```bash
python scripts/runner.py batch \
  --manifest ./jobs.json \
  --gpu A100 \
  --output-dir ./outputs \
  --progress ./outputs/progress.json
```

---

## 🧪 離線測試驗證

儲存庫附帶完整單元測試（使用 Fake Colab CLI 與 Fake FFprobe，不消耗 Colab 運算額度）：

```bash
python -m unittest discover -s tests -v
```

---

## 📄 授權條款 (License)

本專案遵循 [Apache 2.0 License](LICENSE) 條款開放開源使用。
