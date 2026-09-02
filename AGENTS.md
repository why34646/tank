# Agent 宪法 — 坦克动荡 (TankTrouble)

## 1. 项目锚点
- 产品：复刻 4399 经典《坦克动荡》的 Windows 桌面 2D 休闲对战游戏，支持人机无尽模式与局域网多人联机。
- V1 范围：固定窗口主界面、随机迷宫（4 大小）、坦克/炮弹/反弹、个人无尽模式、局域网房间（创建/发现/加入/设置/准备/聊天/同步）、对战模式（各自为战/2v2/3v3）、安装/升级/卸载。不含广域网、账号、皮肤、音效、跨平台。
- 技术栈：Python 3.10+ / Pygame（pygame-ce）+ pygame_gui 0.6+ / 标准库 socket（UDP 广播 + TCP）/ JSON 本地存储（%APPDATA%/TankTrouble/）/ PyInstaller + Inno Setup。未经批准禁止引入替代方案。
- 框架：Pygame 游戏循环 + pygame_gui UI；严格遵循其官方用法，禁止绕过场景生命周期或事件循环。

## 2. 不可协商规则
- 绝不破坏 `app/` 现有分层（config / core / game / network / scenes / storage / utils / ai / assets）。
- 未经用户明确批准，绝不新增依赖、框架或架构模式。
- 绝不在基础设施层写业务代码，也绝不在业务层写基础设施代码。
- 绝不用表面补丁掩盖根因问题；先定位根因，再在源头修复。
- 未跑完第 5 节验收标准，绝不宣称任务"完成"。
- 绝不重命名、移动或删除当前任务范围之外的文件。

## 3. 变更协议
- 新增文件前，先确认现有文件已无对应职责。
- 新增依赖前，必须说明：用途、为何现有依赖无法覆盖、许可证（本项目可免费分发，注意 Pygame 为 LGPL）。
- 修改共享/基础设施文件（config、core、network/protocol、scenes/base_scene）前，说明对所有调用方的影响。
- 修改数据模型或网络协议字段前，检查对已存战绩 JSON 与联机消息的兼容性，必要时提供迁移。

## 4. 代码质量标准
- 每个场景 MUST 继承 `app.scenes.base_scene.Scene`，实现 `on_enter / on_exit / handle_event / update / draw`。
- 场景 MUST 通过 `SceneContext` 访问共享资源（screen / gui_manager / settings / logger / event_bus / records_store），禁止新建全局单例或裸 import 全局状态。
- 网络消息 MUST 用 `app.network.protocol.MessageType` 枚举 + `MessageProtocol.wrap()` 构造，禁止魔法字符串 `type`。
- 网络消息收发 MUST 走统一工具类，用 4 字节大端长度头分隔，禁止各模块自行处理粘包。
- 自定义异常 MUST 继承 `app.core.exceptions.TankTroubleError`；错误 MUST 在合适层处理，禁止静默吞掉。
- 魔法数字与魔法字符串 MUST 收敛到 `app/config/settings.py` 或 `app/core/constants.py`；战斗数值 MUST 从 `Settings` 读取，禁止硬编码。
- 本地战绩读写 MUST 统一走 `app/storage/records_store.py`，禁止绕过直接操作 JSON 文件。
- 日志 MUST 用 `app/utils/logger.py` 的 logger，禁止用 `print` 调试。
- 高 DPI 适配只在 `main.py` 入口处理；游戏循环为单线程，禁止从工作线程直接操作 Pygame 对象。

## 5. 验收标准（每个任务）
- `python main.py` 可正常启动，窗口固定 1700×1200 且不可缩放。
- 无新增异常、崩溃或运行时警告。
- 新功能可用一个具体、可复现的手动检查步骤验证（本项目暂无自动化测试套件）。
- 网络相关改动必须用本地双开（两个实例）验证消息收发与同步。
- 受影响文件仅限任务范围内，未改动无关模块。

## 6. 工作方式
- 不确定时先问，不要猜测。
- 任务跨多层时，按「数据 → 逻辑 → 界面」顺序推进。
- 需要重构时，先明确说明并获得批准再执行。
- 本宪法与用户指令冲突时，该任务以用户指令为准，任务结束后本宪法继续生效。

## 7. 测试约定
- Agent 默认**不主动启动游戏做手动 GUI 测试**，将简练的手动测试步骤直接发给用户执行。
- Agent 仅运行「模块级/函数级纯逻辑验证脚本」（不启动 Pygame 窗口、不跑主循环）来验证数据结构、状态机转换、数值计算等逻辑正确性；此类脚本运行完即删除，不保留在仓库中。
- 改动涉及多线程、网络 I/O 或必须真实画面交互才能验证的场景，Agent 必须将操作步骤（进入路径、预期结果）清晰列出，让用户手动跑 `python main.py` 验证。
- 验收确认后才算任务完成。
