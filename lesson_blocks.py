"""V31.108 Interactive Lesson Builder - the lesson block model.

A lesson is metadata (title, unit, order, status - already on the existing
`lessons` document) plus an ordered list of reusable content blocks:

    lesson
      metadata
      blocks[] -> {id, type, order, content}

This module is deliberately free of Flask and Firebase so the rules can be
unit-tested on their own. It owns four things:

  1. a block-type REGISTRY (`register_block_type`) - adding a new block type
     later means writing one BlockType subclass and registering it; no route
     or renderer plumbing changes;
  2. input validation + sanitising for every supported block type;
  3. safe RENDER data - what a student is allowed to receive, always derived
     from the stored content at read time (never trusted from the database);
  4. the private answer-key split and server-side grading for Practice/Quiz.

Security model
--------------
* Text is plain text. HTML is stripped on the way in and the front end only
  ever inserts it with textContent, so stored content can never execute.
* Every URL must be https, host-only (no credentials, no IP literals, no
  localhost) and free of characters that could break out of an attribute.
* Embeds are NOT arbitrary URLs. Video, GeoGebra, PhET and Canva are parsed
  into a fixed embed URL on a fixed host; a GeoGebra block stores only the
  material id. Anything else becomes a plain outbound link or is rejected.
* Quiz/Practice answer keys and explanations are split out of the block into
  a separate server-only record (`lessonBlockKeys`) and never appear in the
  student-facing render data - even if a teacher wrote them into the stored
  block directly, because render data is built from an allow-list.
"""
import json
import re
import secrets
import unicodedata
from urllib.parse import parse_qs, urlsplit, urlunsplit, unquote

BLOCK_SCHEMA_VERSION = 1
MAX_BLOCKS = 60
MAX_STORED_BYTES = 700_000          # a Firestore document is limited to 1 MiB
MAX_URL_LENGTH = 2000
MAX_TEXT_LENGTH = 10_000
MAX_LATEX_LENGTH = 2_000
MAX_QUESTIONS = 30
OPTION_LETTERS = "ABCDEF"


class BlockValidationError(ValueError):
    """Raised for any teacher-supplied block that fails validation. `index`
    is the block's position in the submitted list (None for list-level
    problems); routes turn this into a 400 with a readable message."""

    def __init__(self, message, index=None, field=None):
        super().__init__(message)
        self.message = message
        self.index = index
        self.field = field

    def to_dict(self):
        out = {"error": self.message}
        if self.index is not None:
            out["blockIndex"] = self.index
        if self.field:
            out["field"] = self.field
        return out


# --------------------------------------------------------------------------
# text sanitising
# --------------------------------------------------------------------------
_DANGEROUS_ELEMENT_RE = re.compile(
    r"<\s*(script|style|iframe|object|embed|template|noscript|svg|math|applet|textarea|select|frameset|frame)\b"
    r"[^>]*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
# Tag-shaped tokens that are removed from prose. Deliberately narrower than
# "anything between < and >" so maths such as "if a<b and c>d" or "x<y>z" survives:
#   1. a bare, known HTML element: <b> </b> <br/> <p>
#   2. ANY element that carries attributes (name=value): <a href=..>, <x onerror=..>
# Dangerous elements (script, style, iframe, svg ...) are removed separately,
# with their contents, whatever they contain.
_KNOWN_TAGS = "a abbr address article aside audio b base bdi bdo blockquote body br button canvas caption center cite code col colgroup data dd del details dfn dialog div dl dt em embed fieldset figcaption figure font footer form frame frameset h1 h2 h3 h4 h5 h6 head header hr html i iframe img input ins kbd label legend li link main map mark math menu meta nav noscript object ol optgroup option output p param picture pre progress q s samp script section select small source span strike strong style sub summary sup svg table tbody td template textarea tfoot th thead time title tr track u ul var video wbr"
_TAG_RE = re.compile(
    r"</?(?:%s)\s*/?>|</?[A-Za-z][A-Za-z0-9:-]*[\s/]+[^<>]*=[^<>]*>" % "|".join(_KNOWN_TAGS.split()),
    re.IGNORECASE,
)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_OPEN_DANGEROUS_RE = re.compile(
    r"<\s*/?\s*(script|style|iframe|object|embed|template|noscript|svg|math|applet|textarea|select|frameset|frame|"
    r"link|meta|base|form|input|button|img|video|audio|source|body|html|head)\b[^>]*>?",
    re.IGNORECASE,
)


def _strip_controls(value, keep_newlines=True):
    """Drop control and invisible-format characters (including bidi
    overrides). Newlines/tabs are kept, or turned into spaces when
    keep_newlines is False."""
    out = []
    value = value.replace("\r\n", "\n")
    for ch in value:
        if ch in "\n\t\r":
            out.append(("\n" if ch != "\t" else "\t") if keep_newlines else " ")
        elif unicodedata.category(ch) in ("Cc", "Cf") and ch not in ("\u200d", "\u200c"):
            continue
        else:
            out.append(ch)
    return "".join(out)


def sanitize_text(value, limit=MAX_TEXT_LENGTH, allow_newlines=True):
    """Plain-text sanitiser for teacher-authored prose.

    Removes HTML comments, dangerous elements (with their contents), any
    remaining tag-shaped token, control characters and bidi overrides, then
    normalises whitespace and truncates. Non-string input becomes ''.
    Text that merely contains `<` or `>` as comparison signs ("x < 5") is
    preserved because only tag-shaped tokens are removed.
    """
    if not isinstance(value, str):
        return ""
    text = _COMMENT_RE.sub("", value)
    previous = None
    while previous != text:              # nested/re-assembled markup
        previous = text
        text = _DANGEROUS_ELEMENT_RE.sub("", text)
        text = _TAG_RE.sub("", text)
    text = _OPEN_DANGEROUS_RE.sub("", text)  # unterminated <script ... etc.
    text = _strip_controls(text, keep_newlines=allow_newlines)
    if not allow_newlines:
        text = re.sub(r"\s+", " ", text)
    else:
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()[:limit]


# --------------------------------------------------------------------------
# URL validation
# --------------------------------------------------------------------------
_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_URL_FORBIDDEN_CHARS = set('<>"\'` \\{}|^\t\r\n')
_BLOCKED_HOST_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".corp", ".intranet")


def validate_external_url(raw, *, allowed_hosts=None, field="url"):
    """Return a normalised https URL or raise BlockValidationError.

    * https only (javascript:, data:, http:, ftp:, file:, blob: ... rejected)
    * no embedded credentials, no non-443 port
    * host must be a public-looking DNS name: no IP literals, no localhost,
      no single-label / internal suffixes
    * no characters that could terminate an HTML attribute
    * optional allowed_hosts: exact host or subdomain of one of them
    """
    if not isinstance(raw, str):
        raise BlockValidationError("A link is required.", field=field)
    url = raw.strip()
    if not url:
        raise BlockValidationError("A link is required.", field=field)
    if len(url) > MAX_URL_LENGTH:
        raise BlockValidationError("That link is too long.", field=field)
    if any(ch in _URL_FORBIDDEN_CHARS or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        raise BlockValidationError("That link contains characters that are not allowed.", field=field)
    if not url.lower().startswith("https://"):
        raise BlockValidationError("Links must start with https://", field=field)
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise BlockValidationError("That link is not a valid web address.", field=field)
    if parts.scheme.lower() != "https" or not parts.netloc:
        raise BlockValidationError("Links must start with https://", field=field)
    if "@" in parts.netloc:
        raise BlockValidationError("Links with a username or password are not allowed.", field=field)
    if port not in (None, 443):
        raise BlockValidationError("Links with a custom port are not allowed.", field=field)
    host = (parts.hostname or "").lower().rstrip(".")
    try:
        host.encode("ascii")
    except UnicodeEncodeError:
        raise BlockValidationError("Use the ASCII (punycode) form of that web address.", field=field)
    if (not host or host == "localhost" or host.startswith("[") or ":" in host
            or _IPV4_RE.match(host) or host.isdigit() or host.startswith("0x")
            or host.endswith(_BLOCKED_HOST_SUFFIXES)):
        raise BlockValidationError("That web address is not allowed.", field=field)
    labels = host.split(".")
    if len(labels) < 2 or not all(_HOST_LABEL_RE.match(l) for l in labels) or not re.match(r"^[a-z]{2,}$|^xn--[a-z0-9-]+$", labels[-1]):
        raise BlockValidationError("That web address is not valid.", field=field)
    if allowed_hosts is not None and not any(host == h or host.endswith("." + h) for h in allowed_hosts):
        raise BlockValidationError("That website is not supported for this block.", field=field)
    # Reject encoded control characters / encoded angle brackets in the path.
    decoded = unquote(parts.path + "?" + parts.query)
    if any(ord(ch) < 32 or ch in "<>" for ch in decoded):
        raise BlockValidationError("That link contains characters that are not allowed.", field=field)
    return urlunsplit(("https", host, parts.path or "/", parts.query, ""))


def safe_web_link(raw):
    """V31.108 audit fix. Scheme allow-list for the LEGACY lesson `url` field.

    Returns the trimmed link when it is a plain http(s) web link, else "".
    Deliberately looser than validate_external_url() (which is for new blocks):
    older lessons may hold http:// links or single-label hosts, and those must
    keep working. What it blocks is what made the field dangerous: javascript:,
    data:, vbscript:, file:, protocol-relative and credential-bearing links.
    """
    if not isinstance(raw, str):
        return ""
    url = raw.strip()
    if not url or len(url) > MAX_URL_LENGTH:
        return ""
    if any(ch in _URL_FORBIDDEN_CHARS or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        return ""
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return ""
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc or not host or "@" in parts.netloc:
        return ""
    return url


def _host(url):
    return (urlsplit(url).hostname or "").lower()


def _path(url):
    return urlsplit(url).path


# --------------------------------------------------------------------------
# provider parsers (fixed embed hosts)
# --------------------------------------------------------------------------
_YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,80}$")
_GEOGEBRA_ID_RE = re.compile(r"^[A-Za-z0-9]{5,20}$")
_MEDIA_EXT = (".mp4", ".webm", ".ogg", ".ogv", ".m4v")
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif")
_IMAGE_HOSTS = ("res.cloudinary.com", "firebasestorage.googleapis.com", "storage.googleapis.com", "lh3.googleusercontent.com")
_YOUTUBE_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")
# Hosts a simulation may be shown INSIDE the page from. Everything else is an
# outbound link only. Extend this tuple deliberately, one reviewed host at a time.
EMBEDDABLE_SIMULATION_HOSTS = ("phet.colorado.edu",)
GEOGEBRA_HOSTS = ("geogebra.org",)
CANVA_HOSTS = ("canva.com",)
# The only hosts the front end will ever put in an <iframe>. Kept here so the
# Python tests and lesson_builder.js are checked against one list.
IFRAME_HOSTS = (
    "www.youtube-nocookie.com", "player.vimeo.com", "drive.google.com",
    "www.geogebra.org", "phet.colorado.edu", "www.canva.com",
)


def parse_video(raw):
    url = validate_external_url(raw)
    host, path = _host(url), _path(url)
    segs = [s for s in path.split("/") if s]
    if any(host == h or host.endswith("." + h) for h in _YOUTUBE_HOSTS):
        vid = ""
        if host.endswith("youtu.be") and segs:
            vid = segs[0]
        elif segs and segs[0] in ("embed", "shorts", "live", "v") and len(segs) > 1:
            vid = segs[1]
        else:
            vid = (parse_qs(urlsplit(url).query).get("v") or [""])[0]
        if not _YT_ID_RE.match(vid):
            raise BlockValidationError("That does not look like a valid YouTube video link.", field="url")
        return {"provider": "youtube", "videoId": vid, "embedUrl": f"https://www.youtube-nocookie.com/embed/{vid}"}
    if host in ("vimeo.com", "www.vimeo.com", "player.vimeo.com"):
        vid = next((s for s in reversed(segs) if s.isdigit()), "")
        if not vid or len(vid) > 12:
            raise BlockValidationError("That does not look like a valid Vimeo video link.", field="url")
        return {"provider": "vimeo", "videoId": vid, "embedUrl": f"https://player.vimeo.com/video/{vid}"}
    if host == "drive.google.com":
        if len(segs) >= 3 and segs[0] == "file" and segs[1] == "d" and _DRIVE_ID_RE.match(segs[2]):
            fid = segs[2]
            return {"provider": "drive", "fileId": fid, "embedUrl": f"https://drive.google.com/file/d/{fid}/preview"}
        raise BlockValidationError("Use a Google Drive file link (drive.google.com/file/d/...).", field="url")
    is_cloudinary_video = host == "res.cloudinary.com" and "/video/upload/" in path
    if is_cloudinary_video or path.lower().endswith(_MEDIA_EXT):
        return {"provider": "direct", "src": url}
    raise BlockValidationError(
        "Unsupported video source. Use YouTube, Vimeo, Google Drive, Cloudinary, or a direct .mp4/.webm link.", field="url")


def parse_image(raw):
    url = validate_external_url(raw)
    host, path = _host(url), _path(url)
    if any(host == h or host.endswith("." + h) for h in CANVA_HOSTS):
        m = re.match(r"^/design/([A-Za-z0-9_-]{6,40})/([A-Za-z0-9_-]{4,80})/(view|watch)/?$", path)
        if not m:
            raise BlockValidationError(
                "For Canva, paste the design's 'Share > View link', or upload the exported image instead.", field="url")
        return {"provider": "canva", "embedUrl": f"https://www.canva.com/design/{m.group(1)}/{m.group(2)}/view?embed"}
    if path.lower().endswith(_IMAGE_EXT) or host in _IMAGE_HOSTS:
        return {"provider": "image", "src": url}
    raise BlockValidationError(
        "That link is not an image. Use a .png/.jpg/.webp/.gif link, an uploaded image, or a Canva view link.", field="url")


def parse_geogebra(raw):
    """A GeoGebra block stores ONLY the public material id. Accepts the bare
    id or a geogebra.org material URL and reduces it to the id; every other
    input (including any applet code/JSON) is rejected."""
    if not isinstance(raw, str):
        raise BlockValidationError("A GeoGebra material id or link is required.", field="materialId")
    value = raw.strip()
    if not value:
        raise BlockValidationError("A GeoGebra material id or link is required.", field="materialId")
    if _GEOGEBRA_ID_RE.match(value):
        return value
    url = validate_external_url(value, allowed_hosts=GEOGEBRA_HOSTS, field="materialId")
    segs = [s for s in _path(url).split("/") if s]
    candidate = ""
    if len(segs) >= 2 and segs[0] in ("m", "classic", "graphing", "geometry", "3d", "cas", "scientific", "calculator", "spreadsheet", "probability", "notes"):
        candidate = segs[1]
    elif len(segs) >= 4 and segs[0] == "material" and segs[1] in ("iframe", "show") and segs[2] == "id":
        candidate = segs[3]
    if not _GEOGEBRA_ID_RE.match(candidate):
        raise BlockValidationError(
            "Could not find a GeoGebra material id in that link. Use the id (for example 'abcd1234') or a geogebra.org/m/<id> link.",
            field="materialId")
    return candidate


def parse_simulation(raw):
    url = validate_external_url(raw)
    host, path = _host(url), _path(url)
    provider = "phet" if host == "phet.colorado.edu" else "external"
    embeddable = (
        any(host == h for h in EMBEDDABLE_SIMULATION_HOSTS)
        and path.startswith("/sims/") and path.lower().endswith(".html")
    )
    return {"provider": provider, "url": url, "embeddable": embeddable}


# --------------------------------------------------------------------------
# helpers shared by block types
# --------------------------------------------------------------------------
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def new_id(prefix="b"):
    return f"{prefix}_{secrets.token_hex(6)}"


def _require_dict(content, field="content"):
    if not isinstance(content, dict):
        raise BlockValidationError("Block content must be an object.", field=field)
    return content


def _short(value, limit, field, required=False):
    text = sanitize_text(value, limit=limit, allow_newlines=False) if isinstance(value, str) else ""
    if required and not text:
        raise BlockValidationError("This field is required.", field=field)
    return text


def _int_in(value, lo, hi, default, field):
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise BlockValidationError("Must be a whole number.", field=field)
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise BlockValidationError("Must be a whole number.", field=field)
    if not lo <= n <= hi:
        raise BlockValidationError(f"Must be between {lo} and {hi}.", field=field)
    return n


# --------------------------------------------------------------------------
# block types
# --------------------------------------------------------------------------
class BlockType:
    """Base class. To add a block type: subclass, set `name`/`label`/
    `description`/`fields`, implement `clean` (teacher input -> public content
    [+ private keys]) and `render` (stored public content -> safe student
    data), then call register_block_type()."""
    name = ""
    label = ""
    description = ""
    fields = ()      # UI metadata only: [{"name","label","kind","required"}...]
    interactive = False

    def clean(self, content):
        """Return (public_content, private_or_None). Must raise
        BlockValidationError on bad input."""
        raise NotImplementedError

    def render(self, public):
        """Return safe render data for a student, built ONLY from `public`
        through an allow-list. Must raise BlockValidationError if the stored
        content is no longer valid."""
        raise NotImplementedError

    def references(self, public):
        """[(kind, id)] this block depends on that the route must authorize."""
        return []


def _field(name, label, kind="text", required=False, **extra):
    d = {"name": name, "label": label, "kind": kind, "required": required}
    d.update(extra)
    return d


class TextBlock(BlockType):
    name, label, description = "text", "Text", "A paragraph or short explanation (plain text)."
    fields = (_field("text", "Text", "textarea", True),)

    def clean(self, content):
        c = _require_dict(content)
        text = sanitize_text(c.get("text"), limit=MAX_TEXT_LENGTH)
        if not text:
            raise BlockValidationError("Write some text for this block.", field="text")
        return {"text": text}, None

    def render(self, public):
        return {"text": self.clean(public)[0]["text"]}


_LATEX_DENY_RE = re.compile(
    r"\\(?:href|url|includegraphics|html[A-Za-z]*|input|include|write|immediate|openin|openout|read|csname|"
    r"catcode|def|gdef|edef|xdef|let|newcommand|renewcommand|providecommand|newenvironment)(?![A-Za-z])")
_LATEX_HTML_RE = re.compile(
    r"<\s*/?\s*(script|style|iframe|object|embed|link|meta|base|form|input|button|img|svg|math|video|audio|source|body|html|template)\b",
    re.IGNORECASE)


class FormulaBlock(BlockType):
    name, label, description = "formula", "Formula", "A LaTeX formula, rendered with KaTeX."
    fields = (_field("latex", "LaTeX", "textarea", True), _field("caption", "Caption", "text"),
              _field("display", "Show on its own line", "boolean"))

    def clean(self, content):
        c = _require_dict(content)
        latex = c.get("latex")
        if not isinstance(latex, str):
            raise BlockValidationError("Enter a formula.", field="latex")
        latex = _strip_controls(latex, keep_newlines=True).strip()
        # A leading/trailing $ or \[ delimiter is added by the renderer, not the teacher.
        if not latex:
            raise BlockValidationError("Enter a formula.", field="latex")
        if len(latex) > MAX_LATEX_LENGTH:
            raise BlockValidationError(f"Formulas are limited to {MAX_LATEX_LENGTH} characters.", field="latex")
        if _LATEX_DENY_RE.search(latex):
            raise BlockValidationError("That formula uses a command that is not allowed (links, images or macros).", field="latex")
        if _LATEX_HTML_RE.search(latex):
            raise BlockValidationError("HTML is not allowed in formulas.", field="latex")
        if re.search(r"(?<!\\)\$", latex):
            raise BlockValidationError("Do not include $ signs - BMT adds the math delimiters itself.", field="latex")
        return {"latex": latex, "caption": _short(c.get("caption"), 200, "caption"),
                "display": bool(c.get("display", True))}, None

    def render(self, public):
        return self.clean(public)[0]


class ImageBlock(BlockType):
    name, label, description = "image", "Image", "An image or a Canva design (upload/export it, or paste its view link)."
    fields = (_field("url", "Image or Canva view link", "url", True), _field("alt", "Alt text", "text"),
              _field("caption", "Caption", "text"))

    def clean(self, content):
        c = _require_dict(content)
        url = validate_external_url(c.get("url"))
        parse_image(url)  # rejects anything that is neither an image nor a Canva view link
        return {"url": url, "alt": _short(c.get("alt"), 200, "alt"),
                "caption": _short(c.get("caption"), 300, "caption")}, None

    def render(self, public):
        c = self.clean(public)[0]
        info = parse_image(c["url"])
        out = {"provider": info["provider"], "alt": c["alt"], "caption": c["caption"]}
        out.update({k: v for k, v in info.items() if k in ("src", "embedUrl")})
        return out


class VideoBlock(BlockType):
    name, label, description = "video", "Video", "A YouTube, Vimeo, Google Drive, Cloudinary or direct video."
    fields = (_field("url", "Video link", "url", True), _field("title", "Title", "text"),
              _field("caption", "Caption", "text"))

    def clean(self, content):
        c = _require_dict(content)
        url = validate_external_url(c.get("url"))
        parse_video(url)  # rejects unsupported video sources
        return {"url": url, "title": _short(c.get("title"), 160, "title"),
                "caption": _short(c.get("caption"), 300, "caption")}, None

    def render(self, public):
        c = self.clean(public)[0]
        out = parse_video(c["url"])
        out.update({"title": c["title"], "caption": c["caption"]})
        return out


class PdfReadingBlock(BlockType):
    name, label, description = "pdf", "PDF / Reading", "A PDF or reading link, shown as an outbound resource card."
    fields = (_field("url", "PDF or reading link", "url", True), _field("title", "Title", "text", True),
              _field("notes", "Notes", "textarea"))

    def clean(self, content):
        c = _require_dict(content)
        url = validate_external_url(c.get("url"))
        return {"url": url, "title": _short(c.get("title"), 160, "title", required=True),
                "notes": sanitize_text(c.get("notes"), limit=2000)}, None

    def render(self, public):
        c = self.clean(public)[0]
        host, path = _host(c["url"]), _path(c["url"])
        is_pdf = path.lower().endswith(".pdf") or host in ("drive.google.com", "res.cloudinary.com",
                                                          "firebasestorage.googleapis.com", "storage.googleapis.com")
        return {"kind": "pdf" if is_pdf else "reading", "url": c["url"], "title": c["title"], "notes": c["notes"]}


class GeoGebraBlock(BlockType):
    name, label, description = "geogebra", "GeoGebra", "A GeoGebra material, referenced by its public material id."
    fields = (_field("materialId", "Material id or geogebra.org link", "text", True), _field("title", "Title", "text"),
              _field("height", "Height (px)", "number"))

    def clean(self, content):
        c = _require_dict(content)
        return {"materialId": parse_geogebra(c.get("materialId")), "title": _short(c.get("title"), 160, "title"),
                "height": _int_in(c.get("height"), 200, 900, 480, "height")}, None

    def render(self, public):
        c = self.clean(public)[0]
        mid = c["materialId"]
        return {"materialId": mid, "title": c["title"], "height": c["height"],
                "embedUrl": f"https://www.geogebra.org/material/iframe/id/{mid}",
                "openUrl": f"https://www.geogebra.org/m/{mid}"}


class SimulationBlock(BlockType):
    name, label, description = "simulation", "External simulation", "A simulation link (for example PhET) shown as a controlled resource."
    fields = (_field("url", "Simulation link", "url", True), _field("title", "Title", "text", True),
              _field("description", "Instructions", "textarea"))

    def clean(self, content):
        c = _require_dict(content)
        info = parse_simulation(c.get("url"))
        return {"url": info["url"], "title": _short(c.get("title"), 160, "title", required=True),
                "description": sanitize_text(c.get("description"), limit=1000)}, None

    def render(self, public):
        c = self.clean(public)[0]
        info = parse_simulation(c["url"])
        return {"provider": info["provider"], "url": info["url"], "embeddable": info["embeddable"],
                "title": c["title"], "description": c["description"]}


class AssignmentBlock(BlockType):
    name, label, description = "assignment", "Assignment", "Points the student to one of your existing assignments."
    fields = (_field("assignmentId", "Assignment", "assignment", True), _field("note", "Note", "text"))
    interactive = True

    def clean(self, content):
        c = _require_dict(content)
        aid = c.get("assignmentId")
        if not isinstance(aid, str) or not re.match(r"^[A-Za-z0-9_-]{1,150}$", aid.strip()):
            raise BlockValidationError("Choose one of your assignments.", field="assignmentId")
        return {"assignmentId": aid.strip(), "note": _short(c.get("note"), 300, "note")}, None

    def render(self, public):
        return self.clean(public)[0]

    def references(self, public):
        return [("assignment", public["assignmentId"])]


# ---- Practice / Quiz -------------------------------------------------------
def _clean_questions(raw, index_for_errors=None):
    """Shared by Practice and Quiz. Returns (public_questions, answers,
    explanations). Answer keys never enter public_questions."""
    if not isinstance(raw, list) or not raw:
        raise BlockValidationError("Add at least one question.", field="questions")
    if len(raw) > MAX_QUESTIONS:
        raise BlockValidationError(f"A block can hold at most {MAX_QUESTIONS} questions.", field="questions")
    public, answers, explanations, seen = [], {}, {}, set()
    for i, q in enumerate(raw):
        where = f"Question {i + 1}: "
        if not isinstance(q, dict):
            raise BlockValidationError(where + "is not valid.", field="questions")
        prompt = sanitize_text(q.get("prompt"), limit=1000)
        if not prompt:
            raise BlockValidationError(where + "write the question.", field="questions")
        qtype = str(q.get("type") or "mcq").strip().lower()
        if qtype not in ("mcq", "true_false"):
            raise BlockValidationError(where + "type must be multiple choice or true/false.", field="questions")
        answer = str(q.get("answer") if q.get("answer") is not None else "").strip()
        if qtype == "true_false":
            options = {"A": "True", "B": "False"}
            answer = {"true": "A", "t": "A", "false": "B", "f": "B"}.get(answer.lower(), answer.upper())
        else:
            raw_opts = q.get("options")
            if isinstance(raw_opts, list):
                raw_opts = {OPTION_LETTERS[j]: v for j, v in enumerate(raw_opts[:len(OPTION_LETTERS)])}
            if not isinstance(raw_opts, dict):
                raise BlockValidationError(where + "add answer options.", field="questions")
            options = {}
            for key, val in raw_opts.items():
                letter = str(key).strip().upper()
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    val = str(val)
                text = sanitize_text(val, limit=300, allow_newlines=False) if isinstance(val, str) else ""
                if letter in OPTION_LETTERS and len(letter) == 1 and text:
                    options[letter] = text
            if len(options) < 2:
                raise BlockValidationError(where + "give at least two answer options.", field="questions")
            options = dict(sorted(options.items()))
            answer = answer.upper()
        if answer not in options:
            raise BlockValidationError(where + "choose the correct answer.", field="questions")
        qid = q.get("id")
        qid = qid.strip() if isinstance(qid, str) and _ID_RE.match(qid.strip()) else new_id("q")
        if qid in seen:
            raise BlockValidationError(where + "duplicate question id.", field="questions")
        seen.add(qid)
        try:
            points = _int_in(q.get("points"), 1, 100, 1, "questions")
        except BlockValidationError as exc:
            raise BlockValidationError(where + "points " + exc.message[0].lower() + exc.message[1:], field="questions")
        public.append({"id": qid, "prompt": prompt, "type": qtype, "options": options, "points": points})
        answers[qid] = answer
        expl = sanitize_text(q.get("explanation"), limit=500)
        if expl:
            explanations[qid] = expl
    return public, answers, explanations


def _public_questions(raw):
    """Re-validate STORED public questions for rendering. Never reads answer
    or explanation fields, so a key written into a stored block by mistake (or
    on purpose) cannot reach a student."""
    if not isinstance(raw, list) or not raw or len(raw) > MAX_QUESTIONS:
        raise BlockValidationError("This block has no valid questions.", field="questions")
    out = []
    for q in raw:
        if not isinstance(q, dict) or not isinstance(q.get("options"), dict):
            raise BlockValidationError("A question is not valid.", field="questions")
        qid = q.get("id")
        if not isinstance(qid, str) or not _ID_RE.match(qid):
            raise BlockValidationError("A question is not valid.", field="questions")
        options = {k: sanitize_text(v, limit=300, allow_newlines=False)
                   for k, v in q["options"].items() if k in OPTION_LETTERS and isinstance(v, str)}
        options = {k: v for k, v in sorted(options.items()) if v}
        prompt = sanitize_text(q.get("prompt"), limit=1000)
        if not prompt or len(options) < 2:
            raise BlockValidationError("A question is not valid.", field="questions")
        out.append({"id": qid, "prompt": prompt, "type": "true_false" if q.get("type") == "true_false" else "mcq",
                    "options": options, "points": _int_in(q.get("points"), 1, 100, 1, "questions")})
    return out


class PracticeBlock(BlockType):
    name, label = "practice", "Practice"
    description = "Ungraded self-check questions: unlimited tries, answers and explanations shown after checking."
    fields = (_field("title", "Title", "text"), _field("questions", "Questions", "questions", True))
    interactive = True

    def clean(self, content):
        c = _require_dict(content)
        questions, answers, explanations = _clean_questions(c.get("questions"))
        return ({"title": _short(c.get("title"), 160, "title"), "questions": questions},
                {"answers": answers, "explanations": explanations})

    def render(self, public):
        c = _require_dict(public)
        return {"title": _short(c.get("title"), 160, "title"), "questions": _public_questions(c.get("questions")),
                "graded": False, "maxAttempts": None}


class QuizBlock(BlockType):
    name, label = "quiz", "Quiz"
    description = "Scored questions with a limited number of attempts. Answer keys stay on the server."
    fields = (_field("title", "Title", "text"), _field("maxAttempts", "Attempts allowed", "number"),
              _field("questions", "Questions", "questions", True))
    interactive = True

    def clean(self, content):
        c = _require_dict(content)
        questions, answers, explanations = _clean_questions(c.get("questions"))
        return ({"title": _short(c.get("title"), 160, "title"), "maxAttempts": _int_in(c.get("maxAttempts"), 1, 10, 3, "maxAttempts"),
                 "questions": questions},
                {"answers": answers, "explanations": explanations})

    def render(self, public):
        c = _require_dict(public)
        return {"title": _short(c.get("title"), 160, "title"), "questions": _public_questions(c.get("questions")),
                "graded": True, "maxAttempts": _int_in(c.get("maxAttempts"), 1, 10, 3, "maxAttempts")}


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------
_REGISTRY = {}


def register_block_type(block_type):
    if not isinstance(block_type, BlockType) or not re.match(r"^[a-z][a-z0-9_]{1,30}$", block_type.name or ""):
        raise ValueError("Invalid block type registration.")
    _REGISTRY[block_type.name] = block_type
    return block_type


def get_block_type(name):
    return _REGISTRY.get(name if isinstance(name, str) else "")


def block_type_names():
    return list(_REGISTRY)


def block_type_catalog():
    """UI metadata for the builder (no behaviour, safe to send to a teacher)."""
    return [{"type": b.name, "label": b.label, "description": b.description,
             "fields": list(b.fields), "interactive": b.interactive} for b in _REGISTRY.values()]


for _bt in (TextBlock(), FormulaBlock(), ImageBlock(), VideoBlock(), PdfReadingBlock(), GeoGebraBlock(),
            SimulationBlock(), PracticeBlock(), QuizBlock(), AssignmentBlock()):
    register_block_type(_bt)


# --------------------------------------------------------------------------
# list-level validation, ordering, rendering, grading
# --------------------------------------------------------------------------
def validate_blocks(raw_blocks, *, max_blocks=MAX_BLOCKS):
    """Validate a teacher-submitted list of blocks.

    Returns (blocks, keys): `blocks` is the storable list
    [{id, type, order, content}] with order renumbered 0..n-1 in list order,
    and `keys` maps blockId -> {"answers", "explanations"} for Practice/Quiz
    (to be written to the server-only key record, NOT the lesson).
    Raises BlockValidationError naming the offending block.
    """
    if not isinstance(raw_blocks, list):
        raise BlockValidationError("blocks must be a list.")
    if len(raw_blocks) > max_blocks:
        raise BlockValidationError(f"A lesson can hold at most {max_blocks} blocks.")
    blocks, keys, seen = [], {}, set()
    for index, raw in enumerate(raw_blocks):
        try:
            if not isinstance(raw, dict):
                raise BlockValidationError("Each block must be an object.")
            bt = get_block_type(raw.get("type"))
            if bt is None:
                raise BlockValidationError("Unsupported block type.", field="type")
            public, private = bt.clean(raw.get("content"))
            bid = raw.get("id")
            bid = bid.strip() if isinstance(bid, str) and _ID_RE.match(bid.strip()) else new_id("b")
            if bid in seen:
                raise BlockValidationError("Duplicate block id.", field="id")
            seen.add(bid)
        except BlockValidationError as exc:
            if exc.index is None:
                exc.index = index
            raise
        blocks.append({"id": bid, "type": bt.name, "order": index, "content": public})
        if private:
            keys[bid] = private
    if len(json.dumps(blocks, ensure_ascii=False)) > MAX_STORED_BYTES:
        raise BlockValidationError("This lesson is too large. Remove some content and try again.")
    return blocks, keys


def normalize_block_order(blocks):
    """Return stored blocks sorted by their `order` (ties by list position)
    with order renumbered 0..n-1. Tolerates legacy/odd stored data."""
    if not isinstance(blocks, list):
        return []
    valid = [b for b in blocks if isinstance(b, dict)]

    def key(pair):
        pos, b = pair
        o = b.get("order")
        return (o if isinstance(o, int) and not isinstance(o, bool) else pos, pos)
    ordered = [b for _pos, b in sorted(enumerate(valid), key=key)]
    return [dict(b, order=i) for i, b in enumerate(ordered)]


def reorder_blocks(blocks, ordered_ids):
    """Reorder stored blocks to `ordered_ids`. The id list must be exactly the
    existing set (same contract as the unit/lesson reorder routes)."""
    by_id = {b.get("id"): b for b in blocks if isinstance(b, dict)}
    if (not isinstance(ordered_ids, list) or len(ordered_ids) != len(by_id)
            or len(set(ordered_ids)) != len(ordered_ids) or set(ordered_ids) != set(by_id)):
        return None
    return [dict(by_id[bid], order=i) for i, bid in enumerate(ordered_ids)]


def render_blocks(stored_blocks, on_invalid=None):
    """Student-safe render data for stored blocks, in order. Content is
    re-validated on every read; a block that no longer validates is dropped
    (and reported through `on_invalid`) instead of being shown."""
    out = []
    for b in normalize_block_order(stored_blocks):
        bt = get_block_type(b.get("type"))
        if bt is None:
            if on_invalid:
                on_invalid(b, "unknown block type")
            continue
        try:
            out.append({"id": str(b.get("id") or ""), "type": bt.name, "order": b["order"],
                        "render": bt.render(b.get("content") if isinstance(b.get("content"), dict) else {})})
        except BlockValidationError as exc:
            if on_invalid:
                on_invalid(b, exc.message)
    return out


def block_references(stored_blocks):
    refs = []
    for b in stored_blocks or []:
        bt = get_block_type(b.get("type")) if isinstance(b, dict) else None
        if bt and isinstance(b.get("content"), dict):
            try:
                refs.extend((b.get("id"), k, v) for k, v in bt.references(b["content"]))
            except (KeyError, TypeError):
                continue
    return refs


def grade_answers(public_render, private, submitted, *, reveal):
    """Grade `submitted` ({questionId: 'A'}) against the private key.

    `public_render` is the block's render() dict (question list), `private`
    the {"answers","explanations"} record. Unknown question ids are ignored;
    unanswered questions score zero. When `reveal` is False the response
    carries only per-question correctness, never the key or explanation.
    """
    answers = (private or {}).get("answers") or {}
    explanations = (private or {}).get("explanations") or {}
    submitted = submitted if isinstance(submitted, dict) else {}
    score = total = correct = 0
    results = []
    for q in public_render.get("questions", []):
        qid, pts = q["id"], int(q.get("points") or 1)
        key = str(answers.get(qid, "")).upper()
        chosen = submitted.get(qid)
        chosen = str(chosen).strip().upper()[:2] if isinstance(chosen, (str, int)) and not isinstance(chosen, bool) else ""
        total += pts
        ok = bool(key) and chosen == key
        if ok:
            score += pts
            correct += 1
        row = {"questionId": qid, "selectedAnswer": chosen if chosen in q["options"] else "", "correct": ok}
        if reveal:
            row["correctAnswer"] = key
            if explanations.get(qid):
                row["explanation"] = explanations[qid]
        results.append(row)
    pct = round(score / total * 100, 1) if total else 0.0
    return {"score": score, "totalPoints": total, "percentage": pct, "correctCount": correct,
            "questionCount": len(results), "results": results}
