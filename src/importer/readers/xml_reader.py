"""XML reader and element identities (design §8.6, §8.10).

External entities and network access are disabled (XXE-safe). Identities are
namespace-free paths from the root, with [n] only where a name repeats among
siblings; paths over 256 characters fall back to an ordinal path like 1.3.2.
"""

from __future__ import annotations

from lxml import etree

from .base import ReaderError, XmlDocument

MAX_IDENTITY = 256


def _parser() -> etree.XMLParser:
    return etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)


def read_xml(data: bytes) -> XmlDocument:
    try:
        root = etree.fromstring(data, _parser())
    except etree.XMLSyntaxError as exc:
        raise ReaderError("corrupt", f"invalid XML: {exc}") from exc
    qname = etree.QName(root)
    encoding = root.getroottree().docinfo.encoding
    return XmlDocument(root=root, namespace=qname.namespace, local_name=qname.localname, encoding=encoding)


def local(el) -> str:
    return etree.QName(el).localname


def element_identity(el, root) -> str:
    if el is root:
        return "/"
    steps: list[str] = []
    node = el
    while node is not None and node is not root:
        name = local(node)
        before = sum(1 for _ in node.itersiblings(node.tag, preceding=True))
        after = next(node.itersiblings(node.tag), None) is not None
        steps.append(name if before == 0 and not after else f"{name}[{before + 1}]")
        node = node.getparent()
    path = "/" + "/".join(reversed(steps))
    return path if len(path) <= MAX_IDENTITY else ordinal_identity(el, root)


def ordinal_identity(el, root) -> str:
    steps: list[str] = []
    node = el
    while node is not None and node is not root:
        before = sum(1 for s in node.itersiblings(preceding=True) if isinstance(s.tag, str))
        steps.append(str(before + 1))
        node = node.getparent()
    return ".".join(reversed(steps))
