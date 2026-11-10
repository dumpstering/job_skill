"""Hidden text and AI-addressed text in a posting (shared decision R5).

posting.py calls this module; nothing here fetches or writes.

Hidden text is text a sighted reader does not see. An element hides its text
when it carries the hidden attribute or a screen-reader-only class, or when
its inline style or a <style> rule on the page sets one of these:
    display:none, visibility:hidden or collapse, content-visibility:hidden,
    opacity (or filter:opacity) under 0.1, a font size of 1px or less (px, pt,
    em, rem, %, keywords; em and % against the parent's size), color or
    -webkit-text-fill-color transparent (unless the text is painted by a
    background clip, a shadow or a stroke) or equal to its own background,
    a transform or scale with a zero factor, a translate, offset, margin or
    text-indent of -500px or more, clip or clip-path to nothing, or a width
    or height of 1px or less with overflow hidden or clip.
Style values are normalized first: CSS comments and escapes decoded, case
and whitespace folded, !important read as a priority. <style> rules are
read with selector lists, type, class, id and attribute selectors,
descendant, child and sibling combinators, :not() :is() :where() and the
structural pseudo-classes a page's first render can decide, nesting, and
the cascade: a later or more specific rule that shows the element again
wins, as in a browser. Rules inside @media or @container, or under a state
pseudo-class (:hover, :focus), hide only sometimes and are left out.
visibility, color and font size are inherited and a child may set them back;
the rest hide the whole subtree. Not read: linked style sheets and scripts,
a text color matched only against a parent's or the page's background, the
closed part of <details>, and aria-hidden (that text is on the screen).

AI-addressed text is matched on a folded copy (NFKC compatibility forms,
accents and invisible characters removed, Cyrillic, Greek and small-capital
look-alikes mapped to Latin): instructions to ignore or disregard earlier
text, 'you are now', a 'system:' line, lines addressed to an AI, assistant,
agent, language model or LLM, and the same in Spanish, French, German and
Portuguese. Visible text is flagged only for instructions aimed at an AI
(AI companies' postings name their products all the time); hidden text is
flagged for an AI's name too.

Every function here is linear in its input (D3): bounded patterns, capped
style sheets and a work budget for selector matching that, once spent,
counts an element a remaining rule could hide as hidden (fail closed).
Stdlib only.
"""
import math
import re
import unicodedata

# ---------- limits ----------

STYLE_MAX = 100_000        # characters of one style attribute or rule block read
CSS_BLOCK_MAX = 200_000    # characters of one <style> element read
CSS_PAGE_MAX = 1_000_000   # characters of <style> read per page
RULES_MAX = 50_000         # selectors kept per page
SELECTORS_PER_RULE = 64
SELECTOR_MAX = 500         # characters of one selector
COMPOUNDS_MAX = 8          # compound selectors in one complex selector
NEST_MAX = 32              # nesting depth of CSS blocks read
MATCH_BUDGET = 400_000     # compound tests per page before failing closed
CLASSES_MAX = 64           # classes and attributes of one element looked up
STYLE_CACHE_MAX = 4096
FONT_MIN = 1.34            # px: a font this small (1px, 1pt) hides its text
OFFSET_MIN = -500.0        # px: an offset this far left or up hides its text
ALPHA_MIN = 0.1            # opacity or color alpha under this hides its text
AI_SENTENCE_MAX = 200
AI_FOUND_MAX = 20          # AI-addressed sentences kept per posting

# Classes that hide text from sighted readers in common CSS frameworks.
HIDDEN_CLASSES = frozenset({"sr-only", "visually-hidden", "visuallyhidden", "screen-reader-text",
                            "screenreader-only"})

# ---------- style values ----------

_ESC = re.compile(r"\\(?:([0-9a-fA-F]{1,6})(?:\r\n|[ \t\r\n\f])?|(\r\n|[\r\n\f])|(.))", re.S)
_SPACES = re.compile(r"\s+")
_PUNCT = re.compile(r" ?([:;,()/!]) ?")
_NUM = re.compile(r"([+-]?(?:\d{1,12}(?:\.\d{0,12})?|\.\d{1,12})(?:e[+-]?\d{1,3})?)([a-z%]{0,5})")
_FUNC = re.compile(r"(?<![a-z0-9-])([a-z][a-z0-9-]{0,39})\(([^()]{0,400})\)")
_ARGS = re.compile(r"[ ,/]+")


def _esc(m):
    h = m.group(1)
    if h is not None:
        cp = int(h, 16)
        return chr(cp) if 0 < cp <= 0x10FFFF and not 0xD800 <= cp <= 0xDFFF else "\ufffd"
    return "" if m.group(2) is not None else m.group(3)


def css_unescape(s):
    """CSS escapes decoded: '\\6e one' is 'none'."""
    return _ESC.sub(_esc, s) if "\\" in s else s


def strip_comments(s):
    """CSS comments removed (each one a space); an unclosed one runs to the end."""
    if "/*" not in s:
        return s
    out, i = [], 0
    while True:
        j = s.find("/*", i)
        if j < 0:
            out.append(s[i:])
            break
        out.append(s[i:j])
        out.append(" ")
        k = s.find("*/", j + 2)
        if k < 0:
            break
        i = k + 2
    return "".join(out)


def normalize_style(style):
    """A style value as the matcher reads it: comments out, escapes decoded,
    lowercase, one space at most and none around : ; , ( ) / !"""
    s = str(style or "")[:STYLE_MAX]
    s = css_unescape(strip_comments(s)).lower()
    return _PUNCT.sub(r"\1", _SPACES.sub(" ", s)).strip()


def declarations(norm):
    """[(property, value, important)] of a normalized declaration block."""
    out = []
    for part in norm.split(";", 400)[:400]:
        prop, sep, val = part.partition(":")
        if not sep:
            continue
        prop, val = prop.strip(), val.strip()
        imp = val.endswith("!important")
        if imp:
            val = val[:-10].strip()
        if prop and val:
            out.append((prop, val, imp))
    return out


def number(v):
    """A plain number or a percentage as a fraction ('5%' is 0.05), else None."""
    m = _NUM.fullmatch(v)
    if not m or m.group(2) not in ("", "%"):
        return None
    n = float(m.group(1))
    if not math.isfinite(n):
        return None
    return n / 100 if m.group(2) == "%" else n


ABS_PX = {"px": 1.0, "pt": 4 / 3, "pc": 16.0, "in": 96.0, "cm": 96 / 2.54, "mm": 96 / 25.4, "q": 96 / 101.6,
          "rem": 16.0, "vw": 10.0, "vh": 8.0, "vmin": 8.0, "vmax": 10.0}
REL = {"em": 1.0, "ex": 0.5, "ch": 0.5, "%": 0.01}


def length(v):
    """(number, unit) of a CSS length ('0' is (0, 'px')), else None."""
    m = _NUM.fullmatch(v)
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2)
    if not math.isfinite(n):
        return None
    if unit == "":
        return (0.0, "px") if n == 0 else None
    return (n, unit) if unit in ABS_PX or unit in REL else None


def to_px(v, rel=16.0):
    """px of a CSS length, em and % against rel; None when unknown."""
    ln = length(v)
    if ln is None:
        return None
    n, unit = ln
    return n * ABS_PX[unit] if unit in ABS_PX else n * REL[unit] * rel


def tokens(v):
    """The space-separated parts of a value, a function call kept whole."""
    out, depth, cur = [], 0, []
    for ch in v[:2000]:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == " " and depth == 0:
            if cur:
                out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


# ---------- colors ----------

NAMED = {"white": (255, 255, 255), "black": (0, 0, 0), "red": (255, 0, 0), "lime": (0, 255, 0),
         "green": (0, 128, 0), "blue": (0, 0, 255), "yellow": (255, 255, 0), "cyan": (0, 255, 255),
         "aqua": (0, 255, 255), "magenta": (255, 0, 255), "fuchsia": (255, 0, 255), "gray": (128, 128, 128),
         "grey": (128, 128, 128), "silver": (192, 192, 192), "maroon": (128, 0, 0), "olive": (128, 128, 0),
         "navy": (0, 0, 128), "purple": (128, 0, 128), "teal": (0, 128, 128), "orange": (255, 165, 0),
         "whitesmoke": (245, 245, 245), "snow": (255, 250, 250), "ivory": (255, 255, 240),
         "ghostwhite": (248, 248, 255), "floralwhite": (255, 250, 240), "linen": (250, 240, 230),
         "lightgray": (211, 211, 211), "lightgrey": (211, 211, 211), "gainsboro": (220, 220, 220),
         "darkgray": (169, 169, 169), "darkgrey": (169, 169, 169), "dimgray": (105, 105, 105),
         "dimgrey": (105, 105, 105), "windowtext": (0, 0, 0), "window": (255, 255, 255),
         "canvas": (255, 255, 255), "canvastext": (0, 0, 0)}
UNKNOWN_COLOR = "unknown"   # currentcolor, var(), a system color: a color, value unknown


def _channel(v):
    if v.endswith("%"):
        n = number(v)
        return None if n is None else n * 255
    n = number(v)
    return n


def _alpha(v):
    n = number(v)
    return None if n is None else min(1.0, max(0.0, n))


def _hue(v):
    m = _NUM.fullmatch(v)
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2)
    scale = {"": 1, "deg": 1, "turn": 360, "rad": 180 / math.pi, "grad": 0.9}.get(unit)
    return None if scale is None or not math.isfinite(n) else (n * scale) % 360


def parse_color(v):
    """(r, g, b, alpha) of a CSS color, UNKNOWN_COLOR for one whose value is
    not known here, or None for no color."""
    if v == "transparent":
        return (0, 0, 0, 0.0)
    if v in NAMED:
        return NAMED[v] + (1.0,)
    if v.startswith("#"):
        h = v[1:]
        if not re.fullmatch(r"[0-9a-f]{3,8}", h) or len(h) not in (3, 4, 6, 8):
            return None
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        return rgb + ((int(h[6:8], 16) / 255) if len(h) == 8 else 1.0,)
    m = re.fullmatch(r"(rgba?|hsla?)\(([^()]{0,200})\)", v)
    if m:
        args = [a for a in _ARGS.split(m.group(2)) if a]
        if "var(" in v or not 3 <= len(args) <= 4:
            return UNKNOWN_COLOR
        alpha = _alpha(args[3]) if len(args) == 4 else 1.0
        if alpha is None:
            return UNKNOWN_COLOR
        if m.group(1).startswith("rgb"):
            ch = [_channel(a) for a in args[:3]]
            if None in ch:
                return UNKNOWN_COLOR
            return tuple(int(min(255, max(0, c))) for c in ch) + (alpha,)
        h, s, li = _hue(args[0]), number(args[1]), number(args[2])
        if None in (h, s, li):
            return UNKNOWN_COLOR
        s, li = min(1, max(0, s)), min(1, max(0, li))
        c = (1 - abs(2 * li - 1)) * s
        x = c * (1 - abs((h / 60) % 2 - 1))
        r, g, b = [(c, x, 0), (x, c, 0), (0, c, x), (0, x, c), (x, 0, c), (c, 0, x)][int(h // 60) % 6]
        mm = li - c / 2
        return tuple(int(round((q + mm) * 255)) for q in (r, g, b)) + (alpha,)
    if v in ("currentcolor", "inherit", "initial", "unset", "revert") or v.startswith(("var(", "color(", "lab(",
                                                                                       "lch(", "oklab(", "oklch(",
                                                                                       "hwb(", "color-mix(",
                                                                                       "light-dark(")):
        return UNKNOWN_COLOR
    return None


def _same_color(a, b):
    return all(abs(a[i] - b[i]) <= 4 for i in range(3)) and a[3] >= 0.5 and b[3] >= 0.5


def background_color(v):
    """The color a background value paints, None when it has an image or none."""
    if "url(" in v or "gradient(" in v or "image(" in v or "element(" in v:
        return None
    for t in tokens(v):
        c = parse_color(t)
        if c is not None:
            return c
    return None


# ---------- per-property hiding ----------

DISPLAY_SHOWN = frozenset(
    "block inline inline-block flex inline-flex grid inline-grid table inline-table table-row table-cell "
    "table-row-group table-header-group table-footer-group table-column table-column-group table-caption "
    "list-item contents flow flow-root run-in ruby ruby-base ruby-text ruby-base-container ruby-text-container "
    "initial unset revert revert-layer -webkit-box -webkit-inline-box -ms-flexbox -ms-inline-flexbox "
    "-webkit-flex -webkit-inline-flex -ms-grid -ms-inline-grid math".split())
FONT_KEYWORDS = {"xx-small": 9.0, "x-small": 10.0, "small": 13.0, "medium": 16.0, "large": 18.0, "x-large": 24.0,
                 "xx-large": 32.0, "xxx-large": 48.0, "caption": 13.0, "icon": 13.0, "menu": 13.0,
                 "message-box": 13.0, "small-caption": 11.0, "status-bar": 12.0, "initial": 16.0}
FONT_SIZE_TOKEN = re.compile(r"(?:^| )((?:\d{1,12}(?:\.\d{0,12})?|\.\d{1,12})(?:px|pt|pc|in|cm|mm|q|em|rem|ex|ch|%"
                             r"|vw|vh|vmin|vmax)|0|xx-small|x-small|small|medium|large|x-large|xx-large|xxx-large"
                             r"|smaller|larger)(?=/| |$)")
OFFSET_PROPS = frozenset("left right top bottom text-indent margin-left margin-top margin-right margin-bottom "
                         "margin-inline-start margin-inline-end margin-block-start margin-block-end "
                         "inset-inline-start inset-inline-end inset-block-start inset-block-end".split())
OFFSET_SHORTHANDS = frozenset({"margin", "inset", "inset-inline", "inset-block", "margin-inline", "margin-block"})
BOX_DIMS = ("height", "width", "max-height", "max-width")
# Groups whose hiding no descendant can undo; visibility, color and font-size are inherited and can be set back.
SOFT = frozenset({"visibility", "color", "font-size"})


def font_entry(v):
    """('abs', px) or ('rel', factor) of a font size, else None."""
    if v in FONT_KEYWORDS:
        return ("abs", FONT_KEYWORDS[v])
    if v == "smaller":
        return ("rel", 0.833)
    if v == "larger":
        return ("rel", 1.2)
    if v in ("inherit", "unset", "revert"):
        return ("rel", 1.0)
    ln = length(v)
    if ln is None:
        return None
    n, unit = ln
    return ("abs", n * ABS_PX[unit]) if unit in ABS_PX else ("rel", n * REL[unit])


def font_shorthand(v):
    if v in FONT_KEYWORDS or v in ("inherit", "unset", "revert"):
        return font_entry(v)
    m = FONT_SIZE_TOKEN.search(v)
    return font_entry(m.group(1)) if m else None


def font_px(entry, parent_px):
    kind, n = entry
    return n if kind == "abs" else parent_px * n


def _nums(args):
    return [number(a) for a in args]


def transform_hides(v):
    """True for a transform that collapses or moves its element out of sight,
    False for one that does not, None for an unreadable value."""
    if v in ("none", "initial", "unset", "inherit", "revert"):
        return False
    found = False
    for m in _FUNC.finditer(v):
        found = True
        name, args = m.group(1), [a for a in _ARGS.split(m.group(2)) if a]
        if name in ("scale", "scalex", "scaley", "scale3d"):
            ns = _nums(args[:2] if name == "scale3d" else args)
            if any(n is not None and abs(n) < 0.01 for n in ns):
                return True
        elif name == "matrix" and len(args) == 6:
            a, b, c, d = _nums(args[:4])
            if None not in (a, b, c, d) and abs(a * d - b * c) < 1e-4:
                return True
        elif name in ("translate", "translatex", "translatey", "translate3d"):
            for a in args[:2]:
                px = to_px(a)
                if px is not None and (px <= OFFSET_MIN or px >= 5000):
                    return True
    return False if found else None


def scale_hides(v):
    if v == "none":
        return False
    ns = _nums(v.split(" ")[:3])
    if not ns or None in ns:
        return None
    return any(abs(n) < 0.01 for n in ns[:2])


def translate_hides(v):
    if v == "none":
        return False
    pxs = [to_px(a) for a in v.split(" ")[:3]]
    if not pxs or None in pxs:
        return None
    return any(p <= OFFSET_MIN or p >= 5000 for p in pxs[:2])


def clip_hides(v):
    if v == "auto":
        return False
    m = re.fullmatch(r"rect\(([^()]{0,200})\)", v)
    if not m:
        return None
    parts = [a for a in _ARGS.split(m.group(1)) if a]
    if len(parts) != 4:
        return None
    t, r, b, left = [None if a == "auto" else to_px(a) for a in parts]
    return bool((t is not None and b is not None and b <= t) or (r is not None and left is not None and r <= left))


def _inset_pct(a):
    """An inset as a percentage of the box: a % value as is, a length of
    5000px or more as 100, any other length as 0 (the box size is unknown)."""
    ln = length(a)
    if ln is None:
        return None
    n, unit = ln
    if unit == "%":
        return n
    return 100.0 if unit in ABS_PX and n * ABS_PX[unit] >= 5000 else 0.0


def clip_path_hides(v):
    if v == "none":
        return False
    m = re.fullmatch(r"(inset|circle|ellipse|polygon)\(([^()]{0,400})\)(?: [a-z-]{1,20})?", v)
    if not m:
        return None
    name, body = m.group(1), m.group(2)
    if name == "inset":
        vals = [_inset_pct(a) for a in body.split(" round")[0].split(" ") if a][:4]
        if not vals or None in vals:
            return None
        if len(vals) == 1:
            vals = vals * 4
        elif len(vals) == 2:
            vals = vals * 2
        elif len(vals) == 3:
            vals = vals + [vals[1]]
        t, r, b, left = vals
        return t + b >= 100 or r + left >= 100
    if name in ("circle", "ellipse"):
        radii = body.split(" at ")[0].split(" ")
        rs = [to_px(a) for a in radii if a and a not in ("closest-side", "farthest-side")]
        return bool(rs) and any(r is not None and r <= 0 for r in rs)
    points = [p.strip() for p in body.split(",")]
    if points and points[0] in ("nonzero", "evenodd"):
        points = points[1:]
    return len(points) >= 1 and len(set(points)) == 1


def offset_hides(v):
    """True for an offset far enough left or up to put text out of sight."""
    if v in ("auto", "initial", "unset", "inherit", "revert", "normal"):
        return False
    ln = length(v)
    if ln is None:
        return None
    n, unit = ln
    if unit == "%":
        return n <= -1000
    px = n * ABS_PX[unit] if unit in ABS_PX else n * REL[unit] * 16
    return px <= OFFSET_MIN


def style_groups(decls):
    """{group: (hides, important, extra)} for the declarations of one block
    that bear on whether its element's text is seen. A group is a property
    (or a pair read together: color with its background, a small box with
    its overflow); hides False means the block sets it back to visible.
    Within the block the last valid declaration wins, unless an earlier one
    is !important; an invalid value is ignored, as a browser does."""
    vals = {}
    for prop, val, imp in decls:
        if prop in vals and vals[prop][1] and not imp:
            continue
        vals.pop(prop, None)  # keep the order of the last declarations (font-size, then a later font)
        vals[prop] = (val, imp)
    g = {}

    def put(group, hides, imp, extra=None):
        if hides is None:
            return
        if group in g and g[group][1] and not imp:
            return
        g[group] = (bool(hides), imp, extra)

    for prop, (v, imp) in vals.items():
        if prop == "display":
            put("display", True if v == "none" else (False if v in DISPLAY_SHOWN or v.split(" ")[0] in DISPLAY_SHOWN
                                                       else None), imp)
        elif prop == "visibility":
            put("visibility", True if v in ("hidden", "collapse") else (
                False if v in ("visible", "initial", "unset", "revert") else None), imp)
        elif prop == "content-visibility":
            put("content-visibility", True if v == "hidden" else (False if v in ("visible", "auto") else None), imp)
        elif prop == "opacity":
            n = number(v)
            put("opacity", None if n is None else n < ALPHA_MIN, imp)
        elif prop == "filter":
            m = re.search(r"(?<![a-z-])opacity\(([^()]{0,40})\)", v)
            n = number(m.group(1)) if m else None
            put("filter", bool(m) and n is not None and n < ALPHA_MIN, imp)
        elif prop in ("font-size", "font"):
            e = font_entry(v) if prop == "font-size" else font_shorthand(v)
            if e is not None:
                put("font-size", font_px(e, 16.0) <= FONT_MIN, imp, e)
        elif prop == "transform":
            put("transform", transform_hides(v), imp)
        elif prop == "scale":
            put("scale", scale_hides(v), imp)
        elif prop == "translate":
            put("translate", translate_hides(v), imp)
        elif prop == "clip":
            put("clip", clip_hides(v), imp)
        elif prop in ("clip-path", "-webkit-clip-path"):
            put("clip-path", clip_path_hides(v), imp)
        elif prop in OFFSET_PROPS:
            put(prop, offset_hides(v), imp)
        elif prop in OFFSET_SHORTHANDS:
            hs = [offset_hides(t) for t in v.split(" ")[:4]]
            put(prop, True if True in hs else (False if hs and None not in hs else None), imp)

    # a box of 1px or less that hides its overflow
    ov = [vals[p][0].split(" ") for p in ("overflow", "overflow-x", "overflow-y") if p in vals]
    ov_imp = any(vals[p][1] for p in ("overflow", "overflow-x", "overflow-y") if p in vals)
    ov_hide = any(t in ("hidden", "clip") for parts in ov for t in parts)
    ov_show = bool(ov) and not ov_hide
    for dim in BOX_DIMS:
        if dim in vals:
            v, imp = vals[dim]
            px = to_px(v) if v not in ("auto", "none", "initial", "unset") else 1e9
            if px is None and v.endswith("-content"):
                px = 1e9
            if px is None:
                continue
            if px <= 1 and ov_hide:
                put("box-" + dim, True, imp or ov_imp)
            elif px > 1:
                put("box-" + dim, False, imp)
        if ov_show:
            put("box-" + dim, False, ov_imp)

    # the color the text is painted in: transparent, or the same as its own background
    paint = None
    for p in ("-webkit-text-fill-color", "color"):
        if p in vals:
            c = parse_color(vals[p][0])
            if c is not None:
                paint, paint_imp = c, vals[p][1]
                break
    if paint is not None:
        if paint == UNKNOWN_COLOR:
            put("color", False, paint_imp)
        else:
            painted = any("text" in vals.get(p, ("",))[0].split(" ")
                          for p in ("background-clip", "-webkit-background-clip")) \
                or vals.get("text-shadow", ("none",))[0] not in ("none", "initial", "unset") \
                or (vals.get("-webkit-text-stroke-width", ("0",))[0] not in ("0", "0px")) \
                or (vals.get("-webkit-text-stroke", ("0",))[0] not in ("0", "0px", "none"))
            if paint[3] < ALPHA_MIN:
                put("color", not painted, paint_imp)
            else:
                back = None
                if "background-color" in vals:
                    back = parse_color(vals["background-color"][0])
                elif "background" in vals:
                    back = background_color(vals["background"][0])
                same = isinstance(back, tuple) and _same_color(paint, back)
                put("color", same, paint_imp)
    return g


_STYLE_CACHE = {}


def inline_groups(style):
    """style_groups of an inline style attribute (cached: pages repeat them)."""
    key = str(style or "")
    got = _STYLE_CACHE.get(key)
    if got is None:
        got = style_groups(declarations(normalize_style(key)))
        if len(key) <= 2000:  # the runner reads hundreds of pages in one process: the cache stays small
            if len(_STYLE_CACHE) >= STYLE_CACHE_MAX:
                _STYLE_CACHE.clear()
            _STYLE_CACHE[key] = got
    return got


def hidden_style(style):
    """True for an inline style that hides its own text (read on its own, a
    parent's font size taken as 16px)."""
    return any(e[0] for e in inline_groups(style).values())


def is_hidden(attrs, hidden_classes=()):
    """An element's own attributes hide it: the hidden attribute, an inline
    style that hides, or a screen-reader-only class (or one of
    hidden_classes). aria-hidden alone does not count: that text is on the
    screen (only a screen reader skips it), so it stays posting text, where
    AI_TEXT reads it."""
    for k, v in attrs:
        key = k.lower()
        if key == "hidden":
            return True
        if key == "style" and hidden_style(v):
            return True
        # one set lookup per class name: a page whose <style> hides 10,000 classes stays linear
        if key == "class" and any(c in HIDDEN_CLASSES or c in hidden_classes for c in (v or "").lower().split()):
            return True
    return False


# ---------- selectors ----------

_IDENT_CH = r"(?:[-\w]|[^\x00-\x7f]|\\[0-9a-f]{1,6} ?|\\[^0-9a-f\n])"
_IDENT = r"(?:-{0,2}(?:[_a-z]|[^\x00-\x7f]|\\[0-9a-f]{1,6} ?|\\[^0-9a-f\n])" + _IDENT_CH + r"{0,200})"
_SEL = re.compile(
    r"(?P<comb> ?[>+~] ?)|(?P<ws> )|(?P<star>\*)(?!\|)|#(?P<id>" + _IDENT_CH + r"{1,200})|\.(?P<cls>" + _IDENT
    + r")|\[ ?(?P<an>" + _IDENT + r") ?(?:(?P<op>[~|^$*]?=) ?(?P<av>\"(?:[^\"\\]|\\.){0,200}\""
    r"|'(?:[^'\\]|\\.){0,200}'|" + _IDENT_CH + r"{1,200}) ?(?P<fl>[is])? ?)?\]|(?P<pe>::?)(?P<pn>" + _IDENT
    + r")(?P<pa>\()?|(?P<tag>" + _IDENT + ")")
NEVER = frozenset("hover focus active focus-within focus-visible target target-within visited checked indeterminate "
                  "default invalid user-invalid user-valid placeholder-shown autofill -webkit-autofill playing paused "
                  "fullscreen modal popover-open open closed current past future drop picture-in-picture "
                  "-moz-focusring".split())
LEGACY_ELEMENTS = frozenset({"before", "after", "first-line", "first-letter", "selection", "placeholder", "marker"})
EMPTY = frozenset()
_NO_ATTRS = {}  # shared by every element with no attributes; never written to


def _unq(s):
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1]
    return css_unescape(s)


def _paren_end(s, i):
    """Index of the ')' closing the '(' before s[i], or -1."""
    depth = 1
    while i < len(s):
        c = s[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        elif c in "\"'":
            j = s.find(c, i + 1)
            if j < 0:
                return -1
            i = j
        i += 1
    return -1


def _nth(arg):
    """(a, b) of an an+b formula, else None."""
    arg = arg.replace(" ", "")
    if arg == "odd":
        return (2, 1)
    if arg == "even":
        return (2, 0)
    m = re.fullmatch(r"([+-]?\d{0,6})n([+-]\d{1,6})?|([+-]?\d{1,6})", arg)
    if not m:
        return None
    if m.group(3) is not None:
        return (0, int(m.group(3)))
    a = m.group(1)
    a = 1 if a in ("", "+") else (-1 if a == "-" else int(a))
    return (a, int(m.group(2) or 0))


def _nth_ok(n, ab):
    a, b = ab
    if a == 0:
        return n == b
    return (n - b) % a == 0 and (n - b) // a >= 0


def split_list(text):
    """A selector list split at its top-level commas."""
    if not any(c in text for c in "([\"'"):
        return text.split(",")
    out, depth, cur, quote = [], 0, [], ""
    for c in text:
        if quote:
            if c == quote:
                quote = ""
        elif c in "\"'":
            quote = c
        elif c in "([":
            depth += 1
        elif c in ")]":
            depth = max(0, depth - 1)
        elif c == "," and depth == 0:
            out.append("".join(cur))
            cur = []
            continue
        cur.append(c)
    out.append("".join(cur))
    return out


def parse_compound_list(text, depth):
    """[(compound, specificity)] of a list of compound selectors (the
    argument of :not, :is, :where), else None."""
    out = []
    for part in split_list(text)[:16]:
        got = parse_selector(part, depth + 1)
        if not got or len(got[0]) != 1:
            return None
        out.append((got[0][0], got[2]))
    return out or None


def parse_selector(text, depth=0):
    """(compounds, combinators, specificity) of one complex selector, or
    None for one this matcher does not read (a pseudo-element, a namespace,
    a pseudo-class a page's first render cannot decide). combinators[i] sits
    between compounds[i] and compounds[i + 1]."""
    s = _SPACES.sub(" ", text.lower()).strip()
    if not s or len(s) > SELECTOR_MAX or depth > 3:
        return None
    compounds, combs = [], []
    cur = None
    spec = [0, 0, 0]
    pending = None
    i = 0
    while i < len(s):
        m = _SEL.match(s, i)
        if not m:
            return None
        i = m.end()
        if m.group("comb") is not None or m.group("ws") is not None:
            if cur is None:
                if m.group("comb") is not None and not compounds:
                    return None  # a leading combinator: only valid nested, read with '&'
                continue
            compounds.append(cur)
            cur = None
            pending = m.group("comb").strip() if m.group("comb") is not None else " "
            continue
        if cur is None:
            if compounds:
                combs.append(pending or " ")
            cur = [None, None, [], [], []]
        if m.group("star") is not None:
            continue
        if m.group("id") is not None:
            cur[1] = css_unescape(m.group("id"))
            spec[0] += 1
        elif m.group("cls") is not None:
            cur[2].append(css_unescape(m.group("cls")))
            spec[1] += 1
        elif m.group("an") is not None:
            if "|" in m.group("an"):
                return None
            op = m.group("op") or ""
            cur[3].append((css_unescape(m.group("an")), op, _unq(m.group("av") or ""), True))
            spec[1] += 1
        elif m.group("pe") is not None:
            name = css_unescape(m.group("pn"))
            arg = None
            if m.group("pa") is not None:
                end = _paren_end(s, i)
                if end < 0:
                    return None
                arg, i = s[i:end].strip(), end + 1
            if m.group("pe") == "::" or name in LEGACY_ELEMENTS:
                return None
            if name in ("not", "is", "matches", "where", "-webkit-any", "-moz-any") and arg is not None:
                lst = parse_compound_list(arg, depth)
                if lst is None:
                    return None
                cur[4].append(("not" if name == "not" else "is", tuple(c for c, _ in lst)))
                if name != "where":
                    best = max((sp for _, sp in lst), default=(0, 0, 0))
                    spec = [spec[0] + best[0], spec[1] + best[1], spec[2] + best[2]]
                continue
            spec[1] += 1
            if name in NEVER and arg is None:
                cur[4].append(("never",))
            elif name in ("first-child", "first-of-type") and arg is None:
                cur[4].append(("nth" if name == "first-child" else "nth-type", (0, 1)))
            elif name in ("nth-child", "nth-of-type") and arg is not None and _nth(arg):
                cur[4].append(("nth" if name == "nth-child" else "nth-type", _nth(arg)))
            elif name == "root" and arg is None:
                cur[4].append(("root",))
            elif name in ("link", "any-link", "-webkit-any-link") and arg is None:
                cur[4].append(("link",))
            elif name == "defined" and arg is None:
                cur[4].append(("defined",))
            else:
                return None
        elif m.group("tag") is not None:
            if cur[0] is not None or cur[1] or cur[2] or cur[3] or cur[4]:
                return None
            cur[0] = css_unescape(m.group("tag"))
            spec[2] += 1
        else:
            return None
    if cur is None:
        return None
    compounds.append(cur)
    if len(compounds) > COMPOUNDS_MAX or len(combs) != len(compounds) - 1:
        return None
    out = tuple((c[0], c[1], tuple(c[2]), tuple(c[3]), tuple(c[4])) for c in compounds)
    return out, tuple(combs), tuple(spec)


def compound_key(c):
    """The index key of a compound: its id, else a class, an attribute name,
    its tag, or '*'."""
    tag, id_, classes, attrs, _ = c
    if id_:
        return "#" + id_
    if classes:
        return "." + classes[0]
    if attrs:
        return "[" + attrs[0][0]
    if tag:
        return "t:" + tag
    return "*"


def match_compound(c, d):
    """Does compound c match element descriptor d (tag, id, classes, attrs, n, n_type, is_root)?"""
    tag, id_, classes, attrs, pseudos = c
    dtag, did, dclasses, dattrs = d[0], d[1], d[2], d[3]
    if tag is not None and tag != dtag:
        return False
    if id_ is not None and id_ != did:
        return False
    for cl in classes:
        if cl not in dclasses:
            return False
    for name, op, val, _ in attrs:
        if name not in dattrs:
            return False
        if op:
            v = dattrs[name].lower()
            if op == "=":
                ok = v == val
            elif op == "~=":
                ok = val in v.split()
            elif op == "|=":
                ok = v == val or v.startswith(val + "-")
            elif op == "^=":
                ok = bool(val) and v.startswith(val)
            elif op == "$=":
                ok = bool(val) and v.endswith(val)
            else:
                ok = bool(val) and val in v
            if not ok:
                return False
    for p in pseudos:
        k = p[0]
        if k == "never":
            return False
        if k == "not":
            if any(match_compound(x, d) for x in p[1]):
                return False
        elif k == "is":
            if not any(match_compound(x, d) for x in p[1]):
                return False
        elif k == "nth":
            if not _nth_ok(d[4], p[1]):
                return False
        elif k == "nth-type":
            if not _nth_ok(d[5], p[1]):
                return False
        elif k == "root":
            if not d[6]:
                return False
        elif k == "link":
            if dtag not in ("a", "area") or "href" not in dattrs:
                return False
        elif k == "defined":
            if "-" in dtag:
                return False
    return True


class Rule:
    __slots__ = ("comps", "combs", "prio", "groups", "sib")

    def __init__(self, comps, combs, spec, order, groups):
        self.comps, self.combs, self.groups = comps, combs, groups
        self.prio = (spec, order)
        self.sib = ()


class Frame:
    __slots__ = ("desc", "hard", "vis", "color", "font", "hidden", "n", "types", "sib_first", "last_cids",
                 "prev_cids")

    def __init__(self, desc, hard, vis, color, font, prev_cids):
        self.desc, self.hard, self.vis, self.color, self.font = desc, hard, vis, color, font
        self.hidden = hard or vis or color or font <= FONT_MIN
        self.n, self.types = 0, None  # children so far; tag -> children of that tag (made when needed)
        self.sib_first = None     # compound id -> child number of the first child that matched it
        self.last_cids = EMPTY    # compound ids the latest child matched
        self.prev_cids = prev_cids  # compound ids this element's previous sibling matched


class Hider:
    """Hidden-text state while an HTML page is read: TextExtractor calls
    add_css for each <style> element's text, enter for each element it puts
    on its stack (its attributes) and leave for each one it takes off.
    hidden is True while the innermost open element's text is hidden."""

    def __init__(self):
        self.frames = []
        self.top = Frame(("#document", None, EMPTY, {}, 1, 1, False), False, False, False, 16.0, EMPTY)
        self.hide, self.reset = {}, {}   # compound key -> [Rule]
        self.sib = {}                    # compound key -> [(compound id, compound)] left of + or ~
        self.sib_ids = {}
        self.order = 0
        self.rules = 0
        self.css_read = 0
        self.budget = MATCH_BUDGET
        self._sel_cache = {}
        self.hidden = False  # the innermost open element's text is hidden (kept by enter and leave)

    # ----- style sheets -----

    def add_css(self, css):
        """Read the rules of one <style> element (D3: at most CSS_BLOCK_MAX
        characters of it and CSS_PAGE_MAX per page)."""
        if self.frames and self.frames[-1].desc[0] == "style":
            media = self.frames[-1].desc[3].get("media", "").strip().lower()
            if media and media not in ("all", "screen", "only screen"):
                return
        room = min(CSS_BLOCK_MAX, CSS_PAGE_MAX - self.css_read)
        if room <= 0 or not css:
            return
        css = strip_comments(str(css)[:room])
        self.css_read += len(css)
        stack = [["list", None]]
        pos = 0
        for m in _CSS_TOK.finditer(css):
            t = m.group()
            if t[0] in "\"'":
                continue
            chunk, pos = css[pos:m.start()], m.end()
            top = stack[-1]
            if top[0] == "skip":
                top[1] += 1 if t == "{" else (-1 if t == "}" else 0)
                if top[1] == 0:
                    stack.pop()
                continue
            if t == ";":
                if top[0] == "rule":
                    top[2].append(chunk + ";")
                continue
            if t == "{":
                prelude = chunk.strip()
                parents = top[1]
                if len(stack) > NEST_MAX or len(prelude) > SELECTOR_MAX * SELECTORS_PER_RULE:
                    stack.append(["skip", 1])
                elif prelude.startswith("@"):
                    name = re.match(r"@([-a-z]{0,40})", prelude.lower()).group(1)
                    if name in ("supports", "layer", "scope", "document", "-moz-document", "starting-style"):
                        stack.append(["list", parents])
                    else:  # @media, @container, @keyframes, @font-face, @page ...: not on every screen, or no text
                        stack.append(["skip", 1])
                else:
                    sels = [x.strip() for x in split_list(prelude)][:SELECTORS_PER_RULE]
                    if parents:
                        sels = [(c.replace("&", p) if "&" in c else p + " " + c) for p in parents for c in sels]
                        sels = sels[:SELECTORS_PER_RULE]
                    elif any("&" in x for x in sels):
                        sels = [x.replace("&", ":root") for x in sels]
                    stack.append(["rule", [x for x in sels if x], []])
            elif t == "}":
                if top[0] == "rule":
                    top[2].append(chunk)
                    self._add_rule(top[1], "".join(top[2]))
                if len(stack) > 1:
                    stack.pop()
        tail = css[pos:]
        while len(stack) > 1:
            top = stack.pop()
            if top[0] == "rule":
                top[2].append(tail)
                tail = ""
                self._add_rule(top[1], "".join(top[2]))

    def _compile(self, sel):
        got = self._sel_cache.get(sel)
        if got is None:
            got = parse_selector(sel) or False
            if len(self._sel_cache) < 20_000:
                self._sel_cache[sel] = got
        return got or None

    def _add_rule(self, sels, body):
        if self.rules >= RULES_MAX or not sels:
            return
        groups = style_groups(declarations(normalize_style(body)))
        if not groups:
            return
        hides = any(e[0] for e in groups.values())
        shows = any(not e[0] for e in groups.values())
        for sel in sels:
            got = self._compile(sel)
            if got is None:
                continue
            comps, combs, spec = got
            self.order += 1
            self.rules += 1
            rule = Rule(comps, combs, spec, self.order, groups)
            key = compound_key(comps[-1])
            if hides:
                self.hide.setdefault(key, []).append(rule)
            if shows:
                self.reset.setdefault(key, []).append(rule)
            if "+" in combs or "~" in combs:
                ids = []
                for i, cb in enumerate(combs):
                    if cb in ("+", "~"):
                        c = comps[i]
                        cid = self.sib_ids.get(c)
                        if cid is None:
                            cid = self.sib_ids[c] = len(self.sib_ids)
                            self.sib.setdefault(compound_key(c), []).append((cid, c))
                        ids.append(cid)
                    else:
                        ids.append(None)
                rule.sib = tuple(ids)

    # ----- elements -----

    def _test(self, c, d):
        self.budget -= 1
        return match_compound(c, d)

    def _level(self, s):
        """(element frame or None for the one being entered, its parent frame) at stack level s."""
        parent = self.frames[s - 1] if s >= 1 else self.top
        return (self.frames[s] if s < len(self.frames) else None), parent

    def _match(self, rule, d, fail):
        """Does rule's selector match the element being entered (descriptor
        d)? Right to left over the open elements; when the budget is spent the
        answer is fail (True for a rule that hides: fail closed)."""
        comps, combs = rule.comps, rule.combs
        if not self._test(comps[-1], d):
            return False
        n = len(self.frames)
        pos = [n]
        for j in range(len(comps) - 2, -1, -1):
            if self.budget <= 0:
                return fail
            c, cb = comps[j], combs[j]
            nxt = []
            if cb == ">":
                for s in pos:
                    if s >= 1 and self._test(c, self.frames[s - 1].desc):
                        nxt.append(s - 1)
            elif cb in ("+", "~"):
                cid = rule.sib[j]
                for s in pos:
                    frame, parent = self._level(s)
                    if cb == "+":
                        prev = parent.last_cids if frame is None else frame.prev_cids
                        ok = cid in prev
                    else:
                        num = d[4] if frame is None else frame.desc[4]
                        ok = (parent.sib_first or {}).get(cid, num) < num
                    if ok:
                        nxt.append(s)
            else:
                top = max(pos) - 1
                for i in range(top, -1, -1):
                    if self._test(c, self.frames[i].desc):
                        nxt.append(i)
                        if j == 0 or combs[j - 1] == " ":
                            break
                    if self.budget <= 0:
                        return fail
            if not nxt:
                return False
            pos = nxt
        return True

    def enter(self, tag, attrs):
        """An element opens; True when its text is hidden."""
        parent = self.frames[-1] if self.frames else self.top
        if attrs:
            a = {}
            for k, v in attrs[:CLASSES_MAX * 4]:
                k = str(k).lower()
                if k not in a:
                    a[k] = "" if v is None else str(v)
            classes = frozenset(a["class"].lower().split()[:CLASSES_MAX]) if a.get("class") else EMPTY
            id_ = a.get("id", "").strip().lower() or None
        else:
            a, classes, id_ = _NO_ATTRS, EMPTY, None
        tag = str(tag).lower()
        parent.n += 1
        types = parent.types
        if types is None:
            types = parent.types = {}
        nt = types[tag] = types.get(tag, 0) + 1
        d = (tag, id_, classes, a, parent.n, nt, not self.frames)
        prev_cids = parent.last_cids
        rules = self.hide or self.reset or self.sib
        if not attrs and not rules and tag != "dialog":  # most elements of most pages: the parent's state
            f = Frame(d, parent.hard, parent.vis, parent.color, parent.font, prev_cids)
            self.frames.append(f)
            self.hidden = f.hidden
            return f.hidden
        hard = parent.hard or "hidden" in a or bool(classes & HIDDEN_CLASSES) \
            or (tag == "dialog" and "open" not in a)
        own = {}
        if "style" in a:
            for g, e in inline_groups(a["style"]).items():
                own[g] = (e, (e[1], 1, (0, 0, 0), 0))
        keys = self._keys(d) if rules else ()
        if not hard and (self.hide or self.reset):
            hard = self._cascade(d, keys, own, parent)
        if not hard and own:
            hard = any(e[0] for g, (e, _) in own.items() if g not in SOFT)
        if own:
            vis = own["visibility"][0][0] if "visibility" in own else parent.vis
            color = own["color"][0][0] if "color" in own else parent.color
            font = font_px(own["font-size"][0][2], parent.font) if "font-size" in own else parent.font
        else:
            vis, color, font = parent.vis, parent.color, parent.font
        if self.sib:
            cids = set()
            for key in keys:
                for cid, c in self.sib.get(key, ()):
                    if self.budget > 0 and self._test(c, d):
                        cids.add(cid)
                        if parent.sib_first is None:
                            parent.sib_first = {}
                        parent.sib_first.setdefault(cid, parent.n)
            parent.last_cids = frozenset(cids) if cids else EMPTY
        f = Frame(d, hard, vis, color, font, prev_cids)
        self.frames.append(f)
        self.hidden = f.hidden
        return f.hidden

    def leave(self):
        """The innermost open element closes."""
        if self.frames:
            self.frames.pop()
            self.hidden = self.frames[-1].hidden if self.frames else False

    @staticmethod
    def _keys(d):
        tag, id_, classes, a = d[0], d[1], d[2], d[3]
        keys = ["*", "t:" + tag]
        if id_:
            keys.append("#" + id_)
        keys += ["." + c for c in classes]
        keys += ["[" + n for n in list(a)[:CLASSES_MAX]]
        return keys

    def _cascade(self, d, keys, own, parent):
        """Merge the matching <style> rules into own (group -> (entry,
        priority)) by the cascade; True when the budget ran out while a rule
        that hides could still match (fail closed)."""
        need = set()   # groups whose winner must be found: hidden by a rule, inline, or inherited
        for g, (e, _) in own.items():
            if e[0]:
                need.add(g)
        if parent.vis:
            need.add("visibility")
        if parent.color:
            need.add("color")
        if parent.font <= FONT_MIN:
            need.add("font-size")
        tested = set()
        hide, reset = self.hide, self.reset
        for key in keys:
            rules = hide.get(key)
            if not rules:
                continue
            for rule in rules:
                if self.budget <= 0:
                    return True
                tested.add(id(rule))
                if self._match(rule, d, True):
                    self._consider(own, rule)
                    for g, e in rule.groups.items():
                        if e[0]:
                            need.add(g)
        if need:
            for key in keys:
                rules = reset.get(key)
                if not rules:
                    continue
                for rule in rules:
                    if self.budget <= 0:
                        return False
                    if id(rule) in tested or need.isdisjoint(rule.groups):
                        continue
                    if self._match(rule, d, False):
                        self._consider(own, rule)
        return False

    @staticmethod
    def _consider(own, rule):
        """Each group the rule sets wins over what own holds when its priority
        (important, origin, specificity, order) is higher."""
        spec, order = rule.prio
        for g, e in rule.groups.items():
            pr = (e[1], 0, spec, order)
            if g not in own or pr > own[g][1]:
                own[g] = (e, pr)


# CSS tokens the block reader stops at: braces, semicolons, and strings read whole.
_CSS_TOK = re.compile(r"[{};]|\"(?:[^\"\\\n]|\\.){0,2000}\"?|'(?:[^'\\\n]|\\.){0,2000}'?")


# ---------- AI-addressed text ----------

# Look-alike letters a reader takes for Latin ones (Cyrillic, Greek, small capitals, a few Latin forms).
CONFUSABLES = {
    "\u0430": "a", "\u0410": "A", "\u0432": "b", "\u0412": "B", "\u0435": "e", "\u0415": "E", "\u0451": "e",
    "\u0401": "E", "\u043a": "k", "\u041a": "K", "\u043c": "m", "\u041c": "M", "\u043d": "h", "\u041d": "H",
    "\u043e": "o", "\u041e": "O", "\u0440": "p", "\u0420": "P", "\u0441": "c", "\u0421": "C", "\u0442": "t",
    "\u0422": "T", "\u0443": "y", "\u0423": "Y", "\u0445": "x", "\u0425": "X", "\u0455": "s", "\u0405": "S",
    "\u0456": "i", "\u0406": "I", "\u0457": "i", "\u0458": "j", "\u0408": "J", "\u0501": "d", "\u051b": "q",
    "\u051d": "w", "\u04bb": "h", "\u04ae": "Y", "\u04af": "y", "\u04cf": "l", "\u0433": "r", "\u0413": "G",
    "\u0438": "u", "\u043f": "n", "\u044c": "b", "\u0261": "g",
    "\u03b1": "a", "\u0391": "A", "\u03b2": "b", "\u0392": "B", "\u03b5": "e", "\u0395": "E", "\u0396": "Z",
    "\u03b7": "n", "\u0397": "H", "\u03b9": "i", "\u0399": "I", "\u03ba": "k", "\u039a": "K", "\u03bc": "u",
    "\u039c": "M", "\u03bd": "v", "\u039d": "N", "\u03bf": "o", "\u039f": "O", "\u03c1": "p", "\u03a1": "P",
    "\u03c4": "t", "\u03a4": "T", "\u03c5": "u", "\u03a5": "Y", "\u03c7": "x", "\u03a7": "X", "\u03b3": "y",
    "\u03c9": "w", "\u03c2": "s", "\u03f2": "c", "\u03f3": "j", "\u03ba": "k",
    "\u1d00": "a", "\u0299": "b", "\u1d04": "c", "\u1d05": "d", "\u1d07": "e", "\ua730": "f", "\u0262": "g",
    "\u029c": "h", "\u026a": "i", "\u1d0a": "j", "\u1d0b": "k", "\u029f": "l", "\u1d0d": "m", "\u0274": "n",
    "\u1d0f": "o", "\u1d18": "p", "\u01eb": "q", "\u0280": "r", "\ua731": "s", "\u1d1b": "t", "\u1d1c": "u",
    "\u1d20": "v", "\u1d21": "w", "\u028f": "y", "\u1d22": "z",
    "\u0131": "i", "\u0237": "j", "\u0585": "o", "\u057d": "u", "\u0261": "g",
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'", "\u02bc": "'", "\u00b4": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2033": '"', "\u00ab": '"', "\u00bb": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2015": "-", "\u2212": "-",
}


class _Fold(dict):
    """str.translate table for NFKD text, filled as characters are met:
    combining marks and invisible format characters go, look-alikes map to
    Latin, everything else stays."""

    def __missing__(self, cp):
        c = chr(cp)
        r = "" if unicodedata.category(c) in ("Mn", "Me", "Cf") else CONFUSABLES.get(c, c)
        if len(self) < 100_000:
            self[cp] = r
        return r


_FOLD = _Fold({i: chr(i) for i in range(128)})


def fold(text):
    """The text the AI-text patterns read: compatibility forms (fullwidth and
    mathematical letters, ligatures, NFKC's set) decomposed, accents and
    invisible format characters removed, and look-alike letters mapped to
    Latin. ASCII text comes back unchanged."""
    text = text if isinstance(text, str) else str(text or "")
    return text if text.isascii() else unicodedata.normalize("NFKD", text).translate(_FOLD)


_EARLY =(r"(?:previous|previously given|prior|above|earlier|preceding|foregoing|other|system|original|initial"
          r"|old|former|existing|given)")
_ORDERS = (r"(?:instructions?|prompts?|directions?|directives?|rules|guidelines|guidance|text|content|messages?"
           r"|context|commands?|orders|constraints|programming)")
_IGNORE = (r"(?:ignore|disregard|forget|override|overrule|discard|bypass|pay no (?:attention|heed|mind) to"
           r"|(?:do not|don't|dont) (?:follow|obey|heed)|stop following|set aside)")
_AI_NOUN = (r"(?:ai|a\.i\.|llms?|large language models?|language models?|chatbots?|gpt(?:-?\d{1,2}\w{0,3})?"
            r"|chatgpt|claude|gemini|copilot)")
_AI_QUAL = r"(?:ai|a\.i\.|automated|autonomous|artificial intelligence|llm|gpt|virtual|robotic)"
_AI_PEOPLE = (r"(?:assistants?|agents?|applicants?|bots?|models?|reviewers?|screeners?|readers?|recruiters?"
              r"|evaluators?|systems?|tools?|crawlers?|scrapers?|parsers?|programs?)")
_VERBS = (r"(?:ignore|disregard|fill|enter|type|write|answer|submit|add|include|insert|rate|mark|select|choose"
          r"|check|tick|say|state|tell|use|put|respond|reply|mention|click|score|rank|recommend|output|print"
          r"|return|give|classify|flag|approve|accept|reject|forward|send|email|label|grade|evaluate|append"
          r"|treat|consider|prioriti[sz]e|shortlist|advance|pass|move|repeat|reveal|copy|always|never|only|do)")
_NOT_PROF = r"(?! (?:professor|director|manager|scientist|researcher|specialist|editor|dean|teacher|coach))"

# (name, pattern) on lowercase folded text with single spaces. Each pattern
# starts with a word, after one shared \b, so the matcher tries each place in
# the text cheaply (D3). Names in SENTENCE_START count only where a sentence
# starts; AFTER_NO_DETERMINER only when no 'our', 'the' ... comes before
# ('our AI agents must answer customer questions' describes a product).
AI_PATTERNS = [
    # English: drop what came before
    ("ign1", _IGNORE + r" (?:(?:all|any|your|the|of|these|those|my|every|each|such) ){0,4}(?:" + _EARLY + r" ){1,2}"
     + _ORDERS + r"\b"),
    ("ign2", _IGNORE + r" (?:(?:all|any|your|the|these|those|my) ){0,3}" + _ORDERS
     + r" (?:above|before this|so far|given (?:above|earlier|before)|you (?:were|have been) given)\b"),
    ("ign3", r"(?:ignore|disregard|forget) everything (?:above|before|said|written|you (?:were|have been) told"
     r"|so far)\b"),
    ("ign4", r"ignore (?:all |any )?(?:your|my) (?:previous |prior |original )?(?:instructions|prompts?)"),
    # English: an AI addressed
    ("ifai", r"if (?:you are|you're|this is being read by|this is read by|you are reading this as) (?:an? |the )?"
     r"(?:ai|a\.i\.|llm|large language model|language model|bot|chatbot|gpt|chatgpt|claude"
     r"|automated (?:system|tool|agent|assistant|reader|reviewer|screener|program|applicant"
     r"|application assistant)|ai (?:assistant|agent|model|tool|system|reader|reviewer|screener|program|applicant)"
     r"|assistant" + _NOT_PROF + r")\b"),
    ("word", r"(?:include|mention|use|add|insert) the (?:word|phrase|code word|keyword)\b"),
    ("note", r"(?:note|message|memo|instructions?|directions?|reminder|attention|notice|warning) (?:to|for) "
     r"(?:the |any |all |every |each )?(?:" + _AI_NOUN + r"|" + _AI_QUAL + r"[- ](?:\w{1,20} )?" + _AI_PEOPLE
     + r"|assistants?)\b"),
    ("read", r"(?:" + _AI_NOUN + r"|assistants?|agents?|bots?) (?:that is |who is |which is |currently |now )?"
     r"(?:reading|processing|parsing|reviewing|screening|scanning|summari[sz]ing|analy[sz]ing|evaluating) this\b"),
    ("now1", r"you are now (?:a|an|the|in|acting|operating|playing|my|our|no longer|free|unrestricted|jailbroken"
     r"|developer|dan)\b"),
    ("now2", r"from now on,? you (?:are|will|must|should|shall)\b"),
    # Spanish
    ("es1", r"(?:ignora|ignore|ignorar|ignoren|olvida|olvide|olviden|olvidar|omite|omita|descarta|descarte"
     r"|no hagas caso (?:a|de)|haz caso omiso (?:a|de)) (?:(?:todas|todos|las|los|tus|sus|esas|estas|cualquier) )"
     r"{0,3}(?:instrucciones|instruccion|indicaciones|ordenes|reglas|directrices|mensajes|prompts?) "
     r"(?:anteriores|previas|precedentes|de arriba|originales|del sistema|dadas anteriormente)\b"),
    ("es2", r"si (?:eres|es|usted es) (?:una? )?(?:ia|i\.a\.|inteligencia artificial|modelo de lenguaje|llm|chatbot"
     r"|bot|asistente (?:de ia|virtual|automatizado)|agente (?:de ia|automatizado))\b"),
    ("es3", r"nota (?:para|a) (?:la |el |los |las |cualquier )?(?:ia|inteligencia artificial|modelos? de lenguaje"
     r"|llms?|chatbots?|asistentes? de ia|agentes? de ia)\b"),
    ("es4", r"(?:ahora|a partir de ahora) eres\b|eres ahora\b"),
    # French
    ("fr1", r"(?:ignore|ignorez|ignorer|oublie|oubliez|oublier|ne tiens pas compte|ne tenez pas compte"
     r"|fais abstraction|faites abstraction) (?:de |des |d')?(?:(?:toutes|tous|les|tes|vos|ces|l') ?){0,3}"
     r"(?:instructions?|consignes?|directives?|regles|indications|messages?|prompts?) (?:precedentes?"
     r"|anterieures?|ci-dessus|initiales?|d'avant|qui precedent|du systeme)\b"),
    ("fr2", r"si (?:tu es|vous etes) (?:une? )?(?:ia|i\.a\.|intelligence artificielle|modele de langage|llm|chatbot"
     r"|robot|bot|assistant (?:ia|virtuel|automatise)|agent (?:ia|conversationnel|automatise))\b"),
    ("fr3", r"note (?:a|pour|aux) (?:l' ?|les |la )?(?:ia|intelligence artificielle|modeles? de langage|llms?"
     r"|assistants? (?:ia|virtuels?)|agents? ia)\b"),
    ("fr4", r"(?:tu es|vous etes) (?:maintenant|desormais|a present)\b"),
    # German
    ("de1", r"(?:ignoriere|ignorier|ignorieren sie|ignoriert|vergiss|vergessen sie|missachte|missachten sie"
     r"|uberspringe) (?:(?:alle|samtliche|die|deine|ihre|jegliche) ){0,3}(?:vorherigen|vorhergehenden|bisherigen"
     r"|vorigen|obigen|fruheren|vorangegangenen|vorstehenden|ursprunglichen|alten) (?:anweisungen|instruktionen"
     r"|befehle|anordnungen|regeln|vorgaben|prompts?|hinweise)\b"),
    ("de2", r"(?:wenn|falls) (?:du|sie) (?:eine? )?(?:ki|k\.i\.|kunstliche intelligenz|sprachmodell|llm|chatbot|bot"
     r"|ki-assistent\w{0,3}|ki-agent\w{0,3}) (?:bist|sind)\b"),
    ("de3", r"hinweis (?:an|fur) (?:die |alle |jede )?(?:ki|kunstliche intelligenz|sprachmodelle?|llms?"
     r"|ki-\w{1,20})\b"),
    ("de4", r"(?:du bist|sie sind) (?:jetzt|nun|ab sofort|ab jetzt) (?:ein|eine|der|die|im|kein|keine)\b"),
    # Portuguese
    ("pt1", r"(?:ignore|ignora|ignorar|ignorem|esqueca|esquece|esquecer|esquecam|desconsidere|desconsidera"
     r"|desconsiderar|despreze) (?:(?:todas|todos|as|os|suas|tuas|essas|estas|quaisquer) ){0,3}(?:instrucoes"
     r"|instrucao|orientacoes|ordens|regras|diretrizes|mensagens|prompts?) (?:anteriores|previas|acima|originais"
     r"|do sistema|dadas anteriormente)\b"),
    ("pt2", r"se (?:voce|tu) (?:e|for|es) (?:uma? )?(?:ia|i\.a\.|inteligencia artificial|modelo de linguagem|llm"
     r"|chatbot|bot|robo|assistente (?:de ia|virtual)|agente (?:de ia|virtual))\b"),
    ("pt3", r"nota (?:para|aos?|as) (?:a |o |as |os |qualquer )?(?:ia|inteligencia artificial|modelos? de linguagem"
     r"|llms?|assistentes? de ia|agentes? de ia)\b"),
    ("pt4", r"(?:agora voce e|voce agora e|a partir de agora voce e|voce e agora)\b"),
    # English: AI agents told what to do (not after 'our', 'the' ...)
    ("must", _AI_QUAL + r"[- ](?:\w{1,20}[- ]){0,2}?(?:agents?|assistants?|applicants?|bots?|screeners?|reviewers?"
     r"|readers?) (?:must|should|shall|needs? to|ha(?:ve|s) to|(?:are|is) (?:required|instructed|expected|asked"
     r"|told|directed) to|are to|is to) (?:(?:now|always|also|only|instead|then|simply|just) ){0,2}" + _VERBS + r"\b"),
    # English: a sentence that opens by addressing an AI, or a 'system:' line
    ("adr1", r"(?:(?:dear|hey|hi|hello|attention|ok|okay) )?(?:" + _AI_NOUN + r" (?:assistants?|agents?|models?"
     r"|tools?|systems?|applicants?|reviewers?|screeners?)|claude|chatgpt|gpt-?\d{0,2}|gemini|copilot|llms?"
     r"|language models?|chatbots?|" + _AI_QUAL + r" (?:\w{1,20} )?" + _AI_PEOPLE + r") ?[,:] ?(?:please )?"
     r"(?:also |now )?" + _VERBS + r"\b"),
    ("adr2", r"(?:(?:dear|hey|hi|hello|ok|okay) )?(?:assistant|agent|bot|model|robot) ?[,:] ?(?:please )?"
     r"(?:also |now )?" + _VERBS + r"\b"),
    ("sys", r"system(?: (?:prompt|message|note|notice|instructions?|override|update|command|alert))? ?: ?\S"),
]
SENTENCE_START = frozenset({"adr1", "adr2", "sys"})
AFTER_NO_DETERMINER = frozenset({"must"})
_DETERMINERS = ("our ", "the ", "their ", "these ", "such ", "your ")
_TOKENS = r"<\|(?:im_start|im_end|system|endoftext)\|>|\[/?inst\]|\[system\]"
# The scan runs without named groups (sre skips an alternative whose first
# letter differs only when no group opens first: twice as fast); at a hit the
# named form says which pattern matched.
_AI_RX = re.compile(r"\b(?:" + "|".join(p for _, p in AI_PATTERNS) + r")|" + _TOKENS)
_AI_NAMED = re.compile(r"\b(?:" + "|".join(f"(?P<{n}>{p})" for n, p in AI_PATTERNS) + r")|(?P<tok>" + _TOKENS + ")")
_NAMES_RX = re.compile(r"\b(ai|llm|large language model|chatgpt|claude|gemini|copilot|gpt-?\d)\b")
_WS = re.compile(r"\s+")


def _sentence_start(low, i):
    if i == 0 or low[i - 1] == "\n":
        return True
    return low[i - 1] == " " and (i == 1 or low[i - 2] in ".!?;:])\"'\n")


def _scan(rx, low, named=None):
    """Matches of rx in lowercase folded text; with named (rx's named form),
    the place conditions are checked here. Each place in the text is tried
    at most twice: linear."""
    pos, n = 0, len(low)
    while pos <= n:
        m = rx.search(low, pos)
        if m is None:
            return
        if named is not None:
            m = named.match(low, m.start()) or m
            g = m.lastgroup
            if (g in SENTENCE_START and not _sentence_start(low, m.start())) or (
                    g in AFTER_NO_DETERMINER and low.endswith(_DETERMINERS, max(0, m.start() - 7), m.start())):
                pos = m.start() + 1
                continue
        yield m
        pos = max(m.end(), m.start() + 1)


def _lower(folded):
    low = folded.lower()
    # str.lower keeps the length of folded text (fold already took U+0130 apart); never trust it blindly
    return low if len(low) == len(folded) else "".join(c.lower()[:1] or c for c in folded)


class FoldedPattern:
    """A pattern read on fold(text), lowercased, with runs of whitespace as
    one space: search returns the first match, finditer every match, on that
    prepared text."""

    def __init__(self, rx, named=None):
        self.rx, self.named = rx, named
        self.pattern = rx.pattern

    @staticmethod
    def prepare(text):
        return _lower(_WS.sub(" ", fold(text)))

    def search(self, text):
        return next(_scan(self.rx, self.prepare(text), self.named), None)

    def finditer(self, text):
        return _scan(self.rx, self.prepare(text), self.named)


AI_TEXT = FoldedPattern(_AI_RX, _AI_NAMED)
AI_NAMES = FoldedPattern(_NAMES_RX)

SENTENCE_ENDS = (". ", "! ", "? ", "\n\n")


def sentence_span(flat, start, end):
    """(a, b): the whole sentence around flat[start:end], at most
    AI_SENTENCE_MAX characters kept around the match. Each search looks at
    most AI_SENTENCE_MAX characters each way (D3)."""
    lo, hi = max(0, start - AI_SENTENCE_MAX), min(len(flat), end + AI_SENTENCE_MAX)
    a = max([flat.rfind(c, lo, start) for c in SENTENCE_ENDS] + [lo - 1]) + 1
    ends = [i for i in (flat.find(c, end, hi) for c in SENTENCE_ENDS) if i >= 0]
    b = (min(ends) + 1) if ends else hi
    if b - a > AI_SENTENCE_MAX:
        a = max(a, start - 60)
        b = min(b, a + AI_SENTENCE_MAX)
    return a, b


def ai_sentence(flat, start, end):
    """The whole sentence around flat[start:end] (at most AI_SENTENCE_MAX
    characters, kept around the match), so the AI-TEXT line says what the
    text asks for."""
    a, b = sentence_span(flat, start, end)
    return _WS.sub(" ", flat[a:b]).strip()


def ai_lines(description, hidden_texts=()):
    """Text addressed to an AI: each whole sentence of the description once,
    found across hard line wraps (on the folded text, so a sentence is shown
    in its folded form); then hidden text that names an AI or addresses
    one, as it was. At most AI_FOUND_MAX sentences: one is enough to park
    the job (G11)."""
    found, seen = [], set()
    # paragraphs stay apart; the line breaks inside one fold to spaces
    paras = re.split(r"\n\s*\n", fold(description))
    flat = "\n\n".join(_WS.sub(" ", x).strip() for x in paras)
    taken = 0  # end of the last sentence shown: a later match inside it adds nothing (linear on repeats)
    for m in _scan(_AI_RX, _lower(flat), _AI_NAMED):
        if m.start() < taken:
            continue
        a, taken = sentence_span(flat, m.start(), m.end())
        sent = _WS.sub(" ", flat[a:taken]).strip()
        if sent and sent not in seen:
            seen.add(sent)
            found.append(sent)
            if len(found) >= AI_FOUND_MAX:
                return found
    for ln in hidden_texts:
        if len(found) >= AI_FOUND_MAX:
            break
        if (AI_TEXT.search(ln) or AI_NAMES.search(ln)) and ln not in seen:
            seen.add(ln)
            found.append(ln)
    return found
