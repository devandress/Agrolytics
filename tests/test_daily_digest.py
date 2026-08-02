"""El resumen diario.

La redacción se prueba entera porque este correo puede terminar en una decisión de
aplicar un producto sobre un cultivo: tiene que ser determinista, no simpática.
"""

from datetime import date

from app.services.daily_digest import compose_digest


class _T:
    """Lo mínimo que mira el redactor de una tarea."""

    def __init__(self, task_type="riego", detail="Humedad baja", priority=3,
                 recommended_value=None, pin_scope="campo", title="t"):
        self.task_type = task_type
        self.detail = detail
        self.title = title
        self.priority = priority
        self.recommended_value = recommended_value
        self.pin_scope = pin_scope


_HOY = date(2026, 8, 2)
_URL = "https://agrolytics.app"


def test_nothing_to_say_means_no_email():
    """La decisión más importante del módulo. Un correo diario que la mitad de los
    días dice "todo bien" enseña a ignorarlo, y el día que importa ya está filtrado
    mentalmente."""
    assert compose_digest([], [], _URL, hoy=_HOY) is None


def test_urgent_proposals_drive_the_subject():
    """El asunto tiene que poder decidirse desde la bandeja de entrada, sin abrir."""
    asunto, _ = compose_digest([(_T(priority=1), "norte")], [], _URL, hoy=_HOY)
    assert "urgente" in asunto.lower()


def test_subject_without_urgency_says_how_many_to_approve():
    asunto, _ = compose_digest([(_T(priority=3), "norte"), (_T(priority=3), "sur")], [], _URL, hoy=_HOY)
    assert "2" in asunto
    assert "urgente" not in asunto.lower()


def test_only_pending_still_gets_a_digest():
    asunto, cuerpo = compose_digest([], [(_T(), "norte")], _URL, hoy=_HOY)
    assert "pendiente" in asunto.lower()
    assert "YA APROBADAS" in cuerpo


def test_the_body_leads_with_what_to_decide():
    _, cuerpo = compose_digest([(_T(), "norte")], [(_T(), "sur")], _URL, hoy=_HOY)
    assert cuerpo.index("EL SISTEMA PROPONE") < cuerpo.index("YA APROBADAS")


def test_the_approval_link_is_included():
    _, cuerpo = compose_digest([(_T(), "norte")], [], _URL, hoy=_HOY)
    assert _URL in cuerpo


def test_the_amount_is_carried_over():
    """Si la tarea trae dosis, el correo la lleva: sin eso hay que abrir la app
    igual y el correo no ahorró nada."""
    _, cuerpo = compose_digest([(_T(recommended_value="25 mm"), "norte")], [], _URL, hoy=_HOY)
    assert "25 mm" in cuerpo


def test_whole_field_tasks_say_so():
    """Mismo criterio que el mapa: no prometer una precisión que el dato no tiene."""
    _, cuerpo = compose_digest([(_T(pin_scope="campo"), "norte")], [], _URL, hoy=_HOY)
    assert "toda la parcela" in cuerpo


def test_an_exact_spot_does_not_claim_the_whole_field():
    _, cuerpo = compose_digest([(_T(pin_scope="punto"), "norte")], [], _URL, hoy=_HOY)
    assert "toda la parcela" not in cuerpo


def test_a_long_list_is_truncated_with_a_count():
    """Veinte renglones en un teléfono no se leen. Se corta, pero diciendo cuántas
    quedaron: ocultar el resto sin avisar es peor que no mandar nada."""
    muchas = [(_T(detail=f"tarea {i}"), "norte") for i in range(20)]
    _, cuerpo = compose_digest(muchas, [], _URL, hoy=_HOY)
    assert "y 8 más" in cuerpo


def test_the_body_carries_no_markup_symbols():
    """Mismo criterio que WhatsApp: en texto plano los asteriscos se ven crudos."""
    _, cuerpo = compose_digest([(_T(), "norte")], [(_T(), "sur")], _URL, hoy=_HOY)
    assert "*" not in cuerpo
    assert "_" not in cuerpo


def test_the_date_is_the_one_given():
    _, cuerpo = compose_digest([(_T(), "norte")], [], _URL, hoy=_HOY)
    assert "02/08/2026" in cuerpo
