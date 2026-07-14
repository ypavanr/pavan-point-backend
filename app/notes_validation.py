"""Server-side allow-list validation for note content.

Note content is user-authored rich text stored as a Tiptap/ProseMirror JSON
document and later rendered back into other users' browsers. That makes it a
classic stored-XSS vector if the frontend were ever trusted to only send
well-formed output, so every node/mark type is checked against an explicit
allow-list here before anything is written to SQLite - no raw HTML nodes, no
node types outside what the editor's toolbar (section 2 of the Notes spec)
actually exposes.
"""
import re

# Keep in sync with the Tiptap extensions enabled in NoteEditorModal.jsx.
ALLOWED_NODE_TYPES = {
    "doc", "paragraph", "text", "heading",
    "bulletList", "orderedList", "listItem", "hardBreak",
}
ALLOWED_MARK_TYPES = {"bold", "italic", "underline", "strike", "highlight"}
ALLOWED_HEADING_LEVELS = {1, 2, 3}
ALLOWED_TEXT_ALIGN = {"left", "center", "right", "justify"}
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$")

MAX_CONTENT_JSON_BYTES = 3 * 1024 * 1024  # a few MB ceiling per the spec


class NoteContentValidationError(ValueError):
    pass


def _validate_mark(mark):
    if not isinstance(mark, dict):
        raise NoteContentValidationError("Mark must be an object")
    mark_type = mark.get("type")
    if mark_type not in ALLOWED_MARK_TYPES:
        raise NoteContentValidationError(f"Disallowed mark type: {mark_type!r}")
    if mark_type == "highlight":
        attrs = mark.get("attrs") or {}
        color = attrs.get("color")
        if color is not None and not HEX_COLOR_RE.match(color):
            raise NoteContentValidationError("Highlight color must be a hex color")


def _validate_node(node):
    if not isinstance(node, dict):
        raise NoteContentValidationError("Node must be an object")

    node_type = node.get("type")
    if node_type not in ALLOWED_NODE_TYPES:
        raise NoteContentValidationError(f"Disallowed node type: {node_type!r}")

    attrs = node.get("attrs") or {}

    if node_type == "heading":
        level = attrs.get("level")
        if level not in ALLOWED_HEADING_LEVELS:
            raise NoteContentValidationError(f"Disallowed heading level: {level!r}")

    if node_type in ("paragraph", "heading"):
        align = attrs.get("textAlign")
        if align is not None and align not in ALLOWED_TEXT_ALIGN:
            raise NoteContentValidationError(f"Disallowed textAlign: {align!r}")

    if node_type == "text":
        if not isinstance(node.get("text"), str):
            raise NoteContentValidationError("Text node missing string 'text'")
        for mark in node.get("marks") or []:
            _validate_mark(mark)

    for child in node.get("content") or []:
        _validate_node(child)


def validate_note_content(doc: dict) -> None:
    """Raises NoteContentValidationError if `doc` isn't a conforming ProseMirror
    document built only from the allowed node/mark types."""
    if not isinstance(doc, dict) or doc.get("type") != "doc":
        raise NoteContentValidationError("Root node must be of type 'doc'")
    for child in doc.get("content") or []:
        _validate_node(child)


def _walk_plaintext(node, out: list) -> None:
    node_type = node.get("type")
    if node_type == "text":
        out.append(node.get("text", ""))
        return
    if node_type == "hardBreak":
        out.append("\n")
        return
    for child in node.get("content") or []:
        _walk_plaintext(child, out)
    if node_type in ("paragraph", "heading", "listItem"):
        out.append("\n")


def extract_plaintext(doc: dict) -> str:
    """Plain-text extraction of a note's body, for grid/list preview snippets -
    a simple tree walk, negligible CPU, no server-side HTML rendering."""
    out = []
    _walk_plaintext(doc, out)
    return "".join(out).strip()
