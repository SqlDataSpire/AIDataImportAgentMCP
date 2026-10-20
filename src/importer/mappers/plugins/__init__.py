"""Human-saved plugin mappers (design §8.5 tier 3).

Each module here holds a Mapper subclass with a ``version`` class attribute.
A plugin becomes live only when a person commits it AND a mapper artifact
pins its SHA-256; editing a file in place makes the loader refuse it.
"""
