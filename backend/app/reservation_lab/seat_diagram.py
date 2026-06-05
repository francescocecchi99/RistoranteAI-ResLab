"""Seat diagram HTML — mirrors static/js/restaurant-setup.js (chairLayout, buildSeatDiagramHTML)."""

from __future__ import annotations


ACCENT_BORDER = [
    "rgb(166 77 50)",
    "rgb(124 90 72)",
    "rgb(180 120 90)",
    "rgb(140 95 70)",
    "rgb(110 78 62)",
]


def chair_layout(seats: int) -> dict[str, int]:
    n = max(1, int(seats))
    if n == 1:
        return {"top": 0, "bottom": 1, "left": 0, "right": 0}
    if n == 2:
        return {"top": 1, "bottom": 1, "left": 0, "right": 0}
    if n == 3:
        return {"top": 1, "bottom": 1, "left": 1, "right": 0}
    if n == 4:
        return {"top": 2, "bottom": 2, "left": 0, "right": 0}
    if n == 5:
        return {"top": 2, "bottom": 2, "left": 1, "right": 0}
    if n == 6:
        return {"top": 3, "bottom": 3, "left": 0, "right": 0}
    if n == 7:
        return {"top": 3, "bottom": 3, "left": 1, "right": 0}
    if n == 8:
        return {"top": 4, "bottom": 4, "left": 0, "right": 0}
    top = (n + 1) // 2
    return {"top": top, "bottom": n - top, "left": 0, "right": 0}


def _chair_arc(side: str, stroke: str, narrow: bool, dashed: bool) -> str:
    w = 12 if narrow else 16
    h = 5 if narrow else 7
    ds = " lab-chair--head" if dashed else ""
    if side == "top":
        return f'<div class="lab-chair lab-chair--top{ds}" style="width:{w}px;height:{h}px;border-color:{stroke}"></div>'
    if side == "bottom":
        return f'<div class="lab-chair lab-chair--bottom{ds}" style="width:{w}px;height:{h}px;border-color:{stroke}"></div>'
    if side == "left":
        return f'<div class="lab-chair lab-chair--left{ds}" style="width:{h}px;height:{w}px;border-color:{stroke}"></div>'
    return f'<div class="lab-chair lab-chair--right{ds}" style="width:{h}px;height:{w}px;border-color:{stroke}"></div>'


def _chair_placeholder(narrow: bool) -> str:
    w = 12 if narrow else 16
    h = 5 if narrow else 7
    return f'<div class="lab-chair-ph" style="width:{h}px;height:{w}px"></div>'


def _table_core_class(L: dict[str, int], seats: int) -> str:
    tb_only = L["left"] == 0 and L["right"] == 0
    if seats == 2 and tb_only:
        return "lab-table-core"
    if not tb_only:
        return "lab-table-core lab-table-core--wide"
    along = max(L["top"], L["bottom"])
    if along <= 2:
        return "lab-table-core"
    if along == 3:
        return "lab-table-core lab-table-core--wide"
    return "lab-table-core lab-table-core--xlarge"


def build_seat_diagram_html(seats: int, free_stroke: str, head_extra: int) -> str:
    n = max(1, int(seats))
    L = chair_layout(n)
    stroke = free_stroke or "rgb(23 23 23)"
    fill = "rgb(255 255 255)"
    n_head = min(2, max(0, int(head_extra)))
    max_along = max(L["top"], L["bottom"], 1)
    narrow = max_along >= 4
    head_right = (n_head + 1) // 2
    head_left = n_head // 2
    phantom_left = head_right if head_left == 0 and head_right > 0 else 0
    phantom_right = head_left if head_right == 0 and head_left > 0 else 0
    core_cls = _table_core_class(L, n)
    html = '<div class="lab-diagram">'
    if head_left > 0 or phantom_left > 0:
        html += '<div class="lab-chair-stack lab-chair-stack--pull">'
        for _ in range(head_left):
            html += _chair_arc("left", stroke, narrow, True)
        for _ in range(phantom_left):
            html += _chair_placeholder(narrow)
        html += "</div>"
    html += '<div class="lab-diagram-col">'
    if L["top"] > 0:
        html += '<div class="lab-chair-row">'
        for _ in range(L["top"]):
            html += _chair_arc("top", stroke, narrow, False)
        html += "</div>"
    html += '<div class="lab-table-row">'
    if L["left"] > 0:
        html += '<div class="lab-chair-stack">'
        for _ in range(L["left"]):
            html += _chair_arc("left", stroke, narrow, False)
        html += "</div>"
    html += f'<div class="{core_cls}" style="border-color:{stroke};background:{fill}"><span class="lab-table-digit">{n}</span></div>'
    if L["right"] > 0:
        html += '<div class="lab-chair-stack">'
        for _ in range(L["right"]):
            html += _chair_arc("right", stroke, narrow, False)
        html += "</div>"
    html += "</div>"
    if L["bottom"] > 0:
        html += '<div class="lab-chair-row">'
        for _ in range(L["bottom"]):
            html += _chair_arc("bottom", stroke, narrow, False)
        html += "</div>"
    html += "</div>"
    if phantom_right > 0 or head_right > 0:
        html += '<div class="lab-chair-stack lab-chair-stack--pull-left">'
        for _ in range(phantom_right):
            html += _chair_placeholder(narrow)
        for _ in range(head_right):
            html += _chair_arc("right", stroke, narrow, True)
        html += "</div>"
    html += "</div>"
    return html


def area_stroke(area_names_ordered: list[str], area_name: str) -> str:
    if area_name == "__other__":
        return ACCENT_BORDER[1]
    try:
        idx = area_names_ordered.index(area_name)
    except ValueError:
        idx = 0
    return ACCENT_BORDER[idx % len(ACCENT_BORDER)]
