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

PLATES: dict[str, list[dict]] = {
    "robots": [
        {
            "file": "01-overview.jpg",
            "kind": "flow",
            "badge": "01 · 全景",
            "title": "机器人产业链全景",
            "subtitle": "从核心零部件到系统集成，再到场景落地与运营服务",
            "boxes": [
                ("上游零部件", "减速器·伺服·控制器\n传感器与视觉"),
                ("本体制造", "工业/协作机器人\n人形与特种机器人"),
                ("系统集成", "产线规划·工装夹具\n软件与数字孪生"),
                ("下游应用", "汽车·3C·锂电\n物流与服务场景"),
            ],
            "hint": "专题配图 · 主题关联≠收入占比 · 先定位企业处在哪一层",
        },
        {
            "file": "02-core.jpg",
            "kind": "grid",
            "badge": "02 · 核心硬件",
            "title": "中游本体与关键硬件",
            "subtitle": "本体决定形态，核心件决定性能与成本",
            "cols": 3,
            "boxes": [
                ("工业机器人", "六轴多关节为主\n搬运·焊接·喷涂"),
                ("协作机器人", "力控与安全交互\n柔性产线部署快"),
                ("人形 / 特种", "双足轮式·巡检\n仍处示范与小批量"),
                ("伺服系统", "电机+驱动器\n决定精度与节拍"),
                ("减速器", "RV / 谐波\n成本与供给关键点"),
                ("控制器", "运动规划与总线\n软硬协同壁垒"),
            ],
            "hint": "专题配图 · 核对订单是样机还是批量交付",
        },
        {
            "file": "03-system.jpg",
            "kind": "hub",
            "badge": "03 · 子系统",
            "title": "成本与子系统结构",
            "subtitle": "核心零部件通常占整机成本大头，集成与软件决定可复制性",
            "center": ("整机成本结构", "零部件 + 装配\n+ 软件与调试"),
            "satellites": [
                ("减速器", "高精度传动，供给集中"),
                ("伺服驱动", "响应速度与过载能力"),
                ("控制器/算法", "轨迹、力控、安全"),
                ("感知视觉", "2D/3D相机与标定"),
                ("本体结构", "铸件/钣金与线缆"),
                ("系统集成", "夹具、工艺包、联调"),
            ],
            "hint": "专题配图 · 自研核心件与外购比例影响毛利率叙事",
        },
        {
            "file": "04-downstream.jpg",
            "kind": "grid",
            "badge": "04 · 下游变现",
            "title": "下游场景与赚钱方式",
            "subtitle": "设备销售、集成项目、运维租赁与工艺包可并存",
            "cols": 2,
            "boxes": [
                ("汽车与零部件", "焊接冲压涂装线\n资本开支周期敏感"),
                ("3C 与锂电", "高速节拍·换型频繁\n对柔性和视觉要求高"),
                ("物流仓储", "AMR/分拣搬运\n按场站复制扩张"),
                ("服务与运维", "备件·培训·远程诊断\n关注续费与毛利质量"),
            ],
            "hint": "专题配图 · 问：客户付的是设备款还是产线/产能？",
        },
    ],
    "metals": [
        {
            "file": "01-overview.jpg",
            "kind": "flow",
            "badge": "01 · 全景",
            "title": "金属产业链全景",
            "subtitle": "资源开采 → 冶炼加工 → 材料制品 → 终端需求",
            "boxes": [
                ("资源端", "矿山勘探开采\n精矿与权益矿"),
                ("冶炼端", "粗炼精炼电解\n合金与再生金属"),
                ("加工端", "板带箔棒线\n压延与深加工"),
                ("需求端", "建筑·汽车·电力\n包装与消费电子"),
            ],
            "hint": "专题配图 · 商品价格、加工费与库存周期需分开看",
        },
        {
            "file": "02-core.jpg",
            "kind": "grid",
            "badge": "02 · 核心产品",
            "title": "主要金属与中间品",
            "subtitle": "不同金属的定价机制、成本曲线与政策约束差异很大",
            "cols": 3,
            "boxes": [
                ("铜", "导电与电力基建\n精矿TC/RC敏感"),
                ("铝", "电解铝+氧化铝\n电价与能耗约束"),
                ("钢铁", "长流程/短流程\n板材长材结构"),
                ("贵金属", "金银伴生回收\n避险与工业需求"),
                ("小金属", "钨钼锂稀土等\n供给与政策权重高"),
                ("再生金属", "废料回收冶炼\n碳足迹优势渐显"),
            ],
            "hint": "专题配图 · 先分清卖的是矿、金属还是加工材",
        },
        {
            "file": "03-system.jpg",
            "kind": "hub",
            "badge": "03 · 成本结构",
            "title": "成本与利润驱动因子",
            "subtitle": "资源自给率、能源成本与加工费决定盈利弹性",
            "center": ("利润敏感项", "价格 − 现金成本\n− 费用与税费"),
            "satellites": [
                ("矿石品位", "影响单位金属成本"),
                ("能源电力", "电解铝等高耗能关键"),
                ("加工费", "冶炼厂与矿山分成"),
                ("运费汇率", "进出口与海运波动"),
                ("环保配额", "排放与产能合规"),
                ("库存周期", "社会库存与基差"),
            ],
            "hint": "专题配图 · 主题涨价≠公司利润同步扩张",
        },
        {
            "file": "04-downstream.jpg",
            "kind": "grid",
            "badge": "04 · 下游应用",
            "title": "终端需求如何传导",
            "subtitle": "地产链、制造链与新能源链对金属品种的拉动不同",
            "cols": 2,
            "boxes": [
                ("建筑与基建", "螺纹线材铝型材\n开工与投资节奏"),
                ("汽车与交通", "车身板·线缆·轻量化\n新能源车单车用铜升"),
                ("电力与电网", "电缆变压器铜铝\n电网投资确定性相对高"),
                ("包装与消费", "铝罐箔材不锈钢\n可选消费弹性更大"),
            ],
            "hint": "专题配图 · 核对公司产品结构与下游敞口",
        },
    ],
    "real-estate": [
        {
            "file": "01-overview.jpg",
            "kind": "flow",
            "badge": "01 · 全景",
            "title": "房地产产业链全景",
            "subtitle": "土地与融资 → 开发建设 → 销售经营 → 物业与资管",
            "boxes": [
                ("土地与融资", "招拍挂·收并购\n开发贷与债券"),
                ("开发建设", "设计施工精装\n供应链与工期"),
                ("销售去化", "预售网签回款\n价格与折扣策略"),
                ("持有运营", "物业商业写字楼\nREITs与轻资产"),
            ],
            "hint": "专题配图 · 销售金额、权益比例与现金流要分开读",
        },
        {
            "file": "02-core.jpg",
            "kind": "grid",
            "badge": "02 · 核心业务",
            "title": "开发与经营主产品",
            "subtitle": "住宅开发仍是主盘，商业与代建改变报表节奏",
            "cols": 3,
            "boxes": [
                ("住宅开发", "刚需改善豪宅\n周转与土储质量"),
                ("综合住区", "配套教育商业\n城市更新项目"),
                ("商业综合体", "购物中心酒店\n出租率与租金"),
                ("写字楼产业园", "去化慢、资本重\n更看运营能力"),
                ("代建管理", "输出品牌与管理\n轻资产收费"),
                ("城市服务", "物业增值服务\n续费率与收缴率"),
            ],
            "hint": "专题配图 · 并表口径与合作盘权益需核对公告",
        },
        {
            "file": "03-system.jpg",
            "kind": "hub",
            "badge": "03 · 资金结构",
            "title": "成本、杠杆与回款",
            "subtitle": "地产是资金与信用行业，利润表之外更要看现金流",
            "center": ("关键闭环", "拿地→开工→\n销售→回款→偿债"),
            "satellites": [
                ("土地成本", "货值与利润率上限"),
                ("建安成本", "材料人工与精装标准"),
                ("融资成本", "利率、期限、抵押"),
                ("预售监管", "资金支用节奏约束"),
                ("去化速度", "决定周转与减值"),
                ("存量经营", "租金覆盖与估值"),
            ],
            "hint": "专题配图 · 有销售不等于可自由支配现金",
        },
        {
            "file": "04-downstream.jpg",
            "kind": "grid",
            "badge": "04 · 变现路径",
            "title": "钱从哪里来",
            "subtitle": "销售回款、租金物业费与管理输出是主要来源",
            "cols": 2,
            "boxes": [
                ("商品房销售", "认购网签按揭回款\n关注权益销售额"),
                ("租赁与商业", "租金物业多经\n抗周期但扩张慢"),
                ("代建与品牌", "管理费激励费\n考验标准化能力"),
                ("资产盘活", "售物业、REITs\n回收资金再投放"),
            ],
            "hint": "专题配图 · 政策、城市能级与产品结构共同决定弹性",
        },
    ],
    "consumer-electronics": [
        {
            "file": "01-overview.jpg",
            "kind": "flow",
            "badge": "01 · 全景",
            "title": "消费电子产业链全景",
            "subtitle": "元器件 → 模组部件 → 整机 EMS → 品牌渠道",
            "boxes": [
                ("元器件", "芯片被动件\n连接器与声学"),
                ("模组部件", "显示摄像结构件\n电池与散热"),
                ("组装制造", "ODM/EMS\n测试包装物流"),
                ("品牌渠道", "手机PC可穿戴\n零售与电商"),
            ],
            "hint": "专题配图 · 大客户份额与新品年周期决定业绩弹性",
        },
        {
            "file": "02-core.jpg",
            "kind": "grid",
            "badge": "02 · 核心硬件",
            "title": "中游关键部件与整机",
            "subtitle": "精密制造与垂直整合能力是竞争焦点",
            "cols": 3,
            "boxes": [
                ("连接器线缆", "高速传输与射频\n汽车电子延伸"),
                ("声学光学", "扬声器麦克风\n摄像头模组"),
                ("结构件散热", "金属中框CNC\n均热板与风扇"),
                ("显示模组", "面板驱动触控\n笔电车载穿戴"),
                ("组装测试", "SMT整机测试\n良率与节拍"),
                ("可穿戴整机", "耳机手表VR\n创新形态迭代快"),
            ],
            "hint": "专题配图 · 区分部件商、整机代工与品牌商",
        },
        {
            "file": "03-system.jpg",
            "kind": "hub",
            "badge": "03 · 成本系统",
            "title": "BOM 与制造成本结构",
            "subtitle": "芯片与显示常占 BOM 大头，制造费用看自动化与良率",
            "center": ("整机成本", "BOM + 制造\n+ 物流售后"),
            "satellites": [
                ("主芯片", "规格升级驱动 ASP"),
                ("显示触控", "尺寸刷新率差异大"),
                ("存储电池", "行情与容量敏感"),
                ("结构声学", "体验差异化卖点"),
                ("制造费用", "人工折旧良率"),
                ("认证运输", "各地准入与关税"),
            ],
            "hint": "专题配图 · 份额提升也可能伴随价格与毛利压力",
        },
        {
            "file": "04-downstream.jpg",
            "kind": "grid",
            "badge": "04 · 下游应用",
            "title": "终端品类与变现",
            "subtitle": "消费电子以产品周期和出货量为核心，服务收入占比通常较低",
            "cols": 2,
            "boxes": [
                ("智能手机", "换机与创新周期\n零部件升级主引擎"),
                ("PC / 平板", "商用消费双轨\nAI PC 规格迁移"),
                ("可穿戴 XR", "耳机手表MR\n形态未完全定型"),
                ("汽车电子外溢", "线束连接座舱\n消费电子产能平移"),
            ],
            "hint": "专题配图 · 看客户集中度与下一代产品导入进度",
        },
    ],
    "new-consumption": [
        {
            "file": "01-overview.jpg",
            "kind": "flow",
            "badge": "01 · 全景",
            "title": "新消费产业链全景",
            "subtitle": "原料供应 → 品牌研发制造 → 渠道内容 → 用户复购",
            "boxes": [
                ("原料包材", "农产品香精\n包材与代工厂"),
                ("品牌与产品", "配方研发品控\n包装与大单品"),
                ("渠道内容", "商超便利电商\n直播兴趣内容"),
                ("用户运营", "会员复购社群\n数据与私域"),
            ],
            "hint": "专题配图 · 流量红利消退后更看复购与渠道利润",
        },
        {
            "file": "02-core.jpg",
            "kind": "grid",
            "badge": "02 · 核心产品",
            "title": "代表性新消费品类",
            "subtitle": "饮料零食美妆等赛道逻辑相近：大单品 + 渠道效率",
            "cols": 3,
            "boxes": [
                ("功能饮料", "能量补水场景\n铺货与动销"),
                ("休闲零食", "口味迭代快\n供应链柔性"),
                ("美妆个护", "成分功效种草\n代工与自主生产"),
                ("速冻餐饮", "家庭与B端\n冷链能力关键"),
                ("宠物消费", "主粮零食医疗\n情感消费属性"),
                ("潮玩文创", "IP与限量\n库存风险更高"),
            ],
            "hint": "专题配图 · 大单品依赖度与新品成功率要并看",
        },
        {
            "file": "03-system.jpg",
            "kind": "hub",
            "badge": "03 · 成本费用",
            "title": "成本与费用结构",
            "subtitle": "毛利率高不代表净利率高，营销与渠道费用常是关键",
            "center": ("盈利公式", "毛利 − 销售费用\n− 管理研发"),
            "satellites": [
                ("原料成本", "农产品与包材波动"),
                ("制造代工", "产能利用率"),
                ("经销折扣", "渠道库存与返利"),
                ("广告投放", "投产比与品牌资产"),
                ("仓储物流", "鲜度与破损"),
                ("门店费用", "若有直营网络"),
            ],
            "hint": "专题配图 · 费用率趋势比单季爆品更重要",
        },
        {
            "file": "04-downstream.jpg",
            "kind": "grid",
            "badge": "04 · 赚钱方式",
            "title": "渠道与变现闭环",
            "subtitle": "卖货是主业；会员、联名与供应链输出是增强项",
            "cols": 2,
            "boxes": [
                ("传统通路", "经销商超便利\n铺货深度决定基本盘"),
                ("线上内容", "电商直播短视频\n获客快但费用波动大"),
                ("场景餐饮", "即饮即食团购\n与天气季节相关"),
                ("复购运营", "会员订阅周边\n提升终身价值"),
            ],
            "hint": "专题配图 · 区分主题热度与可验证的动销/利润",
        },
    ],
    "semiconductors": [
        {
            "file": "01-overview.jpg",
            "kind": "flow",
            "badge": "01 · 全景",
            "title": "半导体产业链全景",
            "subtitle": "设计 → 制造 → 封测 → 设备材料 → 整机需求",
            "boxes": [
                ("IC 设计", "架构IP与芯片\nfabless 模式"),
                ("晶圆制造", "逻辑存储功率\n制程与产能"),
                ("封装测试", "先进封装\n可靠性与交期"),
                ("设备材料", "光刻薄膜检测\n硅片气体化学品"),
            ],
            "hint": "专题配图 · 设计、制造、设备是不同风险收益结构",
        },
        {
            "file": "02-core.jpg",
            "kind": "grid",
            "badge": "02 · 核心环节",
            "title": "中游制造与关键硬件",
            "subtitle": "晶圆厂与设备材料构成国产替代与资本开支主线",
            "cols": 3,
            "boxes": [
                ("逻辑代工", "先进/成熟制程\n客户与产能利用率"),
                ("存储芯片", "DRAM/NAND\n价格周期强"),
                ("功率与模拟", "MOS/IGBT/CIS\n汽车工业需求"),
                ("前道设备", "刻蚀薄膜注入\n检测量测"),
                ("后道封测", "Flip-Chip/Chiplet\n异构集成"),
                ("关键材料", "硅片光刻胶\n电子特气靶材"),
            ],
            "hint": "专题配图 · 订单、产能与制程节点要具体到产品线",
        },
        {
            "file": "03-system.jpg",
            "kind": "hub",
            "badge": "03 · 子系统",
            "title": "制造成本与技术栈",
            "subtitle": "资本密集 + 工艺know-how，折旧与良率决定单位成本",
            "center": ("晶圆成本", "折旧材料人力\n+ 良率损耗"),
            "satellites": [
                ("光刻与掩模", "分辨率与套刻"),
                ("薄膜刻蚀", "结构成型关键"),
                ("清洗量测", "缺陷控制"),
                ("气体化学品", "纯度与供应稳定"),
                ("自动化厂务", "无尘与动力"),
                ("EDA / 设计服务", "设计到流片桥梁"),
            ],
            "hint": "专题配图 · 主题“国产替代”需落到具体产品验证进度",
        },
        {
            "file": "04-downstream.jpg",
            "kind": "grid",
            "badge": "04 · 下游需求",
            "title": "芯片用在哪里、如何变现",
            "subtitle": "手机PC之后，汽车、工业与算力成为重要增量",
            "cols": 2,
            "boxes": [
                ("消费电子", "手机PC可穿戴\n仍是最大存量池"),
                ("算力与数据中心", "CPU/GPU/ASIC\n高速互联与存储"),
                ("汽车电子", "智驾域控功率\n认证周期长"),
                ("工业与能源", "工控逆变充电\n可靠性要求高"),
            ],
            "hint": "专题配图 · 设计公司看流片放量，晶圆厂看稼动率",
        },
    ],
    "nev": [
        {
            "file": "01-overview.jpg",
            "kind": "flow",
            "badge": "01 · 全景",
            "title": "新能源车产业链全景",
            "subtitle": "零部件 → 电驱电控电池 → 整车制造 → 补能与出行服务",
            "boxes": [
                ("关键零部件", "车身底盘热管理\n域控与智驾感知"),
                ("三电系统", "电池电机电控\n功率半导体"),
                ("整车制造", "平台化与产能\n品牌与渠道"),
                ("补能服务", "充电换电\n后市场与出行"),
            ],
            "hint": "专题配图 · 整车、零部件与电池公司驱动因素不同",
        },
        {
            "file": "02-core.jpg",
            "kind": "grid",
            "badge": "02 · 核心硬件",
            "title": "中游整车与关键总成",
            "subtitle": "平台、三电与智驾决定产品力，成本控制决定利润",
            "cols": 3,
            "boxes": [
                ("纯电平台", "续航效率空间\n高压架构趋势"),
                ("混动/增程", "油耗与补能便利\n细分市场需求"),
                ("电驱动总成", "电机减速器电控\n集成化降本"),
                ("热管理系统", "电池座舱效率\n影响续航体验"),
                ("底盘车身", "轻量化与安全\n一体化压铸等"),
                ("智驾域控", "算力传感线控\n软件迭代重要"),
            ],
            "hint": "专题配图 · 交付量、平均售价与单车利润要拆开",
        },
        {
            "file": "03-system.jpg",
            "kind": "hub",
            "badge": "03 · 成本结构",
            "title": "单车成本与关键子系统",
            "subtitle": "电池仍是最大成本项之一，规模与垂直整合影响毛利",
            "center": ("单车成本", "电池+车身+\n电子电器+制造"),
            "satellites": [
                ("动力电池", "容量与价格波动"),
                ("电驱系统", "集成与功率器件"),
                ("车身轻量化", "铝镁复合材料"),
                ("智能座舱", "显示芯片软件"),
                ("智驾硬件", "雷达相机域控"),
                ("制造费用", "产能利用率"),
            ],
            "hint": "专题配图 · 销量增长若伴随价格战，利润未必同步",
        },
        {
            "file": "04-downstream.jpg",
            "kind": "grid",
            "badge": "04 · 下游变现",
            "title": "整车如何赚钱、生态如何延伸",
            "subtitle": "整车销售仍是主体，软件订阅与出行服务在培育中",
            "cols": 2,
            "boxes": [
                ("整车销售", "批发零售交付\n关注折扣与库存"),
                ("零部件配套", "定点→爬产→降本\n客户份额是关键"),
                ("补能网络", "充电运营换电\n利用率决定回报"),
                ("后市场服务", "保险金融配件\n品牌粘性来源"),
            ],
            "hint": "专题配图 · 主题热度≠单车盈利改善已验证",
        },
    ],
    "innovative-drugs": [
        {
            "file": "01-overview.jpg",
            "kind": "flow",
            "badge": "01 · 全景",
            "title": "创新药产业链全景",
            "subtitle": "靶点发现 → 临床开发 → 生产商业化 → 诊疗支付",
            "boxes": [
                ("早期发现", "靶点筛选分子\n平台与许可引进"),
                ("临床开发", "I/II/III期\n注册与审评"),
                ("生产供应", "原料药制剂\nCDMO与质量体系"),
                ("商业化支付", "进院医保零售\n学术推广与可及性"),
            ],
            "hint": "专题配图 · 管线阶段决定风险，支付决定利润",
        },
        {
            "file": "02-core.jpg",
            "kind": "grid",
            "badge": "02 · 核心产品",
            "title": "创新药与关键服务形态",
            "subtitle": "自研分子、许可引进与 CXO 服务是常见路径",
            "cols": 3,
            "boxes": [
                ("小分子药物", "合成可及性高\n适应症竞争激烈"),
                ("抗体/生物药", "靶向与免疫\n工艺与产能重要"),
                ("ADC / 细胞基因", "技术门槛高\n仍处快速演进"),
                ("临床 CRO", "试验设计执行\n与管线景气相关"),
                ("CDMO 生产", "工艺开发商业化生产\n订单能见度关键"),
                ("诊断伴随", "患者筛选监测\n与药物协同"),
            ],
            "hint": "专题配图 · 区分 Biopharma 与 CXO 的收入确认方式",
        },
        {
            "file": "03-system.jpg",
            "kind": "hub",
            "badge": "03 · 研发成本",
            "title": "研发与商业化成本结构",
            "subtitle": "失败率高、周期长，资本开支与销售体系同样关键",
            "center": ("价值实现", "临床证据 →\n注册 → 放量"),
            "satellites": [
                ("临床费用", "入组速度与中心数"),
                ("CMC 生产", "工艺稳定性与成本"),
                ("注册合规", "审评补材与检查"),
                ("销售队伍", "进院与学术推广"),
                ("医保准入", "价格与放量权衡"),
                ("专利寿命", "独占期与竞争"),
            ],
            "hint": "专题配图 · 里程碑叙事需对应临床试验与获批事实",
        },
        {
            "file": "04-downstream.jpg",
            "kind": "grid",
            "badge": "04 · 变现",
            "title": "商业化与支付闭环",
            "subtitle": "药品销售、许可金与服务费是主要收入类型",
            "cols": 2,
            "boxes": [
                ("医院渠道", "进院处方放量\n集采/国谈影响价格"),
                ("零售药店", "慢病与自费品种\nDTP 等模式"),
                ("对外许可", "首付款里程碑分成\n兑现取决于条款"),
                ("CXO 服务费", "项目制/产能锁定\n客户研发预算敏感"),
            ],
            "hint": "专题配图 · 管线关联≠已形成可持续销售收入",
        },
    ],
    "ai-compute": [
        {
            "file": "01-overview.jpg",
            "kind": "flow",
            "badge": "01 · 全景",
            "title": "算力产业链全景",
            "subtitle": "芯片 → 服务器集群 → 互联存储液冷 → 云与应用",
            "boxes": [
                ("算力芯片", "CPU/GPU/加速卡\n互联与内存"),
                ("整机服务器", "AI服务器机柜\n主板电源结构"),
                ("数据中心", "网络存储液冷\n电力与运维"),
                ("云与应用", "训练推理平台\n行业大模型服务"),
            ],
            "hint": "专题配图 · 芯片、整机与IDC运营商利润池不同",
        },
        {
            "file": "02-core.jpg",
            "kind": "grid",
            "badge": "02 · 核心硬件",
            "title": "中游算力硬件栈",
            "subtitle": "加速卡供给与整机交付能力决定短期弹性",
            "cols": 3,
            "boxes": [
                ("通用 CPU", "控制面与通用计算\n国产化推进中"),
                ("加速 GPU/ASIC", "训练推理算力核心\n供给与生态关键"),
                ("高速互联", "NVLink/以太网\n集群扩展瓶颈"),
                ("AI 服务器", "多卡机箱电源\n交付与认证"),
                ("存储内存", "HBM/DDR/SSD\n带宽与容量"),
                ("液冷散热", "冷板浸没\n功耗墙解决方案"),
            ],
            "hint": "专题配图 · 出货量叙事要核对供应链真实交付",
        },
        {
            "file": "03-system.jpg",
            "kind": "hub",
            "badge": "03 · 系统成本",
            "title": "集群成本与关键子系统",
            "subtitle": "单卡价格只是一部分，网络、电力与利用率决定 TCO",
            "center": ("集群 TCO", "芯片+网络+\n电力运维折旧"),
            "satellites": [
                ("加速卡", "占硬件成本大头"),
                ("交换机网络", "带宽与拥塞"),
                ("存储系统", "数据吞吐瓶颈"),
                ("电力配电", "机架功率密度"),
                ("液冷设施", "改造与PUE"),
                ("调度软件", "有效算力利用率"),
            ],
            "hint": "专题配图 · 资本开支高峰不等于应用侧已盈利",
        },
        {
            "file": "04-downstream.jpg",
            "kind": "grid",
            "badge": "04 · 下游变现",
            "title": "算力如何变成收入",
            "subtitle": "卖服务器、卖云时长、卖模型服务是三条主路径",
            "cols": 2,
            "boxes": [
                ("硬件销售", "服务器与部件\n订单波动大"),
                ("云算力租赁", "按卡时/token\n利用率决定利润"),
                ("模型与应用", "API与行业方案\n仍在商业化早期"),
                ("智算中心运营", "建设+运营补贴\n需看真实上架率"),
            ],
            "hint": "专题配图 · 主题“算力”需落到具体产品与客户账单",
        },
    ],
    "photovoltaics": [
        {
            "file": "01-overview.jpg",
            "kind": "flow",
            "badge": "01 · 全景",
            "title": "光伏产业链全景",
            "subtitle": "硅料 → 硅片电池组件 → 逆变器支架 → 电站与运营",
            "boxes": [
                ("硅料硅片", "多晶硅拉晶切片\n成本与能耗"),
                ("电池组件", "N型技术迭代\n效率与非硅成本"),
                ("逆变器系统", "组串集中储能\n电控与并网"),
                ("电站应用", "集中式分布式\n开发EPC运营"),
            ],
            "hint": "专题配图 · 各环节盈利常错位，需看供需与技术路线",
        },
        {
            "file": "02-core.jpg",
            "kind": "grid",
            "badge": "02 · 核心产品",
            "title": "中游主产品与硬件",
            "subtitle": "电池技术迭代快，逆变器与系统产品连接发电侧",
            "cols": 3,
            "boxes": [
                ("多晶硅", "致密化与电耗\n价格周期剧烈"),
                ("硅片", "大尺寸薄片化\n与电池匹配"),
                ("电池片", "TOPCon/HJT等\n效率决定溢价"),
                ("组件", "功率质保渠道\n一体化率差异"),
                ("逆变器", "转换效率调度\n光储一体化"),
                ("支架跟踪", "结构与 steerable\n影响发电小时数"),
            ],
            "hint": "专题配图 · 技术口号需对照量产良率与成本",
        },
        {
            "file": "03-system.jpg",
            "kind": "hub",
            "badge": "03 · 成本结构",
            "title": "制造成本与系统成本",
            "subtitle": "硅料价格、非硅成本与电站BOS共同影响收益率",
            "center": ("度电成本", "组件+BOS+\n融资运维"),
            "satellites": [
                ("硅成本", "硅料硅片价格"),
                ("非硅成本", "银浆玻璃胶膜"),
                ("制造费用", "折旧良率稼动"),
                ("逆变器BOS", "电缆支架施工"),
                ("土地并网", "指标与消纳"),
                ("运维保险", "长期发电表现"),
            ],
            "hint": "专题配图 · 装机高增时期也可能出现环节亏损",
        },
        {
            "file": "04-downstream.jpg",
            "kind": "grid",
            "badge": "04 · 下游应用",
            "title": "电站与变现模式",
            "subtitle": "设备销售之外，开发持有与运营是另一条利润池",
            "cols": 2,
            "boxes": [
                ("地面电站", "大型集中式\n依赖电价与消纳"),
                ("分布式工商业", "屋顶自发自用\n收益率看电价结构"),
                ("户用光伏", "经销安装金融\n回款与质量风险"),
                ("光储一体", "逆变器+储能\n提高可调度价值"),
            ],
            "hint": "专题配图 · 出货量与盈利能力经常不同步",
        },
    ],
    "lithium-battery": [
        {
            "file": "01-overview.jpg",
            "kind": "flow",
            "badge": "01 · 全景",
            "title": "锂电池产业链全景",
            "subtitle": "资源材料 → 电芯电芯制造 → 系统集成 → 应用与回收",
            "boxes": [
                ("资源材料", "锂镍钴磷\n正负极电解液隔膜"),
                ("电芯制造", "电芯模组PACK\n设备与良率"),
                ("电池系统", "BMS热管理\n整包安全"),
                ("应用回收", "动力储能消费\n梯次与再生"),
            ],
            "hint": "专题配图 · 材料、电芯与设备公司周期位置不同",
        },
        {
            "file": "02-core.jpg",
            "kind": "grid",
            "badge": "02 · 核心产品",
            "title": "中游电芯与关键材料",
            "subtitle": "路线之争落在能量密度、成本、安全与循环寿命",
            "cols": 3,
            "boxes": [
                ("正极材料", "LFP/三元等高镍\n成本与性能权衡"),
                ("负极材料", "石墨硅基\n快充相关"),
                ("电解液", "锂盐溶剂添加剂\n配方壁垒"),
                ("隔膜", "干法湿法涂覆\n安全与供给"),
                ("电芯", "方壳圆柱软包\n能量密度与成本"),
                ("结构件设备", "壳盖极耳\n涂布辊分卷绕"),
            ],
            "hint": "专题配图 · 装机量增长若价格下行，利润需另证",
        },
        {
            "file": "03-system.jpg",
            "kind": "hub",
            "badge": "03 · 成本结构",
            "title": "电芯成本与系统环节",
            "subtitle": "材料占成本主体，制造费用看良率与稼动率",
            "center": ("电芯成本", "材料为主\n+ 制造折旧"),
            "satellites": [
                ("正极", "通常最大成本项"),
                ("负极导电", "快充与循环"),
                ("电解液隔膜", "安全与低温性能"),
                ("铜铝箔", "集流体成本"),
                ("制造良率", "单位费用关键"),
                ("PACK/BMS", "系统集成增值"),
            ],
            "hint": "专题配图 · 一体化并不能自动等于高利润",
        },
        {
            "file": "04-downstream.jpg",
            "kind": "grid",
            "badge": "04 · 下游应用",
            "title": "动力、储能与回收变现",
            "subtitle": "动力电池看车企定点，储能看系统集成与电价机制",
            "cols": 2,
            "boxes": [
                ("动力电池", "乘用车商用车\n装机与价格双因子"),
                ("电力储能", "源网荷储\n循环寿命与安全"),
                ("消费与电动工具", "小型电芯\n订单更碎",),
                ("回收再生", "废料提锂镍钴\n政策与渠道为王"),
            ],
            "hint": "专题配图 · 材料涨价传导能力取决于合同条款",
        },
    ],
    "defense": [
        {
            "file": "01-overview.jpg",
            "kind": "flow",
            "badge": "01 · 全景",
            "title": "军工产业链全景",
            "subtitle": "材料元器件 → 分系统 → 主机总装 → 维修保障",
            "boxes": [
                ("基础材料", "合金复合材料\n电子元器件"),
                ("分系统", "发动机航电\n武器与机电"),
                ("主机总装", "飞机舰船装甲\n导弹与航天器"),
                ("维修保障", "航材维稳\n训练与信息化"),
            ],
            "hint": "专题配图 · 主机与配套节奏、定价机制差异大",
        },
        {
            "file": "02-core.jpg",
            "kind": "grid",
            "badge": "02 · 核心装备",
            "title": "中游主机与关键系统",
            "subtitle": "航空产业链条长，发动机与机载系统是常见研究焦点",
            "cols": 3,
            "boxes": [
                ("战斗机/教练机", "主机总装试飞\n批次交付节奏"),
                ("运输/特种飞机", "改装任务系统\n全寿命保障"),
                ("直升机", "军用民用双轨\n传动与旋翼关键"),
                ("航空发动机", "心脏分系统\n研制生产维修"),
                ("机载航电", "雷达通信光电\n任务计算机"),
                ("导弹武器", "制导推进战斗部\n配套层级多"),
            ],
            "hint": "专题配图 · 订单公告到收入确认常有明显时滞",
        },
        {
            "file": "03-system.jpg",
            "kind": "hub",
            "badge": "03 · 子系统",
            "title": "飞机级子系统结构",
            "subtitle": "主机厂集成众多配套，单一主题关联不等于整机收入",
            "center": ("主机集成", "结构+动力+\n航电+武器"),
            "satellites": [
                ("机体结构", "复材金属加工"),
                ("动力装置", "发动机与附件"),
                ("航电任务", "传感通信火控"),
                ("机电液压", "环控燃油起落架"),
                ("武器挂载", "导弹吊舱接口"),
                ("地面保障", "检测维修训练"),
            ],
            "hint": "专题配图 · 保密与信息披露限制下更要谨慎外推",
        },
        {
            "file": "04-downstream.jpg",
            "kind": "grid",
            "badge": "04 · 下游变现",
            "title": "装备交付与后市场",
            "subtitle": "列装交付是主收入，维修航材与升级改造成长性值得关注",
            "cols": 2,
            "boxes": [
                ("装备销售", "列装交付结算\n计划性较强"),
                ("维修保障", "寿命周期服务\n随保有量上升"),
                ("升级改装", "航电武器改进\n延伸型号价值"),
                ("军民技术转化", "部分能力外溢\n需严格边界说明"),
            ],
            "hint": "专题配图 · 主题事件催化≠合同与收入已兑现",
        },
    ],
}


def render_plate(sector_id: str, spec: dict) -> Path:
    out = OUT_ROOT / sector_id / spec["file"]
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
