"""
Parser for Wikimedia pageview_complete monthly dump lines.

Line format:
    wiki_code  title  page_id  agent  daily_total  [hourly_counts]

Notes (confirmed against real dump samples):
- page_id is always present as a field: either numeric, or the literal
  string "null".
- hourly_counts is optional and, when present or absent, is simply
  discarded — it is never needed downstream.
- Titles containing a literal double-quote character are CSV-style
  quoted in the raw dump: the whole title field is wrapped in an
  outer pair of unescaped " characters, and every literal " inside
  the title is escaped as \". Titles with no " character are left
  completely bare (no wrapping). E.g. title `"` is written as `"\""`
  (open-quote, escaped-quote, close-quote); title `"W"_x` is written
  as `"\"W\"_x"`. This wrapper must be stripped and the inner \"
  sequences unescaped back to " after extraction.
- Titles use underscores for spaces and can contain arbitrary
  punctuation (including leading '!', "'", '(') and non-Latin scripts.
- We deliberately do NOT assume a fixed total column count. We split
  from the left with maxsplit=4 to reliably isolate the first four
  fixed fields, then treat everything else as an opaque tail from
  which we only need the first token (daily_total).
"""

from dataclasses import dataclass


class MalformedLineError(ValueError):
    """Raised when a line does not have the minimum expected structure."""


@dataclass(frozen=True)
class ParsedPageview:
    wiki_code: str
    title: str
    page_id: str  # kept as-is (numeric string or "null"); unused downstream
    agent: str
    daily_total: int

    @staticmethod
    def unescape_title(raw_title: str) -> str:
        """
        Convert a raw dump title field into its true string form.

        The dump uses CSV-style conditional quoting: a title is wrapped in
        an outer, unescaped pair of double-quotes IF AND ONLY IF it contains
        a literal double-quote character; any literal " inside such a title
        is escaped as \". Titles without a " character are left bare with
        no wrapping at all.

        Examples (raw field -> true title):
            '!'                 -> '!'                  (no quote char, bare)
            '"\\""'             -> '"'                  (wrapped + escaped)
            '"\\"W\\"_x"'       -> '"W"_x'               (wrapped + escaped)
        """

        # raw_title = raw_title.replace("_", " ")
        # Check if the raw_title is wrapped in double quotes
        if len(raw_title) >= 2 and raw_title.startswith('"') and raw_title.endswith('"'):
            # Extract the inner content by removing the outer quotes
            inner = raw_title[1:-1]
            # Replace escaped quotes (\") with actual quotes (")
            return inner.replace('\\"', '"')
        # If not wrapped in quotes, return as-is
        return raw_title

    @classmethod
    def parse(cls, line: str) -> "ParsedPageview":
        """
        Parse a single line of the pageview_complete dump.

        Raises MalformedLineError if the line doesn't have at least the
        5 fixed-position fields (wiki_code, title, page_id, agent, rest).
        """
        line = line.rstrip("\n")
        if not line:
            raise MalformedLineError("empty line")

        parts = line.split(" ", maxsplit=4)
        if len(parts) < 5:
            raise MalformedLineError(f"expected at least 5 space-separated fields, got {len(parts)}: {line!r}")

        wiki_code, raw_title, page_id, agent, rest = parts

        # rest is "daily_total" or "daily_total hourly_counts"; we only need
        # the first token. hourly_counts (if present) is discarded untouched.
        daily_total_str = rest.split(" ", maxsplit=1)[0]
        try:
            daily_total = int(daily_total_str)
        except ValueError as exc:
            raise MalformedLineError(f"could not parse daily_total from {daily_total_str!r} in line: {line!r}") from exc

        title = cls.unescape_title(raw_title)

        return cls(
            wiki_code=wiki_code,
            title=title,
            page_id=page_id,
            agent=agent,
            daily_total=daily_total,
        )


__all__ = [
    "ParsedPageview",
]
