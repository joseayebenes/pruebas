"""Cliente de DOORS Classic por Automation: implementacion real de ``RequirementsSource``.

Cubre RF-001 a RF-007 y RF-010 a RF-037. Es la unica clase del proyecto que necesita
Windows y DOORS instalado; el resto del sistema la ve solo a traves del protocolo.

Las tres decisiones que gobiernan este archivo, todas nacidas de fallos reales
(seccion 10, ADR-002):

* **Sesion propia.** Se crea con ``Dispatch(prog_id)``, nunca con ``GetActiveObject``. Una
  ventana de DOORS abierta a mano no aparece de forma fiable en la Running Object Table, y
  la sesion que crea COM no comparte el modulo que el usuario tenga abierto.
* **Modulo explicito.** Cada script abre el modulo por su ``fullName`` con ``read(...)``.
  Dar por hecho que "el modulo ya esta abierto" producia errores ``NO_MODULE``.
* **Validacion contra el esquema.** Los atributos se comprueban contra las ``AttrDef`` del
  modulo antes de leer nada, porque ``objectAttributeText`` con ``noError`` devuelve cadena
  vacia tanto si el atributo no existe como si esta vacio.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from typing import Any

from ...config import Settings
from ...errors import (
    DoorsModuleError,
    DoorsSessionError,
    DxlExecutionError,
)
from ...models import (
    AttributeDefinition,
    AttributeValidation,
    LinkRecord,
    RequirementPage,
    RequirementRecord,
    SearchHit,
    SearchPage,
)
from ..base import validar_nombres
from . import dxl
from .com_worker import ComWorker

logger = logging.getLogger(__name__)


class DoorsComClient:
    """Acceso de solo lectura a DOORS Classic. Implementa ``RequirementsSource``."""

    def __init__(self, settings: Settings | None = None, worker: ComWorker | None = None) -> None:
        self.settings = settings or Settings()
        self.worker = worker or ComWorker()
        self._doors: Any | None = None
        # El esquema de atributos por modulo se cachea: es estable durante una sesion y
        # consultarlo en cada pagina multiplicaria las llamadas DXL sin aportar nada.
        self._esquema: dict[str, list[AttributeDefinition]] = {}

    # -----------------------------------------------------------------------------------
    # Sesion
    # -----------------------------------------------------------------------------------

    @property
    def session_started(self) -> bool:
        return self._doors is not None

    def start_session(self, timeout: float | None = None) -> dict[str, object]:
        """Crea la sesion Automation y espera a que este utilizable (RF-002, RF-003).

        DOORS puede mostrar la ventana de login: la autenticacion la completa el usuario a
        mano, y por eso hay una espera con timeout en lugar de un fallo inmediato (RNF-003).
        """
        if self._doors is not None:
            return {"status": "already_started", "prog_id": self.settings.prog_id}

        espera = timeout or self.settings.start_timeout_seconds
        self._doors = self.worker.call(self._crear_sesion, timeout=espera)

        limite = time.monotonic() + espera
        while time.monotonic() < limite:
            if self._sesion_lista(espera):
                return {
                    "status": "started",
                    "prog_id": self.settings.prog_id,
                    "message": "Sesion Automation lista.",
                }
            time.sleep(1.0)

        raise DoorsSessionError(
            f"La sesion Automation de DOORS no estuvo lista en {espera} segundos. "
            "Completa el inicio de sesion en la ventana de DOORS que ha abierto Python "
            "(no en una ventana anterior) y vuelve a intentarlo. Si tarda mas, sube "
            "DOORS_START_TIMEOUT_SECONDS."
        )

    def _crear_sesion(self) -> Any:
        """Crea el objeto Automation dentro del hilo COM.

        pywin32 se importa aqui para que el modulo siga siendo importable fuera de Windows;
        solo hace falta cuando se abre una sesion de verdad.
        """
        try:
            import win32com.client  # type: ignore[import-not-found]
        except ImportError as exc:
            raise DoorsSessionError(
                "pywin32 no esta instalado. El acceso a DOORS Classic solo funciona en "
                'Windows: instala el extra con  pip install -e ".[win]"  en la maquina '
                "donde este DOORS."
            ) from exc

        logger.info("Creando sesion Automation con ProgID '%s'", self.settings.prog_id)
        try:
            return win32com.client.Dispatch(self.settings.prog_id)
        except Exception as exc:
            raise DoorsSessionError(
                f"No se pudo crear la sesion Automation '{self.settings.prog_id}': {exc}. "
                "Comprueba que DOORS Classic esta instalado y que Python y DOORS se "
                "ejecutan con el mismo usuario de Windows."
            ) from exc

    def _sesion_lista(self, timeout: float) -> bool:
        """Comprueba si la sesion ya responde a DXL (el usuario ya se autentico)."""
        try:
            self._ejecutar_dxl('oleSetResult("ok")', timeout=timeout)
            return True
        except Exception:
            return False

    def status(self, module_path: str | None = None) -> dict[str, object]:
        """Comprueba la sesion y la accesibilidad del modulo seleccionado (RF-007)."""
        ruta = self.settings.resolve_module_path(module_path)
        if self._doors is None:
            return {
                "session": "not_started",
                "module_path": ruta,
                "message": "Llama antes a start_doors_session.",
            }
        atributos = self.list_object_attributes(ruta)
        return {
            "session": "ready",
            "module_path": ruta,
            "module_readable": True,
            "object_attributes": len(atributos),
        }

    # -----------------------------------------------------------------------------------
    # Ejecucion de DXL
    # -----------------------------------------------------------------------------------

    def _ejecutar_dxl(self, script: str, *, timeout: float | None = None) -> str:
        """Ejecuta un script en la sesion y devuelve su resultado como texto."""
        if self._doors is None:
            raise DoorsSessionError(
                "No hay sesion Automation. Llama a start_doors_session antes de consultar."
            )
        espera = timeout or self.settings.dxl_timeout_seconds

        def ejecutar() -> str:
            doors = self._doors
            doors.runStr(script)
            return str(doors.result or "")

        return self.worker.call(ejecutar, timeout=espera)

    def _ejecutar_json(self, script: str, module_path: str) -> dict[str, Any]:
        """Ejecuta un script y parsea su respuesta JSON.

        Un resultado que no es JSON casi siempre significa que el interprete DXL devolvio un
        mensaje de error; se propaga con el texto original, que es lo que permite
        diagnosticarlo.
        """
        crudo = self._ejecutar_dxl(script)
        try:
            datos = json.loads(crudo) if crudo else {}
        except json.JSONDecodeError as exc:
            raise DxlExecutionError(
                f"DOORS devolvio una respuesta que no es JSON: {crudo[:500]!r}", script=script
            ) from exc
        if isinstance(datos, dict) and datos.get("error") == "NO_MODULE":
            raise DoorsModuleError(module_path, "read(...) devolvio null")
        return datos

    # -----------------------------------------------------------------------------------
    # Protocolo RequirementsSource
    # -----------------------------------------------------------------------------------

    def list_object_attributes(self, module_path: str) -> list[AttributeDefinition]:
        """Lista los atributos de objeto del modulo (RF-010, RF-011)."""
        if module_path in self._esquema:
            return self._esquema[module_path]

        datos = self._ejecutar_json(
            dxl.script_list_attributes(module_path, self.settings.dxl_run_limit_cycles),
            module_path,
        )
        definiciones = [
            AttributeDefinition(
                name=a["name"],
                type_name=a.get("type", ""),
                is_object=True,
                is_system=bool(a.get("is_system", False)),
                multi_valued=bool(a.get("multi_valued", False)),
                enum_values=tuple(a.get("enum_values", ())),
            )
            for a in datos.get("attributes", [])
        ]
        self._esquema[module_path] = definiciones
        return definiciones

    def validate_attributes(self, module_path: str, names: Sequence[str]) -> AttributeValidation:
        """Valida nombres contra el esquema real del modulo (RF-012, RF-013)."""
        return validar_nombres(module_path, names, self.list_object_attributes(module_path))

    def fetch_page(
        self,
        module_path: str,
        attributes: Sequence[str],
        *,
        cursor: int | None = None,
        page_size: int = 25,
        max_attribute_chars: int = 20_000,
        include_deleted: bool = False,
        include_table_internals: bool = False,
    ) -> RequirementPage:
        """Lee una pagina de requisitos a partir del cursor (RF-020, RF-057, RF-058)."""
        self.validate_attributes(module_path, attributes).raise_if_invalid()
        datos = self._ejecutar_json(
            dxl.script_fetch_page(
                module_path,
                attributes,
                cursor=cursor,
                page_size=page_size,
                max_attribute_chars=max_attribute_chars,
                include_deleted=include_deleted,
                include_table_internals=include_table_internals,
                run_limit_cycles=self.settings.dxl_run_limit_cycles,
            ),
            module_path,
        )
        registros = tuple(
            self._a_registro(module_path, bruto) for bruto in datos.get("records", [])
        )
        return RequirementPage(records=registros, next_cursor=datos.get("next_cursor"))

    def get_requirement(
        self,
        module_path: str,
        absolute_number: int,
        attributes: Sequence[str],
        *,
        max_attribute_chars: int = 20_000,
    ) -> RequirementRecord | None:
        """Obtiene un objeto por su Absolute Number (RF-021)."""
        self.validate_attributes(module_path, attributes).raise_if_invalid()
        datos = self._ejecutar_json(
            dxl.script_get_requirement(
                module_path,
                absolute_number,
                attributes,
                max_attribute_chars=max_attribute_chars,
                run_limit_cycles=self.settings.dxl_run_limit_cycles,
            ),
            module_path,
        )
        bruto = datos.get("record")
        if not bruto or bruto.get("is_deleted"):
            return None
        return self._a_registro(module_path, bruto)

    def search(
        self,
        module_path: str,
        query: str,
        attributes: Sequence[str],
        *,
        regex: bool = False,
        case_sensitive: bool = False,
        cursor: int | None = None,
        page_size: int = 25,
        max_attribute_chars: int = 20_000,
    ) -> SearchPage:
        """Busca dentro de DOORS y devuelve solo las coincidencias (RF-030..RF-034)."""
        self.validate_attributes(module_path, attributes).raise_if_invalid()
        datos = self._ejecutar_json(
            dxl.script_search(
                module_path,
                query,
                attributes,
                regex=regex,
                case_sensitive=case_sensitive,
                cursor=cursor,
                page_size=page_size,
                max_attribute_chars=max_attribute_chars,
                run_limit_cycles=self.settings.dxl_run_limit_cycles,
            ),
            module_path,
        )
        hits = tuple(
            SearchHit(
                record=RequirementRecord(
                    module_path=module_path,
                    absolute_number=int(h["absolute_number"]),
                    identifier=h.get("identifier", ""),
                    outline_number=h.get("outline_number", ""),
                ),
                matched_attribute=h.get("matched_attribute", ""),
                match_start=int(h.get("match_start", -1)),
                match_text=h.get("value", ""),
            )
            for h in datos.get("hits", [])
        )
        return SearchPage(hits=hits, next_cursor=datos.get("next_cursor"))

    def get_links(
        self, module_path: str, absolute_number: int, *, direction: str = "both"
    ) -> list[LinkRecord]:
        """Trazabilidad entrante, saliente o ambas (RF-035, RF-036).

        No incluye enlaces externos OSLC; el servidor MCP lo declara en su respuesta
        (RF-037).
        """
        datos = self._ejecutar_json(
            dxl.script_get_links(
                module_path,
                absolute_number,
                direction=direction,
                run_limit_cycles=self.settings.dxl_run_limit_cycles,
            ),
            module_path,
        )
        fallos = datos.get("load_failures", [])
        if fallos:
            logger.warning(
                "No se pudieron cargar %d modulo(s) origen al leer enlaces entrantes: %s",
                len(fallos),
                ", ".join(fallos),
            )
        enlaces = []
        for bruto in datos.get("links", []):
            sentido = bruto.get("direction", "outgoing")
            enlaces.append(
                LinkRecord(
                    source_module=bruto.get("source_module", module_path),
                    source_absolute_number=int(
                        bruto.get("source_absolute_number", absolute_number)
                    ),
                    target_module=bruto.get("target_module", ""),
                    target_absolute_number=int(bruto.get("target_absolute_number", 0)),
                    link_module=bruto.get("link_module", ""),
                    direction=sentido,
                )
            )
        return enlaces

    # -----------------------------------------------------------------------------------

    @staticmethod
    def _a_registro(module_path: str, bruto: dict[str, Any]) -> RequirementRecord:
        """Convierte un objeto del JSON de DXL en un ``RequirementRecord``.

        Object Heading y Object Text se promueven a campos propios por ser los atributos de
        contenido predeterminados (RF-014); el resto queda en ``attributes``.
        """
        atributos = dict(bruto.get("attributes", {}))
        return RequirementRecord(
            module_path=module_path,
            absolute_number=int(bruto["absolute_number"]),
            identifier=bruto.get("identifier", ""),
            outline_number=bruto.get("outline_number", ""),
            heading=atributos.pop("Object Heading", ""),
            text=atributos.pop("Object Text", ""),
            attributes=atributos,
        )

    def close(self) -> None:
        """Cierra el worker COM. La sesion de DOORS la cierra el usuario."""
        self.worker.stop()
        self._doors = None
