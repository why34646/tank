"""
坦克动荡 (TankTrouble) - 启动入口
===================================

功能：
1. 高 DPI 适配（ctypes 设置 SetProcessDPIAware）
2. 初始化日志系统
3. 初始化 Pygame 与 pygame_gui
4. 创建场景管理器，加载主菜单场景
5. 进入游戏主循环

运行方式：
    python main.py
"""

import sys
import os
import json
import traceback


# ============================================================
# 1. 高 DPI 适配（必须在 Pygame 初始化前执行）
# ============================================================
def _setup_high_dpi() -> None:
    """Windows 高 DPI 适配，防止窗口/字体模糊。"""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            # 失败不致命，继续运行
            pass


_setup_high_dpi()


# ============================================================
# 2. 确保 app 包可导入（PyInstaller 打包后兼容）
# ============================================================
def _ensure_paths() -> None:
    """将项目根目录加入 sys.path，兼容 PyInstaller --onedir 打包。"""
    if getattr(sys, "frozen", False):
        # PyInstaller 打包运行时
        sys.path.insert(0, sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        # 源码运行：将当前文件所在目录加入 path
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


_ensure_paths()


def _inject_font_path(theme_data: dict, font_path: str) -> None:
    """
    将找到的中文字体路径注入 theme.json 数据的所有 font 配置中。

    pygame_gui 的 font 配置中 regular_path / bold_path 为 null 时，
    会直接用 name 字段调用 pygame.font.Font(name, size)，而 Font()
    只接受文件路径——name 不是路径，导致找不到字体回退到默认字体（不支持中文）。

    解决：对每个 font 节，把 regular_path / bold_path 等都设为
    match_font() 找到的实际文件路径。
    """
    def _patch(font_section: dict) -> None:
        # 对所有四种路径字段，如果为 None 或不存在，都设为字体路径
        for field_name in ("regular_path", "bold_path", "italic_path", "bold_italic_path"):
            if font_section.get(field_name) is None:
                font_section[field_name] = font_path

    # 递归遍历所有包含 font 键的节点
    def _walk(obj: dict) -> None:
        for key, val in obj.items():
            if isinstance(val, dict):
                if key == "font":
                    _patch(val)
                else:
                    _walk(val)

    _walk(theme_data)


# ============================================================
# 3. 主函数
# ============================================================
def main() -> int:
    """游戏主入口。返回 0 表示正常退出，非零表示异常退出。"""
    from app.config.settings import Settings
    from app.utils.logger import setup_logger
    from app.core.event_bus import EventBus
    from app.scenes.scene_manager import SceneManager
    from app.scenes.main_menu_scene import MainMenuScene
    from app.storage.records_store import RecordsStore

    # --- 初始化配置、日志 ---
    settings = Settings.load()
    logger = setup_logger(settings)
    logger.info("=" * 50)
    logger.info(f"坦克动荡 启动 - 版本 v{settings.VERSION}")
    logger.info(f"窗口尺寸: {settings.WINDOW_WIDTH}x{settings.WINDOW_HEIGHT}")
    logger.info("=" * 50)

    try:
        # --- 初始化 Pygame ---
        import pygame
        pygame.init()
        logger.info("Pygame 初始化完成")

        # --- 初始化 mixer + 预加载音效 ---
        try:
            from app.game.sound_manager import SoundManager
            SoundManager.instance().preload(
                base_dir=settings.sound_dir_path(),
                volume=settings.SFX_VOLUME,
            )
            logger.info("音效预加载完成")
        except Exception as _e:
            logger.warning(f"音效初始化失败（不致命）: {_e}")

        # --- 创建窗口（固定大小，不可缩放）---
        # 不使用 RESIZABLE 标志，窗口尺寸即固定
        screen = pygame.display.set_mode(
            (settings.WINDOW_WIDTH, settings.WINDOW_HEIGHT),
        )
        pygame.display.set_caption(f"坦克动荡 v{settings.VERSION}")
        logger.info("游戏窗口创建完成")

        # --- 初始化 pygame_gui 管理器 ---
        # 字体修复：pygame_gui 的 theme.json font.name 会直接传给
        # pygame.font.Font(name, size)，但 Font() 只接受文件路径。
        # 所以用 match_font() 找到中文字体文件路径，写入 regular_path。
        import pygame_gui
        theme_path = settings.theme_file_path()
        font_path = settings.find_chinese_font_path()
        if font_path:
            # 读取主题 → 注入字体路径 → 写临时文件
            with open(theme_path, "r", encoding="utf-8") as f:
                theme_data = json.load(f)
            _inject_font_path(theme_data, font_path)
            theme_path = str(settings.APPDATA_DIR / "theme_resolved.json")
            with open(theme_path, "w", encoding="utf-8") as f:
                json.dump(theme_data, f, ensure_ascii=False, indent=2)
            logger.info(f"中文字体路径注入: {font_path}")
        else:
            logger.warning("未找到系统中文字体，文字可能显示异常")

        gui_manager = pygame_gui.UIManager(
            (settings.WINDOW_WIDTH, settings.WINDOW_HEIGHT),
            theme_path,
        )
        logger.info("pygame_gui 初始化完成")

        # --- 初始化全局基础设施 ---
        event_bus = EventBus()
        records_store = RecordsStore(settings)
        records_store.ensure_file()
        logger.info("本地战绩存储初始化完成")

        # --- 初始化场景管理器并加载主菜单 ---
        scene_manager = SceneManager(
            screen=screen,
            gui_manager=gui_manager,
            settings=settings,
            logger=logger,
            event_bus=event_bus,
            records_store=records_store,
        )
        scene_manager.switch(MainMenuScene)
        logger.info("场景管理器就绪，已加载主菜单场景")

        # --- 主循环（60 FPS）---
        clock = pygame.time.Clock()
        running = True

        while running:
            # 计算 delta time（秒）
            dt = clock.tick(settings.TARGET_FPS) / 1000.0

            # ---- 事件分发 ----
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    logger.info("收到退出事件，开始关闭游戏")
                    running = False
                    break

                # pygame_gui 必须收到所有事件（鼠标、键盘等）才能响应交互
                try:
                    gui_manager.process_events(event)
                except Exception:  # noqa: BLE001
                    logger.warning(f"GUI 事件处理异常: {traceback.format_exc()}")

                # 当前场景处理事件（包括 pygame_gui 产生的 UI_BUTTON_PRESSED 等）
                try:
                    scene_manager.handle_event(event)
                except Exception:  # noqa: BLE001
                    logger.error(f"场景事件处理异常: {traceback.format_exc()}")

            if not running:
                break

            # ---- 更新 ----
            try:
                gui_manager.update(dt)
            except Exception:  # noqa: BLE001
                logger.warning(f"GUI 更新异常: {traceback.format_exc()}")

            try:
                scene_manager.update(dt)
            except Exception:  # noqa: BLE001
                logger.error(f"场景更新异常: {traceback.format_exc()}")

            # ---- 绘制 ----
            try:
                # 清屏（深色背景占位）
                screen.fill(settings.BG_COLOR)
                scene_manager.draw(screen)
                gui_manager.draw_ui(screen)
                scene_manager.post_gui_draw(screen)
                pygame.display.flip()
            except Exception:  # noqa: BLE001
                logger.error(f"场景绘制异常: {traceback.format_exc()}")

        # --- 清理退出 ---
        logger.info("正在关闭场景管理器...")
        scene_manager.shutdown()
        pygame.quit()
        logger.info("游戏正常退出")
        return 0

    except Exception:  # noqa: BLE001
        # 顶层异常捕获，写日志后优雅退出
        error_msg = traceback.format_exc()
        try:
            logger = setup_logger(Settings.load())
        except Exception:  # noqa: BLE001
            logger = None
        if logger:
            logger.critical(f"游戏崩溃:\n{error_msg}")
        else:
            print(f"[FATAL] 游戏崩溃:\n{error_msg}", file=sys.stderr)
        try:
            import pygame
            pygame.quit()
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
