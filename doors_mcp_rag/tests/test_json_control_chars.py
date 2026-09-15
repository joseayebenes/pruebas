from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYNC_DIR = PROJECT_ROOT / "sync"
if str(SYNC_DIR) not in sys.path:
    sys.path.insert(0, str(SYNC_DIR))

from doors_client import _dxl_helpers, _escape_unescaped_json_control_chars


def main() -> None:
    # JSON estricto no admite U+0000..U+001F sin escapar dentro de strings.
    raw = '{"ok":true,"text":"A\x00B\x08C\x0bD\x0cE\x1fF"}'

    try:
        json.loads(raw)
    except json.JSONDecodeError as exc:
        assert "Invalid control character" in exc.msg
    else:
        raise AssertionError("La prueba esperaba JSONDecodeError.")

    sanitised = _escape_unescaped_json_control_chars(raw)
    parsed = json.loads(sanitised)
    assert parsed["text"] == "A\x00B\x08C\x0bD\x0cE\x1fF"

    # El productor DXL debe escapar cualquier carácter ASCII 0..31 antes de
    # construir el JSON, no solo LF/CR/TAB.
    dxl = _dxl_helpers()
    assert "code = intOf(ch)" in dxl
    assert "code >= 0 && code < 32" in dxl
    assert 'escaped += "\\\\u00"' in dxl
    assert "code == 127" in dxl

    print("PRUEBA OK: caracteres de control JSON correctamente tratados")


if __name__ == "__main__":
    main()
