"""Write the guide as Markdown pages that GitHub shows directly: README.md (contents) and one <chapter>.md each.

Called by build_guide.py; the HTML fragments in chapters/ stay the only place the text is edited. It knows the tags the
chapters use and stops on any other, so nothing is dropped without a word.
"""
import html.parser
import os
import re

BLOCKS = {"section", "p", "h2", "h3", "h4", "ol", "ul", "table", "div", "figure", "figcaption", "aside"}
VOID = {"img", "br"}
SPACE = re.compile(r"[ \t\r\n]+")


class Node:
    def __init__(self, tag, attrs=()):
        self.tag, self.attrs, self.kids = tag, dict(attrs), []

    def find(self, tag):
        for k in self.kids:
            if isinstance(k, Node):
                if k.tag == tag:
                    yield k
                yield from k.find(tag)

    def text(self):
        return "".join(k if isinstance(k, str) else k.text() for k in self.kids)


class Tree(html.parser.HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.open = [self.root]
        self.feed(source)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.open[-1].kids.append(node)
        if tag not in VOID:
            self.open.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.open) - 1, 0, -1):
            if self.open[i].tag == tag:
                del self.open[i:]
                return

    def handle_data(self, data):
        self.open[-1].kids.append(data)


def slug(title):
    """The anchor GitHub gives a heading."""
    return re.sub(r"[^\w\- ]", "", title.strip().lower()).replace(" ", "-")


def escape(text):
    text = re.sub(r"([\\`*\[\]~])", r"\\\1", text)
    text = re.sub(r"(?<![^\W_])_|_(?![^\W_])", r"\\_", text)
    text = re.sub(r"&(?=#?\w+;)", "&amp;", text)
    return text.replace("<", "&lt;")


def line_start(text):
    """Keep a paragraph that begins like a list, a heading or a quote an ordinary paragraph."""
    text = re.sub(r"^(\d+)([.)])(?=\s)", r"\1\\\2", text)
    return re.sub(r"^([#>+=-])", r"\\\1", text)


def wrap(mark, body):
    inner = body.strip()
    if not inner:
        return body
    return body[:len(body) - len(body.lstrip())] + mark + inner + mark + body[len(body.rstrip()):]


class Page:
    def __init__(self, key, links, figures):
        self.key, self.links, self.figures = key, links, figures

    def href(self, target):
        if not target.startswith("#"):
            return target
        page, anchor = self.links[target[1:]]              # a link to nowhere stops the build
        if page == self.key and anchor:
            return "#" + anchor
        return f"{page}.md" + (f"#{anchor}" if anchor else "")

    def inline(self, node):
        if isinstance(node, str):
            return escape(SPACE.sub(" ", node))
        tag = node.tag
        if tag == "code":
            body = SPACE.sub(" ", node.text())
            fence = "`"
            while fence in body:
                fence += "`"
            pad = " " if body.startswith("`") or body.endswith("`") else ""
            return fence + pad + body + pad + fence
        if tag == "img":
            return f"![{escape(node.attrs.get('alt') or '')}]({node.attrs['src']})"
        if tag == "br":
            return "<br>"
        body = "".join(self.inline(k) for k in node.kids)
        if tag == "strong":
            return wrap("**", body)
        if tag == "em":
            return wrap("*", body)
        if tag == "kbd":
            return f"<kbd>{body}</kbd>"
        if tag == "a":
            return f"[{body}]({self.href(node.attrs['href'])})"
        if tag in ("span", "root"):
            return body
        raise ValueError(f"{self.key}: unknown inline tag <{tag}>")

    def blocks(self, node, top=False):
        """The children of node as a list of Markdown blocks."""
        out, run = [], []

        def flush():
            text = SPACE.sub(" ", "".join(run)).strip()
            if text:
                out.append(line_start(text))
            run.clear()
        for k in node.kids:
            if isinstance(k, Node) and k.tag in BLOCKS:
                flush()
                out.extend(self.block(k, top))
            else:
                run.append(self.inline(k))
        flush()
        return out

    def block(self, node, top):
        tag = node.tag
        if tag in ("section", "div"):
            return self.blocks(node, top)
        if tag == "p":
            return self.blocks(node)
        if tag in ("h2", "h3", "h4"):
            return ["#" * (int(tag[1]) - 1) + " " + "".join(self.inline(k) for k in node.kids).strip()]
        if tag in ("ol", "ul"):
            return [self.listing(node)]
        if tag == "table":
            return [self.table(node)]
        if tag == "figure":
            return self.blocks(node)
        if tag == "figcaption":
            self.figures[0] += 1
            return [f"<sub>Fig. {self.figures[0]}</sub> · " + " ".join(self.blocks(node))]
        if tag == "aside":
            limits = "limits" in node.attrs.get("class", "")
            body = self.blocks(node)
            if top:                                         # GitHub draws these boxes only outside lists
                head = ["[!IMPORTANT]", "**Limits**", ""] if limits else ["[!NOTE]"]
            else:
                head = ["**Limits**" if limits else "**Note**", ""]
            lines = head + "\n\n".join(body).split("\n")
            return ["\n".join(("> " + line).rstrip() for line in lines)]
        raise ValueError(f"{self.key}: unknown block tag <{tag}>")

    def listing(self, node):
        items, number = [], int(node.attrs.get("start", 1))
        for li in node.kids:
            if not isinstance(li, Node):
                continue
            if li.tag != "li":
                raise ValueError(f"{self.key}: <{li.tag}> directly inside a list")
            marker = f"{number}. " if node.tag == "ol" else "- "
            number += 1
            tight = not any(isinstance(k, Node) and k.tag in ("p", "figure", "aside", "table", "div") for k in li.kids)
            lines = ("\n" if tight else "\n\n").join(self.blocks(li)).split("\n")
            pad = " " * len(marker)
            items.append("\n".join([marker + lines[0]] + [pad + line if line else "" for line in lines[1:]]))
        return "\n".join(items)

    def cell(self, node):
        parts = []
        for block in self.blocks(node):
            parts.extend(line.strip() for line in block.split("\n") if line.strip())
        # a list inside a cell keeps its numbers as text, one per line
        parts = [re.sub(r"^(\d+)\. ", r"\1\\. ", p) for p in parts]
        return "<br>".join(parts).replace("|", "\\|")

    def table(self, node):
        rows = [[c for c in tr.kids if isinstance(c, Node) and c.tag in ("td", "th")] for tr in node.find("tr")]
        width = max(len(r) for r in rows)
        head = rows.pop(0) if all(c.tag == "th" for c in rows[0]) else []

        def line(cells):
            return "| " + " | ".join(cells + [""] * (width - len(cells))) + " |"
        out = [line([self.cell(c) for c in head]), line(["---"] * width)]
        out += [line([self.cell(c) for c in row]) for row in rows]
        return "\n".join(out)


def write(here, order, blurbs, template):
    chapters = []
    for key, title in order:
        path = os.path.join(here, "chapters", f"{key}.html")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                tree = Tree(f.read()).root
            heading = next(tree.find("h2"), None)
            chapters.append((key, heading.text().strip() if heading else title, tree))
    # where every id of the one-page guide lives in the Markdown pages
    links = {}
    for key, _title, tree in chapters:
        links[key] = (key, "")
        seen = {}
        for node in (n for n in walk(tree) if n.tag in ("h2", "h3", "h4")):
            text = SPACE.sub(" ", node.text()).strip()
            anchor = slug(text)
            count = seen.get(anchor, 0)
            seen[anchor] = count + 1
            if count:
                anchor += f"-{count}"
            if node.tag == "h3":
                links.setdefault(f"{key}-" + re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-"), (key, anchor))
    figures = [0]
    for i, (key, title, tree) in enumerate(chapters):
        page = Page(key, links, figures)
        nav = ["[Contents](README.md)"]
        if i:
            nav.append(f"Previous: [{escape(chapters[i - 1][1])}]({chapters[i - 1][0]}.md)")
        if i + 1 < len(chapters):
            nav.append(f"Next: [{escape(chapters[i + 1][1])}]({chapters[i + 1][0]}.md)")
        bar = " · ".join(nav)
        note = f"<!-- Written by build_guide.py from chapters/{key}.html: edit that file, not this one. -->"
        save(here, f"{key}.md", [note, bar] + page.blocks(tree, top=True) + ["---", bar])
    top = Tree(template.split("<header", 1)[1].split("{{CARDS}}")[0].split(">", 1)[1]).root
    front = Page("README", links, figures)
    heading = " ".join(next(top.find("h1")).text().split())
    leads = [" ".join(front.blocks(p)) for p in top.find("p")]
    listing = "\n".join(f"{n}. [{escape(title)}]({key}.md): {escape(blurbs[key])}"
                        for n, (key, title, _tree) in enumerate(chapters, 1))
    save(here, "README.md", ["<!-- Written by build_guide.py: edit the template and the chapters, not this file. -->",
                             f"# {escape(heading)}"] + leads + ["## Chapters", listing])
    return len(chapters)


def walk(node):
    for k in node.kids:
        if isinstance(k, Node):
            yield k
            yield from walk(k)


def save(here, name, blocks):
    with open(os.path.join(here, name), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n\n".join(blocks) + "\n")
