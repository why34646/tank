"""
游戏介绍场景（左右两列）
========================

- 左侧：UISelectionList，一级标题（操作 / 个人游戏 / 联机游戏 / 对战模式 / 道具预告）
- 右侧：UITextBox，点选左侧某标题后切换内容
- 顶部：返回主菜单按钮 + "游戏介绍" 标题
"""

from __future__ import annotations

from typing import Dict, List, Optional

import pygame
import pygame_gui
import pygame_gui.elements.ui_button as ui_button
import pygame_gui.elements.ui_label as ui_label
import pygame_gui.elements.ui_selection_list as ui_selection_list
import pygame_gui.elements.ui_text_box as ui_text_box

from .base_scene import Scene


# ------------------------------------------------------------------
# 介绍文案（html_text 格式：<br> 换行，<b>...</b> 加粗）
# key 与左侧列表项完全一致
# ------------------------------------------------------------------
INTRO_SECTIONS: Dict[str, str] = {

    "操作": """
<b>基础按键</b><br>
· <b>W / ↑</b>　　前进<br>
· <b>S / ↓</b>　　后退<br>
· <b>A / ←</b>　　左转<br>
· <b>D / →</b>　　右转<br>
· <b>Q / 空格</b>　　发射炮弹<br>
<br>
<b>战斗机制</b><br>
· 炮弹在墙之间反弹，可以击穿敌人<br>
· 每颗炮弹有独立 6 秒补弹倒计时<br>
""".strip(),

    "个人游戏": """
<b>进入路径</b><br>
主菜单 → 【个人游戏】 → 进入单机设置页。<br>
<br>
<b>可配置项</b><br>
· <b>地图大小</b>：小 / 中 / 大 / 巨（4 种递进，格子越大视野越远）<br>
· <b>AI 难度</b>：简单 / 普通 / 大师 / 噩梦 <br>
· <b>对战模式</b>：各自为战 / 2v2 / 3v3<br>
· <b>地图生成</b>：原版（隔间砌墙）/ 新版（破壁凿洞）<br>
<br>
<b>出生点</b><br>
每局所有坦克随机出生在地图空地<br>
<br>
<b>对局循环</b><br>
无尽：某一局结束后，自动开始下一局，地图、出生点全部重新生成，累计击杀与存活轮数。
""".strip(),

    "联机游戏": """
<b>进入路径</b><br>
主菜单 → 【联机游戏】→ 输入房名（留空=自动发现）→ 看到房间列表。<br>
<br>
<b>房主流程</b><br>
1) 点【创建房间】→ 等玩家加入 → 设置 AI 难度、对战模式、地图大小、地图生成<br>
2) 所有玩家准备完毕后，房主点【开始游戏】<br>
<br>
<b>客户端流程</b><br>
1) 自动列出局域网内所有正在广播的房间<br>
2) 点【加入】进入房间，选择坦克颜色、队伍，点【准备】<br>
3) 等房主开始游戏 →  自动切到战斗场景<br>
<br>
<b>聊天</b><br>
房间里可以发文字聊天
""".strip(),

    "对战模式": """
<b>各自为战（FFA）</b><br>
· 所有人都是敌人<br>
· 没有队伍概念<br>
· 一局活得最久的获胜<br>
<br>
<b>2v2 / 3v3</b><br>
· 按队伍编号区分：队 A vs 队 B（2v2），或队 A / 队 B / 队 C（3v3）<br>
· AI 只会自动瞄准敌方坦克，不会误伤队友<br>
<br>
<b>队伍分配</b><br>
· 房主创建房间后可以看到总人数，自动按队伍均衡分配<br>
· 联机玩家可以自己在下拉框里选队；如果该队满了就只能选其他队
""".strip(),

    "道具预告": """
<b>即将上线的道具</b><br>
战斗中地图上会随机刷新道具，走到道具旁自动拾取：<br>
<br>
· <b>机关枪</b> —— 短时间超高射速弹幕<br>
· <b>激光</b> —— 高速直线贯穿射线<br>
· <b>穿墙激光</b> —— 无视墙体的致命光束<br>
· <b>火箭弹</b> —— 慢速追踪弹，命中后爆炸<br>
· <b>导弹</b> —— 智能追踪导弹，自动锁定最近目标<br>
· <b>范围爆破弹</b> —— 落地产生大范围爆炸<br>
· <b>地雷</b> —— 放置后对所有踩上去的坦克爆炸<br>
<br>
<b>拾取规则</b><br>
· 谁碰归谁<br>
· 道具球不能被炮弹摧毁，只能靠坦克拾取
""".strip(),
}

# 左侧列表显示顺序（可随时调整）
SECTION_ORDER: List[str] = ["操作", "个人游戏", "联机游戏", "对战模式", "道具预告"]


class IntroductionScene(Scene):
    """游戏介绍页（左右两列）。"""

    def __init__(self) -> None:
        super().__init__()
        self._title: Optional[ui_label.UILabel] = None
        self._back_btn: Optional[ui_button.UIButton] = None
        self._nav_list: Optional[ui_selection_list.UISelectionList] = None
        self._content_box: Optional[ui_text_box.UITextBox] = None

    # -------------------------------------------------
    def on_enter(self) -> None:
        assert self.ctx is not None
        s = self.ctx.settings
        gui = self.ctx.gui_manager
        w, h = s.WINDOW_WIDTH, s.WINDOW_HEIGHT

        # 顶部栏（与 records/achievement 保持一致）
        self._title = ui_label.UILabel(
            relative_rect=pygame.Rect((w // 2 - 200, 60), (400, 60)),
            text="游戏介绍",
            manager=gui,
            object_id="#page_title",
        )
        self._back_btn = ui_button.UIButton(
            relative_rect=pygame.Rect((60, 60), (180, 54)),
            text="← 返回主菜单",
            manager=gui,
        )

        # 布局：top=160 起，bottom=h-40 留边，左侧 280px + 右侧自适应
        content_top = 160
        content_bottom = h - 40
        content_h = content_bottom - content_top

        nav_w = 280
        nav_x = 80
        self._nav_list = ui_selection_list.UISelectionList(
            relative_rect=pygame.Rect((nav_x, content_top), (nav_w, content_h)),
            item_list=SECTION_ORDER,
            manager=gui,
            allow_multi_select=False,
            default_selection=SECTION_ORDER[0],  # 默认选中第一个
        )

        content_x = nav_x + nav_w + 30          # 30px 间距
        content_w = w - content_x - 80          # 右侧留 80px 边距
        self._content_box = ui_text_box.UITextBox(
            html_text=INTRO_SECTIONS[SECTION_ORDER[0]],  # 默认显示第一页内容
            relative_rect=pygame.Rect((content_x, content_top), (content_w, content_h)),
            manager=gui,
            object_id="#intro_text",
        )

    def on_exit(self) -> None:
        for el in [self._title, self._back_btn, self._nav_list, self._content_box]:
            if el is not None:
                try:
                    el.kill()
                except Exception:  # noqa: BLE001
                    pass
        self._title = self._back_btn = self._nav_list = self._content_box = None

    # -------------------------------------------------
    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame_gui.UI_BUTTON_PRESSED:
            assert self.ctx is not None and self.ctx.scene_manager is not None
            if event.ui_element is self._back_btn:
                from .main_menu_scene import MainMenuScene
                self.ctx.scene_manager.switch(MainMenuScene)
            return

        if event.type == pygame_gui.UI_SELECTION_LIST_NEW_SELECTION:
            if event.ui_element is not self._nav_list or self._content_box is None:
                return
            # 获取当前选中项
            selected = self._nav_list.get_single_selection()
            if selected and selected in INTRO_SECTIONS:
                self._content_box.set_text(INTRO_SECTIONS[selected])