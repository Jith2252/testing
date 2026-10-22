import re
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).parents[1]


class MarkupParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.buttons = []
        self._button_stack = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get('id'):
            self.ids.add(attributes['id'])
        if tag == 'button':
            self._button_stack.append({
                'attrs': attributes,
                'text': [],
            })

    def handle_data(self, data):
        if self._button_stack:
            self._button_stack[-1]['text'].append(data)

    def handle_endtag(self, tag):
        if tag == 'button' and self._button_stack:
            button = self._button_stack.pop()
            button['text'] = ''.join(button['text']).strip()
            self.buttons.append(button)


def referenced_ids(*scripts):
    references = set()
    pattern = re.compile(r'''querySelector\(\s*['\"]#([A-Za-z][\w-]*)['\"]\s*\)''')
    for script in scripts:
        references.update(pattern.findall((ROOT / script).read_text(encoding='utf-8')))
    return references


def parse_index():
    parser = MarkupParser()
    parser.feed((ROOT / 'index.html').read_text(encoding='utf-8'))
    return parser


def test_every_script_id_reference_exists_in_markup():
    parser = parse_index()
    missing = referenced_ids('player.js', 'playlist.js') - parser.ids
    assert not missing, f'Missing IDs referenced by player scripts: {sorted(missing)}'


def test_all_buttons_have_accessible_labels():
    parser = parse_index()
    unlabeled = []
    for button in parser.buttons:
        attrs = button['attrs']
        accessible_name = attrs.get('aria-label') or attrs.get('title') or button['text']
        if not accessible_name.strip():
            unlabeled.append(attrs.get('id', '<anonymous button>'))
    assert not unlabeled, f'Buttons without accessible labels: {unlabeled}'
