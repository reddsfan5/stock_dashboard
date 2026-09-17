#!/usr/bin/env python3
"""Render educational sector-atlas plates (dark navy + cyan) with Pillow."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = ROOT / "scripts" / "services" / "static" / "sector-atlas"

W, H = 1168, 784
BG = (11, 27, 51)          # #0b1b33
BG2 = (16, 38, 70)
CARD = (18, 48, 86)
CARD2 = (24, 58, 102)
CYAN = (64, 196, 255)
CYAN_DIM = (40, 140, 190)
WHITE = (235, 242, 255)
MUTED = (160, 180, 210)
GOLD = (255, 200, 100)
LINE = (50, 110, 160)

FONT_CANDIDATES = [
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
]


def _font(size: int, index: int = 0) -> ImageFont.FreeTypeFont:
    last = None
    for path in FONT_CANDIDATES:
        p = Path(path)
        if not p.exists():
            continue
        try:
            return ImageFont.truetype(str(p), size=size, index=index)
        except Exception as exc:  # noqa: BLE001
            last = exc
            try:
                return ImageFont.truetype(str(p), size=size)
            except Exception as exc2:  # noqa: BLE001
                last = exc2
    raise RuntimeError(f"No Chinese font found: {last}")


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: int) -> list[str]:
    if not text:
        return []
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for ch in para:
            trial = cur + ch
            if draw.textlength(trial, font=font) <= max_w:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = ch
        lines.append(cur or "")
    return lines


def _text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill=WHITE,
    max_w: int | None = None,
    line_gap: int = 6,
    align: str = "left",
) -> int:
    x, y = xy
    lines = _wrap(draw, text, font, max_w) if max_w else text.split("\n")
    for line in lines:
        w = draw.textlength(line, font=font)
        tx = x
        if align == "center":
            tx = x - w / 2
        elif align == "right":
            tx = x - w
        draw.text((tx, y), line, font=font, fill=fill)
        bbox = font.getbbox(line or " ")
        y += (bbox[3] - bbox[1]) + line_gap
    return y


def _rounded(draw: ImageDraw.ImageDraw, box, fill, outline=None, width=1, radius=14):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _header(draw, title: str, subtitle: str, badge: str):
    f_badge = _font(18)
    f_title = _font(36)
    f_sub = _font(20)
    _rounded(draw, (36, 28, 210, 62), CARD2, outline=CYAN_DIM, width=1, radius=10)
    _text(draw, (48, 36), badge, f_badge, fill=CYAN)
    _text(draw, (230, 24), title, f_title, fill=WHITE)
    _text(draw, (230, 68), subtitle, f_sub, fill=MUTED, max_w=880)


def _footer(draw, hint: str):
    f = _font(16)
    draw.line((36, H - 42, W - 36, H - 42), fill=LINE, width=1)
    _text(draw, (36, H - 32), hint, f, fill=MUTED)


def _arrow(draw, x1, y1, x2, y2, color=CYAN_DIM):
    draw.line((x1, y1, x2, y2), fill=color, width=3)
    # simple arrow head
    if abs(x2 - x1) >= abs(y2 - y1):
        direction = 1 if x2 > x1 else -1
        draw.polygon(
            [(x2, y2), (x2 - 10 * direction, y2 - 6), (x2 - 10 * direction, y2 + 6)],
            fill=color,
        )
    else:
        direction = 1 if y2 > y1 else -1
        draw.polygon(
            [(x2, y2), (x2 - 6, y2 - 10 * direction), (x2 + 6, y2 - 10 * direction)],
            fill=color,
        )


def render_flow_plate(
    path: Path,
    *,
    badge: str,
    title: str,
    subtitle: str,
    boxes: Sequence[tuple[str, str]],
    hint: str,
    orientation: str = "horizontal",
):
    """Boxes as (title, body)."""
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    # subtle grid
    for i in range(0, W, 48):
        draw.line((i, 0, i, H), fill=(14, 32, 58), width=1)
    for j in range(0, H, 48):
        draw.line((0, j, W, j), fill=(14, 32, 58), width=1)

    _header(draw, title, subtitle, badge)
    f_h = _font(22)
    f_b = _font(17)
    n = len(boxes)
    top = 120
    bottom = H - 70

    if orientation == "horizontal":
        gap = 16
        usable = W - 72 - gap * (n - 1)
        bw = usable // n
        bh = bottom - top
        xs = []
        for i, (ht, body) in enumerate(boxes):
            x0 = 36 + i * (bw + gap)
            y0 = top
            x1 = x0 + bw
            y1 = y0 + bh
            xs.append((x0, y0, x1, y1))
            _rounded(draw, (x0, y0, x1, y1), CARD, outline=CYAN_DIM, width=2, radius=16)
            # index chip
            _rounded(draw, (x0 + 14, y0 + 14, x0 + 52, y0 + 42), CARD2, outline=CYAN, width=1, radius=8)
            _text(draw, (x0 + 33, y0 + 18), f"{i+1:02d}", _font(16), fill=CYAN, align="center")
            y = _text(draw, (x0 + 14, y0 + 56), ht, f_h, fill=WHITE, max_w=bw - 28)
            _text(draw, (x0 + 14, y + 8), body, f_b, fill=MUTED, max_w=bw - 28, line_gap=5)
            if i > 0:
                px1 = xs[i - 1][2]
                _arrow(draw, px1 + 2, (y0 + y1) // 2, x0 - 2, (y0 + y1) // 2)
    else:
        gap = 14
        usable = bottom - top - gap * (n - 1)
        bh = usable // n
        for i, (ht, body) in enumerate(boxes):
            y0 = top + i * (bh + gap)
            y1 = y0 + bh
            x0, x1 = 36, W - 36
            _rounded(draw, (x0, y0, x1, y1), CARD, outline=CYAN_DIM, width=2, radius=14)
            _rounded(draw, (x0 + 16, y0 + 16, x0 + 56, y0 + 46), CARD2, outline=CYAN, width=1, radius=8)
            _text(draw, (x0 + 36, y0 + 20), f"{i+1:02d}", _font(16), fill=CYAN, align="center")
            _text(draw, (x0 + 72, y0 + 16), ht, f_h, fill=WHITE, max_w=x1 - x0 - 90)
            _text(draw, (x0 + 72, y0 + 48), body, f_b, fill=MUTED, max_w=x1 - x0 - 90, line_gap=4)

    _footer(draw, hint)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=90, optimize=True)


def render_grid_plate(
    path: Path,
    *,
    badge: str,
    title: str,
    subtitle: str,
    boxes: Sequence[tuple[str, str]],
    hint: str,
    cols: int = 3,
):
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    for i in range(0, W, 48):
        draw.line((i, 0, i, H), fill=(14, 32, 58), width=1)
    for j in range(0, H, 48):
        draw.line((0, j, W, j), fill=(14, 32, 58), width=1)
    _header(draw, title, subtitle, badge)
    f_h = _font(22)
    f_b = _font(17)
    n = len(boxes)
    cols = max(1, min(cols, n))
    rows = (n + cols - 1) // cols
    top, bottom = 118, H - 64
    left, right = 36, W - 36
    gap_x, gap_y = 16, 14
    bw = (right - left - gap_x * (cols - 1)) // cols
    bh = (bottom - top - gap_y * (rows - 1)) // rows
    for i, (ht, body) in enumerate(boxes):
        r, c = divmod(i, cols)
        x0 = left + c * (bw + gap_x)
        y0 = top + r * (bh + gap_y)
        x1, y1 = x0 + bw, y0 + bh
        _rounded(draw, (x0, y0, x1, y1), CARD, outline=CYAN_DIM, width=2, radius=14)
        draw.rectangle((x0, y0, x0 + 8, y1), fill=CYAN)
        y = _text(draw, (x0 + 22, y0 + 18), ht, f_h, fill=WHITE, max_w=bw - 36)
        _text(draw, (x0 + 22, y + 6), body, f_b, fill=MUTED, max_w=bw - 36, line_gap=5)
    _footer(draw, hint)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=90, optimize=True)


def render_hub_plate(
    path: Path,
    *,
    badge: str,
    title: str,
    subtitle: str,
    center: tuple[str, str],
    satellites: Sequence[tuple[str, str]],
    hint: str,
):
    """Center card + surrounding satellites."""
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    for i in range(0, W, 48):
        draw.line((i, 0, i, H), fill=(14, 32, 58), width=1)
    for j in range(0, H, 48):
        draw.line((0, j, W, j), fill=(14, 32, 58), width=1)
    _header(draw, title, subtitle, badge)
    f_h = _font(24)
    f_b = _font(17)
    cx, cy = W // 2, H // 2 + 20
    cw, ch = 280, 160
    _rounded(draw, (cx - cw // 2, cy - ch // 2, cx + cw // 2, cy + ch // 2), CARD2, outline=CYAN, width=3, radius=18)
    _text(draw, (cx, cy - 40), center[0], f_h, fill=CYAN, align="center", max_w=cw - 30)
    _text(draw, (cx, cy + 4), center[1], f_b, fill=WHITE, align="center", max_w=cw - 36)

    # place up to 6 satellites around
    positions = [
        (80, 130), (W // 2 - 140, 130), (W - 360, 130),
        (80, H - 250), (W // 2 - 140, H - 250), (W - 360, H - 250),
    ]
    sw, sh = 280, 110
    for i, ((ht, body), (sx, sy)) in enumerate(zip(satellites[:6], positions)):
        _rounded(draw, (sx, sy, sx + sw, sy + sh), CARD, outline=CYAN_DIM, width=2, radius=12)
        _text(draw, (sx + 16, sy + 14), ht, _font(20), fill=WHITE, max_w=sw - 32)
        _text(draw, (sx + 16, sy + 46), body, f_b, fill=MUTED, max_w=sw - 32, line_gap=4)
        # connector to center
        scx, scy = sx + sw // 2, sy + sh // 2
        draw.line((scx, scy, cx, cy), fill=LINE, width=2)

    _footer(draw, hint)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=90, optimize=True)


# ---------------------------------------------------------------------------
# Per-sector plate specs (4 chapters each)
# ---------------------------------------------------------------------------
# Per-sector plate specs (five-layer: 国家/社会/个人/股市/产业)
# ---------------------------------------------------------------------------

PLATES: dict[str, list[dict]] = {'robots': [{'file': '01-national.svg',
             'kind': 'hub',
             'badge': '01 · 国家',
             'title': '机器人·国家层',
             'subtitle': '制造升级 · 人口结构 · 核心件供应链',
             'center': ('国家目标', '智能制造\n生产韧性'),
             'satellites': [('产线升级', '节拍质量可复现'),
                            ('劳动力结构', '危险重复工序自动化'),
                            ('核心件安全', '减速器伺服控制器'),
                            ('出口竞争力', '认证与本地化'),
                            ('标准安全', '人机协作规范'),
                            ('产业政策', '技改与园区配套')],
             'hint': '专题配图 · 国家层强调机制而非口号'},
            {'file': '02-society.svg',
             'kind': 'grid',
             'badge': '02 · 社会',
             'title': '机器人·社会层',
             'subtitle': '工厂岗位变化 · 安全生产 · 地方配套',
             'boxes': [('岗位迁移', '示教维保质检'),
                       ('安全生产', '焊接冲压搬运减伤'),
                       ('地方产业', '本体核心件集成'),
                       ('服务场景', '仓储分拣巡检')],
             'hint': '专题配图 · 社会层看就业与安全',
             'cols': 2},
            {'file': '03-personal.svg',
             'kind': 'grid',
             'badge': '03 · 个人',
             'title': '机器人·个人层',
             'subtitle': '协作臂 · 配送 · 家用清洁',
             'boxes': [('工作台协作', '力控示教好维护'),
                       ('园区配送', '电梯调度最后一百米'),
                       ('家用清洁', '续航噪音地图'),
                       ('人形预期', '多数仍处演示小批量')],
             'hint': '专题配图 · 个人层看是否真省事',
             'cols': 2},
            {'file': '04-market.svg',
             'kind': 'flow',
             'badge': '04 · 股市',
             'title': '机器人·股市层',
             'subtitle': '资本开支 → 出货 → 核心件 → 项目确认',
             'boxes': [('下游开支', '汽车3C锂电'), ('本体出货', '量与价格'), ('核心件', '批量与毛利'), ('集成确认', '收入时点')],
             'hint': '专题配图 · 主题热度≠利润兑现',
             'orientation': 'horizontal'},
            {'file': '05-industry.svg',
             'kind': 'flow',
             'badge': '05 · 产业',
             'title': '机器人·产业层',
             'subtitle': '零部件 → 本体 → 集成 → 场景 → 服务',
             'boxes': [('核心零部件', '减速器伺服控制传感'),
                       ('本体制造', '工业协作移动人形'),
                       ('系统集成', '工装视觉软件'),
                       ('场景服务', '应用+运维租赁')],
             'hint': '专题配图 · 对照五层产业链',
             'orientation': 'horizontal'}],
 'ai-compute': [{'file': '01-national.svg',
                 'kind': 'hub',
                 'badge': '01 · 国家',
                 'title': '算力·国家层',
                 'subtitle': '智算底座 · 电力能耗 · 芯片供给',
                 'center': ('算力底座', '训练推理\n基础设施'),
                 'satellites': [('智算布局', '集群互联存储'),
                                ('电力约束', '能耗选址绿电'),
                                ('芯片供应链', '先进封装生态'),
                                ('出口管制', '可得边界'),
                                ('软件生态', '框架与工具链'),
                                ('数字经济', '产业应用')],
                 'hint': '专题配图 · 有卡还要有电与互联'},
                {'file': '02-society.svg',
                 'kind': 'grid',
                 'badge': '02 · 社会',
                 'title': '算力·社会层',
                 'subtitle': '智算中心落地 · 利用率 · 城市负荷',
                 'boxes': [('地方智算', '算力券与租用'),
                           ('运维就业', '网络电力岗位'),
                           ('水电负荷', '冷却与用电'),
                           ('上架率', '空置风险对照')],
                 'hint': '专题配图 · 签约≠已投产',
                 'cols': 2},
                {'file': '03-personal.svg',
                 'kind': 'grid',
                 'badge': '03 · 个人',
                 'title': '算力·个人层',
                 'subtitle': 'AI助手 · 云服务 · 本地加速',
                 'boxes': [('云端推理', '响应与排队'),
                           ('网络瓶颈', '最后一公里'),
                           ('本地GPU/NPU', '购置 vs 按量'),
                           ('隐私选择', '数据在哪算')],
                 'hint': '专题配图 · 体验看调度与网络',
                 'cols': 2},
                {'file': '04-market.svg',
                 'kind': 'flow',
                 'badge': '04 · 股市',
                 'title': '算力·股市层',
                 'subtitle': '资本开支 → 芯片 → 服务器 → 光模块/电力',
                 'boxes': [('资本开支', '云与互联网'), ('芯片供给', '量价'), ('服务器', '出货'), ('光模块电力', '带宽与PUE')],
                 'hint': '专题配图 · 训练与推理节奏不同',
                 'orientation': 'horizontal'},
                {'file': '05-industry.svg',
                 'kind': 'flow',
                 'badge': '05 · 产业',
                 'title': '算力·产业层',
                 'subtitle': '芯片 → 整机 → 集群 → IDC → 云应用',
                 'boxes': [('算力芯片', '密度与生态'),
                           ('整机服务器', '供电散热'),
                           ('集群基建', '互联存储液冷'),
                           ('云与应用', '付费场景')],
                 'hint': '专题配图 · 对照五层产业链',
                 'orientation': 'horizontal'}],
 'semiconductors': [{'file': '01-national.svg',
                     'kind': 'hub',
                     'badge': '01 · 国家',
                     'title': '半导体·国家层',
                     'subtitle': '科技主权 · 设备材料 · 成熟与先进',
                     'center': ('芯片底座', '计算存储\n功率能源'),
                     'satellites': [('先进制程', '设备材料集群'),
                                    ('成熟制程', '汽车工控家电'),
                                    ('出口管制', '约束边界'),
                                    ('工程人才', '工艺与良率'),
                                    ('知识产权', '设计与EDA'),
                                    ('供应链', '硅片特气光刻胶')],
                     'hint': '专题配图 · 先进与成熟权重不同'},
                    {'file': '02-society.svg',
                     'kind': 'grid',
                     'badge': '02 · 社会',
                     'title': '半导体·社会层',
                     'subtitle': '晶圆厂配套 · 高技能就业 · 水电',
                     'boxes': [('超净厂房', '电力超纯水特气'),
                               ('人才留存', '工艺设备工程师'),
                               ('封测材料', '更广就业面'),
                               ('缺芯传导', '交车交机延期')],
                     'hint': '专题配图 · 投资额≠已量产',
                     'cols': 2},
                    {'file': '03-personal.svg',
                     'kind': 'grid',
                     'badge': '03 · 个人',
                     'title': '半导体·个人层',
                     'subtitle': '手机电脑汽车里的芯',
                     'boxes': [('手机SoC', '性能能效发热'),
                               ('PC加速', 'CPU/GPU/NPU'),
                               ('车规芯片', 'MCU功率传感'),
                               ('功率器件', '家电能效可靠')],
                     'hint': '专题配图 · 纳米数字不如体验',
                     'cols': 2},
                    {'file': '04-market.svg',
                     'kind': 'flow',
                     'badge': '04 · 股市',
                     'title': '半导体·股市层',
                     'subtitle': '需求 → 设计 → 代工稼动率 → 设备',
                     'boxes': [('终端需求', '库存周期'), ('设计出货', 'ASP'), ('代工稼动', '价格'), ('设备材料', '资本开支')],
                     'hint': '专题配图 · 设计制造弹性不同',
                     'orientation': 'horizontal'},
                    {'file': '05-industry.svg',
                     'kind': 'flow',
                     'badge': '05 · 产业',
                     'title': '半导体·产业层',
                     'subtitle': '设计 → 制造 → 封测 → 设备材料',
                     'boxes': [('IC设计', '架构与IP'),
                               ('晶圆制造', '前道工艺'),
                               ('封装测试', '后道'),
                               ('设备材料', '工具与耗材')],
                     'hint': '专题配图 · 对照五层产业链',
                     'orientation': 'horizontal'}],
 'photovoltaics': [{'file': '01-national.svg',
                    'kind': 'hub',
                    'badge': '01 · 国家',
                    'title': '光伏·国家层',
                    'subtitle': '能源转型 · 消纳 · 制造出口',
                    'center': ('清洁电力', '低边际燃料\n成本电源'),
                    'satellites': [('双碳路径', '风光协同'),
                                   ('电网消纳', '灵活性与市场'),
                                   ('制造出口', '贸易与碳足迹'),
                                   ('能耗约束', '硅料布局'),
                                   ('电价机制', '市场化交易'),
                                   ('标准规范', '并网与质量')],
                    'hint': '专题配图 · 装机要配消纳'},
                   {'file': '02-society.svg',
                    'kind': 'grid',
                    'badge': '02 · 社会',
                    'title': '光伏·社会层',
                    'subtitle': '屋顶分布式 · 地面电站 · 地方就业',
                    'boxes': [('分布式', '备案并网结算'),
                              ('地面电站', '土地与收益'),
                              ('制造就业', '开工率对照'),
                              ('复合业态', '农光渔光边界')],
                    'hint': '专题配图 · 宣传装机≠已并网',
                    'cols': 2},
                   {'file': '03-personal.svg',
                    'kind': 'grid',
                    'badge': '03 · 个人',
                    'title': '光伏·个人层',
                    'subtitle': '电费 · 户用安装 · 绿电感知',
                    'boxes': [('户用经济性', '电价投资衰减'),
                              ('安装条件', '屋顶荷载并网'),
                              ('阳台光伏', '规范与预期'),
                              ('光储协同', '谷峰与体验')],
                    'hint': '专题配图 · 回本节奏因地而异',
                    'cols': 2},
                   {'file': '04-market.svg',
                    'kind': 'flow',
                    'badge': '04 · 股市',
                    'title': '光伏·股市层',
                    'subtitle': '装机/出口 × 价格 × 开工率',
                    'boxes': [('量', '装机出口'), ('价', '硅料组件'), ('利润率', '毛利开工'), ('环节', '逆变器辅材')],
                    'hint': '专题配图 · 出货高也可能价格战',
                    'orientation': 'horizontal'},
                   {'file': '05-industry.svg',
                    'kind': 'flow',
                    'badge': '05 · 产业',
                    'title': '光伏·产业层',
                    'subtitle': '硅料 → 组件 → 逆变器 → 电站',
                    'boxes': [('硅料硅片', '原料'),
                              ('电池组件', '光电产品'),
                              ('逆变器BOS', '并网系统'),
                              ('电站应用', '发电收益')],
                    'hint': '专题配图 · 光储请对照锂电池专题',
                    'orientation': 'horizontal'}],
 'nev': [{'file': '01-national.svg',
          'kind': 'hub',
          'badge': '01 · 国家',
          'title': '新能源车·国家层',
          'subtitle': '交通电气化 · 产业升级 · 出海',
          'center': ('电动化', '交通能源\n结构变化'),
          'satellites': [('石油替代', '电力负荷形态'),
                         ('供应链', '三电轻量化电子'),
                         ('安全监管', '召回与数据'),
                         ('出海', '关税认证本地化'),
                         ('标准', '补能与互联'),
                         ('工业升级', '整车定义权')],
          'hint': '专题配图 · 补贴叙事已让位于产品力'},
         {'file': '02-society.svg',
          'kind': 'grid',
          'badge': '02 · 社会',
          'title': '新能源车·社会层',
          'subtitle': '充电网络 · 公交电气 · 就业迁移',
          'boxes': [('补能网络', '小区报装占位'), ('公交出租', '车队补能节奏'), ('就业迁移', '三电电子软件'), ('回收责任', '电池与报废')],
          'hint': '专题配图 · 桩数≠补能焦虑消失',
          'cols': 2},
         {'file': '03-personal.svg',
          'kind': 'grid',
          'badge': '03 · 个人',
          'title': '新能源车·个人层',
          'subtitle': '续航充电 · 持有成本 · 智驾边界',
          'boxes': [('续航场景', '冬天高速打折'), ('充电体验', '家桩与快充'), ('持有成本', '保险电费残值'), ('智驾边界', '能力与责任')],
          'hint': '专题配图 · 官方续航≠你的行程',
          'cols': 2},
         {'file': '04-market.svg',
          'kind': 'flow',
          'badge': '04 · 股市',
          'title': '新能源车·股市层',
          'subtitle': '交付 × 均价结构 × 单车利润',
          'boxes': [('交付量', '量'), ('均价结构', '价'), ('单车利润', '利润率'), ('供应链', '零部件智驾')],
          'hint': '专题配图 · 价格战下量增可利润降',
          'orientation': 'horizontal'},
         {'file': '05-industry.svg',
          'kind': 'flow',
          'badge': '05 · 产业',
          'title': '新能源车·产业层',
          'subtitle': '零部件 → 三电 → 整车 → 智驾 → 补能',
          'boxes': [('关键零部件', '底盘热管理'), ('三电系统', '驱电电控'), ('整车制造', '品牌渠道'), ('智驾补能', '软硬与网络')],
          'hint': '专题配图 · 电池细节见锂电池专题',
          'orientation': 'horizontal'}],
 'defense': [{'file': '01-national.svg',
              'kind': 'hub',
              'badge': '01 · 国家',
              'title': '军工·国家层',
              'subtitle': '国防现代化 · 先进制造 · 自主可控',
              'center': ('国防装备', '体系能力\n全寿命保障'),
              'satellites': [('计划节奏', '预算与型号节点'),
                             ('关键分系统', '动力航电材料'),
                             ('供应链', '元器件与复材'),
                             ('军民边界', '转化需合规'),
                             ('信息披露', '公开信息克制'),
                             ('先进制造', '航空船舶电子')],
              'hint': '专题配图 · 只讨论公开机制框架'},
             {'file': '02-society.svg',
              'kind': 'grid',
              'badge': '02 · 社会',
              'title': '军工·社会层',
              'subtitle': '基地就业 · 配套集群 · 保密治理',
              'boxes': [('产业集群', '技能岗位'),
                        ('保密合规', '安全与环保'),
                        ('人才培养', '航空电子装配'),
                        ('项目落地', '开工对照订单')],
              'hint': '专题配图 · 规划≠已兑现',
              'cols': 2},
             {'file': '03-personal.svg',
              'kind': 'grid',
              'badge': '03 · 个人',
              'title': '军工·个人层',
              'subtitle': '公共安全感知 · 谨慎主题情绪',
              'boxes': [('公共服务', '经预算间接参与'),
                        ('技术外溢', '民航材料等'),
                        ('情绪分离', '阅兵≠基本面'),
                        ('信息边界', '回报表与公告')],
              'hint': '专题配图 · 不提供投资建议',
              'cols': 2},
             {'file': '04-market.svg',
              'kind': 'flow',
              'badge': '04 · 股市',
              'title': '军工·股市层',
              'subtitle': '任务环境 → 交付 → 配套 → 后市场',
              'boxes': [('任务预算', '环境'), ('主机交付', '节奏'), ('配套定价', '份额'), ('维修后市场', '保有量')],
              'hint': '专题配图 · 大单≠当年利润',
              'orientation': 'horizontal'},
             {'file': '05-industry.svg',
              'kind': 'flow',
              'badge': '05 · 产业',
              'title': '军工·产业层',
              'subtitle': '材料 → 分系统 → 主机 → 配套 → 维修',
              'boxes': [('基础材料', '合金复材元器件'),
                        ('分系统', '发动机航电'),
                        ('主机总装', '平台交付'),
                        ('维修保障', '航材寿命周期')],
              'hint': '专题配图 · 商业航天见独立专题',
              'orientation': 'horizontal'}],
 'innovative-drugs': [{'file': '01-national.svg',
                       'kind': 'hub',
                       'badge': '01 · 国家',
                       'title': '创新药·国家层',
                       'subtitle': '审评科学 · 支付制度 · 创新激励',
                       'center': ('医药创新', '临床价值\n可及平衡'),
                       'satellites': [('审评审批', '速度与质量'),
                                      ('医保集采', '以价换量路径'),
                                      ('基础研究', '临床资源IP'),
                                      ('全球注册', '多中心与出海'),
                                      ('支付改革', '激励与负担'),
                                      ('产业升级', '从仿制到创新')],
                       'hint': '专题配图 · 不评价具体药品疗效'},
                      {'file': '02-society.svg',
                       'kind': 'grid',
                       'badge': '02 · 社会',
                       'title': '创新药·社会层',
                       'subtitle': '患者可及 · 进院路径 · 园区就业',
                       'boxes': [('进院挂网', '可及卡点'),
                                 ('患者负担', '医保商保援助'),
                                 ('生物园区', '研发生产就业'),
                                 ('诊疗规范', '识别与规范治疗')],
                       'hint': '专题配图 · 获批≠广泛可及',
                       'cols': 2},
                      {'file': '03-personal.svg',
                       'kind': 'grid',
                       'badge': '03 · 个人',
                       'title': '创新药·个人层',
                       'subtitle': '适应症匹配 · 就医路径 · 信息辨别',
                       'boxes': [('医师决策', '适应症与检测'),
                                 ('随访管理', '不良反应'),
                                 ('信息噪声', '说明书与指南'),
                                 ('支付安排', '报销与现金流')],
                       'hint': '专题配图 · 不提供用药建议',
                       'cols': 2},
                      {'file': '04-market.svg',
                       'kind': 'flow',
                       'badge': '04 · 股市',
                       'title': '创新药·股市层',
                       'subtitle': '管线期权 → 获批 → 支付 → 放量',
                       'boxes': [('管线阶段', '临床催化'),
                                 ('获批落地', '适应症'),
                                 ('支付定价', '医保谈判'),
                                 ('销售放量', '费用与利润')],
                       'hint': '专题配图 · 早期更像期权',
                       'orientation': 'horizontal'},
                      {'file': '05-industry.svg',
                       'kind': 'flow',
                       'badge': '05 · 产业',
                       'title': '创新药·产业层',
                       'subtitle': '发现 → 临床 → 生产 → 商业化 → 支付',
                       'boxes': [('早期发现', '分子优化'),
                                 ('临床开发', '安全有效'),
                                 ('生产供应', 'CMC与CXO'),
                                 ('商业化支付', '准入与可及')],
                       'hint': '专题配图 · 对照五层产业链',
                       'orientation': 'horizontal'}],
 'metals': [{'file': '01-national.svg',
             'kind': 'hub',
             'badge': '01 · 国家',
             'title': '金属·国家层',
             'subtitle': '资源安全 · 工业底座 · 绿色约束',
             'center': ('工业原料', '制造建筑\n电力底座'),
             'satellites': [('供给安全', '储量冶炼回收'),
                            ('能耗双控', '冶炼布局'),
                            ('进口依赖', '矿汇运费'),
                            ('贸易工具', '关税收储'),
                            ('新能源相关', '铜铝电网'),
                            ('定价', '国际市场')],
             'hint': '专题配图 · 政策≠自动利润'},
            {'file': '02-society.svg',
             'kind': 'grid',
             'badge': '02 · 社会',
             'title': '金属·社会层',
             'subtitle': '矿区就业 · 环境治理 · 地方财政',
             'boxes': [('就业税收', '价格周期敏感'),
                       ('环境治理', '尾矿废水废气'),
                       ('城市转型', '资源枯竭风险'),
                       ('城市矿山', '规范回收')],
             'hint': '专题配图 · 涨价≠地方必然受益',
             'cols': 2},
            {'file': '03-personal.svg',
             'kind': 'grid',
             'badge': '03 · 个人',
             'title': '金属·个人层',
             'subtitle': '建材家电汽车中的间接成本',
             'boxes': [('建材', '向建筑成本传导'), ('家电汽车', '铜铝用料'), ('线缆空调', '铜价与零售'), ('投资风险', '全球定价波动')],
             'hint': '专题配图 · 间接影响为主',
             'cols': 2},
            {'file': '04-market.svg',
             'kind': 'flow',
             'badge': '04 · 股市',
             'title': '金属·股市层',
             'subtitle': '需求 → 价格 → 成本曲线 → 加工费',
             'boxes': [('终端需求', '建筑汽车电力'), ('价格', '现货期货'), ('成本曲线', '矿品位能源'), ('加工费', '冶炼TC')],
             'hint': '专题配图 · 矿冶炼加工弹性不同',
             'orientation': 'horizontal'},
            {'file': '05-industry.svg',
             'kind': 'flow',
             'badge': '05 · 产业',
             'title': '金属·产业层',
             'subtitle': '开采 → 冶炼 → 加工 → 贸易 → 终端',
             'boxes': [('资源开采', '精矿'), ('冶炼精炼', '金属锭'), ('材加工', '板带箔型材'), ('终端需求', '建筑汽车家电')],
             'hint': '专题配图 · 铜铝钢模板不可混用',
             'orientation': 'horizontal'}],
 'consumer-electronics': [{'file': '01-national.svg',
                           'kind': 'hub',
                           'badge': '01 · 国家',
                           'title': '消费电子·国家层',
                           'subtitle': '出口制造 · 精密制造 · 合规门槛',
                           'center': ('精密制造', '全球供应链\n节点'),
                           'satellites': [('出口布局', '关税与本地组装'),
                                          ('精密能力', '模具自动化'),
                                          ('关键件', '显示存储连接'),
                                          ('回收指令', '海外合规'),
                                          ('标准', '互联与安全'),
                                          ('品牌升级', '附加值')],
                           'hint': '专题配图 · 爆款≠制造能力'},
                          {'file': '02-society.svg',
                           'kind': 'grid',
                           'badge': '02 · 社会',
                           'title': '消费电子·社会层',
                           'subtitle': '电子厂就业 · 以旧换新 · 回收',
                           'boxes': [('制造业就业', '订单波动快'),
                                     ('以旧换新', '节奏与渠道'),
                                     ('电子废弃物', '正规回收'),
                                     ('屏幕时间', '社会讨论')],
                           'hint': '专题配图 · 补贴≠持续需求',
                           'cols': 2},
                          {'file': '03-personal.svg',
                           'kind': 'grid',
                           'badge': '03 · 个人',
                           'title': '消费电子·个人层',
                           'subtitle': '换机 · 生态 · 维修',
                           'boxes': [('换机决策', '性能电池系统年限'),
                                     ('生态配件', '便利与锁定'),
                                     ('维修成本', '配件与数据迁移'),
                                     ('AI功能', '云端订阅隐私')],
                           'hint': '专题配图 · 参数不如日常体验',
                           'cols': 2},
                          {'file': '04-market.svg',
                           'kind': 'flow',
                           'badge': '04 · 股市',
                           'title': '消费电子·股市层',
                           'subtitle': '出货 × ASP × 份额良率',
                           'boxes': [('出货量', '量'),
                                     ('单机价值', 'ASP'),
                                     ('份额良率', '利润'),
                                     ('创新部件', '光学折叠AI')],
                           'hint': '专题配图 · 创新年与平庸年不同',
                           'orientation': 'horizontal'},
                          {'file': '05-industry.svg',
                           'kind': 'flow',
                           'badge': '05 · 产业',
                           'title': '消费电子·产业层',
                           'subtitle': '元器件 → 模组 → 组装 → 品牌',
                           'boxes': [('元器件', '功能与成本'),
                                     ('模组部件', '光学声学结构'),
                                     ('组装制造', 'ODM交付'),
                                     ('品牌整机', '定义与生态')],
                           'hint': '专题配图 · 芯片制造见半导体专题',
                           'orientation': 'horizontal'}],
 'new-consumption': [{'file': '01-national.svg',
                      'kind': 'hub',
                      'badge': '01 · 国家',
                      'title': '新消费·国家层',
                      'subtitle': '内需 · 质量安全 · 品牌升级',
                      'center': ('消费升级', '内需与品质'),
                      'satellites': [('促消费', '工具与收入'),
                                     ('质量安全', '食品化妆品法规'),
                                     ('品牌化', '附加值'),
                                     ('人口结构', '慢变量趋势'),
                                     ('广告合规', '长期门槛'),
                                     ('产业政策', '扩大内需')],
                      'hint': '专题配图 · 政策难指定爆款'},
                     {'file': '02-society.svg',
                      'kind': 'grid',
                      'badge': '02 · 社会',
                      'title': '新消费·社会层',
                      'subtitle': '零售就业 · 地方特产 · 内容治理',
                      'boxes': [('就业波动', '扩张与价格战'),
                                ('特产升级', '县域品牌'),
                                ('直播治理', '虚假宣传'),
                                ('健康趋势', '标签与真实')],
                      'hint': '专题配图 · 爆款≠产业升级完成',
                      'cols': 2},
                     {'file': '03-personal.svg',
                      'kind': 'grid',
                      'badge': '03 · 个人',
                      'title': '新消费·个人层',
                      'subtitle': '性价比 · 复购 · 情绪价值',
                      'boxes': [('功能消费', '痛点是否解决'),
                                ('情绪消费', '设计与表达'),
                                ('直播尝鲜', '退货与信任'),
                                ('会员私域', '优惠与打扰')],
                      'hint': '专题配图 · 达人话术需核对',
                      'cols': 2},
                     {'file': '04-market.svg',
                      'kind': 'flow',
                      'badge': '04 · 股市',
                      'title': '新消费·股市层',
                      'subtitle': '销量 × 吨价 × 费用率 × 库存',
                      'boxes': [('销量', '量'), ('吨价结构', '价'), ('费用率', '投放效率'), ('渠道库存', '质量信号')],
                      'hint': '专题配图 · 费用推高收入要看效率',
                      'orientation': 'horizontal'},
                     {'file': '05-industry.svg',
                      'kind': 'flow',
                      'badge': '05 · 产业',
                      'title': '新消费·产业层',
                      'subtitle': '原料 → 品牌 → 渠道 → 内容 → 用户',
                      'boxes': [('原料包材', '成本品质'),
                                ('品牌制造', '配方品控'),
                                ('渠道分销', '铺货'),
                                ('内容用户', '转化复购')],
                      'hint': '专题配图 · 对照五层产业链',
                      'orientation': 'horizontal'}],
 'real-estate': [{'file': '01-national.svg',
                  'kind': 'hub',
                  'badge': '01 · 国家',
                  'title': '房地产·国家层',
                  'subtitle': '住房制度 · 金融稳定 · 城市分化',
                  'center': ('住房与金融', '防风险\n稳需求'),
                  'satellites': [('土地住房', '供给形成'),
                                 ('金融审慎', '杠杆约束'),
                                 ('保障改造', '供给结构'),
                                 ('城市分化', '人口迁移'),
                                 ('上下游', '建材家电'),
                                 ('地方财政', '土地依赖转型')],
                  'hint': '专题配图 · 不预测房价'},
                 {'file': '02-society.svg',
                  'kind': 'grid',
                  'badge': '02 · 社会',
                  'title': '房地产·社会层',
                  'subtitle': '安居 · 地方财政 · 建筑就业',
                  'boxes': [('居住成本', '青年安居感受'),
                            ('土地财政', '公共投资能力'),
                            ('行业就业', '建筑中介物业'),
                            ('社区品质', '物业与配套')],
                  'hint': '专题配图 · 单城行情不外推全国',
                  'cols': 2},
                 {'file': '03-personal.svg',
                  'kind': 'grid',
                  'badge': '03 · 个人',
                  'title': '房地产·个人层',
                  'subtitle': '买房租房 · 月供 · 交付',
                  'boxes': [('买租选择', '收入利率首付'),
                            ('月供压力', '挤压其他消费'),
                            ('使用价值', '通勤学区质量'),
                            ('交付风险', '预售监管')],
                  'hint': '专题配图 · 不构成购房建议',
                  'cols': 2},
                 {'file': '04-market.svg',
                  'kind': 'flow',
                  'badge': '04 · 股市',
                  'title': '房地产·股市层',
                  'subtitle': '销售回款 → 结算 → 融资债务',
                  'boxes': [('销售回款', '现金'),
                            ('结算利润', '项目利润率'),
                            ('融资债务', '到期与成本'),
                            ('物管叙事', '收缴与关联')],
                  'hint': '专题配图 · 暖风≠回款已改善',
                  'orientation': 'horizontal'},
                 {'file': '05-industry.svg',
                  'kind': 'flow',
                  'badge': '05 · 产业',
                  'title': '房地产·产业层',
                  'subtitle': '土地融资 → 开发 → 销售 → 持有 → 物业',
                  'boxes': [('土地融资', '启动条件'), ('开发建设', '产品交付'), ('销售去化', '回笼'), ('持有物业', '租金与服务')],
                  'hint': '专题配图 · 开发与物管风险不同',
                  'orientation': 'horizontal'}],
 'banks': [{'file': '01-national.svg', 'kind': 'hub', 'badge': '01 · 国家', 'title': '银行·国家层', 'subtitle': '货币政策 · 金融稳定 · 资本监管', 'center': ('银行体系', '信用中介 支付清算'), 'satellites': [('货币传导', '利率与准备金'), ('资本监管', '充足率约束'), ('重点领域', '小微绿色科技'), ('系统重要', '大行稳定'), ('金融稳定', '风险隔离'), ('融资成本', '实体感受')], 'hint': '专题配图 · 政策≠息差已修复'}, {'file': '02-society.svg', 'kind': 'grid', 'badge': '02 · 社会', 'title': '银行·社会层', 'subtitle': '支付 · 小微融资 · 网点就业', 'boxes': [('支付清算', '民生基础设施'), ('小微融资', '可得性与风控'), ('网点数字化', '就业与可及'), ('地方绑定', '区域敞口')], 'hint': '专题配图 · 贷款余额≠融资难消失', 'cols': 2}, {'file': '03-personal.svg', 'kind': 'grid', 'badge': '03 · 个人', 'title': '银行·个人层', 'subtitle': '存款理财 · 房贷 · 支付App', 'boxes': [('存款理财', '利率与风险等级'), ('房贷月供', '利率与规则'), ('支付体验', '安全与便捷'), ('消费信贷', '量入为出')], 'hint': '专题配图 · 不构成理财建议', 'cols': 2}, {'file': '04-market.svg', 'kind': 'flow', 'badge': '04 · 股市', 'title': '银行·股市层', 'subtitle': '规模 × 息差 × 资产质量 × 中间收入', 'boxes': [('规模', '贷款与资产'), ('息差', 'NIM'), ('资产质量', '不良拨备'), ('分红资本', '约束与回报')], 'hint': '专题配图 · 息差与不良常主导', 'orientation': 'horizontal'}, {'file': '05-industry.svg', 'kind': 'flow', 'badge': '05 · 产业', 'title': '银行·产业层', 'subtitle': '负债 → 资产 → 中间 → 风控 → 渠道', 'boxes': [('负债资金', '存款同业'), ('资产投放', '贷款债券'), ('中间业务', '手续费代销'), ('风控渠道', '合规与科技')], 'hint': '专题配图 · 对照五层产业链', 'orientation': 'horizontal'}],
 'securities': [{'file': '01-national.svg', 'kind': 'hub', 'badge': '01 · 国家', 'title': '证券·国家层', 'subtitle': '注册制 · 直接融资 · 监管', 'center': ('资本市场', '融资与定价'), 'satellites': [('制度改革', '注册制再融资'), ('交易制度', '适当性与质量'), ('对外开放', '互联互通'), ('资本约束', '净资本'), ('风险隔离', '功能监管'), ('融资功能', '服务实体')], 'hint': '专题配图 · 改革≠成交额已回升'}, {'file': '02-society.svg', 'kind': 'grid', 'badge': '02 · 社会', 'title': '证券·社会层', 'subtitle': '财富配置 · 信息透明 · 就业', 'boxes': [('财富管理', '适当性'), ('信息披露', '事实与话术'), ('行业周期', '就业与收入'), ('投资者教育', '风险揭示')], 'hint': '专题配图 · 开户数≠投资能力', 'cols': 2}, {'file': '03-personal.svg', 'kind': 'grid', 'badge': '03 · 个人', 'title': '证券·个人层', 'subtitle': '开户 · 佣金 · 两融 · 产品', 'boxes': [('交易通道', '佣金与体验'), ('杠杆业务', '保证金规则'), ('产品代销', '风险等级'), ('账户安全', '底线')], 'hint': '专题配图 · 不构成交易建议', 'cols': 2}, {'file': '04-market.svg', 'kind': 'flow', 'badge': '04 · 股市', 'title': '证券·股市层', 'subtitle': '成交额 × 投行 × 两融 × 资管', 'boxes': [('经纪', '成交额佣金'), ('投行', '融资窗口'), ('信用', '两融利息'), ('资管自营', '费率与波动')], 'hint': '专题配图 · 券商常具市场β', 'orientation': 'horizontal'}, {'file': '05-industry.svg', 'kind': 'flow', 'badge': '05 · 产业', 'title': '证券·产业层', 'subtitle': '投行 → 经纪 → 信用 → 资管 → 研究', 'boxes': [('投行承销', '股债并购'), ('经纪交易', '通道席位'), ('信用业务', '两融质押'), ('资管研究', '财富与卖方')], 'hint': '专题配图 · 对照五层产业链', 'orientation': 'horizontal'}],
 'liquor': [{'file': '01-national.svg', 'kind': 'hub', 'badge': '01 · 国家', 'title': '白酒·国家层', 'subtitle': '消费税 · 粮食 · 市场秩序', 'center': ('白酒产业', '品牌消费'), 'satellites': [('消费税', '财政与价格'), ('粮食原料', '基础约束'), ('市场规范', '打假与广告'), ('营销边界', '未成年人保护'), ('地方经济', '产区就业'), ('消费升级', '结构变化')], 'hint': '专题配图 · 税制讨论≠利润已变'}, {'file': '02-society.svg', 'kind': 'grid', 'badge': '02 · 社会', 'title': '白酒·社会层', 'subtitle': '宴席 · 礼赠 · 理性饮酒', 'boxes': [('宴席场景', '商务婚庆'), ('礼赠属性', '社交货币'), ('年轻偏好', '场景迁移'), ('理性饮酒', '健康倡导')], 'hint': '专题配图 · 场景≠高端放量', 'cols': 2}, {'file': '03-personal.svg', 'kind': 'grid', 'badge': '03 · 个人', 'title': '白酒·个人层', 'subtitle': '选购 · 鉴真 · 适度', 'boxes': [('价格带', '场景与预算'), ('正品渠道', '防伪'), ('收藏营销', '流动性风险'), ('适量饮用', '健康底线')], 'hint': '专题配图 · 不构成消费建议', 'cols': 2}, {'file': '04-market.svg', 'kind': 'flow', 'badge': '04 · 股市', 'title': '白酒·股市层', 'subtitle': '销量 × 结构 × 费用 × 库存', 'boxes': [('销量', '动销开票'), ('结构', '价格带'), ('费用率', '广告渠道'), ('批价库存', '渠道健康')], 'hint': '专题配图 · 批价常是关键观察', 'orientation': 'horizontal'}, {'file': '05-industry.svg', 'kind': 'flow', 'badge': '05 · 产业', 'title': '白酒·产业层', 'subtitle': '酿造 → 品牌 → 宴席 → 直营 → 边界', 'boxes': [('酿造窖藏', '基酒窖池'), ('品牌渠道', '经销终端'), ('宴席礼赠', '场景消费'), ('直营边界', '新零售与健康')], 'hint': '专题配图 · 对照五层产业链', 'orientation': 'horizontal'}],
 'power-utilities': [{'file': '01-national.svg', 'kind': 'hub', 'badge': '01 · 国家', 'title': '电力·国家层', 'subtitle': '能源安全 · 电价 · 系统平衡', 'center': ('电力系统', '保供与转型'), 'satellites': [('保供优先', '系统平衡'), ('电价机制', '市场与监管'), ('新能源接入', '灵活性'), ('碳约束', '结构转型'), ('容量机制', '回报重塑'), ('电网投资', '输配协同')], 'hint': '专题配图 · 改革≠利润已兑现'}, {'file': '02-society.svg', 'kind': 'grid', 'badge': '02 · 社会', 'title': '电力·社会层', 'subtitle': '可靠供电 · 电价可承受 · 就业', 'boxes': [('供电可靠', '民生底线'), ('电价结构', '公共政策'), ('转型就业', '地方调整'), ('用户侧', '分布式')], 'hint': '专题配图 · 装机≠供电无忧', 'cols': 2}, {'file': '03-personal.svg', 'kind': 'grid', 'badge': '03 · 个人', 'title': '电力·个人层', 'subtitle': '电费 · 峰谷 · 绿电选择', 'boxes': [('电费账单', '阶梯峰谷'), ('季节负荷', '空调节能'), ('绿电屋顶', '算清回报'), ('安全合规', '底线')], 'hint': '专题配图 · 不构成装机建议', 'cols': 2}, {'file': '04-market.svg', 'kind': 'flow', 'badge': '04 · 股市', 'title': '电力·股市层', 'subtitle': '电量 × 电价 − 成本 → 分红', 'boxes': [('上网电量', '利用小时'), ('电价', '市场化'), ('燃料成本', '煤价敏感'), ('分红现金流', '可持续')], 'hint': '专题配图 · 电源类型决定框架', 'orientation': 'horizontal'}, {'file': '05-industry.svg', 'kind': 'flow', 'badge': '05 · 产业', 'title': '电力·产业层', 'subtitle': '电源 → 电网 → 市场 → 综合能源 → 碳', 'boxes': [('电源结构', '水火核风光'), ('电网输配', '调度平衡'), ('市场化', '中长期现货'), ('综合碳约束', '售电与碳成本')], 'hint': '专题配图 · 对照五层产业链', 'orientation': 'horizontal'}],
 'telecom': [{'file': '01-national.svg', 'kind': 'hub', 'badge': '01 · 国家', 'title': '通信·国家层', 'subtitle': '新基建 · 频谱 · 网络主权', 'center': ('通信底座', '连接与安全'), 'satellites': [('频谱牌照', '竞争格局'), ('网络安全', '供应链'), ('算网协同', '东数西算'), ('普遍服务', '覆盖'), ('数据安全', '合规'), ('标准演进', '技术节奏')], 'hint': '专题配图 · 口号≠CAPEX已启动'}, {'file': '02-society.svg', 'kind': 'grid', 'badge': '02 · 社会', 'title': '通信·社会层', 'subtitle': '连接普及 · 防诈 · 就业', 'boxes': [('宽带普及', '数字鸿沟'), ('资费可负担', '公共政策'), ('防诈治理', '公众信任'), ('适老化', '无障碍')], 'hint': '专题配图 · 用户数≠体验满意', 'cols': 2}, {'file': '03-personal.svg', 'kind': 'grid', 'badge': '03 · 个人', 'title': '通信·个人层', 'subtitle': '套餐 · 网速 · 隐私安全', 'boxes': [('套餐合约', '月费体验'), ('信号宽带', '稳定性'), ('携号转网', '选择权'), ('防诈隐私', '底线')], 'hint': '专题配图 · 不构成套餐建议', 'cols': 2}, {'file': '04-market.svg', 'kind': 'flow', 'badge': '04 · 股市', 'title': '通信·股市层', 'subtitle': '用户 × ARPU | 招标 × 份额', 'boxes': [('运营商', 'ARPU与分红'), ('资本开支', '网络投资'), ('设备招标', '订单节奏'), ('新兴业务', '云与政企')], 'hint': '专题配图 · 运营与设备逻辑不同', 'orientation': 'horizontal'}, {'file': '05-industry.svg', 'kind': 'flow', 'badge': '05 · 产业', 'title': '通信·产业层', 'subtitle': '无线 → 传输 → 核心网 → 终端 → 运营', 'boxes': [('无线接入', '基站射频'), ('传输承载', '光传送'), ('核心网云', '云网'), ('终端运营', '物联网与套餐')], 'hint': '专题配图 · 对照五层产业链', 'orientation': 'horizontal'}],
 'internet-platform': [{'file': '01-national.svg', 'kind': 'hub', 'badge': '01 · 国家', 'title': '互联网·国家层', 'subtitle': '平台治理 · 数据 · 公平竞争', 'center': ('平台经济', '连接与治理'), 'satellites': [('反垄断', '竞争秩序'), ('数据算法', '合规成本'), ('内容安全', '未成年人'), ('稳就业', '促消费'), ('出海规则', '数据出境'), ('并购空间', '扩张方式')], 'hint': '专题配图 · 规范≠增长已恢复'}, {'file': '02-society.svg', 'kind': 'grid', 'badge': '02 · 社会', 'title': '互联网·社会层', 'subtitle': '灵活就业 · 商户 · 内容生态', 'boxes': [('灵活就业', '保护与收入'), ('商户佣金', '流量分配'), ('算法分发', '茧房争议'), ('零售形态', '社区商业')], 'hint': '专题配图 · GMV≠商户赚钱', 'cols': 2}, {'file': '03-personal.svg', 'kind': 'grid', 'badge': '03 · 个人', 'title': '互联网·个人层', 'subtitle': '便利 · 隐私 · 数字消费', 'boxes': [('下单履约', '时效体验'), ('隐私广告', '权限打扰'), ('促销会员', '算清成本'), ('未成年人', '时长管理')], 'hint': '专题配图 · 不构成消费建议', 'cols': 2}, {'file': '04-market.svg', 'kind': 'flow', 'badge': '04 · 股市', 'title': '互联网·股市层', 'subtitle': '用户/交易 × 变现 − 费用', 'boxes': [('用户时长', '流量基础'), ('变现率', '广告佣金'), ('补贴费用', '利润质量'), ('监管预期', '估值弹性')], 'hint': '专题配图 · 增长与利润常权衡', 'orientation': 'horizontal'}, {'file': '05-industry.svg', 'kind': 'flow', 'badge': '05 · 产业', 'title': '互联网·产业层', 'subtitle': '流量 → 交易 → 履约 → 广告 → 合规', 'boxes': [('流量入口', '获客留存'), ('匹配交易', '撮合'), ('履约生态', '物流骑手'), ('广告合规', '营销与治理')], 'hint': '专题配图 · 对照五层产业链', 'orientation': 'horizontal'}],
 'shipping-logistics': [{'file': '01-national.svg', 'kind': 'hub', 'badge': '01 · 国家', 'title': '航运物流·国家层', 'subtitle': '外贸通道 · 港口 · 供应链韧性', 'center': ('航运物流', '通道与履约'), 'satellites': [('通道安全', '海峡港口'), ('减排规范', '船队更新'), ('保通保畅', '冲击情景'), ('枢纽布局', '中长期'), ('能源运输', '战略货种'), ('国际规则', '成本')], 'hint': '专题配图 · 政策≠运价已见底'}, {'file': '02-society.svg', 'kind': 'grid', 'badge': '02 · 社会', 'title': '航运物流·社会层', 'subtitle': '物价 · 就业 · 城市配送', 'boxes': [('物流成本', '物价传导'), ('司机快递', '就业保障'), ('末端治理', '噪音包装'), ('冷链医药', '民生安全')], 'hint': '专题配图 · 运价≠快递涨价', 'cols': 2}, {'file': '03-personal.svg', 'kind': 'grid', 'badge': '03 · 个人', 'title': '航运物流·个人层', 'subtitle': '寄递 · 时效 · 面单安全', 'boxes': [('时效理赔', '满意度'), ('品类差异', '生鲜大件'), ('面单隐私', '安全'), ('驿站便利', '最后一公里')], 'hint': '专题配图 · 不构成寄递建议', 'cols': 2}, {'file': '04-market.svg', 'kind': 'flow', 'badge': '04 · 股市', 'title': '航运物流·股市层', 'subtitle': '运价 × 货量 − 成本 | 件量 × 单价', 'boxes': [('运价指数', '即期市场'), ('长约比例', '稳定性'), ('燃油减排', '成本'), ('件量份额', '快递')], 'hint': '专题配图 · 航运与快递逻辑不同', 'orientation': 'horizontal'}, {'file': '05-industry.svg', 'kind': 'flow', 'badge': '05 · 产业', 'title': '航运物流·产业层', 'subtitle': '运力 → 港口 → 干支线 → 仓配 → 数字化', 'boxes': [('运力供给', '船队航线'), ('港口枢纽', '装卸集疏'), ('干线支线', '多式联运'), ('仓配数字', '快递与跟踪')], 'hint': '专题配图 · 对照五层产业链', 'orientation': 'horizontal'}],
 'machinery': [{'file': '01-national.svg', 'kind': 'hub', 'badge': '01 · 国家', 'title': '工程机械·国家层', 'subtitle': '基建投资 · 设备更新 · 高端装备', 'center': ('工程机械', '施工装备'), 'satellites': [('稳投资', '专项债工程'), ('设备更新', '置换需求'), ('自主可控', '液压动力'), ('排放安全', '合规成本'), ('两重两新', '结构方向'), ('时滞', '政策落地')], 'hint': '专题配图 · 政策≠销量已拐点'}, {'file': '02-society.svg', 'kind': 'grid', 'badge': '02 · 社会', 'title': '工程机械·社会层', 'subtitle': '施工就业 · 安全 · 城市更新', 'boxes': [('建筑就业', '收入波动'), ('施工安全', '标准提升'), ('城市更新', '需求结构'), ('扬尘噪音', '市区治理')], 'hint': '专题配图 · 开工≠人人有活', 'cols': 2}, {'file': '03-personal.svg', 'kind': 'grid', 'badge': '03 · 个人', 'title': '工程机械·个人层', 'subtitle': '从业工具 · 市政体感 · 租赁', 'boxes': [('操作维保', '技能升级'), ('市政施工', '出行体感'), ('设备租赁', '中小客户'), ('非直接采购', '消费者少')], 'hint': '专题配图 · 不构成采购建议', 'cols': 2}, {'file': '04-market.svg', 'kind': 'flow', 'badge': '04 · 股市', 'title': '工程机械·股市层', 'subtitle': '销量 × 单价 × 毛利 | 出口', 'boxes': [('销量', '主机出货'), ('开工小时', '使用强度'), ('价格竞争', '利润质量'), ('出口', '外需对冲')], 'hint': '专题配图 · 销量与利润可能背离', 'orientation': 'horizontal'}, {'file': '05-industry.svg', 'kind': 'flow', 'badge': '05 · 产业', 'title': '工程机械·产业层', 'subtitle': '主机 → 零部件 → 后市场 → 电动 → 出海', 'boxes': [('主机整机', '挖起重砼'), ('核心零部件', '液压动力'), ('渠道后市场', '配件租赁'), ('电动出海', '智能与出口')], 'hint': '专题配图 · 对照五层产业链', 'orientation': 'horizontal'}],
 'lithium-battery': [{'file': '01-national.jpg',
                      'kind': 'hub',
                      'badge': '01 · 国家',
                      'title': '锂电池·国家层',
                      'subtitle': '能源安全 · 双碳 · 制造竞争力',
                      'center': ('电化学储能', '电力系统\n灵活性'),
                      'satellites': [('风光消纳', '削峰填谷调频'),
                                     ('交通电气化', '动力电池'),
                                     ('制造出口', '规模成本良率'),
                                     ('战略资源', '锂镍钴加工'),
                                     ('安全标准', '并网与产品'),
                                     ('产业链', '材料到回收')],
                      'hint': '专题配图 · 不引用未经核实统计'},
                     {'file': '02-society.jpg',
                      'kind': 'grid',
                      'badge': '02 · 社会',
                      'title': '锂电池·社会层',
                      'subtitle': '公交电气化 · 社区储能 · 地方产业',
                      'boxes': [('车队电气化', '补能与电池健康'),
                                ('社区储能', '电价与寿命'),
                                ('地方工厂', '就业与环保安全'),
                                ('空气质量', '尾气相关改善叙事')],
                      'hint': '专题配图 · 社会机制可观察',
                      'cols': 2},
                     {'file': '03-personal.jpg',
                      'kind': 'grid',
                      'badge': '03 · 个人',
                      'title': '锂电池·个人层',
                      'subtitle': '随身供电 · 家用电动车体验',
                      'boxes': [('消费电子', '待机快充安全'),
                                ('家用电动车', '续航充电冬天'),
                                ('补能焦虑', '网络与行程匹配'),
                                ('残值质保', '健康度边界')],
                      'hint': '专题配图 · 体验优于口号',
                      'cols': 2},
                     {'file': '04-market.jpg',
                      'kind': 'flow',
                      'badge': '04 · 股市',
                      'title': '锂电池·股市层',
                      'subtitle': '量 × 价 × 利润率 · 环节轮动',
                      'boxes': [('装机出货', '量'), ('锂价材料价', '价'), ('毛利率', '利润率'), ('轮动', '资源材料电芯')],
                      'hint': '专题配图 · 装机高≠利润好',
                      'orientation': 'horizontal'},
                     {'file': '05-industry.jpg',
                      'kind': 'flow',
                      'badge': '05 · 产业',
                      'title': '锂电池·产业层',
                      'subtitle': '材料 → 电芯 → 系统 → 应用 → 回收',
                      'boxes': [('资源材料', '锂盐四大材料'),
                                ('电芯制造', '形态与良率'),
                                ('电池系统', 'PACK BMS热管理'),
                                ('应用回收', '动力储能闭环')],
                      'hint': '专题配图 · 整车细节见新能源车专题',
                      'orientation': 'horizontal'}]}


def render_plate(sector_id: str, spec: dict) -> Path:
    out = OUT_ROOT / sector_id / spec["file"]
    if str(spec["file"]).endswith(".svg"):
        from scripts.tools._emit_new_sectors import render_svg
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_svg(spec), encoding="utf-8")
        return out
    kind = spec["kind"]
    common = dict(
        path=out,
        badge=spec["badge"],
        title=spec["title"],
        subtitle=spec["subtitle"],
        hint=spec["hint"],
    )
    if kind == "flow":
        render_flow_plate(**common, boxes=spec["boxes"], orientation=spec.get("orientation", "horizontal"))
    elif kind == "grid":
        render_grid_plate(**common, boxes=spec["boxes"], cols=spec.get("cols", 3))
    elif kind == "hub":
        render_hub_plate(**common, center=spec["center"], satellites=spec["satellites"])
    else:
        raise ValueError(kind)
    return out


def render_sector(sector_id: str) -> list[Path]:
    if sector_id not in PLATES:
        raise KeyError(sector_id)
    return [render_plate(sector_id, spec) for spec in PLATES[sector_id]]


def render_all(sector_ids: Iterable[str] | None = None) -> dict[str, list[Path]]:
    ids = list(sector_ids) if sector_ids else list(PLATES)
    return {sid: render_sector(sid) for sid in ids}


if __name__ == "__main__":
    import sys

    targets = sys.argv[1:] or list(PLATES)
    result = render_all(targets)
    total = sum(len(v) for v in result.values())
    print(f"rendered {len(result)} sectors, {total} images → {OUT_ROOT}")
