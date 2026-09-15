from doors_client import _dxl_preamble


def main() -> None:
    preamble = _dxl_preamble()

    assert preamble.startswith("pragma runLim, ")
    assert preamble.endswith("\n")
    assert "\\n" not in preamble, (
        "El preámbulo contiene un \\\\n literal en vez de un salto real."
    )

    lines = preamble.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("pragma runLim, ")

    print("PRUEBA OK")
    print("repr(preamble):", repr(preamble))


if __name__ == "__main__":
    main()
