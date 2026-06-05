from bot.config import practica_ok


def test_practica_ok_detects_spanish_practica_variants():
    assert not practica_ok("Práctica profesional en desarrollo de software")
    assert not practica_ok("oferta para practicante TI")
    assert not practica_ok("pasantía en análisis de datos")
    assert not practica_ok("pasantias disponibles")
    assert not practica_ok("internship for new graduates")
    assert not practica_ok("interns wanted")
    assert practica_ok("Desarrollador Python senior")
