# resources/styles

- `global.qss`：主窗口与悬浮球统一的基础样式表，负责颜色、字体、边框、圆角与交互状态。
- Token：QSS 里的 `@token` 由 `liteapp/design/style_loader.py` 在加载时用
  `liteapp/design/theme.py` 的 `qss_vars()` 替换；未登记的 `@xxx` 原样保留。
- 加载：`from liteapp.design.style_loader import load_qss, load_stylesheet`；
  多个文件按顺序拼接，文件缺失时返回空串并打印一条警告，不抛异常。
- 新增样式：先在 `theme.py` 注册 token（颜色/间距/圆角/字号/尺寸），
  再在本目录新建 `*.qss` 用 `@token` 引用，最后用 `load_stylesheet(...)` 组合。
- 禁止在 QSS 里写颜色字面量与字号字面量；布局、尺寸计算与动画留在 Python。
- 开发态改完 QSS 调用 `clear_cache()` 清掉 `lru_cache` 的读取结果。
- 打包：`ChatAssistantLite.spec` 需把 `liteapp/resources` 加进 `datas`（本次未改）。
