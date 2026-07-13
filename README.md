# 视频片段检索工具

本项目是一个本地 Windows 视频片段检索工具：输入影视解说脚本文案，程序会在指定视频文件夹中查找可能对应的片段，并记录所在视频、集数、开始时间、结束时间、证据文本和分数。

当前默认测试素材：

- 视频目录：`E:\影视剧集`
- 文案文件：`D:\ClaudeCode_AI\闯关东\《闯关东》深度解析01：离乡不是选择，而是穷人最后的生路.md`

## 功能

- 递归扫描视频目录，并按 `第1集`、`EP01`、`第1话` 等常见集数格式自然排序
- GUI 会记住上次选择的视频目录和脚本文案路径
- 使用 `ffprobe` 获取视频时长、音轨、字幕轨信息
- 优先使用同名外挂字幕（`.srt/.ass/.ssa/.vtt`）或内嵌文本字幕建立索引
- 无可用文本字幕时可使用 `faster-whisper` 本地转写视频音频
- 将字幕/转写内容切成带时间码的检索块
- 使用 SQLite FTS5 + 中文分词 + 轻量语义相似度进行混合检索
- 将 Markdown 脚本文案拆成多个查询段
- 输出候选片段表，并导出 CSV/JSON
- 选中候选结果后可用 `ffplay` 预览对应时间点
- 选中候选结果后可用 FFmpeg 导出短视频片段
- 可为候选片段生成 JPG 缩略图，并写入剪辑清单
- 提供 PySide6 桌面 GUI 和命令行入口

## 安装依赖

建议使用 Python 3.10+。

```bash
pip install -r requirements.txt
```

还需要安装 FFmpeg，并确保 `ffmpeg` 与 `ffprobe` 在 PATH 中可用：

```bash
ffmpeg -version
ffprobe -version
```

## 快速测试

### 1. 环境检查

```bash
python scripts/check_env.py
```

### 2. 扫描视频目录

```bash
python main.py scan --video-dir "E:\影视剧集"
```

### 3. 先索引第 1 集测试

```bash
python main.py index --video-dir "E:\影视剧集" --episodes 1 --model small
```

> 首次转写会较慢；如果没有 NVIDIA 显卡，建议先用 `tiny` 或 `base` 模型跑通流程。

### 4. 搜索脚本文案

```bash
python main.py search --script "D:\ClaudeCode_AI\闯关东\《闯关东》深度解析01：离乡不是选择，而是穷人最后的生路.md" --top-k 5
```

结果会导出到 `exports/` 目录。

### 5. 导出一个候选片段

```bash
python main.py clip --script "D:\ClaudeCode_AI\闯关东\《闯关东》深度解析01：离乡不是选择，而是穷人最后的生路.md" --top-k 1 --limit 1 --output-dir exports/clips
```

### 6. 启动 GUI

```bash
python main.py gui
```

## 打包 EXE

```bash
python scripts/build_exe.py
```

打包脚本会使用正式 PyInstaller 配置文件：

```text
视频片段检索工具.spec
```

默认使用 PyInstaller onedir 模式，输出目录类似：

```text
dist/视频片段检索工具/视频片段检索工具.exe
```

打包版运行后会在 EXE 所在目录下自动创建运行时目录：

```text
data/
cache/
exports/
logs/
```

其中 `data/settings.json` 会保存 GUI 最近选择的视频目录和脚本文案路径。FFmpeg 不会被打包进 EXE；目标机器仍需确保 `ffmpeg` 与 `ffprobe` 在 PATH 中可用。

## 重要说明

- 本工具默认本地运行，不上传视频和文案。
- 当前 MVP 主要依赖 ASR 转写文本检索。对于“风雪、老屋、饭桌”等纯画面描述，首版命中能力会弱于人物/剧情/对白类查询。
- 已转写和索引的视频会缓存；视频文件、字幕来源或索引参数未变化时不会重复转写/导入。
- 字幕优先级为：同名外挂文本字幕 → 内嵌文本字幕 → Whisper ASR；首版不支持 PGS/VobSub 等图片字幕 OCR，会自动回退 ASR。
- 默认会递归扫描视频目录；如只想扫描第一层，可在 CLI 使用 `--no-recursive`。子目录中的同名外挂字幕也会被字幕优先索引识别。
- GUI 首次启动时会使用通用默认路径；选择视频目录和脚本文案后，会自动保存到 `data/settings.json`，下次启动自动恢复。
