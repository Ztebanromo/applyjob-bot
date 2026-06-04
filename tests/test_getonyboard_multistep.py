"""Tests para el flujo multi-paso de GetOnBoard."""
from unittest.mock import MagicMock, patch


def _make_page(url="https://www.getonbrd.com/jobs/test/applications/1/edit?step=basic"):
    page = MagicMock()
    page.url = url
    page.query_selector.return_value = None
    page.query_selector_all.return_value = []
    page.evaluate.return_value = ""
    return page


def test_navigate_multistep_detects_preview_and_submits():
    """En el paso preview, hace click en 'Postular' y retorna True."""
    from bot.portals.getonyboard import _navigate_gob_multistep
    page = _make_page(url="https://www.getonbrd.com/jobs/test/applications/1/edit?step=preview")
    btn = MagicMock()
    btn.is_visible.return_value = True
    btn.is_enabled.return_value = True
    page.query_selector.side_effect = lambda sel: btn if ("Postular" in sel or "submit" in sel) else None

    profile = {"cover_letter": "Me interesa esta posición.", "salary": "850000"}
    result = _navigate_gob_multistep(page, profile, "Desarrollador Junior")
    assert result is True


def test_navigate_multistep_advances_through_steps():
    """Itera pasos haciendo click en 'Siguiente' hasta llegar a preview."""
    from bot.portals.getonyboard import _navigate_gob_multistep

    step_urls = [
        "https://www.getonbrd.com/jobs/test/applications/1/edit?step=basic",
        "https://www.getonbrd.com/jobs/test/applications/1/edit?step=basic_data",
        "https://www.getonbrd.com/jobs/test/applications/1/edit?step=preview",
    ]
    step_iter = iter(step_urls)

    page = MagicMock()
    page.url = next(step_iter)
    page.query_selector_all.return_value = []
    page.evaluate.return_value = ""

    siguiente_btn = MagicMock()
    siguiente_btn.is_visible.return_value = True
    siguiente_btn.is_enabled.return_value = True

    postular_btn = MagicMock()
    postular_btn.is_visible.return_value = True
    postular_btn.is_enabled.return_value = True

    def mock_query_selector(sel):
        if "preview" in page.url:
            return postular_btn if ("Postular" in sel or "submit" in sel) else None
        return siguiente_btn if "Siguiente" in sel else None

    def mock_click():
        try:
            page.url = next(step_iter)
        except StopIteration:
            pass

    siguiente_btn.click.side_effect = mock_click
    page.query_selector.side_effect = mock_query_selector

    with patch("bot.form_filler.fill_form", return_value={}):
        result = _navigate_gob_multistep(page, {"cover_letter": "Test"}, "Dev Junior")

    assert result is True


def test_navigate_multistep_returns_false_if_no_submit_button():
    """En el paso preview sin botón, retorna False."""
    from bot.portals.getonyboard import _navigate_gob_multistep
    page = _make_page(url="https://www.getonbrd.com/jobs/test/applications/1/edit?step=preview")
    page.query_selector.return_value = None

    result = _navigate_gob_multistep(page, {}, "Dev Junior")
    assert result is False


def test_is_multistep_url():
    """Detecta correctamente URLs de flujo multi-paso."""
    from bot.portals.getonyboard import _is_gob_multistep_url
    assert _is_gob_multistep_url("https://www.getonbrd.com/jobs/dev/applications/123/edit?step=basic")
    assert _is_gob_multistep_url("https://www.getonbrd.com/empleos/dev/applications/5/edit?step=preview")
    assert not _is_gob_multistep_url("https://www.getonbrd.com/jobs/desarrollador-junior-5")
    assert not _is_gob_multistep_url("https://www.getonbrd.com/empleos/dev/apply")
