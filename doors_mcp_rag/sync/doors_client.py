from __future__ import annotations

import atexit
import json
import os
import queue
import sys
import threading
import time
from concurrent.futures import Future, InvalidStateError, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from difflib import get_close_matches
from typing import Any, Literal


class DoorsError(RuntimeError):
    pass


class DoorsTimeoutError(DoorsError):
    pass


DOORS_PROG_ID = os.environ.get("DOORS_PROG_ID", "DOORS.Application").strip()
START_TIMEOUT_SECONDS = float(os.environ.get("DOORS_START_TIMEOUT_SECONDS", "30"))
DXL_TIMEOUT_SECONDS = float(os.environ.get("DOORS_DXL_TIMEOUT_SECONDS", "90"))
DXL_RUN_LIMIT_CYCLES = int(os.environ.get("DOORS_DXL_RUN_LIMIT_CYCLES", "0"))
COM_BUSY_RETRIES = 8
COM_BUSY_RETRY_SECONDS = 0.5


@dataclass
class _WorkerJob:
    action: Literal["start", "dxl", "stop"]
    payload: str | None
    future: Future[str | None]


class _DoorsAutomationWorker:
    """Mantiene DOORS.Application en un único hilo/apartamento COM."""

    _BUSY_HRESULTS = {0x80010001, 0x8001010A}
    _DEAD_HRESULTS = {0x80010108, 0x800706BA, 0x80010007}

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise DoorsError("DOORS Automation solo está disponible en Windows.")

        try:
            import pythoncom  # type: ignore
            import pywintypes  # type: ignore
            import win32com.client  # type: ignore
        except ImportError as exc:
            raise DoorsError(
                "Falta pywin32. Instala las dependencias con: pip install pywin32"
            ) from exc

        self.pythoncom = pythoncom
        self.pywintypes = pywintypes
        self.win32_client = win32com.client

        self._jobs: queue.Queue[_WorkerJob] = queue.Queue()
        self._state_lock = threading.Lock()
        self._closed = False
        self._poisoned = False
        self._thread = threading.Thread(
            target=self._run,
            name="doors-com-worker",
            daemon=True,
        )
        self._thread.start()

    def start_session(self, timeout_seconds: float = START_TIMEOUT_SECONDS) -> None:
        future: Future[str | None] = Future()
        self._submit(_WorkerJob("start", None, future))
        try:
            future.result(timeout=timeout_seconds)
        except FutureTimeoutError as exc:
            future.cancel()
            self._mark_poisoned()
            raise DoorsTimeoutError(
                "Crear la sesión Automation de DOORS superó el timeout. "
                "Comprueba diálogos modales, licencia o autenticación y reinicia "
                "el proceso antes de reintentar."
            ) from exc

    def execute_dxl(self, dxl: str, timeout_seconds: float = DXL_TIMEOUT_SECONDS) -> str:
        future: Future[str | None] = Future()
        self._submit(_WorkerJob("dxl", dxl, future))
        try:
            result = future.result(timeout=timeout_seconds)
        except FutureTimeoutError as exc:
            future.cancel()
            self._mark_poisoned()
            raise DoorsTimeoutError(
                "La llamada DXL a DOORS superó el timeout. DOORS puede estar "
                "esperando una interacción o procesando una consulta grande. "
                "Cierra el diálogo y reinicia el proceso."
            ) from exc
        return str(result or "")

    def shutdown(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        if not self._thread.is_alive():
            return
        future: Future[str | None] = Future()
        self._jobs.put(_WorkerJob("stop", None, future))
        try:
            future.result(timeout=2)
        except Exception:
            pass
        self._thread.join(timeout=2)

    def _submit(self, job: _WorkerJob) -> None:
        with self._state_lock:
            if self._closed:
                raise DoorsError("El worker COM ya está cerrado.")
            if self._poisoned:
                raise DoorsError(
                    "El worker COM quedó bloqueado tras un timeout. Reinicia el proceso."
                )
        self._jobs.put(job)

    def _mark_poisoned(self) -> None:
        with self._state_lock:
            self._poisoned = True

    @staticmethod
    def _safe_set_result(future: Future[str | None], value: str | None) -> None:
        if future.cancelled():
            return
        try:
            future.set_result(value)
        except InvalidStateError:
            pass

    @staticmethod
    def _safe_set_exception(future: Future[str | None], exc: BaseException) -> None:
        if future.cancelled():
            return
        try:
            future.set_exception(exc)
        except InvalidStateError:
            pass

    @staticmethod
    def _hresult(exc: BaseException) -> int | None:
        value = getattr(exc, "hresult", None)
        return (value & 0xFFFFFFFF) if isinstance(value, int) else None

    def _create_session(self) -> Any:
        try:
            return self.win32_client.Dispatch(DOORS_PROG_ID)
        except Exception as exc:
            hresult = self._hresult(exc)
            detail = f" HRESULT=0x{hresult:08X}" if hresult is not None else ""
            raise DoorsError(
                f"No se pudo crear {DOORS_PROG_ID}.{detail} Comprueba DOORS Classic, "
                "usuario, privilegios y registro COM."
            ) from exc

    def _execute_with_retry(self, doors: Any, dxl: str) -> str:
        for attempt in range(COM_BUSY_RETRIES + 1):
            try:
                doors.Result = ""
                doors.runStr(dxl)
                return str(doors.Result or "")
            except self.pywintypes.com_error as exc:
                hresult = self._hresult(exc)
                if hresult in self._BUSY_HRESULTS and attempt < COM_BUSY_RETRIES:
                    time.sleep(COM_BUSY_RETRY_SECONDS)
                    continue
                raise
        raise DoorsError("DOORS permaneció ocupado.")

    def _run(self) -> None:
        self.pythoncom.CoInitialize()
        doors: Any | None = None
        try:
            while True:
                job = self._jobs.get()
                if job.future.cancelled():
                    continue
                if job.action == "stop":
                    self._safe_set_result(job.future, None)
                    break
                try:
                    if doors is None:
                        doors = self._create_session()
                    if job.action == "start":
                        self._safe_set_result(job.future, None)
                        continue
                    if job.action != "dxl" or job.payload is None:
                        raise DoorsError("Trabajo COM interno no válido.")
                    result = self._execute_with_retry(doors, job.payload)
                    self._safe_set_result(job.future, result)
                except Exception as exc:
                    hresult = self._hresult(exc)
                    if hresult in self._DEAD_HRESULTS:
                        doors = None
                    if isinstance(exc, DoorsError):
                        controlled = exc
                    else:
                        detail = f" HRESULT=0x{hresult:08X}" if hresult else ""
                        controlled = DoorsError(
                            f"DOORS rechazó la operación.{detail} {type(exc).__name__}: {exc}"
                        )
                    self._safe_set_exception(job.future, controlled)
        finally:
            doors = None
            self.pythoncom.CoUninitialize()


def _dxl_string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _dxl_bool(value: bool) -> str:
    return "true" if value else "false"


def _escape_unescaped_json_control_chars(raw: str) -> str:
    """Escape raw U+0000..U+001F characters that occur inside JSON strings.

    This is a defensive fallback for legacy/unexpected DOORS values. The DXL
    producer should already escape these characters, but sanitising once before
    giving up avoids losing a complete synchronisation because of one historic
    control character stored in an attribute.
    """
    result: list[str] = []
    in_string = False
    escaped = False

    for char in raw:
        if in_string:
            if escaped:
                result.append(char)
                escaped = False
            elif char == "\\":
                result.append(char)
                escaped = True
            elif char == '"':
                result.append(char)
                in_string = False
            elif ord(char) < 0x20:
                result.append(f"\\u{ord(char):04x}")
            else:
                result.append(char)
        else:
            result.append(char)
            if char == '"':
                in_string = True

    return "".join(result)


def _dxl_preamble() -> str:
    """Configura el watchdog interno de DXL para nuestros scripts generados."""
    if DXL_RUN_LIMIT_CYCLES < 0:
        raise DoorsError("DOORS_DXL_RUN_LIMIT_CYCLES no puede ser negativo.")
    return f"pragma runLim, {DXL_RUN_LIMIT_CYCLES}\n"


def _dxl_helpers() -> str:
    return r'''
string jsonEscape(string sourceText) {
    Buffer escaped = create
    string hexDigits = "0123456789ABCDEF"
    int i = 0
    int code = 0
    char ch
    for (i = 0; i < length(sourceText); i++) {
        ch = sourceText[i]
        code = intOf(ch)
        if (ch == '"') escaped += "\\\""
        else if (ch == '\\') escaped += "\\\\"
        else if (code >= 0 && code < 32) {
            escaped += "\\u00"
            escaped += hexDigits[code / 16]
            escaped += hexDigits[code % 16]
        }
        else if (code == 127) escaped += "\\u007F"
        else escaped += ch
    }
    string result = stringOf(escaped)
    delete escaped
    return result
}

void appendJsonString(Buffer targetBuffer, string value) {
    targetBuffer += "\""
    targetBuffer += jsonEscape(value)
    targetBuffer += "\""
}

void appendJsonBool(Buffer targetBuffer, bool value) {
    if (value) targetBuffer += "true"
    else targetBuffer += "false"
}

string objectAttributeText(Object obj, string attributeName, int maxChars) {
    string value = ""
    noError
    if (maxChars > 0) value = getBoundedUnicode((obj).(attributeName), maxChars)
    else value = unicodeString((obj).(attributeName))
    string dxlError = lastError
    if (!null dxlError) return ""
    return value
}

void appendObjectJson(
    Buffer targetBuffer,
    Object obj,
    string attributeNames[],
    int attributeCount,
    int maxAttributeChars
) {
    int absoluteNumber = obj."Absolute Number"
    targetBuffer += "{"
    targetBuffer += "\"absolute_number\":"
    targetBuffer += (absoluteNumber "")
    targetBuffer += ",\"identifier\":"
    appendJsonString(targetBuffer, identifier(obj))
    targetBuffer += ",\"outline_number\":"
    appendJsonString(targetBuffer, number(obj))
    targetBuffer += ",\"is_deleted\":"
    appendJsonBool(targetBuffer, isDeleted(obj))
    targetBuffer += ",\"attributes\":{" 

    bool firstAttribute = true
    int index = 0
    for (index = 0; index < attributeCount; index++) {
        if (!firstAttribute) targetBuffer += ","
        firstAttribute = false
        appendJsonString(targetBuffer, attributeNames[index])
        targetBuffer += ":"
        appendJsonString(
            targetBuffer,
            objectAttributeText(obj, attributeNames[index], maxAttributeChars)
        )
    }
    targetBuffer += "}}"
}
'''


def _module_context_dxl(module_path: str) -> str:
    template = r'''
string configuredModulePath = __MODULE_PATH__
Module currentModule = current Module

if (null currentModule || fullName(currentModule) != configuredModulePath) {
    noError
    currentModule = read(configuredModulePath, true, true)
    string moduleOpenError = lastError
    if (!null moduleOpenError || null currentModule) {
        Buffer moduleErrorOutput = create
        moduleErrorOutput += "{\"ok\":false,\"error\":"
        if (!null moduleOpenError) appendJsonString(moduleErrorOutput, moduleOpenError)
        else appendJsonString(moduleErrorOutput, "No se pudo abrir el módulo formal.")
        moduleErrorOutput += "}"
        oleSetResult(stringOf(moduleErrorOutput))
        delete moduleErrorOutput
        halt
    }
}
current = currentModule
'''
    return template.replace("__MODULE_PATH__", _dxl_string(module_path))


def _dxl_attribute_array(attributes: list[str]) -> str:
    lines = [f"string attributeNames[{len(attributes)}]"]
    for index, attribute in enumerate(attributes):
        lines.append(f"attributeNames[{index}] = {_dxl_string(attribute)}")
    lines.append(f"int attributeNamesCount = {len(attributes)}")
    return "\n".join(lines)


class DoorsClient:
    """Cliente mínimo reutilizable para sincronizar módulos DOORS."""

    def __init__(self) -> None:
        self._worker = _DoorsAutomationWorker()
        atexit.register(self.close)

    def close(self) -> None:
        self._worker.shutdown()

    def start_session(self) -> None:
        self._worker.start_session()

    def _run_json(self, dxl: str) -> dict[str, Any]:
        raw = self._worker.execute_dxl(dxl)
        if not raw.strip():
            raise DoorsError("DOORS no devolvió resultado DXL.")
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            if "Invalid control character" in exc.msg:
                sanitised = _escape_unescaped_json_control_chars(raw)
                try:
                    result = json.loads(sanitised)
                except json.JSONDecodeError:
                    preview = repr(raw[max(0, exc.pos - 80):exc.pos + 80])
                    raise DoorsError(
                        "DOORS devolvió JSON con caracteres de control no válidos "
                        f"cerca de la posición {exc.pos}: {preview}"
                    ) from exc
            else:
                preview = repr(raw[max(0, exc.pos - 80):exc.pos + 80])
                raise DoorsError(
                    f"DOORS devolvió JSON inválido en la posición {exc.pos}: {preview}"
                ) from exc
        if not isinstance(result, dict):
            raise DoorsError("Respuesta inesperada de DOORS.")
        if not result.get("ok", False):
            raise DoorsError(str(result.get("error", "Error DXL desconocido")))
        return result

    def status(self, module_path: str) -> dict[str, Any]:
        dxl = (
            _dxl_preamble()
            + _dxl_helpers()
            + _module_context_dxl(module_path)
            + r'''
Buffer output = create
output += "{\"ok\":true,\"module\":{\"name\":"
appendJsonString(output, name(currentModule))
output += ",\"full_name\":"
appendJsonString(output, fullName(currentModule))
output += "}}"
oleSetResult(stringOf(output))
delete output
'''
        )
        return self._run_json(dxl)

    def list_object_attributes(self, module_path: str) -> list[str]:
        dxl = (
            _dxl_preamble()
            + _dxl_helpers()
            + _module_context_dxl(module_path)
            + r'''
Buffer output = create
output += "{\"ok\":true,\"attributes\":["
bool firstAttribute = true
AttrDef ad
for ad in currentModule do {
    if (ad.object) {
        if (!firstAttribute) output += ","
        firstAttribute = false
        appendJsonString(output, ad.name)
    }
}
output += "]}"
oleSetResult(stringOf(output))
delete output
'''
        )
        result = self._run_json(dxl)
        values = result.get("attributes", [])
        if not isinstance(values, list):
            raise DoorsError("La lista de atributos devuelta por DOORS no es válida.")
        return [str(value) for value in values]

    def validate_attributes(self, module_path: str, attributes: list[str]) -> None:
        available = self.list_object_attributes(module_path)
        available_set = set(available)
        missing = [name for name in attributes if name not in available_set]
        if not missing:
            return

        details: list[str] = []
        lower = {name.casefold(): name for name in available}
        for name in missing:
            suggestions: list[str] = []
            if name.casefold() in lower:
                suggestions.append(lower[name.casefold()])
            for candidate in get_close_matches(name, available, n=3, cutoff=0.55):
                if candidate not in suggestions:
                    suggestions.append(candidate)
            if suggestions:
                details.append(f"{name!r} (quizá: {', '.join(suggestions)})")
            else:
                details.append(repr(name))
        raise DoorsError(
            "Atributos no válidos: " + ", ".join(details)
        )

    def list_requirements(
        self,
        module_path: str,
        *,
        after_absolute_number: int | None,
        limit: int,
        attributes: list[str],
        max_attribute_chars: int = 20_000,
    ) -> dict[str, Any]:
        """
        Lee una página continuando después de un objeto concreto.

        Se usa un cursor basado en Absolute Number para evitar volver a
        recorrer el módulo desde el principio en cada página.
        """
        requested_after = (
            int(after_absolute_number)
            if after_absolute_number is not None
            else -1
        )

        dxl = (
            _dxl_preamble()
            + _dxl_helpers()
            + _dxl_attribute_array(attributes)
            + "\n"
            + f"int requestedAfterAbsoluteNumber = {requested_after}\n"
            + f"int requestedLimit = {int(limit)}\n"
            + f"int requestedMaxAttributeChars = {int(max_attribute_chars)}\n"
            + _module_context_dxl(module_path)
            + r'''
// La instancia Automation pertenece al sincronizador.
// Fijamos un display set estable para navegar con next(Object).
filtering off
level 0
sorting off

Buffer output = create
output += "{\"ok\":true,\"requirements\":["

int returned = 0
int lastVisitedAbsoluteNumber = requestedAfterAbsoluteNumber
bool firstObject = true
Object obj = null

if (requestedAfterAbsoluteNumber < 0) {
    obj = first(currentModule)
} else {
    Object cursorObject = object(
        requestedAfterAbsoluteNumber,
        currentModule
    )

    if (null cursorObject) {
        Buffer cursorError = create
        cursorError += "{\"ok\":false,\"error\":"
        appendJsonString(
            cursorError,
            "El objeto cursor ya no existe. Reinicia la sincronización."
        )
        cursorError += "}"
        oleSetResult(stringOf(cursorError))
        delete cursorError
        delete output
        halt
    }

    obj = next(cursorObject)
}

while (!null obj && returned < requestedLimit) {
    int currentAbsoluteNumber = obj."Absolute Number"
    lastVisitedAbsoluteNumber = currentAbsoluteNumber

    bool includeObject = true
    if (isDeleted(obj)) includeObject = false
    if (table(obj) || row(obj) || cell(obj)) includeObject = false

    if (includeObject) {
        if (!firstObject) output += ","
        firstObject = false

        appendObjectJson(
            output,
            obj,
            attributeNames,
            attributeNamesCount,
            requestedMaxAttributeChars
        )
        returned++
    }

    obj = next(obj)
}

bool hasMore = !null obj

output += "],\"returned\":"
output += (returned "")
output += ",\"has_more\":"
appendJsonBool(output, hasMore)
output += ",\"next_after_absolute_number\":"

if (hasMore && lastVisitedAbsoluteNumber >= 0) {
    output += (lastVisitedAbsoluteNumber "")
} else {
    output += "null"
}

output += "}"
oleSetResult(stringOf(output))
delete output
'''
        )

        return self._run_json(dxl)
