"""XML value rules shared by the xml and tree spec kinds (design §8.9, §8.24).

Values land exactly as written. An element's value is read through the spec's
value_child (e.g. the CMS <cellValue>) when present, otherwise from its text.
"""

from __future__ import annotations

from lxml import etree


def put(fields: dict, extras: dict, schema_set: set[str], column: str, value: object) -> None:
    (fields if column in schema_set else extras)[column] = value


class XmlValues:
    """Mixin. The host sets ``self.spec`` (with ``namespaces`` and ``value_child``) and ``self._compiled = {}``."""

    def _xpath(self, expr: str) -> etree.XPath:
        if expr not in self._compiled:
            self._compiled[expr] = etree.XPath(expr, namespaces=self.spec.namespaces)
        return self._compiled[expr]

    def _values(self, result, used: set) -> list[str]:
        if isinstance(result, list):
            out: list[str] = []
            for item in result:
                if isinstance(item, etree._Element):
                    used.add(item)
                    out.append(self._value(item))
                else:
                    out.append(str(item))
            return out
        if isinstance(result, bool):
            return ["true" if result else "false"]
        if isinstance(result, float):
            return [str(int(result)) if result.is_integer() else str(result)]
        return [str(result)]

    def _value(self, el) -> str:
        if self.spec.value_child:
            hit = self._xpath(self.spec.value_child)(el)
            if hit and isinstance(hit[0], etree._Element):
                return hit[0].text if hit[0].text is not None else ""
        return el.text if el.text is not None else ""

    def _is_value_element(self, el) -> bool:
        if self.spec.value_child and self._xpath(self.spec.value_child)(el):
            return True
        return len(el) == 0
