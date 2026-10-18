"""设计系统 Token：颜色、间距、圆角、字体、边框与动效。

唯一真源：``docs/windows/UI_REDESIGN_2026-10-19.md`` 的 5.1 / 5.2 / 5.3 节。
本模块只放纯数据与纯函数：不导入 PySide6，不读写文件，不产生副作用。
QSS 用 ``@token`` 占位，由 :mod:`liteapp.design.style_loader` 在加载时替换。
"""
from __future__ import annotations

# 5.1 色彩 Token。前 13 项与设计文档同名同值（唯一真源）；
# 其后是 QSS 交互状态必需的派生色，全部在文档色相内取值。
COLORS: dict[str, str] = {
    # --- 文档 5.1 原始 Token ---
    "window-bg": "#F4F2EC",
    "surface": "#FFFFFF",
    "surface-muted": "#EAE8E0",
    "text-primary": "#1F2421",
    "text-secondary": "#68716B",
    "accent": "#176B5B",
    "nav-bg": "#17211E",
    "border": "#DCDDD4",
    "success": "#287A4B",
    "warning": "#9A6415",
    "error": "#B42332",
    "info": "#3267A8",
    "focus": "#4D9C8B",
    # --- 5.3 示例中出现的交互色 ---
    "accent-hover": "#125548",
    "text-disabled": "#8A918C",
    # --- 派生：强调色状态 ---
    "accent-pressed": "#0F483D",
    "accent-soft": "#E6F2EC",
    "accent-soft-hover": "#DCEBE4",
    "accent-border": "#B6D6C9",
    "accent-disabled": "#A9C4BA",
    # --- 派生：面与边框 ---
    "surface-subtle": "#F7F5EE",
    "surface-hover": "#EEF3EF",
    "surface-pressed": "#E3ECE7",
    "border-strong": "#C9CDC4",
    "text-on-accent": "#FFFFFF",
    # --- 派生：深色导航 ---
    "nav-hover": "#22302A",
    "nav-checked": "#2A3B33",
    "nav-text": "#F4F2EC",
    "nav-text-muted": "#A9B8B1",
    # --- 派生：状态色浅底 ---
    "success-soft": "#E7F1EA",
    "warning-soft": "#F6EEDF",
    "error-soft": "#F6E7E8",
    "info-soft": "#E8EFF7",
    # --- 派生：危险操作 ---
    "danger": "#B42332",
    "danger-hover": "#9A1E2A",
    "danger-pressed": "#801821",
    "danger-soft": "#F6E7E8",
    "danger-border": "#E3B4B9",
}

# 5.2 间距基数 4px；常用档位 4/8/12/16/24/32。
SPACING: dict[str, int] = {
    "xs": 4,
    "sm": 8,
    "md": 12,
    "lg": 16,
    "xl": 24,
    "xxl": 32,
}

# 5.2 圆角：输入框 6、按钮 8、卡片 12、浮层 14。
RADIUS: dict[str, int] = {
    "input": 6,
    "button": 8,
    "card": 12,
    "overlay": 14,
    "indicator": 4,
    "scrollbar": 4,
}

# 5.2 边框：默认 1px，键盘焦点 2px。
BORDER: dict[str, int] = {
    "default": 1,
    "focus": 2,
}

# 5.2 字体：优先 Microsoft YaHei UI，回退 Microsoft YaHei / Segoe UI。
FONT_FAMILY = '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif'

# 5.2 字号档位。
FONT_SIZE: dict[str, int] = {
    "caption": 12,
    "secondary": 13,
    "body": 14,
    "section": 16,
    "title": 20,
    "metric": 24,
}

FONT_WEIGHT: dict[str, int] = {
    "normal": 400,
    "medium": 500,
    "semibold": 600,
    "bold": 700,
}

# 供 Python 侧引用的成组结构（QSS 变量直接取上面的扁平常量）。
FONT: dict[str, object] = {
    "family": FONT_FAMILY,
    "size": FONT_SIZE,
    "weight": FONT_WEIGHT,
}

# 5.2 动效（毫秒）：悬停 120、面板展开 180、状态过渡 160。由 Python 执行。
DURATION: dict[str, int] = {
    "hover": 120,
    "panel": 180,
    "state": 160,
}

# QSS 需要、但不属于颜色/间距档位的固定尺寸。
SIZES: dict[str, int] = {
    "control": 32,
    "icon": 24,
    "checkbox": 16,
    "scrollbar": 10,
    "scrollbar-min": 28,
    "menu-min-width": 180,
}


def qss_vars() -> dict[str, str]:
    """把全部 Token 摊平成 ``{"@accent": "#176B5B", ...}``，供 QSS 模板替换。"""
    values: dict[str, str] = {
        f"@{name}": value for name, value in COLORS.items()
    }
    for tier, size in SPACING.items():
        values[f"@space-{tier}"] = f"{size}px"
    for name, radius in RADIUS.items():
        values[f"@radius-{name}"] = f"{radius}px"
    values["@border-width"] = f"{BORDER['default']}px"
    values["@border-width-focus"] = f"{BORDER['focus']}px"
    values["@font-family"] = FONT_FAMILY
    for tier, size in FONT_SIZE.items():
        values[f"@font-size-{tier}"] = f"{size}px"
    for name, weight in FONT_WEIGHT.items():
        values[f"@font-weight-{name}"] = str(weight)
    for name, milliseconds in DURATION.items():
        values[f"@duration-{name}"] = f"{milliseconds}ms"
    for name, size in SIZES.items():
        values[f"@size-{name}"] = f"{size}px"
    return values
