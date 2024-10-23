# Abbreviation Extension for Python-Markdown
# ==========================================

# This extension adds abbreviation handling to Python-Markdown.

# See https://Python-Markdown.github.io/extensions/abbreviations
# for documentation.

# Original code Copyright 2007-2008 [Waylan Limberg](http://achinghead.com/)
# and [Seemant Kulleen](http://www.kulleen.org/)

# All changes Copyright 2008-2014 The Python Markdown Project

# License: [BSD](https://opensource.org/licenses/bsd-license.php)

from __future__ import annotations

from . import Extension
from ..blockprocessors import BlockProcessor
from ..treeprocessors import Treeprocessor
from ..util import AtomicString
from typing import TYPE_CHECKING, Optional
import re
import xml.etree.ElementTree as etree
from functools import lru_cache

if TYPE_CHECKING:  # pragma: no cover
    from .. import Markdown
    from ..blockparser import BlockParser


class AbbrExtension(Extension):
    """
    Abbreviation Extension for Python-Markdown.

    This extension implements abbreviations in Python-Markdown. It adds the ability
    to define abbreviations in the document and automatically wrap them in <abbr>
    tags with corresponding title attributes.
    """

    def __init__(self, **kwargs):
        """
        Initialize the extension with optional configuration.

        Args:
            **kwargs: Configuration options for the extension
                     - glossary: A dictionary of abbreviations and their definitions
        """
        self.config = {
            "glossary": [
                {},
                "A dictionary where the `key` is the abbreviation and the `value` is the definition. "
                "Default: `{}`",
            ],
        }
        super().__init__(**kwargs)
        self.abbrs = {}  # Current abbreviations
        self.glossary = {}  # Persistent glossary abbreviations

    def reset(self):
        """
        Clear all previously defined abbreviations and restore glossary items.
        Called before each new document is processed.
        """
        self.abbrs.clear()
        if self.glossary:
            self.abbrs.update(self.glossary)

    def reset_glossary(self):
        """Clear all abbreviations from the glossary."""
        self.glossary.clear()

    def load_glossary(self, dictionary: dict[str, str]):
        """
        Add abbreviations to the glossary.

        Args:
            dictionary: A dictionary of abbreviations where keys are terms and values are definitions.
                      New entries will override existing ones with the same key.
        """
        if dictionary:
            self.glossary = {**dictionary, **self.glossary}

    def extendMarkdown(self, md):
        """
        Register the required processors with Python-Markdown.

        Args:
            md: The Markdown instance to extend.
        """
        if self.config["glossary"][0]:
            self.load_glossary(self.config["glossary"][0])
        self.abbrs.update(self.glossary)
        md.registerExtension(self)
        md.treeprocessors.register(AbbrTreeprocessor(md, self.abbrs), "abbr", 7)
        md.parser.blockprocessors.register(
            AbbrBlockprocessor(md.parser, self.abbrs), "abbr", 16
        )


class AbbrTreeprocessor(Treeprocessor):
    """
    Replace abbreviation text with `<abbr>` elements.

    This processor searches through the document tree for text matching defined
    abbreviations and wraps them in <abbr> elements with appropriate titles.
    The implementation is optimized for performance through regex caching and
    efficient text processing.
    """

    def __init__(self, md: Markdown | None = None, abbrs: dict | None = None):
        """
        Initialize the tree processor.

        Args:
            md: The Markdown instance (optional)
            abbrs: Dictionary of abbreviations where keys are terms and values are definitions
        """
        self.abbrs: dict = abbrs if abbrs is not None else {}
        self._pattern: Optional[str] = None  # Cached regex pattern string
        self._regex_cache: dict[str, re.Pattern] = (
            {}
        )  # Cache for compiled regex patterns
        super().__init__(md)

    @property
    def pattern(self) -> str:
        """
        Lazily build and cache the regex pattern for matching abbreviations.

        The pattern is built only when needed and cached for reuse. Abbreviations
        are sorted by length (longest first) to ensure proper matching of overlapping terms.

        Returns:
            str: A regex pattern that matches any of the defined abbreviations
        """
        if self._pattern is None and self.abbrs:
            # Sort by length (longest first) to handle overlapping matches correctly
            abbr_list = sorted(self.abbrs.keys(), key=len, reverse=True)
            # Create pattern that matches whole words only using word boundaries
            self._pattern = f"\\b(?:{'|'.join(re.escape(key) for key in abbr_list)})\\b"
        return (
            self._pattern or r"(?!)"
        )  # Return non-matching pattern if no abbreviations

    @lru_cache(maxsize=1024)
    def get_regex(self, pattern: str) -> re.Pattern:
        """
        Get or compile a cached regex pattern.

        Args:
            pattern: The regex pattern string

        Returns:
            re.Pattern: A compiled regex pattern object
        """
        return re.compile(pattern)

    def create_element(self, title: str, text: str, tail: str) -> etree.Element:
        """
        Create an `abbr` element with the given attributes.

        Args:
            title: The expansion/definition of the abbreviation
            text: The abbreviation text itself
            tail: Any text that follows the abbreviation

        Returns:
            etree.Element: A new abbr element
        """
        abbr = etree.Element("abbr", {"title": title})
        abbr.text = AtomicString(
            text
        )  # Prevent further processing of abbreviation text
        abbr.tail = tail
        return abbr

    def process_text(self, text: str, matches: list) -> str:
        """
        Find all abbreviation matches in the given text.

        This method performs a single pass over the text to find all matches,
        collecting them for later processing to minimize string operations.

        Args:
            text: The text to process
            matches: List to collect match information (start, end, text, title)

        Returns:
            str: The original text (unchanged in this method)
        """
        if not text:
            return text

        regex = self.get_regex(self.pattern)
        for m in regex.finditer(text):
            if self.abbrs[m.group(0)]:
                matches.append((m.start(), m.end(), m.group(0), self.abbrs[m.group(0)]))
        return text

    def process_element_text(
        self, el: etree.Element, parent: Optional[etree.Element] = None
    ) -> None:
        """
        Process text content of an element and its children recursively.

        This method handles both the element's own text and the tail text of its children.
        Matches are processed in reverse order to maintain correct string indices.

        Args:
            el: The element to process
            parent: The parent element (needed for processing tail text)
        """
        # Process children first (in reverse order to maintain indices)
        for child in reversed(el):
            self.process_element_text(child, el)

        # Process element's own text
        if el.text:
            matches = []
            el.text = self.process_text(el.text, matches)
            # Insert abbreviation elements in reverse order
            for start, end, text, title in reversed(matches):
                abbr = self.create_element(title, text, el.text[end:])
                el.insert(0, abbr)
                el.text = el.text[:start]

        # Process tail text if element has a parent
        if parent is not None and el.tail:
            matches = []
            el.tail = self.process_text(el.tail, matches)
            # Insert abbreviation elements after the current element
            idx = list(parent).index(el) + 1
            for start, end, text, title in reversed(matches):
                abbr = self.create_element(title, text, el.tail[end:])
                parent.insert(idx, abbr)
                el.tail = el.tail[:start]

    def run(self, root: etree.Element) -> etree.Element | None:
        """
        Process the document tree for abbreviations.

        This is the main entry point called by the Markdown processor.

        Args:
            root: The root element of the document tree

        Returns:
            None: The tree is modified in place
        """
        if not self.abbrs:
            return None

        self.process_element_text(root)
        return None


class AbbrBlockprocessor(BlockProcessor):
    """
    Parse text for abbreviation references.

    This processor handles the syntax for defining abbreviations in the document:
    *[abbr]: definition
    """

    RE = re.compile(
        r"^[*]\[(?P<abbr>[^\\]*?)\][ ]?:[ ]*\n?[ ]*(?P<title>.*)$", re.MULTILINE
    )

    def __init__(self, parser: BlockParser, abbrs: dict):
        """
        Initialize the block processor.

        Args:
            parser: The block parser instance
            abbrs: Dictionary to store discovered abbreviations
        """
        self.abbrs: dict = abbrs
        super().__init__(parser)

    def test(self, parent: etree.Element, block: str) -> bool:
        """
        Test if the block should be processed.

        Returns:
            bool: Always returns True as we need to check all blocks for abbreviation definitions
        """
        return True

    def run(self, parent: etree.Element, blocks: list[str]) -> bool:
        """
        Process the given block for abbreviation definitions.

        Args:
            parent: Parent element
            blocks: List of blocks to process

        Returns:
            bool: True if a definition was found and processed
        """
        block = blocks.pop(0)
        m = self.RE.search(block)
        if m:
            abbr = m.group("abbr").strip()
            title = m.group("title").strip()
            if title and abbr:
                if title == "''" or title == '""':
                    # Empty title removes abbreviation
                    self.abbrs.pop(abbr)
                else:
                    # Add or update abbreviation
                    self.abbrs[abbr] = title
                if block[m.end() :].strip():
                    # Add remaining content back to blocks
                    blocks.insert(0, block[m.end() :].lstrip("\n"))
                if block[: m.start()].strip():
                    # Add preceding content back to blocks
                    blocks.insert(0, block[: m.start()].rstrip("\n"))
                return True
        blocks.insert(0, block)  # No match found, restore block
        return False


def makeExtension(**kwargs):  # pragma: no cover
    """Create an instance of AbbrExtension with the given config options."""
    return AbbrExtension(**kwargs)
