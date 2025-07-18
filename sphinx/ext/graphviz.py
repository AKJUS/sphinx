"""Allow graphviz-formatted graphs to be included inline in generated documents."""

from __future__ import annotations

import os.path
import re
import subprocess
import xml.etree.ElementTree as ET
from hashlib import sha1
from itertools import chain
from subprocess import CalledProcessError
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

from docutils import nodes
from docutils.parsers.rst import directives

import sphinx
from sphinx.errors import SphinxError
from sphinx.locale import _, __
from sphinx.util import logging
from sphinx.util._pathlib import _StrPath
from sphinx.util.docutils import SphinxDirective
from sphinx.util.i18n import search_image_for_language
from sphinx.util.nodes import set_source_info

if TYPE_CHECKING:
    from typing import Any, ClassVar

    from docutils.nodes import Node

    from sphinx.application import Sphinx
    from sphinx.config import Config
    from sphinx.util.typing import ExtensionMetadata, OptionSpec
    from sphinx.writers.html5 import HTML5Translator
    from sphinx.writers.latex import LaTeXTranslator
    from sphinx.writers.manpage import ManualPageTranslator
    from sphinx.writers.texinfo import TexinfoTranslator
    from sphinx.writers.text import TextTranslator

logger = logging.getLogger(__name__)


class GraphvizError(SphinxError):
    category = 'Graphviz error'


class ClickableMapDefinition:
    """A manipulator for clickable map file of graphviz."""

    maptag_re = re.compile('<map id="(.*?)"')
    href_re = re.compile('href=".*?"')

    def __init__(self, filename: str, content: str, dot: str = '') -> None:
        self.id: str | None = None
        self.filename = filename
        self.content = content.splitlines()
        self.clickable: list[str] = []

        self.parse(dot=dot)

    def parse(self, dot: str) -> None:
        matched = self.maptag_re.match(self.content[0])
        if not matched:
            msg = f'Invalid clickable map file found: {self.filename}'
            raise GraphvizError(msg)

        self.id = matched.group(1)
        if self.id == '%3':
            # graphviz generates wrong ID if graph name not specified
            # https://gitlab.com/graphviz/graphviz/issues/1327
            hashed = sha1(dot.encode(), usedforsecurity=False).hexdigest()
            self.id = f'grapviz{hashed[-10:]}'
            self.content[0] = self.content[0].replace('%3', self.id)

        for line in self.content:
            if self.href_re.search(line):
                self.clickable.append(line)

    def generate_clickable_map(self) -> str:
        """Generate clickable map tags if clickable item exists.

        If not exists, this only returns empty string.
        """
        if self.clickable:
            return '\n'.join((self.content[0], *self.clickable, self.content[-1]))
        else:
            return ''


class graphviz(nodes.General, nodes.Inline, nodes.Element):
    pass


def figure_wrapper(
    directive: SphinxDirective, node: graphviz, caption: str
) -> nodes.figure:
    figure_node = nodes.figure('', node)
    if 'align' in node:
        figure_node['align'] = node.attributes.pop('align')

    inodes, messages = directive.parse_inline(caption)
    caption_node = nodes.caption(caption, '', *inodes)
    caption_node.extend(messages)
    set_source_info(directive, caption_node)
    figure_node += caption_node
    return figure_node


def align_spec(argument: Any) -> str:
    return directives.choice(argument, ('left', 'center', 'right'))


class Graphviz(SphinxDirective):
    """Directive to insert arbitrary dot markup."""

    has_content = True
    required_arguments = 0
    optional_arguments = 1
    final_argument_whitespace = False
    option_spec: ClassVar[OptionSpec] = {
        'alt': directives.unchanged,
        'align': align_spec,
        'caption': directives.unchanged,
        'layout': directives.unchanged,
        'graphviz_dot': directives.unchanged,  # an old alias of `layout` option
        'name': directives.unchanged,
        'class': directives.class_option,
    }

    def run(self) -> list[Node]:
        if self.arguments:
            document = self.state.document
            if self.content:
                return [
                    document.reporter.warning(
                        __(
                            'Graphviz directive cannot have both content and '
                            'a filename argument'
                        ),
                        line=self.lineno,
                    )
                ]
            argument = search_image_for_language(self.arguments[0], self.env)
            rel_filename, filename = self.env.relfn2path(argument)
            self.env.note_dependency(rel_filename)
            try:
                with open(filename, encoding='utf-8') as fp:
                    dotcode = fp.read()
            except OSError:
                return [
                    document.reporter.warning(
                        __('External Graphviz file %r not found or reading it failed')
                        % filename,
                        line=self.lineno,
                    )
                ]
        else:
            dotcode = '\n'.join(self.content)
            rel_filename = None
            if not dotcode.strip():
                return [
                    self.state_machine.reporter.warning(
                        __('Ignoring "graphviz" directive without content.'),
                        line=self.lineno,
                    )
                ]
        node = graphviz()
        node['code'] = dotcode
        node['options'] = {'docname': self.env.current_document.docname}

        if 'graphviz_dot' in self.options:
            node['options']['graphviz_dot'] = self.options['graphviz_dot']
        if 'layout' in self.options:
            node['options']['graphviz_dot'] = self.options['layout']
        if 'alt' in self.options:
            node['alt'] = self.options['alt']
        if 'align' in self.options:
            node['align'] = self.options['align']
        if 'class' in self.options:
            node['classes'] = self.options['class']
        if rel_filename:
            node['filename'] = rel_filename

        if 'caption' not in self.options:
            self.add_name(node)
            return [node]
        else:
            figure = figure_wrapper(self, node, self.options['caption'])
            self.add_name(figure)
            return [figure]


class GraphvizSimple(SphinxDirective):
    """Directive to insert arbitrary dot markup."""

    has_content = True
    required_arguments = 1
    optional_arguments = 0
    final_argument_whitespace = False
    option_spec: ClassVar[OptionSpec] = {
        'alt': directives.unchanged,
        'align': align_spec,
        'caption': directives.unchanged,
        'layout': directives.unchanged,
        'graphviz_dot': directives.unchanged,  # an old alias of `layout` option
        'name': directives.unchanged,
        'class': directives.class_option,
    }

    def run(self) -> list[Node]:
        node = graphviz()
        dot_code = '\n'.join(self.content)
        node['code'] = f'{self.name} {self.arguments[0]} {{\n{dot_code}\n}}\n'
        node['options'] = {'docname': self.env.current_document.docname}
        if 'graphviz_dot' in self.options:
            node['options']['graphviz_dot'] = self.options['graphviz_dot']
        if 'layout' in self.options:
            node['options']['graphviz_dot'] = self.options['layout']
        if 'alt' in self.options:
            node['alt'] = self.options['alt']
        if 'align' in self.options:
            node['align'] = self.options['align']
        if 'class' in self.options:
            node['classes'] = self.options['class']

        if 'caption' not in self.options:
            self.add_name(node)
            return [node]
        else:
            figure = figure_wrapper(self, node, self.options['caption'])
            self.add_name(figure)
            return [figure]


def fix_svg_relative_paths(
    self: HTML5Translator | LaTeXTranslator | TexinfoTranslator,
    filepath: str | os.PathLike[str],
) -> None:
    """Change relative links in generated svg files to be relative to imgpath."""
    env = self.builder.env
    tree = ET.parse(filepath)  # NoQA: S314
    root = tree.getroot()
    ns = {'svg': 'http://www.w3.org/2000/svg', 'xlink': 'http://www.w3.org/1999/xlink'}
    href_name = '{http://www.w3.org/1999/xlink}href'
    modified = False

    for element in chain(
        root.findall('.//svg:image[@xlink:href]', ns),
        root.findall('.//svg:a[@xlink:href]', ns),
    ):
        scheme, hostname, rel_uri, query, fragment = urlsplit(element.attrib[href_name])
        if hostname:
            # not a relative link
            continue

        docname = env.path2doc(self.document['source'])
        if docname is None:
            # This shouldn't happen!
            continue
        doc_dir = self.builder.outdir.joinpath(docname).resolve().parent

        old_path = doc_dir / rel_uri
        img_path = doc_dir / self.builder.imgpath
        new_path = os.path.relpath(old_path, start=img_path)
        modified_url = urlunsplit((scheme, hostname, new_path, query, fragment))

        element.set(href_name, modified_url)
        modified = True

    if modified:
        tree.write(filepath)


def render_dot(
    self: HTML5Translator | LaTeXTranslator | TexinfoTranslator,
    code: str,
    options: dict[str, Any],
    format: str,
    prefix: str = 'graphviz',
    filename: str | None = None,
) -> tuple[_StrPath | None, _StrPath | None]:
    """Render graphviz code into a PNG or PDF output file."""
    graphviz_dot = options.get('graphviz_dot', self.builder.config.graphviz_dot)
    if not graphviz_dot:
        raise GraphvizError(
            __('graphviz_dot executable path must be set! %r') % graphviz_dot,
        )
    hashkey = ''.join((
        code,
        str(options),
        str(graphviz_dot),
        str(self.builder.config.graphviz_dot_args),
    )).encode()

    fname = f'{prefix}-{sha1(hashkey, usedforsecurity=False).hexdigest()}.{format}'
    relfn = _StrPath(self.builder.imgpath, fname)
    outfn = self.builder.outdir / self.builder.imagedir / fname

    if outfn.is_file():
        return relfn, outfn

    if getattr(self.builder, '_graphviz_warned_dot', {}).get(graphviz_dot):
        return None, None

    outfn.parent.mkdir(parents=True, exist_ok=True)

    dot_args = [graphviz_dot]
    dot_args.extend(self.builder.config.graphviz_dot_args)
    dot_args.extend([f'-T{format}', f'-o{outfn}'])

    docname = options.get('docname', 'index')
    if filename:
        cwd = (self.builder.srcdir / filename).parent
    else:
        cwd = (self.builder.srcdir / docname).parent

    if format == 'png':
        dot_args.extend(['-Tcmapx', f'-o{outfn}.map'])

    try:
        ret = subprocess.run(
            dot_args, input=code.encode(), capture_output=True, cwd=cwd, check=True
        )
    except OSError:
        logger.warning(
            __(
                'dot command %r cannot be run (needed for graphviz '
                'output), check the graphviz_dot setting'
            ),
            graphviz_dot,
        )
        if not hasattr(self.builder, '_graphviz_warned_dot'):
            self.builder._graphviz_warned_dot = {}  # type: ignore[union-attr]
        self.builder._graphviz_warned_dot[graphviz_dot] = True  # type: ignore[union-attr]
        return None, None
    except CalledProcessError as exc:
        raise GraphvizError(
            __('dot exited with error:\n[stderr]\n%r\n[stdout]\n%r')
            % (exc.stderr, exc.stdout)
        ) from exc
    if not outfn.is_file():
        raise GraphvizError(
            __('dot did not produce an output file:\n[stderr]\n%r\n[stdout]\n%r')
            % (ret.stderr, ret.stdout)
        )

    if format == 'svg':
        fix_svg_relative_paths(self, outfn)

    return relfn, outfn


def render_dot_html(
    self: HTML5Translator,
    node: graphviz,
    code: str,
    options: dict[str, Any],
    prefix: str = 'graphviz',
    imgcls: str | None = None,
    alt: str | None = None,
    filename: str | None = None,
) -> tuple[str, str]:
    format = self.builder.config.graphviz_output_format
    if format not in {'png', 'svg'}:
        logger.warning(
            __("graphviz_output_format must be either 'png' or 'svg', but is %r"),
            format,
        )
    try:
        fname, outfn = render_dot(self, code, options, format, prefix, filename)
    except GraphvizError as exc:
        logger.warning(__('dot code %r: %s'), code, exc)
        raise nodes.SkipNode from exc

    classes = [imgcls, 'graphviz', *node.get('classes', [])]
    imgcls = ' '.join(filter(None, classes))

    if fname is None:
        self.body.append(self.encode(code))
    else:
        src = fname.as_posix()
        if alt is None:
            alt = node.get('alt', self.encode(code).strip())
        if 'align' in node:
            align = node['align']
            self.body.append(f'<div align="{align}" class="align-{align}">')
        if format == 'svg':
            self.body.append('<div class="graphviz">')
            self.body.append(
                f'<object data="{src}" type="image/svg+xml" class="{imgcls}">\n'
            )
            self.body.append(f'<p class="warning">{alt}</p>')
            self.body.append('</object></div>\n')
        else:
            assert outfn is not None
            with open(f'{outfn}.map', encoding='utf-8') as mapfile:
                map_content = mapfile.read()
            imgmap = ClickableMapDefinition(f'{outfn}.map', map_content, dot=code)
            if imgmap.clickable:
                # has a map
                self.body.append('<div class="graphviz">')
                self.body.append(
                    f'<img src="{src}" alt="{alt}" usemap="#{imgmap.id}" class="{imgcls}" />'
                )
                self.body.append('</div>\n')
                self.body.append(imgmap.generate_clickable_map())
            else:
                # nothing in image map
                self.body.append('<div class="graphviz">')
                self.body.append(f'<img src="{src}" alt="{alt}" class="{imgcls}" />')
                self.body.append('</div>\n')
        if 'align' in node:
            self.body.append('</div>\n')

    raise nodes.SkipNode


def html_visit_graphviz(self: HTML5Translator, node: graphviz) -> None:
    render_dot_html(
        self, node, node['code'], node['options'], filename=node.get('filename')
    )


def render_dot_latex(
    self: LaTeXTranslator,
    node: graphviz,
    code: str,
    options: dict[str, Any],
    prefix: str = 'graphviz',
    filename: str | None = None,
) -> None:
    try:
        fname, _outfn = render_dot(self, code, options, 'pdf', prefix, filename)
    except GraphvizError as exc:
        logger.warning(__('dot code %r: %s'), code, exc)
        raise nodes.SkipNode from exc

    is_inline = self.is_inline(node)

    if not is_inline:
        pre = ''
        post = ''
        if 'align' in node:
            if node['align'] == 'left':
                pre = '{'
                post = r'\hspace*{\fill}}'
            elif node['align'] == 'right':
                pre = r'{\hspace*{\fill}'
                post = '}'
            elif node['align'] == 'center':
                pre = r'{\hfill'
                post = r'\hspace*{\fill}}'
        self.body.append(f'\n{pre}')

    self.body.append(r'\sphinxincludegraphics[]{%s}' % fname)

    if not is_inline:
        self.body.append(f'{post}\n')

    raise nodes.SkipNode


def latex_visit_graphviz(self: LaTeXTranslator, node: graphviz) -> None:
    render_dot_latex(
        self, node, node['code'], node['options'], filename=node.get('filename')
    )


def render_dot_texinfo(
    self: TexinfoTranslator,
    node: graphviz,
    code: str,
    options: dict[str, Any],
    prefix: str = 'graphviz',
) -> None:
    try:
        fname, _outfn = render_dot(self, code, options, 'png', prefix)
    except GraphvizError as exc:
        logger.warning(__('dot code %r: %s'), code, exc)
        raise nodes.SkipNode from exc
    if fname is not None:
        self.body.append('@image{%s,,,[graphviz],png}\n' % fname[:-4])
    raise nodes.SkipNode


def texinfo_visit_graphviz(self: TexinfoTranslator, node: graphviz) -> None:
    render_dot_texinfo(self, node, node['code'], node['options'])


def text_visit_graphviz(self: TextTranslator, node: graphviz) -> None:
    if 'alt' in node.attributes:
        self.add_text(_('[graph: %s]') % node['alt'])
    else:
        self.add_text(_('[graph]'))
    raise nodes.SkipNode


def man_visit_graphviz(self: ManualPageTranslator, node: graphviz) -> None:
    if 'alt' in node.attributes:
        self.body.append(_('[graph: %s]') % node['alt'])
    else:
        self.body.append(_('[graph]'))
    raise nodes.SkipNode


def on_config_inited(_app: Sphinx, config: Config) -> None:
    css_path = sphinx.package_dir.joinpath('templates', 'graphviz', 'graphviz.css')
    config.html_static_path.append(str(css_path))


def setup(app: Sphinx) -> ExtensionMetadata:
    app.add_node(
        graphviz,
        html=(html_visit_graphviz, None),
        latex=(latex_visit_graphviz, None),
        texinfo=(texinfo_visit_graphviz, None),
        text=(text_visit_graphviz, None),
        man=(man_visit_graphviz, None),
    )
    app.add_directive('graphviz', Graphviz)
    app.add_directive('graph', GraphvizSimple)
    app.add_directive('digraph', GraphvizSimple)
    app.add_config_value('graphviz_dot', 'dot', 'html', types=frozenset({str}))
    app.add_config_value(
        'graphviz_dot_args', (), 'html', types=frozenset({list, tuple})
    )
    app.add_config_value(
        'graphviz_output_format', 'png', 'html', types=frozenset({str})
    )
    app.add_css_file('graphviz.css')
    app.connect('config-inited', on_config_inited)
    return {
        'version': sphinx.__display_version__,
        'parallel_read_safe': True,
    }
