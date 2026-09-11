import json
from pathlib import Path
import pytest
from hub.math_text import normalize_math
from hub.documents import markdown_renderer

cases=json.loads((Path(__file__).parent/'fixtures/math-delimiters.json').read_text(encoding='utf-8'))
@pytest.mark.parametrize('item',cases,ids=[item['name'] for item in cases])
def test_shared_delimiter_cases(item):assert normalize_math(item['source'])==item['expected']

def test_html_export_preserves_math_for_offline_katex_without_touching_code():
    result=markdown_renderer().render(r'Inline \(x^2\).'+'\n\n'+r'\[\frac{1}{2}\]'+'\n\n'+r'`\(code\)`')
    assert '$x^2$' in result and r'$$\frac{1}{2}$$' in result
    assert r'<code>\(code\)</code>' in result
